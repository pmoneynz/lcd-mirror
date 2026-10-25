# LCD Mirror

**A companion helper for the P-MONEY MPC3000 update.**

A free desktop window that shows an Akai MPC3000's screen live on a Mac or PC.
The MPC sends its screen as MIDI SysEx, and this app draws it: the 40×8 text
pages and the Mixer's 240×64 graphics, with the real blink of the cursor.

**Unofficial.** Not made, endorsed or supported by Akai Professional or inMusic.
Akai and MPC are trademarks of inMusic Brands, Inc.; the names here only say
which instrument the app works with.

## What you need

- An MPC3000 running the **P-MONEY MPC3000 update** with its LCD mirror, which
  streams the screen. The update patches your own copy of OS 3.16. It is not
  part of this repository, and no Akai software is included or distributed here.
- A MIDI interface: the MPC3000's **MIDI OUT D** to the computer's MIDI input.

## Download

Get the zip for your computer from the
[Releases](https://github.com/pmoneynz/lcd-mirror/releases) page:

| File | For |
|---|---|
| `LCD-Mirror-macos-apple-silicon.zip` | Macs with M1 or later |
| `LCD-Mirror-macos-intel.zip` | Intel Macs |
| `LCD-Mirror-windows-x64.zip` | Windows 10 / 11 (64-bit) |

Each release has a `SHA256SUMS.txt`. To check a download:

```
shasum -a 256 LCD-Mirror-*.zip       # macOS
certutil -hashfile LCD-Mirror-windows-x64.zip SHA256   # Windows
```

and compare with the line in `SHA256SUMS.txt`. The apps are built from
this repository's source by GitHub Actions (see the workflow run linked from
each release).

**The apps are not code-signed.** The first time you open one:

- **macOS:** unzip, move the app to Applications, then right-click it and choose
  **Open**, then **Open** again. If macOS still refuses, go to System Settings,
  then Privacy & Security, and click **Open Anyway**.
- **Windows:** unzip the whole folder and run `LCD Mirror.exe`. If
  SmartScreen warns, click **More info**, then **Run anyway**.

## Use

1. Connect MIDI OUT D to the computer and start the MPC with the mirror mod.
2. Open the app and choose your interface under **MIDI input**. The choice is
   remembered.
3. The screen appears as soon as anything on it changes. Otherwise it takes up
   to 15 seconds, because the MPC sends the whole screen every 15 seconds.
   The status line counts messages and any drops. The MPC sends nothing while
   the transport runs, so the picture pauses during playback.

| Key | View menu |
|---|---|
| `-` / `=` | Zoom out / in (x2, x4, largest that fits) |
| `2` `4` `M` | Zoom x2, x4, max |
| `P` | Next palette (blue/white, yellow-green, black on white, white on black, grey, amber) |
| `L` | Real LCD look / flat pixels |
| `[` / `]` / `0` | Less / more text contrast, reset to 100 % |
| `B` | Cursor blink on / off (rate and fade smoothness are in the menu) |
| `S` | Status line on / off |

## Privacy and safety

- The app only **reads** the MIDI input you choose. It never sends MIDI, never
  uses the network, and collects nothing.
- It writes one small settings file, holding your View and MIDI choices:
  - macOS: `~/Library/Application Support/LCD Mirror/settings.json`
  - Windows: `%APPDATA%\LCD Mirror\settings.json`
- Every message is range-checked before it is drawn. Malformed or foreign SysEx
  is counted and ignored, and the test suite feeds the decoder random data.

## Run from source

Python 3.10 or later, with Tk:

```
python -m pip install -r requirements.txt
python lcd_mirror_viewer.py               # the window
python lcd_mirror_viewer.py --headless    # print screens in the terminal
python -m unittest discover -s tests      # tests
```

`python lcd_mirror_viewer.py --help` lists the options (MIDI input, zoom,
palette, look, contrast, logging).

## Build the apps

The `build` workflow builds all three on every push to `main`. It runs the
tests first, and a tag `v*` turns the result into a draft release. To build
locally on the system you are on:

```
python -m pip install --only-binary=:all: -r requirements-build.txt
python packaging/collect_licenses.py
pyinstaller --noconfirm packaging/lcd_mirror_viewer.spec
```

## Protocol

The MPC side sends messages of at most 64 bytes:
`F0 7D 4D 33 4B <type> <seq> <payload> F7`. `7D` is the non-commercial
manufacturer ID, and `4D 33 4B` is "M3K". The message types are described at the
top of `lcd_mirror_viewer.py`. Any other MIDI on the same input is ignored.

## License

MIT, see [LICENSE](LICENSE). The bundled components keep their own licenses;
see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
