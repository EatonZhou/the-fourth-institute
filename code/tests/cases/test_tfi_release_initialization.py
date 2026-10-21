from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from scripts import tfi_release_install as installer
from scripts import tfi_release_initialization as initialization


class InitializationBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tfi-init-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = {"install_root": str(self.root), "run_id": "fixture", "supervisor_resident_id": "supervisor",
                       "python": sys.executable}
        self.state = {"target": self.target, "manifest_sha256": "a" * 64, "completed": ["database"]}
        self.manifest = {"feature_resolution": {"valid": True}}
        self.controller = Mock()
        self.controller.status.return_value = {"active": "inactive", "pid": 0}
        self.api = SimpleNamespace(read_json=installer.read_json, write_json=installer.write_json,
            COMPONENTS=installer.COMPONENTS, service_controller=lambda: self.controller,
            unit_name=lambda t, c: c, run_checked=Mock(), save_stage=Mock(), runtime_python=installer.runtime_python)
        self.evidence = self.root / "verified.json"
        installer.write_json(self.evidence, {"run_id": "fixture", "supervisor_resident_id": "supervisor",
            "manifest_sha256": "a" * 64, "passed": True, "violations": {"old_work": 0}})

    def test_foreign_or_failed_evidence_never_dispatches_helper(self):
        for changes in ({"manifest_sha256": "b" * 64}, {"run_id": "foreign"}, {"passed": False},
                        {"violations": {"old_work": 1}}):
            value = installer.read_json(self.evidence)
            installer.write_json(self.evidence, {**value, **changes})
            with self.assertRaises(ValueError):
                initialization.apply(self.api, self.state, self.manifest, freeze_evidence=self.evidence)
        self.api.run_checked.assert_not_called()
        self.assertFalse((self.root / "runtime").exists())

    def test_application_process_blocks_initialization(self):
        self.controller.status.return_value = {"active": "inactive", "pid": 123}
        with self.assertRaisesRegex(ValueError, "services stopped"):
            initialization.apply(self.api, self.state, self.manifest, freeze_evidence=self.evidence)
        self.api.run_checked.assert_not_called()

    def test_interrupted_initialization_cannot_be_replayed(self):
        self.api.run_checked.side_effect = RuntimeError("child failed after a file write")
        with self.assertRaises(RuntimeError):
            initialization.apply(self.api, self.state, self.manifest, freeze_evidence=self.evidence)
        with self.assertRaisesRegex(ValueError, "interrupted"):
            initialization.apply(self.api, self.state, self.manifest, freeze_evidence=self.evidence)
        self.assertEqual(self.api.run_checked.call_count, 1)


