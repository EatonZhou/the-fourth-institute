"""Personal PDF annotations.

:module: workflow.paper
:design: decision_528bf9985705
"""
import copy
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal import paper_highlights as highlights, presentation_store

PAPER = "paper_0123456789ab"
DOC = "a" * 32
ITEM = {"id": "00000000-0000-4000-8000-000000000001", "text": "Selected text",
        "rects": [{"page": 1, "x": .1, "y": .2, "width": .3, "height": .02}]}


@pytest.fixture
def resource(monkeypatch):
    get = Mock(return_value={"resource_id": PAPER})
    monkeypatch.setattr(highlights, "get_resource_service", lambda: SimpleNamespace(get=get))
    return get


def save(actor="alice", **kw):
    return highlights.save_highlight(actor, {"paper_id": PAPER, "document": DOC,
        "operation": "add", "highlight": ITEM, **kw})


def test_isolated_account_pdf_version_and_idempotent_writes(resource):
    save(); save()
    assert highlights.list_highlights("alice", PAPER, DOC)["highlights"] == [ITEM]
    assert highlights.list_highlights("bob", PAPER, DOC)["highlights"] == []
    assert highlights.list_highlights("alice", PAPER, "b" * 32)["highlights"] == []
    save("bob", operation="delete", id=ITEM["id"])
    assert len(highlights.list_highlights("alice", PAPER, DOC)["highlights"]) == 1
    save(operation="delete", id=ITEM["id"])
    assert highlights.list_highlights("alice", PAPER, DOC)["highlights"] == []


def test_concurrent_highlights_do_not_overwrite_other_items(resource):
    def add(n):
        item = {**ITEM, "id": f"00000000-0000-4000-8000-{n:012x}"}
        save(highlight=item)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(add, range(8)))
    assert len(highlights.list_highlights("alice", PAPER, DOC)["highlights"]) == 8
    save("bob")
    presentation_store.delete_actor_associations("alice")
    assert highlights.list_highlights("alice", PAPER, DOC)["highlights"] == []
    assert len(highlights.list_highlights("bob", PAPER, DOC)["highlights"]) == 1


def test_read_and_mutations_check_current_acl(resource):
    save()
    resource.side_effect = PermissionError("not visible")
    for operation in (lambda: save(), lambda: save(operation="delete", id=ITEM["id"]),
                      lambda: highlights.list_highlights("alice", PAPER, DOC)):
        with pytest.raises(PermissionError): operation()
    with pytest.raises(PermissionError): save("")


@pytest.mark.parametrize("field,value", [("x", -1), ("width", float("nan")), ("height", 0),
    ("page", True), ("x", 0.9), ("y", float("inf"))])
def test_invalid_coordinates_are_rejected(resource, field, value):
    item = copy.deepcopy(ITEM); item["rects"][0][field] = value
    with pytest.raises(ValueError): save(highlight=item)


def test_http_personal_route_uses_authenticated_actor(resource):
    from management_portal.http_paper import PaperHttp
    respond = Mock(side_effect=lambda request, code, result: (code, result))
    http = PaperHttp(respond=respond, exception_response=Mock(), media_file=Mock(),
        get_auth=lambda _: {"username": "alice"}, authorize=lambda *_: "alice",
        read_json=lambda _: {"paper_id": PAPER, "document": DOC, "operation": "add",
                             "highlight": ITEM, "actor": "bob"},
        read_multipart=Mock(), multipart_limit=Mock(), body_too_large=ValueError,
        require_resource=Mock(), require_active_actor=Mock(), project_ids=Mock(), notify_index=Mock())
    route = http.personal_post_route(http.highlight)
    assert route(object(), SimpleNamespace(query=""), "bob")[0] == 200
    assert len(highlights.list_highlights("alice", PAPER, DOC)["highlights"]) == 1
    assert highlights.list_highlights("bob", PAPER, DOC)["highlights"] == []
    http.require_resource.assert_not_called()  # Read-only users can annotate.


NATIVE = {"annotationType": 9, "pageIndex": 0, "rotation": 0,
          "rect": [10, 20, 30, 40], "color": [255, 228, 94], "opacity": 1, "thickness": 12,
          "quadPoints": [10, 40, 30, 40, 10, 20, 30, 20]}


def test_native_style_and_legacy_records_roundtrip(resource):
    item = {**ITEM, "native": [NATIVE]}
    save(highlight=item)
    assert highlights.list_highlights("alice", PAPER, DOC)["highlights"] == [item]
    free = {k: v for k, v in NATIVE.items() if k != "quadPoints"}
    free["outlines"] = {"points": [[10, 20, 20, 25, 30, 40]]}
    save(highlight={**ITEM, "native": [free]})
    assert highlights.list_highlights("alice", PAPER, DOC)["highlights"][0]["native"] == [free]
    save()  # The original rectangle-only format remains accepted unchanged.
    assert highlights.list_highlights("alice", PAPER, DOC)["highlights"] == [ITEM]


@pytest.mark.parametrize("field,value", [("annotationType", 3), ("pageIndex", 1),
    ("rotation", 45), ("opacity", float("nan")), ("color", [256, 0, 0]),
    ("quadPoints", [1, 2, 3]), ("rect", [30, 20, 10, 40])])
def test_invalid_native_geometry_rejected(resource, field, value):
    native = {**NATIVE, field: value}
    with pytest.raises(ValueError): save(highlight={**ITEM, "native": [native]})


def test_native_drops_untrusted_actions_and_bounds_freehand_size(resource):
    save(highlight={**ITEM, "native": [{**NATIVE, "actions": {"JavaScript": "bad"}}]})
    assert "actions" not in highlights.list_highlights("alice", PAPER, DOC)["highlights"][0]["native"][0]
    free = {k: v for k, v in NATIVE.items() if k != "quadPoints"}
    free["outlines"] = {"points": [[0] * 40002]}
    with pytest.raises(ValueError): save(highlight={**ITEM, "native": [free]})
