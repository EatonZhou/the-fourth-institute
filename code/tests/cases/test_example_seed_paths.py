"""发行安装与实例隔离回归。

:module: operations.deployment
:design: decision_d88306d0bd17
:profiles: local.fixture
:risk: no-external-side-effects"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "code"))
from prepare_install import inventory


class ExamplePaths(unittest.TestCase):
    def test_seed_designs_use_current_identity_and_path_contracts(self):
        """A fresh installation must not teach removed runtime interfaces."""
        decisions = self.seed / 'data/projects/AI员工/knowledge_base/decision'
        text = '\n'.join(path.read_text() for path in decisions.glob('*.md'))
        for obsolete in ('retired_identities', 'retired_identity_boundary',
                         'resident_workspace_retirement', 'PORTAL_PULSE_ROOT',
                         ':lifecycle: retired'):
            self.assertFalse(obsolete in text, f'Seed Design still names removed interface: {obsolete}')

    def test_public_seed_bootstraps_current_lineage_schema(self):
        """Both installer choices must satisfy the domain startup contracts."""
        import psycopg
        from psycopg.rows import dict_row
        from tests.cases.postgres_test_database import start_disposable_cluster
        from management_portal.society_service.state_store import (
            LINEAGE_DATA_SCHEMA, lineage_data_schema_matches,
        )
        from management_portal.resources.registry_store import _TABLES as registry_tables
        from management_portal.society_service.directory_owner import directory_metadata
        from management_common.json_columns import json_value
        from scripts.tfi_seed_selection import plan
        from scripts.tfi_release_install import map_directories

        # An independent disposable server: never import public data into a
        # Portal instance or the other tests' shared schemas.
        dsn = start_disposable_cluster()
        original = self.manifest['engineering']
        for included in (True, False):
            with self.subTest(include_examples=included):
                _, selected, _ = plan(self.seed, original, include_examples=included)
                sql = selected if selected is not None else (
                    self.seed / original['database']['path']).read_text(encoding='utf-8')
                replacements = {
                    entry['placeholder']: '/isolated-seed/' + entry['root']
                    for entry in original['directory_mappings']
                }
                sql = map_directories(sql, replacements)
                with psycopg.connect(dsn, row_factory=dict_row) as connection:
                    connection.execute(sql, prepare=False)
                    connection.execute('SET search_path TO lineage')
                    self.assertEqual(connection.execute(
                        "SELECT value FROM lineage_meta WHERE key='schema'"
                    ).fetchone()['value'], LINEAGE_DATA_SCHEMA)
                    self.assertTrue(lineage_data_schema_matches(connection))
                    # Imported identities must be usable without an offline
                    # migration, including their directory ownership facts.
                    for table, identity in (("lineages", "lineage_id"), ("residents", "resident_id")):
                        records = connection.execute(
                            f'SELECT {identity}, directory_json FROM {table}'
                        ).fetchall()
                        self.assertTrue(records)
                        for row in records:
                            self.assertEqual(directory_metadata(row['directory_json'], row[identity]),
                                             {'origin': 'created', 'owner_id': row[identity]})
                    workspaces = connection.execute(
                        'SELECT w.origin,w.owner_id,w.operation_id,r.resident_id '
                        'FROM workspace.workspaces w '
                        'LEFT JOIN residents r ON r.owned_workspace_id=w.workspace_id'
                    ).fetchall()
                    self.assertTrue(workspaces)
                    for row in workspaces:
                        self.assertIsNotNone(row['resident_id'])
                        self.assertEqual(row['origin'], 'created')
                        self.assertEqual(row['owner_id'], row['resident_id'])
                        self.assertIsNone(row['operation_id'])
                    for row in connection.execute(
                        "SELECT collection,document_json FROM resource_registry.registry_documents "
                        "WHERE collection IN ('project_registry','repo','article')"
                    ).fetchall():
                        document = json_value(row['document_json'])
                        if row['collection'] == 'project_registry':
                            for project in document['projects']:
                                self.assertEqual(project['directory'],
                                                 {'origin': 'created', 'owner_id': project['id']})
                        else:
                            locations = document['checkouts' if row['collection'] == 'repo' else 'roots']
                            for location in locations:
                                # The shared installed code is adopted; example
                                # content is created by copying the seed areas.
                                expected_origin = 'adopted' if location['path'] == replacements['${code_root}'] else 'created'
                                self.assertEqual(location['origin'], expected_origin)
                                self.assertEqual(location['owner_id'], document['id'])
                    # A seed import bypasses state_store._create_schema. Its
                    # cross-owner deletion trigger must therefore be present in
                    # the dump, not merely in fresh-schema Python tests.
                    owner, borrower = connection.execute(
                        "SELECT resident_id,owned_workspace_id FROM residents "
                        "WHERE deleted_at IS NULL ORDER BY resident_id LIMIT 2"
                    ).fetchall()
                    connection.execute(
                        "INSERT INTO resident_workspace_bindings(resident_id,workspace_id,purpose) "
                        "VALUES (%s,%s,'seed deletion regression') ON CONFLICT DO NOTHING",
                        (borrower['resident_id'], owner['owned_workspace_id']))
                    connection.execute(
                        "UPDATE workspace.workspaces SET deleted_at=now() "
                        "WHERE workspace_id=%s", (owner['owned_workspace_id'],))
                    self.assertEqual(connection.execute(
                        "SELECT count(*) AS n FROM resident_workspace_bindings WHERE workspace_id=%s",
                        (owner['owned_workspace_id'],)).fetchone()['n'], 0)
                    queued = connection.execute(
                        "SELECT payload_json FROM resident_lifecycle_operations WHERE resident_id=%s",
                        (borrower['resident_id'],)).fetchone()
                    self.assertIsNotNone(queued)
                    self.assertIn({'kind': 'workspace_document', 'resident_id': borrower['resident_id']},
                                  json_value(queued['payload_json'])['steps'])
                    actual_registry_tables = {row['table_name'] for row in connection.execute(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema='resource_registry'"
                    ).fetchall()}
                    self.assertEqual(actual_registry_tables, registry_tables)
                    connection.rollback()

    def test_seed_metadata_initializes_only_storage_schema(self):
        """Installed instances acquire their own leases and normalize their state."""
        from scripts.tfi_seed_selection import inserts
        sql = (self.seed / self.manifest['engineering']['database']['path']).read_text(encoding='utf-8')
        metadata = {}
        for _, _, table, row in inserts(sql):
            if table[1] in {'lineage_meta', 'agent_store_meta', 'registry_meta'}:
                metadata.setdefault(table[1], []).append(row['key'])
        self.assertEqual(metadata, {name: ['schema'] for name in
                         ('lineage_meta', 'agent_store_meta', 'registry_meta')})

    def test_builtin_maintenance_scripts_use_exported_code(self):
        """Fresh installs resolve built-in scripts inside their own code tree."""
        from scripts.tfi_seed_selection import inserts
        from scripts.tfi_release_install import map_directories
        from pathlib import PureWindowsPath

        names = {'update_paper_ccf_cache.py', 'resource_backup_lineage.py',
                 'portal_capacity_alerts.py'}
        sql = (self.seed / self.manifest['engineering']['database']['path']).read_text(encoding='utf-8')
        found = set()
        for _, _, table, row in inserts(sql):
            if table != ('lineage', 'lineage_components'):
                continue
            script = row.get('config_json', {}).get('script', '')
            for name in names:
                if name not in script:
                    continue
                found.add(name)
                expected = '${code_root}/scripts/' + name
                self.assertIn(expected, script, row['node_id'])
                self.assertTrue((REPO / 'code/scripts' / name).is_file())
                for root in ('/isolated/portal/code', 'D:/独立实例/code'):
                    mapped = map_directories(script, {'${code_root}': root})
                    self.assertIn(root + '/scripts/' + name, mapped)
                    self.assertNotIn('${code_root}', mapped)
                self.assertTrue(PureWindowsPath('D:/独立实例/code/scripts/' + name).is_absolute())
        self.assertEqual(found, names)

    @classmethod
    def setUpClass(cls):
        cls.seed = Path(os.environ.get("TFI_EXAMPLE_SEED", REPO / "instance/example_instance/seed"))
        cls.manifest = json.loads((cls.seed / "manifest.json").read_text(encoding="utf-8"))

    def test_data_is_laid_out_by_area_and_records_name_files_by_area(self):
        """Seed files sit in data/<area>/ and every record names its file as <area>:<path>."""
        from management_portal.distribution.layout import EXPORT_AREAS, reference_target, split_reference
        from scripts.tfi_seed_selection import inserts
        engineering = self.manifest["engineering"]
        areas = sorted(path.name for path in (self.seed / "data").iterdir())
        self.assertTrue(set(areas) <= set(EXPORT_AREAS), areas)
        self.assertEqual(engineering["files"], {area: "data/" + area for area in areas})
        self.assertEqual(engineering["directory_mappings"],
                         [{"placeholder": "${code_root}", "root": "code_root", "path": "."}])
        sql = (self.seed / engineering["database"]["path"]).read_text(encoding="utf-8")
        # The only directory placeholder left is the code directory.
        placeholders = set(re.findall(r"\$\{(\w+_root)\}", sql + json.dumps(self.manifest)))
        self.assertEqual(placeholders, {"code_root"})
        lineage_project, located = {}, []
        for _, _, table, row in inserts(sql):
            if table == ("lineage", "lineages"):
                lineage_project[row["lineage_id"]] = row.get("project_id") or ""
            elif table == ("workspace", "workspaces"):
                located.append(row["path"])
            elif table == ("resource_registry", "registry_documents") and row["collection"] in {"repo", "article"}:
                document = row["document_json"]
                located.append(document.get("canonical_checkout") or document["canonical_root"])
            elif table == ("resource_registry", "registry_documents") and row["collection"] == 'project_registry':
                located.extend(project['path'] for project in row['document_json']['projects'])
        self.assertTrue(located)
        for reference in located:
            if reference == "${code_root}":
                continue
            split_reference(reference)
            self.assertTrue((self.seed / reference_target(reference)).is_dir(), reference)
        # Repos and articles sit in their project's folder; a Lineage folder in its project's agents folder.
        for reference in located:
            if "/articles/" in reference or "/codes/" in reference:
                self.assertRegex(reference, r"^projects:[^/]+/(articles/article|codes/repo)_[0-9a-f]{12}$")
        data = self.seed / "data"
        for folder in data.glob("projects/*/agents/lineage_*"):
            self.assertEqual(lineage_project[folder.name], folder.parent.parent.name, folder)
        for folder in data.glob("lineages/lineage_*"):
            self.assertEqual(lineage_project[folder.name], "", folder)
        self.assertTrue(list(data.glob("projects/*/agents/lineage_*")))

    def test_inventory_and_path_budget(self):
        """安装与实例回归：inventory and path budget。"""
        rows = [r for r in inventory(self.seed) if r["path"] != "manifest.json"]
        self.assertEqual(rows, sorted(self.manifest["inventory"], key=lambda row: row["path"]))
        self.assertLessEqual(max(len(r["path"]) for r in rows), 175)
        self.assertFalse(any("release-runtime-v1/workspace_" in r["path"] for r in rows))

    def test_architecture_descriptors(self):
        """安装与实例回归：architecture descriptors。"""
        roots = list(self.seed.glob("data/projects/*/agents/*/residents/*/workspace/architecture/manifest.json"))
        self.assertEqual(len(roots), 1)
        workspace = roots[0].parent.parent
        checked = set()
        def walk(value):
            if isinstance(value, list):
                for item in value:
                    walk(item)
            elif isinstance(value, dict):
                address = value.get("address", "")
                if isinstance(address, str) and address.startswith("workspace_04ad72d24263/file/"):
                    path = workspace / address.split("/file/", 1)[1]
                    self.assertTrue(path.is_file(), address)
                    if "sha256" in value:
                        raw = path.read_bytes()
                        self.assertEqual(value["sha256"], hashlib.sha256(raw).hexdigest(), address)
                        if "size" in value:
                            self.assertEqual(value["size"], len(raw), address)
                    checked.add(path)
                for item in value.values():
                    walk(item)
        for path in roots[0].parent.rglob("*.json"):
            walk(json.loads(path.read_text(encoding="utf-8")))
        walk(self.manifest["engineering"])
        self.assertGreaterEqual(len(checked), 14)

    def test_architecture_model_loads_in_current_consumer(self):
        """An inventoried model must also satisfy the installed UI contract."""
        model = next(self.seed.glob('data/projects/*/agents/*/residents/*/workspace/architecture/explore/exploration-model.json'))
        consumer = REPO / 'code/management_portal/workbench_projections/development_app/src/architecture/exploration-model.js'
        program = """
