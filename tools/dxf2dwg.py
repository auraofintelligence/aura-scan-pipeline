#!/usr/bin/env python3
"""Convert an aura-scan DXF export to DWG with the free ODA File Converter.

Usage:
    python tools/dxf2dwg.py scans/<name>/exports/<name>.dxf

The DWG is written next to the DXF. Native DWG writing is deliberately out of
scope for the pipeline (see DESIGN.md); this helper shells out to
ODAFileConverter.exe when it is installed. Stdlib only, so it runs outside the
project virtual environment too.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

DOWNLOAD_URL = "https://www.opendesign.com/guestfiles/oda_file_converter"

NOT_FOUND_MESSAGE = f"""\
ODA File Converter was not found on this machine.

Option 1 (recommended): install the free ODA File Converter from
    {DOWNLOAD_URL}
It normally installs under C:\\Program Files\\ODA\\ and this script will find
it there automatically next time.

Option 2: open the DXF in FreeCAD (free, https://www.freecad.org) and export
to DWG from there. Note that FreeCAD's DWG support also relies on the ODA
converter, so option 1 is usually the simpler path.

No conversion is strictly required: AutoCAD and most CAD packages open DXF
natively, so sharing the DXF as-is often works.
"""


def find_converter() -> Path | None:
    """Look for ODAFileConverter.exe on PATH, then in the usual install roots."""
    for name in ("ODAFileConverter", "ODAFileConverter.exe"):
        hit = shutil.which(name)
        if hit:
            return Path(hit)
    roots = []
    for env in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        value = os.environ.get(env)
        if value and Path(value) not in roots:
            roots.append(Path(value))
    for root in roots:
        oda_dir = root / "ODA"
        if not oda_dir.is_dir():
            continue
        hits = sorted(oda_dir.rglob("ODAFileConverter.exe"))
        if hits:
            return hits[-1]  # highest version folder sorts last
    return None


def convert(dxf: Path, converter: Path, acad_version: str) -> int:
    """Run the converter on one file, in place. Returns a process exit code."""
    folder = dxf.parent
    dwg = dxf.with_suffix(".dwg")
    mtime_before = dwg.stat().st_mtime if dwg.exists() else None

    # CLI contract: input folder, output folder, output version, output type,
    # recurse folders (0/1), audit (0/1), optional input file filter.
    cmd = [str(converter), str(folder), str(folder),
           acad_version, "DWG", "0", "1", dxf.name]
    print("Running:", subprocess.list2cmdline(cmd))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"Failed to run the converter: {exc}", file=sys.stderr)
        return 1

    produced = dwg.exists() and (mtime_before is None
                                 or dwg.stat().st_mtime != mtime_before)
    if result.returncode != 0 or not produced:
        print("Conversion did not produce a DWG.", file=sys.stderr)
        if result.stdout.strip():
            print(result.stdout.strip(), file=sys.stderr)
        if result.stderr.strip():
            print(result.stderr.strip(), file=sys.stderr)
        print(f"Check {folder} for a .err file with the converter's audit "
              "details, and confirm the DXF opens in a CAD viewer.",
              file=sys.stderr)
        return 1
    print(f"Wrote {dwg}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert a .dxf file to .dwg in place using the free "
                    "ODA File Converter.")
    parser.add_argument("dxf", type=Path, help="path to the .dxf file to convert")
    parser.add_argument("--acad-version", default="ACAD2018",
                        help="output DWG version in ODA naming, e.g. ACAD2018, "
                             "ACAD2013, ACAD2010 (default: ACAD2018)")
    parser.add_argument("--converter", type=Path, default=None,
                        help="explicit path to ODAFileConverter.exe, if it is "
                             "installed somewhere unusual")
    args = parser.parse_args(argv)

    dxf = args.dxf.resolve()
    if not dxf.is_file():
        print(f"No such file: {dxf}", file=sys.stderr)
        return 2
    if dxf.suffix.lower() != ".dxf":
        print(f"Expected a .dxf file, got: {dxf.name}", file=sys.stderr)
        return 2

    converter = args.converter.resolve() if args.converter else find_converter()
    if converter is None or not converter.is_file():
        print(NOT_FOUND_MESSAGE, file=sys.stderr)
        return 1
    return convert(dxf, converter, args.acad_version)


if __name__ == "__main__":
    sys.exit(main())
