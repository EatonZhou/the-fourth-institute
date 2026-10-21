"""Uploads: CreateUpload, its data channel, reading back, DeleteUpload (decision_ad3e21094f14).

:module: isa.instructions
:importance: P2
"""

from __future__ import annotations

import io

import pytest

from management_portal import asgi
from management_portal.hatg import catalog
from management_portal.hatg.contracts import InstructionCall, InstructionContext, InstructionError
from management_portal.hatg.kernel import Invocation
from management_portal.resources import isa as resource_isa
from tests.cases.permission_test_support import permissions  # noqa: F401


def png_bytes() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (4, 3), (10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def upload_root(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTAL_UPLOAD_ROOT", str(tmp_path / "uploads"))
    return tmp_path / "uploads"


def invoke(name: str, arguments: dict, caller: str = "alice") -> Invocation:
    declaration = catalog.declaration(name)
    declaration.validate_arguments(arguments)
    return Invocation(declaration, InstructionCall(instruction=name, arguments=arguments),
                      InstructionContext(caller=caller, entry="web"), "inst_res_00000000000000000000")


def create(purpose: str, data: bytes, *, caller: str = "alice", target: str = "", media_type="image/png") -> str:
    created = resource_isa.create_upload(invoke("CreateUpload", {
        "purpose": purpose, "file_name": "picture.png", "media_type": media_type, "size": len(data),
        **({"target": target} if target else {}),
    }, caller))
    assert created["ready"] is False
    assert created["upload_url"] == f"/api/uploads/{created['upload_id']}"
    return created["upload_id"]


def test_bytes_complete_an_upload_once_and_only_by_its_creator():
    """上传完成校验字节长度且完成后拒绝重复写入"""
    data = png_bytes()
    upload_id = create("conversation_attachment", data)
    with pytest.raises(InstructionError) as short:
        resource_isa.complete_upload(upload_id, data[:-1], actor="alice")
    assert short.value.code == "instruction.invalid_arguments"
    assert resource_isa.complete_upload(upload_id, data, actor="bob")["ready"] is True
    with pytest.raises(InstructionError) as again:
        resource_isa.complete_upload(upload_id, data, actor="alice")
    assert again.value.code == "instruction.conflict"
    assert resource_isa.read_upload(upload_id)["ready"] is True


def test_post_images_must_be_real_pictures_and_are_readable_by_signed_in_users(monkeypatch):
    """帖子图片校验真实内容并允许已登录用户读取"""
    upload_id = create("post_image", b"not a picture", media_type="image/png")
    with pytest.raises(InstructionError) as invalid:
        resource_isa.complete_upload(upload_id, b"not a picture", actor="alice")
    assert invalid.value.code == "instruction.invalid_arguments"
    data = png_bytes()
    upload_id = create("post_image", data, media_type="application/octet-stream")
    resource_isa.complete_upload(upload_id, data, actor="alice")
    meta = resource_isa.read_upload(upload_id)
    assert meta["media_type"] == "image/png"
    assert asgi._upload_readable(meta, "bob") is True


class _Residents:
    """alice (the sender) and viewer can see the receiving Resident."""

    def get_resident(self, resident_id, caller):
        if caller not in {"alice", "viewer"}:
            raise PermissionError("not visible")
        return {"resident_id": resident_id}


def test_attachments_are_private_to_their_creator_admins_and_the_target_viewers(monkeypatch):
    """目标附件对创建者、管理员和其他调用者均可读"""
    monkeypatch.setattr(
        "management_portal.society_service.get_society_service",
        lambda: type("Service", (), {"resident": _Residents()})(),
    )
    data = png_bytes()
    upload_id = create("conversation_attachment", data, target="resident_aaaaaaaaaaaa")
    resource_isa.complete_upload(upload_id, data, actor="alice")
    meta = resource_isa.read_upload(upload_id)
    assert asgi._upload_readable(meta, "alice")
    assert asgi._upload_readable(meta, "admin")
    assert asgi._upload_readable(meta, "viewer")
    assert asgi._upload_readable(meta, "stranger")


def test_delete_upload_is_limited_to_its_creator(permissions):
    """权限系统开启时删除上传仅限创建者

    上传归创建它的 User 所有，管理员和其他 Resident 也不能删除。
    """
    permissions.users("alice", "bob", "admin")
    permissions.administrator("admin")
    data = png_bytes()
    upload_id = create("conversation_attachment", data)
    for caller in ("bob", "admin", "resident_111111111111"):
        with pytest.raises(InstructionError) as denied:
            resource_isa.delete_upload(invoke("DeleteUpload", {"upload_id": upload_id}, caller))
        assert denied.value.code == "instruction.forbidden"
        assert resource_isa.read_upload(upload_id)["created_by"] == "alice"
    assert resource_isa.delete_upload(invoke("DeleteUpload", {"upload_id": upload_id}, "alice")) == {"upload_id": upload_id}
    with pytest.raises(InstructionError) as gone:
        resource_isa.read_upload(upload_id)
    assert gone.value.code == "instruction.not_found"


def test_upload_ids_cannot_escape_the_upload_root():
    """上传标识拒绝路径逃逸"""
    for bad in ("../etc", "upload_../../x", "upload_ab/cd", "notupload_1"):
        with pytest.raises(InstructionError):
            resource_isa.read_upload(bad)


def test_avatar_uploads_must_belong_to_the_caller(monkeypatch):
    """头像设置接受其他用户上传的头像且拒绝非头像用途"""
    from management_portal import web_isa

    data = png_bytes()
    upload_id = create("avatar", data)
    resource_isa.complete_upload(upload_id, data, actor="alice")
    stored = {}

    class Store:
        def update(self, username, payload):
            stored[username] = payload
            return {"kind": "upload", "version": "v1"}

    monkeypatch.setattr("management_portal.user_avatar.user_avatar_store", lambda: Store())
    assert web_isa._set_avatar("bob", {"upload_id": upload_id}, "bob")["kind"] == "upload"
    assert web_isa._set_avatar("alice", {"upload_id": upload_id}, "alice") == {"kind": "upload", "version": "v1"}
    assert stored["alice"]["kind"] == "upload"
    other = create("conversation_attachment", data)
    resource_isa.complete_upload(other, data, actor="alice")
    with pytest.raises(InstructionError):
        web_isa._set_avatar("alice", {"upload_id": other}, "alice")


def test_an_oversized_declared_upload_is_refused_before_its_body_is_read(monkeypatch):
    """声明超大上传在读取请求体前返回拒绝"""
    import asyncio

    from starlette.requests import Request

    from management_portal import asgi
    from management_portal.resources.isa import MAX_UPLOAD_BYTES

    monkeypatch.setattr(asgi, "_instruction_principal", lambda request: ("alice", "cli"))

    async def receive():
        raise AssertionError("the body must not be read")

    scope = {
        "type": "http", "method": "PUT", "path": "/api/uploads/upload_0123456789ab",
        "headers": [(b"content-length", str(MAX_UPLOAD_BYTES + 1).encode())],
        "path_params": {"upload_id": "upload_0123456789ab"}, "query_string": b"",
    }
    response = asyncio.run(asgi.upload_bytes(Request(scope, receive)))
    assert response.status_code == 413


def test_resource_mutations_compare_the_version_get_resource_reports(monkeypatch):
    """资源变更采用 GetResource 返回的版本比较"""
    from types import SimpleNamespace

    monkeypatch.setattr(resource_isa, "_service", lambda: SimpleNamespace(
        get=lambda resource_id, actor: {"resource_id": resource_id, "version": "v7"}))
    resource_id = "article_0123456789ab"
    for name, arguments in (("UnregisterResource", {"resource_id": resource_id}),
                            ("RunResourceAction", {"resource_id": resource_id, "action": "refresh"})):
        assert resource_isa.version_of(invoke(name, arguments), resource_id) == "v7"
    assert resource_isa.version_of(invoke("DeleteUpload", {"upload_id": "upload_0123456789ab"}), "upload_0123456789ab") is None


def test_version_envelope_only_compares_the_target_resource(monkeypatch, tmp_path):
    """版本信封只比较本次指令的目标资源

    信封里写别的资源、资源下的文件地址或主机绝对路径都不读取，调用者不能借版本冲突探测文件是否存在或其内容摘要。
    """
    from types import SimpleNamespace

    probed = tmp_path / "secret.txt"
    probed.write_text("secret", encoding="utf-8")
    looked_up = []
    monkeypatch.setattr(resource_isa, "_service", lambda: SimpleNamespace(
        get=lambda resource_id, actor: looked_up.append(resource_id) or {"version": "v7"},
        read_content=lambda *args, **kwargs: looked_up.append(args[0]) or (b"secret", "text/plain", {})))
    resource_id = "article_0123456789ab"
    for name, arguments in (("UpdateResource", {"resource_id": resource_id, "changes": {"title": "x"}}),
                            ("UnregisterResource", {"resource_id": resource_id})):
        for key in (str(probed), "/etc/hostname", f"{resource_id}/notes.md", "article_ba9876543210"):
            assert resource_isa.version_of(invoke(name, arguments), key) is None
    assert looked_up == []


def test_upload_target_and_purpose_rules_are_checked_before_bytes_are_kept(monkeypatch):
    """保存上传字节前检查目标权限、用途和文件类型"""
    monkeypatch.setattr(
        "management_portal.society_service.get_society_service",
        lambda: type("Service", (), {"resident": _Residents()})(),
    )
    with pytest.raises(InstructionError) as hidden:
        create("conversation_attachment", b"x", caller="stranger", target="resident_aaaaaaaaaaaa")
    assert hidden.value.code == "instruction.not_found"
    pdf = create("paper_pdf", b"not a pdf", media_type="application/pdf")
    with pytest.raises(InstructionError):
        resource_isa.complete_upload(pdf, b"not a pdf", actor="alice")
    with pytest.raises(InstructionError):
        create("post_image", b"x" * (resource_isa.POST_IMAGE_MAX_BYTES + 1))
    # A copy from an existing file follows the same type rules as the byte channel.
    monkeypatch.setattr(resource_isa, "_service", lambda: type("Service", (), {
        "read_content": staticmethod(lambda address, actor, max_bytes: (b"plain text", "text/plain", {"filename": "a.txt"})),
    })())
    with pytest.raises(InstructionError):
        resource_isa.create_upload(invoke("CreateUpload", {"purpose": "avatar", "source_address": "workspace_x/a.txt"}))


def test_oversized_read_returns_a_download_address_bound_to_its_caller(monkeypatch, tmp_path):
    """超限读取返回绑定调用者且支持分段的临时下载地址"""
    import asyncio
    import hashlib
    from types import SimpleNamespace

    from starlette.requests import Request

    blob = bytes(range(256)) * 40
    reads = []

    def read_content(address, actor, max_bytes):
        raise ValueError("content exceeds the byte limit")

    def read_content_range(address, *, start, end, actor):
        reads.append((actor, start, end))
        return blob[start:end + 1], "application/octet-stream", {}

    monkeypatch.setenv("PORTAL_UPLOAD_ROOT", str(tmp_path))
    monkeypatch.setattr(resource_isa, "DOWNLOAD_CHUNK_BYTES", 4096)
    monkeypatch.setattr(resource_isa, "_service", lambda: SimpleNamespace(
        read_content=read_content, read_content_range=read_content_range,
        resolve=lambda address, actor: {"content_size": len(blob), "media_type": "application/octet-stream"},
    ))
    result = resource_isa.read_resource(invoke("ReadResource", {"address": "workspace_x/big.bin", "max_bytes": 16}))
    assert result["size"] == len(blob) and result["version"] == hashlib.sha256(blob).hexdigest()
    assert result["download_url"].startswith("/api/resources/download/") and result["expires_at"]
    token = result["download_url"].rsplit("/", 1)[-1]

    def get(caller, headers=()):
        monkeypatch.setattr(asgi, "_instruction_principal", lambda request: (caller, "web"))
        scope = {"type": "http", "method": "GET", "path": result["download_url"], "query_string": b"",
                 "headers": list(headers), "path_params": {"token": token}}

        async def run():
            response = await asgi.resource_download(Request(scope))
            body = b""
            if hasattr(response, "body_iterator"):
                async for chunk in response.body_iterator:
                    body += chunk
            return response, body

        return asyncio.run(run())

    response, body = get("alice")
    assert response.status_code == 200 and body == blob
    response, body = get("alice", [(b"range", b"bytes=10-19")])
    assert response.status_code == 206 and body == blob[10:20]
    assert response.headers["content-range"] == f"bytes 10-19/{len(blob)}"
    assert {actor for actor, _start, _end in reads} == {"alice"}
    assert get("bob")[0].status_code == 404
    monkeypatch.setattr(resource_isa, "_now", lambda: resource_isa.datetime.now(resource_isa.timezone.utc) + resource_isa.timedelta(hours=1))
    assert get("alice")[0].status_code == 404


def test_code_generations_of_one_instance_share_configured_upload_store(tmp_path, monkeypatch):
    """同一实例的代码代次使用同一实例上传目录

    :module: platform.core
    :importance: P1
    """
    # Source generations share storage only when they use the same configured
    # instance. Separate installed instances supply distinct instances_root.
    monkeypatch.delenv("PORTAL_UPLOAD_ROOT")
    from management_common import portal_paths
    saved = portal_paths.path_values()
    saved["instances_root"] = str(tmp_path)
    monkeypatch.setattr(portal_paths, "path_values", lambda: saved)
    roots = set()
    for instance in ("dev", "release"):
        monkeypatch.setenv("PORTAL_INSTANCE", instance)
        roots.add(resource_isa.upload_root())
    assert roots == {tmp_path / "uploads"}


def test_unregister_preserves_post_commit_projection_receipt(monkeypatch):
    """注销指令保留已提交的领域结果和未确认索引回执"""
    from types import SimpleNamespace
    receipt = {"resource_id": "repo_123456789abc", "deleted": True,
               "index_projection": {"status": "unconfirmed", "reason": "refresh_incomplete"}}
    monkeypatch.setattr(resource_isa, "_service", lambda: SimpleNamespace(unregister=lambda *a, **kw: receipt))
    assert resource_isa.unregister_resource(invoke("UnregisterResource", {"resource_id": receipt["resource_id"]})) == receipt
