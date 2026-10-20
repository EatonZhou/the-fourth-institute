"""Canonical test contracts and explicit installation parameters.

:module: operations.test-framework
:profiles: local.fixture
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from management_common.format_tags import schema_matches
from management_portal.test_framework import compatibility, paths, executor, isolation


@contextmanager
def configured_site(tmp_path, domain_values=None):
    site = tmp_path / "site.json"
    site.write_text(json.dumps({"deployment": {"compatibility": {
        "format_namespaces": ["archive."],
        "environment_aliases": {"PORTAL_TEST_FRAMEWORK_STATE_ROOT": ["ARCHIVE_TEST_STATE"]},
        "domain_values": domain_values or {},
    }}}))
    with patch.dict(os.environ, {"PORTAL_SITE_CONFIG": str(site)}):
        yield site


def test_historical_marker_requires_explicit_same_version_alias(tmp_path):
    """Retired aliases cannot authorize a record; rejection never rewrites it."""
    from management_portal.test_framework.records import RunRecords
    canonical = "tfi.test-run-index.v1"
    historical = "archive.controlled-run-index.v1"
    record = tmp_path / "runs.json"
    with configured_site(tmp_path, {"test_framework.schema_aliases": {canonical: [historical]}}) as site:
        before_site = site.read_bytes()
        for marker in (historical, "archive.test-run-index.v1", "tfi.test-run-index.v2", None):
            raw = json.dumps({"schema": marker, "runs": [], "digest": "frozen"}).encode()
            record.write_bytes(raw)
            assert not schema_matches(marker, canonical)
            with pytest.raises(RuntimeError, match="结构无效"):
                RunRecords(record)._read_index()
            assert record.read_bytes() == raw
        record.write_text(json.dumps({"schema": canonical, "runs": [], "digest": "frozen"}))
        before_record = record.read_bytes()
        assert RunRecords(record)._read_index() == []
        assert record.read_bytes() == before_record and site.read_bytes() == before_site


def test_configured_actor_profiles_and_paths_are_read_in_place(tmp_path, monkeypatch):
    """Deployment identity and isolation paths remain explicit, without aliases."""
    home = tmp_path / "home"
    history = tmp_path / "history"
    history.mkdir()
    evidence = history / "evidence"
    evidence.write_bytes(b"unchanged evidence")
    secret = home / "site-credential"
    secret.parent.mkdir()
    secret.write_text("fixture secret")
    monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("PORTAL_TEST_FRAMEWORK_STATE_ROOT", raising=False)
    monkeypatch.delenv("PORTAL_POSTGRES_ENV_FILE", raising=False)
    monkeypatch.setenv("ARCHIVE_TEST_STATE", str(history))
    monkeypatch.setenv("PORTAL_INSTANCE", "dev")
    with configured_site(tmp_path, {"test_framework.actor": "site-operator",
                                   "test_framework.unit_prefix": "site-test-",
                                   "test_framework.storage_state": str(history / "browser.json"),
                                   "test_framework.credential_paths": [str(secret)]}) as site:
        settings = json.loads(site.read_text())
        settings["deployment"]["postgres_env_file"] = str(history / "db.env")
        site.write_text(json.dumps(settings))
        before_site = site.read_bytes()
        assert compatibility.test_actor() == "site-operator"
        assert paths.state_root() == home / ".local/state/tfi-portal/test-framework/dev"
        assert executor.postgres_environment_file() == str(history / "db.env")
        assert secret.resolve() in isolation.credential_paths(home)
        # Module-level defaults must consume this site's parameters in a fresh interpreter.
        script = ("import json; from management_portal.test_framework import executor, tau; "
                  "print(json.dumps([executor.UNIT_PREFIX, str(tau.DEFAULT_STORAGE_STATE)]))")
        result = subprocess.run([sys.executable, "-c", script],
                                cwd=Path(__file__).resolve().parents[2],
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == ["site-test-", str(history / "browser.json")]
        monkeypatch.setenv("PORTAL_TEST_FRAMEWORK_STATE_ROOT", str(tmp_path / "explicit"))
        monkeypatch.setenv("PORTAL_POSTGRES_ENV_FILE", str(tmp_path / "explicit.env"))
        assert paths.state_root() == tmp_path / "explicit"
        assert executor.postgres_environment_file() == str(tmp_path / "explicit.env")
        assert site.read_bytes() == before_site
    assert evidence.read_bytes() == b"unchanged evidence" and secret.read_text() == "fixture secret"
    assert not (history / "test-framework").exists()


def test_no_site_aliases_are_inferred(tmp_path, monkeypatch):
    config = tmp_path / "empty-site.json"
    config.write_text("{}")
    monkeypatch.setenv("PORTAL_SITE_CONFIG", str(config))
    assert compatibility.test_actor() == "test-actor"
    assert not schema_matches("archive.test-run-index.v1", "tfi.test-run-index.v1")
    with configured_site(tmp_path, {"test_framework.actor": ""}):
        with pytest.raises(ValueError):
            compatibility.test_actor()


def test_catalog_and_runner_share_explicit_historical_profile(tmp_path):
    """Only the canonical Live profile is admitted, regardless of retired config."""
    from management_portal.test_framework.catalog_source import build_catalog
    from management_portal.test_framework.registry import RunnerRegistry
    from management_portal.test_framework.local_runs import lane
    source = tmp_path / "tests/cases/test_profile.py"
    source.parent.mkdir(parents=True)
    source.write_text('def test_existing():\n    """Controlled check\n\n    :profiles: live.archive\n    """\n    pass\n')
    original = source.read_bytes()
    with configured_site(tmp_path, {"test_framework.live_profiles": ["live.archive"]}):
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
    """Sandbox uses canonical browser defaults and never projects site aliases/secrets."""
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
        with configured_site(tmp_path, {"browser_identifiers": {"demo_frame_scope": marker},
                                       "test_framework.actor": "private-do-not-project"}):
            html = live_adapter.demo_frame_html(case.id, "fixture-session-123456").decode()
        assert html.count('src="/static/portal_state.js"') == 1
        assert html.index('/static/portal_state.js') < html.index('/static/portal_demo_frame.js')
        assert html.index('/static/portal_state.js') < html.index('/static/portal_i18n.js')
        assert "window.__PORTAL_SITE__" not in html and "archive.demo.v1" not in html
        assert "private-do-not-project" not in html and 'format_namespaces' not in html
        assert "connect-src 'none'" in html and "frame-src 'none'" in html
