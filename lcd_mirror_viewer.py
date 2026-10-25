#!/usr/bin/env python3
"""Viewer for the MPC3000 LCD mirror (mods/lcdmirror316.inc) over MIDI SysEx.

    python3 tools/lcd_mirror_midi_viewer.py [--port NAME] [--log FILE] [--headless]
    python3 tools/lcd_mirror_midi_viewer.py --replay MIDI.log [--out-port 4]

The mod streams the 40x8 text screen (and the Mixer page's 240x64 pixels) as
SysEx; this program decodes the stream and draws it.  Protocol (frozen, version
2; notes/lcd_mirror_midi.md "Protocol"):

    F0 7D 4D 33 4B <type> <seq> <payload...> F7      at most 64 bytes

7D is the non-commercial manufacturer ID, 4D 33 4B is "M3K", seq a 7-bit count
that increments per message (gaps are drops).  Types:

    01 HELLO   proto, mod version, OS major, OS minor (03 10 = 3.16)
    02 ROW     row 0..7, col 0..39, len 1..40, codes (escaped, run tokens; len = decoded count)
    03 CURSOR  row, col, mode lo7, mode hi1   (BIOS cursor; mode = OS cursor-mode byte)
    04 MODE    00 text, 01 graphics (Mixer)
    05 PIX     offset hi7, offset lo7 (0..1919), len 1..32, bytes packed 7-in-8
    06 FULL    a full repaint follows
    07 PCOPY   offset hi7, offset lo7, len 1..127, distance hi7, distance lo7: framebuffer
               bytes offset+i = offset-distance+i for i = 0..len-1, in that order (overlap allowed)

Character escape: a code below 7D is sent as itself; 7E nn is code 80|nn; 7F nn
is the literal nn (for 7D, 7E and 7F); 7D n c is n (4..127) copies of the code c,
itself escaped (protocol 2; protocol 1 had no run token and sent 7D as itself).  7-in-8: per group of up to 7 bytes, one
byte of high bits (bit i = bit 7 of the group's byte i), then each byte & 7F.
PIX bytes are the OS framebuffer (DGROUP 1579h, 30 bytes per row) as stored.

Input: python-rtmidi or mido (whichever is installed) for a live port; --replay
reads the replay host's --midi-log ("<seconds> <port> 0x<byte>" per line).
The Tk window needs a Python with _tkinter; --headless prints each settled
screen instead.  Any other MIDI on the port is ignored.
"""
from __future__ import annotations

__version__ = "1.0.0"

import argparse
import functools
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

HEADER = bytes([0xF0, 0x7D, 0x4D, 0x33, 0x4B])
MAX_MSG = 64
PROTO = 2
HELLO, ROW, CURSOR, MODE, PIX, FULL, PCOPY = 1, 2, 3, 4, 5, 6, 7
RUN = 0x7D
RUN_MIN = 4
COLS, ROWS = 40, 8
FB_W, FB_H, FB_STRIDE = 240, 64, 30
FB_SIZE = FB_STRIDE * FB_H          # 1920
PIX_MAX = 32
# Bits of a framebuffer byte, left to right: True = bit 7 is the leftmost pixel
# (the OS bit-reverses each byte for the LC7981).  PROVEN (MAME): the Mixer page
# decoded this way equals lcd_pixels.txt with 0 differing pixels, and with
# bit 0 leftmost differs in 6374 (emu/test_lcdmirror316.py).
FB_MSB_LEFT = True


# ---------------------------------------------------------------- encoder (reference)

def escape1(c: int) -> bytes:
    if c < RUN:
        return bytes([c])
    if c >= 0x80:
        return bytes([0x7E, c & 0x7F])
    return bytes([0x7F, c])


def escape(codes: bytes) -> bytes:
    """Protocol 2 encoding of codes: runs of RUN_MIN or more become 7D n c."""
    out, i = bytearray(), 0
    while i < len(codes):
        j = i
        while j < len(codes) and codes[j] == codes[i] and j - i < 127:
            j += 1
        if j - i >= RUN_MIN:
            out += bytes([RUN, j - i]) + escape1(codes[i])
            i = j
        else:
            out += escape1(codes[i])
            i += 1
    return bytes(out)


def pack7(data: bytes) -> bytes:
    out = bytearray()
    for i in range(0, len(data), 7):
        group = data[i:i + 7]
        out.append(sum(((b >> 7) & 1) << j for j, b in enumerate(group)))
        out += bytes(b & 0x7F for b in group)
    return bytes(out)


def message(kind: int, seq: int, payload: bytes = b"") -> bytes:
    msg = HEADER + bytes([kind, seq & 0x7F]) + payload + b"\xF7"
    if len(msg) > MAX_MSG or any(b & 0x80 for b in msg[1:-1]):
        raise ValueError(f"bad message {msg.hex()}")
    return msg


def row_message(seq: int, row: int, col: int, codes: bytes) -> bytes:
    return message(ROW, seq, bytes([row, col, len(codes)]) + escape(codes))


def pix_message(seq: int, offset: int, data: bytes) -> bytes:
    return message(PIX, seq, bytes([offset >> 7, offset & 0x7F, len(data)]) + pack7(data))


def pcopy_message(seq: int, offset: int, length: int, distance: int) -> bytes:
    return message(PCOPY, seq, bytes([offset >> 7, offset & 0x7F, length, distance >> 7, distance & 0x7F]))


# ---------------------------------------------------------------- decoder

