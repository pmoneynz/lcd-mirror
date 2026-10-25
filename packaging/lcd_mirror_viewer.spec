# PyInstaller spec for LCD Mirror (macOS .app, Windows folder with .exe).
#
#     python packaging/collect_licenses.py
#     pyinstaller --noconfirm packaging/lcd_mirror_viewer.spec
#
# One folder (not one file), no UPX: both avoid the self-unpacking behaviour
# that virus scanners flag.  The licenses/ folder, LICENSE and
# THIRD_PARTY_NOTICES.md ship inside the app.
import re
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
NAME = "LCD Mirror"
VERSION = re.search(r'__version__ = "([^"]+)"', (ROOT / "lcd_mirror_viewer.py").read_text()).group(1)

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
                             "NSHighResolutionCapable": True, "LSMinimumSystemVersion": "11.0",
                             "NSHumanReadableCopyright": "Companion helper for the P-MONEY MPC3000 update. MIT License. Not affiliated with Akai or inMusic."})
