"""Desktop startup drives the shared installer for the registered default instance.

:module: operations.deployment
:design: decision_d88306d0bd17
:profiles: local.fixture
:risk: no-external-side-effects"""
import contextlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from management_common.instance_manifest import load
from scripts import tfi_windows_launcher as launcher
from scripts import portal_setup
from scripts import tfi_release_install as installer
from tests.cases.instance_test_support import write_instance


class DesktopStartup(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.platform = Path(temporary.name) / "program"
        self.root = Path(temporary.name) / "instances/example_instance"
        bundle = self.platform / ".build/w/engineering/manifest.json"
        bundle.parent.mkdir(parents=True)
        bundle.write_text(json.dumps({"run_id": "fixture"}))
        self.flow = launcher.DefaultStartup(self.platform)
        self.exists = False
        for name, value in (("registered", lambda: self.exists),
                            ("instance", lambda: load(self.root)),
                            ("engineering", lambda: bundle)):
            patcher = patch.object(self.flow, name, side_effect=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def created(self):
        write_instance(self.root)
        self.exists = True

    def test_elevated_launch_stops_before_initializing_or_touching_existing_state(self):
        with patch.object(launcher, 'is_elevated_process', return_value=True), \
                patch.object(self.flow, 'initialize') as initialize, \
                patch.object(self.flow, 'execute') as execute:
            with self.assertRaisesRegex(RuntimeError, '不要选择“以管理员身份运行”'):
                self.flow.run()
        initialize.assert_not_called()
        execute.assert_not_called()
        self.flow.instance.assert_not_called()

    def test_first_launch_creates_then_opens_registration_and_next_launch_keeps_accounts(self):
        actions = []
        accounts_exist = False

        def execute(argv):
            action = argv[2]
            actions.append(action)
            if action == "create":
                self.assertEqual(argv[argv.index("--name") + 1], "example_instance")
                self.created()
            elif action == "first-admin":
                self.assertEqual(argv[3:5], ["--instance", "example_instance"])
                self.assertIn("--if-needed", argv)
                if accounts_exist:
                    return '@TFI_PROGRESS {"message": "x"}\n{\n  "status": "already_configured"\n}\n'
                secrets = self.root / "secrets"
                secrets.mkdir(parents=True, exist_ok=True)
                (secrets / installer.FIRST_ADMIN_FILE).write_text("http://127.0.0.1:28086/join#invite=test-token")
                return '{\n  "status": "setup_link_created"\n}\n'
            return "{}"
        with patch.object(self.flow, "execute", side_effect=execute), \
                patch.object(self.flow, "account_configured", return_value=False), \
                patch.object(self.flow, "definitions_current", return_value=True), \
                patch.object(self.flow, "running_url", return_value=None):
            self.assertEqual(self.flow.run(), "http://127.0.0.1:28086/join#invite=test-token")
            self.assertEqual(actions, ["create", "first-admin"])
            before = (self.root / "instance.json").read_bytes()
            accounts_exist = True
            actions.clear()
            self.assertEqual(self.flow.run(), "http://127.0.0.1:28086/")
            self.assertEqual(actions, ["start", "first-admin"])
            self.assertEqual(before, (self.root / "instance.json").read_bytes())

    def test_healthy_reopen_does_not_start_services(self):
        self.created()
        with patch.object(self.flow, 'running_url', return_value='http://127.0.0.1:28086'), \
                patch.object(self.flow, 'definitions_current', return_value=True), \
                patch.object(self.flow, 'account_configured', return_value=True), \
                patch.object(self.flow, 'execute') as execute:
            self.assertEqual(self.flow.run(), 'http://127.0.0.1:28086/')
        execute.assert_not_called()
        self.assertEqual([t['stage'] for t in self.flow.timings], ['running_check', 'account_check'])

    def test_replaced_code_runs_the_installers_code_update_instead_of_start(self):
        self.created()
        with patch.object(self.flow, 'definitions_current', return_value=False), \
                patch.object(self.flow, 'running_url') as running, \
                patch.object(self.flow, 'account_configured', return_value=True), \
                patch.object(self.flow, 'execute') as execute:
            self.flow.run()
        self.assertEqual(execute.call_args.args[0][2:5], ['update-code', '--instance', 'example_instance'])
        running.assert_not_called()

    def test_definition_check_reports_a_previous_code_environment(self):
        self.created()
        instance = load(self.root)
        with patch('scripts.instance_services.verify_definitions', return_value=None):
            self.assertTrue(self.flow.definitions_current(instance))
        with patch('scripts.instance_services.verify_definitions',
                   side_effect=ValueError('Instance service definition differs from its manifest')):
            self.assertFalse(self.flow.definitions_current(instance))

    def test_account_status_requires_positive_current_instance_response(self):
        self.created()
        instance = load(self.root)
        expected = json.dumps({"instance": instance.name, "configured": True}).encode()
        for payload in [b'not json', b'[]', b'{}',
                        b'{"instance":"other","configured":true}',
                        json.dumps({"instance": instance.name, "configured": "true"}).encode(),
                        json.dumps({"instance": instance.name, "configured": False}).encode(),
                        expected]:
            with self.subTest(payload=payload):
                response = Mock(status=200)
                response.read.return_value = payload
                with patch.object(installer._LOCAL_HTTP, 'open', return_value=contextlib.nullcontext(response)):
                    self.assertEqual(self.flow.account_configured('http://127.0.0.1:28086', instance),
                                     payload == expected)
        with patch.object(installer._LOCAL_HTTP, 'open', side_effect=OSError('offline')):
            self.assertFalse(self.flow.account_configured('http://127.0.0.1:28086', instance))

    def test_reopen_trusts_only_fresh_complete_health_of_this_instance(self):
        self.created()
        instance = load(self.root)
        with patch.object(installer, 'observed_status') as observe:
            for status in ['unhealthy', 'running']:
                observe.return_value = {'status': status, 'url': 'http://127.0.0.1:28086'}
                self.assertEqual(self.flow.running_url(instance),
                                 'http://127.0.0.1:28086' if status == 'running' else None)
            observe.assert_called_with(instance, request_timeout=0.5)

    def test_failed_creation_never_attempts_start(self):
        def execute(argv):
            if argv[2] == "create":
                raise RuntimeError("download failed")
            return "{}"
        with patch.object(self.flow, "execute", side_effect=execute) as execute:
            with self.assertRaisesRegex(RuntimeError, "download failed"):
                self.flow.run()
        self.assertEqual([call.args[0][2] for call in execute.call_args_list], ["create"])

    def test_incomplete_creation_is_retired_and_created_again_before_start(self):
        self.created()
        (self.root / "runtime").mkdir()
        (self.root / "runtime/creation.json").write_text(json.dumps({"phase": "database", "status": "failed"}))
        actions = []

        def execute(argv):
            actions.append(argv[2])
            if argv[2] == "retire":
                (self.root / "runtime/creation.json").unlink()
                self.exists = False
            elif argv[2] == "create":
                self.created()
            return '{"status":"already_configured"}'
        with patch.object(self.flow, "execute", side_effect=execute), \
                patch.object(self.flow, "account_configured", return_value=False):
            self.flow.run()
        self.assertEqual(actions, ["retire", "create", "first-admin"])

    def test_installer_result_follows_progress_lines(self):
        output = '@TFI_PROGRESS {"message": "正在启动"}\n{\n  "status": "setup_link_created"\n}\n'
        self.assertEqual(launcher.last_result(output), {"status": "setup_link_created"})
        self.assertEqual(launcher.last_result('{"status": "ok"}'), {"status": "ok"})
        with self.assertRaisesRegex(ValueError, '没有返回结果'):
            launcher.last_result('no json')

    def test_existing_account_does_not_create_or_replace_an_invitation(self):
        enrollment = Mock()
        enrollment.has_accounts.return_value = True
        with patch("management_portal.user_enrollment.UserEnrollment", return_value=enrollment), \
                patch("management_portal.portal_access_control.get_portal_access_store"), \
                patch("management_portal.portal_access_control.PortalCredentialStore"), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            portal_setup.first_admin(SimpleNamespace(if_needed=True))
        self.assertEqual(json.loads(output.getvalue()), {"status": "already_configured"})
        enrollment.create_invitation.assert_not_called()


class PlatformInstances(unittest.TestCase):
    def test_only_instances_running_this_platform_code_are_stopped_and_unregistered(self):
        from management_common import host_instances
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            platform = base / "program"
            (platform / "code").mkdir(parents=True)
            ours = load(write_instance(base / "ours", code=platform / "code"))
            other = load(write_instance(base / "other"))
            registry = {"ours": str(ours.root), "other": str(other.root)}
            with patch.object(host_instances, "read_registry", return_value=registry):
                self.assertEqual([i.root for i in launcher.platform_instances(platform)], [ours.root])
                with patch.object(launcher.subprocess, "run") as run, \
                        patch("scripts.instance_services.remove_definitions") as remove:
                    launcher.stop_all(platform, unregister=True)
            self.assertEqual(run.call_args.args[0][2:5], ["stop", "--instance", ours.name])
            remove.assert_called_once()
            self.assertEqual(remove.call_args.args[0].root, ours.root)


if __name__ == "__main__":
    unittest.main()


def test_explicit_selection_never_creates_missing_or_foreign_instance(tmp_path, monkeypatch):
    flow = launcher.DefaultStartup(tmp_path, instance="second")
    monkeypatch.setattr(flow, "choices", lambda: ["first", "second"])
    flow.validate_selection()
    assert flow.name == "second"
    monkeypatch.setattr(flow, "choices", lambda: ["first"])
    import pytest
    with pytest.raises(ValueError, match="退役"):
        flow.run()
    assert not (tmp_path / ".build").exists()


def test_desktop_multiple_instances_waits_for_choice(tmp_path, monkeypatch):
    import pytest
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Desktop display unavailable")
    flow = launcher.DefaultStartup(tmp_path)
    monkeypatch.setattr(flow, "choices", lambda: ["first", "second"])
    calls = []
    monkeypatch.setattr(launcher.Launcher, "begin", lambda self: calls.append(self.flow.name))
    try:
        window = launcher.Launcher(root, flow=flow)
        root.update()
        assert calls == []
        combo, button = window.selection.winfo_children()
        combo.set("second")
        button.invoke()
        assert calls == ["second"] and flow.selected_name == "second"
    finally:
        root.destroy()


def test_only_nondefault_instance_is_opened_without_recreating_default(tmp_path, monkeypatch):
    import pytest
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Desktop display unavailable")
    flow = launcher.DefaultStartup(tmp_path)
    monkeypatch.setattr(flow, "choices", lambda: ["remaining"])
    calls = []
    monkeypatch.setattr(launcher.Launcher, "begin", lambda self: calls.append(self.flow.name))
    try:
        launcher.Launcher(root, flow=flow)
        root.update()
        assert calls == ["remaining"]
    finally:
        root.destroy()
