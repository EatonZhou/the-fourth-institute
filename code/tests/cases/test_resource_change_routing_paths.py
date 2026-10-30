"""Routing a changed path resolves it once, whatever the number of adapters.

Routing asked every adapter whether it owned the changed path, and each
adapter resolved that path and its own roots again: about 190 system calls
per routing.  Each call gives up the interpreter lock, so while other
ResourceService threads computed, routing one Resident Resource change took
0.3-1.5 s.  The changed path is now resolved once per routing, and adapter
roots reuse their resolution for ``ROOT_RESOLUTION_SECONDS``.
"""
from __future__ import annotations

import os
from pathlib import Path

from management_portal import resource_index_adapters, resource_index_refresh


def _adapter(name: str, root: Path) -> resource_index_adapters._PathsAdapter:
    adapter = resource_index_adapters._PathsAdapter()
    adapter.name = name
    adapter.roots = (root,)
    return adapter


def _layer(tmp_path: Path, count: int) -> resource_index_refresh.AdapterLayer:
    roots = [tmp_path / f"root{index}" for index in range(count)]
    for root in roots:
        root.mkdir()
    adapters = [_adapter(f"adapter{index}", root) for index, root in enumerate(roots)]
    return resource_index_refresh.AdapterLayer(None, adapters, start_worker=False)


def _lstat_calls(monkeypatch) -> list[str]:
    calls: list[str] = []
    original = os.lstat

    def counted(path, *args, **kwargs):
        calls.append(str(path))
        return original(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", counted)
    return calls


def test_routing_resolves_the_changed_path_once(tmp_path, monkeypatch):
    """路由变更时只解析一次变更路径"""
    layer = _layer(tmp_path, 12)
    changed = tmp_path / "root7" / "item.yaml"
    assert layer.matching_adapters(path=str(changed)) == ["adapter7"]
    calls = _lstat_calls(monkeypatch)
    assert layer.matching_adapters(path=str(changed)) == ["adapter7"]
    single = len(calls)
    calls.clear()
    Path(changed).resolve(strict=False)
    assert single == len(calls), "routing resolved the changed path more than once"


def test_a_moved_root_is_followed_once_its_resolution_expires(tmp_path, monkeypatch):
    """根目录解析过期后跟随移动后的根目录"""
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    link = tmp_path / "link"
    link.symlink_to(first)
    layer = resource_index_refresh.AdapterLayer(None, [_adapter("linked", link)], start_worker=False)
    assert layer.matching_adapters(path=str(first / "item.yaml")) == ["linked"]
    link.unlink()
    link.symlink_to(second)
    monkeypatch.setattr(resource_index_adapters, "ROOT_RESOLUTION_SECONDS", 0.0)
    assert layer.matching_adapters(path=str(second / "item.yaml")) == ["linked"]
    assert layer.matching_adapters(path=str(first / "item.yaml")) == []
