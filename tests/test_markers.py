import json

from televibe.markers import Markers


def test_write_is_atomic_and_rewritable(tmp_path):
    """REQ-STATE-1: a marker is <state_dir>/turns/<turn_id>.json, written through a temp file and a rename."""
    markers = Markers(tmp_path)
    markers.open()
    markers.write("t1", {"turn_id": "t1"})
    markers.write("t1", {"turn_id": "t1", "pgid": 42})
    assert sorted(p.name for p in (tmp_path / "turns").iterdir()) == ["t1.json"]
    assert json.loads((tmp_path / "turns" / "t1.json").read_text()) == {"turn_id": "t1", "pgid": 42}


def test_all_skips_unreadable_and_remove_is_idempotent(tmp_path):
    """REQ-STATE-5: markers stay until removed; removing twice is fine."""
    markers = Markers(tmp_path)
    markers.open()
    markers.write("t1", {"turn_id": "t1"})
    (tmp_path / "turns" / "junk.json").write_text("{not json")
    assert markers.all() == [{"turn_id": "t1"}]
    markers.remove("t1")
    markers.remove("t1")
    assert markers.all() == []
