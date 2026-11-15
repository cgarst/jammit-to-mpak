"""Parser for Jammit's `nowline.nodes`: where each beat falls in each notation strip.

Layout (little endian), worked out from real files and checked against the rendered notation:

    u32 track count
    per track:
        char[32] name        "Guitar 1"
        char[32] kind        "Score" or "Tablature"
        u32 n                one record for each beat of beats.plist
        n x (u32 packed, f32 x)
    nothing after the last track

`packed` is `(system << 16) | bar`. `bar` is the printed bar number (bar 0 and the count-in beats have no place:
`0xFFFFFFFF`, x = -1). `system` is the 0-based row in the stacked notation strips, and `x` is the pixel position of the
beat inside the strip's width (724 px).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

NO_PLACE = 0xFFFFFFFF


@dataclass
class Node:
    bar: int
    system: int
    x: float


@dataclass
class NowlineTrack:
    name: str
    kind: str  # 'Score' | 'Tablature'
    nodes: list[Node | None]  # one for each beat; None for a beat that is not drawn (the count-in)


class NowlineError(ValueError):
    pass


def _text(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("utf-8", "replace")


def parse_nowline(data: bytes, expected_beats: int | None = None) -> list[NowlineTrack]:
    if len(data) < 4:
        raise NowlineError("nowline.nodes is too short")
    (count,) = struct.unpack_from("<I", data, 0)
    off = 4
    tracks: list[NowlineTrack] = []
    for _ in range(count):
        if off + 68 > len(data):
            raise NowlineError("nowline.nodes ends inside a track header")
        name, kind = _text(data[off:off + 32]), _text(data[off + 32:off + 64])
        (n,) = struct.unpack_from("<I", data, off + 64)
        off += 68
        if off + 8 * n > len(data):
            raise NowlineError(f"nowline.nodes ends inside the records of {name} {kind}")
        if expected_beats is not None and n != expected_beats:
            raise NowlineError(f"{name} {kind} has {n} beats, beats.plist has {expected_beats}")
        nodes: list[Node | None] = []
        for j in range(n):
            packed, x = struct.unpack_from("<If", data, off + 8 * j)
            nodes.append(None if packed == NO_PLACE else Node(bar=packed & 0xFFFF, system=packed >> 16, x=x))
        off += 8 * n
        tracks.append(NowlineTrack(name, kind, nodes))
    if off != len(data):
        raise NowlineError(f"{len(data) - off} unexpected bytes after the last track")
    return tracks
