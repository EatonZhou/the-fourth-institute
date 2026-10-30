from __future__ import annotations

import ast
import copy
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlparse

import pytest
import yaml

from management_common.actor_identity import canonical_actor_id, same_actor
from management_portal.watchboards import service, telepus


ROOT = Path(__file__).resolve().parents[2]
RESIDENT_ID = "resident_aaaaaaaaaaaa"
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class NoteApplication:
    """A minimal application that exercises the generic service contract."""

    APPLICATION_ID = "notes"

    @staticmethod
    def matches(context) -> bool:
        return (context.root / "notes.enabled").is_file()

    @staticmethod
    def project(context) -> dict:
        folder = context.root / "data"
        return {"notes": {"enabled": True, "dates": sorted(item.name for item in folder.iterdir()) if folder.is_dir() else []}}

    @staticmethod
    def act(context, operation: str, arguments: dict) -> dict:
        date, text = str(arguments.get("date") or ""), str(arguments.get("text") or "").strip()
        if operation != "save" or set(arguments) - {"date", "text"} or not DATE.fullmatch(date) or not text:
            raise ValueError("invalid note")
        day = context.root / "data" / date
        day.mkdir(parents=True)
        (day / "note.md").write_text(text + "\n")
        (day / "saved.json").write_text(json.dumps({"saved_by": context.actor}))
        return {"note_saved": {"date": date}}


@pytest.fixture
def application(tmp_path, monkeypatch):
    root = tmp_path / "watchboard"
    root.mkdir()
    (root / "notes.enabled").write_text("")
    payload = {
        "resident_id": RESIDENT_ID,
        "watchboard": {
            "enabled": True, "resident_id": RESIDENT_ID, "script_root": str(root),
            "signature": "core-signature", "manifest": {"payload": {}},
            "status": {"payload": {"date": "2026-09-09"}},
            "permissions": {"can_run": True, "can_edit_config": True},
        },
    }
    resident = SimpleNamespace(
        get_resident_watchboard=Mock(side_effect=lambda *args: copy.deepcopy(payload)),
    )
    monkeypatch.setattr(service, "get_society_service", lambda: SimpleNamespace(resident=resident))
    monkeypatch.setattr(service, "applications", lambda: (telepus, NoteApplication))
    return SimpleNamespace(root=root, payload=payload, resident=resident)


def test_application_projection_is_outside_the_core_snapshot(application):
    """Watchboard 应用投影附加在副本上，不改动核心快照且签名稳定"""
    original = copy.deepcopy(application.payload)
    projected = service.project(original, "alice")
    assert original == application.payload
    assert "notes" not in original["watchboard"]
    assert projected["watchboard"]["notes"]["enabled"] is True
    assert projected["watchboard"]["application_id"] == "notes"
    assert projected["watchboard"]["signature"] != "core-signature"
    assert service.project(original, "alice")["watchboard"]["signature"] == projected["watchboard"]["signature"]


def test_action_writes_application_files_and_records_actor(application):
    """Watchboard 应用动作写入应用文件，并以规范化后的调用者记录操作人"""
    result = service.action(RESIDENT_ID, "＠ alice", "notes", "save", {"date": "2026-09-09", "text": "工作日程"})
    day = application.root / "data" / "2026-09-09"
    assert (day / "note.md").read_text() == "工作日程\n"
    assert json.loads((day / "saved.json").read_text())["saved_by"] == "alice"
    assert result["note_saved"]["date"] == "2026-09-09"
    assert result["watchboard"]["notes"]["dates"] == ["2026-09-09"]


@pytest.mark.parametrize("arguments", [
    {"date": "../../elsewhere", "text": "invalid"},
    {"date": "2026-09-09", "text": "invalid", "actor": "admin"},
    {"date": "2026-09-09", "text": ""},
])
def test_invalid_application_input_has_no_business_effect(application, arguments):
    """Watchboard 应用收到非法日期、多余参数或空文本时报错且不写文件"""
    with pytest.raises(ValueError):
        service.action(RESIDENT_ID, "alice", "notes", "save", arguments)
    assert not (application.root / "data").exists()


