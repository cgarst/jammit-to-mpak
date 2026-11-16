"""Turns a Jammit song folder into a Music Pack (.mpak, https://github.com/cgarst/MusicPackSpec).

What it makes
  scores      one PDF for each notation and tab strip set, built from the strips
  recordings  the backing track ("Band") and one stem for each instrument track, as FLAC
  beats       beats/<root>.json from beats.plist and sections.plist
  sync        one pdf sync file for each score, from nowline.nodes: every beat gets a page and a position
"""
from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__
from .audio import duration_ms, to_flac
from .jcf import Jcf, Track
from .mpak_writer import MpakWriter, derived_pack_id, sha256_file
from .notation import DEFAULT_INTERVAL, build_pdf
from .sections import convert_sections

INSTRUMENTS = {0: "guitar", 1: "bass", 2: "drums", 3: "keys", 4: "vocals"}
KIND_NAME = {"n": "Score", "t": "Tablature"}
PLACEHOLDER_STRINGS = {"C-1"}  # tuning.plist pads the string list with this


@dataclass
class Report:
    carried: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def slug(text: str, used: set[str]) -> str:
    base = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower() or "x"
    base = base[:56]
    name, n = base, 2
    while name in used:
        name, n = f"{base}-{n}", n + 1
    used.add(name)
    return name


def _tuning(jcf: Jcf, track: Track, instrument: str | None) -> list[str] | None:
    notes = (jcf.tuning.get("notes") or {}).get(instrument or "", {})
    digits = re.search(r"(\d+)\s*$", track.title)
    strings = notes.get(f"track{digits.group(1) if digits else 1}")
    if not isinstance(strings, list):
        return None
    strings = [s for s in strings if isinstance(s, str)]
    while strings and strings[-1] in PLACEHOLDER_STRINGS:
        strings.pop()
    return strings or None


def _song(jcf: Jcf, length_ms: int | None, report: Report) -> dict:
    info = jcf.info
    song: dict = {}
    for src, dst in (("title", "title"), ("artist", "artist"), ("album", "album"), ("genre", "genre"),
                     ("publishedBy", "publisher"), ("writtenBy", "composer")):
        if info.get(src):
            song[dst] = str(info[src])
    year = str(info.get("copyright", "")).strip()
    if re.fullmatch(r"\d{4}", year):
        song["year"] = int(year)
    elif year:
        report.dropped.append(f"copyright '{year}' (not a year)")
    if length_ms:
        song["length"] = length_ms
    for key in ("courtesyOf", "bpm", "countInBeats", "skillLevel", "sku", "demo", "explicit", "slow", "version"):
        if key in info:
            report.dropped.append(f"info.plist {key} (no place in the specification)")
    return song


def convert(jcf: Jcf, out: Path, systems_per_page: int = 7, quality: int = 90, work: Path | None = None) -> Report:
    report = Report(warnings=list(jcf.warnings))
    work_ctx = None
    if work is None:
        work_ctx = tempfile.TemporaryDirectory(prefix="jcf2mpak-")
        work = Path(work_ctx.name)
    work.mkdir(parents=True, exist_ok=True)
    try:
        return _convert(jcf, out, systems_per_page, quality, work, report)
    finally:
        if work_ctx:
            work_ctx.cleanup()