def unescape(data: bytes, count: int) -> tuple[bytes, int]:
    """`count` codes from escaped `data`; returns (codes, bytes consumed)."""
    out, i = bytearray(), 0

    def one(at: int) -> tuple[int, int]:
        b = data[at]
        if b == 0x7E:
            return 0x80 | data[at + 1], at + 2
        if b == 0x7F:
            return data[at + 1], at + 2
        if b == RUN or b & 0x80:
            raise ValueError("bad code byte")
        return b, at + 1
    while len(out) < count:
        if data[i] == RUN:
            n = data[i + 1]
            if not RUN_MIN <= n <= 127:
                raise ValueError("bad run")
            c, i = one(i + 2)
            out += bytes([c]) * n
        else:
            c, i = one(i)
            out.append(c)
    if len(out) != count:
        raise ValueError("run past len")
    return bytes(out), i


def unpack7(data: bytes, count: int) -> bytes:
    out, i = bytearray(), 0
    while len(out) < count:
        hi = data[i]; i += 1
        for j in range(min(7, count - len(out))):
            out.append(data[i] | (((hi >> j) & 1) << 7)); i += 1
    if i != len(data):
        raise ValueError("PIX length mismatch")
    return bytes(out)


@dataclass
class Screen:
    text: list[bytearray] = field(default_factory=lambda: [bytearray(b" " * COLS) for _ in range(ROWS)])
    fb: bytearray = field(default_factory=lambda: bytearray(FB_SIZE))
    graphics: bool = False
    cursor: tuple[int, int, int] = (0, 0, 0)        # row, col, mode
    hello: tuple[int, ...] | None = None

    def text_lines(self) -> list[str]:
        return ["".join(chr(c) if 0x20 <= c < 0x7F else "?" for c in r) for r in self.text]

    def text_codes(self) -> list[bytes]:
        return [bytes(r) for r in self.text]

    def fb_pixels(self, msb_left: bool = FB_MSB_LEFT) -> list[str]:
        rows = []
        for y in range(FB_H):
            line = []
            for b in self.fb[y * FB_STRIDE:(y + 1) * FB_STRIDE]:
                bits = range(7, -1, -1) if msb_left else range(8)
                line += ["#" if (b >> k) & 1 else "." for k in bits]
            rows.append("".join(line))
        return rows


class Decoder:
    """Feed MIDI bytes; SysEx in this protocol updates `screen`.

    Real-time bytes (F8..FF) inside a SysEx are skipped, as MIDI allows; any
    other status byte ends it (`aborted`).  SysEx of other makers is skipped
    whole (`foreign`).  `drops` counts missing sequence numbers (a HELLO resyncs:
    the MPC rebooted or the viewer joined late; a repeated number counts in
    `dups`); `bad` counts malformed protocol messages.  `events` keeps the last
    few counter changes for the log.
    """

    def __init__(self) -> None:
        self.screen = Screen()
        self.buf: bytearray | None = None
        self.skip = False                   # inside a foreign SysEx
        self.seq: int | None = None
        self.messages = self.drops = self.dups = self.bad = self.aborted = self.foreign = 0
        self.kinds: dict[int, int] = {}
        self.longest = 0
        self.events: list[str] = []

    def counters(self) -> str:
        return (f"msgs {self.messages} drops {self.drops} dups {self.dups} bad {self.bad} "
                f"aborted {self.aborted} foreign {self.foreign}")

    def _event(self, what: str) -> None:
        self.events.append(what)
        del self.events[:-64]

    def feed(self, data: bytes | int) -> list[bytes]:
        """Returns the complete protocol messages decoded from `data`."""
        done = []
        for b in ([data] if isinstance(data, int) else data):
            if b >= 0xF8:
                continue
            if b == 0xF0:
                if self.buf is not None:
                    self.aborted += 1
                    self._event("aborted by F0")
                self.buf, self.skip = bytearray([b]), False
            elif self.buf is None:
                continue
            elif b == 0xF7:
                msg = bytes(self.buf + b"\xF7")
                self.buf, skip, self.skip = None, self.skip, False
                if skip:
                    self.foreign += 1
                elif self._handle(msg):
                    done.append(msg)
            elif b & 0x80:
                self.aborted += 1
                self._event(f"aborted by {b:02X}")
                self.buf = None
            elif self.skip:
                continue
            else:
                self.buf.append(b)
                n = len(self.buf)
                if n <= len(HEADER) and self.buf != HEADER[:n]:
                    self.skip = True                    # someone else's SysEx
                elif n >= MAX_MSG:                      # ours, longer than any we send
                    self.bad += 1
                    self._event("bad: over 64 bytes")
                    self.skip = True
        return done

    def _handle(self, msg: bytes) -> bool:
        if not msg.startswith(HEADER):
            self.foreign += 1
            return False
        self.longest = max(self.longest, len(msg))
        if len(msg) < 8:
            self.bad += 1
            self._event("bad: short")
            return False
        kind, seq, p = msg[5], msg[6], msg[7:-1]
        if kind == HELLO or self.seq is None:
            pass                                        # resync
        elif seq == self.seq:
            self.dups += 1
            self._event(f"dup seq {seq}")
        else:
            gap = (seq - self.seq - 1) & 0x7F
            if gap:
                self.drops += gap
                self._event(f"drop {gap} before seq {seq}")
        self.seq = seq
        try:
            self._apply(kind, p)
        except (IndexError, ValueError) as exc:
            self.bad += 1
            self._event(f"bad: {exc} {msg.hex()}")
            return False
        self.messages += 1
        self.kinds[kind] = self.kinds.get(kind, 0) + 1
        return True

    def _apply(self, kind: int, p: bytes) -> None:
        s = self.screen
        if kind == HELLO:
            if len(p) != 4:
                raise ValueError("HELLO length")
            s.hello = tuple(p)
        elif kind == ROW:
            row, col, n = p[0], p[1], p[2]
            codes, used = unescape(p[3:], n)
            if row >= ROWS or not 1 <= n <= COLS or col + n > COLS or 3 + used != len(p):
                raise ValueError("ROW out of range")
            s.text[row][col:col + n] = codes
        elif kind == CURSOR:
            if len(p) != 4 or p[0] >= ROWS or p[1] >= COLS or p[3] > 1:
                raise ValueError("CURSOR out of range")
            s.cursor = (p[0], p[1], p[2] | (p[3] << 7))
        elif kind == MODE:
            if len(p) != 1 or p[0] > 1:
                raise ValueError("MODE value")
            s.graphics = p[0] == 1
        elif kind == PIX:
            off, n = (p[0] << 7) | p[1], p[2]
            if not 1 <= n <= PIX_MAX or off + n > FB_SIZE:
                raise ValueError("PIX out of range")
            s.fb[off:off + n] = unpack7(p[3:], n)
        elif kind == FULL:
            if p:
                raise ValueError("FULL length")
        elif kind == PCOPY:
            if len(p) != 5:
                raise ValueError("PCOPY length")
            off, n, d = (p[0] << 7) | p[1], p[2], (p[3] << 7) | p[4]
            if not 1 <= n <= 127 or not 1 <= d <= off or off + n > FB_SIZE:
                raise ValueError("PCOPY out of range")
            for i in range(n):
                s.fb[off + i] = s.fb[off - d + i]
        else:
            raise ValueError(f"unknown type {kind}")


