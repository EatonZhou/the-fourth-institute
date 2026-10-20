"""Deliver a template prompt once to each selected Resident."""

from .registry import TemplateError, result
from .values import render


def deliver(resident_id, message, actor, policy="queue"):
    from management_portal.hatg import routing
    from management_portal.hatg.contracts import InstructionContext

    executed = routing.execute(
        {
            "instruction": "DeliverMessage",
            "arguments": {
                "resident_id": resident_id,
                "message": {"content_parts": [{"type": "text", "text": message}]},
                "activation_policy": "enqueue_and_trigger"
                if policy == "queue"
                else "attach_to_next",
            },
        },
        InstructionContext(caller=actor, entry="lineage"),
    )
    if executed.status != "committed":
        from management_portal.hatg.contracts import InstructionError

        error = executed.error or {}
        raise InstructionError(
            error.get("code", "instruction.failed"),
            error.get("message", "模板消息投递失败"),
        )
    return executed.result


class PromptKind:
    key = "prompt"
    title = "施加 Prompt"
    targets = ("resident", "selection")

    def normalize(self, template):
        body = template["body"]
        if not isinstance(body.get("message"), str) or not body["message"].strip():
            raise TemplateError("Prompt 模板需要 message")
        body.setdefault("activation_policy", "queue")
        if body["activation_policy"] not in {"queue", "attach_to_next"}:
            raise TemplateError("未知 Prompt 投递方式")
        return template

    def describe(self, template):
        return {
            "steps": [
                "给每个目标 Resident 投递一次提示词。"
                if template["body"]["activation_policy"] == "queue"
                else "给每个目标 Resident 附加提示词，随下一条请求送达。"
            ],
            "persistent": False,
        }

    def editor(self):
        return {
            "fields": [
                {"name": "message", "type": "text", "label": "消息", "multiline": True},
                {
                    "name": "activation_policy",
                    "type": "enum",
                    "label": "投递方式",
                    "options": ["queue", "attach_to_next"],
                    "default": "queue",
                },
            ]
        }

    def apply(self, ctx):
        from .. import lineage_domain

        nodes = {
            node["id"]: node
            for node in lineage_domain._read_lineage(ctx.lineage_id, actor=ctx.actor)[
                "nodes"
            ]
        }
        if not ctx.target["resident_ids"]:
            raise TemplateError("Prompt 需要至少一个目标 Resident")
        receipts = []
        for resident_id in ctx.target["resident_ids"]:
            text = render(
                ctx.template["body"]["message"],
                ctx.parameters,
                roles={
                    "target": {
                        "id": resident_id,
                        "title": nodes[resident_id].get("title", resident_id),
                    }
                },
                lineage_id=ctx.lineage_id,
                title=ctx.title,
            )
            receipt = deliver(
                resident_id,
                str(text),
                ctx.actor,
                ctx.template["body"]["activation_policy"],
            )
            receipts.append(
                {"resident_id": resident_id, "message_id": receipt["message_id"]}
            )
        return result(
            f"已向 {len(receipts)} 个 Resident 投递提示词。", delivered=receipts
        )


KIND = PromptKind()
