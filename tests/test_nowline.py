import pytest

from jammit_to_mpak.nowline import NowlineError, parse_nowline
from fixtures import beat_nodes, nowline_bytes


def test_round_trip():
    nodes = beat_nodes()
    tracks = parse_nowline(nowline_bytes([("Guitar 1", "Score", nodes)]), expected_beats=len(nodes))
    assert tracks[0].name == "Guitar 1" and tracks[0].kind == "Score"
    assert tracks[0].nodes[:2] == [None, None]
    first = tracks[0].nodes[2]
    assert (first.bar, first.system, first.x) == (1, 0, 20.0)
    assert tracks[0].nodes[-1].system == 7


def test_packed_value_is_system_high_bar_low():
    data = nowline_bytes([("A", "Score", [(250, 124, 627.25)])])
    node = parse_nowline(data)[0].nodes[0]
    assert (node.bar, node.system, node.x) == (250, 124, 627.25)


def test_beat_count_must_match_beats_plist():
    with pytest.raises(NowlineError, match="beats"):
        parse_nowline(nowline_bytes([("A", "Score", beat_nodes())]), expected_beats=3)


def test_truncated_and_trailing_data_are_refused():
    data = nowline_bytes([("A", "Score", beat_nodes())])
    with pytest.raises(NowlineError):
        parse_nowline(data[:-3])
    with pytest.raises(NowlineError, match="unexpected"):
        parse_nowline(data + b"\0")
    with pytest.raises(NowlineError):
        parse_nowline(b"")