def test_application_actions_check_permissions_before_writing(application):
    """无运行权限的用户执行 Watchboard 应用动作时在加锁和写入前被拒绝"""
    application.payload["watchboard"]["permissions"]["can_run"] = False
    with pytest.raises(PermissionError):
        service.action(RESIDENT_ID, "reader", "notes", "save", {"date": "2026-09-09", "text": "denied"})
    assert not (application.root / ".portal-application.lock").exists()
    assert not (application.root / "data").exists()


def test_permission_is_rechecked_after_acquiring_application_lock(application):
    """Watchboard 应用动作取得锁后重新检查权限，权限已撤销则拒绝"""
    revoked = copy.deepcopy(application.payload)
    revoked["watchboard"]["permissions"]["can_run"] = False
    application.resident.get_resident_watchboard.side_effect = [application.payload, revoked]
    with pytest.raises(PermissionError):
        service.action(RESIDENT_ID, "alice", "notes", "save", {"date": "2026-09-09", "text": "denied"})
    assert not (application.root / "data").exists()


def test_changed_root_cannot_escape_the_locked_application(application, tmp_path):
    """加锁后 Watchboard 脚本根目录发生变化时拒绝执行应用动作"""
    changed = copy.deepcopy(application.payload)
    changed["watchboard"]["script_root"] = str(tmp_path / "replacement")
    application.resident.get_resident_watchboard.side_effect = [application.payload, changed]
    with pytest.raises(ValueError, match="binding changed"):
        service.action(RESIDENT_ID, "alice", "notes", "save", {})


def test_unknown_or_mismatched_application_cannot_run(application):
    """未知应用或与该 Watchboard 不匹配的应用不能执行动作"""
    with pytest.raises(ValueError, match="Unknown"):
        service.action(RESIDENT_ID, "alice", "custom.module", "save", {})
    with pytest.raises(ValueError, match="does not match"):
        service.action(RESIDENT_ID, "alice", "telepus", "delete", {"id_token": "12345"})


def test_broken_application_projection_preserves_generic_dashboard(application, monkeypatch):
    """Watchboard 应用投影出错时仍返回通用看板并附带 application_error"""
    monkeypatch.setattr(NoteApplication, "project", Mock(side_effect=RuntimeError("broken application")))
    projected = service.read(RESIDENT_ID, "alice")["watchboard"]
    assert projected["enabled"] is True
    assert projected["application_error"]
    assert "notes" not in projected
    assert projected["manifest"] == application.payload["watchboard"]["manifest"]