@unittest.skipUnless(os.environ.get("TFI_UPGRADE_TEST_PG_BIN"), "explicit independent PG binaries required")
class IndependentInitializationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="tfi-defaults-")
        cls.root = Path(cls.temp.name)
        cls.pg = Path(os.environ["TFI_UPGRADE_TEST_PG_BIN"])
        cls.env = {"PATH": "/usr/bin:/bin", "HOME": str(cls.root), "LANG": "C.UTF-8",
                   "LD_LIBRARY_PATH": os.environ.get("TFI_UPGRADE_TEST_PG_LIB", "")}
        (cls.root / "runtime/s").mkdir(parents=True)
        source = Path(installer.__file__).resolve().parents[1]
        code = cls.root / "code"
        shutil.copytree(source / "management_common", code / "management_common", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(source / "management_portal/execution", code / "management_portal/execution", ignore=shutil.ignore_patterns("__pycache__"))
        for relative in ["scripts/tfi_release_install.py", "scripts/tfi_release_initialization.py",
                         "scripts/tfi_release_platform_linux.py", "scripts/portal_source_layout.py", "management_portal/__init__.py",
                         "management_portal/distribution/__init__.py", "management_portal/distribution/initialization.py",
                         "management_portal/execution/__init__.py", "management_portal/execution/service_settings.py"]:
            destination = code / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / relative, destination)
        subprocess.run([str(cls.pg / "initdb"), "-D", str(cls.root / "runtime/pg"), "-U", "tfi", "-A", "trust",
                        "--no-locale", "--encoding=UTF8"], env=cls.env, capture_output=True, check=True)
        subprocess.run([str(cls.pg / "pg_ctl"), "-D", str(cls.root / "runtime/pg"), "-l", str(cls.root / "pg.log"),
                        "-o", f"-k {cls.root}/runtime/s -p 56434 -c listen_addresses=''", "-w", "start"],
                       env=cls.env, capture_output=True, check=True)

    @classmethod
    def tearDownClass(cls):
        subprocess.run([str(cls.pg / "pg_ctl"), "-D", str(cls.root / "runtime/pg"), "-m", "fast", "-w", "stop"],
                       env=cls.env, capture_output=True, check=True)
        cls.temp.cleanup()

    def test_real_child_applies_on_off_and_preserves_upgrade_choices(self):
        import psycopg
        roots = {k: str(self.root / "data" / k) for k in installer.KEYS}
        target = {"install_root": str(self.root), "run_id": "independent-component-check",
                  "supervisor_resident_id": "test-supervisor", "service_prefix": "tfi-defaults-test",
                  "ports": {"postgres": 56434, "nats": 14224}, "database_name": "postgres", "python": sys.executable,
                  "pg_bin": str(self.pg), "data_roots": roots}
        installer.write_json(self.root / "config/paths.json", roots)
        installer.write_json(self.root / "config/site.json", {})
        controller = Mock()
        controller.status.return_value = {"active": "inactive", "pid": 0}
        def run_child(*args, **kwargs):
            try:
                return installer.run_checked(*args, **kwargs)
            except RuntimeError as exc:
                self.fail(str(exc) + "\n" + (self.root / "runtime/command-error.log").read_text())
        api = SimpleNamespace(read_json=installer.read_json, write_json=installer.write_json,
            COMPONENTS=installer.COMPONENTS, service_controller=lambda: controller,
            unit_name=lambda t, c: c, run_checked=run_child, save_stage=installer.save_stage)
        with psycopg.connect(installer.clean_environment(target)["PORTAL_DATABASE_URL"], autocommit=True) as c:
            for enabled in (False, True):
                c.execute("DROP SCHEMA IF EXISTS execution_runtime CASCADE")
                c.execute("DROP SCHEMA IF EXISTS distribution_installation CASCADE")
                c.execute("DROP SCHEMA IF EXISTS lineage CASCADE")
                shutil.rmtree(self.root / "data", ignore_errors=True)
                actions = [{"id": "model_request_timing", "adapter": "execution_setting",
                            "field": "model_request_timing_enabled", "effective_enabled": enabled, "missing_requirements": []},
                           {"id": "resident_semantic_search", "adapter": "embedding_setting",
                            "effective_enabled": enabled, "missing_requirements": []}]
                manifest = {"feature_resolution": {"valid": True, "initialization": {"actions": actions}}}
                path = self.root / "runtime/engineering-manifest.json"
                installer.write_json(path, manifest)
                state = {"target": target, "manifest_sha256": installer.digest(path), "completed": ["database"]}
                evidence = self.root / "verified.json"
                installer.write_json(evidence, {"run_id": target["run_id"], "supervisor_resident_id": target["supervisor_resident_id"],
                    "manifest_sha256": state["manifest_sha256"], "passed": True, "violations": {"old_work": 0}})
                result = initialization.apply(api, state, manifest, freeze_evidence=evidence)
                self.assertFalse(result["work_dispatched"])
                self.assertIs(c.execute("SELECT provider_timing_proxy_enabled FROM execution_runtime.service_settings").fetchone()[0], enabled)
                settings = Path(roots["instances_root"]) / "settings/resident_embeddings.json"
                self.assertEqual(json.loads(settings.read_text()), {"enabled": enabled})
                # Real local choices, opposite to release defaults, survive an upgrade.
                c.execute("UPDATE execution_runtime.service_settings SET provider_timing_proxy_enabled=%s", (not enabled,))
                installer.write_json(settings, {"enabled": not enabled})
                # An existing local timer omitted from new actions must remain owned.
                c.execute("CREATE SCHEMA lineage")
                c.execute("CREATE TABLE lineage.lineage_components(node_id text, config_json text)")
                c.execute("CREATE TABLE lineage.event_source_progress(lineage_id text,source_id text,checkpoint_json text)")
                c.execute("INSERT INTO lineage.lineage_components VALUES('local-timer',%s)", (json.dumps({"event_type": "time.reached"}),))
                c.execute("INSERT INTO lineage.event_source_progress VALUES('local-lineage','local-timer',%s)", (json.dumps({"paused": False}),))
                manifest["version"] = "upgrade"
                installer.write_json(path, manifest)
                state["manifest_sha256"] = installer.digest(path)
                result = initialization.apply(api, state, manifest, mode="upgrade",
                    existing_feature_ids=["model_request_timing", "resident_semantic_search"])
                self.assertTrue(all(f["status"] == "preserved_existing" for f in result["features"]))
                self.assertIs(c.execute("SELECT provider_timing_proxy_enabled FROM execution_runtime.service_settings").fetchone()[0], not enabled)
                self.assertEqual(json.loads(settings.read_text()), {"enabled": not enabled})
                self.assertTrue(result["lineage_timer_required"])
                self.assertEqual(installer.clean_environment(target)["PORTAL_LINEAGE_TIMER_INSTANCE"], target["service_prefix"])