def read_replay_log(path: Path, port: int) -> list[tuple[float, int]]:
    """(emulated seconds, byte) of one MIDI OUT from mpc3k_replay --midi-log."""
    out = []
    for line in path.read_text().splitlines():
        t, p, b = line.split()
        if int(p) == port:
            out.append((float(t), int(b, 16)))
    return out


# ---------------------------------------------------------------- live input

class Input:
    """One MIDI input through python-rtmidi, else mido.  `poll()` returns the bytes
    received since the last call; `present()` says whether the port is still listed
    (neither library reports a vanished port on read)."""

    def __init__(self, name: str | None) -> None:
        self.want = name
        try:
            import rtmidi                               # python-rtmidi
            self.lib = "rtmidi"
            self.mi = rtmidi.MidiIn()
            self.mi.ignore_types(sysex=False, timing=True, active_sense=True)
            ports = self.mi.get_ports()
            idx = self._pick(ports)
            self.mi.open_port(idx)
            self.name = ports[idx]
        except ImportError:
            import mido
            self.lib = "mido"
            ports = mido.get_input_names()
            self.name = ports[self._pick(ports)]
            self.port = mido.open_input(self.name)

    def _pick(self, ports: list[str]) -> int:
        for i, p in enumerate(ports):
            if self.want is None or self.want.lower() in p.lower():
                return i
        raise OSError(f"no MIDI input matching {self.want!r} in {ports}")

    def poll(self) -> list[int]:
        data: list[int] = []
        if self.lib == "rtmidi":
            while (m := self.mi.get_message()) is not None:
                data += m[0]
        else:
            for m in self.port.iter_pending():
                data += m.bytes()
        return data

    def present(self) -> bool:
        if self.lib == "rtmidi":
            return self.name in self.mi.get_ports()
        import mido
        return self.name in mido.get_input_names()

    def close(self) -> None:
        try:
            self.mi.close_port() if self.lib == "rtmidi" else self.port.close()
        except Exception:                               # noqa: BLE001 - closing a dead port
            pass


def describe(msg: bytes) -> str:
    names = {HELLO: "HELLO", ROW: "ROW", CURSOR: "CURSOR", MODE: "MODE", PIX: "PIX", FULL: "FULL", PCOPY: "PCOPY"}
    return f"{names.get(msg[5], '?')} seq={msg[6]} {msg[7:-1].hex()}"


def screen_text(s: Screen) -> list[str]:
    """The settled screen for --headless: text with a ^ under the cursor, or pixels."""
    if s.graphics:
        return ["(graphics)"] + s.fb_pixels()
    rows = [f"|{r}|" for r in s.text_lines()]
    row, col, mode = s.cursor
    rows.insert(row + 1, " " * (col + 1) + f"^ cursor mode {mode}")
    return rows


QUIET = 0.1          # seconds without a protocol message before --headless prints


