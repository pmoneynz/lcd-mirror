#!/usr/bin/env python3
"""Copy the license texts of everything the app bundles into licenses/.

    python packaging/collect_licenses.py

Run in the build environment, before PyInstaller: the files come from the
installed packages themselves, so they match the versions that get bundled.
Fails if any expected license text is missing.
"""
from __future__ import annotations

import re
import shutil
import sys
import sysconfig
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "licenses"
DISTS = ("python-rtmidi", "numpy", "pillow", "pyinstaller")
NAME = re.compile(r"(licen[cs]e|copying|notice|authors)", re.I)


def copy_dist(dist: str) -> list[str]:
    d = metadata.distribution(dist)
    found = []
    for f in d.files or []:
        if NAME.search(Path(str(f)).name) and not str(f).endswith((".py", ".pyc")):
            src = Path(d.locate_file(f))
            if src.is_file():
                # The file's path inside the package, flattened, so same-named files stay apart.
                dst = OUT / f"{d.metadata['Name']}-{d.version}" / str(f).replace("/", "__").replace("\\", "__")
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
                found.append(str(dst.relative_to(OUT)))
    if not found:
        raise SystemExit(f"no license file found for {dist}")
    return found


def copy_python() -> list[str]:
    stdlib = Path(sysconfig.get_paths()["stdlib"])
    for cand in (stdlib / "LICENSE.txt", Path(sys.base_prefix) / "LICENSE.txt", Path(sys.base_prefix) / "LICENSE"):
        if cand.is_file():
            dst = OUT / f"python-{sys.version.split()[0]}" / "LICENSE.txt"
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(cand, dst)
            return [str(dst.relative_to(OUT))]
    raise SystemExit("Python's LICENSE.txt not found")


def copy_cpython_incorporated() -> list[str]:
    """CPython's own notices for the libraries it is built with (expat, libffi, zlib, libmpdec, ...),
    Doc/license.rst at the exact version being bundled."""
    import urllib.request
    ver = "v" + sys.version.split()[0]
    url = f"https://raw.githubusercontent.com/python/cpython/{ver}/Doc/license.rst"
    with urllib.request.urlopen(url, timeout=60) as r:
        text = r.read().decode()
    if "Licenses and Acknowledgements for Incorporated Software" not in text:
        raise SystemExit(f"unexpected content from {url}")
    dst = OUT / f"python-{sys.version.split()[0]}" / "license.rst"
    dst.write_text(f"Source: {url}\n\n{text}")
    return [str(dst.relative_to(OUT))]


def copy_tcl_tk() -> list[str]:
    """Tcl and Tk ship the same license.terms; Tcl's is found without opening a window."""
    import tkinter
    tcl = tkinter.Tcl()
    lib = Path(tcl.eval("info library"))                # e.g. .../lib/tcl8.6 (python.org) or Homebrew's
    cands = [lib / "license.terms", lib.parent / "license.terms", lib.parent.parent / "license.terms"]
    src = next((c for c in cands if c.is_file()), None)
    if src is None:
        raise SystemExit(f"Tcl/Tk license.terms not found near {lib}")
    dst = OUT / f"tcl-tk-{tcl.eval('info patchlevel')}" / "license.terms"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    return [str(dst.relative_to(OUT))]


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    found = copy_python() + copy_cpython_incorporated() + copy_tcl_tk()
    for dist in DISTS:
        found += copy_dist(dist)
    (OUT / "README.txt").write_text(
        "License texts of the components bundled in LCD Mirror for MPC3000, copied from the\n"
        "installed packages at build time. Tcl and Tk share one license (license.terms).\n"
        "PyInstaller's bootloader is GPL-2.0 with an exception that allows distributing\n"
        "bundled applications under any license (see its COPYING.txt).\n\n" + "\n".join(found) + "\n")
    print("\n".join(found))


if __name__ == "__main__":
    main()
