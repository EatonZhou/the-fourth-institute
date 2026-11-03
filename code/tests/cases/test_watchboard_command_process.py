"""Watchboard commands preserve the ScriptRun's output and process lifetime.

:design: decision_0571f3741e23
:module: resident.conversation
:risk: no-external-side-effects
:profiles: local.fixture
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from management_common import process_runtime


CODE = Path(__file__).resolve().parents[2]


def command(tmp_path, source):
    (tmp_path / "entry.py").write_text(source, encoding="utf-8")
    inputs = tmp_path / "input.json"
    inputs.write_text(json.dumps({"arguments": {
        "entrypoint": "entry.py", "spec": {}, "prompt": "{}",
    }}), encoding="utf-8")
    env = dict(os.environ, PORTAL_SCRIPT_INPUT=str(inputs),
               PYTHONPATH=str(CODE / "management_portal/society_service/script_runs/clients"))
    return [sys.executable, str(CODE / "scripts/run_watchboard_command.py"),
            str(tmp_path), "status"], env


@pytest.mark.parametrize("exit_code", [0, 7])
def test_command_waits_for_output_and_preserves_exit_code(tmp_path, exit_code):
    argv, env = command(tmp_path, f'''
import sys,time
from pathlib import Path
time.sleep(.3)
print('watchboard stdout', flush=True)
print('watchboard stderr', file=sys.stderr, flush=True)
Path('completed.json').write_text(__import__('json').dumps(sys.argv[1:]))
sys.exit({exit_code})
''')
    result = process_runtime.run_process(argv, env=env, capture_output=True,
                                         text=True, timeout=15)
    assert result.returncode == exit_code
    assert result.stdout.strip() == "watchboard stdout"
    assert result.stderr.strip() == "watchboard stderr"
    assert json.loads((tmp_path / "completed.json").read_text()) == ["status"]


def test_command_timeout_reaps_the_running_entrypoint(tmp_path):
    argv, env = command(tmp_path, '''
import os,time
from pathlib import Path
Path('entry.pid').write_text(str(os.getpid()))
time.sleep(60)
''')
    with pytest.raises(subprocess.TimeoutExpired):
        process_runtime.run_process(argv, env=env, capture_output=True, timeout=2)
    pid = int((tmp_path / "entry.pid").read_text())
    assert not process_runtime.process_alive(pid)
