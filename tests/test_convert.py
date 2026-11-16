import hashlib
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from jammit_to_mpak.cli import main
from jammit_to_mpak.convert import convert
from jammit_to_mpak.jcf import load
from jammit_to_mpak.mpak_writer import derived_pack_id
from fixtures import make_jcf

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


@pytest.fixture
def packed(tmp_path):
    jcf = load(make_jcf(tmp_path / "song"))
    out = tmp_path / "out.mpak"
    report = convert(jcf, out, systems_per_page=4, work=tmp_path / "work")
    z = zipfile.ZipFile(out)
    return z, json.loads(z.read("manifest.json")), report, out


def test_layout_and_order(packed):
    z, m, _, _ = packed
    names = z.namelist()
    assert names[0] == "manifest.json" and "mimetype" not in names
    assert {"score/guitar-1-tablature.pdf", "score/guitar-1-score.pdf", "audio/band.flac", "audio/guitar-1.flac",
            "beats/band.json", "sync/band-guitar-1-score.json", "sync/band-guitar-1-tablature.json"} <= set(names)
    assert all(i.compress_type == zipfile.ZIP_STORED for i in z.infolist() if i.filename.startswith("audio/"))


def test_song_and_scores(packed):
    _, m, _, _ = packed
    assert m["format"] == "mpak" and m["version"] == 1
    assert m["id"] == derived_pack_id(m["scores"][0]["sha256"])  # SPEC section 4: the same file gives the same id
    assert m["song"] == {"title": "Test Song", "artist": "Test Artist", "album": "Test Album", "genre": "Rock",
                         "publisher": "Pub", "composer": "Writer", "year": 1999, "length": 2000}
    assert [s["id"] for s in m["scores"]] == ["guitar-1-tablature", "guitar-1-score"]
    s = m["scores"][0]
    assert s["format"] == "pdf" and s["pages"] == 2 and len(s["sha256"]) == 64
    assert s["instruments"] == [{"name": "Guitar 1", "tuning": ["D", "A", "D", "G", "B", "E"]}]  # the C-1 padding is gone


def test_hashes_match(packed):
    z, m, _, _ = packed
    for s in m["scores"]:
        assert hashlib.sha256(z.read(s["path"])).hexdigest() == s["sha256"]
    for r in m["recordings"]:
        for src in r["sources"]:
            assert hashlib.sha256(z.read(src["path"])).hexdigest() == src["sha256"]


def test_recordings(packed):
    _, m, _, _ = packed
    band, g1 = m["recordings"]
    assert band["id"] == "band" and band["role"] == "backing" and band["without"] == ["guitar-1"]
    assert g1["id"] == "guitar-1" and g1["role"] == "stem" and g1["stem"] == {"instrument": "guitar"} and g1["syncFrom"] == "band"
    assert band["beats"] == "beats/band.json" and set(band["sync"]) == {"guitar-1-tablature", "guitar-1-score"}


def test_beats_and_sections(packed):
    z, _, _, _ = packed
    doc = json.loads(z.read("beats/band.json"))
    assert doc["recording"] == "band" and len(doc["beats"]) == 34
    assert doc["beats"][0] == {"timeMs": 0, "downbeat": True} and doc["beats"][1] == {"timeMs": 500}
    assert doc["sections"] == [{"name": "Intro 1", "type": "intro", "timeMs": 1000},
                               {"name": "Verse 1", "type": "verse", "timeMs": 5000}]  # the boundary codes 0 and 9 are left out


def test_sync_points_are_page_and_position(packed):
    z, _, _, _ = packed
    doc = json.loads(z.read("sync/band-guitar-1-score.json"))
    assert doc["recording"] == "band" and doc["score"] == "guitar-1-score" and doc["anchorType"] == "pdf"
    pts = doc["points"]
    assert len(pts) == 32                    # the two count-in beats have no place
    assert pts[0] == {"timeMs": 1000, "page": 1, "x": 0.1, "y": 0.125}
    assert pts[4]["page"] == 1 and pts[4]["y"] == 0.375                  # second system, page 1
    assert pts[16]["page"] == 2 and pts[16]["y"] == 0.125                # fifth system, first row of page 2
    assert all(b["timeMs"] > a["timeMs"] for a, b in zip(pts, pts[1:]))


