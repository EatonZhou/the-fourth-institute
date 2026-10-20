"""Test cases never write deployed Portal state or reach deployed services.

Cases run from a Resident's Worker inherit its environment (socket routing,
instance sockets). The case root replaces every location a case could
otherwise write in /share/Agents or ~/.tfi, and every owner socket.

:module: operations.test-framework
"""
from __future__ import annotations

import os
from pathlib import Path

from management_portal import avatar_store, resident_avatar, user_avatar
from management_portal.execution.transport import ExecutionServiceClient
from management_portal.hatg.routing import routing_mode
from management_portal.hatg.socket_rpc import owner_socket
from management_portal.society_service import lineage_domain, resident_hatg_credentials
from management_portal.resources.config import resource_service_mode

DEPLOYED = (Path("/share"), Path.home() / ".tfi", Path("/srv/agent-staging/portal_instances"),
            Path(__file__).resolve().parents[2] / "portal_instances")


def _outside_deployed(path: Path) -> bool:
    resolved = Path(path).expanduser().resolve(strict=False)
    return not any(resolved.is_relative_to(root) for root in DEPLOYED)


def test_case_files_stay_in_the_case_root():
    """测试用例的文件只写入用例根目录

    Lineage 文件夹、Resident 令牌文件、头像与访问审计在测试中都指向用例根目录，不写入 /share/Agents 或 ~/.tfi。
    """
    locations = {
        "lineage folders": lineage_domain.LINEAGE_FOLDER_ROOT,
        "Resident bearer files": resident_hatg_credentials.TOKEN_ROOT,
        "Resident avatars": Path(os.environ["PORTAL_RESIDENT_AVATAR_ROOT"]),
        "user avatars": Path(os.environ["PORTAL_USER_AVATAR_ROOT"]),
        "Lineage and Project avatars": avatar_store.scope_avatar_store().legacy.root,
    }
    assert {name: str(path) for name, path in locations.items() if not _outside_deployed(path)} == {}
    assert resident_avatar.resident_avatar_store().legacy.root == locations["Resident avatars"]
    assert user_avatar.user_avatar_store().legacy.root == locations["user avatars"]
    assert avatar_store.scope_avatar_store().legacy.root == Path(os.environ["PORTAL_SCOPE_AVATAR_ROOT"])


def test_cases_run_owners_in_process_and_never_reach_deployed_sockets():
    """测试用例不连接已部署的服务

    即使从带 socket 路由的 Worker 环境启动，测试也在进程内运行 HATG owner 与 ResourceService，所有 owner socket 都指向用例根目录。
    """
    assert routing_mode() == "local"
    assert resource_service_mode() == "local"
    sockets = {owner: owner_socket(owner) for owner in ("lineage", "resource", "execution", "web")}
    # ExecutionService clients also try the peer instance's socket.
    sockets.update({f"execution client {index}": path for index, path in enumerate(ExecutionServiceClient().socket_paths)})
    assert {owner: str(path) for owner, path in sockets.items() if not _outside_deployed(path)} == {}
