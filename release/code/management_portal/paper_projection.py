from __future__ import annotations

from copy import deepcopy

from management_common.format_tags import schema_matches

from .workbench_projections import native


class PaperInputIdentityError(ValueError):
    """A completion cannot be associated with its confirmed Paper input."""


PAPER_INPUT_RECEIPT_SCHEMA = "tfi.paper-resident-input.v1"
BUILTIN = {"name": "paper", "title": "Paper", "path": "/paper", "description": "论文阅读、研究与评审工作台"}


ROLES = {
    "paper:collector": ("Paper Collector", "按本次研究方向收集、筛选并登记论文，保留来源和实际登记结果。"),
    "paper:registration-metadata": ("PDF 登记信息提取", "从本次 PDF 和来源提取登记字段，区分已证实信息与待核对信息。"),
    "paper:ccf": ("CCF 论文信息补全", "根据本次目录、论文来源和补全要求返回可追溯的论文信息。"),
    "paper:summary": ("简短论文摘要", "总结本次论文的核心问题、方法和结果，不捏造实验数据。"),
    "paper:deep-read": ("Deep Read", "深入分析本次论文的方法、假设、证据和局限，按指定产物协议完成。"),
    "paper:review": ("AI Review", "对本次论文开展有证据的评审，区分观察、推断和建议。"),
    "paper:exp": ("Experiment Extraction", "提取本次论文的实验设置、指标和结果，并保留原文依据。"),
    "paper:figure": ("Figure Explanation", "解释本次论文图表及其证据，区分图中事实和推断。若本次提供结构化生成结果协议则严格返回该结果，否则直接给出解释。"),
    "paper:figure-generation": ("Figure Generation / Editing", "按本次请求生成或编辑图片，保留真实图片产物，不将文字描述作为生成完成。"),
}


def definitions(role: str) -> list[dict[str, str]]:
    if role == "system:semantic-search":
        return [{"name": role, "title": "Portal 语义搜索", "prompt": "在本次已授权资源和查询范围内进行语义检索，严格遵守本次输出协议，不扩大资源访问范围。"}]
    if role not in ROLES:
        return []
    title, purpose = ROLES[role]
    return [{"name": role, "title": title, "prompt": purpose + " 每次输入是独立任务，论文和评审重点仅来自本次 prompt。内容生成与业务正式保存是不同状态，只有取得业务保存回执才能声明已正式保存。遵循本次明确提供的输出和提交协议；若约定由业务服务保存，只返回生成结果，不自行寻找或重复执行提交。未提供提交协议时直接交付请求的内容，未执行未被要求的业务保存不视为生成失败，也不得声称已保存。实际生成失败或明确要求的提交失败必须如实报告。"}]


def _workflow(role: str) -> dict:
    matches = definitions(role)
    if not matches:
        raise ValueError(f"未定义的 Paper 功能角色：{role}")
    return matches[0]


def receipt_from_resident_handle(handle: dict, *, requested_message_id: str = "") -> dict:
    """Translate the current shared Resident handle into Paper's input receipt."""
    pending = (handle.get("status") in {"assigned", "prepared", "queued", "pending"}
               or handle.get("input_identity_status") == "unconfirmed")
    canonical = str(handle.get("message_id") or "") if not pending else ""
    receipt = {key: str(handle.get(key) or "") for key in (
        "resident_id", "lineage_id", "event_id", "event_source_id", "effect_id", "trigger_path_id",
    )}
    receipt.update({
        "schema": PAPER_INPUT_RECEIPT_SCHEMA,
        "requested_message_id": str(handle.get("requested_message_id") or requested_message_id),
        "planned_message_id": str(handle.get("planned_message_id") or (handle.get("message_id") if pending else "") or ""),
        "input_message_id": canonical,
        "input_identity_status": "delivered" if canonical else "unconfirmed",
        "reply_message_ids": list(handle.get("reply_message_ids") or []),
        "usage_message_id": str(handle.get("usage_message_id") or ""),
    })
    return receipt


def input_receipt(receipt: dict) -> dict:
    """Validate and normalize one current Paper input receipt."""
    if not schema_matches(receipt.get("schema"), "tfi.paper-resident-input.v1"):
        raise PaperInputIdentityError(
            f"Paper input receipt requires schema {PAPER_INPUT_RECEIPT_SCHEMA}"
        )
    normalized = {key: str(receipt.get(key) or "") for key in (
        "resident_id", "lineage_id", "event_id", "event_source_id", "effect_id", "trigger_path_id",
        "requested_message_id", "planned_message_id", "input_message_id", "usage_message_id",
    )}
    normalized.update({
        "schema": PAPER_INPUT_RECEIPT_SCHEMA,
        "input_identity_status": "delivered" if normalized["input_message_id"] else "unconfirmed",
        "reply_message_ids": [str(value) for value in receipt.get("reply_message_ids") or [] if str(value)],
    })
    return normalized


