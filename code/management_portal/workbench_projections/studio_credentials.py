"""Studio's task-scoped grant, stock scripts and conservative script upgrade.

The message owner reserves and accepts inputs; the permission owner issues and
activates task grants. This module only describes Studio's single-draft intent.
It never impersonates an actor, edits a graph or manages credential storage.
"""
from __future__ import annotations

from dataclasses import fields, is_dataclass
import re

from management_common.message_identity import derived_message_identity
from management_portal.society_service.lineage_effect_script import (
    ScriptSyntaxError, deliver_message_script, parse, quote,
)


REQUEST_SCHEMA = "tfi.projection-studio-request.v1"
JOB_FIELDS = ("id", "workbench_id", "resident_id", "lineage_id", "event_source_id",
              "effect_id", "base_revision", "request_prompt")


def submission_payload(job: dict, *, actor: str, store) -> dict:
    """Derive the request from the saved job under the actual initiating actor.

    Call after persisting the job and while holding its existing submission
    lock. Page input cannot supply issuer, grant items, recipient or message ID.
    Recovery observes the old receipt instead of constructing another request.
    """
    saved = store.job(job["id"], actor)
    if saved.get("created_by") != actor:
        raise PermissionError("只有原发起人可以提交投影任务")
    if any(saved.get(key) != job.get(key) for key in JOB_FIELDS):
        raise ValueError("投影提交与已保存任务绑定不一致")
    if job.get("receipt"):
        from management_portal.lineage_workflows import WorkflowBindings
        expected = WorkflowBindings.prepare(saved, request_id=saved["id"])
        for key in ("lineage_id", "resident_id", "event_source_id", "effect_id", "request_id", "idempotency_key"):
            if job["receipt"].get(key) != expected.get(key):
                raise ValueError("投影提交回执与已保存任务绑定不一致")
    if saved.get("status") != "submitting" or saved.get("message_id"):
        raise ValueError("投影任务已有输入或已结束；请恢复原回执")
    for key, pattern in (("workbench_id", r"workbench_[0-9a-f]{12}"),
                         ("resident_id", r"resident_[0-9a-f]{12}")):
        if not re.fullmatch(pattern, str(saved.get(key) or "")):
            raise ValueError("投影任务缺少有效的工作台或设计者绑定")
    store.require_write(store.get(saved["workbench_id"], actor), actor)
    message_id = derived_message_identity("studio-task", actor, saved["id"],
                                          saved["resident_id"], saved["workbench_id"])
    return {"studio_request": {
        "schema": REQUEST_SCHEMA, "job_id": saved["id"],
        "workbench_id": saved["workbench_id"], "resident_id": saved["resident_id"],
        "message_id": message_id,
    }}


def studio_script(prompt: str) -> str:
    """Prepare → task grant → release, with only the final input as output.

    PrepareResidentInput and DeliverMessage use the Effect runtime's same
    message composition and route policy. Their authored arguments are equal.
    A failed/interrupted grant leaves a non-runnable preparation: the existing
    Effect journal can resume it, or CancelInput can retire it explicitly.
    """
    return '\n'.join([
        'request="${event.payload.data.studio_request}"',
        f'if [ "${{request.schema}}" != {quote(REQUEST_SCHEMA)} ]; then fail "缺少投影任务绑定"; fi',
        'if [ -z "${request.job_id}" ] || [ -z "${request.message_id}" ]; then fail "缺少任务或输入标识"; fi',
        'if [ "${request.resident_id}" != "${endpoint.target}" ]; then fail "设计者与任务绑定不一致"; fi',
        'if [ "${request.workbench_id:0:10}" != "workbench_" ]; then fail "授权目标必须是任务稿件"; fi',
        'message:={"message_id":${request.message_id},"role":"user","content_parts":[{"type":"text","text":'
        + quote(prompt) + '}] }',
        'prepared=$(PrepareResidentInput resident_id="${endpoint.target}" message="${message}" activation_policy="enqueue_and_trigger")',
        'if [ "${prepared.status}" != "prepared" ] && [ "${prepared.status}" != "released" ]; then fail "准备输入已取消或不可用"; fi',
        'CreateTemporaryCredential principal_id="${endpoint.target}" message_id="${prepared.message_id}" a="@contributor" resource_id="${request.workbench_id}"',
        'delivered=$(DeliverMessage resident_id="${endpoint.target}" message="${message}" activation_policy="enqueue_and_trigger")',
        'output resident_id="${delivered.resident_id}" message_id="${delivered.message_id}"',
    ])


def studio_effect_config(prompt: str) -> dict:
    return {"script": studio_script(prompt), "endpoints": ["target"]}


def _semantic(value):
    """Compare the complete AST, retaining references, slices and literal types."""
    if is_dataclass(value):
        return type(value).__name__, tuple((field.name, _semantic(getattr(value, field.name)))
                                         for field in fields(value) if field.name not in {"line", "offset"})
    if isinstance(value, (tuple, list)):
        return tuple(_semantic(child) for child in value)
    return value


def _tree(source):
    try:
        return _semantic(parse(source))
    except (ScriptSyntaxError, TypeError):
        return None


def _stock():
    # The Studio owns its prompts. Lazy import keeps the migration hook pure
    # and avoids an installation/import cycle.
    from .studio import FLOWS
    for _title, prompt in FLOWS.values():
        yield prompt, studio_script(prompt)


def migrate_studio_script(source: str) -> str:
    """Upgrade only the complete original three scripts; no I/O or graph edit.

    Unknown/custom scripts (including extra statements, constant credentials or
    different endpoints) are preserved for explicit review. Migrated scripts
    are unchanged. The caller separately guards in-flight graph activations.
    """
    tree = _tree(source)
    if tree is None:
        return source
    for prompt, replacement in _stock():
        for constants in ({}, {"activation_policy": "enqueue_and_trigger"}):
            old = deliver_message_script(prompt, lend_temporary_credentials=True, **constants)
            if tree == _tree(old):
                return replacement
    return source


def is_studio_script(source: str, *, prompt: str | None = None) -> bool:
    """Ownership predicate for the public workflow installer, old or current."""
    tree = _tree(source)
    if tree is None:
        return False
    for fixed, current in _stock():
        if prompt is not None and fixed != prompt:
            continue
        if tree == _tree(current):
            return True
        for constants in ({}, {"activation_policy": "enqueue_and_trigger"}):
            if tree == _tree(deliver_message_script(fixed, lend_temporary_credentials=True, **constants)):
                return True
    return False
