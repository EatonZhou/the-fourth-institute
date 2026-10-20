""":module: spec.design-governance"""
from management_portal import resident_view_projection


def test_resident_view_unread_is_actor_scoped_and_matches_index(monkeypatch):
    """Resident 视图的未读状态按查看者分别计算，共享的运行状态相同"""
    key = "resident:resident_111111111111"
    rows = {
        "alice": {key: {"unread": True, "read_revision": 7}},
        "bob": {key: {"unread": False, "read_revision": 9}},
        "carol": {},
    }
    monkeypatch.setattr(resident_view_projection.conversation_read_state, "read_rows",
                        lambda actor, keys: {item: rows[actor][item] for item in keys if item in rows[actor]})
    base = {"resident_id": "resident_111111111111", "state": {"work_state": "idle"}}
    alice, bob, carol = (resident_view_projection.attach_viewer_state([dict(base)], actor)[0]
                         for actor in ("alice", "bob", "carol"))
    assert alice["viewer_state"] == {"unread": True, "read_revision": 7}
    assert bob["viewer_state"] == {"unread": False, "read_revision": 9}
    assert carol["viewer_state"] == {"unread": False, "read_revision": 0}
    assert alice["state"] == bob["state"]
