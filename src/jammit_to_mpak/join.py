"""Glues the parts of one song (`Pt. I`, `Pt. II, III`, ...) into a single .mpak, end to end.

Nothing is added or dropped: the scores are the parts' PDFs appended page after page, each part's sync points and beats
move by the length of the parts before them (and by their pages), and every audio track is the parts' audio joined
without a gap. A track that a part lacks (Jammit only has some stems for some parts) is silent for that part.
"""
from __future__ import annotations

import copy
import json
import re
import subprocess
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__
from .audio import AUDIO_FORMATS, AudioError, _need
from .mpak_writer import MpakWriter, derived_pack_id, sha256_file

_PART_SUFFIX = re.compile(r"\s*\((?:Pt|Part)\b[^)]*\)\s*$", re.IGNORECASE)
_EXT_FORMAT = {".m4a": "m4a", ".flac": "flac"}


class JoinError(Exception):
    pass


@dataclass
class JoinReport:
    carried: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Part:
    path: Path
    manifest: dict
    zf: zipfile.ZipFile
    offset_ms: int = 0       # where the part starts in the joined song
    length_ms: int = 0       # the part's length: the next part starts here
    page_offset: int = 0     # pages before the part's first page


def base_title(title: str) -> str:
    """`A Change of Seasons (Pt. II, III)` -> `A Change of Seasons`."""
    return _PART_SUFFIX.sub("", title).strip()


def default_output_name(first_input: Path) -> str:
    return _PART_SUFFIX.sub("", first_input.stem).strip() + ".mpak" if _PART_SUFFIX.search(first_input.stem) else \
        first_input.stem + " (joined).mpak"


def _probe(path: Path) -> tuple[float, int]:
    """(duration in seconds, sample rate) of an audio file."""
    run = subprocess.run(
        [_need("ffprobe"), "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=sample_rate:format=duration",
         "-of", "json", str(path)], capture_output=True, text=True)
    try:
        info = json.loads(run.stdout)
        return float(info["format"]["duration"]), int(info["streams"][0]["sample_rate"])
    except (ValueError, KeyError, IndexError) as e:
        raise AudioError(f"ffprobe could not read {path.name}") from e


def _extract(zf: zipfile.ZipFile, name: str, dst: Path) -> Path:
    """One archive entry to `dst`. `name` comes from the manifest, so a path that leaves the pack is refused."""
    parts = Path(name).parts
    if Path(name).is_absolute() or ".." in parts:
        raise JoinError(f"unsafe path in manifest: {name}")
    with zf.open(name) as src, open(dst, "wb") as out:
        while chunk := src.read(1 << 20):
            out.write(chunk)
    return dst


def _read_json(zf: zipfile.ZipFile, name: str) -> dict:
    return json.loads(zf.read(name).decode("utf-8"))


def _join_audio(files: list[Path | None], seg_samples: list[int], rate: int, dst: Path, fmt: str) -> None:
    """Decodes each part's file (silence for None), forces it to exactly its segment length, and encodes the lot as one."""
    cmd = [_need("ffmpeg"), "-v", "error", "-y"]
    chains, labels = [], []
    for i, (f, n) in enumerate(zip(files, seg_samples)):
        if f is None:
            cmd += ["-f", "lavfi", "-t", f"{n / rate + 1:.3f}", "-i", f"anullsrc=r={rate}:cl=stereo"]
        else:
            cmd += ["-i", str(f)]
        chains.append(f"[{i}:a]aresample={rate},aformat=sample_fmts=fltp:channel_layouts=stereo,"
                      f"apad=whole_len={n},atrim=end_sample={n},asetpts=PTS-STARTPTS[a{i}]")
        labels.append(f"[a{i}]")
    graph = ";".join(chains) + ";" + "".join(labels) + f"concat=n={len(files)}:v=0:a=1[out]"
    _, codec_args = AUDIO_FORMATS[fmt]
    cmd += ["-filter_complex", graph, "-map", "[out]", "-map_metadata", "-1", "-ar", str(rate), *codec_args, str(dst)]
    run = subprocess.run(cmd, capture_output=True, text=True)
    if run.returncode != 0:
        raise AudioError(f"ffmpeg could not join {dst.name}: {run.stderr.strip()[-400:]}")


