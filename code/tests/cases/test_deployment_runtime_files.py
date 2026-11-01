"""Each deployment's socket, lock and signing key live in ``runtime/deployments/<deployment>/``.

:module: operations.deployment
:design: decision_d88306d0bd17
:profiles: local.fixture
:risk: no-external-side-effects
"""
import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from management_common import rpc_auth
from tests.cases.instance_test_support import select_instance


@pytest.fixture
def paired(tmp_path, monkeypatch):
    for name in ("PORTAL_DEPLOYMENT", "PORTAL_RESOURCE_RPC_SIGNING_KEY_FILE"):
        monkeypatch.delenv(name, raising=False)
    return select_instance(monkeypatch, tmp_path / "site", dev=True)


@pytest.mark.parametrize("deployment", ["dev", "release"])
def test_a_web_process_serves_its_own_deployments_socket(paired, monkeypatch, deployment):
    """Web 进程的指令 socket 在自己部署的运行目录中，不取自环境变量"""
    from management_portal.hatg.socket_rpc import web_service_socket
    monkeypatch.setenv("PORTAL_DEPLOYMENT", deployment)
    monkeypatch.setenv("PORTAL_WEB_SERVICE_SOCKET", str(paired.root / "elsewhere.sock"))
    assert web_service_socket() == paired.root / "runtime/deployments" / deployment / "web-service.sock"


def test_shared_services_reach_the_web_that_runs_their_code(tmp_path, monkeypatch):
    """共享服务没有部署名，找有 Dev 时的 Dev Web，否则 Release Web"""
    from management_portal.hatg.socket_rpc import web_service_socket
    monkeypatch.delenv("PORTAL_DEPLOYMENT", raising=False)
    pair = select_instance(monkeypatch, tmp_path / "pair", dev=True)
    assert web_service_socket() == pair.root / "runtime/deployments/dev/web-service.sock"
    single = select_instance(monkeypatch, tmp_path / "single")
    assert web_service_socket() == single.root / "runtime/deployments/release/web-service.sock"


@pytest.mark.parametrize("deployment", ["dev", "release"])
def test_the_deployment_signing_key_is_regenerated_beside_its_socket(paired, deployment):
    """签名密钥与 socket 同目录；缺失时由服务重新生成，权限仅限所有者"""
    socket_path = paired.deployment_runtime(deployment) / "web-service.sock"
    key_path = rpc_auth.rpc_signing_key_file(socket_path=socket_path)
    assert key_path == paired.root / "runtime/deployments" / deployment / "resource-rpc-signing.key"
    assert not key_path.exists()
    first = rpc_auth.RpcAuthenticator(socket_path).key
    assert len(first) == 32 and stat.S_IMODE(key_path.stat().st_mode) == 0o600
    assert rpc_auth.RpcAuthenticator(socket_path).key == first
    key_path.unlink()
    assert rpc_auth.RpcAuthenticator(socket_path).key != first


def test_the_two_deployments_have_separate_operation_locks(paired):
    from scripts import portal_services
    assert portal_services._operation_lock_path("dev") == paired.root / "runtime/deployments/dev/portal-services.lock"
    assert portal_services._operation_lock_path("release") == paired.root / "runtime/deployments/release/portal-services.lock"
    with pytest.raises(ValueError, match="unknown Portal deployment"):
        portal_services._operation_lock_path("explore-1")


def test_restart_requests_name_the_deployment(paired):
    """RestartPortal 与 request-restart 的参数是 deployment，只接受 dev 与 release"""
    from scripts import tfi_development_runtime as paired_runtime
    with patch.object(paired_runtime, "management_env", return_value={}), \
            patch.object(paired_runtime, "runtime_python", return_value=Path("/python")), \
            patch.object(paired_runtime.subprocess, "run") as run:
        result = paired_runtime.request_restart(paired.root, "dev", ["web"])
    command = run.call_args.args[0]
    assert command[command.index("--deployment") + 1] == "dev" and "--instance" not in command
    assert result["deployment"] == "dev" and "instance" not in result
    with pytest.raises(ValueError, match="Unknown Web deployment"):
        paired_runtime.request_restart(paired.root, "explore-1")
    assert paired_runtime.restart_components("release", ["web"]) == ["web"]
    assert paired_runtime.restart_components("dev", ["web"]) == ["dev-web"]


def test_status_reports_deployments(paired):
    from scripts import tfi_development_runtime as paired_runtime

    def unreachable(*args, **kwargs):
        raise OSError("not running")
    with patch.object(paired_runtime.urllib.request, "urlopen", unreachable):
        result = paired_runtime.status(paired.root)
    assert [row["deployment"] for row in result["deployments"]] == ["release", "dev"]
    assert "instances" not in result and result["ok"] is False


def test_the_deployment_menu_and_identity_use_deployment_names(paired, monkeypatch):
    """/api/me 与路由偏好接口返回 portal_deployment 与 deployments，不再有 instance 字段"""
    from management_portal.http_portal_session import PortalSessionHttp
    http = PortalSessionHttp(
        respond=lambda request, status, payload: payload, exception_response=None, read_json=None,
        session_actor=lambda request: "alice", access=lambda: None, avatar=lambda actor: None,
        deployment="dev", build_id="b1", preferences=lambda actor: {}, set_preferences=None,
        conversation_payload=None, workspace_adapter=None)
    route = http.router_preference(None, None, "alice")
    assert route["effective_deployment"] == route["default_deployment"] == "dev"
    assert [item["id"] for item in route["deployments"]] == ["dev"]
    assert not {"instances", "effective_instance", "default_instance"} & set(route)


def test_the_preference_cookie_is_named_for_the_deployment():
    from management_portal.portal_deployment_router import DEPLOYMENT_PREFERENCE_COOKIE
    assert DEPLOYMENT_PREFERENCE_COOKIE == "db_portal_deployment"


def test_health_and_setup_status_name_instance_and_deployment(paired, monkeypatch):
    """健康检查报告实例名（站点）和部署名（Dev 或 Release），两者分开"""
    from management_portal import http_instance, http_overview
    monkeypatch.setattr(http_instance, "_PORTAL_DEPLOYMENT", "dev")
    monkeypatch.setattr(http_instance, "_PORTAL_INSTANCE_NAME", "fixture")
    with patch.dict(os.environ, {}):
        payload = http_overview._portal_health_payload()
    assert payload["deployment"] == "dev" and payload["instance"] == "fixture"
