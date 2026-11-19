"""Telepus settings and instance-owned bridge lifecycle."""
from __future__ import annotations

import subprocess
import time
from typing import Any

from management_common.operation_errors import OperationError
from management_common.instance_manifest import load
from scripts import instance_services


def setting(name: str, default: Any = None) -> Any:
    return load().site_settings().get('deployment', {}).get('telepus', {}).get(name, default)


COMPONENTS = ('telepus-portal-refresh', 'telepus-telegram-router', 'telepus-weixin-router')
WEIXIN_COMPONENTS = ('telepus-portal-refresh', 'telepus-weixin-router')
UNITS = tuple(component + '.service' for component in COMPONENTS)
CONTROL_ERRORS = (OSError, RuntimeError, ValueError, subprocess.SubprocessError)


class TelepusRuntime:
    def __init__(self, *, root=None):
        self.root = load(root).root

    def status(self, components=COMPONENTS) -> dict[str, Any]:
        rows = []
        try:
            if instance_services.platform_name() not in {'linux', 'macos'}:
                raise RuntimeError('Telepus 服务开关需要 Linux 或 macOS')
            instance = load(self.root)
            control = instance_services.controller(instance)
            for component in components:
                registered = component in instance_services.components(instance)
                name = instance_services.unit_name(instance, component) if registered else component
                state = control.status(name) if registered else {}
                loaded = state.get('load') == 'loaded'
                rows.append({'name': name, 'loaded': loaded,
                             'active': state.get('active') == 'active',
                             'enabled': bool(loaded and control.is_enabled(name))})
            missing = [row['name'] for row in rows if not row['loaded']]
            running = all(row['active'] for row in rows)
            return {'available': True, 'enabled': any(r['enabled'] or r['active'] for r in rows),
                    'running': running, 'complete': not missing and running and all(r['enabled'] for r in rows),
                    'missing': missing, 'units': rows, 'error': ''}
        except CONTROL_ERRORS as exc:
            return {'available': False, 'enabled': False, 'running': False, 'complete': False,
                    'missing': [], 'units': rows, 'error': str(exc)}

    def ensure_weixin(self):
        return self._set_enabled(True, WEIXIN_COMPONENTS)

    def set_enabled(self, enabled: bool):
        return self._set_enabled(enabled, COMPONENTS)

    def _set_enabled(self, enabled, components):
        try:
            instance = load(self.root)
            if instance_services.platform_name() not in {'linux', 'macos'}:
                raise RuntimeError('Telepus 服务开关需要 Linux 或 macOS')
            if enabled:
                blocker = instance_services.start_blocker(instance)
                if blocker:
                    raise RuntimeError(blocker)
                for component in components:
                    # Registration locks configuration and verifies definition ownership.
                    # Retry repairs a failed definition write without duplicate processes.
                    instance_services.register_component(self.root, component)
            instance = load(self.root)
            selected = tuple(c for c in components if c in instance_services.components(instance))
            with instance_services.configuration_lock(self.root):
                instance_services.verify_definitions(instance, exact=enabled, selected_components=selected)
                control = instance_services.controller(instance)
                for component in selected if enabled else reversed(selected):
                    name = instance_services.unit_name(instance, component)
                    if control.status(name)['load'] == 'loaded':
                        control.set_enabled(name, enabled)
                # launchd bootstrap returns before the child reaches running.
                deadline = time.monotonic() + 5
                while True:
                    after = self.status(components)
                    if after['available'] and (after['complete'] if enabled else not after['enabled']):
                        return after
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Telepus 服务未就绪，请在服务管理中重试启用')
                    time.sleep(0.1)
        except CONTROL_ERRORS as exc:
            raise OperationError('Telepus 消息服务启动或停止失败：' + str(exc),
                                 code='telepus.service_control_failed', http_status=503) from exc
