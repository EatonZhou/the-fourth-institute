"""Question cards that request permissions lend exactly the approved ones with the answer.

:module: resident.identity
:importance: P1
:design: decision_1e2eb869bc10
"""
from pathlib import Path
import subprocess

from tests.cases.test_resident_delivery_receipts import delivery_resident  # noqa: F401
from tests.cases.test_resident_folder_creation_contract import future_creation_store  # noqa: F401


def test_chat_question_credentials():
    """提问卡片展示权限请求，同意时随回答签发正好这些凭证

    卡片列出精确的 A 节点与资源，只提供固定的同意与不交出；同意后按固定回答身份准备、暂存任务凭证并投递。拒绝直接投递，部分授权失败不得投递。
    重试保持相同身份，取消先撤回准备；门面保留原投递地址，只给有效回执所指向的提问成员暂存凭证。真实聊天面板和问题卡片截获所有指令。

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    script = Path(__file__).with_name("javascript") / "chat_question_credentials.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_direct_question_uses_task_credentials_under_the_human_sender(delivery_resident, monkeypatch):
    from management_common.async_questions import CREDENTIAL_APPROVE, CREDENTIAL_DECLINE, question_message_parts
    from management_portal import society_service
    from management_portal.permission_system.facts import Fact
    from management_portal.society_service import contracts, resident_conversation_stream, resident_input_queue_domain, server
    from management_portal.society_service.transport import SocietyServiceClient
    from tests.cases.isa_test_support import run
    from tests.cases.permission_test_support import enable

    resident_id = delivery_resident["resident_id"]
    actor = "approval-browser-user"
    permissions = enable(monkeypatch)
    permissions.users(actor)
    permissions.place(Fact("resident", resident_id))
    permissions.grant(actor, resident_id, "@message")
    permissions.grant(actor, "@shared", "@admin")

    def dispatch(_client, method, params=None, **_options):
        assert method == "domain.call"
        domain, name = params["domain"], params["name"]
        handler, bound = server._bind_domain_call(domain, name, params["args"], params["kwargs"])
        return server._invoke_domain(contracts.operation_contract(domain, name), handler, bound)

    monkeypatch.setattr(SocietyServiceClient, "_request", dispatch)
    monkeypatch.setattr(society_service, "get_society_service", lambda: SocietyServiceClient())
    repository = resident_conversation_stream.get_repository()
    message = resident_input_queue_domain.build_message("agent", "", resident_id)
    credential = {"a": "@admin", "resource_id": "@shared"}
    message["content_parts"] = question_message_parts("", [{
        "question_index": 0, "title": "Approve?", "credentials": [credential],
        "options": [CREDENTIAL_APPROVE, CREDENTIAL_DECLINE],
    }], resident_id, message["message_id"])
    repository.append_message(resident_id, message)
    answer_id = message["content_parts"][0]["answer_message_id"]
    answers = [{"question_index": 0, "answer": CREDENTIAL_APPROVE}]
    args = {"resident_id": resident_id, "message": {"role": "user", "message_id": answer_id, "content_parts": [
        {"type": "text", "text": CREDENTIAL_APPROVE},
        {"type": "async_question_answer", "source_message_id": message["message_id"], "answers": answers},
    ]}}

    assert run("PrepareResidentInput", args, actor=actor).raise_for_error()["status"] == "prepared"
    loan = {"principal_id": resident_id, "message_id": answer_id, **credential}
    assert run("CreateTemporaryCredential", loan, actor=actor).raise_for_error()["state"] == "pending"
    assert repository.message(resident_id, answer_id) is None
    assert not permissions.holds(resident_id, "@admin", "@shared")
    assert run("DeliverMessage", args, actor=actor).raise_for_error()["message_id"] == answer_id
    accepted = repository.message(resident_id, answer_id)
    assert accepted["author_id"] == actor
    assert accepted["content_parts"][1]["answers"] == answers
    assert permissions.holds(resident_id, "@admin", "@shared")
