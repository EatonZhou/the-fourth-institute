"""
:importance: P1
:aspects: functional, behavior
"""
import copy
import hashlib
import json
import os
import threading
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest import mock
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

from management_portal import resources, serve_management_portal as portal
from management_portal import http_auth, web_resources
from management_portal.resources import ResourceService
from management_portal.resources.collaboration_registry import COLLABORATION_DESCRIPTOR_SCHEMA, CollaborationDescriptorStore
from management_portal.resources.errors import ResourceOperationError
from management_portal.resources.providers import install_resolvers
from management_portal.resources.registration import ResourceRegistrationStore
from management_portal.resources.server import ResourceServiceServer
from management_portal.resources.transport import ResourceServiceClient


WORKSPACE = "workspace_0123456789ab"
RESIDENT = "resident_0123456789ab"
ADDRESS = WORKSPACE + "/file/sample/.wall/plans/设计 plan.md"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    file = root / "sample/.wall/plans/设计 plan.md"
    file.parent.mkdir(parents=True)
    file.write_text("# Verified report\n", encoding="utf-8")
    descriptors = CollaborationDescriptorStore()
    descriptor = {
        "schema": COLLABORATION_DESCRIPTOR_SCHEMA, "resource_id": WORKSPACE,
        "title": "Fixture Workspace", "status": "active", "attributes": {"path": str(root)},
    }
    from management_portal.resources.workspace_store import get_workspace_store
    get_workspace_store().ensure(WORKSPACE, str(root), title="Fixture Workspace")
    record = {
        "resource_id": WORKSPACE, "resource_type": "workspace", "title": "Fixture Workspace",
        "source": {}, "facets": ["workspace", "collaboration"],
        "attributes": {"path": str(root), "status": "active"}, "url": "/r/" + WORKSPACE,
    }
    index = mock.Mock()
    index.indexed_types.return_value = frozenset({"workspace"})
    index.has_indexed_type.side_effect = lambda kind: kind == "workspace"
    index.get_resource.side_effect = lambda *_args, **_kwargs: copy.deepcopy(record)
    service = ResourceService(
        index_provider=index,  collaboration_store=descriptors,
        registration_store=ResourceRegistrationStore(),
        resolver_loader=install_resolvers,
    )
    # The permission system is on; alice and RESIDENT reach contributor on the Workspace.
    from management_portal import permission_system
    grants = {("alice", WORKSPACE): "@contributor", (RESIDENT, WORKSPACE): "@contributor"}
    # The default A tree nests @viewer under @contributor under @admin, and maps
    # file reads (ReadResource, ResolveResource) to @viewer and writes (UpdateWorkspaceFile) to @contributor.
    covering = {"@viewer": {"@viewer", "@contributor", "@admin"}, "@contributor": {"@contributor", "@admin"}}
    needs = {"ReadResource": "@viewer", "ResolveResource": "@viewer", "UpdateWorkspaceFile": "@contributor"}

    class Granted:
        """The permission system answering by the A nodes this case grants on the object holding the file."""

        def permitted(self, actor, instruction, parameter, resource_ids, *, batch):
            for item in resource_ids:
                yield item, grants.get((actor, item)) in covering[needs[instruction]]

        def readable_ids(self, instruction, parameter, identifiers, *, actor, entry="web"):
            return {item for item, allowed in self.permitted(actor, instruction, parameter, identifiers, batch=0) if allowed}

    monkeypatch.setattr(permission_system, "configured_mode", lambda: permission_system.PAR)
    monkeypatch.setattr(permission_system, "_IMPLEMENTATION", Granted())
    return SimpleNamespace(service=service, root=root, file=file, record=record, descriptors=descriptors, descriptor=descriptor,
                           grants=grants)


