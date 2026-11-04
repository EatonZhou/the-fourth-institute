"""Society diagnostics expose aggregates, never the values being investigated."""
import json
import tracemalloc
from types import SimpleNamespace

from management_portal.society_service import memory_profile


def test_heap_profile_never_uses_application_repr_or_size(monkeypatch):
    secret = "credential-and-private-conversation-sentinel"

    class Private:
        def __repr__(self):
            raise AssertionError("object repr is forbidden")

        def __sizeof__(self):
            raise AssertionError("application size hooks are forbidden")

    objects = [Private(), {"password": secret}, [secret], (secret,)]
    monkeypatch.setattr(memory_profile.gc, "get_objects", lambda: objects)
    result = memory_profile.heap_summary(object_limit=3)
    assert result["tracked_objects"] == 4
    assert result["inspected_objects"] == 3
    assert result["truncated"] is True
    assert secret not in json.dumps(result)
    assert "password" not in json.dumps(result)


def test_extension_type_metadata_descriptors_do_not_break_sampling(monkeypatch):
    class NativeType:
        pass
    NativeType.__module__ = property(lambda _: "never evaluate a descriptor")
    monkeypatch.setattr(memory_profile.gc, "get_objects", lambda: [NativeType()])
    result = memory_profile.heap_summary()
    assert result["inspected_objects"] == 1
    assert result["top_types"][0]["type"].startswith("<native>.")


def test_profile_is_inert_without_a_current_bounded_operator_lease(monkeypatch, tmp_path):
    monkeypatch.setattr(memory_profile, "instance_runtime_root", lambda: tmp_path)
    monkeypatch.setattr(memory_profile.tracemalloc, "is_tracing", lambda: False)
    monkeypatch.setattr(memory_profile.tracemalloc, "start", lambda *_: (_ for _ in ()).throw(AssertionError("no tracer")))
    assert memory_profile.start() is None
    folder = tmp_path / "diagnostics"
    folder.mkdir()
    for expiry in (0, memory_profile.time.time() + 1000, "invalid"):
        (folder / "society-memory-profile.json").write_text(json.dumps({"expires_at": expiry}))
        assert memory_profile.start() is None


def test_allocation_profile_has_only_locations_and_numeric_totals():
    assert not tracemalloc.is_tracing()
    tracemalloc.start(1)
    try:
        private = bytearray(b"do-not-dump-this-heap-value" * 100)
        result = memory_profile.allocation_summary()
        assert result["enabled"] is True
        assert result["current_bytes"] >= len(private)
        assert "do-not-dump" not in json.dumps(result)
        assert all(set(row) == {"file", "line", "bytes", "count"} for row in result["top_lines"])
    finally:
        tracemalloc.stop()


def test_profile_stops_at_lease_deadline_and_leaves_only_aggregate_rows(monkeypatch, tmp_path):
    clock = [0.0]
    events = []
    monkeypatch.setattr(memory_profile, "instance_runtime_root", lambda: tmp_path)
    monkeypatch.setattr(memory_profile, "time", SimpleNamespace(
        time=lambda: clock[0], monotonic=lambda: clock[0], time_ns=lambda: 123,
        sleep=lambda delay: clock.__setitem__(0, clock[0] + delay)))
    monkeypatch.setattr(memory_profile, "sample", lambda: {"aggregate_bytes": 42})
    monkeypatch.setattr(memory_profile.tracemalloc, "is_tracing", lambda: False)
    monkeypatch.setattr(memory_profile.tracemalloc, "start", lambda frames: events.append(("start", frames)))
    monkeypatch.setattr(memory_profile.tracemalloc, "stop", lambda: events.append(("stop",)))
    class ImmediateThread:
        def __init__(self, *, target, **_):
            self.target = target
        def start(self):
            self.target()
    monkeypatch.setattr(memory_profile.threading, "Thread", ImmediateThread)
    folder = tmp_path / "diagnostics"
    folder.mkdir(mode=0o700)
    (folder / "society-memory-profile.json").write_text('{"expires_at":45,"interval_seconds":20}')
    memory_profile.start()
    assert clock[0] == 45
    assert events == [("start", 4), ("stop",)]
    records = next(folder.glob("*.jsonl")).read_text().splitlines()
    assert [json.loads(row) for row in records] == [{"aggregate_bytes": 42}] * 3


def test_diagnostic_directory_failure_cannot_prevent_service_start(monkeypatch, tmp_path):
    monkeypatch.setattr(memory_profile, "instance_runtime_root", lambda: tmp_path)
    monkeypatch.setattr(memory_profile.tracemalloc, "is_tracing", lambda: False)
    folder = tmp_path / "diagnostics"
    folder.mkdir()
    (folder / "society-memory-profile.json").write_text(json.dumps({"expires_at": memory_profile.time.time() + 30}))
    def denied(*_):
        raise PermissionError("private-path-sentinel")
    monkeypatch.setattr(memory_profile, "ensure_private_directory", denied)
    assert memory_profile.start() is None
