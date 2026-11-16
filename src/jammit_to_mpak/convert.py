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
from typing import Sequence
from pathlib import Path

from . import __version__
from .audio import AUDIO_FORMATS, convert_audio, duration_ms, mix_level_db, render_mix
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


def instrument_of(jcf: Jcf) -> str | None:
    v = jcf.info.get("instrument")
    return INSTRUMENTS.get(v) if isinstance(v, int) else None


def convert(jcf: Jcf, out: Path, systems_per_page: int = 7, quality: int = 90, work: Path | None = None,
            companions: Sequence[Jcf] = (), audio_format: str = "flac") -> Report:
    """`companions` are the folders of the other instruments of the same song. With any, the pack holds the stems of
    every folder (the score and the cursor are the primary folder's) and no Band track: the full mix and every
    mix without a part is the stems played together."""
    if audio_format not in AUDIO_FORMATS:
        raise ValueError(f"unsupported audio format {audio_format!r}; expected one of {', '.join(AUDIO_FORMATS)}")
    report = Report(warnings=list(jcf.warnings))
    work_ctx = None
    if work is None:
        work_ctx = tempfile.TemporaryDirectory(prefix="jcf2mpak-")
        work = Path(work_ctx.name)
    work.mkdir(parents=True, exist_ok=True)
    try:
        return _convert(jcf, out, systems_per_page, quality, work, report, list(companions), audio_format)
    finally:
        if work_ctx:
            work_ctx.cleanup()


def _convert(jcf: Jcf, out: Path, per_page: int, quality: int, work: Path, report: Report,
             companions: list[Jcf], audio_format: str) -> Report:
    instrument = instrument_of(jcf)
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
    stem_mode = bool(companions)
    # (folder, track, is a stem). A folder's Band track is a backing recording, unless the stems are all there is.
    wanted: list[tuple[Jcf, Track, bool]] = []
    for folder in [jcf, *companions]:
        notated = {t.identifier for t in folder.tracks if folder.strips(t.identifier, "n")}
        for tr in folder.tracks:
            if tr.cls != "JMFileTrack" or not folder.audio(tr.identifier):
                continue
            if stem_mode:
                if tr.title.strip().lower() != "band":
                    wanted.append((folder, tr, True))
            else:
                wanted.append((folder, tr, tr.identifier in notated))
    mime, _ = AUDIO_FORMATS[audio_format]

    def _add_audio(rid: str, audio_file: Path) -> dict:
        src = {"type": "file", "path": f"audio/{rid}.{audio_format}", "mime": mime,
               "durationMs": duration_ms(audio_file), "sha256": sha256_file(audio_file), "size": audio_file.stat().st_size}
        entries.append((src["path"], audio_file, True))
        return src

    first_ms = None
    for folder, tr, is_stem in wanted:
        rid = slug(tr.title, set(rec_ids.values()) | used_ids)
        rec_ids[tr.identifier] = rid
        audio_file = work / f"{rid}.{audio_format}"
        convert_audio(folder.audio(tr.identifier), audio_file, audio_format)
        src = _add_audio(rid, audio_file)
        rec = {"id": rid, "role": "stem" if is_stem else "backing", "label": tr.title, "sources": [src]}
        if is_stem:
            has_notes = bool(folder.strips(tr.identifier, "n"))
            rec["stem"] = {"instrument": (instrument_of(folder) if has_notes else None) or "other"}
            stems.append(rec)
        else:
            backings.append(rec)
        report.carried.append(f"recording '{tr.title}' ({rec['role']}), {src['durationMs'] / 1000:.1f} s")
        if first_ms is None:
            first_ms = src["durationMs"]
        elif is_stem and stem_mode and abs(src["durationMs"] - first_ms) > 50:
            report.warnings.append(f"'{tr.title}' is {src['durationMs'] / 1000:.2f} s, the first stem {first_ms / 1000:.2f} s: "
                                   "the stems may not line up")
    for t in jcf.tracks:
        if t.cls != "JMFileTrack":
            report.dropped.append(f"track '{t.title}' ({t.cls}): not an audio file in the folder")
    if stem_mode:
        residual = _check_companions(jcf, companions, report)
        if residual:
            parts, level_diff = residual
            rid = slug("Other", set(rec_ids.values()) | used_ids)
            audio_file = work / f"{rid}.{audio_format}"
            render_mix(parts, audio_file, audio_format)
            src = _add_audio(rid, audio_file)
            stems.append({"id": rid, "role": "stem", "label": "Other", "sources": [src],
                          "stem": {"instrument": "other"}})
            report.carried.append(f"recording 'Other' (stem, recovered {level_diff:.1f} dB below the mix), "
                                  f"{src['durationMs'] / 1000:.1f} s")
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


def _check_companions(jcf: Jcf, companions: list[Jcf], report: Report) -> tuple[list[tuple[Path, float]], float] | None:
    """Checks that the other folders match the primary one, and how much of its mix the stems cover.
    Returns `(residual_parts, gap_minus_full_db)` when the stems leave out part of the mix (> -20 dB)."""
    for c in companions:
        name = instrument_of(c) or "other"
        report.dropped.append(f"{name} folder: notation, cursor data and Band track (its stems stand in for the Band)")
        same = len(c.beats) == len(jcf.beats) and all(
            abs(a["position"] - b["position"]) < 0.002 for a, b in zip(c.beats, jcf.beats))
        if not same:
            report.warnings.append(f"the {name} folder's beats differ from the primary folder's: its stems may not line up")
    band = next((t for t in jcf.tracks if t.cls == "JMFileTrack" and t.title.strip().lower() == "band"
                 and jcf.audio(t.identifier)), None)
    if not band:
        return None
    mine = [t for t in jcf.tracks if t.cls == "JMFileTrack" and jcf.audio(t.identifier) and t is not band]
    others = [(c.audio(t.identifier), -1.0) for c in companions for t in c.tracks
              if t.cls == "JMFileTrack" and c.audio(t.identifier) and t.title.strip().lower() != "band"]
    residual_parts = [(jcf.audio(band.identifier), 1.0)] + others
    try:
        full = mix_level_db([(jcf.audio(band.identifier), 1.0)] + [(jcf.audio(t.identifier), 1.0) for t in mine])
        gap = mix_level_db(residual_parts)
    except Exception as e:  # a failed measurement must not fail the conversion
        report.warnings.append(f"could not check that the stems cover the mix: {e}")
        return None
    diff = gap - full
    report.carried.append(f"stems against the {instrument_of(jcf) or 'primary'} folder's full mix: "
                          f"what no stem carries is {diff:.1f} dB below it")
    if diff > -20:
        return residual_parts, diff
    return None


def _same_ghost(jcf: Jcf) -> bool:
    try:
        import plistlib
        ghost = plistlib.load(open(jcf.root / "ghost.plist", "rb"))
        return len(ghost) == len(jcf.beats) and all(
            abs(g["position"] - b["position"]) < 1e-9 for g, b in zip(ghost, jcf.beats))
    except Exception:
        return False