@pytest.mark.parametrize("actor", ["alice", RESIDENT])
def test_registered_workspace_reference_and_live_content(workspace, actor):
    """User 与 Resident 通过 Workspace 文件地址读取实时内容

    验证规范 Workspace 文件地址的 resolve、reference 与 read_content 对 User 和 Resident 一致可用，引用保留相对路径与内容链接且不暴露 resolved_path，磁盘直接修改立即可读。
    """
    service = workspace.service
    resolved = service.resolve(ADDRESS, actor=actor)
    assert resolved["exists"] is True
    assert resolved["target_kind"] == "file"
    assert resolved["relative_path"] == "sample/.wall/plans/设计 plan.md"
    assert not resolved.get("resolved_path")
    reference = service.reference(ADDRESS, actor=actor)
    assert reference["resource_id"] == WORKSPACE
    assert reference["title"] == "设计 plan.md"
    assert reference["url"] == "/api/resources/content?" + urlencode({"address": ADDRESS})
    assert reference["media_type"] == "text/markdown"
    assert reference["content_size"] == len(b"# Verified report\n")
    assert reference["content_kind"] == ""
    data, content_type, _ = service.read_content(ADDRESS, actor=actor)
    assert data == b"# Verified report\n"
    assert content_type == "text/markdown"
    workspace.file.write_text("Changed directly on disk", encoding="utf-8")
    assert service.read_content(ADDRESS, actor=actor)[0] == b"Changed directly on disk"


def test_workspace_root_never_reads_descriptor_yaml(workspace):
    """Workspace 根地址不返回描述符文件内容

    验证 Workspace 根地址可解析且不暴露 resolved_path，直接读取字节内容明确拒绝，不把注册描述符作为工作区内容返回。
    """
    root = workspace.service.resolve(WORKSPACE, actor="alice")
    assert root["exists"] and not root.get("resolved_path")
    with pytest.raises(ValueError, match="root has no byte content"):
        workspace.service.read_content(WORKSPACE, actor="alice")


@pytest.mark.parametrize("operation", ["resolve", "reference", "read_content"])
def test_workspace_requires_a_reading_actor(workspace, operation):
    """读取 Workspace 文件须有可读的调用者，按登记记录读取

    :importance: P2
    :aspects: functional
    """
    # A registered Workspace is read from its descriptor; an actor who cannot read it does not see it.
    for actor in ("", "bob"):
        with pytest.raises(KeyError):
            getattr(workspace.service, operation)(ADDRESS, actor=actor)
    assert getattr(workspace.service, operation)(ADDRESS, actor="alice")


@pytest.mark.parametrize("operation", ["resolve", "reference", "read_content"])
def test_workspace_files_need_viewer_on_the_workspace(workspace, operation):
    """Workspace 文件读取按 ReadResource 对 Workspace 本身的要求鉴权，不借用绑定它的 Resident

    :importance: P2
    :aspects: functional
    """
    # bob reads only once he holds @viewer on the Workspace; what he holds on a
    # binding Resident does not count, and a denied read looks like a missing file.
    read = lambda: getattr(workspace.service, operation)(ADDRESS, actor="bob")
    workspace.grants[("bob", RESIDENT)] = "@admin"
    with pytest.raises(KeyError):
        read()
    workspace.grants[("bob", WORKSPACE)] = "@viewer"
    assert read()


def test_workspace_files_inside_a_personal_ama_folder_answer_only_to_that_ama(workspace, monkeypatch):
    """权限系统关闭时，个人 AMA 文件夹中的 Workspace 文件只对该 AMA 的所有者及代表所有者的 Resident 开放

    关闭时其余一律放行，这是保留的唯一一条规则；其他主体读取时 Workspace 对其不存在。

    :importance: P2
    :aspects: functional
    """
    from management_portal import permission_system
    from management_portal.society_service import lineage_access_policy
    from management_portal.resources import isa as resource_isa

    monkeypatch.setattr(permission_system, "configured_mode", lambda: permission_system.OPEN)
    ama_lineage, ama = "lineage_aaaaaaaaaaaa", "resident_aaaaaaaaaaaa"
    workspace.descriptors.put({"schema": COLLABORATION_DESCRIPTOR_SCHEMA, "resource_id": ama, "title": "AMA",
                               "status": "active", "attributes": {"personal_owner_id": "owner"}})
    # The Workspace is the AMA's folder.
    monkeypatch.setattr(lineage_access_policy, "personal_folders", lambda: [(str(workspace.root), ama_lineage, "owner")], raising=False)
    monkeypatch.setattr(resource_isa, "_service", lambda: workspace.service)
    monkeypatch.setattr(workspace.service, "notify_source_changed", lambda **_: None, raising=False)
    write = lambda actor: resource_isa.update_workspace_file(SimpleNamespace(
        argument={"address": ADDRESS, "content": f"# {actor}\n"}.get, caller=actor, expected_version=None,
        name="UpdateWorkspaceFile"))

    # The Workspace is unknown to alice.
    with pytest.raises(KeyError):
        workspace.service.reference(ADDRESS, actor="alice")
    with pytest.raises(KeyError):
        write("alice")
    assert workspace.file.read_text(encoding="utf-8") == "# Verified report\n"
    assert workspace.service.read_content(ADDRESS, actor="owner")
    write("owner")
    assert workspace.file.read_text(encoding="utf-8") == "# owner\n"
    # The AMA's own Resident represents the owner.
    assert workspace.service.read_content(ADDRESS, actor=ama)
    write(ama)
    assert workspace.file.read_text(encoding="utf-8") == f"# {ama}\n"