def test_authenticated_users_manage_only_their_own_telepus_contact(application, monkeypatch):
    """已认证用户只能绑定、换绑和删除自己的 Telepus 联系人，匿名请求被拒绝

    管理自己的联系人是固定角色规则，与能否运行 Watchboard 命令（TriggerEvent）无关：没有运行权限的 User 也能管理自己的联系人。
    """
    from management_common.operation_errors import OperationError

    monkeypatch.setattr(telepus, "read_site_config", lambda: {"deployment": {"telepus": {"resident_id": RESIDENT_ID}}})
    monkeypatch.setattr(telepus, "get_portal_access_store", lambda: SimpleNamespace(user=lambda name: {"username": name, "configured": True, "status": "active"}))

    def dashboard(resident_id, actor):
        payload = copy.deepcopy(application.payload)
        payload["watchboard"]["permissions"] = {"can_run": False, "can_edit_config": False}
        return payload

    application.resident.get_resident_watchboard.side_effect = dashboard
    with pytest.raises(PermissionError, match="认证身份"):
        service.action(RESIDENT_ID, "", "telepus", "save", {"id_token": "123456"})
    assert telepus._read_telepus_telegram_user_contacts() == {}

    service.action(RESIDENT_ID, "bob", "telepus", "save", {"id_token": "654321"})
    result = service.action(RESIDENT_ID, "alice", "telepus", "save", {"id_token": "123456"})
    assert result["contact"]["portal_actor"] == "alice"
    contacts = result["watchboard"]["telegram_users"]
    assert contacts["permissions"]["can_manage"] is True
    assert {row["portal_actor"]: row["current_actor"] for row in contacts["users"]} == {"alice": True, "bob": False}
    assert contacts["users"][0]["display_id"] == "12...56"
    saved = telepus._read_telepus_telegram_user_contacts()
    for operation, arguments in [
        ("save", {"id_token": "777777", "previous_id_token": "654321"}),
        ("delete", {"id_token": "654321"}),
    ]:
        with pytest.raises(OperationError) as conflict:
            service.action(RESIDENT_ID, "alice", "telepus", operation, arguments)
        assert conflict.value.http_status == 409
        assert telepus._read_telepus_telegram_user_contacts() == saved
    with pytest.raises(ValueError, match="Unsupported"):
        service.action(RESIDENT_ID, "alice", "telepus", "save", {"id_token": "777777", "username": "bob"})
    assert telepus._read_telepus_telegram_user_contacts() == saved
    result = service.action(RESIDENT_ID, "alice", "telepus", "save", {"id_token": "777777", "previous_id_token": "123456"})
    assert result["contact"]["portal_actor"] == "alice"
    result = service.action(RESIDENT_ID, "alice", "telepus", "delete", {"id_token": "777777"})
    assert [row["portal_actor"] for row in result["watchboard"]["telegram_users"]["users"]] == ["bob"]


def test_telepus_contacts_are_the_rows_the_telegram_router_reads(monkeypatch):
    """Telepus 联系人绑定是 Telegram 路由读取的 telepus.telegram_contacts 表行

    绑定、换绑和解绑 Telepus 联系人只写 PostgreSQL 的 telepus.telegram_contacts 表（Telegram 路由按它决定每个 Telegram 用户以哪个 Portal User 身份操作），Watchboard 读取同一张表；遗留的 user_actors.json 既不被读取也不被改写。

    :module: security.identity-access
    :importance: P1
    """
    from management_common.portal_database import portal_database
    from management_portal.society_service.lineage_storage import canonical_lineage_folder

    # The router authorizes each message from telegram_contacts in its own
    # schema (TELEPUS_DATABASE_SCHEMA=telepus); the former contact file in the
    # Telepus Lineage Folder is neither read nor written.
    retired = canonical_lineage_folder("lineage_c625a4bee56b") / "state" / "telegram" / "user_actors.json"
    retired.parent.mkdir(parents=True, exist_ok=True)
    retired.write_text(json.dumps({"999999": {"name": "mallory", "portal_actor": "mallory"}}), encoding="utf-8")
    before = retired.read_bytes()
    monkeypatch.setattr(telepus, "get_portal_access_store", lambda: SimpleNamespace(user=lambda name: {"username": name, "configured": True, "status": "active"}))
    telepus.bind_contact("alice", "123456")
    telepus.bind_contact("bob", "654321")
    telepus.unbind_contact("bob", "654321")
    with portal_database(schema="telepus").read() as connection:
        rows = [tuple(row) for row in connection.execute("SELECT telegram_user_id, name, portal_actor FROM telegram_contacts")]
    assert rows == [("123456", "alice", "alice")]
    assert telepus._read_telepus_telegram_user_contacts() == {
        "123456": {"telegram_user_id": "123456", "name": "alice", "portal_actor": "alice"}}
    assert retired.read_bytes() == before


