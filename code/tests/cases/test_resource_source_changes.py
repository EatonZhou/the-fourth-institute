""":importance: P1"""
from __future__ import annotations

from management_portal.paper_resource_index import PaperManifestAdapter, PaperReviewAdapter

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

from management_portal import paper_reviews, paper_store
from management_portal import resource_index_backend as backend
from management_portal import (
    resource_index_adapters, resource_index_knowledge, resource_index_records, resource_index_refresh,
)
from management_portal import resource_source_changes

PAPER_ONE = "paper_000000000001"
PAPER_TWO = "paper_000000000002"
PAPER_THREE = "paper_000000000003"
REVIEW_ID = "review_abcdef012345"


def _settle() -> float:
    """A moment after every earlier file change and before every later one."""
    time.sleep(0.05)
    moment = time.time()
    time.sleep(0.05)
    return moment


def test_change_finder_reports_what_changed_since_a_moment(tmp_path: Path) -> None:
    """来源变化查找器只报告指定时刻之后变化的目录与文件

    子进程按声明的目录深度、目录名限制和文件名模式列出来源，只报告 ctime 在指定时刻之后的目录与文件，跳过隐藏项；单独文件报告存在与变化，记录来源报告真实路径与缺失。
    """
    root = tmp_path / "knowledge_base"
    entry = root / "decision" / "entry"
    (entry / "deep").mkdir(parents=True)
    (root / "skipped").mkdir()
    (root / ".hidden").mkdir()
    (root / "decision" / "old.md").write_text("old", encoding="utf-8")
    edited = root / "decision" / "edited.md"
    edited.write_text("first", encoding="utf-8")
    (entry / "removed.md").write_text("removed", encoding="utf-8")
    reviews = tmp_path / "reviews.json"
    reviews.write_text("[]", encoding="utf-8")
    target = tmp_path / "target.md"
    target.write_text("target", encoding="utf-8")
    link = tmp_path / "link.md"
    link.symlink_to(target)

    since = _settle()
    edited.write_text("second", encoding="utf-8")
    (entry / "removed.md").unlink()
    (root / "decision" / "new.md").write_text("new", encoding="utf-8")
    (root / "decision" / "note.txt").write_text("not an item file", encoding="utf-8")
    (entry / "deep" / "too_deep.md").write_text("below max_depth", encoding="utf-8")
    (root / ".hidden" / "hidden.md").write_text("hidden", encoding="utf-8")
    (root / "skipped" / "other.md").write_text("not listed by only", encoding="utf-8")

    request = {
        "since": since,
        "trees": [{
            "root": str(root),
            "max_depth": 2,
            "files": ["*.md"],
            "only": {"1": ["decision"]},
        }],
        "files": [str(reviews), str(tmp_path / "absent.json")],
        "paths": [str(link), str(tmp_path / "gone.md")],
    }
    found = resource_index_refresh._find_source_changes(request)

    assert found == resource_source_changes.find_changes(request)
    assert found["changed"] == sorted([
        str(edited),
        str(root / "decision"),
        str(root / "decision" / "new.md"),
        str(entry),
    ])
    assert found["resolved"] == {
        str(link): os.path.realpath(target),
        str(tmp_path / "gone.md"): os.path.realpath(tmp_path / "gone.md"),
    }
    assert found["missing"] == [str(tmp_path / "gone.md")]

    reviews.write_text("[{}]", encoding="utf-8")
    assert str(reviews) in resource_source_changes.find_changes(request)["changed"]


def test_a_request_larger_than_a_pipe_reaches_a_finder_that_starts_slowly(tmp_path, monkeypatch):
    """来源变更请求大于管道容量、查找子进程启动较慢时，查找仍能完成

    The paper index sends one path per paper (about 300 KB for 4,000). On a
    busy host the child read its standard input after the first 0.1 s wait,
    the rest of the request was never written, and every paper scan hung for
    the full 900 s (eight times between 10-26 and 10-29).
    """
    slow = tmp_path / "slow_finder.py"
    slow.write_text("import runpy, time\ntime.sleep(0.5)\n"
                    f"runpy.run_path({str(resource_index_refresh._SOURCE_CHANGE_FINDER)!r}, run_name='__main__')\n", encoding="utf-8")
    monkeypatch.setattr(resource_index_refresh, "_SOURCE_CHANGE_FINDER", slow)
    monkeypatch.setattr(resource_index_refresh, "SOURCE_CHANGE_TIMEOUT_SECONDS", 20.0)
    paths = [str(tmp_path / "items" / f"package-{index:05d}" / "manifest.json") for index in range(4000)]
    request = {"since": time.time(), "trees": [], "files": [], "paths": paths}
    assert len(json.dumps(request)) > 256 * 1024
    started = time.monotonic()
    assert resource_index_refresh._find_source_changes(request)["missing"] == sorted(paths)
    assert time.monotonic() - started < 10


