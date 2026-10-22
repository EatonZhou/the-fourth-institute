"""发行安装与实例隔离回归。

:module: operations.deployment
:design: decision_d88306d0bd17
:profiles: local.fixture
:risk: no-external-side-effects"""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "code"))
from scripts import tfi_windows_launcher as launcher
from scripts import portal_setup
from scripts import tfi_release_install as installer


class DesktopStartup(unittest.TestCase):
    def test_elevated_launch_stops_before_initializing_or_touching_existing_state(self):
        flow = launcher.DefaultStartup(Path('/unused'))
        with patch.object(launcher, 'is_elevated_process', return_value=True), \
                patch.object(flow, 'initialize') as initialize, \
                patch.object(flow, 'state') as state, patch.object(flow, 'execute') as execute:
            with self.assertRaisesRegex(RuntimeError, '不要选择“以管理员身份运行”'):
                flow.run()
        initialize.assert_not_called()
        state.assert_not_called()
        execute.assert_not_called()

    def setup_flow(self, platform):
        flow = launcher.DefaultStartup(platform)
        bundle = platform / ".build/w/engineering/manifest.json"
        bundle.parent.mkdir(parents=True)
        bundle.write_text(json.dumps({"run_id": "fixture"}))
        return flow

    def installed(self, flow):
        target = json.loads((flow.platform / ".build/windows/targets/example_instance.json").read_text())
        flow.root.mkdir(parents=True, exist_ok=True)
        state = {"target": target, "completed": ["database"], "stages": [{"status": "installed"}]}
        (flow.root / "installation.json").write_text(json.dumps(state))
        return state

    def test_first_launch_initializes_then_opens_registration_and_next_launch_keeps_accounts(self):
        """安装与实例回归：first launch initializes then opens registration and next launch keeps accounts。"""
        with tempfile.TemporaryDirectory() as tmp:
            flow = self.setup_flow(Path(tmp))
            actions = []
            accounts_exist = False
            def execute(argv):
                nonlocal accounts_exist
                action = "prepare" if Path(argv[1]).name == "prepare_install.py" else argv[2]
                actions.append(action)
                if action == "install":
                    self.installed(flow)
                elif action == "first-admin":
                    self.assertIn("--if-needed", argv)
                    if accounts_exist:
                        return '{"status":"already_configured"}'
                    secrets = Path(flow.state()["target"]["data_roots"]["secrets_dir"])
                    secrets.mkdir(parents=True)
                    (secrets / "first-admin-link.txt").write_text("http://127.0.0.1:28199/join#invite=test-token")
                    return '{"status":"setup_link_created"}'
                return "{}"
            with patch.object(flow, "execute", side_effect=execute), \
                    patch.object(flow, "wait_ready", return_value="http://127.0.0.1:28199") as ready:
                self.assertEqual(flow.run(), "http://127.0.0.1:28199/join#invite=test-token")
                self.assertEqual(actions, ["prepare", "install", "first-admin"])
                ready.assert_not_called()
                before = (flow.root / "installation.json").read_bytes()
                accounts_exist = True
                actions.clear()
                self.assertEqual(flow.run(), "http://127.0.0.1:28199/")
                self.assertEqual(actions, ["start", "first-admin"])
                ready.assert_called_once()
                self.assertEqual(before, (flow.root / "installation.json").read_bytes())

    def test_failed_install_never_attempts_start_or_opens_missing_state(self):
        """安装与实例回归：failed install never attempts start or opens missing state。"""
        with tempfile.TemporaryDirectory() as tmp:
            flow = self.setup_flow(Path(tmp))
            def execute_command(argv):
                if argv[2] == "install":
                    raise RuntimeError("download failed")
                return "{}"
            with patch.object(flow, "execute", side_effect=execute_command) as execute, \
                    patch.object(flow, "wait_ready") as ready:
                with self.assertRaisesRegex(RuntimeError, "download failed"):
                    flow.run()
                self.assertEqual(execute.call_count, 2)
                self.assertEqual(execute.call_args.args[0][2], "install")
                ready.assert_not_called()

    def test_preinstall_retry_rebuilds_bundle_and_keeps_target_parameters(self):
        """安装与实例回归：preinstall retry rebuilds bundle and keeps target parameters。"""
        with tempfile.TemporaryDirectory() as tmp:
            flow = self.setup_flow(Path(tmp))
            target_path = flow.platform / ".build/windows/targets/example_instance.json"
            target_path.parent.mkdir(parents=True)
            original = json.dumps(launcher.make_target(flow.platform, "example_instance", {"run_id": "fixture"}))
            target_path.write_text(original)
            commands = []
            with patch.object(flow, "execute", side_effect=lambda argv: commands.append(argv)):
                flow.initialize()
            self.assertEqual(Path(commands[0][1]).name, "prepare_install.py")
            self.assertEqual(commands[1][2], "install")
            self.assertEqual(target_path.read_text(), original)
            self.assertFalse((flow.root / "installation.json").exists())

    def test_interrupted_initialization_resumes_before_start(self):
        """安装与实例回归：interrupted initialization resumes before start。"""
        with tempfile.TemporaryDirectory() as tmp:
            flow = self.setup_flow(Path(tmp))
            with patch.object(flow, "execute"):
                flow.initialize()
            flow.root.mkdir(parents=True, exist_ok=True)
            (flow.root / "installation.json").write_text(json.dumps({"completed": [], "stages": []}))
            actions = []
            def execute(argv):
                action = argv[2]
                actions.append(action)
                if action == "install":
                    self.installed(flow)
                return '{"status":"already_configured"}'
            with patch.object(flow, "execute", side_effect=execute), \
                    patch.object(flow, "wait_ready", return_value="http://127.0.0.1:28199") as ready:
                flow.run()
            self.assertEqual(actions, ["install", "first-admin"])
            ready.assert_not_called()

    def test_other_instances_health_cannot_open_browser(self):
        flow = launcher.DefaultStartup(Path("/unused"))
        failed = {"status": "unhealthy"}
        with patch.object(installer, "wait_for_ready", return_value=failed) as wait, \
                patch.object(installer, "health_failure", return_value="/login: HTTP 500; web.log"):
            with self.assertRaisesRegex(RuntimeError, "/login: HTTP 500; web.log"):
                flow.wait_ready({"target": {}}, timeout=12)
        wait.assert_called_once_with({"target": {}}, timeout=12, progress=flow.progress)

    def test_existing_account_does_not_create_or_replace_an_invitation(self):
        """安装与实例回归：existing account does not create or replace an invitation。"""
        enrollment = Mock()
        enrollment.has_accounts.return_value = True
        with patch("management_portal.user_enrollment.UserEnrollment", return_value=enrollment), \
                patch("management_portal.portal_access_control.get_portal_access_store"), \
                patch("management_portal.portal_access_control.PortalCredentialStore"), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            portal_setup.first_admin(SimpleNamespace(if_needed=True))
        self.assertEqual(json.loads(output.getvalue()), {"status": "already_configured"})
        enrollment.create_invitation.assert_not_called()

    def test_installer_existing_account_keeps_private_file_untouched(self):
        """安装与实例回归：installer existing account keeps private file untouched。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            private = root / "secrets"
            private.mkdir()
            invitation = private / "first-admin-link.txt"
            invitation.write_text("prior private material")
            target = {"install_root": str(root), "ports": {"web": 28199},
                      "data_roots": {"secrets_dir": str(private)}}
            with patch.object(installer, "owned_state", return_value={"target": target}), \
                    patch.object(installer, "runtime_python", return_value=sys.executable), \
                    patch.object(installer, "code_root", return_value=root), \
                    patch.object(installer, "run_checked", return_value='{"status":"already_configured"}') as run:
                result = installer.first_admin(root, "admin", if_needed=True)
            self.assertEqual(result["status"], "already_configured")
            self.assertIn("--if-needed", run.call_args.args[0])
            self.assertEqual(invitation.read_text(), "prior private material")

class ReadinessWait(unittest.TestCase):
    state = {'target': {'service_prefix': 'expected', 'windows_service_mode': 'user'}}

    def snapshot(self, *, code=200, instance='expected', active=True):
        return {'status': 'installed' if code == 200 and instance == 'expected' and active else 'unhealthy',
                'url': 'http://127.0.0.1:28199',
                'services': [{'service': 'web', 'active': 'active' if active else 'inactive',
                              'pid': 123 if active else None}],
                'checks': [{'route': '/api/health', 'ok': instance == 'expected',
                            'payload': {'instance': instance}},
                           {'route': '/login', 'ok': code == 200, 'http_status': code}]}

    def run_wait(self, snapshots, timeout=120):
        clock = [0.0]
        def sleep(seconds):
            clock[0] += seconds
        progress = []
        with patch.object(installer, 'observed_status', side_effect=snapshots) as observed, \
                patch.object(installer.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(installer.time, 'sleep', side_effect=sleep):
            result = installer.wait_for_ready(self.state, timeout=timeout, progress=progress.append)
        self.assertTrue(all(0 < call.kwargs['request_timeout'] <= 3 for call in observed.call_args_list))
        return result, observed.call_count, clock[0], progress

    def test_ready_services_return_without_sleep(self):
        result, calls, elapsed, _ = self.run_wait([self.snapshot()])
        self.assertEqual((result['status'], calls, elapsed), ('installed', 1, 0))

    def test_persistent_login_error_stops_after_three_observations(self):
        result, calls, elapsed, progress = self.run_wait([self.snapshot(code=500)] * 3)
        self.assertEqual((result['status'], calls, elapsed), ('unhealthy', 3, 2))
        self.assertTrue(any('/login' in message for message in progress))

    def test_temporary_errors_and_service_unavailable_can_recover(self):
        for codes in ([500, 200], [500, 503, 500, 200], [503] * 5 + [200]):
            with self.subTest(codes=codes):
                result, calls, _, _ = self.run_wait([self.snapshot(code=c) for c in codes])
                self.assertEqual(result['status'], 'installed')
                self.assertEqual(calls, len(codes))

    def test_exited_services_and_other_instances_fail_immediately(self):
        for snapshot in (self.snapshot(active=False), self.snapshot(instance='other')):
            result, calls, elapsed, _ = self.run_wait([snapshot])
            self.assertEqual((result['status'], calls, elapsed), ('unhealthy', 1, 0))

    def test_retry_deadline_remains_bounded(self):
        result, calls, elapsed, _ = self.run_wait([self.snapshot(code=503)] * 121)
        self.assertEqual((result['status'], calls, elapsed), ('unhealthy', 121, 120))


if __name__ == "__main__":
    unittest.main()
