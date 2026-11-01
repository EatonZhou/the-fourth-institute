"""Test cases never write deployed Portal state or reach deployed services.

Cases run from a Resident's Worker inherit its environment (socket routing,
instance sockets). The case's temporary instance replaces every location a case could
otherwise write in a deployed instance, /share or ~/.tfi, and every owner socket.

:module: operations.test-framework
"""
from __future__ import annotations

from pathlib import Path

from management_portal.execution.transport import ExecutionServiceClient
from management_portal.hatg.routing import routing_mode
from management_portal.hatg.socket_rpc import owner_socket
from management_common.portal_paths import path as portal_path
from management_portal.society_service import resident_hatg_credentials
from management_portal.resources.config import resource_service_mode


def _outside_deployed(path: Path, deployed) -> bool:
    resolved = Path(path).expanduser().resolve(strict=False)
    roots = (Path("/share"), Path.home() / ".tfi", *deployed)
    return not any(resolved.is_relative_to(root) for root in roots)


def test_case_files_stay_in_the_case_root(deployed_instance_roots):
    """测试用例的文件只写入用例根目录

    Lineage 文件夹与 Resident 令牌文件在测试中位于用例的临时实例内（头像在数据库中，不占用文件），不写入已部署实例、/share 或 ~/.tfi。
    """
    locations = {
        "lineage folders": portal_path("lineages"),
        "Resident bearer files": resident_hatg_credentials.TOKEN_ROOT,
    }
    assert {name: str(path) for name, path in locations.items()
            if not _outside_deployed(path, deployed_instance_roots)} == {}


def test_cases_run_owners_in_process_and_never_reach_deployed_sockets(deployed_instance_roots):
    """测试用例不连接已部署的服务

    即使从带 socket 路由的 Worker 环境启动，测试也在进程内运行 HATG owner 与 ResourceService，所有 owner socket 都指向用例根目录。
    """
    assert routing_mode() == "local"
    assert resource_service_mode() == "local"
    sockets = {owner: owner_socket(owner) for owner in ("lineage", "resource", "execution", "web")}
    # ExecutionService clients also try the peer instance's socket.
    sockets.update({f"execution client {index}": path for index, path in enumerate(ExecutionServiceClient().socket_paths)})
    assert {owner: str(path) for owner, path in sockets.items()
            if not _outside_deployed(path, deployed_instance_roots)} == {}
