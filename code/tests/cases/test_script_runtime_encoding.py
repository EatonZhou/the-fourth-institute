import subprocess
import json
from pathlib import Path
import pytest
from management_common.directory_links import create_directory_link, remove_directory_link
from management_portal.society_service.script_runs import runtime
from management_portal.society_service.script_runs import runner
from management_portal.society_service.script_runs import service


def test_script_output_is_utf8_under_legacy_host_encoding(tmp_path, monkeypatch):
    monkeypatch.setenv('PYTHONUTF8', '0')
    monkeypatch.setenv('PYTHONIOENCODING', 'cp1252')
    script = tmp_path / 'unicode.py'
    script.write_text("print('中文 🧪')", encoding='utf-8')
    command, env = runtime.command({'runtime': 'python', 'path': str(script)}, tmp_path)
    result = subprocess.run(command, env=env, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.decode('utf-8').strip() == '中文 🧪'


def test_structured_results_preserve_unicode_under_legacy_host_encoding(tmp_path, monkeypatch):
    original = Path.read_text
    def legacy_read(path, encoding=None, errors=None):
        return original(path, encoding=encoding or 'cp1252', errors=errors)
    monkeypatch.setattr(Path, 'read_text', legacy_read)
    io = tmp_path / 'io'
    (io / 'artifacts').mkdir(parents=True)
    expected = {'text': '中文 🧪', 'path': '中文脚本.py'}
    (io / 'output.json').write_text(json.dumps(expected, ensure_ascii=False), encoding='utf-8')
    (io / 'artifacts' / '结果 🧪.txt').write_text('文件中文 🧪', encoding='utf-8')
    outputs, artifacts = runner.result_fields(io)
    assert outputs == expected
    assert artifacts[0]['name'] == '结果 🧪.txt'


@pytest.mark.parametrize('location', ['root', 'nested'])
def test_artifact_directory_links_are_rejected_at_collection_and_read(tmp_path, monkeypatch, location):
    monkeypatch.setattr(runtime, 'RUN_ROOT', tmp_path)
    identifier = 'script_run_link'
    io = tmp_path / identifier / 'io'
    io.mkdir(parents=True)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'marker.txt').write_bytes(b'outside')
    root = io / 'artifacts'
    if location == 'nested':
        root.mkdir()
    link = root if location == 'root' else root / 'linked'
    name = 'marker.txt' if location == 'root' else 'linked/marker.txt'
    create_directory_link(link, outside)
    monkeypatch.setattr(service, 'get', lambda *a, **kw: {
        'script_run_id': identifier, 'artifacts': [{'name': name, 'mime_type': 'text/plain'}]})
    try:
        with pytest.raises(ValueError, match='script.invalid_artifacts'):
            runner.result_fields(io)
        with pytest.raises(PermissionError):
            service.read_output({'lineage_id': 'fixture', 'script_run_id': identifier,
                                 'stream': 'artifact', 'name': name}, 'fixture')
    finally:
        remove_directory_link(link)
