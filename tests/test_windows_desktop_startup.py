"""Desktop users go directly from setup to the default Portal web page."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
from scripts import tfi_windows_launcher as launcher
from scripts import portal_setup
from scripts import tfi_release_install as installer


class DesktopStartup(unittest.TestCase):
    def setup_flow(self, platform):
        flow = launcher.DefaultStartup(platform)
        bundle = platform / ".build/example_instance-windows/engineering/manifest.json"
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
        with tempfile.TemporaryDirectory() as tmp:
            flow = self.setup_flow(Path(tmp))
            actions = []
            accounts_exist = False
            def execute(argv):
                nonlocal accounts_exist
                action = argv[2]
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
                    patch.object(flow, "wait_ready", return_value="http://127.0.0.1:28199"):
                self.assertEqual(flow.run(), "http://127.0.0.1:28199/join#invite=test-token")
                self.assertEqual(actions, ["install", "start", "first-admin"])
                before = (flow.root / "installation.json").read_bytes()
                accounts_exist = True
                actions.clear()
                self.assertEqual(flow.run(), "http://127.0.0.1:28199/")
                self.assertEqual(actions, ["start", "first-admin"])
                self.assertEqual(before, (flow.root / "installation.json").read_bytes())

    def test_failed_install_never_attempts_start_or_opens_missing_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            flow = self.setup_flow(Path(tmp))
            with patch.object(flow, "execute", side_effect=RuntimeError("download failed")) as execute, \
                    patch.object(flow, "wait_ready") as ready:
                with self.assertRaisesRegex(RuntimeError, "download failed"):
                    flow.run()
                self.assertEqual(execute.call_count, 1)
                self.assertEqual(execute.call_args.args[0][2], "install")
                ready.assert_not_called()

    def test_interrupted_initialization_resumes_before_start(self):
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
                    patch.object(flow, "wait_ready", return_value="http://127.0.0.1:28199"):
                flow.run()
            self.assertEqual(actions, ["install", "start", "first-admin"])

    def test_other_instances_health_cannot_open_browser(self):
        flow = launcher.DefaultStartup(Path("/unused"))
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps({"ok": True, "instance": "other",
            "execution_layer": {"ok": True}, "society_service": {"ok": True}}).encode()
        state = {"target": {"ports": {"web": 28199}, "service_prefix": "expected"}}
        with patch.object(launcher, "urlopen", return_value=response), \
                patch.object(launcher.time, "monotonic", side_effect=[0, 0, 2]), \
                patch.object(launcher.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "未能就绪"):
                flow.wait_ready(state, timeout=1)

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

    def test_installer_existing_account_keeps_private_file_untouched(self):
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


if __name__ == "__main__":
    unittest.main()
