"""Windows startup imports and progress work without Unix modules or UTF-8 mode.

:module: operations.deployment
:importance: P0
"""
import builtins
import io
from pathlib import Path
import runpy
import sys


def test_resource_startup_dependencies_do_not_require_unix_locks(monkeypatch):
    original = builtins.__import__

    def no_unix(name, *args, **kwargs):
        if name == 'fcntl':
            raise ImportError('Windows has no fcntl')
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', no_unix)
    root = Path(__file__).resolve().parents[2] / 'management_portal'
    for name in ('home_report_store', 'home_report_explanations',
                 'home_report_persistent_media', 'home_explanation_media'):
        runpy.run_path(str(root / (name + '.py')))


def test_native_smoke_worker_owns_utf8_console(monkeypatch, tmp_path):
    from scripts.windows import native_smoke
    output, errors = io.BytesIO(), io.BytesIO()
    monkeypatch.setattr(sys, 'stdout', io.TextIOWrapper(output, encoding='cp1252'))
    monkeypatch.setattr(sys, 'stderr', io.TextIOWrapper(errors, encoding='cp1252'))
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setattr(sys, 'argv', ['native_smoke', '--platform', str(tmp_path), '--phase', 'initialize'])
    monkeypatch.setattr(native_smoke, 'worker', lambda *args: print('正在准备中文案例', flush=True))
    native_smoke.main()
    assert output.getvalue().decode('utf-8').strip() == '正在准备中文案例'


def test_packaged_runtime_uses_supported_non_wmi_platform_detection(tmp_path):
    from scripts.windows.build import prepare_python_runtime
    dlls = tmp_path / 'DLLs'
    dlls.mkdir()
    (dlls / '_wmi.pyd').write_bytes(b'optional COM query extension')
    (dlls / '_socket.pyd').write_bytes(b'required networking')
    prepare_python_runtime(tmp_path)
    assert not (dlls / '_wmi.pyd').exists()
    assert (dlls / '_socket.pyd').read_bytes() == b'required networking'
