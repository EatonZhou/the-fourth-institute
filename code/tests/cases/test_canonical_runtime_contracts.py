from __future__ import annotations

import ast
import io
from email.message import Message
from pathlib import Path
from types import SimpleNamespace

import pytest

from management_portal import paper_backend, serve_management_portal
from management_portal.execution.harness import AgentHarness
from management_portal.execution.harness_settings import normalize_agent_harness_settings
from management_portal.serve_management_portal import _read_request_multipart


def _multipart_handler(body: bytes, boundary: str = "portal-boundary") -> SimpleNamespace:
    headers = Message()
    headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    headers["Content-Length"] = str(len(body))
    return SimpleNamespace(headers=headers, rfile=io.BytesIO(body))


def test_multipart_parser_preserves_text_and_binary_parts_contracts(subtests, isolated_portal_case):
    """分段表单保留文本与二进制且 Harness 拒绝无效模式"""
    with subtests.test(contract='test_multipart_parser_preserves_text_and_binary_parts'), isolated_portal_case() as case_path:
        boundary = "portal-boundary"
        body = (
            b"--portal-boundary\r\n"
            b'Content-Disposition: form-data; name="directory"\r\n\r\n'
            b"figures\r\n"
            b"--portal-boundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename="plot.bin"\r\n'
            b"Content-Type: application/octet-stream\r\n\r\n"
            b"\x00\xffportal\r\n"
            b"--portal-boundary--\r\n"
        )

        payload = _read_request_multipart(
            _multipart_handler(body, boundary),
            max_bytes=1024,
        )

        assert payload == {
            "directory": "figures",
            "filename": "plot.bin",
            "content_type": "application/octet-stream",
            "data_bytes": b"\x00\xffportal",
        }
    with subtests.test(contract='test_multipart_parser_rejects_truncated_body'), isolated_portal_case() as case_path:
        handler = _multipart_handler(b"--portal-boundary--\r\n")
        handler.headers.replace_header(
            "Content-Length",
            str(int(handler.headers["Content-Length"]) + 1),
        )

        with pytest.raises(ValueError, match="incomplete multipart"):
            _read_request_multipart(handler, max_bytes=1024)
    with subtests.test(contract='test_agent_harness_runtime_contract_requires_explicit_mode'), isolated_portal_case() as case_path:
        harness = AgentHarness()
        invocation = SimpleNamespace(
            request=SimpleNamespace(
                runtime_options={"agent_harness": {"features": {}}},
            ),
        )

        with pytest.raises(ValueError, match="require mode"):
            harness._config(invocation)
    with subtests.test(contract='test_agent_harness_settings_reject_unknown_mode'), isolated_portal_case() as case_path:
        with pytest.raises(ValueError, match="unsupported AgentHarness mode"):
            normalize_agent_harness_settings({"mode": "automatic"})


def test_multipart_parser_rejects_oversized_body_before_reading() -> None:
    """分段表单超出容量限制时在读取前拒绝"""
    class RecordingStream:
        def __init__(self) -> None:
            self.read_sizes: list[int] = []

        def read(self, size: int = -1) -> bytes:
            self.read_sizes.append(size)
            return b""

    handler = _multipart_handler(b"")
    handler.headers.replace_header("Content-Length", str(512 * 1024 * 1024))
    handler.rfile = RecordingStream()

    with pytest.raises(ValueError, match="too large"):
        _read_request_multipart(handler, max_bytes=1024)

    assert handler.rfile.read_sizes == []


class _PostHarness(serve_management_portal.Handler):

    def __init__(self, path: str, *, content_length: int) -> None:
        self.path = path
        self.command = "POST"
        self.headers = Message()
        self.headers["Content-Type"] = (
            "multipart/form-data; boundary=portal-boundary"
        )
        self.headers["Content-Length"] = str(content_length)
        self.rfile = SimpleNamespace(read_sizes=[])
        self.rfile.read = lambda size=-1: self.rfile.read_sizes.append(size) or b""
        self.wfile = io.BytesIO()
        self.status = 0
        self.response_headers: dict[str, str] = {}

    def send_response(self, status: int) -> None:
        self.status = status

    def send_header(self, name: str, value: str) -> None:
        self.response_headers[name] = value

    def end_headers(self) -> None:
        return


