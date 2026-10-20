import tempfile
import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
from scripts import tfi_release_provider_services as providers


class ProviderCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.target = {"install_root": str(self.root / "instance"),
                       "service_prefix": "tfi-one",
                       "data_roots": {"accounts_root": str(self.root / "instance/accounts")}}
        self.template = self.root / "tfi-one-codex-app-server@.service"
        self.unit = "tfi-one-codex-app-server@account.service"
        self.template.write_text(
            f"Environment=HOME={self.root}/instance/home\n"
            f"Environment=PORTAL_PATHS_CONFIG={self.root}/instance/config/paths.json\n"
            f"WorkingDirectory={self.root}/instance/accounts/codex/%i\n"
            f"Environment=CODEX_HOME={self.root}/instance/accounts/codex/%i\n")
        self.runner = Mock(side_effect=self.run_systemctl)
        self.platform = patch.object(providers.sys, "platform", "linux")
        self.platform.start()
        self.addCleanup(self.platform.stop)

    def run_systemctl(self, command, *arguments):
        if command == "list-units":
            return f"{self.unit} loaded active running account\n"
        if command == "list-unit-files":
            return f"{self.unit} enabled enabled\n{self.template.name} static -\n"
        if command == "show":
            return f"FragmentPath={self.template}\nDropInPaths=\n"
        return ""

    def test_stops_only_owned_service_and_removes_template(self):
        controller = Mock()
        controller.status.return_value = {"pid": 0, "active": "inactive"}
        with patch.object(providers, "_run", self.runner):
            plan = providers.owned_services(self.target, self.root)
            providers.stop_services(plan, controller)
            providers.remove_template(self.target, self.root)
        controller.stop.assert_called_once_with(self.unit)
        self.runner.assert_any_call("disable", self.unit)
        self.assertFalse(self.template.exists())

    def test_foreign_instance_is_rejected_before_stop(self):
        self.target["install_root"] = str(self.root / "other")
        with patch.object(providers, "_run", self.runner), self.assertRaisesRegex(ValueError, "another instance"):
            providers.owned_services(self.target, self.root)

    def test_overrides_are_not_removed(self):
        def run(command, *arguments):
            if command == "show":
                return f"FragmentPath={self.template}\nDropInPaths=/foreign/custom.conf\n"
            return self.run_systemctl(command, *arguments)
        with patch.object(providers, "_run", side_effect=run), self.assertRaisesRegex(ValueError, "overrides"):
            providers.owned_services(self.target, self.root)
        self.assertTrue(self.template.exists())

    def test_live_provider_refuses_cleanup(self):
        controller = Mock()
        controller.status.return_value = {"pid": 123, "active": "active"}
        with patch.object(providers, "_run", self.runner):
            plan = providers.owned_services(self.target, self.root)
            with self.assertRaisesRegex(RuntimeError, "did not stop"):
                providers.stop_services(plan, controller)
        self.assertTrue(self.template.exists())
        self.assertFalse(any(call.args[0] == "disable" for call in self.runner.call_args_list))

    def test_absent_template_and_empty_unit_glob_need_no_cleanup(self):
        self.template.unlink()
        def run(command, **kwargs):
            return subprocess.CompletedProcess(command, 1 if command[2] == "list-unit-files" else 0,
                                               stdout="", stderr="")
        with patch.object(providers.subprocess, "run", side_effect=run):
            self.assertIsNone(providers.owned_services(self.target, self.root))

    def test_systemctl_error_is_not_accepted_as_no_services(self):
        result = subprocess.CompletedProcess([], 1, stdout="", stderr="Failed to connect to bus")
        with patch.object(providers.subprocess, "run", return_value=result):
            with self.assertRaises(subprocess.CalledProcessError):
                providers._run("list-unit-files", "tfi-one-*")

    def test_account_process_uses_instance_cache_and_temporary_directory(self):
        from scripts import portal_service_units as units
        context = {"root": self.root / "instance", "directory": self.root / "units",
                   "prefix": "tfi-one-codex-app-server@"}
        with patch.object(units, "_isolated_codex_context", return_value=context), \
                patch("scripts.portal_instance_storage.render_unit", return_value="[Service]\nType=simple\n"):
            path = units.install_codex_unit_template(reload=False)
        text = path.read_text()
        self.assertIn(f"Environment=XDG_CACHE_HOME={context['root']}/home/.cache\n", text)
        self.assertIn(f"Environment=TMPDIR={context['root']}/runtime\n", text)


if __name__ == "__main__":
    unittest.main()
