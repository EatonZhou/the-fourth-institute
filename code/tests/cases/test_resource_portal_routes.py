from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from unittest import mock

import pytest

from management_portal import (
    http_resources, repo_workspace_backend, resource_backup_backend, semantic_search_jobs, serve_management_portal,
)
from management_portal.hatg.contracts import InstructionError
from management_portal.portal_access_control import PortalAccessStore
from management_portal.resources.portal import (
    portal_route_script,
    portal_url_for_address,
    portal_url_for_resource,
)


class _QuietHandler(serve_management_portal.Handler):
    def log_message(self, _format: str, *_args: object) -> None:
        return


def _server() -> tuple[ThreadingHTTPServer, threading.Thread, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_address[1]}"


def _post_json(url: str, payload: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise AssertionError(
            f"POST {url} returned HTTP {exc.code}: {body}"
        ) from exc


@contextmanager
def _authenticated_resource_api(resource_id: str):
    service = mock.Mock()
    service.get.return_value = {"resource_id": resource_id}
    service.query.return_value = {"resources": [{"resource_id": resource_id}]}
    with (
        mock.patch.object(serve_management_portal, "_get_session_username", return_value="CASE-A"),
        mock.patch.object(serve_management_portal, "get_resource_service", return_value=service),
    ):
        yield


def test_resource_http_routes_preserve_actor_and_validation() -> None:
    """资源解析 HTTP 接口以会话用户身份调用，缺少 address 时返回 400"""
    service = mock.Mock()
    service.resolve.return_value = {"resource_id": "repo_0123456789ab"}
    service.register.return_value = {"resource_id": "repo_0123456789ab"}
    server, thread, base = _server()
    try:
        with (
            mock.patch.object(serve_management_portal, "_check_session", return_value=True),
            mock.patch.object(serve_management_portal, "_get_session_username", return_value="CASE-A"),
            mock.patch.object(serve_management_portal, "get_resource_service", return_value=service),
        ):
            with urllib.request.urlopen(
                f"{base}/api/resources/resolve?address=repo_0123456789ab"
            ) as response:
                assert response.status == 200
                assert json.load(response) == {"resource_id": "repo_0123456789ab"}
            service.resolve.assert_called_once_with("repo_0123456789ab", actor="CASE-A")

            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(f"{base}/api/resources/resolve")
            assert error.value.code == 400
            error_payload = json.load(error.value)
            assert error_payload["error"] == "address required"
            assert error_payload["code"] == "operation.invalid"
            # Registration is the RegisterResource instruction (no page route).
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_resource_route_script_is_generated_from_the_public_contract_contracts(subtests, isolated_portal_case):
    """资源路由脚本由公开契约生成，Resident 和 Lineage ID 指向对应的 Lineage 页面"""
    with subtests.test(contract='test_resource_route_script_is_generated_from_the_public_contract'), isolated_portal_case() as case_path:
        server, thread, base = _server()
        try:
            with urllib.request.urlopen(base + '/static/portal_resource_routes.js') as response:
                assert response.status == 200
                assert response.headers.get_content_type() == 'text/javascript'
                assert response.read() == portal_route_script()
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
    with subtests.test(contract='test_resident_and_lineage_ids_have_canonical_portal_surfaces'), isolated_portal_case() as case_path:
        assert portal_url_for_address('resident_0123456789ab') == '/lineage?resident=resident_0123456789ab'
        assert portal_url_for_address('lineage_0123456789ab') == '/lineage?lineage=lineage_0123456789ab'
    with subtests.test(contract='test_resident_resource_uses_its_exact_lineage_surface'), isolated_portal_case() as case_path:
        assert portal_url_for_resource('resident_fedcba987654', 'resident') == '/lineage?resident=resident_fedcba987654'
    with subtests.test(contract='test_resident_projection_uses_exact_identity'), isolated_portal_case() as case_path:
        resident_id = 'resident_fedcba987654'
        server, thread, base = _server()
        try:
            with mock.patch.object(serve_management_portal, '_check_session', return_value=True), mock.patch.object(serve_management_portal, '_get_session_username', return_value='CASE-A'), mock.patch.object(serve_management_portal.resident_api, 'get_resident_session_projection', return_value={'resident_id': resident_id}) as get_detail:
                with urllib.request.urlopen(f'{base}/api/agent-workbench/residents/projection?id={resident_id}') as response:
                    assert response.status == 200
                    projection = json.loads(response.read().decode('utf-8'))
                    assert projection['resident_id'] == resident_id
                    assert projection['chat_workspace']['target'] == {'kind': 'resident', 'id': resident_id, 'lineage_id': ''}
                get_detail.assert_called_once_with(resident_id, 'CASE-A')
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
    with subtests.test(contract='test_navigation_target_returns_only_resident_lineage_identity'), isolated_portal_case() as case_path:
        resident_id = 'resident_fedcba987654'
        lineage_id = 'lineage_0123456789ab'
        server, thread, base = _server()
        try:
            with mock.patch.object(serve_management_portal, '_check_session', return_value=True), mock.patch.object(serve_management_portal, '_get_session_username', return_value='CASE-A'), mock.patch.object(serve_management_portal.resident_api, 'resolve_resident_identity', return_value={'resident_id': resident_id, 'lineage_id': lineage_id}) as resolve_identity:
                with urllib.request.urlopen(f'{base}/api/agent-workbench/residents/navigation-target?id={resident_id}') as response:
                    assert response.status == 200
                    assert json.loads(response.read().decode('utf-8')) == {'resident_id': resident_id, 'lineage_id': lineage_id}
                resolve_identity.assert_called_once_with(resident_id, 'CASE-A')
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
    with subtests.test(contract='test_resident_reference_uses_canonical_lineage_navigation'), isolated_portal_case() as case_path:
        resident_id = 'resident_0123456789ab'
        lineage_id = 'lineage_0123456789ab'
        resource_service = mock.Mock()
        resource_service.reference.return_value = {'resource_id': resident_id, 'resource_type': 'resident', 'url': f'/lineage?resident={resident_id}'}
        navigation = mock.Mock(return_value={'url': f'/lineage?lineage={lineage_id}&resident={resident_id}'})
        with mock.patch.object(serve_management_portal, 'get_resource_service', return_value=resource_service), mock.patch.object(serve_management_portal.conversation_index_projection, 'get_resident_navigation', navigation):
            payload = serve_management_portal._resource_reference_payload(resident_id, 'sample_admin')
        assert payload['url'] == f'/lineage?lineage={lineage_id}&resident={resident_id}'
        resource_service.reference.assert_called_once_with(resident_id, actor='sample_admin')
        navigation.assert_called_once_with(resident_id, actor='sample_admin')


def test_chat_workspace_session_preserves_committed_projection_error():
    """Chat Workspace 保留原始投影错误与重试回执

    :module: workflow.paper
    :importance: P1
    :aspects: functional, behavior
    """
    failure = InstructionError(
        "instruction.projection_pending", "投影尚未确认", http_status=503,
        details={"instruction_id": "hatginst_existing", "domain_committed": True, "retry": "same_instruction_same_idempotency_key"},
    )
    server, thread, base = _server()
    try:
        with (
            mock.patch.object(serve_management_portal, "_check_session", return_value=True),
            mock.patch.object(serve_management_portal, "_get_session_username", return_value="test-actor"),
            mock.patch.object(serve_management_portal.resident_workspace_adapter, "get_workspace", side_effect=failure) as project,
            pytest.raises(urllib.error.HTTPError) as caught,
        ):
            urllib.request.urlopen(f"{base}/chat-workspace/api/session?surface=paper&workspace_key=paper:test&paper_id=test")
        assert caught.value.code == 503
        payload = json.loads(caught.value.read())
        assert payload == {
            "error": str(failure), "code": failure.code, "details": failure.details,
            "message": str(failure), "http_status": 503,
            "retryable": False, "commit_state": "committed",
        }
        project.assert_called_once_with("test-actor", "paper", "paper:test", {"project_id": "", "paper_id": "test", "query": ""})
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_shared_skill_chat_route_accepts_global_read_scope(tmp_path) -> None:
    """全局可读的共享 Skill 对普通用户开放条目对话接口"""
    skill_id = "skill_0123456789ab"
    actor = "shared-skill-reader"
    access = PortalAccessStore()
    access.upsert_user(actor, actor="CASE-A")
    service = mock.Mock()
    service.get.return_value = {
        "resource_id": skill_id,
        "resource_type": "skill",
        "attributes": {
            "project_ids": ["Shared Skills"],
            "read_scope": "global",
        },
    }
    server, thread, base = _server()
    try:
        with (
            mock.patch.object(
                serve_management_portal,
                "_check_session",
                return_value=True,
            ),
            mock.patch.object(
                serve_management_portal,
                "_get_session_username",
                return_value=actor,
            ),
            mock.patch.object(
                serve_management_portal,
                "_PORTAL_ACCESS",
                access,
            ),
            mock.patch.object(
                serve_management_portal,
                "get_resource_service",
                return_value=service,
            ),
            mock.patch.object(
                serve_management_portal.knowledge_item_chat_backend.knowledge_item_chat_backend,
                "get_state",
                return_value={"kb_id": skill_id, "messages": []},
            ) as get_state,
        ):
            with urllib.request.urlopen(
                f"{base}/knowledge/api/item-chat?kb_id={skill_id}"
            ) as response:
                assert response.status == 200
                assert json.loads(response.read().decode("utf-8"))["kb_id"] == skill_id

        get_state.assert_called_once_with(actor, skill_id)
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_shared_skill_graph_uses_resource_read_access(tmp_path) -> None:
    """共享 Skill 关系图按资源读权限隐藏不可读节点及其边和未解析链接计数"""
    skill_id = "skill_0123456789ab"
    hidden_id = "decision_abcdef123456"
    actor = "shared-skill-reader"
    access = PortalAccessStore()
    access.upsert_user(actor, actor="CASE-A")
    service = mock.Mock()
    service.get.return_value = {
        "resource_id": skill_id,
        "resource_type": "skill",
        "attributes": {
            "project_ids": ["Shared Skills"],
            "read_scope": "global",
        },
    }
    service.query.return_value = {
        "resources": [{"resource_id": skill_id}],
        "total": 1,
    }
    graph = {
        "root_id": skill_id,
        "nodes": [
            {
                "kb_id": skill_id,
                "project": "Shared Skills",
                "degree": 1,
            },
            {
                "kb_id": hidden_id,
                "project": "Private Project",
                "degree": 1,
            },
        ],
        "edges": [
            {"source": skill_id, "target": hidden_id, "relation": "related"},
        ],
        "relation_counts": {"related": 1},
        "unresolved_links": 3,
        "_unresolved_links_by_source": {skill_id: 1, hidden_id: 2},
    }
    server, thread, base = _server()
    try:
        with (
            mock.patch.object(
                serve_management_portal,
                "_check_session",
                return_value=True,
            ),
            mock.patch.object(
                serve_management_portal,
                "_get_session_username",
                return_value=actor,
            ),
            mock.patch.object(
                serve_management_portal,
                "_PORTAL_ACCESS",
                access,
            ),
            mock.patch.object(
                serve_management_portal,
                "get_resource_service",
                return_value=service,
            ),
            mock.patch.object(
                serve_management_portal.knowledge_backend,
                "build_item_graph",
                return_value=graph,
            ),
        ):
            with urllib.request.urlopen(
                f"{base}/knowledge/api/item-graph?kb_id={skill_id}"
            ) as response:
                assert response.status == 200
                payload = json.loads(response.read().decode("utf-8"))

        assert payload["nodes"] == [
            {
                "kb_id": skill_id,
                "project": "Shared Skills",
                "degree": 0,
            }
        ]
        assert payload["edges"] == []
        assert payload["relation_counts"] == {}
        assert payload["unresolved_links"] == 1
        assert "_unresolved_links_by_source" not in payload
        service.query.assert_called_once_with(
            facets=["knowledge"],
            actor=actor,
            limit=5000,
            offset=0,
            include_attributes=False,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_resident_reference_reports_society_service_unavailable_contracts(subtests, isolated_portal_case):
    """SocietyService 或 ResourceService 不可用时资源接口返回 503 并标明能否重试"""
    with subtests.test(contract='test_resident_reference_reports_society_service_unavailable'), isolated_portal_case() as case_path:
        server, thread, base = _server()
        resident_id = 'resident_0123456789ab'
        resource_service = mock.Mock()
        resource_service.reference.return_value = {'resource_id': resident_id, 'resource_type': 'resident', 'url': f'/lineage?resident={resident_id}'}
        navigation = mock.Mock(side_effect=serve_management_portal.SocietyServiceUnavailable('socket unavailable'))
        try:
            with mock.patch.object(serve_management_portal, '_check_session', return_value=True), mock.patch.object(serve_management_portal, 'get_resource_service', return_value=resource_service), mock.patch.object(serve_management_portal.conversation_index_projection, 'get_resident_navigation', navigation):
                with pytest.raises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(f'{base}/api/resources/reference?address={resident_id}')
            assert error.value.code == 503
            assert json.loads(error.value.read().decode('utf-8')) == {
                'error': 'socket unavailable', 'message': 'socket unavailable', 'code': 'lineage.unavailable',
                'http_status': 503, 'retryable': False, 'commit_state': 'unknown', 'details': {},
            }
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
    with subtests.test(contract='test_resource_query_reports_service_unavailable_as_retryable'), isolated_portal_case() as case_path:
        server, thread, base = _server()
        service = mock.Mock()
        service.query.side_effect = serve_management_portal.ResourceService.Unavailable('socket unavailable')
        try:
            with mock.patch.object(serve_management_portal, '_check_session', return_value=True), mock.patch.object(serve_management_portal, 'get_resource_service', return_value=service):
                with pytest.raises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(f'{base}/api/resource-index/query?resource_type=repo')
            assert error.value.code == 503
            assert json.loads(error.value.read().decode('utf-8')) == {
                'error': 'ResourceService 暂时不可用', 'message': 'ResourceService 暂时不可用',
                'code': 'resource_service_unavailable', 'details': {}, 'retryable': True,
                'detail': 'socket unavailable', 'http_status': 503, 'commit_state': 'unknown',
            }
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
    with subtests.test(contract='test_device_terminal_start_and_io_share_the_portal_terminal_backend'), isolated_portal_case() as case_path:
        server, thread, base = _server()
        started = {'session_id': 'term_device_test', 'cursor': 0, 'chunk': '', 'alive': True}
        # Opening a Terminal is OpenTerminal, which the Portal 管理员 category may run.
        from tests.cases.permission_test_support import portal_admin
        portal_admin('sample_admin')
        try:
            with mock.patch.object(serve_management_portal, '_check_session', return_value=True), mock.patch.object(serve_management_portal, '_get_session_username', return_value='sample_admin'), mock.patch.object(serve_management_portal.local_terminal_backend, 'start_device_terminal', return_value=started) as start_terminal:
                status, payload = _post_json(f'{base}/api/terminal/start', {'device_id': 'device_0123456789ab', 'cols': 120, 'rows': 40})
            assert status == 200
            assert payload == started
            start_terminal.assert_called_once_with('sample_admin', 'device_0123456789ab', '', 120, 40)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
    with subtests.test(contract='test_skill_detail_delete_route_refreshes_the_skill_resource'), isolated_portal_case() as case_path:
        server, thread, base = _server()
        skill_id = 'skill_0123456789ab'
        deleted = {'deleted': True, 'resource_type': 'skill', 'skill_id': skill_id, 'kb_id': skill_id, 'path': '/share/Skills/example', 'trash_path': '/share/Skills/.trash/example'}
        try:
            with _authenticated_resource_api(skill_id), mock.patch.object(serve_management_portal, '_check_session', return_value=True), mock.patch.object(serve_management_portal.knowledge_backend, 'delete_item', return_value=deleted) as delete_item, mock.patch.object(serve_management_portal, '_notify_resource_index') as notify:
                status, payload = _post_json(f'{base}/knowledge/api/detail/delete', {'kb_id': skill_id})
            assert status == 200
            assert payload == deleted
            delete_item.assert_called_once_with(skill_id, actor='CASE-A')
            notify.assert_called_once_with(path='/share/Skills/example', resource_id=skill_id, resource_type='skill', kind='deleted', wait=True)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
    with subtests.test(contract='test_resource_backup_routes_preserve_conflict_status'), isolated_portal_case() as case_path:
        server, thread, base = _server()
        try:
            with mock.patch.object(serve_management_portal, '_check_session', return_value=True), mock.patch.object(serve_management_portal, '_get_session_username', return_value='sample_admin'), mock.patch.object(resource_backup_backend, 'request_backup', side_effect=resource_backup_backend.ResourceBackupConflict('backup already running')):
                request = urllib.request.Request(f'{base}/api/resource-backup/runs', data=json.dumps({'profile': 'content'}).encode('utf-8'), headers={'Content-Type': 'application/json'}, method='POST')
                try:
                    urllib.request.urlopen(request)
                except urllib.error.HTTPError as exc:
                    assert exc.code == 409
                    payload = json.loads(exc.read().decode('utf-8'))
                    assert payload['error'] == 'backup already running'
                    assert payload['code'] == 'resource_backup.conflict'
                    assert payload['commit_state'] == 'not_committed'
                else:
                    raise AssertionError('conflicting backup request unexpectedly succeeded')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


def test_resource_content_download_preserves_the_resolved_filename(tmp_path) -> None:
    """资源内容下载保留解析出的中文文件名，并支持 Range 和 HEAD 请求"""
    resource_id = "decision_0123456789ab"
    source = tmp_path / "原始 Design 文件.md"
    source.write_text("# Design\n", encoding="utf-8")
    service = mock.Mock()
    service.open_content.return_value = {
        "resolved_path": str(source),
        "filename": source.name,
    }
    server, thread, base = _server()
    try:
        with (
            mock.patch.object(serve_management_portal, "_check_session", return_value=True),
            mock.patch.object(serve_management_portal, "_get_session_username", return_value="CASE-A"),
            mock.patch.object(serve_management_portal, "get_resource_service", return_value=service),
        ):
            content_url = f"{base}/api/resources/content?address={resource_id}"
            with urllib.request.urlopen(content_url) as response:
                assert response.status == 200
                assert response.headers.get_content_type() == "text/markdown"
                assert response.read() == b"# Design\n"
            with urllib.request.urlopen(
                urllib.request.Request(content_url, headers={"Range": "bytes=2-7"})
            ) as response:
                assert response.status == 206
                assert response.headers["Content-Range"] == "bytes 2-7/9"
                assert response.read() == b"Design"
            with urllib.request.urlopen(
                urllib.request.Request(content_url, method="HEAD")
            ) as response:
                assert response.status == 200
                assert response.headers["Content-Length"] == "9"
                assert response.read() == b""
            with urllib.request.urlopen(
                f"{base}/api/resources/content?address={resource_id}&download=1"
            ) as response:
                assert response.status == 200
                assert response.read() == b"# Design\n"
                disposition = response.headers["Content-Disposition"]

        assert disposition.startswith('attachment; filename="___Design___.md"')
        assert "filename*=UTF-8''%E5%8E%9F%E5%A7%8B%20Design%20%E6%96%87%E4%BB%B6.md" in disposition
        assert service.open_content.call_args_list == [mock.call(resource_id, actor="CASE-A")] * 4
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize("media_type,body", [
    ("image/png", b"provider-image"),
    ("text/plain", "正文内容".encode("utf-8")),
    ("text/markdown", b"# Design\n"),
    ("application/json", b'{"ready":true}'),
    ("application/octet-stream", b"\x00\xffprovider-content"),
])
def test_resource_content_prefers_provider_media_type_for_extensionless_files(tmp_path, media_type, body) -> None:
    """无扩展名的资源文件按内容提供方给出的媒体类型返回"""
    resource_id = "workspace_0123456789ab/file/assets/cover"
    source = tmp_path / "cover"
    source.write_bytes(body)
    service = mock.Mock()
    service.open_content.return_value = {
        "resolved_path": str(source),
        "filename": "cover",
        "media_type": media_type,
    }
    server, thread, base = _server()
    try:
        with (
            mock.patch.object(serve_management_portal, "_check_session", return_value=True),
            mock.patch.object(serve_management_portal, "_get_session_username", return_value="CASE-A"),
            mock.patch.object(serve_management_portal, "get_resource_service", return_value=service),
        ):
            with urllib.request.urlopen(
                f"{base}/api/resources/content?address={urllib.parse.quote(resource_id, safe='')}"
            ) as response:
                assert response.status == 200
                assert response.headers.get_content_type() == media_type
                assert response.read() == body
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_resource_routes_expose_category_shells_and_canonical_deep_links() -> None:
    """资源分类页输出各自的页面骨架，/r/ 短链和 address 参数跳到规范详情地址"""
    server, thread, base = _server()
    try:
        with mock.patch.object(serve_management_portal, "_check_session", return_value=True):
            with urllib.request.urlopen(f"{base}/resource") as response:
                html = response.read().decode("utf-8")
                assert response.geturl().endswith("/resource/knowledge")
                assert 'data-resource-category="knowledge"' in html
                assert 'data-resource-section-header' in html
                assert 'data-portal-nav-active="resource"' in html
                assert 'knowledgeSearchModeHint' not in html
                assert "Archive" in html

            with urllib.request.urlopen(f"{base}/resource/repo") as response:
                html = response.read().decode("utf-8")
                assert response.status == 200
                assert 'data-resource-tabs' in html
                assert 'repo_workspace.js' in html
                assert 'id="repoWorkspace"' in html
                assert 'id="repoBoardMode"' in html
                assert 'id="repoWorkspaceMode"' in html
                assert 'id="repoAutoCommitAll"' in html
                assert 'id="repoAutomationOverlay"' in html
                assert 'id="repoEditorOverlay"' in html
                assert 'id="repoBranchForm"' in html
                assert 'id="repoRegisterOpen"' in html
                assert 'id="repoRegisterForm"' in html

            with urllib.request.urlopen(f"{base}/resource/backup") as response:
                backup_html = response.read().decode("utf-8")
                assert response.status == 200
                assert 'data-resource-category="backup"' in backup_html
                assert 'id="resourceBackupContent"' in backup_html
                assert 'id="resourceBackupRun"' in backup_html
                assert "resource_backup.js" in backup_html

            with urllib.request.urlopen(
                f"{base}/resource/skill?item=skill_0123456789ab&embedded=1"
            ) as response:
                skill_detail_html = response.read().decode("utf-8")
                assert 'data-resource-category="skill"' in skill_detail_html
                assert "portal_resource_detail.js" in skill_detail_html
                assert "PortalResourceDetail.createGuest" in skill_detail_html
                assert (
                    'class="portalDetailSurface detailModalCard"'
                    in skill_detail_html
                )

            with urllib.request.urlopen(f"{base}/resource/skill") as response:
                skill_list_html = response.read().decode("utf-8")
                assert "resource_catalog.js" in skill_list_html
                assert "detailModalCard" not in skill_list_html

            with urllib.request.urlopen(
                f"{base}/resource/article?item=article_0123456789ab"
            ) as response:
                article_list_html = response.read().decode("utf-8")
                assert "article_workspace.js" in article_list_html
                assert 'id="articleRegisterOpen"' in article_list_html
                assert 'id="articleCompileMenu"' in article_list_html
                assert "从头重新编译" in article_list_html
                assert 'id="articleResourceQuery"' in article_list_html
                assert 'id="articleSemanticButton"' in article_list_html

            with urllib.request.urlopen(
                f"{base}/resource/article?item=article_0123456789ab&embedded=1"
            ) as response:
                article_detail_html = response.read().decode("utf-8")
                assert "article_workspace.js" in article_detail_html
                assert 'id="articleWorkbench"' in article_detail_html
                assert 'id="articleChatWorkspace"' in article_detail_html
                assert 'id="articlePdfView"' in article_detail_html

            with urllib.request.urlopen(
                f"{base}/resource/experiment?item=experiment_0123456789ab&embedded=1"
            ) as response:
                experiment_detail_html = response.read().decode("utf-8")
                assert 'data-resource-category="experiment"' in experiment_detail_html
                assert "portal_resource_detail.js" in experiment_detail_html
                assert "PortalResourceDetail.createGuest" in experiment_detail_html
                assert 'id="mExplore"' not in experiment_detail_html
                assert (
                    'class="portalDetailSurface detailModalCard"'
                    in experiment_detail_html
                )

            with urllib.request.urlopen(
                f"{base}/resource?address=repo_0123456789ab"
            ) as response:
                html = response.read().decode("utf-8")
                assert response.geturl().endswith(
                    "/resource/repo?item=repo_0123456789ab"
                )
                assert 'id="repoWorkspace"' in html

            with urllib.request.urlopen(
                f"{base}/r/skill_0123456789ab"
            ) as response:
                html = response.read().decode("utf-8")
                assert response.geturl().endswith(
                    "/resource/skill?item=skill_0123456789ab"
                )
                assert 'id="resourceFrameDetailFrame"' in html

            with urllib.request.urlopen(
                f"{base}/r/review_0123456789ab"
            ) as response:
                html = response.read().decode("utf-8")
                assert response.geturl().endswith(
                    "/resource/knowledge?item=review_0123456789ab"
                )
                assert 'data-resource-category="knowledge"' in html
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_resource_backup_routes_read_projection_and_queue_fixed_actions() -> None:
    """资源备份接口读取概览，并以 202 排队备份和校验任务"""
    server, thread, base = _server()
    overview = {
        "schema": "tfi.resource-backup-overview.v1",
        "health": {"status": "healthy"},
    }
    try:
        with (
            mock.patch.object(
                serve_management_portal,
                "_check_session",
                return_value=True,
            ),
            mock.patch.object(
                serve_management_portal,
                "_get_session_username",
                return_value="sample_admin",
            ),
            mock.patch.object(
                resource_backup_backend,
                "get_overview",
                return_value=overview,
            ) as get_overview,
            mock.patch.object(
                resource_backup_backend,
                "request_backup",
                return_value={
                    "accepted": True,
                    "kind": "backup",
                    "profile": "critical",
                    "status": "queued",
                },
            ) as request_backup,
            mock.patch.object(
                resource_backup_backend,
                "request_verification",
                return_value={
                    "accepted": True,
                    "kind": "verification",
                    "mode": "check",
                    "status": "queued",
                },
            ) as request_verification,
        ):
            with urllib.request.urlopen(
                f"{base}/api/resource-backup/overview"
            ) as response:
                assert response.status == 200
                assert json.loads(response.read().decode("utf-8")) == overview

            backup_status, backup_payload = _post_json(
                f"{base}/api/resource-backup/runs",
                {"profile": "critical"},
            )
            verification_status, verification_payload = _post_json(
                f"{base}/api/resource-backup/verifications",
                {"mode": "check"},
            )

        assert backup_status == 202
        assert backup_payload["profile"] == "critical"
        assert verification_status == 202
        assert verification_payload["mode"] == "check"
        get_overview.assert_called_once_with("sample_admin")
        request_backup.assert_called_once_with("critical", "sample_admin")
        request_verification.assert_called_once_with("check", "sample_admin")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_resource_bridge_routes_do_not_fall_back_to_the_generic_resolver_page_contracts(subtests, isolated_portal_case):
    """没有详情页的资源经短链或 address 访问时返回 404，不回落到通用解析页"""
    with subtests.test(contract='test_resource_bridge_routes_do_not_fall_back_to_the_generic_resolver_page'), isolated_portal_case() as case_path:
        server, thread, base = _server()
        address = 'dataset_0123456789ab'
        try:
            with mock.patch.object(serve_management_portal, '_check_session', return_value=True), mock.patch.object(serve_management_portal, '_resource_portal_location', return_value=''):
                for path in (f'/r/{address}', f'/{address}', f'/resource?address={address}', f'/resource.html?address={address}'):
                    try:
                        urllib.request.urlopen(f'{base}{path}')
                    except urllib.error.HTTPError as exc:
                        assert exc.code == 404
                        payload = json.loads(exc.read().decode('utf-8'))
                        assert payload['error'] == f'resource has no Portal detail surface: {address}'
                        assert payload['http_status'] == 404
                    else:
                        raise AssertionError(f'{path} unexpectedly resolved')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
    with subtests.test(contract='test_resource_capabilities_route_dispatches_through_service'), isolated_portal_case() as case_path:
        server, thread, base = _server()
        resource_service = mock.Mock()
        resource_service.capabilities.return_value = {'resource_type': 'device', 'indexed': True, 'resolvable': True, 'readable': False, 'registerable': True, 'updatable': False, 'unregisterable': True, 'actions': ['ssh.test']}
        try:
            with mock.patch.object(serve_management_portal, '_check_session', return_value=True), mock.patch.object(serve_management_portal, 'get_resource_service', return_value=resource_service):
                with urllib.request.urlopen(f'{base}/api/resources/capabilities?resource_type=device') as response:
                    payload = json.loads(response.read().decode('utf-8'))
            assert response.status == 200
            assert payload['actions'] == ['ssh.test']
            resource_service.capabilities.assert_called_once_with('device')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
    with subtests.test(contract='test_agent_api_auth_carries_claimed_lineage_scope'), isolated_portal_case() as case_path:
        handler = mock.Mock()
        handler.headers = {'Authorization': 'Bearer resident-token', 'X-Portal-Lineage-ID': 'lineage_0123456789ab', 'X-Portal-Execution-ID': 'execution-test'}
        with mock.patch.object(serve_management_portal, '_get_session_username', return_value=None), mock.patch.object(serve_management_portal.agent_token_auth, 'authenticate_bearer_token', return_value={'agent_id': 'resident_0123456789ab', 'categories': ['resource:read']}):
            auth = serve_management_portal._get_agent_api_auth(handler)
            assert auth == {'type': 'agent', 'id': 'resident_0123456789ab', 'lineage_id': 'lineage_0123456789ab', 'execution_id': 'execution-test'}
    with subtests.test(contract='test_resource_knowledge_surface_excludes_resources_with_dedicated_pages'), isolated_portal_case() as case_path:
        server, thread, base = _server()
        view = mock.Mock(return_value={'items': [], 'allowed_types': [], 'projects': [], 'page': {'offset': 0, 'limit': 200, 'count': 0, 'total': 0}})
        try:
            with mock.patch.object(serve_management_portal, '_check_session', return_value=True), mock.patch.object(serve_management_portal.resource_views, 'knowledge_index', view):
                with urllib.request.urlopen(f'{base}/knowledge/api/index?active_only=0&surface=resource-knowledge') as response:
                    assert response.status == 200
                assert view.call_args.kwargs['excluded_resource_types'] == ('skill', 'experiment')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


def test_resource_semantic_search_routes_preserve_the_requested_scope() -> None:
    """资源语义检索接口按请求的资源范围列出和创建检索，未指定范围时归入知识库"""
    server, thread, base = _server()
    job = {
        "search_id": "search_0123456789ab",
        "resource_scope": "repo",
        "query": "runtime repository",
        "status": "queued",
    }
    try:
        with (
            mock.patch.object(
                serve_management_portal,
                "_check_session",
                return_value=True,
            ),
            mock.patch.object(
                semantic_search_jobs,
                "list_searches",
                return_value={"searches": [job]},
            ) as list_searches,
            mock.patch.object(
                semantic_search_jobs,
                "start_search",
                return_value=job,
            ) as start_search,
            mock.patch.object(
                serve_management_portal,
                "_notify_resource_index",
            ) as notify,
        ):
            with urllib.request.urlopen(
                f"{base}/api/resource-semantic-searches?resource_scope=repo"
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
                assert payload["searches"] == [job]
            list_searches.assert_called_once_with("repo", "")

            list_searches.reset_mock()
            with urllib.request.urlopen(
                f"{base}/api/resource-semantic-searches?resource_type=repo"
            ) as response:
                assert response.status == 200
            # Without a scope the request is about Knowledge.
            list_searches.assert_called_once_with("knowledge", "")

            status, started = _post_json(
                f"{base}/api/resource-semantic-search/start",
                {
                    "query": "runtime repository",
                    "resource_scope": "repo",
                },
            )
            assert status == 200
            assert started == job
            start_search.assert_called_once_with(
                "repo",
                {
                    "query": "runtime repository",
                    "resource_scope": "repo",
                },
                "",
            )
            notify.assert_called_once_with(
                resource_id="search_0123456789ab",
                resource_type="search",
                kind="created",
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_repo_workspace_routes_dispatch_git_reads_and_mutations() -> None:
    """仓库工作区接口分发 Git 读取、写入、提交和丢弃，每次修改都通知资源索引"""
    server, thread, base = _server()
    repo_id = "repo_0123456789ab"
    overview = {
        "id": repo_id,
        "name": "portal",
        "current_branch": "main",
        "status": {"dirty": False, "changes": []},
    }
    try:
        with (
            _authenticated_resource_api(repo_id),
            mock.patch.object(
                serve_management_portal,
                "_check_session",
                return_value=True,
            ),
            mock.patch.object(
                repo_workspace_backend,
                "repo_overview",
                return_value=overview,
            ) as get_overview,
            mock.patch.object(
                repo_workspace_backend,
                "list_repo_tree",
                return_value={"id": repo_id, "path": "src", "entries": []},
            ) as list_tree,
            mock.patch.object(
                repo_workspace_backend,
                "read_repo_file",
                return_value={"id": repo_id, "path": "src/app.py", "content": "pass\n"},
            ) as read_file,
            mock.patch.object(
                repo_workspace_backend,
                "write_repo_file",
                return_value={"id": repo_id, "path": "src/app.py"},
            ) as write_file,
            mock.patch.object(
                repo_workspace_backend,
                "commit_repo_changes",
                return_value={"id": repo_id, "commit": {"short_sha": "abc1234"}},
            ) as commit_changes,
            mock.patch.object(
                repo_workspace_backend,
                "discard_repo_changes",
                return_value={
                    "id": repo_id,
                    "operation": "discard",
                    "discarded_count": 2,
                    "workspace": overview,
                },
            ) as discard_changes,
            mock.patch.object(
                repo_workspace_backend,
                "repo_automation_status",
                return_value={
                    "repos": [{"id": repo_id, "dirty": True, "actionable": True}],
                    "actionable_count": 1,
                },
            ) as automation_status,
            mock.patch.object(
                repo_workspace_backend,
                "auto_commit_repo",
                return_value={
                    "id": repo_id,
                    "committed": True,
                    "outcome": "committed_and_pushed",
                    "message": "Update Portal",
                    "reason": "Keep the route covered.",
                },
            ) as auto_commit,
            mock.patch.object(
                serve_management_portal,
                "_notify_resource_index",
            ) as notify,
        ):
            with urllib.request.urlopen(
                f"{base}/api/repos/{repo_id}/workspace"
            ) as response:
                assert json.loads(response.read()) == overview
            get_overview.assert_called_once_with(repo_id)

            with urllib.request.urlopen(
                f"{base}/api/repos/{repo_id}/workspace/tree?path=src"
            ) as response:
                assert json.loads(response.read())["path"] == "src"
            list_tree.assert_called_once_with(repo_id, "src")

            with urllib.request.urlopen(
                f"{base}/api/repos/{repo_id}/workspace/file?path=src%2Fapp.py"
            ) as response:
                assert json.loads(response.read())["content"] == "pass\n"
            read_file.assert_called_once_with(repo_id, "src/app.py")

            with urllib.request.urlopen(
                f"{base}/api/repos/automation/status?repo_id={repo_id}"
            ) as response:
                assert json.loads(response.read())["actionable_count"] == 1
            automation_status.assert_called_once_with([repo_id])

            status, _saved = _post_json(
                f"{base}/api/repos/{repo_id}/workspace/file",
                {
                    "path": "src/app.py",
                    "content": "print('portal')\n",
                    "expected_hash": "old-hash",
                },
            )
            assert status == 200
            write_file.assert_called_once_with(
                repo_id,
                "src/app.py",
                "print('portal')\n",
                expected_hash="old-hash",
            )

            status, committed = _post_json(
                f"{base}/api/repos/{repo_id}/workspace/commit",
                {
                    "message": "Update Portal",
                    "description": "Route behavior",
                },
            )
            assert status == 201
            assert committed["commit"]["short_sha"] == "abc1234"
            commit_changes.assert_called_once_with(
                repo_id,
                "Update Portal",
                description="Route behavior",
                actor="CASE-A",
            )
            status, automated = _post_json(
                f"{base}/api/repos/{repo_id}/workspace/auto-commit",
                {"provider": "kimi_primary"},
            )
            assert status == 201
            assert automated["outcome"] == "committed_and_pushed"
            auto_commit.assert_called_once_with(
                repo_id,
                actor="CASE-A",
                provider_id="kimi_primary",
            )
            status, discarded = _post_json(
                f"{base}/api/repos/{repo_id}/workspace/discard",
                {},
            )
            assert status == 200
            assert discarded["discarded_count"] == 2
            discard_changes.assert_called_once_with(repo_id)
            assert notify.call_count == 4
            assert all(
                call.kwargs == {
                    "resource_id": repo_id,
                    "resource_type": "repo",
                    "kind": "updated",
                }
                for call in notify.call_args_list
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_repo_description_route_generates_and_notifies_resource_index() -> None:
    """仓库描述生成接口按所选 Provider 生成描述并通知资源索引刷新"""
    server, thread, base = _server()
    repo_id = "repo_0123456789ab"
    generated = {
        "schema": "tfi.repo-descriptions.v1",
        "requested_count": 1,
        "generated_count": 1,
        "skipped_count": 0,
        "error_count": 0,
        "repos": [
            {
                "id": repo_id,
                "description": "提供统一资源浏览与 Git 协作能力。",
                "provider": "kimi_primary",
                "model": "kimi-k2.6",
            }
        ],
        "skipped": [],
        "errors": [],
        "usage": {"total_tokens": 80},
    }
    try:
        with (
            _authenticated_resource_api(repo_id),
            mock.patch.object(
                serve_management_portal,
                "_check_session",
                return_value=True,
            ),
            mock.patch.object(
                repo_workspace_backend,
                "generate_repo_descriptions",
                return_value=generated,
            ) as generate,
            mock.patch.object(
                serve_management_portal,
                "_notify_resource_index",
            ) as notify,
        ):
            status, payload = _post_json(
                f"{base}/api/repos/descriptions/generate",
                {
                    "repo_ids": [repo_id],
                    "provider": "kimi_primary",
                },
            )

            assert status == 200
            assert payload["repos"][0]["description"].startswith("提供统一资源")
            generate.assert_called_once_with(
                [repo_id],
                force=False,
                provider_id="kimi_primary",
                actor="CASE-A",
            )
            notify.assert_called_once_with(
                resource_type="repo",
                kind="updated",
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

