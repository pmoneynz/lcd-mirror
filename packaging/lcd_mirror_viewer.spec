# PyInstaller spec for LCD Mirror (macOS .app, Windows folder with .exe).
#
#     python packaging/collect_licenses.py
#     pyinstaller --noconfirm packaging/lcd_mirror_viewer.spec
#
# One folder (not one file), no UPX: both avoid the self-unpacking behaviour
# that virus scanners flag.  The licenses/ folder, LICENSE and
# THIRD_PARTY_NOTICES.md ship inside the app.
import os
import platform
import re
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
NAME = "LCD Mirror"
VERSION = re.search(r'__version__ = "([^"]+)"', (ROOT / "lcd_mirror_viewer.py").read_text()).group(1)
# Oldest macOS the app opens on: Catalina (10.15) for Intel Macs; Apple silicon Macs start at 11.0.
# packaging/check_macos_min.py fails the build if any bundled binary needs a newer macOS than this.
MACOS_MIN = os.environ.get("LCD_MIRROR_MACOS_MIN") or ("10.15" if platform.machine() == "x86_64" else "11.0")

a = Analysis(
    [str(ROOT / "lcd_mirror_viewer.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "licenses"), "licenses"), (str(ROOT / "LICENSE"), "."),
           (str(ROOT / "THIRD_PARTY_NOTICES.md"), ".")],
    hiddenimports=["rtmidi", "lcd_font"],
    # Not used by the app.  ssl and _hashlib would bundle OpenSSL; the app has no networking.
    excludes=["mido", "ssl", "_ssl", "_hashlib", "sqlite3", "_sqlite3"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=NAME, console=False, upx=False,
          argv_emulation=False, codesign_identity=None, entitlements_file=None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=NAME)
if sys.platform == "darwin":
    app = BUNDLE(coll, name=f"{NAME}.app", bundle_identifier="io.github.pmoneynz.lcd-mirror",
                 version=VERSION,
                 info_plist={"CFBundleShortVersionString": VERSION, "CFBundleVersion": VERSION,
                             "NSHighResolutionCapable": True, "LSMinimumSystemVersion": MACOS_MIN,
                             "NSHumanReadableCopyright": "Companion helper for the P-MONEY MPC3000 update. MIT License. Not affiliated with Akai or inMusic."})
