"""Natural language is delivered unchanged; only the Resident interprets grants.

:module: resident.identity
:importance: P1
:design: decision_1e2eb869bc10, decision_b820faf42218
"""
from copy import deepcopy
from pathlib import Path

import pytest

from management_portal.society_service.isa import conversation
from tests.cases.test_credential_delivery_atomicity import delivery, loans  # noqa: F401
from tests.cases.resident_execution_test_support import resident_record  # noqa: F401
from tests.cases.resident_execution_test_support import VALID_PNG_BYTES
from tests.cases.permission_test_support import permissions  # noqa: F401


def plain(delivery, text):
    delivery.arguments.pop("temporary_credentials", None)
    delivery.arguments["message"] = {
        "message_id": "msg_" + "9" * 32, "role": "user",
        "content_parts": [{"type": "text", "text": text}],
    }


@pytest.mark.parametrize("text,extra", [
    ("我给你临时凭证", "none"),
    ("同意交出这些权限", "quote"),
    ("授予你协作权限，继续整理这张图片。", "attachment"),
    ("I authorize you to contribute. Please update the plan.", "context"),
])
def test_natural_language_is_not_rewritten_or_signed_during_delivery(delivery, text, extra):
    # The fixture has an unanswered credential question. Even its former exact
    # approval phrases remain ordinary input until the Resident interprets them.
    plain(delivery, text)
    message = delivery.arguments["message"]
    if extra == "quote":
        message["quote"] = {"message_id": "msg_permission_question"}
    elif extra == "attachment":
        image = Path(delivery.record["workspace_path"]) / "residents" / delivery.resident / "workspace" / "attachment.png"
        image.write_bytes(VALID_PNG_BYTES)
        message["content_parts"].append({"type": "image", "source": str(image)})
    elif extra == "context":
        message["content_parts"].append({"type": "context_reference", "context": {"text": "context"}})
    first = conversation.deliver_message(delivery.inv)
    assert first["message_id"] == message["message_id"] != delivery.message_id
    stored = delivery.repository.message(delivery.resident, message["message_id"])
    assert stored["author_id"] == "lender"
    assert stored["content_parts"][0] == {"type": "text", "text": text}
    assert not any(p["type"] == "async_question_answer" for p in stored["content_parts"])
    assert not loans(delivery)


def test_isa_audit_keeps_authenticated_plain_input_without_auto_grant(delivery):
    from management_portal.hatg.contracts import InstructionCall, InstructionContext
    from management_portal.society_service.isa import build_kernel

    plain(delivery, "我给你临时凭证")
    kernel = build_kernel()
    call = InstructionCall("DeliverMessage", deepcopy(delivery.arguments), idempotency_key="plain-input-audit")
    context = InstructionContext(caller="lender", entry="web")
    result = kernel.execute(call, context)
    assert result.status == "committed", result.to_dict()
    assert result.result["message_id"] == delivery.arguments["message"]["message_id"]
    assert kernel.store.get(result.instruction_id)["caller"] == "lender"
    stored = delivery.repository.message(delivery.resident, result.result["message_id"])
    assert stored["content_parts"] == delivery.arguments["message"]["content_parts"]
    replay = kernel.execute(call, context)
    assert replay.replayed and replay.result["message_id"] == result.result["message_id"]
    assert not loans(delivery)
