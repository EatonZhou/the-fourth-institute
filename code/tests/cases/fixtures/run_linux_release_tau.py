"""Run the Linux fresh-install acceptance case with unconditional owned cleanup.

This opt-in test installs unique real systemd user services. It is deliberately
not an autouse pytest fixture and never targets the campaign's live Portal.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import time

from linux_release_deployment import environment, run, verify_stopped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--release', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--playwright-module', type=Path, required=True)
    parser.add_argument('--node', default='/usr/bin/node')
    args = parser.parse_args()
    if platform.system() != 'Linux' or platform.machine() != 'x86_64':
        raise SystemExit('This case is qualified for Linux x86_64 only')
    if sys.version_info < (3, 12):
        raise SystemExit('Python 3.12 or later is required')
    base = args.base.resolve()
    if base.exists():
        raise SystemExit('Case requires a new, dedicated short directory')
    fixture = Path(__file__).with_name('linux_release_deployment.py')
    browser = fixture.parent.parent / 'javascript/linux_release_deployment.browser.cjs'
    tool_module = args.playwright_module.resolve()
    if not (tool_module / 'package.json').is_file():
        raise SystemExit('A separate installed Playwright toolchain is required')
    # Prove prerequisites before creating an installation. No production service is changed.
    subprocess.run(['systemctl', '--user', 'show-environment'], stdout=subprocess.DEVNULL, check=True)
    subprocess.run([args.node, '--version'], check=True)
    evidence = {'schema': 'tfi.linux-release-tau.v1', 'passed': False, 'cleanup_passed': False,
                'started_at': time.time(), 'steps': [], 'helper_pins': {
                    str(p.relative_to(fixture.parent.parent)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in (fixture, Path(__file__), browser)}}

    def stage(action):
        command = [sys.executable, str(fixture), action, '--base', str(base)]
        if action == 'prepare':
            command += ['--release', str(args.release.resolve()), '--revision', args.revision]
        code = subprocess.run(command).returncode
        evidence['steps'].append({'action': action, 'exit_code': code})
        if code:
            raise RuntimeError(f'{action} exited {code}; see its evidence directory')

    def browse(phase):
        code = run(base, 'browser-command-' + phase,
                   [args.node, str(browser), str(base), phase, 'browser-' + phase, str(tool_module)],
                   cwd=fixture.parent.parent.parent.parent)
        evidence['steps'].append({'action': 'browser-' + phase, 'exit_code': code})
        if code:
            raise RuntimeError(f'Browser {phase} exited {code}; see its redacted result')

    try:
        stage('prepare')
        stage('create')
        stage('status')
        stage('first-admin')
        browse('bootstrap')
        stage('stop')
        verify_stopped(base, retired=False)
        stage('start')
        browse('restart')
        evidence['passed'] = True
    except Exception as exc:
        evidence['error'] = {'type': type(exc).__name__, 'message': str(exc)}
    finally:
        if (base / 'instance/instance.json').exists():
            # Copy only diagnostic logs; never copy database, credentials or secret links.
            log = base / 'instance/runtime/command-error.log'
            if log.exists():
                target = base / 'evidence/installer-error.log'
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(log.read_bytes())
            try:
                stage('retire')
                verify_stopped(base, retired=True)
                evidence['cleanup_passed'] = True
            except Exception as exc:
                evidence['cleanup_error'] = {'type': type(exc).__name__, 'message': str(exc)}
        else:
            evidence['cleanup_passed'] = not (base / 'instance').exists()
        if base.exists():
            evidence['elapsed_seconds'] = time.time() - evidence['started_at']
            (base / 'case-result.json').write_text(json.dumps(evidence, indent=2) + '\n')
    print(json.dumps({'passed': evidence['passed'], 'cleanup_passed': evidence['cleanup_passed'],
                      'evidence': str(base / 'case-result.json')}))
    return 0 if evidence['passed'] and evidence['cleanup_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
