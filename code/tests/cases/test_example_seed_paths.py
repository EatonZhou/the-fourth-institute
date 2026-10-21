"""发行安装与实例隔离回归。

:module: operations.deployment
:design: decision_d88306d0bd17
:profiles: local.fixture
:risk: no-external-side-effects"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "code"))
from prepare_install import inventory


class ExamplePaths(unittest.TestCase):
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

    def test_inventory_and_path_budget(self):
        """安装与实例回归：inventory and path budget。"""
        rows = [r for r in inventory(self.seed) if r["path"] != "manifest.json"]
        self.assertEqual(rows, sorted(self.manifest["inventory"], key=lambda row: row["path"]))
        self.assertLessEqual(max(len(r["path"]) for r in rows), 175)
        self.assertFalse(any("release-runtime-v1/workspace_" in r["path"] for r in rows))

    def test_architecture_descriptors(self):
        """安装与实例回归：architecture descriptors。"""
        roots = list(self.seed.glob("data/share_root/Agents/**/workspace/architecture/manifest.json"))
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
        model = next(self.seed.glob('data/share_root/Agents/**/architecture/explore/exploration-model.json'))
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
        root = self.seed / "data/staging_root/shared/projection_applications"
        with patch.dict(os.environ, {"PORTAL_PROJECTION_ARTIFACT_ROOT": str(root.resolve())}):
            for directory in root.iterdir():
                index = json.loads((directory / "artifact.json").read_text(encoding="utf-8"))
                build = {"status": "ready", "artifact_hash": directory.name, **index}
                for name in index["files"]:
                    content, _ = read_artifact(build, name)
                    self.assertEqual(len(content), index["files"][name]["size"])

    def test_lenia_workers_preserve_clip_and_frame_references(self):
        """安装与实例回归：lenia workers preserve clip and frame references。"""
        roots = list(self.seed.glob("data/staging_root/shared/projection_applications/*/files/catalog.js"))
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
