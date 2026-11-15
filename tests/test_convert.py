import hashlib
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from jammit_to_mpak.convert import convert
from jammit_to_mpak.jcf import load
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
