"""Lock-only checks: no live Lineage, publication, or Harness operation."""
from __future__ import annotations

import ast
import builtins
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from management_common.file_lock import lock_stream, try_lock_descriptor, unlock_descriptor
from management_portal.society_service import invariant_locks as locks, lineage_storage
from scripts import harness_lineage, portal_publication_batch

ROOT = Path(__file__).resolve().parents[2]
FILES = [
    "management_portal/society_service/lineage_storage.py",
    "management_portal/society_service/lineage_domain.py",
    "management_portal/society_service/invariant_locks.py",
    "scripts/portal_publication_batch.py", "scripts/harness_lineage.py",
]


@pytest.mark.parametrize("relative", FILES)
def test_consumers_have_no_unconditional_unix_lock_import(relative):
    # Execute actual top-level fcntl imports with Windows' absent module, not a
    # text-name assertion about unrelated code or comments.
    imports = [n for n in ast.parse((ROOT / relative).read_text()).body
               if isinstance(n, ast.Import) and any(a.name == "fcntl" for a in n.names)]
    def unavailable(name, *args, **kwargs):
        if name == "fcntl":
            raise ModuleNotFoundError("fcntl is absent on Windows")
        return builtins.__import__(name, *args, **kwargs)
    exec(compile(ast.Module(body=imports, type_ignores=[]), relative, "exec"),
         {"__builtins__": {**vars(builtins), "__import__": unavailable}})


def available(path):
    with path.open("a+b") as stream:
        owned = try_lock_descriptor(stream.fileno())
        if owned:
            unlock_descriptor(stream.fileno())
        return owned


@pytest.mark.parametrize("factory,name", [
    (lineage_storage._folder_lock, ".lineage-folders.lock"),
    (portal_publication_batch.preparation_lock, "portal-publish-prepare.lock"),
    (lambda root: harness_lineage.locked(root / "harness.lock"), "harness.lock"),
])
def test_blocking_consumers_preserve_content_and_release_after_failure(tmp_path, factory, name):
    path = tmp_path / name
    path.write_bytes(b"keep")
    with pytest.raises(RuntimeError, match="body failed"):
        with factory(tmp_path):
            assert not available(path)
            raise RuntimeError("body failed")
    assert available(path) and path.read_bytes() == b"keep"


def test_nested_scope_holds_one_file_lock_and_callbacks_wait_for_outer_exit(tmp_path, monkeypatch):
    monkeypatch.setattr(locks, "_ROOT_PROVIDER", lambda: tmp_path)
    events = []
    path = locks._lock_path("test-reentrant")
    with locks._invariant_scope("test-reentrant"):
        with locks._invariant_scope("test-reentrant"):
            assert not available(path)
            locks.defer_until_state_unlock(lambda: events.append(available(path)))
        assert not available(path) and not events
    assert events == [True] and not locks.lock_held()


def test_nonblocking_ownership_does_not_wait_or_release_another_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(locks, "_ROOT_PROVIDER", lambda: tmp_path)
    with pytest.raises(RuntimeError):
        with locks._try_ownership_scope("test-owner") as first:
            assert first
            with locks._try_ownership_scope("test-owner") as second:
                assert not second
            assert not available(locks._lock_path("test-owner"))
            raise RuntimeError("body failed")
    with locks._try_ownership_scope("test-owner") as owned:
        assert owned


@pytest.mark.parametrize("failure", ["acquire", "release"])
def test_file_lock_failure_releases_in_process_scope(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(locks, "_ROOT_PROVIDER", lambda: tmp_path)
    @contextmanager
    def broken(stream):
        if failure == "acquire":
            raise OSError("lock failed")
        yield
        raise OSError("unlock failed")
    monkeypatch.setattr(locks, "lock_stream", broken, raising=False)
    with pytest.raises(OSError):
        with locks._invariant_scope("test-failed-lock"):
            pass
    assert not locks.lock_held()
    record = locks._record("test-failed-lock")
    got = []
    def other_thread():
        got.append(record.lock.acquire(timeout=0.2))
        if got[-1]:
            record.lock.release()
    thread = threading.Thread(target=other_thread)
    thread.start(); thread.join(timeout=1)
    assert got == [True]


def test_projection_publication_lock_covers_read_sync_and_receipt(tmp_path, monkeypatch):
    # Load the exact owner function with isolated ports; importing the complete
    # Society runtime is neither needed nor permitted for a lock-only probe.
    name = "_publish_lineage_resource_projection"
    path = ROOT / "management_portal/society_service/lineage_domain.py"
    function = next(n for n in ast.parse(path.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == name)
    lock_path = tmp_path / "runtime/resource-projection.lock"
    events = []
    def check(event):
        assert not available(lock_path)
        events.append(event)
    @contextmanager
    def transaction(_):
        check("receipt")
        yield {}
    projection = SimpleNamespace(get=lambda _: (check("read") or {}), transaction=transaction)
    namespace = {"__name__": "isolated_projection_lock", "__package__": "management_portal.society_service",
                 "invariant_locks": SimpleNamespace(lock_held=lambda: False),
                 "_lineage_catalog_store": lambda: SimpleNamespace(state_store=SimpleNamespace(lineage_resource_projection=projection)),
                 "_lineage_dir": lambda _: tmp_path, "_clone_json": dict,
                 "_sync_lineage_resource_identity": lambda _: check("sync"), "lock_stream": lock_stream}
    if sys.platform != "win32":
        import fcntl
        namespace["fcntl"] = fcntl
    monkeypatch.setitem(sys.modules, "management_portal.society_service.resource_client", SimpleNamespace())
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), function], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    namespace[name]("lineage_test", committed_revision=1, creation={"revision": 1})
    assert events == ["read", "sync", "receipt"] and available(lock_path)
    namespace["_sync_lineage_resource_identity"] = lambda _: (_ for _ in ()).throw(RuntimeError("sync failed"))
    with pytest.raises(RuntimeError, match="sync failed"):
        namespace[name]("lineage_test", committed_revision=2, creation={"revision": 2})
    assert available(lock_path)