@pytest.mark.parametrize("state", ["file_removed", "workspace_deleted", "descriptor_removed"])
def test_unavailable_files_are_not_recovered_from_stale_index(workspace, state):
    """Workspace 文件或登记删除后不使用陈旧索引恢复内容

    验证文件删除、Workspace 标记删除或描述符消失后，reference 与 read_content 都返回不存在，即使索引仍保存旧记录。
    """
    if state == "file_removed":
        workspace.file.unlink()
    elif state == "workspace_deleted":
        from management_portal.resources.workspace_store import get_workspace_store
        store = get_workspace_store()
        store.database.write(lambda c: store.mark_deleted(WORKSPACE, deleted_at="2026-09-15T00:00:00Z", deleted_by="tester", connection=c))
    else:
        from management_portal.resources.workspace_store import get_workspace_store
        store = get_workspace_store()
        store.database.write(lambda c: c.execute(f"DELETE FROM {store.table} WHERE workspace_id=%s", (WORKSPACE,)))
    for operation in ("reference", "read_content"):
        with pytest.raises((KeyError, FileNotFoundError)):
            getattr(workspace.service, operation)(ADDRESS, actor="alice")


@pytest.mark.parametrize("suffix", ["file/../outside.txt", "file/%2e%2e/outside.txt", "file//etc/passwd", "file/./sample", "file/sample/../../outside.txt", "program/item"])
def test_invalid_workspace_file_addresses_are_rejected(workspace, suffix):
    """Workspace 文件地址拒绝路径穿越与非法子地址

    验证明文或编码的上级路径、绝对路径、点路径、越界组合、错误子地址类型与缺少文件路径均被拒绝。

    :module: resource.identity
    :importance: P0
    """
    with pytest.raises(ValueError):
        workspace.service.read_content(WORKSPACE + "/" + suffix, actor="alice")


@pytest.mark.parametrize("kind", ["file_link", "directory_link", "root_link", "fifo"])
def test_only_regular_files_under_non_symlink_directories_are_readable(workspace, kind):
    """Workspace 内容读取拒绝符号链接与非普通文件

    验证文件、父目录或 Workspace 根的符号链接均不可用于读取，FIFO 也被拒绝，防止越界读取或阻塞。

    :importance: P0
    """
    outside = workspace.root.parent / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("must never be returned")
    target = workspace.root / "target"
    if kind == "file_link":
        target.symlink_to(outside / "secret.md")
    elif kind == "directory_link":
        target.symlink_to(outside, target_is_directory=True)
    elif kind == "root_link":
        moved = workspace.root.with_name("original")
        workspace.root.rename(moved)
        workspace.root.symlink_to(outside, target_is_directory=True)
    else:
        os.mkfifo(target)
    relative = "target/secret.md" if kind == "directory_link" else "secret.md" if kind == "root_link" else "target"
    with pytest.raises(ValueError):
        workspace.service.read_content(WORKSPACE + "/file/" + relative, actor="alice")