def _merge_receipt(previous: dict, current: dict) -> dict:
    for key in ("resident_id", "lineage_id", "input_message_id"):
        if previous.get(key) and current.get(key) and previous[key] != current[key]:
            raise PaperInputIdentityError(f"Paper invocation changed its confirmed {key}")
    merged = {**previous, **{key: value for key, value in current.items() if value not in ("", [])}}
    for key, value in current.items():
        merged.setdefault(key, value)
    if merged.get("input_message_id"):
        merged["input_identity_status"] = "delivered"
    return merged


def _completed_result(result: dict, previous: dict, requested_message_id: str = "") -> dict:
    actual = receipt_from_resident_handle(result, requested_message_id=requested_message_id)
    if not actual["resident_id"] or not actual["input_message_id"]:
        raise PaperInputIdentityError("Paper completion did not return a canonical Resident input identity")
    receipt = _merge_receipt(previous, actual)
    replies, usage_message_id = [], ""
    for message in result.get("messages") or []:
        if not isinstance(message, dict) or message.get("role") != "agent":
            raise PaperInputIdentityError("Paper Resident reply must be a canonical agent message")
        reply_id = str(message.get("message_id") or "")
        if not reply_id:
            raise PaperInputIdentityError("Paper Resident reply is missing its message identity")
        if reply_id == receipt["input_message_id"]:
            raise PaperInputIdentityError("Paper input identity cannot also identify a Resident reply")
        if message.get("reply_to_message_id") != receipt["input_message_id"]:
            raise PaperInputIdentityError("Paper Resident reply does not reference the confirmed input identity")
        if reply_id not in replies:
            replies.append(reply_id)
        if isinstance(message.get("usage"), dict):
            usage_message_id = reply_id
    receipt.update(reply_message_ids=replies, usage_message_id=usage_message_id)
    usage = deepcopy(result.get("usage") or {})
    if usage.get("message_id"):
        usage["usage_reported_message_id"] = usage["message_id"]
    usage.pop("message_id", None)
    usage.update(deepcopy(receipt))
    projected = {**result}
    projected.pop("message_id", None)
    projected.update({
        "input_message_id": receipt["input_message_id"],
        "reply_message_ids": replies,
        "usage_message_id": usage_message_id,
        "invocation": receipt,
        "usage": usage,
    })
    return projected


def _call(method, options: dict) -> dict:
    requested = str(options.get("message_id") or "")
    assigned, submitted = options.get("on_assigned"), options.get("on_submitted")
    latest: dict = {}

    def remember(handle: dict, *, assignment: bool = False) -> None:
        nonlocal latest
        incoming = {**handle, "input_identity_status": "unconfirmed"} if assignment else handle
        latest = _merge_receipt(latest, receipt_from_resident_handle(incoming, requested_message_id=requested))
        callback = assigned if assignment else submitted
        if callback:
            callback(deepcopy(latest))

    result = method("paper", workflow=_workflow(options["resident_scope"]), **{
        **options, "on_assigned": lambda handle: remember(handle, assignment=True), "on_submitted": remember,
    })
    result = _completed_result(result, latest, requested)
    # Idempotent metadata reconciliation, not a second delivery. This also carries
    # the final reply/usage identities that were unavailable at submission time.
    if submitted:
        submitted(deepcopy(result["invocation"]))
    return result


def turn(*, cwd: str = "", **options) -> dict:
    """Deliver one task to the role Resident bound to ``resident_scope`` and wait for its reply.

    The bound Resident runs the task with its own Provider, model and
    settings; Paper sends only the task and its correlation.
    """
    return _call(native.turn, {**options, "cwd": cwd})


def resume(*, on_submitted=None, receipt: dict | None = None, **options) -> dict:
    from .resident_handoff import resume_and_wait

    previous = input_receipt(receipt or {})
    if not previous["input_message_id"]:
        raise PaperInputIdentityError("Paper cannot resume an unconfirmed input identity")
    if str(options.get("message_id") or "") != previous["input_message_id"]:
        raise PaperInputIdentityError("Paper resume message_id does not match its confirmed input_message_id")
    result = _completed_result(resume_and_wait(**options), previous)
    if on_submitted:
        on_submitted(deepcopy(result["invocation"]))
    return result
