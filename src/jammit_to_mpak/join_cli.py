from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .audio import AudioError
from .join import JoinError, default_output_name, join


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="mpak-join",
        description="Glue the parts of one song (e.g. 'Pt. I', 'Pt. II, III') into a single .mpak, in the order given.")
    ap.add_argument("parts", nargs="+", type=Path, help="the .mpak files, in song order")
    ap.add_argument("-o", "--output", type=Path, help="output file (default: the first name without its '(Pt. ...)', in the current folder)")
    ap.add_argument("--title", help="song title of the joined pack (default: the first part's title without '(Pt. ...)')")
    ap.add_argument("--overwrite", action="store_true", help="replace the output if it exists")
    args = ap.parse_args(argv)
    out = args.output or Path(default_output_name(args.parts[0]))
    if out.exists() and not args.overwrite:
        print(f"error: {out} exists (use --overwrite)", file=sys.stderr)
        return 1
    if out.resolve() in {p.resolve() for p in args.parts}:
        print("error: the output is one of the inputs", file=sys.stderr)
        return 1
    try:
        report = join(args.parts, out, args.title)
    except (JoinError, AudioError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"wrote {out}")
    for head, lines in (("carried", report.carried), ("warnings", report.warnings)):
        if lines:
            print(f"\n{head}:")
            for line in lines:
                print(f"  {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
