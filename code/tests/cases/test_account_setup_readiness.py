"""执行账号的冷启动与模型目录就绪检查。

:module: execution.providers
:design: decision_e587fe1c472e
:profiles: local.fixture
:risk: no-external-side-effects
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from management_portal.codex_app_server_protocol import CodexAppServerUnavailable
from management_portal.execution import account_setup


class AccountSetupReadinessTests(unittest.TestCase):
    def test_cold_service_waits_for_catalog_before_check(self):
        """冷启动时等待服务提供模型目录后再完成账号检查。"""
        info = {"models": ["model"], "default_model": "model"}
        with patch.object(account_setup, "catalog", side_effect=[
            CodexAppServerUnavailable("socket not listening"), info
        ]) as catalog, patch.object(account_setup.time, "sleep") as sleep:
            self.assertEqual(account_setup._started_codex_catalog({"account_id": "test"}), info)
        self.assertEqual(catalog.call_count, 2)
        sleep.assert_called_once()

    def test_unavailable_service_remains_an_error_after_deadline(self):
        """就绪期限耗尽后仍报告服务不可用，不继续重试。"""
        with patch.object(account_setup, "catalog", side_effect=CodexAppServerUnavailable("down")), \
                patch.object(account_setup.time, "monotonic", side_effect=[0, 30]), \
                patch.object(account_setup.time, "sleep") as sleep:
            with self.assertRaises(CodexAppServerUnavailable):
                account_setup._started_codex_catalog({"account_id": "test"})
        sleep.assert_not_called()

    def test_invalid_catalog_is_not_retried(self):
        """模型目录内容无效时直接失败，不按启动延迟重试。"""
        with patch.object(account_setup, "catalog", side_effect=ValueError("invalid model")), \
                patch.object(account_setup.time, "sleep") as sleep:
            with self.assertRaisesRegex(ValueError, "invalid model"):
                account_setup._started_codex_catalog({"account_id": "test"})
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()


def test_jev_account_check_submits_required_typed_question(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from management_portal.ai_provider_registry import shared_ai_provider_registry
    from management_portal.execution import resident_configuration
    row = {"account_id": "jev", "provider": "provider_api", "kind": "api_profile", "enabled": True, "config": {"api_id": "jev"}}
    monkeypatch.setattr(account_setup, "account", lambda account: row)
    monkeypatch.setattr(account_setup.provider_updates, "runtime_available", lambda provider: True)
    monkeypatch.setattr(account_setup.provider_updates, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(account_setup, "catalog", lambda row: {"default_model": "jev-latest"})
    monkeypatch.setattr(account_setup, "signature", lambda row: {})
    monkeypatch.setattr(resident_configuration, "validate_api_settings", lambda *args: None)
    monkeypatch.setattr(shared_ai_provider_registry, "get", lambda route: {"kind": "typesafe_system_one"})
    requests = []
    def submit(request):
        requests.append(request)
        return SimpleNamespace(execution_id="test-execution")
    monkeypatch.setattr(account_setup, "ExecutionClient", lambda: SimpleNamespace(submit=submit))
    inv = SimpleNamespace(caller="alice", argument=lambda name: "jev" if name == "account_id" else None)
    account_setup.check(inv)
    assert requests[0].runtime_options["parameters"]["questions"]["greeting"]["type"] == "noul"
    assert requests[0].provider == "jev"