def join(inputs: list[Path], output: Path, title: str | None = None) -> JoinReport:
    """Joins the packs `inputs` (in song order) into `output`."""
    from pypdf import PdfReader, PdfWriter

    if len(inputs) < 2:
        raise JoinError("need at least two packs to join")
    report = JoinReport()
    parts: list[_Part] = []
    try:
        for p in inputs:
            try:
                zf = zipfile.ZipFile(p)
                m = json.loads(zf.read("manifest.json").decode("utf-8"))
            except (OSError, zipfile.BadZipFile, KeyError, ValueError) as e:
                raise JoinError(f"{p.name}: not a readable .mpak ({e})") from e
            if m.get("format") != "mpak":
                raise JoinError(f"{p.name}: not an mpak manifest")
            parts.append(_Part(p, m, zf))
        first = parts[0].manifest

        # --- what must agree across the parts
        score_ids = [s["id"] for s in first["scores"]]
        for part in parts[1:]:
            if [s["id"] for s in part.manifest["scores"]] != score_ids:
                raise JoinError(f"{part.path.name}: scores differ from the first part's ({score_ids})")
        tuning_pick: dict[int, list] = {}  # score index -> the instruments (tuning) to write
        for s_i, sid in enumerate(score_ids):
            variants = [(pi, part.manifest["scores"][s_i].get("instruments")) for pi, part in enumerate(parts)]
            size = lambda ins: sum(len(i.get("tuning", [])) for i in ins or [])
            best = max(variants, key=lambda v: (size(v[1]), -v[0]))  # the most strings; the earliest part on a tie
            tuning_pick[s_i] = best[1]
            if any(v[1] != best[1] for v in variants):
                report.warnings.append(f"'{sid}' has different tunings in the parts; one tuning per score, so the widest "
                                       f"({parts[best[0]].path.name}) is used")
        song0 = first["song"]
        new_title = title or base_title(song0["title"])
        if new_title == song0["title"] and not title:
            raise JoinError(f"cannot work out the joined title from '{song0['title']}'; pass --title")
        for part in parts[1:]:
            if part.manifest["song"].get("artist") != song0.get("artist"):
                report.warnings.append(f"{part.path.name}: artist differs from the first part's")

        # --- the tracks: the union, in order of first appearance
        rec_order: list[str] = []
        rec_template: dict[str, dict] = {}
        for part in parts:
            for r in part.manifest["recordings"]:
                if r["id"] not in rec_template:
                    rec_order.append(r["id"])
                    rec_template[r["id"]] = r
                else:
                    t = rec_template[r["id"]]
                    for key in ("role", "stem", "label", "syncFrom", "without"):
                        if t.get(key) != r.get(key):
                            report.warnings.append(f"{part.path.name}: '{r['id']}' has a different {key}; the first one's is used")
        timeline_ids = [i for i in rec_order if "beats" in rec_template[i]]
        if len(timeline_ids) != 1:
            raise JoinError(f"expected one recording with beats and sync, found {timeline_ids}")
        tl = timeline_ids[0]

        with tempfile.TemporaryDirectory(prefix="mpak-join-") as tmp_s:
            tmp = Path(tmp_s)

            # --- extract audio, measure the parts
            audio: dict[tuple[int, str], Path] = {}
            for pi, part in enumerate(parts):
                for r in part.manifest["recordings"]:
                    if len(r["sources"]) != 1 or r["sources"][0].get("type") != "file":
                        raise JoinError(f"{part.path.name}: '{r['id']}' is not a single audio file")
                    src = r["sources"][0]["path"]
                    audio[(pi, r["id"])] = _extract(part.zf, src, tmp / f"p{pi}-{r['id']}{Path(src).suffix}")
            fmt = _EXT_FORMAT.get(Path(rec_template[tl]["sources"][0]["path"]).suffix.lower())
            if not fmt:
                raise JoinError("only .m4a and .flac audio can be joined")
            rate = None
            cum = 0
            for pi, part in enumerate(parts):
                if (pi, tl) not in audio:
                    raise JoinError(f"{part.path.name}: has no '{tl}' recording to take its length from")
                dur, sr = _probe(audio[(pi, tl)])
                rate = rate or sr
                part.offset_ms = cum
                part.length_ms = round(dur * 1000)
                cum += part.length_ms
            total_ms = cum
            bounds = [round(p.offset_ms * rate / 1000) for p in parts] + [round(total_ms * rate / 1000)]
            seg = [bounds[i + 1] - bounds[i] for i in range(len(parts))]

            # --- scores: append the PDFs, remember each part's page offset
            scores_out: list[dict] = []
            pdf_files: list[tuple[str, Path]] = []
            for s_i, s in enumerate(first["scores"]):
                writer = PdfWriter()
                pages = 0
                for pi, part in enumerate(parts):
                    ps = part.manifest["scores"][s_i]
                    f = _extract(part.zf, ps["path"], tmp / f"p{pi}-{Path(ps['path']).name}")
                    n = len(PdfReader(str(f)).pages)
                    if n != ps.get("pages", n):
                        raise JoinError(f"{part.path.name}: {ps['path']} has {n} pages, manifest says {ps['pages']}")
                    if s_i == 0:
                        part.page_offset = pages
                    elif part.page_offset != pages:
                        raise JoinError(f"{part.path.name}: '{s['id']}' has a different page count from the first score")
                    writer.append(str(f))
                    pages += n
                out = tmp / f"joined-{Path(s['path']).name}"
                with open(out, "wb") as fh:
                    writer.write(fh)
                ns = copy.deepcopy(s)
                if tuning_pick[s_i] is not None:
                    ns["instruments"] = copy.deepcopy(tuning_pick[s_i])
                ns["label"] = s["label"].replace(song0["title"], new_title, 1)
                ns["sha256"], ns["size"], ns["pages"] = sha256_file(out), out.stat().st_size, pages
                scores_out.append(ns)
                pdf_files.append((s["path"], out))
                report.carried.append(f"score '{s['id']}': {pages} pages")

            # --- audio: one joined file per track, in parallel
            def do_track(rid: str) -> Path:
                files = [audio.get((pi, rid)) for pi in range(len(parts))]
                dst = tmp / f"joined-{Path(rec_template[rid]['sources'][0]['path']).name}"
                _join_audio(files, seg, rate, dst, fmt)
                return dst

            with ThreadPoolExecutor(max_workers=4) as ex:
                joined_audio = dict(zip(rec_order, ex.map(do_track, rec_order)))

            # --- beats and sync of the timeline recording
            def shift_beats(pi: int, doc: dict) -> dict:
                off = parts[pi].offset_ms
                doc = copy.deepcopy(doc)
                for b in doc.get("beats", []):
                    b["timeMs"] += off
                for sec in doc.get("sections", []):
                    sec["timeMs"] += off
                return doc

            beat_name = rec_template[tl]["beats"]
            beats_doc: dict | None = None
            for pi, part in enumerate(parts):
                r = next((x for x in part.manifest["recordings"] if x["id"] == tl), None)
                if not r or "beats" not in r:
                    raise JoinError(f"{part.path.name}: '{tl}' has no beat file")
                d = shift_beats(pi, _read_json(part.zf, r["beats"]))
                if beats_doc is None:
                    beats_doc = d
                else:
                    beats_doc["beats"] += d["beats"]
                    beats_doc.setdefault("sections", []).extend(d.get("sections", []))
            sect_names = [s["name"] for s in beats_doc.get("sections", [])]
            dupes = sorted({n for n in sect_names if sect_names.count(n) > 1})
            if dupes:
                report.warnings.append(f"section names repeat across parts (kept as they are): {', '.join(dupes)}")
            report.carried.append(f"{len(beats_doc['beats'])} beats, {len(sect_names)} sections")

            sync_out: dict[str, tuple[str, dict]] = {}
            for sid, name in rec_template[tl]["sync"].items():
                merged: dict | None = None
                for pi, part in enumerate(parts):
                    r = next(x for x in part.manifest["recordings"] if x["id"] == tl)
                    d = _read_json(part.zf, r["sync"][sid])
                    if d.get("anchorType") != "pdf":
                        raise JoinError(f"{part.path.name}: sync for '{sid}' is {d.get('anchorType')}, only pdf can be joined")
                    for pt in d["points"]:
                        pt["timeMs"] += part.offset_ms
                        pt["page"] += part.page_offset
                    if merged is None:
                        merged = d
                    else:
                        merged["points"] += d["points"]
                sync_out[sid] = (name, merged)
                report.carried.append(f"sync '{sid}': {len(merged['points'])} points")

            # --- manifest
            integrity: dict[str, dict] = {}
            entries: list[tuple[str, Path | bytes, bool]] = []

            def add_json(name: str, doc: dict) -> None:
                data = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                import hashlib
                integrity[name] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                entries.append((name, data, False))

            add_json(beat_name, beats_doc)
            for sid, (name, doc) in sync_out.items():
                add_json(name, doc)

            recs_out: list[dict] = []
            for rid in rec_order:
                nr = copy.deepcopy(rec_template[rid])
                f = joined_audio[rid]
                dur, _ = _probe(f)
                nr["sources"][0].update(durationMs=round(dur * 1000), sha256=sha256_file(f), size=f.stat().st_size)
                entries.append((nr["sources"][0]["path"], f, True))
                missing = [parts[pi].path.name for pi in range(len(parts)) if (pi, rid) not in audio]
                if missing:
                    report.warnings.append(f"track '{rid}' is silent for {len(missing)} part(s) that lack it: {', '.join(missing)}")
                report.carried.append(f"track '{rid}': {nr['sources'][0]['durationMs'] / 1000:.1f} s")
                recs_out.append(nr)

            song = dict(song0)
            song["title"], song["length"] = new_title, recs_out[rec_order.index(tl)]["sources"][0]["durationMs"]
            manifest = {"format": "mpak", "version": 1, "id": derived_pack_id(scores_out[0]["sha256"]),
                        "generator": {"name": "jammit-to-mpak", "version": __version__},
                        "song": song, "scores": scores_out, "recordings": recs_out, "entries": integrity}
            for k, v in first.items():  # anything else the parts carry (none today) comes from the first part
                manifest.setdefault(k, v)

            output.parent.mkdir(parents=True, exist_ok=True)
            partial = output.with_name(output.name + ".part")
            try:
                with MpakWriter(partial) as z:
                    z.add_json("manifest.json", manifest, indent=2)
                    for name, f in pdf_files:
                        z.add_file(name, f, False)
                    for name, data, store in entries:
                        if isinstance(data, bytes):
                            z.add_bytes(name, data, store)
                        else:
                            z.add_file(name, data, store)
                partial.replace(output)
            finally:
                partial.unlink(missing_ok=True)
            report.carried.append(f"total {total_ms / 1000:.1f} s from {len(parts)} parts")
    finally:
        for part in parts:
            part.zf.close()
    return report
