""":module: resource.identity"""
from __future__ import annotations

from management_portal.paper_resource_index import PaperManifestAdapter, PaperReviewAdapter
from management_portal import article_registry

import unittest
import json
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from management_portal import (
    device_identity_backend,
    resource_index_backend,
    serve_management_portal,
)
from management_portal import http_auth, web_resources
from management_portal.resources import ResourceService
from management_portal.resources.providers import install_resolvers
from tests.cases.registry_support import put_registry_document


class _QuietHandler(serve_management_portal.Handler):
    def log_message(self, _format: str, *_args: object) -> None:
        return


class ResourceResolverPortalTests(unittest.TestCase):
    def test_only_domain_or_indexed_resource_types_are_resolvable(self) -> None:
        """只有有领域解析器或已建索引的资源类型可解析"""
        index = mock.Mock()
        index.indexed_types.return_value = frozenset({"dataset"})
        index.supports_type.side_effect = lambda value: value == "dataset"
        service = ResourceService(
            index_provider=index,
            resolver_loader=install_resolvers,
        )

        self.assertTrue(service.capabilities("device")["resolvable"])
        self.assertTrue(service.capabilities("dataset")["resolvable"])
        self.assertFalse(service.capabilities("model")["resolvable"])

    def test_device_resources_use_the_registered_domain_resolver(self) -> None:
        """设备资源由登记的设备解析器解析，不查索引"""
        resolution = {
            "schema": "tfi.resource_resolution.v2",
            "address": "device_0123456789ab",
            "root_id": "device_0123456789ab",
            "resource_type": "device",
            "target_kind": "device",
            "record": {"id": "device_0123456789ab"},
            "device_id": "device_0123456789ab",
            "exists": True,
        }
        index = mock.Mock()
        index.indexed_types.return_value = frozenset()
        service = ResourceService(
            index_provider=index,
            resolver_loader=install_resolvers,
        )
        with mock.patch.object(
            device_identity_backend,
            "resolve_device_address",
            return_value=resolution,
        ) as resolve_device:
            resolved = service.resolve("device_0123456789ab", actor="alice")

        self.assertEqual(resolved["root_id"], "device_0123456789ab")
        self.assertEqual(resolved["resource_type"], "device")
        self.assertEqual(resolved["target_kind"], "device")
        resolve_device.assert_called_once_with(
            "device_0123456789ab",
        )
        index.get_resource.assert_not_called()

    def test_indexed_nested_file_address_stays_inside_content_root(self) -> None:
        """索引资源的嵌套文件地址解析为相对路径，越出内容根目录时报错"""
        root = Path(self._testMethodName).resolve()
        record = {
            "resource_id": "dataset_0123456789ab",
            "resource_type": "dataset",
            "title": "Dataset",
            "summary": "",
            "source": {"adapter": "dataset", "path": "", "modified_at": ""},
            "facets": ["dataset"],
            "attributes": {"content_root": str(root)},
            "url": "/datasets/?id=dataset_0123456789ab",
        }
        index = mock.Mock()
        index.indexed_types.return_value = frozenset({"dataset"})
        index.get_resource.return_value = record
        service = ResourceService(
            index_provider=index,
            resolver_loader=install_resolvers,
        )
        resolved = service.resolve(
                "dataset_0123456789ab/file/split/train.json",
                actor="alice",
            )
        self.assertEqual(resolved["relative_path"], "split/train.json")
        with self.assertRaisesRegex(ValueError, "must not escape"):
            service.resolve(
                "dataset_0123456789ab/file/../secret",
                actor="alice",
            )

    def test_repo_registry_records_are_searchable_by_bare_id(self) -> None:
        """仓库登记记录以裸 ID 进入索引，可按所属 Project 和远端地址搜索"""
        record = {
            "id": "repo_0123456789ab",
            "name": "example",
            "ownership": "tfi",
            "project_ids": ["ExampleProject"],
            "canonical_checkout": "/share/Projects/Example/src/example",
            "checkouts": [
                {
                    "path": "/share/Projects/Example/src/example",
                    "role": "canonical",
                }
            ],
            "remotes": [
                {"name": "origin", "url": "git@example.invalid:team/example.git"}
            ],
        }
        put_registry_document(resource_index_backend.resource_identity_backend.REPO_COLLECTION, record["id"], record)
        adapter = resource_index_backend.ResourceRegistryAdapter()
        with mock.patch.object(
            resource_index_backend.resource_identity_backend,
            "_validated_repo",
            return_value=record,
        ):
            entries, _state = adapter.scan()

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["resource_type"], "repo")
        self.assertEqual(entries[0]["resource_id"], "repo_0123456789ab")
        self.assertEqual(entries[0]["url"], "/resource/repo?item=repo_0123456789ab")
        self.assertIn("ExampleProject", entries[0]["search_terms"])
        self.assertIn("git@example.invalid:team/example.git", entries[0]["search_terms"])
        self.assertNotIn("aliases", entries[0])

    def test_article_registry_records_are_searchable_by_bare_id(self) -> None:
        """文章登记记录以裸 ID 进入索引，可按根目录路径搜索"""
        record = {
            "id": "article_0123456789ab",
            "title": "An Addressable Article",
            "project_ids": ["ExampleProject"],
            "canonical_root": "/share/Projects/Example/article",
            "primary_file": "main.tex",
            "roots": [
                {
                    "path": "/share/Projects/Example/article",
                    "role": "canonical",
                }
            ],
        }
        put_registry_document(article_registry.ARTICLE_COLLECTION, record["id"], record)
        adapter = resource_index_backend.ResourceRegistryAdapter()
        with mock.patch.object(
            article_registry,
            "_validated_article",
            return_value=record,
        ):
            entries, _state = adapter.scan()

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["resource_type"], "article")
        self.assertEqual(entries[0]["resource_id"], "article_0123456789ab")
        self.assertEqual(entries[0]["url"], "/resource/article?item=article_0123456789ab")
        self.assertIn(
            "/share/Projects/Example/article",
            entries[0]["search_terms"],
        )
        self.assertEqual(entries[0]["attributes"]["relations"], [])

    def test_papers_are_searchable_by_their_single_unified_id(self) -> None:
        """论文以统一 ID 进入索引，不收录摘要全文和私有文件路径"""
        paper = {
            "id": "paper_0123456789ab",
            "title": "An Addressable Paper",
            "abstract": "A compact summary",
            "venue": "MICRO",
            "year": "2026",
            "relations": {"projects": [{"project_id": "ExampleProject"}]},
            "project_ids": ["ExampleProject"],
            "pdf_path": "/private/paper.pdf",
            "analysis_path": "/private/analysis.md",
        }
        adapter = PaperManifestAdapter()
        entry = adapter._record_for_paper(paper)

        self.assertEqual(entry["resource_type"], "paper")
        self.assertEqual(entry["resource_id"], "paper_0123456789ab")
        self.assertEqual(entry["url"], "/paper?paper=paper_0123456789ab")
        self.assertEqual(entry["summary"], "A compact summary")
        self.assertNotIn("A compact summary", entry["search_terms"])
        self.assertIn("ExampleProject", entry["search_terms"])
        self.assertNotIn("abstract", entry["attributes"])
        self.assertEqual(entry["attributes"]["relations"], [])
        self.assertNotIn("pdf_path", entry["attributes"])
        self.assertNotIn("analysis_path", entry["attributes"])

    def test_authenticated_resolver_content_and_bridge_routes(self) -> None:
        """登录用户的解析、内容和裸 ID 短链路由各自跳到资源对应的页面"""
        resolution = {
            "address": "repo_0123456789ab/file/README.md",
            "root_id": "repo_0123456789ab",
            "target_kind": "file",
            "exists": True,
        }
        server = ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        resource_service = mock.Mock()
        resource_service.resolve.return_value = resolution
        resource_service.open_content.return_value = (
            b"# Repo\n",
            "text/markdown",
            resolution,
        )
        resource_service.reference.side_effect = lambda address, **_context: {
            "url": {
                "repo_0123456789ab/file/README.md": "/api/resources/content?address=repo_0123456789ab%2Ffile%2FREADME.md",
                "resident_0123456789ab": (
                    "/lineage?resident=resident_0123456789ab"
                ),
                "lineage_0123456789ab": (
                    "/lineage?lineage=lineage_0123456789ab"
                ),
            }.get(address, f"/r/{address}")
        }
        try:
            with (
                mock.patch.object(http_auth, "_check_session", return_value=True),
                mock.patch.object(http_auth, "_get_session_username", return_value="alice"),
                mock.patch.object(
                    serve_management_portal,
                    "get_resource_service",
                    return_value=resource_service,
                ),
                mock.patch.object(
                    web_resources,
                    "get_resource_service",
                    return_value=resource_service,
                ),
            ):
                with urllib.request.urlopen(
                    f"{base}/api/resources/resolve?address=repo_0123456789ab%2Ffile%2FREADME.md"
                ) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(json.load(response)["root_id"], "repo_0123456789ab")
                with urllib.request.urlopen(
                    f"{base}/api/resources/content?address=repo_0123456789ab%2Ffile%2FREADME.md"
                ) as response:
                    self.assertEqual(response.headers.get_content_type(), "text/markdown")
                    self.assertEqual(response.read(), b"# Repo\n")
                with urllib.request.urlopen(f"{base}/r/repo_0123456789ab") as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn(
                        "/resource/repo?item=repo_0123456789ab",
                        response.geturl(),
                    )
                    self.assertIn(b"repoWorkspace", response.read())
                with urllib.request.urlopen(f"{base}/repo_0123456789ab/file/README.md") as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn(
                        "/api/resources/content?address=repo_0123456789ab%2Ffile%2FREADME.md",
                        response.geturl(),
                    )
                    self.assertEqual(response.read(), b"# Repo\n")
                with urllib.request.urlopen(f"{base}/article_0123456789ab") as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn(
                        "/resource/article?item=article_0123456789ab",
                        response.geturl(),
                    )
                with urllib.request.urlopen(f"{base}/paper_0123456789ab") as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn(
                        "/paper?paper=paper_0123456789ab",
                        response.geturl(),
                    )
                with urllib.request.urlopen(f"{base}/resident_0123456789ab") as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn(
                        "/lineage?resident=resident_0123456789ab",
                        response.geturl(),
                    )
                with urllib.request.urlopen(f"{base}/lineage_0123456789ab") as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn(
                        "/lineage?lineage=lineage_0123456789ab",
                        response.geturl(),
                    )
                # Workspaces open in the Lineage workbench since the WorkspaceStore refactor.
                with urllib.request.urlopen(f"{base}/workspace_0123456789ab") as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn(
                        "/lineage?workspace=workspace_0123456789ab",
                        response.geturl(),
                    )
                with urllib.request.urlopen(f"{base}/device_0123456789ab") as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn(
                        "/resource/device?item=device_0123456789ab",
                        response.geturl(),
                    )

                resource_service.resolve.assert_called_once_with(
                    "repo_0123456789ab/file/README.md", actor="alice"
                )
                self.assertEqual(resource_service.open_content.call_args_list, [
                    mock.call("repo_0123456789ab/file/README.md", actor="alice"),
                    mock.call("repo_0123456789ab/file/README.md", actor="alice"),
                ])
                resource_service.read_content.assert_not_called()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_resource_content_streams_authorized_pdf_ranges(self) -> None:
        """资源内容接口按 Range 分段返回已授权的 PDF"""
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "manual.pdf"
            pdf_path.write_bytes(b"0123456789")
            resource_service = mock.Mock()
            resource_service.resolve.return_value = {
                "address": "skill_0123456789ab/file/assets/manual.pdf",
                "target_kind": "file",
                "resolved_path": str(pdf_path),
                "exists": True,
            }
            resource_service.open_content.return_value = resource_service.resolve.return_value
            server = ThreadingHTTPServer(("127.0.0.1", 0), serve_management_portal.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            address = "skill_0123456789ab%2Ffile%2Fassets%2Fmanual.pdf"
            request = urllib.request.Request(
                f"{base}/api/resources/content?address={address}",
                headers={"Range": "bytes=2-5"},
            )
            try:
                with (
                    mock.patch.object(http_auth, "_check_session", return_value=True),
                    mock.patch.object(http_auth, "_get_session_username", return_value="alice"),
                    mock.patch.object(
                        serve_management_portal,
                        "get_resource_service",
                        return_value=resource_service,
                    ),
                    mock.patch.object(
                        web_resources,
                        "get_resource_service",
                        return_value=resource_service,
                    ),
                ):
                    with urllib.request.urlopen(request) as response:
                        self.assertEqual(response.status, 206)
                        self.assertEqual(response.headers.get_content_type(), "application/pdf")
                        self.assertEqual(response.headers.get("Accept-Ranges"), "bytes")
                        self.assertEqual(response.headers.get("Content-Range"), "bytes 2-5/10")
                        self.assertEqual(response.read(), b"2345")
                resource_service.open_content.assert_called_once_with(
                    "skill_0123456789ab/file/assets/manual.pdf",
                    actor="alice",
                )
                resource_service.resolve.assert_not_called()
                resource_service.read_content.assert_not_called()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_resource_content_supports_video_seek_ranges(self) -> None:
        """资源内容接口支持视频拖动所需的 Range、If-Range 和越界 416 响应"""
        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = Path(temp_dir) / "clip.mp4"
            video_path.write_bytes(b"0123456789")
            resource_service = mock.Mock()
            resource_service.resolve.return_value = {
                "address": "workspace_0123456789ab/file/clip.mp4",
                "target_kind": "file",
                "resolved_path": str(video_path),
                "exists": True,
            }
            resource_service.open_content.return_value = resource_service.resolve.return_value
            server = ThreadingHTTPServer(("127.0.0.1", 0), serve_management_portal.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            address = "workspace_0123456789ab%2Ffile%2Fclip.mp4"

            def request(range_value: str, *, if_range: str = ""):
                headers = {"Range": range_value}
                if if_range:
                    headers["If-Range"] = if_range
                return urllib.request.Request(
                    f"{base}/api/resources/content?address={address}",
                    headers=headers,
                )

            try:
                with (
                    mock.patch.object(http_auth, "_check_session", return_value=True),
                    mock.patch.object(http_auth, "_get_session_username", return_value="alice"),
                    mock.patch.object(
                        serve_management_portal,
                        "get_resource_service",
                        return_value=resource_service,
                    ),
                    mock.patch.object(
                        web_resources,
                        "get_resource_service",
                        return_value=resource_service,
                    ),
                ):
                    with urllib.request.urlopen(request("bytes=4-")) as response:
                        self.assertEqual(response.status, 206)
                        self.assertEqual(response.headers.get_content_type(), "video/mp4")
                        self.assertEqual(response.headers.get("Content-Range"), "bytes 4-9/10")
                        self.assertEqual(response.read(), b"456789")

                    with urllib.request.urlopen(request("bytes=-3")) as response:
                        self.assertEqual(response.status, 206)
                        self.assertEqual(response.headers.get("Content-Range"), "bytes 7-9/10")
                        self.assertEqual(response.read(), b"789")

                    with urllib.request.urlopen(request("bytes=4-", if_range='"stale"')) as response:
                        self.assertEqual(response.status, 200)
                        self.assertIsNone(response.headers.get("Content-Range"))
                        self.assertEqual(response.read(), b"0123456789")

                    with self.assertRaises(urllib.error.HTTPError) as range_error:
                        urllib.request.urlopen(request("bytes=20-"))
                    self.assertEqual(range_error.exception.code, 416)
                    self.assertEqual(range_error.exception.headers.get("Content-Range"), "bytes */10")
                    self.assertEqual(range_error.exception.headers.get("Accept-Ranges"), "bytes")
                    self.assertEqual(range_error.exception.read(), b"")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
