import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from management_common import host_instances, instance_manifest, instance_locations, portal_paths
from management_common.instance_manifest import AREAS, SCHEMA, load, validate


class InstanceManifestTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "instances/one"
        self.document = {
            "schema": SCHEMA, "instance_id": "instance_one", "name": "one",
            "created_at": "2026-11-01T00:00:00Z", "seed_version": "test",
            "deployments": {"release": {"kind": "release", "code_root": str(self.base / "source/code"), "web_port": 18086}},
            "network": {"bind": "127.0.0.1", "entry_port": 18086, "postgres_port": 15432, "nats_port": 14222},
            "services": {"prefix": "tfi-one", "components": ["postgres", "nats", "web"]},
            "dependencies": {"postgres": {k: str(self.base / "postgres" / k) for k in ("bin", "lib", "share")}},
        }
        self.root.mkdir(parents=True)
        self.write()
        self.registry = patch.object(host_instances, "user_home", return_value=self.base / "host")
        self.registry.start()
        self.addCleanup(self.registry.stop)

    def write(self):
        (self.root / "instance.json").write_text(json.dumps(self.document))

    def test_explicit_selection_and_no_implicit_instance(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "Select an instance"):
                load()
        with patch.dict(os.environ, {"PORTAL_INSTANCE_ROOT": str(self.root)}):
            self.assertEqual(load().root, self.root)

    def test_format_labels_do_not_gate_runtime_configuration(self):
        original = copy.deepcopy(self.document)
        for metadata in ({}, {"schema": "tfi.instance.v0"},
                         {"schema": "tfi.instance.v999", "seed_version": "another-seed"}):
            with self.subTest(metadata=metadata):
                self.document = {key: value for key, value in copy.deepcopy(original).items()
                                 if key not in {"schema", "seed_version"}}
                self.document.update(metadata)
                self.write()
                instance = load(self.root)
                self.assertEqual(instance.document, self.document)
                self.assertEqual(instance.service_name("web"), "tfi-one-web")
                self.assertEqual(instance.internal_web_origin(), "http://127.0.0.1:18086")
                self.assertEqual(instance.resolve("projects:demo"), self.root / "data/projects/demo")
                self.document["deployments"]["release"]["web_port"] = -1
                with self.assertRaisesRegex(ValueError, "ports"):
                    validate(self.root, self.document)

    def test_area_roundtrip_and_relocation_without_record_change(self):
        first = load(self.root)
        record = first.reference(first.area("projects") / "p/agents/lineage_a/file.txt")
        self.assertEqual(record, "projects:p/agents/lineage_a/file.txt")
        self.document["areas"] = {"projects": str(self.base / "large-disk/projects")}
        self.write()
        self.assertEqual(load(self.root).resolve(record), self.base / "large-disk/projects/p/agents/lineage_a/file.txt")
        self.assertEqual(first.reference(first.area("projects")), "projects:")
        self.assertEqual(first.resolve("projects:"), first.area("projects"))

    def test_runtime_path_readers_share_one_validated_manifest_revision(self):
        with patch.dict(os.environ, {"PORTAL_INSTANCE_ROOT": str(self.root)}), \
                patch.object(instance_manifest, "validate", wraps=validate) as validating:
            for _ in range(100):
                self.assertEqual(portal_paths.path("projects"), self.root / "data/projects")
                self.assertEqual(instance_locations.located_path("projects:demo"),
                                 str(self.root / "data/projects/demo"))
            self.assertEqual(validating.call_count, 1)
            self.document["areas"] = {"projects": str(self.base / "relocated")}
            self.write()
            self.assertEqual(portal_paths.path("projects"), self.base / "relocated")
            self.assertEqual(instance_locations.located_path("projects:demo"),
                             str(self.base / "relocated/demo"))
            self.assertEqual(validating.call_count, 2)

    def test_runtime_manifest_detects_replace_switch_and_invalid_revision(self):
        source = self.root / "instance.json"
        with patch.dict(os.environ, {"PORTAL_INSTANCE_ROOT": str(self.root)}):
            original = instance_manifest.current_instance()
            before = source.stat()
            replacement = self.root / "replacement.json"
            replacement.write_text(source.read_text().replace('instance_one', 'instance_two'))
            os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
            replacement.replace(source)
            self.assertEqual(instance_manifest.current_instance().instance_id, "instance_two")
            self.assertEqual(original.instance_id, "instance_one")
            with patch.dict(os.environ, {"PORTAL_INSTANCE_ROOT": str(self.base / "missing")}):
                with self.assertRaises(FileNotFoundError):
                    instance_manifest.current_instance()
            source.write_text('{}')
            with self.assertRaises(ValueError):
                instance_manifest.current_instance()
            source.unlink()
            with self.assertRaises(FileNotFoundError):
                instance_manifest.current_instance()

    def test_runtime_manifest_rejects_symlink_after_cache_is_warm(self):
        source = self.root / "instance.json"
        with patch.dict(os.environ, {"PORTAL_INSTANCE_ROOT": str(self.root)}):
            instance_manifest.current_instance()
            target = self.root / "actual.json"
            source.rename(target)
            source.symlink_to(target)
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                instance_manifest.current_instance()

    def test_host_paths_and_fixed_state_have_different_registration_rules(self):
        instance = load(self.root)
        host = self.base / "work/repo"
        self.assertEqual(instance.resolve(instance.reference(host)), host)
        with self.assertRaisesRegex(ValueError, "Only area files"):
            instance.reference(self.root / "secrets/key")

    def test_reject_traversal_and_noncanonical_references(self):
        instance = load(self.root)
        for reference in ("projects:../key", "projects:/etc/passwd", "projects:a/../b", "projects:a//b", "unknown:a", "relative"):
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                instance.resolve(reference)

    @unittest.skipIf(os.name == 'nt', 'POSIX literal filenames')
    def test_native_area_and_host_filenames_roundtrip(self):
        instance = load(self.root)
        for name in ('literal\\name', 'with\nnewline'):
            for root in (instance.area('projects'), self.base / 'host-data'):
                with self.subTest(name=name, root=root):
                    path = root / name
                    self.assertEqual(instance.resolve(instance.reference(path)), path)

    def test_reject_symlink_escaping_an_area(self):
        instance = load(self.root)
        area = instance.area("features")
        area.mkdir(parents=True)
        (area / "escape").symlink_to(self.base, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "escapes"):
            instance.resolve("features:escape/file")

    def test_fixed_instance_directories_cannot_redirect_to_host_state(self):
        host = self.base / "private-host-state"
        host.mkdir()
        for name in ("secrets", "database", "cache", "runtime"):
            path = self.root / name
            path.symlink_to(host, target_is_directory=True)
            try:
                with self.subTest(directory=name), self.assertRaisesRegex(ValueError, "symbolic links"):
                    load(self.root)
            finally:
                path.unlink()

    def test_reject_overlapping_areas_and_code(self):
        for areas in ({"projects": str(self.base / "same"), "papers": str(self.base / "same/papers")},
                      {"projects": str(self.root / "runtime/p")},
                      {"projects": str(self.base / "source/files")}):
            with self.subTest(areas=areas), self.assertRaises(ValueError):
                validate(self.root, {**self.document, "areas": areas})
        with self.assertRaisesRegex(ValueError, "outside the code"):
            validate(self.base / "source/instance/example", self.document)

    def test_runtime_environment_is_not_an_instance_dependency(self):
        document = copy.deepcopy(self.document)
        document["dependencies"]["python"] = "/usr/bin/python3"
        with self.assertRaisesRegex(ValueError, "data-bound"):
            validate(self.root, document)

    def test_paired_deployments_require_gateway_and_unique_ports(self):
        document = copy.deepcopy(self.document)
        document["deployments"]["dev"] = {"kind": "dev", "code_root": str(self.base / "dev/code"), "web_port": 18087}
        document["network"]["entry_port"] = 18088
        with self.assertRaisesRegex(ValueError, "gateway"):
            validate(self.root, document)
        document["services"]["components"].append("gateway")
        instance = validate(self.root, document)
        self.assertEqual(instance.code_root(), self.base / "dev/code")
        self.assertEqual(instance.service_name("gateway"), "tfi-one-gateway")
        document["deployments"]["dev"]["web_port"] = 18086
        with self.assertRaisesRegex(ValueError, "ports"):
            validate(self.root, document)

    def test_registry_registration_and_retirement(self):
        instance = load(self.root)
        host_instances.register(instance)
        self.assertEqual(host_instances.find("one").root, self.root)
        host_instances.register(instance)
        host_instances.unregister(instance)
        self.assertEqual(host_instances.read_registry(), {})

    def second(self):
        other = copy.deepcopy(self.document)
        other.update(name="two", instance_id="instance_two")
        other["services"]["prefix"] = "tfi-two"
        other["network"].update(entry_port=28086, postgres_port=25432, nats_port=24222)
        other["deployments"]["release"]["web_port"] = 28086
        root = self.base / "instances/two"
        root.mkdir()
        (root / "instance.json").write_text(json.dumps(other))
        return load(root)

    def test_registry_rejects_port_prefix_and_area_conflicts(self):
        host_instances.register(load(self.root))
        second = self.second()
        host_instances.validate_allocation(second)
        for change in (lambda d: d["network"].update(postgres_port=15432),
                       lambda d: d["services"].update(prefix="tfi-one"),
                       lambda d: d.update(areas={"projects": str(self.root / "data/projects")})):
            document = copy.deepcopy(second.document)
            change(document)
            with self.assertRaises(ValueError):
                host_instances.validate_allocation(validate(second.root, document))

    def test_registration_rejects_another_instances_files(self):
        first = load(self.root)
        second = self.second()
        host_instances.register(first)
        host_instances.register(second)
        for path in (second.root / "runtime/item", second.area("projects") / "p"):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "another instance"):
                host_instances.registered_reference(first, path)
        self.assertEqual(host_instances.registered_reference(first, first.area("skills") / "s"), "skills:s")


if __name__ == "__main__":
    unittest.main()