def settings_path() -> Path:
    """Where the window keeps its View and MIDI choices (nothing else is stored)."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA") or Path.home())
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "MPC3000 LCD Mirror" / "settings.json"


def load_settings() -> dict:
    try:
        data = json.loads(settings_path().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(data: dict) -> None:
    try:
        path = settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1))
        tmp.replace(path)
    except OSError:
        pass                                            # settings are a convenience


def midi_inputs() -> list[str]:
    """The MIDI input names the system offers now."""
    try:
        import rtmidi
        mi = rtmidi.MidiIn()
        try:
            return list(mi.get_ports())
        finally:
            del mi
    except ImportError:
        try:
            import mido
            return list(mido.get_input_names())
        except Exception:                               # noqa: BLE001 - no backend at all
            return []
    except Exception:                                   # noqa: BLE001 - rtmidi raises its own types
        return []


def run_live(args: argparse.Namespace) -> int:
    dec = Decoder()
    log = open(args.log, "a") if args.log else None

    def note(text: str) -> None:
        if log:
            log.write(f"{time.time():.3f} {text}\n")
            log.flush()
    ui = None if args.headless else TkView(args.zoom, args.palette, args.look, args.contrast, args.port,
                                           args.saved)
    inp: Input | None = None
    last_data = last_msg = time.time()
    last_check = last_warn = 0.0
    printed: list[str] | None = None
    while True:
        now = time.time()
        if ui and inp is not None and ui.port_want != inp.want:
            note(f"switch from {inp.name}")
            inp.close()                                 # another input chosen in the MIDI menu
            inp = None
        if inp is None:
            try:
                inp = Input(ui.port_want if ui else args.port)
                print(f"listening on {inp.name} ({inp.lib})", file=sys.stderr)
                note(f"open {inp.name}")
            except ImportError:
                print("install python-rtmidi (or mido)", file=sys.stderr)
                return 2
            except Exception as exc:                    # noqa: BLE001 - rtmidi raises its own types
                print(f"{exc}; retrying", file=sys.stderr)
                if ui and not ui.draw(dec, "no MIDI input; retrying", 0):
                    return 0
                time.sleep(1.0)
                continue
        data = inp.poll()
        if data:
            last_data = now
        for msg in dec.feed(bytes(data)):
            last_msg = now
            note(describe(msg))
        for e in dec.events:
            note(f"EVENT {e} | {dec.counters()}")
        dec.events.clear()
        if now - last_check > 2.0:
            last_check = now
            if not inp.present():
                print(f"{inp.name} disappeared; reconnecting", file=sys.stderr)
                note(f"lost {inp.name}")
                inp.close()
                inp = None
                continue
        age = now - last_data
        if ui:
            if not ui.draw(dec, inp.name, age):
                return 0
        elif now - last_msg >= QUIET:
            shown = screen_text(dec.screen)
            if shown != printed and dec.messages:
                printed = shown
                print(f"@ {now:.3f} {dec.counters()}")
                print("\n".join(shown), flush=True)
        if not ui and age > 6.0 and now - last_warn > 5.0:
            last_warn = now
            print(f"(no data for {age:.0f} s: transport running, or the link is down)", file=sys.stderr)
        time.sleep(0.005)


# Window palettes: name -> (label, background, lit pixel, lit pixel at 100 % contrast).
PALETTES = {
    "green": ("Yellow-green STN", "#9ab04a", "#1c2410", "#000000"),
    "blue": ("Blue / white", "#1f3fc4", "#e9f0ff", "#ffffff"),
    "white": ("Black on white", "#e4e6e1", "#141414", "#000000"),
    "black": ("White on black", "#0c0c0c", "#f0f0f0", "#ffffff"),
    "grey": ("Grey FSTN", "#b9bdb4", "#2a2d33", "#000000"),
    "amber": ("Amber on black", "#140c00", "#ffb000", "#ffd257"),
}
ZOOMS = ("2", "4", "max")
LOOKS = ("real", "flat")
# Cursor blink, measured on the unit (2026-10-26, A001_10261113_C017.MOV: a
# camera on a stand, exposure locked, 31 cycles at 120 fps; see
# notes/lcd_mirror_midi.md).  The controller drives the 6x8 block as a plain
# square wave, half on and half off, every 463.6 ms.  The glass follows with
# a turn-on time constant of about 30 ms and a turn-off one of about 170 ms,
# so the block fades to about 25 % before it relights (model rms 0.020 against
# the measured cycle).  The block is solid: the glyph under it shows through
# as it fades.  The flat look switches hard.  Whether OS
# cursor modes 2 and 3 differ is UNDETERMINED; every non-zero mode blinks.
BLINK_PERIODS = {"Unit (0.46 s)": 0.4636, "Slow (1.0 s)": 1.0, "Very slow (1.6 s)": 1.6}
BLINK_DUTY = 0.5
TAU_ON = 0.030       # s, pixel turn-on time constant
TAU_OFF = 0.170      # s, pixel turn-off time constant
# The real look plays that response quantised to a few levels (View -> Cursor
# fade).  Every visible change costs about 10 ms of CPU (macOS redraws the
# whole window), so at x6: 3 levels about 14 % of a core, 5 about 22 %, 10
# about 36 % (rms error against the fitted response 0.088 / 0.046 / 0.021).
BLINK_LEVELS = {"3 levels (cheapest)": 3, "5 levels": 5, "10 levels (smoothest)": 10}
CONTRAST_FLOOR = 0.12  # at 0 % contrast the text keeps 12 % of its tuned difference from the background
PAD = 4              # LCD pixels of bezel around the 240x64 area
STATUS_H = 20        # window pixels for the status line under the LCD

# "real" look: name -> (backlight centre, backlight at the side edges, lit pixel).
# blue is measured from a photo of the unit's LCD (2026-10-26, 1920x1080, about
# 6.7 photo pixels per LCD pixel): background centre 48,75,160, sides 29,59,155,
# lit 88,116,172, here brightened by about 1.2 for the photo's exposure.  The
# others reuse the flat palettes with the same falloff.
REAL_LOOKS = {
    "green": ("#a3b952", "#879c3c", "#1c2410"),
    "blue": ("#3a5ac4", "#2347ba", "#7a90cc"),
    "white": ("#eceee9", "#cdd0ca", "#141414"),
    "black": ("#141414", "#0a0a0a", "#e8e8e8"),
    "grey": ("#c3c7be", "#a5a99f", "#2a2d33"),
    "amber": ("#1c1200", "#100a00", "#ffb000"),
}
# Dot grid: in the photo, the gap between two lit dots is about 1/6 of the
# pitch and well short of background; unlit pixels show no grid; strokes have a
# soft edge of about a sixth of a pixel.  The gap darkening is fixed at half
# the first tuning (0.45), as the owner chose on the unit (2026-10-26); the
# halo (glow) was removed then too.
GAP_FRACTION = 0.17
GAP_DEPTH = 0.225
SOFT = 0.18          # edge blur radius, in LCD pixels


def _rgb(h: str) -> tuple[int, int, int]:
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def _contrast(back, front, contrast: float, extreme):
    """The lit colour at a contrast of 0..1: 0.5 is the tuned colour `front`, 1 is `extreme`
    (pure white or black), 0 is a trace (CONTRAST_FLOOR) of the difference from `back`.
    Works on RGB tuples or numpy arrays."""
    if contrast >= 0.5:
        return front + (extreme - front) * ((contrast - 0.5) * 2)
    return back + (front - back) * (CONTRAST_FLOOR + (1 - CONTRAST_FLOOR) * contrast * 2)


def text_colour(look: str, contrast: float) -> str:
    """The flat look's lit colour for a palette at a contrast of 0..1, as #rrggbb."""
    _, bg, fg, extreme = PALETTES[look]
    rgb = [_contrast(b, f, contrast, e) for b, f, e in zip(_rgb(bg), _rgb(fg), _rgb(extreme))]
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(x))) for x in rgb)


