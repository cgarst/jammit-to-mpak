from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from .audio import AudioError
from .convert import convert
from .jcf import JcfError, load
from .nowline import NowlineError


def _find_inputs(folder: Path) -> list[Path]:
    """The song zips and .jcf folders inside a folder of songs, in name order."""
    found = {p for p in folder.rglob("*") if p.is_file() and p.name.lower().endswith(".zip")}
    found |= {p for p in folder.rglob("*.jcf") if p.is_dir()}
    return sorted(found)


def _convert_one(src: Path, output: Path | None, outdir: Path, used: set[Path], args) -> int:
    try:
        jcf = load(src)
        if output:
            out = output
        else:
            out = outdir / (re.sub(r'[\\/:*?"<>|]+', "_", f"{jcf.info.get('artist', 'Unknown')} - {jcf.info.get('title', 'Untitled')}") + ".mpak")
            if out in used:  # another instrument of the same song
                stem = re.sub(r"(\.jcf)?\.zip$|\.jcf$", "", src.name, flags=re.I)
                out = out.with_name(f"{out.stem} ({stem}).mpak")
            used.add(out)
        report = convert(jcf, out, systems_per_page=args.systems_per_page, quality=args.quality)
    except (JcfError, NowlineError, AudioError, ValueError) as e:
        print(f"error: {src}: {e}", file=sys.stderr)
        return 1
    print(f"wrote {out}")
    for title, lines in (("carried", report.carried), ("not carried", report.dropped), ("warnings", report.warnings)):
        if lines:
            print(f"\n{title}:")
            for line in lines:
                print(f"  {line}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jcf2mpak", description="Convert a Jammit song folder (.jcf, or a zip of one) to a Music Pack (.mpak). Given a folder of songs, converts every .zip and .jcf in it.")
    ap.add_argument("input", help="a song folder, a .jcf.zip / .zip of one, or a folder holding several of these")
    ap.add_argument("-o", "--output", help="output file (default: '<artist> - <title>.mpak' in the current directory); with a folder of songs, the output directory")
    ap.add_argument("--systems-per-page", type=int, default=7, help="systems of music on each PDF page (default 7)")
    ap.add_argument("--quality", type=int, default=90, help="JPEG quality of the PDF pages, 1-95 (default 90)")
    args = ap.parse_args(argv)
    src = Path(args.input)
    if src.is_dir() and not (src / "info.plist").is_file():
        inputs = _find_inputs(src)
        if inputs:
            outdir = Path(args.output) if args.output else Path(".")
            outdir.mkdir(parents=True, exist_ok=True)
            used: set[Path] = set()
            failed = 0
            for i, item in enumerate(inputs, 1):
                print(f"[{i}/{len(inputs)}] {item}")
                failed += _convert_one(item, None, outdir, used, args)
            print(f"\n{len(inputs) - failed} converted, {failed} failed")
            return 1 if failed else 0
    return _convert_one(src, Path(args.output) if args.output else None, Path("."), set(), args)


if __name__ == "__main__":
    sys.exit(main())
