import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))

from management_portal.codex_app_server_client import CodexAppServerUnavailable
from management_portal.execution import account_setup


class AccountSetupReadinessTests(unittest.TestCase):
    def test_cold_service_waits_for_catalog_before_check(self):
        info = {"models": ["model"], "default_model": "model"}
        with patch.object(account_setup, "catalog", side_effect=[
            CodexAppServerUnavailable("socket not listening"), info
        ]) as catalog, patch.object(account_setup.time, "sleep") as sleep:
            self.assertEqual(account_setup._started_codex_catalog({"account_id": "test"}), info)
        self.assertEqual(catalog.call_count, 2)
        sleep.assert_called_once()

    def test_unavailable_service_remains_an_error_after_deadline(self):
        with patch.object(account_setup, "catalog", side_effect=CodexAppServerUnavailable("down")), \
                patch.object(account_setup.time, "monotonic", side_effect=[0, 30]), \
                patch.object(account_setup.time, "sleep") as sleep:
            with self.assertRaises(CodexAppServerUnavailable):
                account_setup._started_codex_catalog({"account_id": "test"})
        sleep.assert_not_called()

    def test_invalid_catalog_is_not_retried(self):
        with patch.object(account_setup, "catalog", side_effect=ValueError("invalid model")), \
                patch.object(account_setup.time, "sleep") as sleep:
            with self.assertRaisesRegex(ValueError, "invalid model"):
                account_setup._started_codex_catalog({"account_id": "test"})
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
