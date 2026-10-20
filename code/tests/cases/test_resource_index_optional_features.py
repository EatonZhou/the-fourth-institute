"""Optional feature removal must leave the ResourceService index usable."""
import json
import subprocess
import sys
from pathlib import Path

import pytest


PROBE = r'''
import fnmatch, importlib.abc, json, sys
from management_portal.portal_features import FEATURES
absent = set(json.loads(sys.argv[1]))
patterns = [pattern for feature in FEATURES if feature.key in absent for pattern in feature.modules]
from tests.cases.feature_packaging import drop_resource_types
drop_resource_types(patterns)
def left_out(name):
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)
class LeftOut(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if left_out(fullname):
            raise ModuleNotFoundError(fullname, name=fullname)
sys.meta_path.insert(0, LeftOut())
from management_portal import resource_index_backend as backend
service = backend.ResourceIndexService(start_worker=False)
adapters = service.adapter_layer.adapters
registry = adapters['resource_registry']
records, state = registry.scan()
assert not state['resource_errors'], state
assert registry.scan_changes([], [], {}).upserts == ()
for name in ('paper', 'paper_review'):
    if name in adapters:
        adapters[name].scan()
        adapters[name].scan_changes([], [], {})
        adapters[name].fingerprint()
print(json.dumps({'adapters': sorted(adapters), 'types': sorted(service.indexed_types()),
                  'registry_types': sorted(registry.resource_types),
                  'loaded_absent': [name for name in sys.modules if left_out(name)]}))
service.adapter_layer.stop()
'''


@pytest.mark.parametrize("absent", [[], ["article"], ["paper"], ["article", "paper"], ["test_framework"]])
def test_index_initializes_and_reads_with_optional_features(absent):
    """功能缺失或安装时，索引初始化与首次扫描均使用实际参与的适配器。

    :design: decision_8dc6b3d03ca7
    """
    result = subprocess.run([sys.executable, "-c", PROBE, json.dumps(absent)],
                            cwd=Path(__file__).resolve().parents[2], capture_output=True,
                            text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(result.stdout.strip().splitlines()[-1])
    assert not data["loaded_absent"]
    assert ("article" in data["registry_types"]) == ("article" not in absent)
    assert "repo" in data["registry_types"]
    assert ("paper" in data["adapters"]) == ("paper" not in absent)
    assert ("paper_review" in data["adapters"]) == ("paper" not in absent)
    assert ("test" in data["adapters"]) == ("test_framework" not in absent)


@pytest.mark.parametrize("broken", ["article", "paper"])
def test_installed_index_feature_with_missing_internal_dependency_fails(broken):
    """已安装功能内部依赖损坏时，索引不得静默当作未安装。

    :design: decision_15aab18c0ab3
    """
    probe = r'''
import importlib.abc, sys
name = 'management_portal.' + sys.argv[1] + ('_identity_backend' if sys.argv[1] == 'article' else '_backend')
class Broken(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == name:
            raise ModuleNotFoundError('broken_internal_dependency', name='broken_internal_dependency')
sys.meta_path.insert(0, Broken())
try:
    from management_portal.resource_index_backend import ResourceIndexService
    ResourceIndexService(start_worker=False)
except ModuleNotFoundError as exc:
    assert exc.name == 'broken_internal_dependency', exc
else:
    raise AssertionError('broken installation was silently ignored')
'''
    result = subprocess.run([sys.executable, "-c", probe, broken],
                            cwd=Path(__file__).resolve().parents[2], capture_output=True,
                            text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("feature", ["article", "paper"])
def test_feature_projection_code_invalidates_fingerprint(feature, monkeypatch):
    """功能适配器代码变化也使持久化索引投影指纹失效。

    :design: decision_8dc6b3d03ca7
    """
    from management_portal import article_resource_index, paper_resource_index
    from management_portal.resource_index_backend import ResourceRegistryAdapter

    module, adapter = (
        (article_resource_index, ResourceRegistryAdapter()) if feature == "article"
        else (paper_resource_index, paper_resource_index.PaperManifestAdapter())
    )
    before = adapter.fingerprint()
    monkeypatch.setattr(module, "PROJECTION_CODE_FINGERPRINT", "reloaded-code")
    assert adapter.fingerprint() != before


@pytest.mark.parametrize("adapter_name", ["paper", "paper_review"])
def test_paper_code_change_rebuilds_unchanged_sources(adapter_name, tmp_path, monkeypatch):
    """仅已加载的Paper投影代码变化，普通刷新也重建未变源文件的摘要。

    :design: decision_8dc6b3d03ca7
    """
    from management_portal import paper_backend, paper_resource_index
    from management_portal import resource_index_backend as backend

    papers = paper_backend.PaperStore(tmp_path / "papers")
    package = papers.items_root / "2026" / "example"
    package.mkdir(parents=True)
    paper_id = "paper_0123456789ab"
    review_id = "review_0123456789ab"
    (package / "manifest.json").write_text(json.dumps({
        "paper_id": paper_id, "paper_key": paper_id, "title": "Original",
        "source": {"created_at": 100, "updated_at": 200}, "files": {}, "relations": {},
    }))
    reviews = package / "reviews"
    reviews.mkdir()
    (reviews / (review_id + ".md")).write_text(
        f"---\nreview_id: {review_id}\npaper_id: {paper_id}\n---\n# Original\n\nBody\n")
    monkeypatch.setattr(paper_backend, "PAPER_STORE", papers)
    adapter = (paper_resource_index.PaperManifestAdapter() if adapter_name == "paper"
               else paper_resource_index.PaperReviewAdapter())
    store = backend.IndexStore()
    layer = backend.AdapterLayer(store, [adapter], start_worker=False)
    try:
        layer.refresh_now(adapter_name)
        assert [r["title"] for r in store.records_for_adapter(adapter_name)] == ["Original"]
        before = store.adapter_source_state(adapter_name)["projection_code_fingerprint"]
        # No source changes: simulate a reloaded adapter with a changed projection rule.
        monkeypatch.setattr(backend, "_find_source_changes", lambda _: {
            "changed": [], "missing": [], "resolved": {},
        })
        method = "_record_for_paper" if adapter_name == "paper" else "_record_for_review"
        original = getattr(adapter, method)
        monkeypatch.setattr(adapter, method, lambda *args: {**original(*args), "title": "Reprojected"})
        monkeypatch.setattr(paper_resource_index, "PROJECTION_CODE_FINGERPRINT", "reloaded-code", raising=False)
        layer.refresh_now(adapter_name)
        assert [r["title"] for r in store.records_for_adapter(adapter_name)] == ["Reprojected"]
        assert store.adapter_source_state(adapter_name)["projection_code_fingerprint"] != before
        assert layer.refresh_now(adapter_name)["changed"] is False
    finally:
        layer.stop()