def test_symlink_swap_between_resolution_and_read_is_rejected(workspace, monkeypatch):
    """Workspace 解析后替换符号链接仍无法越界读取

    验证资源解析完成后目标文件被替换为指向外部文件的符号链接时，read_content 重新检查并拒绝读取。

    :importance: P0
    """
    resolve = workspace.service.resolve
    outside = workspace.root.parent / "secret.md"
    outside.write_text("must never be returned")

    def swap_after_resolution(*args, **kwargs):
        resolved = resolve(*args, **kwargs)
        workspace.file.unlink()
        workspace.file.symlink_to(outside)
        return resolved

    monkeypatch.setattr(workspace.service, "resolve", swap_after_resolution)
    with pytest.raises(ValueError, match="symlink"):
        workspace.service.read_content(ADDRESS, actor="alice")


@pytest.mark.parametrize("invalid_root", ["", ".", "/", "//", "relative/root"])
def test_invalid_registered_root_has_explicit_configuration_error(workspace, invalid_root):
    """Workspace 非法注册根路径返回明确配置错误

    验证空路径、当前目录、文件系统根与相对路径等非法注册根均返回 workspace_content_root_invalid，HTTP 状态为 500。

    :aspects: functional
    """
    from management_portal.resources.workspace_store import get_workspace_store
    with pytest.raises(ValueError):
        get_workspace_store().ensure(WORKSPACE, invalid_root, move=True)
    assert workspace.service.read_content(ADDRESS, actor="alice")[0] == b"# Verified report\n"


def test_current_registered_directory_overrides_stale_index_path(workspace):
    """Workspace 读取遵循最新注册目录

    验证 Workspace 注册目录迁移后，即使索引仍指向旧路径，read_content 也从当前描述符登记的新目录读取文件。
    """
    new_root = workspace.root.with_name("relocated")
    new_file = new_root / "sample/.wall/plans/设计 plan.md"
    new_file.parent.mkdir(parents=True)
    new_file.write_text("current directory")
    from management_portal.resources.workspace_store import get_workspace_store
    get_workspace_store().ensure(WORKSPACE, str(new_root), move=True)
    assert workspace.record["attributes"]["path"] == str(workspace.root)
    assert workspace.service.read_content(ADDRESS, actor="alice")[0] == b"current directory"


def test_descriptor_writer_cannot_bypass_typed_workspace_store(workspace):
    """协作描述写入不能绕过 WorkspaceStore 修改 Workspace 记录

    :module: resource.identity
    :importance: P2
    """
    with pytest.raises(ValueError, match="WorkspaceStore"):
        workspace.descriptors.put({**workspace.descriptor, "attributes": {}})


def test_content_size_and_binary_payload(workspace, monkeypatch):
    """Workspace 二进制内容保持原字节并遵守大小限制

    验证 PNG 字节与媒体类型准确返回，文件大小恰好达到 max_bytes 时可读，超过限制时拒绝。

    :aspects: functional
    """
    file = workspace.root / "image.png"
    file.write_bytes(b"\x89PNG\x00\xff")
    address = WORKSPACE + "/file/image.png"
    data, media, _ = workspace.service.read_content(address, actor="alice", max_bytes=6)
    assert (data, media) == (b"\x89PNG\x00\xff", "image/png")
    with pytest.raises(ValueError, match="content limit"):
        workspace.service.read_content(address, actor="alice", max_bytes=5)
    video = workspace.root / "clip.mp4"
    video.write_bytes(b"0123456789")
    video_address = WORKSPACE + "/file/clip.mp4"
    resolved = workspace.service.resolve(video_address, actor="alice")
    assert not resolved.get("resolved_path")
    assert resolved["content_size"] == 10
    assert type(resolved["content_mtime_ns"]) is int
    data, content_type, current = workspace.service.read_content_range(
        video_address,
        actor="alice",
        start=2,
        end=5,
    )
    assert data == b"2345"
    assert content_type == "video/mp4"
    assert current["content_size"] == 10


def test_file_growth_after_size_check_is_still_bounded(workspace, monkeypatch):
    """Workspace 文件通过大小检查后增长仍受读取上限约束

    验证文件在初始大小检查后、实际读取前追加内容时，读取仍以 max_bytes 为上限并明确报错。
    """
    fdopen = os.fdopen

    def append_before_read(*args, **kwargs):
        with workspace.file.open("ab") as file:
            file.write(b"exceeds the initial size")
        return fdopen(*args, **kwargs)

    monkeypatch.setattr(os, "fdopen", append_before_read)
    with pytest.raises(ValueError, match="content limit"):
        workspace.service.read_content(ADDRESS, actor="alice", max_bytes=len(b"# Verified report\n"))