import fs from 'node:fs';
const {validateExplorationModel} = await import(process.argv[2]);
const result = validateExplorationModel(JSON.parse(fs.readFileSync(process.argv[1], 'utf8')));
if (!result.valid) { console.error(JSON.stringify(result.errors)); process.exit(1); }
"""
        subprocess.run(['node', '--input-type=module', '-e', program, str(model), consumer.as_uri()], check=True)

    def test_published_artifacts_through_real_reader(self):
        """安装与实例回归：published artifacts through real reader。"""
        from management_portal.workbench_projections.application_build import read_artifact
        from tests.cases.instance_test_support import selected_instance
        # The reader finds artifacts in the features area; install the seed's features/ there, as the installer does.
        with tempfile.TemporaryDirectory() as temporary:
            area = Path(temporary) / "features"
            shutil.copytree(self.seed / "data/features", area)
            root = area / "projection_applications"
            self.assertTrue(list(root.iterdir()))
            with selected_instance(Path(temporary) / "instance", areas={"features": area}):
                for directory in root.iterdir():
                    index = json.loads((directory / "artifact.json").read_text(encoding="utf-8"))
                    build = {"status": "ready", "artifact_hash": directory.name, **index}
                    for name in index["files"]:
                        content, _ = read_artifact(build, name)
                        self.assertEqual(len(content), index["files"][name]["size"])

    def test_lenia_workers_preserve_clip_and_frame_references(self):
        """安装与实例回归：lenia workers preserve clip and frame references。"""
        roots = list(self.seed.glob("data/features/projection_applications/*/files/catalog.js"))
        self.assertEqual(len(roots), 1)
        program = r"""
const fs = require('fs'), path = require('path'), vm = require('vm'), crypto = require('crypto');
const root = process.argv[1];
function read(name) {
  let result;
  const self = {postMessage: x => result = x, close() {}};
  vm.runInNewContext(fs.readFileSync(path.join(root, name.replace(/\.json$/, '.js')), 'utf8'), {self});
  self.onmessage();
  if (!result) throw new Error('No payload: ' + name);
  return result;
}
const catalog = read('catalog.json');
const entries = Array.isArray(catalog) ? catalog : catalog.entries;
if (!entries.length) throw new Error('Empty catalog');
for (const entry of entries) {
  const clip = read(entry.offlineClip), frames = read(clip.framesFile);
  const sha = crypto.createHash('sha256').update(Buffer.from(frames.data, 'base64')).digest('hex');
  if (sha !== clip.sha256) throw new Error('Frame integrity: ' + entry.id);
}
console.log('All catalog workers and frame payloads are reachable and intact');
"""
        result = subprocess.run(["node", "-e", program, str(roots[0].parent)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
