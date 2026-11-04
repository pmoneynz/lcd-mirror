#!/usr/bin/env python3
"""Fail if a built LCD Mirror.app cannot open on the macOS its Info.plist promises.

    python packaging/check_macos_min.py "dist/LCD Mirror.app"

Reads LSMinimumSystemVersion, then the minimum OS (LC_BUILD_VERSION minos, or
LC_VERSION_MIN_MACOSX) of every Mach-O file in the bundle, with `vtool`
(Xcode command-line tools; on every GitHub macOS runner).  Any binary that needs
a newer macOS than the plist states is listed and the exit status is 1.  This is
the check that would have caught 1.1.0: LSMinimumSystemVersion 11.0, and numpy's
macosx_14_0 wheel inside the Intel app.
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys

MAGICS = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}


def version(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split(".") if x.isdigit())


def minimum_os(path: str) -> str | None:
    out = subprocess.run(["vtool", "-show-build", path], capture_output=True, text=True).stdout.splitlines()
    found = []
    for i, line in enumerate(out):
        words = line.split()
        if words[:1] == ["minos"]:
            found.append(words[1])
        elif words[:1] == ["cmd"] and words[1:2] == ["LC_VERSION_MIN_MACOSX"]:
            for nxt in out[i + 1:i + 4]:
                if nxt.split()[:1] == ["version"]:
                    found.append(nxt.split()[1])
                    break
    return max(found, key=version) if found else None


def main(app: str) -> int:
    with open(os.path.join(app, "Contents", "Info.plist"), "rb") as f:
        promised = plistlib.load(f).get("LSMinimumSystemVersion")
    if not promised:
        print("FAIL: Info.plist has no LSMinimumSystemVersion")
        return 1
    too_new, checked = [], 0
    for root, _, files in os.walk(app):
        for name in files:
            p = os.path.join(root, name)
            if os.path.islink(p):
                continue
            with open(p, "rb") as f:
                if f.read(4) not in MAGICS:
                    continue
            checked += 1
            need = minimum_os(p)
            if need and version(need) > version(promised):
                too_new.append((need, os.path.relpath(p, app)))
    if too_new:
        print(f"FAIL: Info.plist promises macOS {promised}, but {len(too_new)} of {checked} binaries need newer:")
        for need, rel in sorted(too_new, key=lambda t: version(t[0]), reverse=True):
            print(f"  {need}  {rel}")
        return 1
    print(f"OK: all {checked} binaries run on macOS {promised} (LSMinimumSystemVersion)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
