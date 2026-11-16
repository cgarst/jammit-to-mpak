from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from .audio import AudioError
from .convert import INSTRUMENTS, convert
from .jcf import JcfError, load, peek_info
from .nowline import NowlineError


def _find_inputs(folder: Path) -> list[Path]:
    """The song zips and .jcf folders inside a folder of songs, in name order."""
    found = {p for p in folder.rglob("*") if p.is_file() and p.name.lower().endswith(".zip")}
    found |= {p for p in folder.rglob("*.jcf") if p.is_dir()}
    return sorted(found)


SKIPPED = 2


PARTS = [INSTRUMENTS[i] for i in sorted(INSTRUMENTS)]  # guitar, bass, drums, keys, vocals


def _song_groups(inputs: list[Path]) -> dict[tuple[str, str], dict[str, Path]]:
    """The inputs of each song (by artist and title in info.plist), by instrument. The first of two of an instrument wins."""
    groups: dict[tuple[str, str], dict[str, Path]] = {}
    for item in inputs:
        info = peek_info(item)
        part = INSTRUMENTS.get(info.get("instrument")) if info and isinstance(info.get("instrument"), int) else None
        if not info or not part:
            continue
        key = (str(info.get("artist", "")).strip().lower(), str(info.get("title", "")).strip().lower())
        groups.setdefault(key, {}).setdefault(part, item)
    return groups


def _convert_stems(parts: dict[str, Path], output: Path | None, outdir: Path, args, skip_existing: bool = False) -> int:
    """One song from the zips of all its parts, as stems. Same return codes as `_convert_one`."""
    missing = [p for p in PARTS if p not in parts]
    if not parts:
        print("error: not a Jammit song folder (no instrument in its info.plist)", file=sys.stderr)
        return 1
    try:
        primary = load(parts["guitar"]) if "guitar" in parts else load(next(iter(parts.values())))
        label = f"{primary.info.get('artist', 'Unknown')} - {primary.info.get('title', 'Untitled')}"
        if missing:
            raise ValueError(f"{label}: --stem needs the zip of every part; missing {', '.join(missing)}")
        out = output or outdir / (re.sub(r'[\\/:*?"<>|]+', "_", label) + " (stems).mpak")
        if skip_existing and out.exists():
            print(f"skipped {out} (already exists)")
            return SKIPPED
        report = convert(primary, out, systems_per_page=args.systems_per_page, quality=args.quality,
                         companions=[load(parts[p]) for p in PARTS if p != "guitar"])
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


def _main_stems(args, src: Path) -> int:
    if src.is_dir() and not (src / "info.plist").is_file():  # a folder of songs: every song that has all its parts
        groups = _song_groups(_find_inputs(src))
        outdir = Path(args.output) if args.output else Path(".")
        outdir.mkdir(parents=True, exist_ok=True)
        done = skipped = failed = 0
        for i, parts in enumerate(groups.values(), 1):
            if any(p not in parts for p in PARTS):
                skipped += 1
                print(f"skipped {next(iter(parts.values())).name}: missing {', '.join(p for p in PARTS if p not in parts)}")
                continue
            print(f"[{i}/{len(groups)}] {parts['guitar']}")
            result = _convert_stems(parts, None, outdir, args, skip_existing=not args.overwrite)
            done += result == 0
            skipped += result == SKIPPED
            failed += result == 1
        print(f"\n{done} converted, {skipped} skipped, {failed} failed")
        return 1 if failed else 0
    # one song: its other parts are the zips beside it
    info = peek_info(src)
    if not info:
        print(f"error: {src}: not a Jammit song folder", file=sys.stderr)
        return 1
    key = (str(info.get("artist", "")).strip().lower(), str(info.get("title", "")).strip().lower())
    siblings = _find_inputs(src.resolve().parent)
    parts = _song_groups([src.resolve()] + [p for p in siblings if p != src.resolve()]).get(key, {})
    return _convert_stems(parts, Path(args.output) if args.output else None, Path("."), args)


def _convert_one(src: Path, output: Path | None, outdir: Path, used: set[Path], args, skip_existing: bool = False) -> int:
    """0 converted, 1 failed, SKIPPED when its .mpak already exists (only if `skip_existing`)."""
    try:
        jcf = load(src)
        if output:
            out = output
        else:
            out = outdir / (re.sub(r'[\\/:*?"<>|]+', "_", f"{jcf.info.get('artist', 'Unknown')} - {jcf.info.get('title', 'Untitled')}") + ".mpak")
            if out in used:  # another instrument of the same song
                stem = re.sub(r"(\.jcf)?\.zip$|\.jcf$", "", src.name, flags=re.I)
                out = out.with_name(f"{out.stem} ({stem}).mpak")
            used.add(out)  # recorded even when skipped, so the names of later instruments stay the same
            if skip_existing and out.exists():
                print(f"skipped {out} (already exists)")
                return SKIPPED
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
    ap.add_argument("--overwrite", action="store_true", help="with a folder of songs, convert every song again instead of skipping those whose .mpak already exists")
    ap.add_argument("--stem", action="store_true", help="build the pack from the stems of every part (guitar, bass, drums, keys and vocals) instead of the Band track, so a reader can play any mix of them. Needs the zip of every part of the song, in the folder given or beside the zip given")
    args = ap.parse_args(argv)
    src = Path(args.input)
    if args.stem:
        return _main_stems(args, src)
    if src.is_dir() and not (src / "info.plist").is_file():
        inputs = _find_inputs(src)
        if inputs:
            outdir = Path(args.output) if args.output else Path(".")
            outdir.mkdir(parents=True, exist_ok=True)
            used: set[Path] = set()
            failed = skipped = 0
            for i, item in enumerate(inputs, 1):
                print(f"[{i}/{len(inputs)}] {item}")
                result = _convert_one(item, None, outdir, used, args, skip_existing=not args.overwrite)
                if result == SKIPPED:
                    skipped += 1
                else:
                    failed += result
            print(f"\n{len(inputs) - failed - skipped} converted, {skipped} skipped, {failed} failed")
            return 1 if failed else 0
    return _convert_one(src, Path(args.output) if args.output else None, Path("."), set(), args)


if __name__ == "__main__":
    sys.exit(main())
