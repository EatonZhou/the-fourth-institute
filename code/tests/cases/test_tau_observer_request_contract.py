"""Production TAU observer request contracts, without browser transport claims.

:module: operations.test-framework
:design: decision_c953eab654cd
"""
from pathlib import Path
import subprocess


def _run_contract(name):
    script = Path(__file__).with_name("javascript") / "tau_observer_request_contract.cjs"
    result = subprocess.run(
        ["node", str(script), name], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == f"contract passed: {name}"


def test_resource_failures_preserve_probe_identity_without_content():
    """资源失败观察保留探测身份并排除业务内容

    Stable contract test_47a13dd7cb23; real transport remains in browser coverage.

    :design-point:
    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    _run_contract("resource")


def test_resident_lifecycle_failures_correlate_without_business_content():
    """Resident生命周期失败请求关联与内容脱敏

    Stable contract test_2ae1a6fbf325; real transport remains in browser coverage.

    :design-point:
    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    _run_contract("resident")


def test_injected_observer_preserves_routes_and_unique_transport_identity():
    """注入观察器在独立环境中保持路由脱敏与唯一传输关联

    :design: decision_c953eab654cd
    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    _run_contract("injected")
