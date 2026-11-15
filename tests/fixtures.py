"""Builds a tiny, synthetic Jammit song folder for the tests (no real song data)."""
from __future__ import annotations

import plistlib
import struct
import wave
from pathlib import Path

from PIL import Image, ImageDraw

W, STRIP_H, INTERVAL, STRIPS = 200, 160, 40, 2  # 4 systems of 40 px in each strip, 8 in all
IDS = {"g1": "AAAAAAAA-0000-0000-0000-000000000001", "band": "BBBBBBBB-0000-0000-0000-000000000002"}


def nowline_bytes(tracks: list[tuple[str, str, list[tuple[int, int, float] | None]]]) -> bytes:
    out = struct.pack("<I", len(tracks))
    for name, kind, nodes in tracks:
        out += name.encode().ljust(32, b"\0") + kind.encode().ljust(32, b"\0") + struct.pack("<I", len(nodes))
        for n in nodes:
            out += struct.pack("<If", 0xFFFFFFFF, -1.0) if n is None else struct.pack("<If", (n[1] << 16) | n[0], n[2])
    return out


def beat_nodes(count_in: int = 2, per_system: int = 4):
    """Count-in beats have no place, then `per_system` beats in each system, x spread over the width."""
    beats = 8 * per_system
    nodes = [None] * count_in
    for j in range(beats):
        system, k = divmod(j, per_system)
        nodes.append((j // 4 + 1, system, 20.0 + k * 40.0))
    return nodes


def make_jcf(root: Path, *, count_in: int = 2, with_nowline: bool = True, audio: bool = True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    nodes = beat_nodes(count_in)
    n_beats = len(nodes)
    beats = [{"position": 0.5 * i, **({"isDownbeat": True} if i % 4 == 0 else {})} for i in range(n_beats)]
    (root / "beats.plist").write_bytes(plistlib.dumps(beats))
    (root / "ghost.plist").write_bytes(plistlib.dumps([{"position": b["position"]} for b in beats]))
    (root / "sections.plist").write_bytes(plistlib.dumps([
        {"beat": 0, "number": 0, "type": 0}, {"beat": count_in, "number": 1, "type": 1},
        {"beat": count_in + 8, "number": 1, "type": 2}, {"beat": n_beats - 1, "number": 0, "type": 9}]))
    (root / "info.plist").write_bytes(plistlib.dumps({
        "title": "Test Song", "artist": "Test Artist", "album": "Test Album", "genre": "Rock", "copyright": "1999",
        "publishedBy": "Pub", "writtenBy": "Writer", "courtesyOf": "Somebody", "instrument": 0, "bpm": "120",
        "countInBeats": count_in, "sku": "X", "skillLevel": {"guitar": 3}}))
    (root / "tracks.plist").write_bytes(plistlib.dumps([
        {"class": "JMFileTrack", "identifier": IDS["g1"], "title": "Guitar 1", "scoreSystemInterval": INTERVAL,
         "scoreSystemHeight": INTERVAL + 9},
        {"class": "JMFileTrack", "identifier": IDS["band"], "title": "Band", "scoreSystemInterval": 0, "scoreSystemHeight": 0},
        {"class": "JMClickTrack", "identifier": "CCCCCCCC-0000-0000-0000-000000000003", "title": "Click"}]))
    (root / "tuning.plist").write_bytes(plistlib.dumps(
        {"notes": {"guitar": {"track1": ["D", "A", "D", "G", "B", "E", "C-1"]}}}))
    (root / "cover.jpg").write_bytes(b"jpeg")
    for kind in ("n", "t"):
        for i in range(STRIPS):
            img = Image.new("RGBA", (W, STRIP_H), (255, 255, 255, 0))
            d = ImageDraw.Draw(img)
            for s in range(STRIP_H // INTERVAL):
                d.line([(0, s * INTERVAL + 20), (W, s * INTERVAL + 20)], fill=(0, 0, 0, 255))
            img.save(root / f"{IDS['g1']}_jcf{kind}_{i:02d}", "PNG")
    if with_nowline:
        (root / "nowline.nodes").write_bytes(nowline_bytes([
            ("Guitar 1", "Score", nodes), ("Guitar 1", "Tablature", nodes)]))
    if audio:
        for ident in IDS.values():
            with wave.open(str(root / f"{ident}_jcfx"), "wb") as w:
                w.setnchannels(2), w.setsampwidth(2), w.setframerate(44100)
                w.writeframes(b"\0\0\0\0" * 44100 * 2)
    return root
