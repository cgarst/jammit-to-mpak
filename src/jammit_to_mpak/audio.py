"""Audio: Jammit's `_jcfx` files are AIFF-C (IMA ADPCM). ffmpeg decodes them; they are stored as FLAC."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class AudioError(RuntimeError):
    pass


def _need(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise AudioError(f"{tool} is required to read Jammit audio and was not found on PATH")
    return path


def to_flac(src: Path, dst: Path) -> None:
    run = subprocess.run(
        [_need("ffmpeg"), "-v", "error", "-y", "-i", str(src), "-map_metadata", "-1", "-c:a", "flac", str(dst)],
        capture_output=True, text=True,
    )
    if run.returncode != 0:
        raise AudioError(f"ffmpeg could not convert {src.name}: {run.stderr.strip()}")


def duration_ms(path: Path) -> int:
    run = subprocess.run(
        [_need("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return round(float(run.stdout.strip()) * 1000)
    except ValueError as e:
        raise AudioError(f"ffprobe could not read the length of {path.name}") from e