def test_resident_isa_read_uses_the_same_resource_reader_with_its_own_identity(workspace, monkeypatch):
    """Resident 经 ISA 读取资源时走同一资源读取器并以自身身份鉴权

    :importance: P2
    :aspects: functional
    """
    from management_portal.resources import isa as resource_isa

    readers = []
    read_content = workspace.service.read_content

    def observed(address, *, actor, **options):
        readers.append(actor)
        return read_content(address, actor=actor, **options)

    monkeypatch.setattr(workspace.service, "read_content", observed)
    monkeypatch.setattr(resource_isa, "_service", lambda: workspace.service)
    arguments = {"address": ADDRESS}
    result = resource_isa.read_resource(SimpleNamespace(argument=arguments.get, caller=RESIDENT))
    assert result["size"] == len(b"# Verified report\n")
    assert readers == [RESIDENT]


def test_absolute_paths_map_to_the_address_of_the_workspace_holding_them(workspace, tmp_path):
    """绝对路径换成登记 Workspace 中的文件地址，链接文件和 Watchboard 配置用它交给 UpdateWorkspaceFile

    文件在登记 Workspace 内时给出 <workspace_id>/file/<相对路径>；Workspace 根本身和任何登记 Workspace 之外的路径给出空串。
    """
    from management_portal.society_service.lineage_access_policy import workspace_file_address

    assert workspace_file_address(str(workspace.file)) == ADDRESS
    assert workspace_file_address(str(workspace.root)) == ""
    outside = tmp_path / "outside.md"
    outside.write_text("x", encoding="utf-8")
    assert workspace_file_address(str(outside)) == ""


def test_read_resource_tells_whether_a_workspace_file_save_passes(workspace, monkeypatch):
    """Workspace 文件的读取结果带 allows.UpdateWorkspaceFile：保存按钮按它实际调用的指令对这个文件判定

    能写入的调用者得到 true，只能读取的得到 false；全文读取和预览都给出，目录不给。
    """
    from management_portal.resources import isa as resource_isa

    monkeypatch.setattr(resource_isa, "_service", lambda: workspace.service)
    workspace.grants[("bob", WORKSPACE)] = "@viewer"
    read = lambda actor, address=ADDRESS, **extra: resource_isa.read_resource(SimpleNamespace(  # noqa: E731
        argument={"address": address, **extra}.get, caller=actor, name="ReadResource"))
    assert read("alice")["allows"] == {"UpdateWorkspaceFile": True}
    assert read("bob")["allows"] == {"UpdateWorkspaceFile": False}
    assert read("alice", preview=True)["allows"] == {"UpdateWorkspaceFile": True}
    assert "allows" not in read("alice", WORKSPACE + "/file/sample")


def test_update_workspace_file_compares_the_version_read_resource_returned(workspace, monkeypatch):
    """UpdateWorkspaceFile 以读取时返回的内容版本做比对，版本过期时拒绝写入

    :importance: P2
    :aspects: functional
    """
    from management_portal.hatg.contracts import InstructionError
    from management_portal.resources import isa as resource_isa

    monkeypatch.setattr(resource_isa, "_service", lambda: workspace.service)
    monkeypatch.setattr(workspace.service, "notify_source_changed", lambda **_: None, raising=False)

    def invocation(arguments, expected=None):
        return SimpleNamespace(argument=arguments.get, caller="alice", expected_version=expected,
                               name="UpdateWorkspaceFile")

    read = resource_isa.read_resource(SimpleNamespace(argument={"address": ADDRESS}.get, caller="alice"))
    assert read["version"] == hashlib.sha256(b"# Verified report\n").hexdigest()
    written = resource_isa.update_workspace_file(
        invocation({"address": ADDRESS, "content": "# Revised\n"}, {ADDRESS: read["version"]}),
    )
    assert written["version"] == hashlib.sha256(b"# Revised\n").hexdigest()
    with pytest.raises(InstructionError) as stale:
        resource_isa.update_workspace_file(invocation({"address": ADDRESS, "content": "# Late\n"}, read["version"]))
    assert stale.value.code == "instruction.version_conflict"
    assert resource_isa.read_resource(SimpleNamespace(argument={"address": ADDRESS}.get, caller="alice"))["text"] == "# Revised\n"


