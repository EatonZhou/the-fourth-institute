"""Society diagnostics expose aggregates, never the values being investigated."""
import json
import subprocess
import sys
import textwrap
import tracemalloc
from types import SimpleNamespace

from management_portal.society_service import memory_profile


def test_sample_reads_gc_counters_without_obtaining_heap_references(monkeypatch):
    def forbidden(*_, **__):
        raise AssertionError("live heap enumeration can retain tuples under construction")

    for name in ("get_objects", "get_referrers", "get_referents"):
        monkeypatch.setattr(memory_profile.gc, name, forbidden)
    result = memory_profile.sample()
    assert "heap" not in result
    assert all(isinstance(n, int) for n in result["gc"]["counts"])
    assert all(isinstance(n, int) for n in result["gc"]["thresholds"])
    assert all(isinstance(n, int) for row in result["gc"]["stats"] for n in row.values())


def test_memory_sample_does_not_keep_in_flight_tuples_alive():
    # A separate interpreter contains the old sampler's references, including
    # partially constructed tuples. Force its snapshot to overlap tuple resize;
    # do not rely on thread scheduling or a long stochastic stress test.
    script = textwrap.dedent('''
        import gc
        import threading
        import tracemalloc
        from management_portal.society_service import memory_profile

        sampled = threading.Event()
        release = threading.Event()
        errors = []
        original_get_objects = gc.get_objects

        def overlapping_snapshot(*args, **kwargs):
            objects = original_get_objects(*args, **kwargs)
            sampled.set()
            if not release.wait(5):
                raise AssertionError("tuple constructor did not resume")
            return objects

        gc.get_objects = overlapping_snapshot

        def capture():
            try:
                memory_profile.sample()
            except BaseException as error:
                errors.append(error)
            finally:
                sampled.set()

        sampler = threading.Thread(target=capture, daemon=True)

        def values():
            yield object()
            sampler.start()
            if not sampled.wait(5):
                raise AssertionError("sampler did not finish or obtain its snapshot")
            yield object()

        tracemalloc.start(1)
        try:
            result = tuple(values())
            assert len(result) == 2
        finally:
            release.set()
            sampler.join(5)
            tracemalloc.stop()
        assert not sampler.is_alive()
        if errors:
            raise errors[0]
    ''')
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


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
