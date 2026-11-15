import zipfile

import pytest

from jammit_to_mpak.jcf import JcfError, load
from fixtures import IDS, make_jcf


def test_loads_a_folder(tmp_path):
    jcf = load(make_jcf(tmp_path / "song"))
    assert jcf.info["title"] == "Test Song"
    assert [t.title for t in jcf.tracks] == ["Guitar 1", "Band", "Click"]
    assert len(jcf.beats) == 34 and len(jcf.nowline) == 2
    assert [p.name for p in jcf.strips(IDS["g1"], "n")] == [f"{IDS['g1']}_jcfn_00", f"{IDS['g1']}_jcfn_01"]
    assert jcf.audio(IDS["band"]) is not None and jcf.audio("nope") is None


def test_loads_a_zip_with_a_folder_inside(tmp_path):
    src = make_jcf(tmp_path / "song")
    z = tmp_path / "song.jcf.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for p in src.iterdir():
            zf.write(p, f"Some Song/{p.name}")
    jcf = load(z, workdir=tmp_path / "out")
    assert jcf.info["artist"] == "Test Artist"


def test_zip_slip_is_refused(tmp_path):
    z = tmp_path / "bad.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("../escape.txt", "x")
        zf.writestr("info.plist", "x")
    with pytest.raises(JcfError, match="outside"):
        load(z, workdir=tmp_path / "out")
    assert not (tmp_path / "escape.txt").exists()


def test_not_a_song_folder(tmp_path):
    (tmp_path / "x").mkdir()
    with pytest.raises(JcfError):
        load(tmp_path / "x")
    with pytest.raises(JcfError):
        load(tmp_path / "missing")
