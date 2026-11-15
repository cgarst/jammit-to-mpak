from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from .audio import AudioError
from .convert import convert
from .jcf import JcfError, load
from .nowline import NowlineError


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jcf2mpak", description="Convert a Jammit song folder (.jcf, or a zip of one) to a Music Pack (.mpak).")
    ap.add_argument("input", help="a song folder, or a .jcf.zip / .zip of one")
    ap.add_argument("-o", "--output", help="output file (default: '<artist> - <title>.mpak' in the current directory)")
    ap.add_argument("--systems-per-page", type=int, default=7, help="systems of music on each PDF page (default 7)")
    ap.add_argument("--quality", type=int, default=90, help="JPEG quality of the PDF pages, 1-95 (default 90)")
    args = ap.parse_args(argv)
    try:
        jcf = load(args.input)
        out = Path(args.output) if args.output else Path(
            re.sub(r'[\\/:*?"<>|]+', "_", f"{jcf.info.get('artist', 'Unknown')} - {jcf.info.get('title', 'Untitled')}") + ".mpak")
        report = convert(jcf, out, systems_per_page=args.systems_per_page, quality=args.quality)
    except (JcfError, NowlineError, AudioError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"wrote {out}")
    for title, lines in (("carried", report.carried), ("not carried", report.dropped), ("warnings", report.warnings)):
        if lines:
            print(f"\n{title}:")
            for line in lines:
                print(f"  {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