def test_actor_normalization_keeps_the_existing_contract(subtests, isolated_portal_case):
    """调用者标识规范化时去掉全角或半角 @ 与空白，比较时忽略大小写"""
    for value, expected in [("＠ @ Alice ", "Alice"), (None, ""), ("CASE-A", "CASE-A")]:
        with subtests.test(value=value, expected=expected), isolated_portal_case() as case_path:
            assert canonical_actor_id(value) == expected
            assert same_actor("＠ALICE", "alice")


def test_application_rules_do_not_leak_into_lineage_or_common_view():
    """具体 Watchboard 应用的规则不出现在 Lineage 与通用视图代码中"""
    files = [
        "management_portal/society_service/resident_domain.py",
        "management_portal/society_service/contracts.py",
        "management_portal/static/lineage_workbench_watchboard.js",
        "management_portal/static/lineage_workbench.js",
        "management_portal/static/lineage_workbench_location.js",
        "management_portal/static/lineage_workbench_chat_targets.js",
        "management_portal/static/lineage_workbench_selection.js",
        "management_portal/static/lineage_workbench_chat_detail.js",
        "management_portal/static/lineage_workbench_layout.js",
        "management_portal/static/lineage_workbench_editors.js",
        "management_portal/static/lineage_workbench_dialog_controls.js",
        "management_portal/static/lineage_workbench.css",
        "management_portal/static/portal_resident_service.js",
    ]
    for relative in files:
        content = (ROOT / relative).read_text()
        for marker in ("DailyScheduleAgent", "daily_schedule", "daily-schedule-generate", "RobotCompanyResearchAgent", "KnowledgeHygieneAgent", "PaperCollectorAgent", "TELEPUS_", "saveTelepusTelegramUser", "confirmWatchboardDailySchedule", "watchboardDaily", "wireguardGuide", "khChecker", "data-robot"):
            assert marker not in content, (relative, marker)
    for path in (ROOT / "management_portal/watchboards").glob("*.py"):
        tree = ast.parse(path.read_text())
        imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        assert not any("resident_domain" in name or "resident_store" in name for name in imports), path


def test_application_http_transport_preserves_identity_and_error_contract(monkeypatch, subtests, isolated_portal_case):
    """Watchboard 应用 HTTP 接口以会话用户身份调用，并把错误映射为 403、400、404、409"""
    from management_portal.hatg.contracts import InstructionError
    for error, status in [(None, 200), (PermissionError("denied"), 403), (ValueError("invalid"), 400), (KeyError("missing"), 404), (InstructionError("instruction.conflict", "stale contact"), 409)]:
        with subtests.test(error=error, status=status), isolated_portal_case() as case_path:
            with monkeypatch.context() as monkeypatch:
                from management_portal import serve_management_portal as portal
                from management_portal import http_auth, http_responses

                payload = {"resident_id": RESIDENT_ID, "application_id": "notes", "operation": "save", "args": {"date": "2026-09-09"}, "actor": "spoofed"}
                invoke = Mock(return_value={"accepted": True}, side_effect=error)
                # The route reads its body through the Agent Workbench adapter's JSON reader.
                monkeypatch.setattr(portal._AGENT_WORKBENCH_HTTP, "read_json", lambda *args, **kwargs: payload)
                monkeypatch.setattr(http_auth, "_get_session_username", lambda handler: "alice")
                monkeypatch.setattr(portal.watchboard_applications, "action", invoke)
                monkeypatch.setattr(http_responses, "_serve_json", lambda handler, code, body: (code, body))
                parsed = urlparse("/api/agent-workbench/residents/watchboard-application")
                handler = object.__new__(portal.Handler)
                handler.headers = {}
                response = portal._PORTAL_HTTP_ROUTES.dispatch(
                    "POST", parsed.path, handler, parsed, "spoofed", phase="protected"
                )
                assert response[0] == status
                invoke.assert_called_once_with(RESIDENT_ID, "alice", "notes", "save", {"date": "2026-09-09"})
                assert response[1] == ({"accepted": True} if error is None else {"error": str(error)})
