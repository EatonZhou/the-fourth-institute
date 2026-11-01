"""Canonical test contracts and the selected instance's test settings.

:module: operations.test-framework
:profiles: local.fixture
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from management_common.format_tags import schema_matches
from management_portal.test_framework import compatibility, paths
from tests.cases.instance_test_support import select_instance


def test_record_marker_must_be_the_canonical_schema(tmp_path):
    """Only the canonical schema marker authorizes a record; rejection never rewrites it."""
    from management_portal.test_framework.records import RunRecords
    canonical = "tfi.test-run-index.v1"
    record = tmp_path / "runs.json"
    for marker in ("archive.controlled-run-index.v1", "archive.test-run-index.v1", "tfi.test-run-index.v2", None):
        raw = json.dumps({"schema": marker, "runs": [], "digest": "frozen"}).encode()
        record.write_bytes(raw)
        assert not schema_matches(marker, canonical)
        with pytest.raises(RuntimeError, match="结构无效"):
            RunRecords(record)._read_index()
        assert record.read_bytes() == raw
    record.write_text(json.dumps({"schema": canonical, "runs": [], "digest": "frozen"}))
    before_record = record.read_bytes()
    assert RunRecords(record)._read_index() == []
    assert record.read_bytes() == before_record


def test_actor_and_state_follow_the_selected_instance(tmp_path, monkeypatch):
    """Test identity comes from the instance's site settings; directories come from its manifest."""
    monkeypatch.setenv("PORTAL_DEPLOYMENT", "dev")
    root = select_instance(monkeypatch, tmp_path / "instance",
                           site={"deployment": {"test_framework": {"actor": "site-operator"}}}).root
    before_site = (root / "site.json").read_bytes()
    assert compatibility.test_actor() == "site-operator"
    assert paths.state_root() == root / "runtime/test-framework/dev"
    # Module-level defaults consume the selected instance in a fresh interpreter.
    script = ("import json; from management_portal.test_framework import executor, tau; "
              "print(json.dumps([executor.UNIT_PREFIX, str(tau.DEFAULT_STORAGE_STATE)]))")
    environment = {"PATH": "/usr/bin:/bin", "PORTAL_INSTANCE_ROOT": str(root), "PORTAL_DEPLOYMENT": "dev"}
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[2],
                            env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["tfi-testrun-", str(root / "runtime/test-framework/dev/test-actor-storage-state.json")]
    assert (root / "site.json").read_bytes() == before_site


def test_actor_defaults_and_rejects_empty_identity(tmp_path, monkeypatch):
    select_instance(monkeypatch, tmp_path / "instance", site={})
    assert compatibility.test_actor() == "test-actor"
    assert not schema_matches("archive.test-run-index.v1", "tfi.test-run-index.v1")
    select_instance(monkeypatch, tmp_path / "instance", site={"deployment": {"test_framework": {"actor": ""}}})
    with pytest.raises(ValueError):
        compatibility.test_actor()


def test_catalog_and_runner_admit_only_the_canonical_live_profile(tmp_path):
    """Only the canonical Live profile is admitted."""
    from management_portal.test_framework.catalog_source import build_catalog
    from management_portal.test_framework.registry import RunnerRegistry
    from management_portal.test_framework.local_runs import lane
    source = tmp_path / "tests/cases/test_profile.py"
    source.parent.mkdir(parents=True)
    source.write_text('def test_existing():\n    """Controlled check\n\n    :profiles: live.archive\n    """\n    pass\n')
    original = source.read_bytes()
    assert build_catalog(tmp_path).errors
    with pytest.raises(KeyError, match="未知运行配置"):
        RunnerRegistry().profile("live.archive")
    assert lane({"implementation": {"profiles": ["live.archive"]}}) == "isolated"
    assert source.read_bytes() == original
    source.write_text(source.read_text().replace("live.archive", "live.controlled"))
    assert not build_catalog(tmp_path).errors
    assert RunnerRegistry().profile("live.controlled").id == "live.controlled"
    assert lane({"implementation": {"profiles": ["live.controlled"]}}) == "live"
    live = source.with_name("live_cases.json")
    for schema in ("archive.portal-live-cases.v1", "tfi.portal-live-cases.v2", None):
        live.write_text(json.dumps({"schema": schema, "implementations": {}}))
        original_live = live.read_bytes()
        assert build_catalog(tmp_path).errors
        assert live.read_bytes() == original_live
    live.write_text(json.dumps({"schema": "tfi.portal-live-cases.v1", "implementations": {}}))
    assert not build_catalog(tmp_path).errors


def test_live_document_projects_only_public_configuration_before_state(tmp_path, monkeypatch):
    """Sandbox uses canonical browser defaults and never projects site settings."""
    from types import SimpleNamespace
    from management_portal.test_framework import live_adapter
    source = tmp_path / "demo.html"
    source.write_text('<html><head></head><body><script src="/static/portal_state.js"></script>'
                      '<script src="/static/portal_i18n.js"></script></body></html>')
    case = SimpleNamespace(id="test_0123456789ab", live={"mode": "sandbox", "target": "/test-demo/components"})
    monkeypatch.setattr(live_adapter.api, "service", lambda: SimpleNamespace(catalog=SimpleNamespace(get=lambda _: case)))
    monkeypatch.setitem(live_adapter._SANDBOX_TARGETS, "/test-demo/components", source)
    monkeypatch.setattr(live_adapter, "_inline_static_assets", lambda html: html)
    for marker in ("archive.demo.v1", "</script>"):
        select_instance(monkeypatch, tmp_path / "instance", site={"deployment": {
            "test_framework": {"actor": "private-do-not-project"}}, "browser_identifiers": {"demo_frame_scope": marker}})
        html = live_adapter.demo_frame_html(case.id, "fixture-session-123456").decode()
        assert html.count('src="/static/portal_state.js"') == 1
        assert html.index('/static/portal_state.js') < html.index('/static/portal_demo_frame.js')
        assert html.index('/static/portal_state.js') < html.index('/static/portal_i18n.js')
        assert "window.__PORTAL_SITE__" not in html and "archive.demo.v1" not in html
        assert "private-do-not-project" not in html and 'format_namespaces' not in html
        assert "connect-src 'none'" in html and "frame-src 'none'" in html