def _convert(jcf: Jcf, out: Path, per_page: int, quality: int, work: Path, report: Report) -> Report:
    instrument = INSTRUMENTS.get(jcf.info.get("instrument")) if isinstance(jcf.info.get("instrument"), int) else None
    beats = jcf.beats
    if not beats:
        report.warnings.append("no beats.plist: the file has no beat grid and no sync")
    times: list[int] = []
    for b in beats:
        t = round(float(b["position"]) * 1000)
        times.append(t if not times or t > times[-1] else times[-1] + 1)

    entries: list[tuple[str, Path, bool]] = []  # (archive name, file, store)
    integrity: dict[str, dict] = {}  # sync and beat file -> {size, sha256}, for the manifest's `entries`
    manifest_scores: list[dict] = []
    manifest_recs: list[dict] = []
    sync_files: dict[str, dict] = {}  # score id -> sync doc (recording filled in below)
    used_ids: set[str] = set()

    # --- scores -------------------------------------------------------------------------------------------------
    nowline = {(t.name, t.kind): t for t in jcf.nowline}
    notation_tracks = [t for t in jcf.tracks if t.cls == "JMFileTrack" and jcf.strips(t.identifier, "n")]
    for tr in notation_tracks:
        interval = tr.system_interval or DEFAULT_INTERVAL
        if not tr.system_interval:
            report.warnings.append(f"{tr.title}: no scoreSystemInterval, using {DEFAULT_INTERVAL}")
        for kind in ("t", "n"):  # tab first: it is what a guitarist reads
            strips = jcf.strips(tr.identifier, kind)
            if not strips:
                continue
            song_title = str(jcf.info.get("title") or "").strip()
            label = f"{song_title} - {tr.title} ({KIND_NAME[kind]})" if song_title else f"{tr.title} ({KIND_NAME[kind]})"
            sid = slug(f"{tr.title} {KIND_NAME[kind]}", used_ids)
            node_track = nowline.get((tr.title, KIND_NAME[kind] if kind == "n" else "Tablature"))
            systems = None
            if node_track:
                placed = [n.system for n in node_track.nodes if n]
                systems = max(placed) + 1 if placed else None
            pdf = work / f"{sid}.pdf"
            geo = build_pdf(strips, pdf, interval, systems, per_page, quality)
            entry = {"id": sid, "label": label, "path": f"score/{sid}.pdf", "format": "pdf",
                     "sha256": sha256_file(pdf), "size": pdf.stat().st_size, "name": f"{sid}.pdf", "pages": geo.pages}
            strings = _tuning(jcf, tr, instrument)
            if strings:
                entry["instruments"] = [{"name": tr.title, "tuning": strings}]
            manifest_scores.append(entry)
            entries.append((entry["path"], pdf, False))
            report.carried.append(f"score '{label}': {geo.pages} pages, {geo.systems} systems")

            if node_track and beats:
                points, clamped = [], 0
                for t, node in zip(times, node_track.nodes):
                    if node is None:
                        continue
                    page, x, y = geo.place(node.system, node.x)
                    if node.x < 0 or node.x > geo.width:
                        clamped += 1
                    points.append({"timeMs": t, "page": page, "x": round(x, 5), "y": round(y, 5)})
                if clamped:
                    report.warnings.append(f"{label}: {clamped} beats fell outside the page and were moved to its edge")
                sync_files[sid] = {"version": 1, "score": sid, "anchorType": "pdf", "points": points}
                report.carried.append(f"sync for '{label}': {len(points)} points")
            elif beats:
                report.warnings.append(f"{label}: nowline.nodes has no map for it, so it has no sync")
    if not manifest_scores:
        raise ValueError("this song folder has no notation strips, so the pack would have no score")

    # --- recordings -------------------------------------------------------------------------------------------
    stems, backings = [], []
    rec_ids: dict[str, str] = {}
    audio_tracks = [t for t in jcf.tracks if t.cls == "JMFileTrack" and jcf.audio(t.identifier)]
    for tr in audio_tracks:
        rid = slug(tr.title, set(rec_ids.values()) | used_ids)
        rec_ids[tr.identifier] = rid
        flac = work / f"{rid}.flac"
        to_flac(jcf.audio(tr.identifier), flac)
        src = {"type": "file", "path": f"audio/{rid}.flac", "mime": "audio/flac",
               "durationMs": duration_ms(flac), "sha256": sha256_file(flac), "size": flac.stat().st_size}
        entries.append((src["path"], flac, True))
        is_stem = tr in notation_tracks
        rec = {"id": rid, "role": "stem" if is_stem else "backing", "label": tr.title, "sources": [src]}
        if is_stem:
            rec["stem"] = {"instrument": instrument or "other"}
            stems.append(rec)
        else:
            backings.append(rec)
        report.carried.append(f"recording '{tr.title}' ({rec['role']}), {src['durationMs'] / 1000:.1f} s")
    for t in jcf.tracks:
        if t.cls != "JMFileTrack":
            report.dropped.append(f"track '{t.title}' ({t.cls}): not an audio file in the folder")
    for backing in backings:
        if stems:
            backing["without"] = [s["id"] for s in stems]
            backing["label"] = f"{backing['label']} (without {', '.join(s['label'] for s in stems)})"
    manifest_recs = backings + stems
    if not manifest_recs:
        report.warnings.append("no audio files in the folder")

    # --- beats and sync ---------------------------------------------------------------------------------------
    if manifest_recs:
        root = manifest_recs[0]
        for rec in manifest_recs[1:]:
            rec["syncFrom"] = root["id"]
        if beats:
            doc = {"version": 1, "recording": root["id"], "beats": [
                {"timeMs": t, **({"downbeat": True} if b.get("isDownbeat") else {})} for t, b in zip(times, beats)]}
            secs, warns = convert_sections(jcf.sections, times)
            if secs:
                doc["sections"] = secs
            report.warnings += warns
            beat_path = work / "beats.json"
            beat_path.write_text(__import__("json").dumps(doc, separators=(",", ":")), encoding="utf-8")
            entries.append((f"beats/{root['id']}.json", beat_path, False))
            integrity[f"beats/{root['id']}.json"] = {"size": beat_path.stat().st_size, "sha256": sha256_file(beat_path)}
            root["beats"] = f"beats/{root['id']}.json"
            report.carried.append(f"beats: {len(times)} beats, {sum(1 for b in beats if b.get('isDownbeat'))} downbeats, "
                                  f"{len(secs)} sections")
        root_sync = {}
        for sid, doc in sync_files.items():
            doc["recording"] = root["id"]
            path = work / f"sync-{sid}.json"
            path.write_text(__import__("json").dumps(doc, separators=(",", ":")), encoding="utf-8")
            name = f"sync/{root['id']}-{sid}.json"
            entries.append((name, path, False))
            integrity[name] = {"size": path.stat().st_size, "sha256": sha256_file(path)}
            root_sync[sid] = name
        if root_sync:
            root["sync"] = root_sync

    # --- what is not carried ----------------------------------------------------------------------------------
    notes = {"cover.jpg": "cover art", "patches.plist": "amp and effects settings", "user.plist": "player settings",
             "music.waveform": "waveform cache", "ghost.plist": "ghost beats"}
    for name in sorted(jcf.files):
        if name in notes:
            if name == "ghost.plist" and _same_ghost(jcf):
                report.dropped.append("ghost.plist (the same positions as beats.plist)")
            else:
                report.dropped.append(f"{name} ({notes[name]})")
        elif name.endswith("_anlx"):
            report.dropped.append(f"{name} (audio analysis)")
        elif name.lower().endswith(".l6t"):
            report.dropped.append(f"{name} (Line 6 tone settings)")

    # --- write ------------------------------------------------------------------------------------------------
    song = _song(jcf, manifest_recs[0]["sources"][0]["durationMs"] if manifest_recs else None, report)
    # The pack id (SPEC section 4, "Pack identity"): derived from the primary score, so converting the same file again
    # makes the same id.
    manifest = {"format": "mpak", "version": 1, "id": derived_pack_id(manifest_scores[0]["sha256"]),
                "generator": {"name": "jammit-to-mpak", "version": __version__},
                "song": song, "scores": manifest_scores, "recordings": manifest_recs}
    if integrity:  # the size and hash of the sync and beat files (SPEC section 4, "Integrity")
        manifest["entries"] = integrity
    # Written beside the target and renamed, so an interrupted run never leaves a half-written .mpak that a
    # later run would mistake for a finished one.
    part = out.with_name(out.name + ".part")
    try:
        with MpakWriter(part) as z:
            z.add_json("manifest.json", manifest, indent=2)
            for name, path, store in entries:
                z.add_file(name, path, store)
        part.replace(out)
    finally:
        part.unlink(missing_ok=True)
    return report


def _same_ghost(jcf: Jcf) -> bool:
    try:
        import plistlib
        ghost = plistlib.load(open(jcf.root / "ghost.plist", "rb"))
        return len(ghost) == len(jcf.beats) and all(
            abs(g["position"] - b["position"]) < 1e-9 for g, b in zip(ghost, jcf.beats))
    except Exception:
        return False