def test_report_says_what_is_left_out(packed):
    _, _, report, _ = packed
    text = "\n".join(report.dropped)
    for word in ("cover.jpg", "courtesyOf", "Click", "ghost.plist", "sku"):
        assert word in text


def test_without_nowline_there_is_no_sync(tmp_path):
    jcf = load(make_jcf(tmp_path / "song", with_nowline=False))
    out = tmp_path / "o.mpak"
    report = convert(jcf, out, work=tmp_path / "w")
    m = json.loads(zipfile.ZipFile(out).read("manifest.json"))
    assert "sync" not in m["recordings"][0] and any("nowline" in w for w in report.warnings)


def test_validates_against_the_specification_reference_tool(packed):
    spec = os.environ.get("MPAK_SPEC_DIR")
    node = shutil.which("node")
    if not spec or not node:
        pytest.skip("set MPAK_SPEC_DIR to a MusicPakSpec checkout (and have node) to run this")
    _, _, _, out = packed
    run = subprocess.run([node, str(Path(spec) / "reference/js/bin/mpak.js"), "validate", str(out)], capture_output=True, text=True)
    assert run.returncode == 0 and "0 warnings" in run.stdout, run.stdout + run.stderr


def test_flac_block_size_is_one_webkit_can_decode(packed):
    # Blocks under 256 samples are valid FLAC, but macOS WebKit cannot decode them (see audio.py)
    z, _, _, _ = packed
    for name in (n for n in z.namelist() if n.endswith(".flac")):
        data = z.read(name)
        # fLaC, then the STREAMINFO header (4 bytes), then the minimum and maximum block size as 16-bit numbers
        assert data[:4] == b"fLaC"
        low, high = int.from_bytes(data[8:10], "big"), int.from_bytes(data[10:12], "big")
        assert low == high == 4096, f"{name}: block sizes {low}..{high}"


def test_derived_pack_id_matches_the_spec_vector():
    assert derived_pack_id("0" * 64) == "8980d354-7334-5de0-8804-bb35580aea65"
    assert derived_pack_id("A" * 64) == derived_pack_id("a" * 64)


def test_sizes_and_entries_are_true(packed):
    """SPEC section 4, "Integrity": size on scores and audio, entries for the sync and beat files."""
    z, m, _, _ = packed
    for s in m["scores"]:
        assert s["size"] == len(z.read(s["path"]))
    for r in m["recordings"]:
        for src in r["sources"]:
            if src["type"] == "file":
                assert src["size"] == z.getinfo(src["path"]).file_size
    paths = {p for r in m["recordings"] for p in [*r.get("sync", {}).values(), r.get("beats")] if p}
    assert paths and set(m["entries"]) == paths
    for path, info in m["entries"].items():
        data = z.read(path)
        assert info == {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def test_m4a_format_via_cli(tmp_path):
    jcf_dir = make_jcf(tmp_path / "song")
    out = tmp_path / "out.mpak"
    assert main([str(jcf_dir), "-o", str(out), "--format", "m4a"]) == 0
    with zipfile.ZipFile(out) as z:
        m = json.loads(z.read("manifest.json"))
        names = set(z.namelist())
        assert {"audio/band.m4a", "audio/guitar-1.m4a"} <= names
        for r in m["recordings"]:
            src = r["sources"][0]
            assert src["mime"] == "audio/mp4"
            assert src["path"].endswith(".m4a")
            data = z.read(src["path"])
            assert data[4:8] == b"ftyp"
            assert hashlib.sha256(data).hexdigest() == src["sha256"]
            assert z.getinfo(src["path"]).compress_type == zipfile.ZIP_STORED


def test_tuning_padding_and_invalid_names():
    from types import SimpleNamespace as NS
    from jammit_to_mpak.convert import _tuning
    tr = NS(title="Guitar 1")
    mk = lambda l: NS(tuning={"notes": {"guitar": {"track1": l}}})
    assert _tuning(mk(["E", "A", "D", "G", "B", "E", ""]), tr, "guitar") == ["E", "A", "D", "G", "B", "E"]
    assert _tuning(mk(["E", "A", "D", "G", "B", "E", "C-1", ""]), tr, "guitar") == ["E", "A", "D", "G", "B", "E"]
    assert _tuning(mk(["E", "?", "D"]), tr, "guitar") is None
