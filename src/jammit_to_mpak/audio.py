"""Audio: Jammit's `_jcfx` files are AIFF-C (IMA ADPCM). ffmpeg decodes them; they are stored as FLAC.

The FLAC block size is set explicitly (FLAC_BLOCK_SIZE). One converted song came out with 64-sample blocks. That is
valid FLAC (the reference `flac -t` and ffprobe accept it), but macOS WebKit (Safari, and the WKWebView in the
MajesticTab desktop app) cannot decode it: `decodeAudioData` fails with "Decoding failed" and an audio element
reports a decode error. Measured on a 5-second clip re-encoded at each size: 16, 32, 64 and 128 samples fail, 256 and
up decode. The seek table made no difference. Readers built on WebKit then show a recording that will not play, so
the pack must not carry such files. No WebKit bug documenting this was found.
"""
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


# The FLAC default for 44.1 kHz audio. Anything from 256 up plays in WebKit; see the module note.
FLAC_BLOCK_SIZE = 4096


def to_flac(src: Path, dst: Path) -> None:
    run = subprocess.run(
        [_need("ffmpeg"), "-v", "error", "-y", "-i", str(src), "-map_metadata", "-1",
         "-c:a", "flac", "-frame_size", str(FLAC_BLOCK_SIZE), str(dst)],
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
