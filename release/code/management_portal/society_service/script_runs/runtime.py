"""Host interpreters and per-run client paths, without an isolation boundary."""
from __future__ import annotations
from management_common.portal_paths import path as portal_path
from management_common.instance_paths import instance_root
from management_common.site_compatibility import compatibility_value

import os
import shutil
import sys
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[3]
PYTHON = instance_root() / 'release' / 'runtime-venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
RUN_ROOT = (portal_path("staging_root") / "shared" / "script_runs")
PRIVATE_ROOT = Path(compatibility_value("script_run_private_root", str(Path.home() / '.tfi/secrets/script_runs'))).expanduser()
CLIENTS = Path(__file__).parent / 'clients'


def command(config: dict, run_dir: Path, *, socket_path: Path | None = None) -> tuple[list[str], dict[str, str]]:
    # Linux inherits the user manager environment; macOS receives the runner's
    # explicit allowlist. Portal service secrets are not forwarded.
    env = os.environ.copy()
    env.update(PORTAL_SCRIPT_INPUT=str(run_dir / 'io/input.json'),
               PORTAL_SCRIPT_OUTPUT=str(run_dir / 'io/output.json'),
               PORTAL_SCRIPT_ARTIFACTS=str(run_dir / 'io/artifacts'),
               PORTAL_ISA_SOCKET=str(socket_path or run_dir / 'isa.sock'))
    for key, paths in {'PATH': [CLIENTS / 'bin'], 'PYTHONPATH': [CLIENTS, REPOSITORY],
                       'NODE_PATH': [CLIENTS / 'node_modules']}.items():
        env[key] = os.pathsep.join([*(str(p) for p in paths), *([env[key]] if env.get(key) else [])])
    python = str(PYTHON if PYTHON.is_file() else Path(sys.executable))
    env['PORTAL_PYTHON'] = Path(python).as_posix()
    env['PORTAL_LOCAL_IPC_MODULE'] = str(REPOSITORY / 'management_common/local_ipc.cjs')
    interpreters = {'python': python, 'javascript': shutil.which('node'),
                    'bash': shutil.which('bash')}
    interpreter = interpreters[config['runtime']]
    if not interpreter:
        raise FileNotFoundError(f"Interpreter unavailable: {config['runtime']}")
    args = [interpreter]
    if config['runtime'] == 'javascript':
        args += ['--no-warnings', '--experimental-loader', str(CLIENTS / 'loader.mjs')]
    return [*args, config['path'], *config.get('argv', [])], env
