"""发行安装与实例隔离回归。

:module: operations.deployment
:design: decision_d88306d0bd17
:profiles: local.fixture
:risk: no-external-side-effects"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import PropertyMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "code"))
from scripts import portal_service_units as units
from management_portal.execution import provider_updates as updates


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux user service ownership")
class InstanceProviderUpdateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        # These cases also run directly without pytest's site-isolation fixture.
        # Keep the real activation owner inside this case even if its lookup
        # changes; mocking only link() previously replaced a live SDK symlink.
        runtimes = patch.object(updates, "RUNTIMES", self.root / "runtimes")
        runtimes.start()
        self.addCleanup(runtimes.stop)
        deployment = patch.object(updates, "_PROVIDER_DEPLOYMENT", {})
        deployment.start()
        self.addCleanup(deployment.stop)
        self.context = {"root": self.root / "instance", "directory": self.root,
                        "service_prefix": "tfi-one"}
        self.context_patch = patch.object(units, "_isolated_provider_context", return_value=self.context)
        self.context_patch.start()
        self.addCleanup(self.context_patch.stop)
        self.active = True
        self.commands = []
        self.runner = patch.object(units.subprocess, "run", side_effect=self.run_systemctl)
        self.runner.start()
        self.addCleanup(self.runner.stop)

    def unit(self, provider):
        name = f"tfi-one-{provider}-code.service"
        path = self.root / name
        path.write_text(
            f"Environment=HOME={self.context['root']}/home\n"
            f"Environment=PORTAL_PATHS_CONFIG={self.context['root']}/config/paths.json\n")
        return name, path

    def run_systemctl(self, arguments, **kwargs):
        self.commands.append(arguments)
        self.assertIn("DBUS_SESSION_BUS_ADDRESS", kwargs["env"])
        self.assertFalse(any(a.startswith("portal-") for a in arguments))
        if "show" in arguments:
            name = arguments[3]
            output = (f"FragmentPath={self.root / name}\nDropInPaths=\nLoadState=loaded\n"
                      f"ActiveState={'active' if self.active else 'inactive'}\nSubState=running\nMainPID=1\n")
        else:
            output = ""
        return subprocess.CompletedProcess(arguments, 0, stdout=output, stderr="")

    def test_activation_restarts_only_current_instance_for_both_daemons(self):
        """安装与实例回归：activation restarts only current instance for both daemons。"""
        for provider, runtime in (("claude", updates.ClaudeRuntime()), ("kimi", updates.KimiRuntime())):
            with self.subTest(provider=provider):
                name, _ = self.unit(provider)
                old = self.root / (provider + "-old")
                new = self.root / (provider + "-new")
                old.mkdir()
                new.mkdir()
                link = runtime.activation_link()
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(old)
                with patch.object(updates, "_active_executions", return_value=0), \
                        patch.object(runtime, "busy", return_value=False):
                    self.assertTrue(updates._activate(provider, runtime, new))
                self.assertIn(["systemctl", "--user", "restart", name], self.commands)
                self.assertEqual(link.resolve(), new)

    def test_default_activation_links_stay_in_fixture(self):
        """Direct execution must not inherit the running site's runtime links."""
        for runtime in (updates.ClaudeRuntime(), updates.KimiRuntime()):
            with self.subTest(provider=type(runtime).__name__):
                self.assertTrue(runtime.activation_link().is_relative_to(self.root))

    def test_codex_activation_replaces_private_link_without_changing_shared_binary(self):
        """安装与实例回归：codex activation replaces private link without changing shared binary。"""
        shared = self.root / "shared-install/bin/codex"
        shared.parent.mkdir(parents=True)
        shared.write_text("original shared executable\n")
        staged = self.root / "new-runtime"
        binary = updates.CodexRuntime().link_target(staged)
        binary.parent.mkdir(parents=True)
        binary.write_text("new executable\n")
        link = self.context["root"] / "home/.local/bin/codex"
        link.parent.mkdir(parents=True)
        link.symlink_to(shared)
        with patch.dict(updates._PROVIDER_DEPLOYMENT, {"codex_binary": str(link)}), \
                patch.object(updates, "_active_executions", return_value=0), \
                patch.object(updates.CodexRuntime, "services", new_callable=PropertyMock,
                             return_value=()):
            runtime = updates.CodexRuntime()
            self.assertEqual(runtime.link(), link)
            self.assertTrue(updates._activate("codex", runtime, staged))
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), binary)
        self.assertEqual(shared.read_text(), "original shared executable\n")

    def test_stopped_or_absent_daemon_is_not_started_by_update(self):
        """安装与实例回归：stopped or absent daemon is not started by update。"""
        self.assertEqual(updates.KimiRuntime().services, ())
        self.assertEqual(self.commands, [])
        self.unit("kimi")
        self.active = False
        self.assertEqual(updates.KimiRuntime().services, ())
        self.assertFalse(any("restart" in cmd for cmd in self.commands))

    def test_foreign_definition_is_rejected_before_service_control(self):
        """安装与实例回归：foreign definition is rejected before service control。"""
        _, path = self.unit("claude")
        path.write_text("Environment=HOME=/another-instance/home\n")
        with self.assertRaisesRegex(ValueError, "another instance"):
            _ = updates.ClaudeRuntime().services
        self.assertEqual(self.commands, [])

    def test_foreign_unit_cannot_be_controlled_directly(self):
        """安装与实例回归：foreign unit cannot be controlled directly。"""
        with self.assertRaisesRegex(ValueError, "Refusing source service"):
            updates._service_control().restart("portal-claude-code.service")
        self.assertEqual(self.commands, [])

    def test_source_deployment_keeps_its_existing_names(self):
        """安装与实例回归：source deployment keeps its existing names。"""
        with patch.object(units, "_isolated_provider_context", return_value=None):
            self.assertEqual(updates.ClaudeRuntime().services, ("portal-claude-code.service",))
            self.assertEqual(updates.KimiRuntime().services, ("portal-kimi-code.service",))
        self.assertEqual(updates.ClaudeRuntime().unit_files, ("portal-claude-code.service",))

    def test_ownership_error_is_not_a_fallback_to_host(self):
        """安装与实例回归：ownership error is not a fallback to host。"""
        with patch.object(units, "_isolated_provider_context", side_effect=ValueError("ownership")):
            with self.assertRaisesRegex(ValueError, "ownership"):
                _ = updates.KimiRuntime().services
        self.assertEqual(self.commands, [])


if __name__ == "__main__":
    unittest.main()
