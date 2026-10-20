"""One-shot Effect scripts, dispatched with the applying actor's authority."""

from .registry import TemplateError, result
from .values import render_script


class ScriptKind:
    key = "script"
    title = "执行一次脚本"
    targets = ("resident", "selection", "canvas")

    def normalize(self, template):
        from .. import lineage_effect_script

        body = template["body"]
        if not isinstance(body.get("script"), str) or not body["script"].strip():
            raise TemplateError("script 模板需要脚本")
        body.setdefault("endpoints", ["target"])
        if not isinstance(body["endpoints"], list) or any(
            item != "target" for item in body["endpoints"]
        ):
            raise TemplateError("一次性脚本只支持 target endpoint")
        lineage_effect_script.parse(body["script"])
        return template

    def describe(self, template):
        return {
            "steps": [
                "按施加者的权限执行一次 Effect 脚本。",
                "选中的 Resident 作为 target endpoint。",
            ],
            "persistent": False,
        }

    def editor(self):
        return {
            "fields": [
                {
                    "name": "script",
                    "type": "code",
                    "language": "shell",
                    "label": "Effect 脚本",
                },
                {"name": "endpoints", "type": "array", "label": "Endpoints"},
            ]
        }

    def apply(self, ctx):
        from management_portal.hatg import routing
        from management_portal.hatg.contracts import (
            InstructionContext,
            InstructionError,
        )

        from .. import lineage_effect_script as engine
        from ..lineage_primitives import EffectInvocation, make_event
        from ..lineage_program_effect_rules import prepare_call_arguments

        if ctx.template["body"]["endpoints"] and not ctx.target["resident_ids"]:
            raise TemplateError("脚本需要目标 Resident")
        receipts = []
        trigger_path_ids = []
        invocation = EffectInvocation(
            id="template",
            activation=make_event("manual.triggered"),
            arguments={},
            include_envelope=False,
        )

        def execute(request):
            arguments = prepare_call_arguments(
                request.instruction, request.arguments, invocation
            )
            response = routing.execute(
                {"instruction": request.instruction, "arguments": arguments},
                InstructionContext(caller=ctx.actor, entry="lineage"),
            )
            if response.status == "committed":
                value = response.result or {}
                if request.instruction == "DeliverMessage":
                    receipts.append(
                        {
                            "resident_id": value["resident_id"],
                            "message_id": value["message_id"],
                        }
                    )
                if value.get("trigger_path_id"):
                    trigger_path_ids.append(value["trigger_path_id"])
            return engine.CallResult(
                response.status == "committed",
                response.result or {},
                response.error or {},
                response.instruction_id,
            )

        roles = {
            "target": [{"id": rid, "title": rid} for rid in ctx.target["resident_ids"]]
        }
        source = render_script(
            ctx.template["body"]["script"],
            ctx.parameters,
            roles=roles,
            lineage_id=ctx.lineage_id,
            title=ctx.title,
        )
        for target in (
            ctx.target["resident_ids"] if ctx.template["body"]["endpoints"] else [None]
        ):
            endpoints = {"target": target} if target else {}
            outcome = engine.Interpreter(
                engine.parse(source),
                {"endpoint": endpoints, "arguments": ctx.parameters},
                execute,
            ).run()
            if outcome.status != "succeeded":
                raise InstructionError(
                    outcome.error.code,
                    outcome.error.message,
                    retryable=outcome.error.retryable,
                )
        return result(
            "已执行模板脚本。",
            delivered=receipts,
            trigger_path_ids=trigger_path_ids,
            output=outcome.outputs,
        )


KIND = ScriptKind()
