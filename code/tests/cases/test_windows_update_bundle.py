"""A Windows update must remain a complete, validated engineering after ZIP transport."""
import hashlib
import json
from pathlib import Path
import zipfile

from scripts import tfi_release_install as installer
from scripts.windows.build_update import build


def test_relative_output_and_omitted_empty_zip_directories(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    payload = Path('payload')
    files = {'code/scripts/tfi_release_install.py': b'# installer\n',
             'code/scripts/tfi_release_schema.py': b'# migration\n',
             'database/distribution.sql': b'SELECT 1;'}
    inventory = []
    for name, data in sorted(files.items()):
        path = payload / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        inventory.append({'path': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    code_rows = [{**row, 'path': row['path'][5:]} for row in inventory if row['path'].startswith('code/')]
    manifest = {'schema': 'tfi.release-engineering.v1', 'run_id': 'seed', 'supervisor_resident_id': '',
        'inventory': inventory, 'files': {key: 'data/' + key for key in installer.KEYS},
        'code': {'path': 'code', 'sha256': hashlib.sha256(json.dumps(code_rows, ensure_ascii=False,
            sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()},
        'database': {'path': 'database/distribution.sql', 'format': 'postgresql-sql',
                     'sha256': hashlib.sha256(files['database/distribution.sql']).hexdigest()},
        'installer': {'entrypoint': 'code/scripts/tfi_release_install.py'}}
    with zipfile.ZipFile(payload / 'example-engineering.zip', 'w') as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        archive.writestr('manifest.json', json.dumps(manifest))
    (payload / 'windows-package.json').write_text(json.dumps({'source_dirty': False, 'source_commit': 'a' * 40}))
    wheels = payload / 'dependencies/python-wheels'
    wheels.mkdir(parents=True)
    (wheels / 'requirements.lock').write_text('pinned fixture')
    result = build(payload, Path('output'), run_id='workbench-run', origins=['b' * 64])
    assert result['run_id'] == 'workbench-run'
    assert all((Path('output') / relative).is_dir() for relative in manifest['files'].values())
    check, _ = installer.validate_engineering(Path('output/manifest.json'), {'run_id': 'workbench-run',
        'supervisor_resident_id': '', 'install_root': str(tmp_path / 'independent-site')})
    assert check['upgrade']['application']['from_manifest_sha256'] == ['b' * 64]
    assert any(row['path'] == 'python-wheels/requirements.lock' for row in check['inventory'])