def test_change_finder_pipe_is_utf8_with_windows_default_encoding(tmp_path):
    """中文路径的变更结果不依赖 Windows 控制台或隔离进程的默认编码。"""
    root = tmp_path / "中文 空格"
    root.mkdir()
    existing = root / "知识.md"
    existing.write_text("knowledge", encoding="utf-8")
    missing = root / "已删除.md"
    request = {"since": 0, "files": [str(existing)],
               "paths": [str(existing), str(missing)]}
    result = subprocess.run(
        [sys.executable, "-I", "-c",
         "import runpy,sys; sys.stdout.reconfigure(encoding='cp1252'); "
         "runpy.run_path(sys.argv[1], run_name='__main__')",
         str(Path(resource_source_changes.__file__))],
        input=json.dumps(request, ensure_ascii=False).encode("utf-8"),
        capture_output=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    found = json.loads(result.stdout.decode("utf-8"))
    assert found["changed"] == [str(existing)]
    assert found["missing"] == [str(missing)]
    assert found["resolved"] == {str(p): os.path.realpath(p) for p in (existing, missing)}


class _TrackedAdapter:
    """The papers in one directory, one JSON file each, tracked by change."""

    name = "paper"
    resource_types = frozenset({"paper"})
    registry_collections = frozenset({"projects"})

    def __init__(self, root: Path) -> None:
        self.root = root
        self.scans = 0
        self.change_requests: list[tuple[str, ...]] = []
        self.registry_revision = "1"
        self.cannot_apply = False

    def _record(self, path: Path) -> dict:
        data = json.loads(path.read_text(encoding="utf-8"))
        return resource_index_records.resource_record(
            resource_id=data["id"],
            resource_type="paper",
            title=data["title"],
            adapter=self.name,
            path=path,
            modified_at="2026-10-11T00:00:00+08:00",
            facets=["paper"],
        )

    def _registry_fingerprint(self) -> str:
        return self.registry_revision

    def fingerprint(self) -> str:
        raise AssertionError("a tracked adapter is checked by its source changes")

    def owns_path(self, path: Path) -> bool:
        return self.root in path.parents

    def source_scope(self) -> dict:
        return {
            "trees": [{"root": str(self.root), "max_depth": 0, "files": ["*.json"]}],
            "files": [],
        }

    def scan(self):
        self.scans += 1
        return [self._record(path) for path in sorted(self.root.glob("*.json"))], {}

    def scan_changes(self, paths, previous, _state):
        paths = tuple(paths)
        self.change_requests.append(paths)
        if self.cannot_apply:
            return None
        ids_by_path = {record["source"]["path"]: record["resource_id"] for record in previous}
        upserts = []
        deletes = []
        for raw_path in paths:
            path = Path(raw_path)
            if path.suffix != ".json":
                continue
            if path.is_file():
                upserts.append(self._record(path))
            elif raw_path in ids_by_path:
                deletes.append(ids_by_path[raw_path])
        return resource_index_records.ChangeSet(adapter=self.name, upserts=tuple(upserts), deletes=tuple(deletes))


def _write_paper(path: Path, paper_id: str, title: str) -> None:
    path.write_text(json.dumps({"id": paper_id, "title": title}), encoding="utf-8")


def _titles(store: backend.IndexStore) -> dict[str, str]:
    return {record["resource_id"]: record["title"] for record in store.records_for_adapter("paper")}


def test_tracked_adapter_rereads_only_the_changed_sources(tmp_path: Path, isolated_portal_case) -> None:
    """跟踪来源变化的适配器只重读变化的来源

    普通刷新在来源未变时不读取；修改、新增、删除的来源和等待期间加入的路径按路径应用；强制刷新、登记表版本或索引代码变化、适配器无法单独应用路径时读取完整快照。
    """
    with isolated_portal_case(), mock.patch.object(resource_index_refresh, "SOURCE_CHANGE_MARGIN_SECONDS", 0.0):
        root = tmp_path / "papers"
        root.mkdir()
        first = root / "first.json"
        second = root / "second.json"
        _write_paper(first, PAPER_ONE, "First")
        _write_paper(second, PAPER_TWO, "Second")
        adapter = _TrackedAdapter(root)
        store = backend.IndexStore()
        layer = resource_index_refresh.AdapterLayer(store, [adapter], start_worker=False)
        _settle()

        layer.refresh_now("paper")
        assert adapter.scans == 1
        assert _titles(store) == {PAPER_ONE: "First", PAPER_TWO: "Second"}

        _settle()
        assert layer.refresh_now("paper")["changed"] is False
        assert adapter.change_requests == []

        _write_paper(first, PAPER_ONE, "First edited")
        result = layer.refresh_now("paper")
        assert result["incremental"] is True
        assert adapter.change_requests[-1] == (str(first),)
        assert _titles(store) == {PAPER_ONE: "First edited", PAPER_TWO: "Second"}

        _settle()
        second.unlink()
        _write_paper(root / "third.json", PAPER_THREE, "Third")
        layer.refresh_now("paper")
        assert set(adapter.change_requests[-1]) == {str(root), str(second), str(root / "third.json")}
        assert _titles(store) == {PAPER_ONE: "First edited", PAPER_THREE: "Third"}
        assert adapter.scans == 1

        # A changed path that joined a pending check is applied with it.
        _settle()
        layer.refresh_now("paper", joined_paths=[str(first)])
        assert adapter.change_requests[-1] == (str(first),)
        assert adapter.scans == 1

        layer.refresh_now("paper", force=True)
        assert adapter.scans == 2

        _settle()
        adapter.registry_revision = "2"
        layer.refresh_now("paper")
        assert adapter.scans == 3

        _settle()
        with mock.patch.object(resource_index_adapters, "_CURRENT_PROJECTION_CODE_FINGERPRINT", "changed index code"):
            layer.refresh_now("paper")
            assert adapter.scans == 4
            _settle()
            layer.refresh_now("paper")
            assert adapter.scans == 4
        _settle()
        layer.refresh_now("paper")
        assert adapter.scans == 5

        _settle()
        adapter.cannot_apply = True
        _write_paper(first, PAPER_ONE, "First again")
        layer.refresh_now("paper")
        assert adapter.change_requests[-1] == (str(first),)
        assert adapter.scans == 6
        assert _titles(store)[PAPER_ONE] == "First again"


def test_a_forced_full_refresh_rescans_but_a_forced_path_does_not(tmp_path: Path, isolated_portal_case) -> None:
    """强制的完整刷新重新扫描，强制的按路径刷新不改为完整扫描

    刷新队列只把没有路径的强制请求记为完整重扫；带路径的强制请求与普通请求合并后仍按变化检查。

    :importance: P2
    """
    with isolated_portal_case():
        adapter = _TrackedAdapter(tmp_path)
        layer = resource_index_refresh.AdapterLayer(backend.IndexStore(), [adapter], start_worker=False)
        layer.request_refresh(["paper"], force=True, paths=[str(tmp_path / "one.json")])
        assert layer._pending["paper"].rescan is False
        layer.request_refresh(["paper"])
        assert layer._pending["paper"].rescan is False
        layer.request_refresh(["paper"], force=True)
        assert layer._pending["paper"].rescan is True


def test_knowledge_change_counts_entry_files_like_the_full_scan(tmp_path: Path) -> None:
    """知识条目按路径更新时，文件数与截断标记与完整扫描一致

    新增和已有知识条目按路径更新时，条目文件按完整扫描的上限截取，file_count 与 file_count_truncated 与完整扫描相同。

    :importance: P2
    """
    project_root = tmp_path / "Example"
    entry = project_root / "knowledge_base" / "thinking" / "run"
    entry.mkdir(parents=True)
    readme = entry / "README.md"
    readme.write_text(
        "---\nkb_id: thinking_0123456789ab\ntype: thinking\nproject: Example\n"
        "created_at: 2026-10-11\nimportance: 5\n---\n\n# Run\n\nBody\n",
        encoding="utf-8",
    )
    for name in ("a.md", "b.md", "c.md"):
        (entry / name).write_text(name, encoding="utf-8")
    project = {"id": "Example", "status": "active", "path": str(project_root)}
    adapter = resource_index_knowledge.ProjectKnowledgeAdapter()
    with mock.patch.object(
        resource_index_knowledge.knowledge_source, "_parse_projects_yaml", return_value=[project],
    ), mock.patch.object(resource_index_knowledge.knowledge_source, "_INDEX_ENTRY_FILE_LIMIT", 2):
        added = adapter.scan_changes([str(readme)], [], {})
        assert added is not None
        [record] = added.upserts
        (entry / "c.md").write_text("edited", encoding="utf-8")
        changed = adapter.scan_changes([str(entry / "c.md")], [record], {})

    assert changed is not None
    [again] = changed.upserts
    for item in (record, again):
        assert item["resource_id"] == "thinking_0123456789ab"
        assert item["attributes"]["file_count"] == 2
        assert item["attributes"]["file_count_truncated"] is True


def _symlinked_paper_store(tmp_path: Path) -> tuple[paper_store.PaperStore, Path]:
    """A store whose items directory is a link, as /share/Papers/items is."""
    real_items = tmp_path / "data" / "items"
    real_items.mkdir(parents=True)
    store = paper_store.PaperStore(tmp_path / "papers")
    store.root.mkdir()
    store.items_root.symlink_to(real_items)
    return store, real_items


def test_paper_change_keeps_the_manifest_time_and_the_store_path(tmp_path: Path) -> None:
    """Paper 读取保留清单中的更新时间，按路径更新使用 Paper 库路径

    读取 Paper 清单不再把 updated_at 改成读取时间；Paper 库目录是链接时，按真实路径找到的变化仍以 Paper 库路径作为记录来源。
    """
    store, real_items = _symlinked_paper_store(tmp_path)
    package = real_items / "2026" / "example"
    package.mkdir(parents=True)
    manifest_path = package / "manifest.json"

    def write_manifest(title: str, updated_at: int) -> None:
        manifest_path.write_text(json.dumps({
            "paper_id": PAPER_ONE,
            "paper_key": PAPER_ONE,
            "title": title,
            "source": {"created_at": 100, "updated_at": updated_at},
            "files": {},
            "relations": {},
        }), encoding="utf-8")

    write_manifest("Example", 200)
    adapter = PaperManifestAdapter()
    with mock.patch.object(paper_store, "PAPER_STORE", store):
        [record], _state = adapter.scan()
        write_manifest("Example edited", 300)
        changes = adapter.scan_changes([str(manifest_path)], [record], {})

    store_path = store.items_root / "2026" / "example" / "manifest.json"
    assert record["source"]["path"] == str(store_path)
    assert record["attributes"]["updated_at"] == 200
    assert changes is not None
    [changed] = changes.upserts
    assert changed["title"] == "Example edited"
    assert changed["source"]["path"] == str(store_path)
    assert changed["attributes"]["updated_at"] == 300


def test_paper_review_change_matches_the_full_scan(tmp_path: Path) -> None:
    """Paper 评审按路径更新与完整扫描结果一致

    修改、删除评审文件按路径更新，结果与完整扫描相同；在线评审无法定位文件时只用文件本身；在线评审列表变化或评审 ID 出现在其他文件时改为完整扫描。
    """
    store, real_items = _symlinked_paper_store(tmp_path)
    reviews_dir = real_items / "2026" / "example" / "reviews"
    reviews_dir.mkdir(parents=True)
    review_path = reviews_dir / f"{REVIEW_ID}.md"

    def write_review(path: Path, title: str) -> None:
        path.write_text(
            f'---\nreview_id: "{REVIEW_ID}"\npaper_id: "{PAPER_ONE}"\ncreated_by: "alice"\n'
            f"created_at: 123\nstatus: ai_generated\n---\n\n# {title}\n\nSummary.\n",
            encoding="utf-8",
        )

    write_review(review_path, "Review")
    adapter = PaperReviewAdapter()
    live = {"review_id": REVIEW_ID, "paper_id": PAPER_ONE, "overall_recommendation": "accept"}
    with mock.patch.object(paper_store, "PAPER_STORE", store), mock.patch.object(
        paper_store, "_paper_by_id", return_value={"id": PAPER_ONE, "title": "Paper", "project_ids": []},
    ), mock.patch.object(paper_reviews, "_paper_by_id", paper_store._paper_by_id), mock.patch.object(paper_reviews, "_all_reviews", return_value=[]) as all_reviews:
        [record], _state = adapter.scan()

        write_review(review_path, "Review edited")
        changes = adapter.scan_changes([str(review_path)], [record], {})
        assert changes is not None
        assert changes.upserts == tuple(adapter.scan()[0])
        assert changes.upserts[0]["title"] == "Review edited"
        assert changes.upserts[0]["source"]["path"] == str(store.items_root / "2026" / "example" / "reviews" / f"{REVIEW_ID}.md")

        # The paper has no reviews directory in its manifest, so the live
        # review cannot place its file; the file alone stands for it.
        all_reviews.return_value = [live]
        changes = adapter.scan_changes([str(review_path)], [record], {})
        assert changes is not None
        assert changes.upserts == tuple(adapter.scan()[0])
        assert changes.source_state["resource_errors"][0]["resource_id"] == REVIEW_ID
        all_reviews.return_value = []

        other_dir = real_items / "2026" / "other" / "reviews"
        other_dir.mkdir(parents=True)
        write_review(other_dir / f"{REVIEW_ID}.md", "Copy")
        assert adapter.scan_changes([str(other_dir / f"{REVIEW_ID}.md")], [record], {}) is None
        (other_dir / f"{REVIEW_ID}.md").unlink()

        review_path.unlink()
        changes = adapter.scan_changes([str(review_path)], [record], {})
        assert changes is not None
        assert changes.upserts == ()
        assert changes.deletes == (REVIEW_ID,)
