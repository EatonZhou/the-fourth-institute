"""发行安装与实例隔离回归。

:module: operations.deployment
:design: decision_d88306d0bd17
:profiles: local.fixture
:risk: no-external-side-effects"""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack
import sys

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "code"))
from scripts import tfi_release_install as installer
from scripts.tfi_instance_layout import code_root, database_root, dependencies_root, installer_path, validate_shared_code


class SharedInstanceTests(unittest.TestCase):
    def test_retry_after_database_import_preserves_completed_instance_data(self):
        """数据库导入后的启动失败重试保留已有数据，不再次复制或导入示例。"""
        from scripts import tfi_release_native_dependencies as native

        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            root = Path(temporary) / "instance"
            root.mkdir()
            manifest_path = Path(temporary) / "manifest.json"
            manifest_path.write_text("{}", encoding="utf-8")
            target = self.target(root)
            state = {"schema": installer.SCHEMA, "target": target,
                     "manifest_sha256": installer.digest(manifest_path),
                     "protected_roots": [], "completed": ["prepare", "database"],
                     "stages": [{"phase": "start", "status": "failed"}]}
            installer.write_json(root / installer.STATE_FILE, state)
            stack.enter_context(patch.object(native, "plan", return_value=(target, [])))
            stack.enter_context(patch.object(installer, "owned_state", return_value=state))
            stack.enter_context(patch.object(installer, "target_plan", return_value=target))
            stack.enter_context(patch.object(installer, "validate_engineering", return_value=({}, root)))
            stack.enter_context(patch.object(installer, "platform_preflight", return_value={}))
            stack.enter_context(patch.object(installer, "dependency_preflight", return_value={}))
            for name in ("check_available", "prepare_dependencies", "initialize_database",
                         "initialize_features", "service_controller"):
                stack.enter_context(patch.object(installer, name))
            stack.enter_context(patch.object(installer, "register_services", return_value=[]))
            stack.enter_context(patch.object(installer, "observed_status", return_value={"status": "installed"}))
            importer = stack.enter_context(patch.object(installer, "import_database"))
            copier = stack.enter_context(patch.object(installer, "copy_engineering"))
            self.assertEqual(installer.install(manifest_path, target)["status"], "installed")
            importer.assert_not_called()
            copier.assert_not_called()
            restored = installer.read_json(root / installer.STATE_FILE)
            self.assertEqual(restored["completed"], ["prepare", "database"])
            self.assertEqual(restored["stages"][-1]["status"], "installed")

    def target(self, root):
        return dict(install_root=str(root), shared_code_root=str(REPO / "code"),
                    service_prefix="tfi-layout-test", database_name="original_database",
                    database_user="existing_site_user", runtime_instance="release", run_id="layout-test",
                    python=sys.executable, pg_bin="/usr/bin", nats_server="/usr/bin/true",
                    bind="0.0.0.0", ports=dict(web=28399, postgres=56799, nats=14599),
                    data_roots={k: str(root / "data" / n) for k, n in zip(
                        installer.KEYS, ("share", "staging", "instances", "accounts", "secrets"))})

    def test_two_instances_share_only_code(self):
        """安装与实例回归：two instances share only code。"""
        with tempfile.TemporaryDirectory() as temporary:
            one = self.target(Path(temporary) / "one")
            two = self.target(Path(temporary) / "two")
            two["ports"] = dict(web=28499, postgres=56899, nats=14699)
            two["service_prefix"] = "tfi-layout-second"
            for target in (one, two):
                validate_shared_code(target)
                env = installer.clean_environment(target)
                self.assertEqual(env["PORTAL_INSTANCE"], "release")
                self.assertEqual(env["TMPDIR"], str(Path(target["install_root"]) / "runtime"))
                self.assertEqual(env["XDG_CACHE_HOME"], str(Path(target["install_root"]) / "home/.cache"))
                self.assertEqual(env["PORTAL_EXECUTION_WORKER_SOURCE_ROOT"], str(REPO / "code"))
                self.assertTrue(env["PORTAL_DATABASE_URL"].startswith("postgresql://existing_site_user@/"))
                self.assertIn(str(installer_path(target)), installer.unit_text(target, "web"))
                self.assertNotIn("/runtime/installer.py", installer.unit_text(target, "web"))
                command, _ = installer.component_command(target, "postgres")
                self.assertEqual(command[command.index("-D") + 1], str(database_root(target)))
            self.assertEqual(code_root(one), code_root(two))
            self.assertNotEqual(installer.clean_environment(one)["PORTAL_DATABASE_URL"],
                                installer.clean_environment(two)["PORTAL_DATABASE_URL"])
            self.assertNotEqual(database_root(one), database_root(two))

    def test_legacy_layout_is_preserved(self):
        """安装与实例回归：legacy layout is preserved。"""
        target = {"install_root": "/tmp/legacy"}
        self.assertEqual(code_root(target), Path("/tmp/legacy/code"))
        self.assertEqual(database_root(target), Path("/tmp/legacy/runtime/pg"))
        self.assertEqual(installer_path(target), Path("/tmp/legacy/runtime/installer.py"))

    def test_shared_code_cannot_overlap_instance(self):
        """安装与实例回归：shared code cannot overlap instance。"""
        with tempfile.TemporaryDirectory() as temporary:
            target = self.target(Path(temporary))
            target["shared_code_root"] = temporary
            with self.assertRaises(ValueError):
                validate_shared_code(target)

    def test_native_dependencies_are_shared_without_sharing_database(self):
        """安装与实例回归：native dependencies are shared without sharing database。"""
        from scripts.tfi_release_native_dependencies import plan
        from scripts.tfi_release_dependencies import environment_key
        with tempfile.TemporaryDirectory() as temporary:
            one = self.target(Path(temporary) / "one")
            two = self.target(Path(temporary) / "two")
            for target in (one, two):
                target.pop("pg_bin")
                target.pop("nats_server")
            one, entries_one = plan(one, {}, host={"platform": "linux", "architecture": "x86_64"})
            two, entries_two = plan(two, {}, host={"platform": "linux", "architecture": "x86_64"})
            self.assertEqual(entries_one, entries_two)
            self.assertEqual(dependencies_root(one), dependencies_root(two))
            self.assertEqual(environment_key(one, ["core"]), environment_key(two, ["core"]))
            self.assertNotEqual(environment_key(one, ["core"]), environment_key(one, ["core", "article"]))
            self.assertNotEqual(database_root(one), database_root(two))

    def test_start_does_not_import_seed_and_refuses_unfinished_migration(self):
        """安装与实例回归：start does not import seed and refuses unfinished migration。"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "instance"
            state = {"target": self.target(root), "completed": ["database"],
                     "stages": [{"status": "installed"}]}
            with patch.object(installer, "owned_state", return_value=state), \
                    patch("scripts.tfi_release_provider_services.owned_services", return_value={"names": [], "daemons": [], "units": [], "template": None}), \
                    patch("scripts.tfi_release_provider_services._run"), \
                    patch.object(installer, "check_available"), \
                    patch.object(installer, "register_services"), \
                    patch.object(installer, "wait_database"), \
                    patch.object(installer, "import_database") as importer, \
                    patch.object(installer, "service_controller") as control:
                self.assertEqual(installer.lifecycle("start", root)["status"], "started")
                importer.assert_not_called()
                self.assertEqual(control.return_value.start.call_count, len(installer.COMPONENTS))
                state["migration"] = {"business_execution_allowed": False}
                with self.assertRaisesRegex(ValueError, "cutover"):
                    installer.lifecycle("start", root)

    def test_copy_verifies_shared_code_and_does_not_overwrite_it(self):
        """安装与实例回归：copy verifies shared code and does not overwrite it。"""
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            target = self.target(base / "instance")
            shared = base / "shared"
            shared.mkdir()
            (shared / "application.py").write_text("new content")
            target["shared_code_root"] = str(shared)
            bundle = base / "bundle"
            (bundle / "code").mkdir(parents=True)
            (bundle / "code/application.py").write_text("old content")
            with self.assertRaisesRegex(ValueError, "differs"):
                installer.copy_engineering({"code": {"path": "code"}, "directory_mappings": [],
                                            "files": {}, "inventory": []},
                                           bundle, target)
            self.assertEqual((shared / "application.py").read_text(), "new content")
            self.assertFalse((base / "instance/code").exists())

    def test_shared_source_integrity_excludes_only_generated_dependencies(self):
        """安装与实例回归：shared source integrity excludes only generated dependencies。"""
        from scripts.tfi_instance_layout import source_inventory
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "application.py").write_text("application")
            expected = source_inventory(root)
            (root / "node_modules/library").mkdir(parents=True)
            (root / "node_modules/library/file.js").write_text("installed dependency")
            (root / "__pycache__").mkdir()
            (root / "__pycache__/application.pyc").write_bytes(b"cache")
            self.assertEqual(source_inventory(root), expected)
            (root / "application.py").write_text("changed application")
            self.assertNotEqual(source_inventory(root), expected)

    def test_cleanup_preserves_seed_and_shared_code(self):
        """安装与实例回归：cleanup preserves seed and shared code。"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "instance"
            (root / "seed").mkdir(parents=True)
            (root / "seed/manifest.json").write_text("seed")
            (root / "data").mkdir()
            (root / "data/private").write_text("private")
            target = self.target(root)
            state = {"target": target}
            with patch.object(installer, "owned_state", return_value=state), \
                    patch("scripts.tfi_release_provider_services.owned_services", return_value={"names": [], "daemons": [], "units": [], "template": None}), \
                    patch("scripts.tfi_release_provider_services._run"), \
                    patch.object(installer, "check_available"), \
                    patch.object(installer, "service_controller") as control, \
                    patch.object(installer, "platform_adapter") as adapter:
                control.return_value.status.return_value = {"load": "not-found", "active": "inactive", "pid": 0}
                installer.lifecycle("cleanup", root)
            self.assertEqual((root / "seed/manifest.json").read_text(), "seed")
            self.assertFalse((root / "data").exists())
            self.assertTrue((REPO / "code/scripts/tfi_release_install.py").exists())

    def test_provider_service_recognizes_shared_source_and_private_roots(self):
        """安装与实例回归：provider service recognizes shared source and private roots。"""
        from scripts import portal_service_units as units
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "instance"
            (root / "config").mkdir(parents=True)
            config = root / "config/paths.json"
            config.write_text("{}")
            target = self.target(root)
            (root / "installation.json").write_text(json.dumps({"target": target}))
            with patch.dict("os.environ", {"PORTAL_PATHS_CONFIG": str(config)}), \
                    patch("management_common.portal_paths.path",
                          side_effect=lambda key: Path(target["data_roots"][key])):
                result = units._isolated_codex_context()
                self.assertEqual(result["root"], root)
                self.assertEqual(result["prefix"], "tfi-layout-test-codex-app-server@")
                with patch.object(units, "SOURCE_ROOT", Path(temporary) / "other"):
                    with self.assertRaisesRegex(ValueError, "ownership"):
                        units._isolated_codex_context()

    def test_build_updates_code_without_changing_seed_or_instance_data(self):
        """安装与实例回归：build updates code without changing seed or instance data。"""
        spec = importlib.util.spec_from_file_location("prepare_install", REPO / "prepare_install.py")
        prepare = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(prepare)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed = root / "instance/example_instance/seed"
            (seed / "database").mkdir(parents=True)
            (seed / "database/distribution.sql").write_text("SELECT 1;\n")
            files = {key: "data/" + key for key in installer.KEYS}
            for path in files.values():
                (seed / path).mkdir(parents=True)
            metadata = {"files": files, "database": {"path": "database/distribution.sql",
                        "format": "postgresql-sql", "sha256": prepare.digest(seed / "database/distribution.sql")},
                        "run_id": "test", "supervisor_resident_id": ""}
            record = {"schema": "tfi.instance-seed.v1", "engineering": metadata,
                      "inventory": prepare.inventory(seed)}
            manifest = seed / "manifest.json"
            manifest.write_text(json.dumps(record))
            initial_seed = manifest.read_bytes()
            (root / "code/scripts").mkdir(parents=True)
            (root / "code/scripts/tfi_release_install.py").write_text("# installer fixture")
            (root / "code/main.py").write_text("old")
            (root / "INSTALL.md").write_text("Install")
            private = root / "instance/private/data"
            private.mkdir(parents=True)
            (private / "secret").write_text("private")
            bundle = prepare.prepare(root)
            old_hash = json.loads((bundle / "manifest.json").read_text())["code"]["sha256"]
            (root / "code/main.py").write_text("new")
            prepare.prepare(root)
            new_hash = json.loads((bundle / "manifest.json").read_text())["code"]["sha256"]
            self.assertNotEqual(old_hash, new_hash)
            self.assertEqual(initial_seed, manifest.read_bytes())
            self.assertEqual((private / "secret").read_text(), "private")
            self.assertFalse(any(path.name == "secret" and path.is_file() for path in bundle.rglob("*")))
            valid_bundle = (bundle / "manifest.json").read_bytes()
            (root / "code/scripts/tfi_release_install.py").unlink()
            with self.assertRaisesRegex(ValueError, "installer entrypoint"):
                prepare.prepare(root)
            self.assertEqual((bundle / "manifest.json").read_bytes(), valid_bundle)
            (seed / "database/distribution.sql").write_text("tampered")
            with self.assertRaisesRegex(ValueError, "differs"):
                prepare.prepare(root)


if __name__ == "__main__":
    unittest.main()