@functools.lru_cache(maxsize=8)
def _lcd_layers(k: int, look: str, contrast: float):
    """The parts of a render_lcd() picture that do not depend on the screen: dot grid, backlight, lit colour."""
    import numpy as np
    centre, edge, lit = REAL_LOOKS[look]
    h, w = (FB_H + 2 * PAD) * k, (FB_W + 2 * PAD) * k
    tile = np.ones((k, k), np.float32)
    gap = round(k * GAP_FRACTION) if k >= 4 else 0
    if gap:
        tile[-gap:, :] -= GAP_DEPTH
        tile[:, -gap:] -= GAP_DEPTH
    grid = np.tile(tile, (h // k, w // k))
    if gap:                                             # soften the gap lines a little
        from PIL import Image, ImageFilter
        img = Image.fromarray((grid * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.5))
        grid = np.asarray(img, np.float32) / 255
    ny = (np.arange(h, dtype=np.float32) - (h - 1) / 2) / ((h - 1) / 2)
    nx = (np.arange(w, dtype=np.float32) - (w - 1) / 2) / ((w - 1) / 2)
    v = np.clip(1 - 0.5 * (nx[None, :] ** 2 + ny[:, None] ** 2), 0, 1)[..., None]   # 1 centre, 0.5 sides
    c, e = np.array(_rgb(centre), np.float32), np.array(_rgb(edge), np.float32)
    back = np.clip(c * v + (2 * e - c) * (1 - v), 0, 255)
    front = np.array(_rgb(lit), np.float32) * (0.8 + 0.2 * v)
    front = _contrast(back, front, contrast, np.array(_rgb(PALETTES[look][3]), np.float32))
    return grid, back, np.clip(front, 0, 255) - back


def _panel_level(phase: float, period: float) -> float:
    """Steady-state brightness of a pixel driven on for BLINK_DUTY of `period`, then off, `phase` s in."""
    on, off = period * BLINK_DUTY, period * (1 - BLINK_DUTY)
    low = 0.0
    for _ in range(60):
        high = 1 - (1 - low) * math.exp(-on / TAU_ON)
        low = high * math.exp(-off / TAU_OFF)
    if phase < on:
        return 1 - (1 - low) * math.exp(-phase / TAU_ON)
    return high * math.exp(-(phase - on) / TAU_OFF)


@functools.lru_cache(maxsize=16)
def blink_schedule(period: float, fade: bool, n: int = 3) -> tuple[tuple[float, ...], tuple[tuple[float, float], ...]]:
    """(levels, steps) for one cycle: the levels to pre-render, and (start s, level) in cycle order.
    fade: the panel's response quantised to n levels; otherwise hard on / off."""
    if not fade:
        return (0.0, 1.0), ((0.0, 1.0), (period * BLINK_DUTY, 0.0))
    low = _panel_level(period - 1e-9, period)
    levels = tuple(low + (1 - low) * i / (n - 1) for i in range(n))
    steps, last = [], None
    for ms in range(int(period * 1000)):
        x = _panel_level(ms / 1000, period)
        level = min(levels, key=lambda lv: abs(lv - x))
        if level != last:
            steps.append((ms / 1000, level))
            last = level
    return levels, tuple(steps)


def cursor_level(t: float, period: float, fade: bool, n: int = 3) -> float:
    """The cursor block's level at time t (one of blink_schedule's levels)."""
    phase = t % period
    level = None
    for start, lv in blink_schedule(period, fade, n)[1]:
        if start > phase:
            break
        level = lv
    return level


@functools.lru_cache(maxsize=4)
def _chars(pixels: tuple[str, ...]):
    """The pixel characters as a padded uint8 array (blank = '.')."""
    import numpy as np
    chars = np.full((FB_H + 2 * PAD, FB_W + 2 * PAD), ord("."), np.uint8)
    chars[PAD:PAD + FB_H, PAD:PAD + FB_W] = np.frombuffer("".join(pixels).encode(), np.uint8).reshape(FB_H, FB_W)
    return chars


def _mask(pixels: list[str], level: float):
    """Lit amount per LCD pixel, padded by PAD: '#' 1, 'a' (the cursor block) level, else 0."""
    import numpy as np
    lut = np.zeros(256, np.float32)
    lut[ord("#")], lut[ord("a")] = 1.0, level
    return lut[_chars(tuple(pixels))]


def _render_region(mask, k: int, look: str, contrast: float, real: bool, y0: int = 0, x0: int = 0):
    """RGB float array for `mask` (lit amounts, padded LCD pixels from row y0, column x0)."""
    import numpy as np
    from PIL import Image, ImageFilter
    big = mask.repeat(k, 0).repeat(k, 1)
    if not real:
        bg = np.array(_rgb(PALETTES[look][1]), np.float32)
        fg = np.array(_rgb(text_colour(look, contrast)), np.float32)
        return bg + (fg - bg) * big[..., None]
    grid, back, diff = _lcd_layers(k, look, round(contrast, 3))
    h, w = big.shape
    region = (slice(y0 * k, y0 * k + h), slice(x0 * k, x0 * k + w))
    img = Image.fromarray((big * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(max(0.5, SOFT * k)))
    # Soft-edged pixels, darkened in the gaps; 1.0 inside a lit dot.
    amount = (np.asarray(img, np.float32) / 255 * grid[region])[..., None]
    return back[region] + diff[region] * amount


def render_lcd(pixels: list[str], k: int, look: str, contrast: float = 0.5, real: bool = True,
               level: float = 1.0):
    """The 240x64 picture, k window pixels per LCD pixel, as a PIL RGB image.

    real: the unit's panel (REAL_LOOKS: dot grid, soft edges, backlight); otherwise
    the flat palette (hard square pixels, two colours).  contrast 0..1 sets the
    text against its background, 0.5 being the tuned colours (_contrast).
    level: the cursor block's 'a' pixels (cursor_level).
    """
    import numpy as np
    from PIL import Image
    rgb = _render_region(_mask(pixels, level), k, look, contrast, real)
    return Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), "RGB")


CELL_MARGIN = 2      # LCD pixels drawn around the cursor cell by render_cell
CELL_CONTEXT = 3     # and the extra pixels read around those so blur edges match


def render_cell(pixels: list[str], k: int, look: str, row: int, col: int, level: float,
                contrast: float = 0.5, real: bool = True):
    """The cursor cell (row, col) and CELL_MARGIN around it, as render_lcd would draw it:
    (PIL image, window x, window y)."""
    import numpy as np
    from PIL import Image
    mask = _mask(pixels, level)
    top, left = PAD + row * 8 - CELL_MARGIN, PAD + col * 6 - CELL_MARGIN
    bottom, right = PAD + row * 8 + 8 + CELL_MARGIN, PAD + col * 6 + 6 + CELL_MARGIN
    y0, x0 = max(0, top - CELL_CONTEXT), max(0, left - CELL_CONTEXT)
    y1, x1 = min(mask.shape[0], bottom + CELL_CONTEXT), min(mask.shape[1], right + CELL_CONTEXT)
    rgb = _render_region(mask[y0:y1, x0:x1], k, look, contrast, real, y0, x0)
    rgb = rgb[(top - y0) * k:(bottom - y0) * k, (left - x0) * k:(right - x0) * k]
    return Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), "RGB"), left * k, top * k


