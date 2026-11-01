"""Host identity is available without administrative WMI access.

:module: operations.deployment
:design: decision_d88306d0bd17
:profiles: local.fixture
:risk: no-external-side-effects
"""
import ctypes
from types import SimpleNamespace
import pytest
from management_common import distribution_targets as targets


@pytest.mark.parametrize('machine,expected', [(9, 'x86_64'), (12, 'aarch64'), (0, 'x86'), (99, 'unknown')])
def test_windows_identity_needs_no_wmi(monkeypatch, machine, expected):
    def system_info(buffer):
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ushort))[0] = machine
    monkeypatch.setattr(targets.sys, 'platform', 'win32')
    monkeypatch.setattr(ctypes, 'windll', SimpleNamespace(kernel32=SimpleNamespace(GetNativeSystemInfo=system_info)), raising=False)
    monkeypatch.setattr(targets.sys, 'getwindowsversion', lambda: SimpleNamespace(major=10, minor=0, build=26100), raising=False)
    def forbidden():raise AssertionError('WMI-dependent platform query')
    for name in ('machine', 'system', 'platform'):
        monkeypatch.setattr(targets.platform, name, forbidden)
    assert targets.identify_host() == {'platform': 'windows', 'architecture': expected, 'os_version': 'Windows-10.0.26100'}
