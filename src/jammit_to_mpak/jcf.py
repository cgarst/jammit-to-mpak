"""Reads a Jammit song folder (.jcf): a directory, or a zip of one.

A song folder holds XML property lists (info, tracks, beats, sections, tuning), one audio file per track
(`<id>_jcfx`, AIFF-C), the notation as PNG strips (`<id>_jcfn_NN` notation, `<id>_jcft_NN` tab), and `nowline.nodes`,
a binary map from every beat to a place in each notation strip. Nothing here changes the files.
"""
from __future__ import annotations

import plistlib
import re
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .nowline import NowlineTrack, parse_nowline


class JcfError(Exception):
    """The input is not a usable Jammit song folder."""


@dataclass
class Track:
    identifier: str
    title: str
    cls: str
    system_interval: int = 0
    system_height: int = 0


@dataclass
class Jcf:
    root: Path
    info: dict
    tracks: list[Track]
    beats: list[dict]
    sections: list[dict]
    tuning: dict
    nowline: list[NowlineTrack]
    files: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)

    def strips(self, identifier: str, kind: str) -> list[Path]:
        """The PNG strips of a track, in order. `kind` is 'n' (notation) or 't' (tab)."""
        found = []
        for name in self.files:
            m = re.fullmatch(re.escape(identifier) + r"_jcf" + kind + r"_(\d+)", name)
            if m:
                found.append((int(m.group(1)), self.root / name))
        return [p for _, p in sorted(found)]

    def audio(self, identifier: str) -> Path | None:
        p = self.root / f"{identifier}_jcfx"
        return p if p.is_file() else None


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    base = dest.resolve()
    for info in zf.infolist():
        target = (dest / info.filename).resolve()
        if base != target and base not in target.parents:
            raise JcfError(f"refusing archive entry outside the folder: {info.filename}")
        zf.extract(info, dest)


def _find_root(dest: Path) -> Path:
    if (dest / "info.plist").is_file():
        return dest
    hits = [p.parent for p in dest.rglob("info.plist")]
    if len(hits) == 1:
        return hits[0]
    raise JcfError("no info.plist found: this is not a Jammit song folder")


def _plist(root: Path, name: str, required: bool = True):
    p = root / name
    if not p.is_file():
        if required:
            raise JcfError(f"missing {name}")
        return None
    try:
        with open(p, "rb") as f:
            return plistlib.load(f)
    except Exception as e:  # plistlib raises several types
        raise JcfError(f"{name} is not a readable property list: {e}") from e


def load(path: str | Path, workdir: Path | None = None) -> Jcf:
    """Opens a folder, or a zip (extracted under `workdir`, or a temporary directory that lives until exit)."""
    path = Path(path)
    if path.is_file() and zipfile.is_zipfile(path):
        dest = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="jcf-"))
        dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path) as zf:
            _safe_extract(zf, dest)
        root = _find_root(dest)
    elif path.is_dir():
        root = _find_root(path)
    else:
        raise JcfError(f"{path} is neither a folder nor a zip file")

    info = _plist(root, "info.plist")
    if not isinstance(info, dict):
        raise JcfError("info.plist is not a dictionary")
    tracks = [
        Track(
            identifier=t["identifier"], title=t.get("title", ""), cls=t.get("class", ""),
            system_interval=int(t.get("scoreSystemInterval", 0) or 0),
            system_height=int(t.get("scoreSystemHeight", 0) or 0),
        )
        for t in (_plist(root, "tracks.plist") or [])
        if isinstance(t, dict) and "identifier" in t
    ]
    beats = _plist(root, "beats.plist", required=False) or []
    sections = _plist(root, "sections.plist", required=False) or []
    tuning = _plist(root, "tuning.plist", required=False) or {}
    warnings: list[str] = []

    nowline: list[NowlineTrack] = []
    nl = root / "nowline.nodes"
    if nl.is_file():
        nowline = parse_nowline(nl.read_bytes(), expected_beats=len(beats))
    else:
        warnings.append("no nowline.nodes: the notation cannot be synced")

    files = {p.name for p in root.iterdir() if p.is_file()}
    return Jcf(root, info, tracks, beats, sections, tuning, nowline, files, warnings)
