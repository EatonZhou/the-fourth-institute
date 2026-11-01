"""发行安装与实例隔离回归。

:module: operations.deployment
:design: decision_d88306d0bd17
:profiles: local.fixture
:risk: no-external-side-effects"""
import json
import os
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
        from tests.cases.instance_test_support import write_instance
        from scripts import tfi_release_platform_linux as linux
        code = self.root / 'checkout/code'
        root = write_instance(self.root / 'instance', code=code)
        source = patch.object(units, 'SOURCE_ROOT', code)
        source.start()
        self.addCleanup(source.stop)
        document = json.loads((root / 'instance.json').read_text())
        document['services']['components'] += ['claude-code', 'kimi-code']
        (root / 'instance.json').write_text(json.dumps(document))
        environment = patch.dict(os.environ, {'PORTAL_INSTANCE_ROOT': str(root)})
        environment.start()
        self.addCleanup(environment.stop)
        directory = patch.object(linux, 'unit_directory', return_value=self.root)
        directory.start()
        self.addCleanup(directory.stop)
        self.context = {'root': root}
        # Definition checks query the service manager through the same guarded runner.
        from scripts import instance_services
        from scripts.portal_service_control import ServiceControl
        control = patch.object(instance_services, 'controller', return_value=ServiceControl(
            platform='linux', runner=lambda *arguments, check=True: units._run(*arguments, check=check)))
        control.start()
        self.addCleanup(control.stop)
        self.active = True
        self.commands = []
        self.runner = patch.object(units.subprocess, "run", side_effect=self.run_systemctl)
        self.runner.start()
        self.addCleanup(self.runner.stop)

    def unit(self, provider):
        name = f"tfi-fixture-{provider}-code.service"
        path = self.root / name
        path.write_text(
            f"Environment=PORTAL_INSTANCE_ROOT={self.context['root']}\n")
        return name, path

    def run_systemctl(self, arguments, **kwargs):
        self.commands.append(arguments)
        self.assertIn("DBUS_SESSION_BUS_ADDRESS", kwargs["env"])
        self.assertFalse(any(a.startswith("portal-") for a in arguments))
        if "show" in arguments:
            name = arguments[3]
            loaded = 'loaded' if (self.root / name).exists() else 'not-found'
            output = (f"FragmentPath={self.root / name}\nDropInPaths=\nLoadState={loaded}\n"
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

    def test_codex_activation_switches_code_link_and_preserves_previous_version(self):
        old = self.root / 'codex-old/bin/codex'
        old.parent.mkdir(parents=True)
        old.write_text('previous version')
        staged = self.root / 'codex-new'
        runtime = updates.CodexRuntime()
        binary = runtime.link_target(staged)
        binary.parent.mkdir(parents=True)
        binary.write_text('new version')
        link = runtime.activation_link()
        link.parent.mkdir(parents=True)
        link.symlink_to(old)
        with patch.object(updates, '_active_executions', return_value=0), \
                patch.object(updates.CodexRuntime, 'services', new_callable=PropertyMock, return_value=()):
            self.assertTrue(updates._activate('codex', runtime, staged))
        self.assertEqual(link.resolve(), binary)
        self.assertEqual(old.read_text(), 'previous version')

    def test_stopped_or_absent_daemon_is_not_started_by_update(self):
        """安装与实例回归：stopped or absent daemon is not started by update。"""
        self.assertEqual(updates.KimiRuntime().services, ())
        self.assertTrue(all("show" in cmd for cmd in self.commands))
        self.unit("kimi")
        self.active = False
        self.assertEqual(updates.KimiRuntime().services, ())
        self.assertFalse(any("restart" in cmd for cmd in self.commands))

    def test_foreign_definition_is_rejected_before_service_control(self):
        """安装与实例回归：foreign definition is rejected before service control。"""
        _, path = self.unit("claude")
        path.write_text("Environment=PORTAL_INSTANCE_ROOT=/another-instance\n")
        with self.assertRaisesRegex(ValueError, "another instance"):
            _ = updates.ClaudeRuntime().services
        self.assertEqual(self.commands, [])

    def test_foreign_unit_cannot_be_controlled_directly(self):
        """安装与实例回归：foreign unit cannot be controlled directly。"""
        with self.assertRaisesRegex(ValueError, "unregistered instance"):
            updates._service_control().restart("portal-claude-code.service")
        self.assertEqual(self.commands, [])

    def test_ownership_error_is_not_a_fallback_to_host(self):
        """安装与实例回归：ownership error is not a fallback to host。"""
        with patch.object(units, "_instance", side_effect=ValueError("ownership")):
            with self.assertRaisesRegex(ValueError, "ownership"):
                _ = updates.KimiRuntime().services
        self.assertEqual(self.commands, [])


if __name__ == "__main__":
    unittest.main()