def test_update_workspace_file_refuses_absolute_paths(workspace, monkeypatch):
    """UpdateWorkspaceFile 只接受 Workspace 文件地址，绝对路径即使落在登记 Workspace 内也被拒绝，文件不变

    :importance: P1
    :aspects: functional
    """
    from management_portal.hatg.contracts import InstructionError
    from management_portal.resources import isa as resource_isa

    monkeypatch.setattr(resource_isa, "_service", lambda: workspace.service)
    with pytest.raises(InstructionError) as refused:
        resource_isa.update_workspace_file(SimpleNamespace(
            argument={"address": str(workspace.file), "content": "# Changed\n"}.get, caller="alice",
            expected_version=None, name="UpdateWorkspaceFile"))
    assert refused.value.code == "instruction.invalid_arguments"
    assert workspace.file.read_text(encoding="utf-8") == "# Verified report\n"


def test_update_workspace_file_needs_contributor_on_the_workspace(workspace, monkeypatch):
    """写入 Workspace 文件按 UpdateWorkspaceFile 对 Workspace 本身的要求（contributor）鉴权

    :importance: P2
    :aspects: functional
    """
    from management_portal.hatg.contracts import InstructionError
    from management_portal.resources import isa as resource_isa

    monkeypatch.setattr(resource_isa, "_service", lambda: workspace.service)
    monkeypatch.setattr(workspace.service, "notify_source_changed", lambda **_: None, raising=False)
    write = lambda: resource_isa.update_workspace_file(SimpleNamespace(
        argument={"address": ADDRESS, "content": "# bob\n"}.get, caller="bob", expected_version=None,
        name="UpdateWorkspaceFile"))
    workspace.grants[("bob", RESIDENT)] = "@admin"
    for held in (None, "@viewer"):
        workspace.grants[("bob", WORKSPACE)] = held
        if held is None:
            # A caller without read access must not learn that the Workspace exists.
            with pytest.raises(KeyError, match=WORKSPACE):
                write()
        else:
            with pytest.raises(InstructionError) as denied:
                write()
            assert denied.value.code == "instruction.forbidden"
        assert workspace.file.read_text(encoding="utf-8") == "# Verified report\n"
    workspace.grants[("bob", WORKSPACE)] = "@contributor"
    write()
    assert workspace.file.read_text(encoding="utf-8") == "# bob\n"


@pytest.fixture
def resource_http(workspace, tmp_path, monkeypatch):
    key = tmp_path / "test-rpc.key"
    key.write_bytes(b"isolated-workspace-content-test-signing-key")
    key.chmod(0o600)
    monkeypatch.setenv("PORTAL_RESOURCE_SERVICE_PROCESS", "1")
    monkeypatch.setenv("PORTAL_RESOURCE_RPC_SIGNING_KEY_FILE", str(key))
    monkeypatch.setattr(workspace.service, "status", lambda: {"resource_count": 1})
    rpc = ResourceServiceServer(tmp_path / "resource.sock", service=workspace.service, publisher=mock.Mock())
    worker = threading.Thread(target=rpc.serve_forever, daemon=True)
    worker.start()
    assert rpc.started.wait(2)
    client = ResourceServiceClient(rpc.socket_path, timeout=2)
    monkeypatch.setattr(portal, "get_resource_service", lambda: client)
    monkeypatch.setattr(web_resources, "get_resource_service", lambda: client)
    monkeypatch.setattr(http_auth, "_check_session", lambda _handler: True)
    monkeypatch.setattr(http_auth, "_get_session_username", lambda _handler: "alice")
    http = ThreadingHTTPServer(("127.0.0.1", 0), portal.Handler)
    browser = threading.Thread(target=http.serve_forever, daemon=True)
    browser.start()
    try:
        yield f"http://127.0.0.1:{http.server_port}", client
    finally:
        http.shutdown()
        http.server_close()
        browser.join(2)
        rpc.shutdown()
        rpc.server_close()
        worker.join(2)


