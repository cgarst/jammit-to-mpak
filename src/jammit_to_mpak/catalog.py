"""Expected instrument parts per song, from the fixed Jammit catalog (unjammit.com)."""
from __future__ import annotations

import json
from functools import cache
from importlib.resources import files

from .convert import INSTRUMENTS

ALL_PARTS = [INSTRUMENTS[i] for i in sorted(INSTRUMENTS)]  # guitar, bass, drums, keys, vocals


@cache
def _load_catalog() -> dict[tuple[str, str], list[str]]:
    raw = json.loads(files(__package__).joinpath("catalog.json").read_text(encoding="utf-8"))
    out: dict[tuple[str, str], list[str]] = {}
    for key, parts in raw.items():
        artist, _, title = key.partition("\t")
        out[(artist, title)] = parts
    return out


def _norm(s: str) -> str:
    return " ".join(s.split()).lower()


def expected_parts(artist: str, title: str) -> list[str]:
    """The parts released for `(artist, title)` in the Jammit catalog, or all 5 parts when unlisted."""
    return list(_load_catalog().get((_norm(artist), _norm(title)), ALL_PARTS))
