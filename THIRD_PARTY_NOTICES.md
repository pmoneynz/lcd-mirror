# Third-party notices

The downloadable apps bundle these components. Their full license texts are
in the `licenses/` folder inside each app, copied from the installed packages
at build time (`packaging/collect_licenses.py`). The apps leave out Python's `ssl` and
`sqlite3` modules, so no OpenSSL or SQLite is bundled.

| Component | Version | License |
|---|---|---|
| Python | 3.12 | Python Software Foundation License. The libraries CPython is built with (expat, libffi, zlib, libmpdec and others) are covered by CPython's `Doc/license.rst`, included for the exact version |
| Tcl/Tk (via tkinter) | 8.6 | Tcl/Tk license (BSD-style) |
| python-rtmidi, including RtMidi | 1.5.8 | MIT; RtMidi uses an MIT-style license |
| NumPy, including its bundled libraries (OpenBLAS and others) | 2.5.4 | BSD-3-Clause; the bundled libraries' licenses are in NumPy's license file |
| Pillow, including its bundled image libraries | 13.0.0 | MIT-CMU (HPND); the bundled libraries' licenses are in Pillow's license file |
| PyInstaller bootloader | 6.22.3 | GPL-2.0 with the PyInstaller exception, which allows distributing bundled apps under any license |

The 5×7 character shapes in `lcd_font.py` are simple dot-matrix patterns.
Some were read from photos of an MPC3000 screen, as the pixels it showed. The
others were designed for this project. No ROM dump, firmware or other Akai
file is included.
