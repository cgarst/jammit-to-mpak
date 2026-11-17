import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from pypdf import PdfWriter

from jammit_to_mpak.join import base_title
from jammit_to_mpak.join_cli import main
from jammit_to_mpak.mpak_writer import MpakWriter
from fixtures import write_tone

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def make_part(tmp: Path, name: str, title: str, pages: int, seconds: float, tracks: list[str]) -> Path:
    pdf = tmp / f"{name}.pdf"
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(200, 200)
    w.write(pdf)
    recs, files = [], []
    for t in tracks:
        wav, m4a = tmp / f"{name}-{t}.wav", tmp / f"{name}-{t}.m4a"
        write_tone(wav, [440], seconds)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(wav), "-c:a", "aac", str(m4a)], check=True)
        files.append((f"audio/{t}.m4a", m4a))
        rec = {"id": t, "role": "stem", "label": t, "stem": {"instrument": "guitar"},
               "sources": [{"type": "file", "path": f"audio/{t}.m4a", "mime": "audio/mp4", "durationMs": int(seconds * 1000)}]}
        if not recs:
            rec.update(beats="beats/g.json", sync={"s": "sync/g-s.json"})
        else:
            rec["syncFrom"] = tracks[0]
        recs.append(rec)
    manifest = {"format": "mpak", "version": 1, "id": "x", "song": {"title": title, "artist": "A", "length": int(seconds * 1000)},
                "scores": [{"id": "s", "label": f"{title} - Guitar", "path": "score/s.pdf", "format": "pdf", "pages": pages,
                            "instruments": [{"name": "Guitar", "tuning": ["E", "A", "D", "G", "B", "E"]}]}],
                "recordings": recs}
    out = tmp / f"{name}.mpak"
    with MpakWriter(out) as z:
        z.add_json("manifest.json", manifest)
        z.add_file("score/s.pdf", pdf, False)
        for n, f in files:
            z.add_file(n, f, True)
        z.add_json("beats/g.json", {"version": 1, "recording": tracks[0], "beats": [{"timeMs": 0, "downbeat": True}, {"timeMs": 500}],
                                    "sections": [{"name": "Verse", "timeMs": 0}]})
        z.add_json("sync/g-s.json", {"version": 1, "score": "s", "anchorType": "pdf", "recording": tracks[0],
                                     "points": [{"timeMs": 0, "page": 1, "x": 0.1, "y": 0.1}, {"timeMs": 500, "page": 2, "x": 0.5, "y": 0.1}]})
    return out


def test_base_title():
    assert base_title("A Change of Seasons (Pt. II, III)") == "A Change of Seasons"
    assert base_title("Song") == "Song"


def test_join_glues_parts(tmp_path):
    a = make_part(tmp_path, "one", "Song (Pt. I)", 2, 2.0, ["g1", "g2"])
    b = make_part(tmp_path, "two", "Song (Pt. II)", 3, 3.0, ["g1", "g2", "vox"])
    out = tmp_path / "Song.mpak"
    assert main([str(a), str(b), "-o", str(out)]) == 0
    z = zipfile.ZipFile(out)
    m = json.loads(z.read("manifest.json"))
    assert m["song"]["title"] == "Song" and m["scores"][0]["pages"] == 5
    assert [r["id"] for r in m["recordings"]] == ["g1", "g2", "vox"]
    assert abs(m["song"]["length"] - 5000) <= 2
    for r in m["recordings"]:
        assert abs(r["sources"][0]["durationMs"] - 5000) <= 60
    pts = json.loads(z.read("sync/g-s.json"))["points"]
    assert [p["page"] for p in pts] == [1, 2, 3, 4]
    assert abs(pts[2]["timeMs"] - 2000) <= 2 and abs(pts[3]["timeMs"] - 2500) <= 2
    beats = json.loads(z.read("beats/g.json"))
    assert len(beats["beats"]) == 4 and abs(beats["sections"][1]["timeMs"] - 2000) <= 2
    assert z.testzip() is None
