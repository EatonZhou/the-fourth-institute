"""Explicit contact preserves the authenticated actor and fixed result version."""
import pytest

from management_portal.resident_results_http import contact_author


ID = "deliverable_0123456789ab"
AUTHOR = "resident_0123456789ab"


class Store:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.reads = []

    def get(self, identifier, actor, revision):
        self.reads.append((identifier, actor, revision))
        if not self.allowed:
            raise PermissionError("withdrawn access")
        return {"record": {"author_id": AUTHOR}, "revision": revision}


def test_contact_preserves_actor_fixed_version_and_retry_identity():
    store = Store()
    calls = []
    def call(*args, **kwargs):
        calls.append((args, kwargs))
        return {"accepted": True}
    body = {"revision": 2, "message": "请解释数据来源", "operation_id": "request-0001"}
    for _ in range(2):
        assert contact_author(store, "alice", ID, body, call=call)["accepted"]
    assert calls[0] == calls[1]
    args, kwargs = calls[0]
    assert args[:2] == ("alice", "DeliverMessage")
    assert args[2]["resident_id"] == AUTHOR
    assert args[2]["business_ref"] == ID + "/revision/2"
    assert ID + "/revision/2" in args[2]["message"]["content_parts"][0]["text"]
    assert kwargs["key"].startswith("deliverable:")
    contact_author(store, "bob", ID, body, call=call)
    assert calls[-1][1]["key"] != kwargs["key"]


def test_contact_reauthorizes_before_sending_and_cannot_choose_recipient():
    calls = []
    body = {"revision": 2, "message": "question", "operation_id": "request-0001"}
    with pytest.raises(PermissionError):
        contact_author(Store(False), "alice", ID, body, call=lambda *a, **k: calls.append(a))
    with pytest.raises(ValueError):
        contact_author(Store(), "alice", ID, {**body, "author_id": "resident_deadbeef0000"},
                       call=lambda *a, **k: calls.append(a))
    assert calls == []


@pytest.mark.parametrize("changes", [{"revision": True}, {"revision": 0}, {"message": " "},
                                     {"message": "x" * 8001}, {"operation_id": ""}])
def test_contact_invalid_input_never_sends(changes):
    calls = []
    body = {"revision": 2, "message": "question", "operation_id": "request-0001", **changes}
    with pytest.raises(ValueError):
        contact_author(Store(), "alice", ID, body, call=lambda *a, **k: calls.append(a))
    assert not calls