class TkView:
    """40x8 text through emu/lcd_font.py's glyphs (hatch for codes it lacks) or 240x64 pixels.

    View menu or keys: 2 / 4 / m zoom (- / = step it), p next palette, l real / flat look,
    s status line on / off, b cursor blink on / off, [ / ] contrast (10 % steps)
    and 0 to reset it to the default 100 %: the text against its background
    (50 % = the tuned colours, 100 % = pure white or black).  "real" (numpy + Pillow) draws the
    panel's dot grid, soft edges and backlight; without those modules the window
    draws flat rectangles.
    """

    def __init__(self, zoom: str = "4", palette: str = "green", look: str = "real", contrast: int = 100,
                 port: str | None = None, saved: dict | None = None) -> None:
        import tkinter as tk
        try:
            from emu.lcd_font import font               # in the MPC3000 repo
        except ImportError:
            from lcd_font import font                   # in the standalone receiver
        saved = saved or {}
        self.port_want = port
        self.tk = tk
        self.font = font()
        self.root = tk.Tk()
        self.root.title("LCD Mirror for MPC3000")
        self.root.configure(bg="#2b2b2b")
        # No focus ring or border: Tk draws them inside the canvas, over its
        # coordinate origin, which hid the top pixel row and the left column.
        self.canvas = tk.Canvas(self.root, highlightthickness=0, bd=0)
        self.canvas.pack()
        self.zoom = tk.StringVar(value=zoom)
        self.palette = tk.StringVar(value=palette)
        self.contrast = max(0, min(100, contrast))
        self.show_status = tk.BooleanVar(value=bool(saved.get("show_status", True)))
        self.blink = tk.BooleanVar(value=bool(saved.get("blink", True)))
        period = saved.get("blink_period")
        self.blink_period = tk.DoubleVar(value=period if period in BLINK_PERIODS.values()
                                         else BLINK_PERIODS["Unit (0.46 s)"])
        levels = saved.get("fade_levels")
        self.fade_levels = tk.IntVar(value=levels if levels in BLINK_LEVELS.values() else 3)
        self.port_var = tk.StringVar(value=port or "")
        self.cell = None                                # canvas item of the cursor overlay
        self.cell_photos = {}                           # level -> its pre-rendered cell image
        try:
            import numpy  # noqa: F401
            from PIL import ImageTk  # noqa: F401
            self.can_real = True
        except ImportError:
            self.can_real = False
            if look == "real":
                print("real look needs numpy and Pillow; using flat", file=sys.stderr)
        self.look = tk.StringVar(value=look if self.can_real else "flat")
        menubar = tk.Menu(self.root)
        view = tk.Menu(menubar, tearoff=0)
        for z in ZOOMS:
            view.add_radiobutton(label="Zoom max" if z == "max" else f"Zoom x{z}", variable=self.zoom,
                                 value=z, command=self._relayout, accelerator="M" if z == "max" else z)
        view.add_command(label="Zoom in", accelerator="=", command=lambda: self._zoom_step(1))
        view.add_command(label="Zoom out", accelerator="-", command=lambda: self._zoom_step(-1))
        view.add_separator()
        for name, (label, *_) in PALETTES.items():
            view.add_radiobutton(label=label, variable=self.palette, value=name, command=self._relayout)
        view.add_command(label="Next palette", accelerator="P", command=self._next_palette)
        view.add_separator()
        view.add_radiobutton(label="Real LCD look", variable=self.look, value="real", command=self._relayout,
                             state="normal" if self.can_real else "disabled")
        view.add_radiobutton(label="Flat look", variable=self.look, value="flat", command=self._relayout)
        view.add_command(label="Toggle look", accelerator="L", command=self._toggle_look)
        view.add_separator()
        view.add_command(label="More contrast", accelerator="]", command=lambda: self._step(10))
        view.add_command(label="Less contrast", accelerator="[", command=lambda: self._step(-10))
        view.add_command(label="Contrast 100 % (default)", accelerator="0", command=lambda: self._set_contrast(100))
        view.add_separator()
        view.add_checkbutton(label="Cursor blink", variable=self.blink, accelerator="B",
                             command=self._relayout)
        rate = tk.Menu(view, tearoff=0)
        for label, period in BLINK_PERIODS.items():
            rate.add_radiobutton(label=label, variable=self.blink_period, value=period, command=self._relayout)
        view.add_cascade(label="Cursor blink rate", menu=rate)
        fade = tk.Menu(view, tearoff=0)
        for label, n in BLINK_LEVELS.items():
            fade.add_radiobutton(label=label, variable=self.fade_levels, value=n, command=self._relayout)
        view.add_cascade(label="Cursor fade (real look)", menu=fade)
        view.add_separator()
        view.add_checkbutton(label="Show status line", variable=self.show_status, accelerator="S",
                             command=self._relayout)
        menubar.add_cascade(label="View", menu=view)
        self.midi_menu = tk.Menu(menubar, tearoff=0, postcommand=self._fill_midi_menu)
        menubar.add_cascade(label="MIDI input", menu=self.midi_menu)
        self.root.config(menu=menubar)
        for key, step in (("minus", -1), ("equal", 1), ("plus", 1)):
            self.root.bind(f"<{key}>", lambda _e, step=step: self._zoom_step(step))
        for key, z in (("2", "2"), ("4", "4"), ("m", "max"), ("M", "max")):
            self.root.bind(key, lambda _e, z=z: (self.zoom.set(z), self._relayout()))
        self.root.bind("p", lambda _e: self._next_palette())
        self.root.bind("P", lambda _e: self._next_palette())
        self.root.bind("l", lambda _e: self._toggle_look())
        self.root.bind("L", lambda _e: self._toggle_look())
        self.root.bind("]", lambda _e: self._step(10))
        self.root.bind("[", lambda _e: self._step(-10))
        self.root.bind("0", lambda _e: self._set_contrast(100))
        for key in ("b", "B"):
            self.root.bind(key, lambda _e: (self.blink.set(not self.blink.get()), self._relayout()))
        for key in ("s", "S"):
            self.root.bind(key, lambda _e: (self.show_status.set(not self.show_status.get()), self._relayout()))
        self.alive = True
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.last = None
        self.photo = None                               # keeps the Tk image alive
        self._relayout()

    def _close(self) -> None:
        self.alive = False

    def _fill_midi_menu(self) -> None:
        """List the inputs each time the menu opens (interfaces come and go)."""
        m = self.midi_menu
        m.delete(0, "end")
        m.add_radiobutton(label="First available", variable=self.port_var, value="", command=self._choose_port)
        names = midi_inputs()
        if names:
            m.add_separator()
        for name in names:
            m.add_radiobutton(label=name, variable=self.port_var, value=name, command=self._choose_port)
        if not names:
            m.add_command(label="(no MIDI inputs found)", state="disabled")

    def _choose_port(self) -> None:
        self.port_want = self.port_var.get() or None
        self._save()

    def _save(self) -> None:
        save_settings({"port": self.port_want, "zoom": self.zoom.get(), "palette": self.palette.get(),
                       "look": self.look.get(), "contrast": self.contrast, "show_status": self.show_status.get(),
                       "blink": self.blink.get(), "blink_period": self.blink_period.get(),
                       "fade_levels": self.fade_levels.get()})

    def _max_zoom(self) -> int:
        """Largest even scale whose window fits the screen (title bar and menu bar allowed for)."""
        sw, sh = self.root.winfo_screenwidth() - 40, self.root.winfo_screenheight() - 120
        k = min(sw // (FB_W + 2 * PAD), (sh - STATUS_H) // (FB_H + 2 * PAD))
        return max(2, k - k % 2)

    def _zoom_step(self, step: int) -> None:
        """Next / previous of x2, x4 and max (x6 on a 14-inch MacBook Pro), stopping at either end."""
        i = ZOOMS.index(self.zoom.get()) + step
        if 0 <= i < len(ZOOMS):
            self.zoom.set(ZOOMS[i])
            self._relayout()

    def _next_palette(self) -> None:
        names = list(PALETTES)
        self.palette.set(names[(names.index(self.palette.get()) + 1) % len(names)])
        self._relayout()

    def _set_contrast(self, value: int) -> None:
        self.contrast = max(0, min(100, value))
        self._relayout()

    def _step(self, delta: int) -> None:
        self._set_contrast(self.contrast + delta)

    def _toggle_look(self) -> None:
        if self.can_real:
            self.look.set("flat" if self.look.get() == "real" else "real")
            self._relayout()

    def _relayout(self) -> None:
        z = self.zoom.get()
        self.k = self._max_zoom() if z == "max" else int(z)
        _, self.bg, tuned, _ = PALETTES[self.palette.get()]
        self.fg = text_colour(self.palette.get(), self.contrast / 100)
        real = self.look.get() == "real"
        # Real look: the glass is the image and the window around it is black, as on the unit.
        self.status_fg = "#8c8c8c" if real else tuned
        self.status_h = STATUS_H if self.show_status.get() else 0
        self.canvas.config(width=(FB_W + 2 * PAD) * self.k, height=(FB_H + 2 * PAD) * self.k + self.status_h,
                           bg="#050505" if real else self.bg)
        self.root.geometry("")                          # let the window follow the canvas size
        self.last = None                                # redraw on the next draw()
        self._save()

    def pixels(self, s: Screen) -> list[str]:
        if s.graphics:
            return s.fb_pixels()
        rows = [["."] * FB_W for _ in range(FB_H)]
        for r in range(ROWS):
            for c in range(COLS):
                code = s.text[r][c]
                glyph = self.font.get(code)
                for y in range(8):
                    for x in range(6):
                        if glyph is None:
                            on = code not in (0x00, 0x20) and (x + y) % 2 == 0     # hatch
                        else:
                            on = y < 7 and x < 5 and (glyph[y] >> (4 - x)) & 1
                        if on:
                            rows[r * 8 + y][c * 6 + x] = "#"
        r, c, mode = s.cursor
        if mode and r < ROWS and c < COLS:              # mode 0 hides it (INT 43 AX=2)
            for y in range(8):                          # whole 6x8 cell, solid over the glyph (as on the
                for x in range(6):                      # unit): 'a' = lit while the cursor is on
                    p = rows[r * 8 + y]
                    if p[c * 6 + x] != "#":
                        p[c * 6 + x] = "a"
        return ["".join(r) for r in rows]

    def _level(self, fade: bool) -> float:
        if not self.blink.get():
            return 1.0                                  # steady block
        return cursor_level(time.time(), self.blink_period.get(), fade, self.fade_levels.get())

    def draw(self, dec: Decoder, port: str, age: float) -> bool:
        if not self.alive:
            return False
        s = dec.screen
        status = f"{port}  {dec.counters()}  last data {age:.0f} s ago  contrast {self.contrast} %" + \
            ("  (transport running, or link down)" if age > 6 else "")
        real = self.can_real and self.look.get() == "real"
        level = self._level(real)
        # With numpy + Pillow the cursor steps are swapped images; without, a full redraw each step.
        state = (s.graphics, bytes(s.fb) if s.graphics else b"".join(s.text_codes()), s.cursor) + \
            (() if self.can_real else (level >= 0.5,))
        k, o, fg = self.k, PAD * self.k, self.fg
        look = (self.contrast / 100, real)
        if state != self.last:
            self.last = state
            self.canvas.delete("all")
            self.cell, self.cell_photos = None, {}
            if self.can_real:
                from PIL import ImageTk
                self.pixmap = self.pixels(s)
                self.photo = ImageTk.PhotoImage(render_lcd(self.pixmap, k, self.palette.get(), *look,
                                                           level=level))
                self.canvas.create_image(0, 0, anchor="nw", image=self.photo)
                self.cell_level = level
                r, c, mode = s.cursor
                if not s.graphics and mode:             # the blink's cell images, rendered once per screen
                    levels = (blink_schedule(self.blink_period.get(), real, self.fade_levels.get())[0]
                              if self.blink.get() else (1.0,))
                    for lv in levels:
                        img, self.cell_x, self.cell_y = render_cell(self.pixmap, k, self.palette.get(), r, c,
                                                                    lv, *look)
                        self.cell_photos[lv] = ImageTk.PhotoImage(img)
            else:
                self._draw_flat(s, k, o, fg, level)
            self.status, self.status_text = None, status
            if self.status_h:
                self.status = self.canvas.create_text(o, (FB_H + 2 * PAD) * k + self.status_h // 2, anchor="w",
                                                      text=status, fill=self.status_fg)
        else:
            if self.status is not None and status != self.status_text:
                self.canvas.itemconfig(self.status, text=status)
            self.status_text = status
            if level != self.cell_level and level in self.cell_photos:
                # A blink step: swap in that level's pre-rendered cell.
                photo = self.cell_photos[level]
                if self.cell is None:
                    self.cell = self.canvas.create_image(self.cell_x, self.cell_y, anchor="nw", image=photo)
                else:
                    self.canvas.itemconfig(self.cell, image=photo)
                self.cell_level = level
        self.root.update()
        return self.alive

    def _draw_flat(self, s: Screen, k: int, o: int, fg: str, level: float) -> None:
        lit = {"#", "a"} if level >= 0.5 else {"#"}
        for y, line in enumerate(self.pixels(s)):
            x = 0
            while x < FB_W:
                if line[x] in lit:
                    e = x
                    while e < FB_W and line[e] in lit:
                        e += 1
                    self.canvas.create_rectangle(o + x * k, o + y * k, o + e * k, o + (y + 1) * k,
                                                 fill=fg, width=0)
                    x = e
                else:
                    x += 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--version", action="version", version=f"LCD Mirror for MPC3000 {__version__}")
    ap.add_argument("--port", help="substring of the MIDI input name (default: the last one chosen in the "
                                   "MIDI input menu, else the first)")
    ap.add_argument("--log", type=Path, help="append every decoded message with the laptop clock")
    ap.add_argument("--headless", action="store_true", help="print screens instead of a window")
    ap.add_argument("--zoom", choices=ZOOMS,
                    help="window scale: 2, 4, or max (largest even scale that fits the screen); default 4")
    ap.add_argument("--palette", choices=list(PALETTES), help="window colours; default green")
    ap.add_argument("--look", choices=LOOKS,
                    help="real: dot grid, soft edges and backlight (needs numpy and Pillow); flat: plain pixels")
    ap.add_argument("--contrast", type=int, metavar="0-100",
                    help="text against its background: 100 = pure white or black text (default), 50 = the "
                         "tuned colours, 0 = barely visible")
    ap.add_argument("--replay", type=Path, help="decode an mpc3k_replay --midi-log file and print the end state")
    ap.add_argument("--out-port", type=int, default=4, help="with --replay: MIDI OUT 1-4 (default 4 = OUT D)")
    a = ap.parse_args()
    # Options given on the command line win; then the window's saved choices; then the defaults.
    a.saved = {} if a.headless else load_settings()
    for key, default, ok in (("zoom", "4", ZOOMS), ("palette", "green", PALETTES), ("look", "real", LOOKS),
                             ("contrast", 100, range(101))):
        if getattr(a, key) is None:
            value = a.saved.get(key)
            setattr(a, key, value if value in ok else default)
    if a.port is None and isinstance(a.saved.get("port"), str):
        a.port = a.saved["port"]
    if a.replay:
        dec = Decoder()
        for _, b in read_replay_log(a.replay, a.out_port):
            dec.feed(b)
        s = dec.screen
        print(f"{dec.counters()} longest={dec.longest} hello={s.hello}")
        print("\n".join(screen_text(s)))
        return 0
    return run_live(a)


if __name__ == "__main__":
    sys.exit(main())
