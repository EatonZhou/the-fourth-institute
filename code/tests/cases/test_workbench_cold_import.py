"""Exercise first-load order in fresh interpreters, before UI imports warm it."""

from pathlib import Path
import subprocess
import sys
import textwrap


ROOT = Path(__file__).resolve().parents[2]


def run_cold(source):
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        cwd=ROOT, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_concurrent_workbench_package_and_store_cold_import():
    """冷启动时并发导入工作台投影包与 Store 不死锁，也不提前加载 service 模块"""
    run_cold('''
        import importlib
        import sys
        import threading
        import traceback
        import time

        # These are already present when ResourceService loads a domain owner.
        import management_portal.resources.runtime

        package_entered = threading.Event()
        store_entered = threading.Event()
        errors = []

        def trace(frame, event, arg):
            path = frame.f_code.co_filename
            if event == 'line' and path.endswith('/workbench_projections/__init__.py') and not package_entered.is_set():
                package_entered.set()
                assert store_entered.wait(3), 'Store never started'
                # Let Store reach its sibling import while the package is held.
                time.sleep(0.1)
            if event == 'line' and path.endswith('/workbench_projections/store.py'):
                store_entered.set()
            return trace

        def load(name):
            try:
                importlib.import_module(name)
            except BaseException:
                errors.append(traceback.format_exc())

        threading.settrace(trace)
        warm = threading.Thread(target=load, args=('management_portal.workbench_projections.tables',), daemon=True)
        warm.start()
        assert package_entered.wait(3)
        read = threading.Thread(target=load, args=('management_portal.workbench_projections.store',), daemon=True)
        read.start()
        for thread in (warm, read):
            thread.join(5)
            assert not thread.is_alive(), 'Cold import deadlocked'
        threading.settrace(None)
        assert not errors, errors
        assert 'management_portal.workbench_projections.service' not in sys.modules
        from management_portal.workbench_projections.store import CORE_BUILTINS, Store
        assert CORE_BUILTINS['development']['path'] == '/development'
        from management_portal.workbench_projections import get_service
        from management_portal.workbench_projections.service import get_service as implementation
        assert get_service is implementation
    ''')


def test_cold_resource_provider_read_preserves_actor_and_errors():
    """冷启动时工作台资源解析器以请求者身份读取内容，并原样抛出 403 授权错误"""
    run_cold('''
        import json
        import sys
        from types import SimpleNamespace
        from management_portal.resources.providers import install_resolvers

        registered = {}
        def register(kind, resolve, *, read_content):
            registered[kind] = (resolve, read_content)

        install_resolvers.for_type(SimpleNamespace(register_resolver=register), 'workbench')
        assert 'management_portal.workbench_projections.service' not in sys.modules
        from management_portal.workbench_projections.store import Store, ProjectionError
        calls = []
        def get(self, resource_id, actor):
            calls.append((resource_id, actor))
            if actor != 'reader':
                raise ProjectionError('projection.forbidden', 'denied', 403)
            return {'id': resource_id, 'title': 'Cold read', 'revision': 20}
        Store.get = get
        Store.__init__ = lambda self: None
        resolve, read = registered['workbench']
        address = 'workbench_75b51c00005e'
        content, mime, metadata = read(resolve(address, actor='reader'))
        assert json.loads(content)['revision'] == 20
        assert mime == 'application/json'
        try:
            resolve(address, actor='other')
        except ProjectionError as error:
            assert error.http_status == 403
        else:
            raise AssertionError('Authorization error was lost')
        assert calls == [(address, 'reader'), (address, 'other')]
    ''')
