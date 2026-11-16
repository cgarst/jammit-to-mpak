"""Audio: Jammit's `_jcfx` files are AIFF-C (IMA ADPCM). ffmpeg decodes them; they are stored as FLAC.

The FLAC block size is set explicitly (FLAC_BLOCK_SIZE). One converted song came out with 64-sample blocks. That is
valid FLAC (the reference `flac -t` and ffprobe accept it), but macOS WebKit (Safari, and the WKWebView in the
MajesticTab desktop app) cannot decode it: `decodeAudioData` fails with "Decoding failed" and an audio element
reports a decode error. Measured on a 5-second clip re-encoded at each size: 16, 32, 64 and 128 samples fail, 256 and
up decode. The seek table made no difference. Readers built on WebKit then show a recording that will not play, so
the pack must not carry such files. No WebKit bug documenting this was found.
"""
from __future__ import annotations

import re
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

AUDIO_FORMATS: dict[str, tuple[str, list[str]]] = {
    "flac": ("audio/flac", ["-c:a", "flac", "-frame_size", str(FLAC_BLOCK_SIZE)]),
    "m4a": ("audio/mp4", ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]),
}


def _format_spec(fmt: str) -> tuple[str, list[str]]:
    if fmt not in AUDIO_FORMATS:
        raise ValueError(f"unsupported audio format {fmt!r}; expected one of {', '.join(AUDIO_FORMATS)}")
    return AUDIO_FORMATS[fmt]


def convert_audio(src: Path, dst: Path, fmt: str = "flac") -> None:
    """Transcodes `src` to `dst` in `fmt` (`flac` or `m4a`)."""
    _, codec_args = _format_spec(fmt)
    run = subprocess.run(
        [_need("ffmpeg"), "-v", "error", "-y", "-i", str(src), "-map_metadata", "-1", *codec_args, str(dst)],
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


def _mix_graph(parts: list[tuple[Path, float]]) -> tuple[list[str], str]:
    """Input flags and filter_complex graph summing `parts` (`(path, gain)`). Negative gain inverts phase;
    `amix` ignores negative weights, so inversion uses a `volume` filter."""
    inputs: list[str] = []
    chains: list[str] = []
    for i, (path, gain) in enumerate(parts):
        inputs += ["-i", str(path)]
        chains.append(f"[{i}:a]aformat=sample_fmts=fltp:channel_layouts=stereo,aresample=44100"
                      f"{',volume=-1' if gain < 0 else ''}[a{i}]")
    labels = "".join(f"[a{i}]" for i in range(len(parts)))
    return inputs, ";".join(chains) + f";{labels}amix=inputs={len(parts)}:normalize=0"


def mix_level_db(parts: list[tuple[Path, float]]) -> float:
    """The RMS level in dB (ffmpeg `volumedetect`) of the sum of the files, each times its gain."""
    inputs, graph = _mix_graph(parts)
    cmd = [_need("ffmpeg"), "-hide_banner", "-nostats", *inputs,
           "-filter_complex", f"{graph},volumedetect", "-f", "null", "-"]
    run = subprocess.run(cmd, capture_output=True, text=True)
    m = re.search(r"mean_volume:\s*(-?[\d.]+|-inf) dB", run.stderr)
    if run.returncode != 0 or not m:
        raise AudioError(f"ffmpeg could not measure the mix: {run.stderr.strip()[-300:]}")
    return float(m.group(1))


def render_mix(parts: list[tuple[Path, float]], dst: Path, fmt: str = "flac") -> None:
    """Renders the sum of `parts` (each `(path, gain)`) to `dst` in `fmt` (`flac` or `m4a`)."""
    _, codec_args = _format_spec(fmt)
    inputs, graph = _mix_graph(parts)
    cmd = [_need("ffmpeg"), "-v", "error", "-y", *inputs, "-filter_complex", graph,
           "-map_metadata", "-1", *codec_args, str(dst)]
    run = subprocess.run(cmd, capture_output=True, text=True)
    if run.returncode != 0:
        raise AudioError(f"ffmpeg could not render residual mix {dst.name}: {run.stderr.strip()}")
