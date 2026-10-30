from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from unittest import mock
from urllib.parse import urlparse

from management_portal import serve_management_portal
from management_portal import http_auth, http_files, web_clients


class _ResponseHarness:
    def __init__(self, *, headers: dict[str, str] | None = None, path: str = "") -> None:
        self.headers = dict(headers or {})
        self.path = path
        self.wfile = BytesIO()
        self.status = 0
        self.response_headers: dict[str, str] = {}

    def send_response(self, status: int) -> None:
        self.status = status

    def send_header(self, name: str, value: str) -> None:
        self.response_headers[name] = value

    def end_headers(self) -> None:
        return


def test_remote_workspace_image_path_is_restored_after_transport_contracts(monkeypatch, tmp_path, subtests, isolated_portal_case):
    """Agent Workbench 文件链接预览在远程传输后恢复图片路径

    Agent Workbench 的 link-file-raw 路由按 SocietyService 返回的路径读取图片，返回原始字节和 image/png 类型。
    """
    with subtests.test(contract='test_remote_workspace_image_path_is_restored_after_transport'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            image_path = tmp_path / "chart.png"
            image_bytes = b"\x89PNG\r\n\x1a\nportal-preview"
            image_path.write_bytes(image_bytes)
            monkeypatch.setattr(
                web_clients,
                "workspace_api",
                SimpleNamespace(
                    resolve_workspace_link_file_path=lambda *_args, **_kwargs: str(
                        image_path
                    ),
                ),
            )
            monkeypatch.setattr(
                http_auth,
                "_get_session_username",
                lambda _handler: "sample_admin",
            )
            handler = _ResponseHarness()

            serve_management_portal._AGENT_WORKBENCH_HTTP.get_link_file_raw(handler, urlparse(f'/api/agent-workbench/link-file-raw?path={image_path}'), '')

            assert handler.status == 200
            assert handler.response_headers["Content-Type"] == "image/png"
            assert handler.wfile.getvalue() == image_bytes


def test_workbench_link_file_permission_denial_is_fixed_403_contracts(monkeypatch, subtests, isolated_portal_case):
    """链接文件和 Resident 投影无权访问时固定返回 403，共享文件预览不查 Project 元数据"""
    with subtests.test(contract='test_workbench_link_file_permission_denial_is_fixed_403'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            monkeypatch.setattr(
                web_clients,
                "workspace_api",
                SimpleNamespace(
                    get_workspace_link_file=mock.Mock(
                        side_effect=PermissionError("controlled denial")
                    ),
                ),
            )
            monkeypatch.setattr(
                http_auth,
                "_get_session_username",
                lambda _handler: "[SECURITY-TEST:run_20260825_first_batch50_extension]owner",
            )
            handler = _ResponseHarness()

            serve_management_portal._AGENT_WORKBENCH_HTTP.get_link_file(handler, urlparse('/api/agent-workbench/link-file?path=controlled.txt'), '')

            assert handler.status == 403
    with subtests.test(contract='test_shared_link_preview_skips_project_knowledge_metadata'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            metadata = mock.Mock(side_effect=AssertionError("shared files are not project items"))
            monkeypatch.setattr(
                web_clients,
                "workspace_api",
                SimpleNamespace(
                    get_workspace_link_file=mock.Mock(
                        return_value={
                            "kind": "markdown",
                            "path": "/share/Skills/example/SKILL.md",
                            "access_source": "shared",
                        }
                    ),
                ),
            )
            monkeypatch.setattr(
                serve_management_portal,
                "knowledge_backend",
                SimpleNamespace(item_metadata_for_path=metadata),
            )
            monkeypatch.setattr(
                http_auth,
                "_get_session_username",
                lambda _handler: "test-actor",
            )
            handler = _ResponseHarness()

            serve_management_portal._AGENT_WORKBENCH_HTTP.get_link_file(handler, urlparse('/api/agent-workbench/link-file?path=/share/Skills/example/SKILL.md'), '')

            assert handler.status == 200
            metadata.assert_not_called()
    with subtests.test(contract='test_workbench_link_file_raw_permission_denial_stops_before_media'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            resolver = mock.Mock(side_effect=PermissionError("controlled denial"))
            serve_media = mock.Mock()
            serve_download = mock.Mock()
            monkeypatch.setattr(
                web_clients,
                "workspace_api",
                SimpleNamespace(resolve_workspace_link_file_path=resolver),
            )
            monkeypatch.setattr(
                http_auth,
                "_get_session_username",
                lambda _handler: "[SECURITY-TEST:run_20260825_first_batch50_extension]owner",
            )
            monkeypatch.setattr(http_files, "_serve_media_file", serve_media)
            monkeypatch.setattr(http_files, "_serve_download_file", serve_download)
            handler = _ResponseHarness()

            serve_management_portal._AGENT_WORKBENCH_HTTP.get_link_file_raw(handler, urlparse('/api/agent-workbench/link-file-raw?path=controlled.txt'), '')

            assert handler.status == 403
            assert b"controlled denial" not in handler.wfile.getvalue()
            serve_media.assert_not_called()
            serve_download.assert_not_called()
    with subtests.test(contract='test_resident_projection_permission_denial_is_403'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            monkeypatch.setattr(
                web_clients,
                "resident_api",
                SimpleNamespace(
                    get_resident_session_projection=mock.Mock(
                        side_effect=PermissionError("controlled denial")
                    ),
                ),
            )
            monkeypatch.setattr(
                http_auth,
                "_get_session_username",
                lambda _handler: "sample_admin",
            )
            handler = _ResponseHarness()

            serve_management_portal._AGENT_WORKBENCH_HTTP.get_residents_projection(handler, urlparse('/api/agent-workbench/residents/projection?id=resident_0123456789ab'), '')

            assert handler.status == 403


def test_versioned_media_preview_is_private_and_immutable_contracts(tmp_path, subtests, isolated_portal_case):
    """带版本号的媒体预览按私有不可变长期缓存，无版本预览用 ETag 复验，下载文件名去掉控制字符"""
    with subtests.test(contract='test_versioned_media_preview_is_private_and_immutable'), isolated_portal_case() as case_path:
        tmp_path = case_path
        image_path = tmp_path / "chart.png"
        image_path.write_bytes(b"preview")
        version = image_path.stat().st_mtime_ns
        handler = _ResponseHarness(
            path=f"/preview/chart.png?_preview_mtime={version}",
        )

        http_files._serve_media_file(handler, image_path, "image/png")

        assert handler.status == 200
        assert handler.response_headers["Cache-Control"] == (
            "private, max-age=31536000, immutable"
        )
        assert handler.response_headers["ETag"]
    with subtests.test(contract='test_unversioned_media_preview_uses_etag_revalidation'), isolated_portal_case() as case_path:
        tmp_path = case_path
        image_path = tmp_path / "chart.png"
        image_path.write_bytes(b"preview")
        initial = _ResponseHarness(path="/preview/chart.png")
        http_files._serve_media_file(initial, image_path, "image/png")
        cached = _ResponseHarness(
            path="/preview/chart.png",
            headers={"If-None-Match": initial.response_headers["ETag"]},
        )

        http_files._serve_media_file(cached, image_path, "image/png")

        assert cached.status == 304
        assert cached.response_headers["Cache-Control"] == "private, no-cache"
        assert cached.wfile.getvalue() == b""
    with subtests.test(contract='test_download_filename_control_characters_never_reach_headers'), isolated_portal_case() as case_path:
        tmp_path = case_path
        download_path = tmp_path / "unsafe\r\nname\x7f.pdf"
        download_path.write_bytes(b"%PDF-1.4\nheader-safe")
        handler = _ResponseHarness()

        http_files._serve_download_file(handler, download_path)

        disposition = handler.response_headers["Content-Disposition"]
        assert "\n" not in disposition
        assert "\r" not in disposition
        assert "\x7f" not in disposition
        assert 'filename="unsafe__name_.pdf"' in disposition
        assert "filename*=UTF-8''unsafe__name_.pdf" in disposition
