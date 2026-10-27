"""First-request snapshots for idempotent Family task admission.

The intent records what was requested and rendered, before delivery. Message
and execution state remain owned by Resident intake; this is not a work queue.
"""
from __future__ import annotations

import copy
import json
import re
from string import Template

COLUMNS = {"family_task_dispatches": ("lineage_id", "message_id", "intent_json")}


def create_schema(connection):
    connection.execute("""CREATE TABLE family_task_dispatches (
        lineage_id TEXT NOT NULL REFERENCES lineages(lineage_id),
        message_id TEXT NOT NULL, intent_json JSONB NOT NULL,
        PRIMARY KEY(lineage_id,message_id))""")


def _validate_original(repository, target_id, facade_id, mid, text, business_ref):
    from . import resident_input_queue_domain, resident_message_contract

    original = repository.message(target_id, mid)
    if original is None:
        return None, None
    if (original.get("author_id") != facade_id
            or repository.message_business_ref(target_id, mid) != business_ref):
        raise resident_message_contract.MessageIdentityConflict("Family task message_id belongs to another task")
    candidate = resident_input_queue_domain.build_message(
        "user", text, facade_id, message_id=mid, created_at=original["created_at"],
    )
    if "origin" in original:
        candidate["origin"] = copy.deepcopy(original["origin"])
    else:
        candidate.pop("origin", None)
    repository.validate_message_identity(target_id, candidate)
    return original, candidate


def _legacy_text(target, trigger, facts, original, text):
    """Match the known template; only its guide digest may have changed.

    The task facts and every literal/path remain exact. This recognizes a
    presentation version in a known slot, never extracts facts from a prompt.
    """
    from . import family_tasks, resident_message_contract

    body, block = family_tasks.context(target)
    entry = family_tasks.configured(body, block).get(trigger)
    identifiers = Template(entry["prompt"]).get_identifiers() if entry else []
    if "facts" not in identifiers:
        raise resident_message_contract.MessageIdentityConflict("Legacy Family task has no verifiable request snapshot")
    parts = original.get("content_parts") or []
    if "guide_version" not in identifiers or len(parts) != 1 or parts[0].get("type") != "text":
        return text
    old_text = parts[0].get("text", "")
    marker = "__family_legacy_guide_digest__"
    while marker in text or marker in old_text:
        marker += "_"
    pattern = re.escape(family_tasks.render(target, trigger, facts, guide_version=marker))
    pattern = pattern.replace(re.escape(marker), r"(?P<guide_version>[0-9a-f]{12})", 1)
    pattern = pattern.replace(re.escape(marker), r"(?P=guide_version)")
    return old_text if re.fullmatch(pattern, old_text) else text


def deliver(facade, target, trigger, facts, actor, *, business_ref, message_id=""):
    from management_common.json_columns import json_value
    from management_common.operation_identity import operation_identity_scope
    from management_common.principal import OperationIdentity
    from . import family_tasks, invariant_locks, resident_conversation_stream
    from . import resident_execution_work, resident_intake_domain
    from . import resident_message_contract
    from .state_store import get_lineage_data_store

    mid = resident_message_contract.message_identity(message_id)
    lineage_id, facade_id, target_id = facade["lineage_id"], facade["resident_id"], target["resident_id"]
    # JSON normalization gives object key order no semantic significance and
    # rejects non-finite/unsupported values before any intent is persisted.
    request = json.loads(json.dumps({"facade": facade_id, "target": target_id, "trigger": trigger,
                                    "facts": facts, "business_ref": business_ref}, allow_nan=False))
    with invariant_locks.catalog_lock(f"family-task-{lineage_id}-{mid}", operation="dispatch Family task"):
        database = get_lineage_data_store().database
        with database.read() as connection:
            row = connection.execute("SELECT intent_json FROM family_task_dispatches "
                                     "WHERE lineage_id=%s AND message_id=%s", (lineage_id, mid)).fetchone()
        intent = json_value(row["intent_json"]) if row else None
        repository = resident_conversation_stream.get_repository()
        if intent is not None:
            # Python considers 1 == True. JSON requests must retain their
            # types while ignoring only object-key ordering.
            if json.dumps(intent["request"], sort_keys=True, separators=(",", ":")) != json.dumps(
                request, sort_keys=True, separators=(",", ":"),
            ):
                raise resident_message_contract.MessageIdentityConflict("Family task message_id refers to a different request")
        else:
            text = family_tasks.render(target, trigger, facts)
            prior = repository.message(target_id, mid)
            if prior is not None:
                text = _legacy_text(target, trigger, facts, prior, text)
            original, _candidate = _validate_original(repository, target_id, facade_id, mid, text, business_ref)
            initiator = actor
            if original is not None:
                # Legacy inputs have no structured request snapshot. Adopt only
                # an original-envelope match from the known facts-bearing
                # template, preserving its historical guide digest as well.
                initiator = str(((original.get("origin") or {}).get("initiated_by") or {}).get("id") or facade_id)
            intent = {"request": request, "text": text, "initiated_by": initiator}
            # Commit the winning request before attempting admission. A process
            # interruption resumes this same intent, never a freshly rendered one.
            database.write(lambda connection: connection.execute(
                "INSERT INTO family_task_dispatches VALUES (%s,%s,%s)",
                (lineage_id, mid, json.dumps(intent, ensure_ascii=False, allow_nan=False)),
            ))
        original, candidate = _validate_original(
            repository, target_id, facade_id, mid, intent["text"], business_ref,
        )
        if original is not None and resident_intake_domain.existing_resident_delivery(
            target_id, candidate, bound_job=resident_execution_work.read(target), require_execution_binding=True,
        ):
            return {"resident_id": target_id, "message_id": mid, "accepted": True}
        # dispatch already authorized the current caller. Only the historical
        # message origin is frozen; its ISA invocation keeps its own caller.
        with operation_identity_scope(OperationIdentity.for_actor(facade_id, initiated_by=intent["initiated_by"])):
            return resident_intake_domain.deliver_resident_message_content(
                target_id, facade_id, intent["text"], business_ref=business_ref, message_id=mid,
            )
