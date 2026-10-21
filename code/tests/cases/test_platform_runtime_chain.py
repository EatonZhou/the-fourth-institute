"""Native process ownership and production RunScript clients, without business IO."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

import pytest

from management_common.local_ipc import LocalListener
from management_common.process_runtime import process_alive, run_process, spawn_process, terminate_process_tree
from management_portal.society_service.script_runs import runtime

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("stop_host", [False, True])
def test_daemon_host_reaps_descendant_after_leader_exit_or_shutdown(tmp_path, stop_host):
    marker = tmp_path / "child pid"
    daemon = tmp_path / "daemon with spaces.py"
    daemon.write_text("import pathlib,subprocess,sys,time\n"
                      "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
                      f"pathlib.Path({str(marker)!r}).write_text(str(child.pid))\n" +
                      ("time.sleep(60)\n" if stop_host else ""))
    process = spawn_process([sys.executable, "-I", str(ROOT / 'management_common/process_host.py'),
                             sys.executable, str(daemon)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    child_pid = None
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        assert marker.exists(), process.communicate(timeout=1)
        child_pid = int(marker.read_text())
        if stop_host:
            terminate_process_tree(process, timeout=8)
        else:
            assert process.wait(timeout=8) == 0
        assert not process_alive(child_pid)
    finally:
        terminate_process_tree(process, timeout=8)
        for stream in (process.stdout, process.stderr):
            stream.close()
        # A test failure must not leave its sleeper alive.
        if child_pid and process_alive(child_pid):
            import psutil
            psutil.Process(child_pid).kill()


@pytest.mark.parametrize("language", ['python', 'javascript', 'bash'])
def test_script_client_interpreter_spaces_and_real_round_trip(tmp_path, language):
    if language != 'python' and not shutil.which({'javascript':'node','bash':'bash'}[language]):
        pytest.skip('Host interpreter not installed')
    run_dir = tmp_path / 'run with spaces'
    run_dir.mkdir()
    (run_dir / 'io').mkdir()
    scripts = {
        'python': ('source.py', "from portal_script import isa\nimport json\nprint(json.dumps(isa('Echo',text='space value')))\n"),
        'javascript': ('source.cjs', "require('portal-script').isa('Echo',{text:'space value'}).then(x=>console.log(JSON.stringify(x)))"),
        'bash': ('source.sh', "set -eu\nportal-isa Echo 'text=space value'\n"),
    }
    filename, code = scripts[language]
    script = run_dir / filename
    script.write_text(code)
    endpoint = tmp_path / 'isa.sock'
    captured = []
    with LocalListener(endpoint) as listener:
        listener.settimeout(10)
        def serve():
            connection, _ = listener.accept()
            with connection, connection.makefile('rb') as stream:
                request = json.loads(stream.readline())
                captured.append(request)
                connection.sendall(json.dumps({'status':'committed', 'result':request['arguments']}).encode()+b'\n')
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        command, env = runtime.command({'runtime':language, 'path':str(script)}, run_dir, socket_path=endpoint)
        result = run_process(command, env=env, cwd=run_dir, capture_output=True, text=True, timeout=10)
        thread.join(2)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'text':'space value'}
    assert captured == [{'instruction':'Echo','arguments':{'text':'space value'}}]
    assert not thread.is_alive()