def test_authenticated_http_reference_content_download_and_rpc(workspace, resource_http):
    """HTTP 与 RPC 一致提供授权 Workspace 文件引用和下载

    验证已认证 HTTP 引用、内容与附件下载返回真实文件字节，Resident RPC 读取同样内容；根地址下载返回 400，未授权 RPC 读取被拒绝。
    """
    base, client = resource_http
    with urlopen(base + "/api/resources/reference?" + urlencode({"address": ADDRESS})) as response:
        reference = json.load(response)
    assert reference["title"] == "设计 plan.md"
    for download in ("", "1"):
        with urlopen(base + "/api/resources/content?" + urlencode({"address": ADDRESS, "download": download})) as response:
            assert response.status == 200
            assert response.read() == workspace.file.read_bytes()
            # A file a Resident wrote never runs in the Portal origin: not media, so it downloads.
            assert "attachment;" in response.headers["Content-Disposition"]
            assert response.headers["Content-Security-Policy"] == "sandbox"
            assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert client.read_content(ADDRESS, actor=RESIDENT)[0] == workspace.file.read_bytes()
    with pytest.raises(HTTPError) as error:
        urlopen(base + "/api/resources/content?" + urlencode({"address": WORKSPACE, "download": "1"}))
    assert error.value.code == 400
    with pytest.raises(KeyError):
        client.read_content(ADDRESS, actor="bob")


def test_http_uses_provider_for_media_and_preserves_error_statuses(workspace, resource_http, monkeypatch):
    """Workspace HTTP 内容保留 Provider 媒体类型、错误状态与大媒体分段读取

    验证二进制下载使用 Provider 给出的媒体类型，缺失文件或描述符返回 404，非法路径返回 400；不公开真实路径且超过 4 MiB 的 Workspace 视频支持 Range 播放与完整下载。
    """
    base, _client = resource_http
    image = workspace.root / "sample.png"
    image.write_bytes(b"\x89PNG\x00\xff")
    with urlopen(base + "/api/resources/content?" + urlencode({"address": WORKSPACE + "/file/sample.png", "download": "1"})) as response:
        assert response.read() == b"\x89PNG\x00\xff"
        assert response.headers.get_content_type() == "image/png"
    for suffix, expected in [("/file/missing.md", 404), ("/file/../outside.md", 400)]:
        with pytest.raises(HTTPError) as error:
            urlopen(base + "/api/resources/content?" + urlencode({"address": WORKSPACE + suffix}))
        assert error.value.code == expected
    video = workspace.root / "large.mp4"
    payload = b"0123456789abcdef" * (256 * 1024 + 1)
    video.write_bytes(payload)
    address = WORKSPACE + "/file/large.mp4"
    url = base + "/api/resources/content?" + urlencode({"address": address})
    request = Request(url, headers={"Range": "bytes=7-19"})
    with urlopen(request) as response:
        assert response.status == 206
        assert response.headers.get_content_type() == "video/mp4"
        assert response.headers["Accept-Ranges"] == "bytes"
        assert response.headers["Content-Range"] == f"bytes 7-19/{len(payload)}"
        assert response.headers["Content-Disposition"] is None
        assert response.read() == payload[7:20]
    try:
        download_response = urlopen(url + "&download=1")
    except HTTPError as exc:
        pytest.fail(f"download failed with {exc.code}: {exc.read().decode('utf-8', 'replace')}")
    with download_response as response:
        assert response.status == 200
        assert int(response.headers["Content-Length"]) == len(payload)
        assert "attachment;" in response.headers["Content-Disposition"]
        assert response.read() == payload
    from management_portal.resources.workspace_store import get_workspace_store
    store = get_workspace_store()
    store.database.write(lambda c: c.execute(f"DELETE FROM {store.table} WHERE workspace_id=%s", (WORKSPACE,)))
    with pytest.raises(HTTPError) as error:
        urlopen(base + "/api/resources/reference?" + urlencode({"address": ADDRESS}))
    assert error.value.code == 404
