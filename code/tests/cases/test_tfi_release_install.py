from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch

from scripts import tfi_release_install as installer


class InstallationIsolationTests(unittest.TestCase):
    def test_initialization_helper_keeps_service_transport_but_not_source_home(self):
        from scripts import tfi_release_platform_linux
        target = installer.target_plan(self.target)
        helper = Path(target['install_root']) / 'code/scripts/tfi_release_initialization.py'
        transport = {'XDG_RUNTIME_DIR': '/run/user/1000',
                     'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/run/user/1000/bus'}
        with patch.dict(os.environ, {**transport, 'UNRELATED_SECRET': 'private'}, clear=True), \
                patch.object(installer, 'platform_adapter', return_value=tfi_release_platform_linux):
            environment = installer.command_environment(target, [target['python'], str(helper), '--verify-frozen'])
            for key, value in transport.items():
                self.assertEqual(environment[key], value)
            self.assertEqual(environment['HOME'], str(Path(target['install_root']) / 'home'))
            self.assertNotIn('UNRELATED_SECRET', environment)
            unrelated = installer.command_environment(target, [target['python'], '-c', 'pass'])
            for key in transport:
                self.assertNotIn(key, unrelated)

    def test_internal_script_callbacks_use_the_selected_instance_port(self):
        target = installer.target_plan(self.target)
        with patch.dict(os.environ, {"PORTAL_URL": "http://old-site:18086"}):
            for port in (28399, 28499):
                target["ports"]["web"] = port
                _, environment = installer.component_command(target, "society")
                self.assertEqual(environment["PORTAL_URL"], f"http://127.0.0.1:{port}")

    def test_published_generation_identifies_every_application_component(self):
        target = installer.target_plan(self.target)
        generation = Path(target["install_root"]) / "config/generation.env"
        generation.parent.mkdir(parents=True)
        self.assertEqual(installer.clean_environment(target)["PORTAL_BUILD_ID"], target["run_id"])
        generation.write_text("PORTAL_BUILD_ID=published-commit\nUNRELATED_SECRET=ignored\n", encoding="utf-8")
        for component in installer.COMPONENTS:
            _, environment = installer.component_command(target, component)
            self.assertEqual(environment["PORTAL_BUILD_ID"], "published-commit")
            self.assertNotIn("UNRELATED_SECRET", environment)

    def test_linux_components_preserve_user_manager_transport_only(self):
        from scripts import tfi_release_platform_linux

        target = installer.target_plan(self.target)
        operator = {"XDG_RUNTIME_DIR": "/run/user/1000",
                    "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
                    "HOME": "/operator-home", "UNRELATED_SECRET": "not-inherited"}
        with patch.dict(os.environ, operator, clear=True), \
             patch.object(installer, "platform_adapter", return_value=tfi_release_platform_linux):
            for component in installer.COMPONENTS:
                with self.subTest(component=component):
                    _, environment = installer.component_command(target, component)
                    for key in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"):
                        self.assertEqual(environment[key], operator[key])
                    self.assertEqual(environment["HOME"], str(Path(target["install_root"]) / "home"))
                    self.assertNotIn("UNRELATED_SECRET", environment)

    def test_maintenance_reexec_preserves_operator_service_context_only(self):
        target = installer.target_plan(self.target)
        operator = {"HOME": str(self.base / "operator"),
                    "XDG_RUNTIME_DIR": str(self.base / "user-runtime"),
                    "DBUS_SESSION_BUS_ADDRESS": "unix:path=/test-user-bus",
                    "PORTAL_DATABASE_URL": "postgresql://source-must-not-be-used",
                    "UNRELATED_SECRET": "must-not-be-carried"}
        for command in ("upgrade", "recover"):
            with self.subTest(command=command), patch.dict(os.environ, operator, clear=True), \
                 patch.object(installer, "owned_state", return_value={"target": target}), \
                 patch.object(installer, "runtime_python", return_value="/test-instance/venv/bin/python"), \
                 patch.object(installer.os, "execve", side_effect=SystemExit) as execute:
                args = [command, "--root", target["install_root"]]
                with self.assertRaises(SystemExit):
                    installer.main(args)
                program, argv, environment = execute.call_args.args
                self.assertEqual(program, "/test-instance/venv/bin/python")
                self.assertEqual(argv[2:], args)
                for key in ("HOME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"):
                    self.assertEqual(environment[key], operator[key])
                self.assertNotIn("XDG_CONFIG_HOME", environment)
                self.assertNotIn("UNRELATED_SECRET", environment)
                clean = installer.clean_environment(target)
                self.assertEqual(environment["PORTAL_DATABASE_URL"], clean["PORTAL_DATABASE_URL"])
                self.assertNotEqual(environment["PORTAL_DATABASE_URL"], operator["PORTAL_DATABASE_URL"])
                self.assertEqual(clean["HOME"], str(Path(target["install_root"]) / "home"))
                self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", clean)

    def test_skipped_verification_rejects_code_links_without_reading_target(self):
        bundle = self.bundle()
        outside = self.base / 'outside.txt'
        outside.write_text('outside-copy-boundary')
        (bundle / 'code/nested').mkdir()
        (bundle / 'code/nested/link').symlink_to(outside)
        manifest, _ = installer.validate_engineering(bundle / 'manifest.json', self.target,
                                                     skip_file_verification=True)
        root = Path(self.target['install_root'])
        (root / 'runtime').mkdir(parents=True)
        with patch.object(installer, 'digest', side_effect=AssertionError('unexpected hash')):
            with self.assertRaisesRegex((ValueError, shutil.Error), 'unsafe entry'):
                installer.copy_engineering(manifest, bundle, self.target)
        self.assertFalse((root / 'code/nested/link').exists())
        self.assertEqual(outside.read_text(), 'outside-copy-boundary')

    def test_new_install_uses_own_browser_cookie_namespace(self):
        target = installer.target_plan(self.target)
        installer.prepare_configuration(target)
        site = installer.read_json(Path(target['install_root']) / 'config/site.json')
        self.assertEqual(site['deployment']['auth']['session_cookie_name'],
                         'db_session_' + target['service_prefix'])

    def test_skip_file_verification_avoids_walk_and_hash_but_retains_identity(self):
        bundle = self.bundle()
        target = installer.target_plan(self.target)
        (bundle / "code/scripts/install_tfi_release").write_text("updated code")
        with patch.object(installer.os, "walk", side_effect=AssertionError("scan")), \
             patch.object(installer, "digest", side_effect=AssertionError("hash")):
            installer.validate_engineering(bundle / "manifest.json", target, skip_file_verification=True)
            with self.assertRaisesRegex(ValueError, "run_id"):
                installer.validate_engineering(bundle / "manifest.json", {**target, "run_id": "other"}, skip_file_verification=True)

    def test_deferred_data_checks_do_not_hash_or_walk_data_but_still_check_code(self):
        bundle = self.bundle()
        data = bundle / "data/share_root/unverified.txt"
        data.write_text("retained workspace")
        target = installer.target_plan(self.target)
        with self.assertRaisesRegex(ValueError, "inventory"):
            installer.validate_engineering(bundle / "manifest.json", target)
        original_digest = installer.digest
        def core_digest(path):
            self.assertFalse(Path(path).is_relative_to(bundle / "data"))
            return original_digest(path)
        with patch.object(installer, "digest", side_effect=core_digest):
            installer.validate_engineering(bundle / "manifest.json", target, defer_data_verification=True)
        (bundle / "code/scripts/install_tfi_release").write_text("changed code")
        with self.assertRaisesRegex(ValueError, "checksum"):
            installer.validate_engineering(bundle / "manifest.json", target, defer_data_verification=True)

    def test_deferred_data_still_rejects_credentials_and_copy_links(self):
        bundle = self.bundle()
        target = installer.target_plan(self.target)
        credential = bundle / "data/secrets_dir/credential"
        credential.write_text("test")
        with self.assertRaisesRegex(ValueError, "secret"):
            installer.validate_engineering(bundle / "manifest.json", target, defer_data_verification=True)
        credential.unlink()
        (bundle / "data/share_root/link").symlink_to(bundle / "database/distribution.sql")
        manifest, _ = installer.validate_engineering(bundle / "manifest.json", target, defer_data_verification=True)
        with self.assertRaisesRegex((ValueError, shutil.Error), "unsafe entry"):
            installer.copy_engineering(manifest, bundle, target)

    def test_timer_activation_requires_completed_matching_initialization(self):
        target = installer.target_plan(self.target)
        path = Path(target["install_root"]) / installer.STATE_FILE
        state = {"target": target, "manifest_sha256": "a" * 64,
                 "feature_initialization": {"status": "applying", "manifest_sha256": "a" * 64,
                                            "result": {"lineage_timer_required": True}}}
        installer.write_json(path, state)
        self.assertEqual(installer.clean_environment(target)["PORTAL_LINEAGE_TIMER_INSTANCE"], "disabled-until-configured")
        state["feature_initialization"]["status"] = "applied"
        installer.write_json(path, state)
        self.assertEqual(installer.clean_environment(target)["PORTAL_LINEAGE_TIMER_INSTANCE"], target["service_prefix"])
        state["manifest_sha256"] = "b" * 64
        installer.write_json(path, state)
        self.assertEqual(installer.clean_environment(target)["PORTAL_LINEAGE_TIMER_INSTANCE"], "disabled-until-configured")

    def test_adapter_selection_uses_actual_host_without_legacy_manifest(self):
        with patch("management_common.distribution_targets.identify_host", return_value={"platform": "macos"}), \
             patch.object(installer, "platform_preflight", side_effect=AssertionError("fabricated manifest")), \
             patch.object(installer.importlib, "import_module") as native_import:
            installer.platform_adapter()
        native_import.assert_called_once_with("scripts.tfi_release_platform_macos")

    def test_legacy_preflight_preserves_snapshot_and_records_actual_host(self):
        manifest = {"selection_snapshot": {"history": True}}
        original = copy.deepcopy(manifest)
        report = installer.platform_preflight(manifest)
        self.assertEqual(manifest, original)
        self.assertEqual(report["target_resolution"], "legacy-effective-linux")
        self.assertEqual(report["selected_targets"], ["linux"])
        self.assertTrue(report["host"]["architecture"])
        self.assertTrue(report["host"]["os_version"])

    def test_unknown_architecture_has_structured_preflight_failure(self):
        host = {"platform": "linux", "architecture": "unclaimed-arch", "os_version": "test"}
        with patch("management_common.distribution_targets.identify_host", return_value=host), \
             self.assertRaises(installer.PlatformPreflightError) as failure:
            installer.platform_preflight({})
        self.assertEqual(failure.exception.report["host"], host)
        self.assertFalse(failure.exception.report["ok"])

    def test_platform_rejection_precedes_native_import_and_filesystem_effects(self):
        bundle = self.bundle()
        host = {"platform": "windows", "architecture": "x86_64", "os_version": "test"}
        with patch("management_common.distribution_targets.identify_host", return_value=host), \
             patch.object(installer, "platform_adapter", side_effect=AssertionError("native import before preflight")), \
             self.assertRaisesRegex(ValueError, "平台"):
            installer.install(bundle / "manifest.json", self.target)
        self.assertFalse(Path(self.target["install_root"]).exists())

    def test_declared_capability_gap_prevents_installation(self):
        bundle = self.bundle()
        manifest = installer.read_json(bundle / "manifest.json")
        manifest["selected_targets"] = ["linux"]
        manifest["support_matrix"] = [{"platform": "linux", "capability": "local_ipc",
                                       "implementation": "incomplete"}]
        installer.write_json(bundle / "manifest.json", manifest)
        with patch.object(installer, "platform_adapter", side_effect=AssertionError("native import before preflight")), \
             self.assertRaises(ValueError):
            installer.install(bundle / "manifest.json", self.target)
        self.assertFalse(Path(self.target["install_root"]).exists())

    def test_broken_dependency_prevents_installation_root_creation(self):
        bundle = self.bundle()
        with patch.object(installer, "check_available"), self.assertRaisesRegex(ValueError, "Dependency preflight"):
            installer.install(bundle / "manifest.json", self.target)
        self.assertFalse(Path(self.target["install_root"]).exists())

    def test_help_import_does_not_require_posix_lock_or_service_modules(self):
        child = """
import builtins, runpy, sys
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name in {'fcntl', 'scripts.portal_service_control'}:
        raise ImportError('platform module must remain lazy: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
sys.argv = ['install_tfi_release', '--help']
runpy.run_module('scripts.tfi_release_install', run_name='__main__')
"""
        result = subprocess.run([sys.executable, "-c", child], cwd=installer.SOURCE_ROOT,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--prepare-only", result.stdout)

    def test_installation_lock_excludes_another_process_and_releases(self):
        root = self.base / "locked"
        child = (
            "import sys; from pathlib import Path; "
            "from management_common.file_lock import try_lock_descriptor; "
            "f=(Path(sys.argv[1])/'.install.lock').open('a'); "
            "sys.exit(0 if try_lock_descriptor(f.fileno()) else 3)"
        )
        command = [sys.executable, "-c", child, str(root)]
        with installer.installation_lock(root):
            self.assertEqual(subprocess.run(command, cwd=installer.SOURCE_ROOT).returncode, 3)
        self.assertEqual(subprocess.run(command, cwd=installer.SOURCE_ROOT).returncode, 0)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="tfi-install-test-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        tools = self.base / "tools"
        tools.mkdir()
        for name in ("python", "nats-server", "initdb", "postgres", "pg_isready", "psql", "createdb", "pg_restore"):
            executable = tools / name
            executable.touch(mode=0o700)
        (tools / "postgres.bki").touch()
        root = self.base / "instance"
        self.target = {"install_root": str(root), "run_id": "release-run-001",
                       "service_prefix": "tfi-release-001", "database_name": "tfi_release_001",
                       "supervisor_resident_id": "resident_e4f55ad12dae",
                       "data_roots": {key: str(root / "data" / key) for key in installer.KEYS},
                       "ports": {"web": 28086, "postgres": 56432, "nats": 14223},
                       "python": str(tools / "python"), "pg_bin": str(tools),
                       "pg_share": str(tools), "nats_server": str(tools / "nats-server")}

    def test_plan_is_read_only_and_environment_drops_source_credentials(self):
        with patch.dict(os.environ, {"PORTAL_DATABASE_URL": "source-private-db", "OPENAI_API_KEY": "source-secret", "CODEX_HOME": "/source/accounts"}):
            plan = installer.target_plan(self.target)
            env = installer.clean_environment(plan)
        self.assertFalse(Path(plan["install_root"]).exists())
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("CODEX_HOME", env)
        self.assertNotIn("source-private-db", env.values())
        self.assertEqual(env["PORTAL_PATHS_CONFIG"], str(Path(plan["install_root"]) / "config/paths.json"))
        for key, path in plan["data_roots"].items():
            self.assertEqual(env[installer.ENV_KEYS[key]], path)
        self.assertNotEqual(env["PORTAL_LINEAGE_TIMER_INSTANCE"], env["PORTAL_INSTANCE"])

    def test_postgres_libraries_do_not_leak_into_application_or_system_tools(self):
        target = installer.target_plan(self.target)
        target['pg_lib'] = str(self.base / 'postgres-private-libraries')
        for platform in ('linux', 'macos', 'windows'):
            with self.subTest(platform=platform), patch.object(installer, 'platform_adapter') as adapter:
                adapter.return_value.__name__ = 'test_' + platform
                library_key = 'DYLD_LIBRARY_PATH' if platform == 'macos' else 'LD_LIBRARY_PATH'
                for component in ('web', 'resource', 'society', 'execution', 'worker', 'nats'):
                    _, environment = installer.component_command(target, component)
                    self.assertNotIn(library_key, environment)
                    self.assertNotIn(target['pg_lib'], environment['PATH'])
                _, environment = installer.component_command(target, 'postgres')
                if platform == 'windows':
                    self.assertIn(target['pg_lib'], environment['PATH'])
                else:
                    self.assertEqual(environment[library_key], target['pg_lib'])
                for tool in ('initdb', 'pg_isready', 'pg_dump', 'psql'):
                    command = [str(Path(target['pg_bin']) / installer.native_tool(tool))]
                    native = installer.command_environment(target, command)
                    self.assertEqual(native.get(library_key), environment.get(library_key))
                    self.assertEqual(native['PATH'], environment['PATH'])
                for command in ([target['python'], '--version'], ['/usr/bin/pdftotext', '-v']):
                    environment = installer.command_environment(target, command)
                    self.assertNotIn(library_key, environment)
                    self.assertNotIn(target['pg_lib'], environment['PATH'])

    def test_source_root_and_nested_data_roots_are_rejected(self):
        self.target["install_root"] = str(installer.SOURCE_ROOT)
        with self.assertRaisesRegex(ValueError, "overlaps"):
            installer.target_plan(self.target)
        self.target["install_root"] = str(self.base / "instance")
        self.target["data_roots"]["secrets_dir"] = self.target["data_roots"]["accounts_root"] + "/secrets"
        with self.assertRaisesRegex(ValueError, "overlap"):
            installer.target_plan(self.target)

    def test_symlink_to_source_data_is_rejected(self):
        root = Path(self.target["install_root"])
        root.mkdir()
        (root / "data").symlink_to(installer.SOURCE_ROOT, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "inside install_root"):
            installer.target_plan(self.target)

    def test_duplicate_ports_and_source_service_names_rejected(self):
        target = copy.deepcopy(self.target)
        target["ports"]["nats"] = target["ports"]["web"]
        with self.assertRaisesRegex(ValueError, "different"):
            installer.target_plan(target)
        self.target["service_prefix"] = "portal-dev"
        with self.assertRaisesRegex(ValueError, "unique tfi-"):
            installer.target_plan(self.target)

    def test_units_and_commands_only_reference_target(self):
        plan = installer.target_plan(self.target)
        for component in installer.COMPONENTS:
            unit = installer.unit_text(plan, component)
            self.assertNotIn("management-portal.service", unit)
            self.assertNotIn("portal-dev", unit)
            self.assertIn(plan["install_root"], unit)
            self.assertIn("WorkingDirectory=" + plan["install_root"] + "/code\n", unit)
            self.assertEqual("KillSignal=SIGINT\n" in unit, component == "nats")
            command, env = installer.component_command(plan, component)
            self.assertEqual(env["PORTAL_INSTANCE"], plan["service_prefix"])
            self.assertNotIn("archive_lineage", env["PORTAL_DATABASE_URL"])
        command, _ = installer.component_command(plan, "postgres")
        self.assertIn("listen_addresses=", command)

    def bundle(self):
        bundle = self.base / "bundle"
        (bundle / "code/scripts").mkdir(parents=True)
        (bundle / "code/scripts/install_tfi_release").write_text("# fixed installer\n")
        (bundle / "database").mkdir()
        (bundle / "database/distribution.sql").write_text("SELECT '${share_root}';\n")
        for key in installer.KEYS:
            (bundle / "data" / key).mkdir(parents=True)
        inventory = [{"path": p.relative_to(bundle).as_posix(), "sha256": installer.digest(p), "size": p.stat().st_size}
                     for p in sorted(bundle.rglob("*")) if p.is_file()]
        code_files = [{**i, "path": i["path"][5:]} for i in inventory if i["path"].startswith("code/")]
        manifest = {"schema": "tfi.release-engineering.v1", "run_id": self.target["run_id"],
                    "supervisor_resident_id": self.target["supervisor_resident_id"], "unresolved": [],
                    "code": {"path": "code", "sha256": hashlib.sha256(json.dumps(code_files, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()},
                    "database": {"path": "database/distribution.sql", "format": "postgresql-sql", "sha256": installer.digest(bundle / "database/distribution.sql")},
                    "files": {k: f"data/{k}" for k in installer.KEYS}, "inventory": inventory,
                    "directory_mappings": [{"root": k, "placeholder": "${" + k + "}", "path": "."} for k in (*installer.KEYS, "code_root")],
                    "installer": {"entrypoint": "code/scripts/install_tfi_release"}}
        installer.write_json(bundle / "manifest.json", manifest)
        return bundle

    def test_engineering_tamper_and_unlisted_file_are_rejected_before_install(self):
        bundle = self.bundle()
        installer.validate_engineering(bundle / "manifest.json", self.target)
        (bundle / "extra").write_text("unlisted")
        with self.assertRaisesRegex(ValueError, "inventory"):
            installer.validate_engineering(bundle / "manifest.json", self.target)
        (bundle / "extra").unlink()
        (bundle / "database/distribution.sql").write_text("changed")
        with self.assertRaisesRegex(ValueError, "checksum"):
            installer.validate_engineering(bundle / "manifest.json", self.target)
        self.assertFalse(Path(self.target["install_root"]).exists())

    def test_supervisor_and_traversal_cannot_be_silently_reinterpreted(self):
        bundle = self.bundle()
        self.target["supervisor_resident_id"] = "resident_667111522609"
        with self.assertRaisesRegex(ValueError, "supervisor_resident_id"):
            installer.validate_engineering(bundle / "manifest.json", self.target)
        with self.assertRaisesRegex(ValueError, "relative"):
            installer.engineering_path(bundle, "../source.sql")

    def test_materialization_maps_declared_paths_without_changing_source(self):
        bundle = self.bundle()
        manifest, _ = installer.validate_engineering(bundle / "manifest.json", self.target)
        root = Path(self.target["install_root"])
        installer.prepare_configuration(self.target)
        installer.copy_engineering(manifest, bundle, self.target)
        self.assertIn(self.target["data_roots"]["share_root"], (root / "runtime/distribution.sql").read_text())
        self.assertIn("${share_root}", (bundle / "database/distribution.sql").read_text())
        self.assertEqual(installer.read_json(root / "config/paths.json"), self.target["data_roots"])
        self.assertEqual(installer.read_json(root / "config/site.json")["site_name"], "TFI Portal")
        from management_common.portal_network import router_access
        self.assertEqual(router_access(root / "config/network.env")["mode"], "local")

    def test_read_only_templates_materialize_and_retry_with_source_modes(self):
        bundle = self.bundle()
        template = bundle / 'data/share_root/readonly.json'
        original = b'{"root":"${share_root}/Articles/example"}\r\n'
        template.write_bytes(original)
        template.chmod(0o444)
        binary = bundle / 'data/share_root/image.bin'
        binary.write_bytes(b'\xff\x00${share_root}')
        binary.chmod(0o444)
        code = bundle / 'code/scripts/install_tfi_release'
        code.chmod(0o555)
        installer.prepare_configuration(self.target)
        manifest = installer.read_json(bundle / 'manifest.json')
        for _ in range(2):
            installer.copy_engineering(manifest, bundle, self.target)
        copied = Path(self.target['data_roots']['share_root']) / template.name
        self.assertEqual(copied.read_bytes(), original.replace(b'${share_root}', self.target['data_roots']['share_root'].encode()))
        self.assertEqual(copied.stat().st_mode & 0o777, 0o444)
        self.assertEqual(template.read_bytes(), original)
        self.assertEqual(template.stat().st_mode & 0o777, 0o444)
        self.assertEqual((copied.parent / binary.name).read_bytes(), binary.read_bytes())
        self.assertEqual((Path(self.target['install_root']) / 'code/scripts/install_tfi_release').stat().st_mode & 0o777, 0o555)
        self.assertFalse(list(copied.parent.glob('.tfi-install-*')))

    def test_failed_materialization_replace_preserves_old_bytes_and_mode(self):
        source = self.base / 'template'
        destination = self.base / 'installed'
        source.write_text('${share_root}/new')
        source.chmod(0o444)
        destination.write_text('old complete content')
        destination.chmod(0o444)
        with patch.object(installer.os, 'replace', side_effect=PermissionError('replace denied')):
            with self.assertRaises(PermissionError):
                installer.copy_materialized_file(source, destination, {'${share_root}': '/target'})
        self.assertEqual(destination.read_text(), 'old complete content')
        self.assertEqual(destination.stat().st_mode & 0o777, 0o444)
        self.assertFalse(list(self.base.glob('.tfi-install-*')))

    def test_changed_bundle_cannot_overwrite_an_existing_instance(self):
        bundle = self.bundle()
        target = installer.target_plan(self.target)
        state = {"schema": installer.SCHEMA, "target": target, "manifest_sha256": "different",
                 "protected_roots": [str(installer.SOURCE_ROOT)]}
        installer.write_json(Path(target["install_root"]) / installer.STATE_FILE, state)
        with patch.object(installer, "check_available"), self.assertRaisesRegex(ValueError, "different immutable"):
            installer.install(bundle / "manifest.json", self.target)

    def test_native_download_failure_can_retry_before_any_service_registration(self):
        from scripts import tfi_release_native_dependencies as native
        bundle = self.bundle()
        self.target.pop('nats_server')
        root = Path(self.target['install_root'])
        def prepare(api, target, entries):
            executable = Path(target['nats_server'])
            executable.parent.mkdir(parents=True)
            executable.touch(mode=0o700)
            return [{'status': 'verified'}]
        with patch.object(installer, 'check_available'), \
             patch.object(installer, 'run_utf8_capture', return_value=subprocess.CompletedProcess([], 0, 'Python 3.12.8\n', '')), \
             patch.object(installer, 'dependency_preflight', return_value={}), \
             patch.object(installer, 'register_services') as register, \
             patch.object(installer, 'prepare_configuration', side_effect=RuntimeError('stop before copy')), \
             patch.object(native, 'prepare', side_effect=OSError('offline')) as download:
            with self.assertRaisesRegex(OSError, 'offline'):
                installer.install(bundle / 'manifest.json', self.target)
            state = installer.read_json(root / installer.STATE_FILE)
            self.assertEqual(state['stages'][-1]['phase'], 'native_dependencies')
            self.assertEqual(state['status'], 'failed')
            download.side_effect = prepare
            with self.assertRaisesRegex(RuntimeError, 'stop before copy'):
                installer.install(bundle / 'manifest.json', self.target)
            self.assertEqual(download.call_count, 2)
            register.assert_not_called()
            state = installer.read_json(root / installer.STATE_FILE)
            self.assertEqual(state['native_dependencies'][0]['status'], 'verified')

    def test_foreign_service_definition_blocks_stop_and_cleanup(self):
        directory = self.base / "units"
        directory.mkdir()
        (directory / installer.unit_name(self.target, "web")).write_text("foreign unit")
        with patch.object(installer, "unit_directory", return_value=directory), self.assertRaisesRegex(ValueError, "ownership mismatch"):
            installer.check_available(self.target, existing=True)

    def test_stop_checks_only_owned_units_and_does_not_delete_data(self):
        from unittest.mock import Mock
        target = installer.target_plan(self.target)
        root = Path(target["install_root"])
        state = {"schema": installer.SCHEMA, "target": target,
                 "protected_roots": [str(installer.SOURCE_ROOT)], "stages": []}
        installer.write_json(root / installer.STATE_FILE, state)
        (root / "retained").write_text("instance data")
        controller = Mock()
        controller.status.side_effect = lambda name: {"service": name, "load": "loaded", "active": "inactive", "pid": 0}
        with patch.object(installer, "service_controller", return_value=controller), patch.object(installer, "check_available"), \
             patch('scripts.tfi_release_provider_services.owned_services', return_value=None):
            result = installer.lifecycle("stop", root)
        self.assertEqual(result["status"], "stopped")
        self.assertEqual([c.args[0] for c in controller.stop.call_args_list],
                         [installer.unit_name(target, c) for c in reversed(installer.COMPONENTS)])
        self.assertEqual((root / "retained").read_text(), "instance data")

    def test_cleanup_refuses_when_one_owned_process_is_still_running(self):
        from unittest.mock import Mock
        target = installer.target_plan(self.target)
        root = Path(target["install_root"])
        state = {"schema": installer.SCHEMA, "target": target,
                 "protected_roots": [str(installer.SOURCE_ROOT)], "stages": []}
        installer.write_json(root / installer.STATE_FILE, state)
        controller = Mock()
        controller.status.return_value = {"load": "loaded", "active": "active", "pid": 1234}
        with patch.object(installer, "service_controller", return_value=controller), patch.object(installer, "check_available"), \
             patch('scripts.tfi_release_provider_services.owned_services', return_value=None), self.assertRaisesRegex(RuntimeError, "cleanup refused"):
            installer.lifecycle("cleanup", root)
        self.assertTrue((root / installer.STATE_FILE).is_file())

    def test_first_admin_uses_target_setup_owner_and_does_not_return_secret(self):
        target = installer.target_plan(self.target)
        root = Path(target["install_root"])
        installer.prepare_configuration(target)
        state = {"schema": installer.SCHEMA, "target": target, "status": "installed",
                 "protected_roots": [str(installer.SOURCE_ROOT)], "stages": []}
        installer.write_json(root / installer.STATE_FILE, state)
        with patch.object(installer, "check_available"), patch.object(installer, "run_checked", return_value="one-time-test-secret") as run:
            result = installer.first_admin(root, "tfi-verifier")
        self.assertNotIn("one-time-test-secret", json.dumps(result))
        self.assertEqual(Path(result["setup_file"]).stat().st_mode & 0o777, 0o600)
        self.assertEqual(run.call_args.kwargs["target"], target)
        self.assertEqual(run.call_args.args[0][1], str(root / "code/scripts/portal_setup.py"))

    def test_fresh_process_reads_only_target_paths_and_disables_timers(self):
        target = installer.target_plan(self.target)
        installer.prepare_configuration(target)
        command = [sys.executable, "-c", "import json; from management_common.portal_paths import paths; from management_portal.society_service.config import runs_lineage_timers; print(json.dumps({'paths': {k:str(v) for k,v in paths().items()}, 'timers': runs_lineage_timers()}))"]
        result = subprocess.run(command, cwd=installer.SOURCE_ROOT, env=installer.clean_environment(target),
                                text=True, capture_output=True, check=True)
        observed = json.loads(result.stdout)
        self.assertEqual(observed["paths"], target["data_roots"])
        self.assertFalse(observed["timers"])


if __name__ == "__main__":
    unittest.main()
