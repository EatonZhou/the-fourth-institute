import subprocess
from management_portal.society_service.script_runs import runtime


def test_script_output_is_utf8_under_legacy_host_encoding(tmp_path, monkeypatch):
    monkeypatch.setenv('PYTHONUTF8', '0')
    monkeypatch.setenv('PYTHONIOENCODING', 'cp1252')
    script = tmp_path / 'unicode.py'
    script.write_text("print('中文 🧪')", encoding='utf-8')
    command, env = runtime.command({'runtime': 'python', 'path': str(script)}, tmp_path)
    result = subprocess.run(command, env=env, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.decode('utf-8').strip() == '中文 🧪'
