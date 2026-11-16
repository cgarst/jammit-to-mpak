import json
import shutil
import zipfile
from pathlib import Path

import pytest

from jammit_to_mpak.audio import mix_level_db
from jammit_to_mpak.cli import main
from jammit_to_mpak.convert import convert
from jammit_to_mpak.jcf import load, peek_info
from fixtures import make_jcf, write_tone

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")

PARTS = {"guitar": (0, "Guitar 1", 220), "bass": (1, "Bass", 330), "drums": (2, "Drums", 440),
         "keys": (3, "Keys", 550), "vocals": (4, "Vocal", 660)}


def make_part(root: Path, part: str, band_tones: list[float] | None = None) -> Path:
    """One instrument's folder of the same song: its stem is a sine, its Band the sum of the others' sines."""
    n, title, freq = PARTS[part]
    ids = {"g1": f"{n}AAAAAAA-0000-0000-0000-000000000001", "band": f"{n}BBBBBBB-0000-0000-0000-000000000002"}
    make_jcf(root, instrument=n, title=title, ids=ids, with_nowline=(part == "guitar"))
    write_tone(root / f"{ids['g1']}_jcfx", [freq])
    others = [f for p, (_, _, f) in PARTS.items() if p != part] if band_tones is None else band_tones
    write_tone(root / f"{ids['band']}_jcfx", others)
    return root


def zip_dir(src: Path, dst: Path) -> Path:
    with zipfile.ZipFile(dst, "w") as z:
        for f in src.iterdir():
            z.write(f, f.name)
    return dst


def test_stem_pack(tmp_path):
    folders = {p: load(make_part(tmp_path / p, p)) for p in PARTS}
    out = tmp_path / "out.mpak"
    report = convert(folders["guitar"], out, systems_per_page=4, work=tmp_path / "w",
                     companions=[folders[p] for p in PARTS if p != "guitar"])
    m = json.loads(zipfile.ZipFile(out).read("manifest.json"))
    recs = m["recordings"]
    assert [r["id"] for r in recs] == ["guitar-1", "bass", "drums", "keys", "vocal"]
    assert all(r["role"] == "stem" and "without" not in r for r in recs)  # no Band: the stems are the mix
    assert [r["stem"]["instrument"] for r in recs] == ["guitar", "bass", "drums", "keys", "vocals"]
    assert "beats" in recs[0] and "sync" in recs[0]
    assert all(r["syncFrom"] == "guitar-1" for r in recs[1:])
    assert any("what no stem carries" in line for line in report.carried)
    assert not [w for w in report.warnings if "leave out" in w]  # the stems add up to the Band's mix


def test_stems_that_miss_part_of_the_mix_recover_other(tmp_path):
    folders = {p: load(make_part(tmp_path / p, p, band_tones=[330, 440, 550, 660, 880])) for p in PARTS}
    out = tmp_path / "o.mpak"
    report = convert(folders["guitar"], out, systems_per_page=4, work=tmp_path / "w",
                     companions=[folders[p] for p in PARTS if p != "guitar"])
    assert not [w for w in report.warnings if "leave out" in w]
    assert any("recording 'Other'" in line for line in report.carried)
    with zipfile.ZipFile(out) as zf:
        m = json.loads(zf.read("manifest.json"))
        recs = m["recordings"]
        assert [r["id"] for r in recs] == ["guitar-1", "bass", "drums", "keys", "vocal", "other"]
        assert recs[-1]["stem"] == {"instrument": "other"} and recs[-1]["syncFrom"] == "guitar-1"
        extracted = tmp_path / "ex"
        zf.extractall(extracted)
    stems = [(extracted / r["sources"][0]["path"], 1.0) for r in recs]
    guitar = folders["guitar"]
    band = next(t for t in guitar.tracks if t.title == "Band")
    g1 = next(t for t in guitar.tracks if t.title == "Guitar 1")
    full = mix_level_db([(guitar.audio(band.identifier), 1.0), (guitar.audio(g1.identifier), 1.0)])
    diff = mix_level_db([(guitar.audio(band.identifier), 1.0), (guitar.audio(g1.identifier), 1.0)]
                        + [(p, -1.0) for p, _ in stems])
    assert diff - full < -20


def test_cli_needs_every_part(tmp_path, capsys):
    songs = tmp_path / "songs"
    songs.mkdir()
    for p in ("guitar", "bass", "drums", "keys"):
        zip_dir(make_part(tmp_path / p, p), songs / f"{p}.jcf.zip")
    assert peek_info(songs / "bass.jcf.zip")["instrument"] == 1
    assert main([str(songs / "guitar.jcf.zip"), "--stem", "-o", str(tmp_path / "x.mpak")]) == 1
    assert "missing vocals" in capsys.readouterr().err
    assert not (tmp_path / "x.mpak").exists()
    assert main([str(songs), "--stem", "-o", str(tmp_path / "o")]) == 0  # a folder: the incomplete song is skipped
    assert "missing vocals" in capsys.readouterr().out
    assert not list((tmp_path / "o").glob("*.mpak"))


def test_cli_with_every_part(tmp_path):
    songs = tmp_path / "songs"
    songs.mkdir()
    for p in PARTS:
        zip_dir(make_part(tmp_path / p, p), songs / f"{p}.jcf.zip")
    assert main([str(songs), "--stem", "-o", str(tmp_path / "o")]) == 0
    assert [f.name for f in (tmp_path / "o").glob("*.mpak")] == ["Test Artist - Test Song (stems).mpak"]
    assert main([str(songs / "bass.jcf.zip"), "--stem", "-o", str(tmp_path / "one.mpak")]) == 0  # any part finds the rest
    assert (tmp_path / "one.mpak").exists()