def test_oversized_multipart_route_returns_413_without_reading_contracts(monkeypatch, subtests, isolated_portal_case):
    """上传在读取前检查权限并在处理前检查容量"""
    with subtests.test(contract='test_article_upload_checks_role_before_reading_body'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            monkeypatch.setattr(serve_management_portal, "_check_session", lambda _handler: True)
            monkeypatch.setattr(
                serve_management_portal,
                "_get_session_username",
                lambda _handler: "scoped-user",
            )
            monkeypatch.setattr(
                serve_management_portal,
                "_require_resource",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    PermissionError("sign-in required")
                ),
            )
            handler = _PostHarness(
                "/api/articles/article_0123456789ab/upload",
                content_length=1024,
            )

            serve_management_portal.Handler.do_POST(handler)

            assert handler.status == 403
            assert handler.rfile.read_sizes == []
    with subtests.test(contract='test_paper_uploads_enforce_domain_byte_limits_before_processing'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            monkeypatch.setattr(paper_backend, "PAPER_PDF_UPLOAD_MAX_BYTES", 4)
            with pytest.raises(ValueError, match="pdf file is too large"):
                paper_backend.upload_paper_pdf(
                    {"paper_id": "paper_test", "data_bytes": b"%PDF-1.4"},
                    "sample_admin",
                )

            monkeypatch.setattr(paper_backend, "PAPER_FIGURE_IMAGE_MAX_BYTES", 1)
            with pytest.raises(ValueError, match="image file is too large"):
                paper_backend.upload_figure_input_image(
                    {"paper_id": "paper_test", "data_bytes": b"image"},
                    "sample_admin",
                )


def test_paper_image_rejects_pixel_limit_before_decode(
    tmp_path,
    monkeypatch,
) -> None:
    """论文图片超过像素限制时在解码前拒绝"""
    class OversizedImage:
        size = (10, 10)

        def load(self) -> None:
            raise AssertionError("oversized image must not be decoded")

    monkeypatch.setattr(paper_backend, "PAPER_FIGURE_IMAGE_MAX_PIXELS", 50)
    monkeypatch.setattr(paper_backend, "_paper_by_id", lambda _paper_id: {"id": "paper_test"})
    monkeypatch.setattr(paper_backend, "_paper_root", lambda _paper: tmp_path)
    monkeypatch.setattr(paper_backend.Image, "open", lambda _stream: OversizedImage())

    with pytest.raises(ValueError, match="dimensions are too large"):
        paper_backend.upload_figure_input_image(
            {"paper_id": "paper_test", "data_bytes": b"image"},
            "sample_admin",
        )


def test_hatg_graph_nodes_use_only_id_for_resident_identity() -> None:
    """HATG 图节点仅以 id 表示 Resident 身份"""
    root = Path(__file__).resolve().parents[2]
    sources = (
        "management_portal/society_service/lineage_domain.py",
        "management_portal/society_service/lineage_member_projection.py",
        "management_portal/society_service/state_store.py",
        "management_portal/workbench_projections/studio.py",
    )
    duplicate_identity_literals: list[str] = []
    for relative_path in sources:
        tree = ast.parse((root / relative_path).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            values = {
                key.value: value
                for key, value in zip(node.keys, node.values)
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
            if (
                "id" in values
                and "resident_id" in values
                and ast.dump(values["id"]) == ast.dump(values["resident_id"])
            ):
                duplicate_identity_literals.append(f"{relative_path}:{node.lineno}")
    assert duplicate_identity_literals == []


def test_project_list_requires_explicit_current_projection_schema(monkeypatch) -> None:
    """项目列表要求显式采用当前投影版本"""
    from types import SimpleNamespace

    from management_portal.resources import isa as resource_isa

    class ProjectService:
        state: dict[str, object] = {}

        def query(self, **_kwargs: object) -> dict[str, object]:
            return {"resources": [], "total": 0}

        def projection_state(self, _adapter: str) -> dict[str, object]:
            return self.state

    service = ProjectService()
    monkeypatch.setattr(resource_isa, "_service", lambda: service)
    invocation = SimpleNamespace(caller="alice", argument=lambda _name: None)
    with pytest.raises(RuntimeError, match="requires schema_version 1"):
        resource_isa.list_projects(invocation)
    service.state = {"schema_version": 1, "project_ids": [], "status_vocab": ["active"]}
    assert resource_isa.list_projects(invocation) == {"projects": [], "status_vocab": ["active"]}


def test_lineage_reads_require_an_explicit_actor() -> None:
    """Lineage 读取要求显式传入调用者身份"""
    root = Path(__file__).resolve().parents[2]
    source = (
        root / "management_portal/society_service/lineage_aggregate.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    definition = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "read_lineage"
    )
    actor_index = [
        argument.arg for argument in definition.args.kwonlyargs
    ].index("actor")
    assert definition.args.kw_defaults[actor_index] is None

    missing_actor: list[str] = []
    for path in (root / "management_portal").rglob("*.py"):
        module = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(module):
            if not isinstance(node, ast.Call):
                continue
            function_name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else ""
            )
            if function_name == "read_lineage" and not any(
                keyword.arg == "actor" for keyword in node.keywords
            ):
                missing_actor.append(
                    f"{path.relative_to(root)}:{node.lineno}"
                )
    assert missing_actor == []
