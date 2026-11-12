"""Quota controls for the actual owners of a facade's unfinished requests.

Only durable relays, admission reviews and accepted role tasks identify owners.
Reading never chooses a member, captures routing preferences or starts work.
"""
from . import (family_facade, family_quota, family_task_dispatches, resident_access,
               resident_conversation_stream, resident_execution_gate, resident_mailbox,
               resident_record_domain)
from .state_store import get_lineage_data_store


def _unfinished(state):
    if state.get("state") in {"queued", "running"}:
        return True
    if state.get("state") == "failed":
        from management_portal.execution.provider_failures import is_usage_limit_failure
        return is_usage_limit_failure(state.get("error") or {})
    return False


def quota_switch(record, actor):
    repository = resident_conversation_stream.get_repository()
    records = {record["resident_id"]: record}
    candidates = {}
    seen = set()

    def get_record(rid):
        if rid not in records:
            records[rid] = resident_record_domain.find_resident(rid) or {}
        return records[rid]

    def candidate(rid, roots):
        target = get_record(rid)
        if target and not target.get("deleted_at") and not family_facade.is_facade(target):
            candidates.setdefault(rid, set()).update(roots)

    def walk(facade, requests, depth):
        if depth >= family_facade.MAX_RELAY_HOPS:
            return
        fid = facade["resident_id"]
        requests = {mid: {root for root in roots if (fid, mid, root) not in seen} for mid, roots in requests.items()}
        requests = {mid: roots for mid, roots in requests.items() if roots}
        seen.update((fid, mid, root) for mid, roots in requests.items() for root in roots)
        if not requests:
            return
        links = family_facade.relay_links(facade)
        source_states = repository.input_statuses_by_id([(fid, mid) for mid in requests])
        linked, unlinked = {}, {}
        for mid, roots in requests.items():
            source = source_states.get((fid, mid), {})
            if source.get("state") == "cancelled":
                continue
            link = links.get(mid) or {}
            if link.get("resident_id") and link.get("message_id"):
                linked.setdefault((link["resident_id"], link["message_id"]), set()).update(roots)
            elif source.get("state") in {"queued", "failed"}:
                unlinked[mid] = roots
        # Completed independent inputs cannot follow a Mailbox group. Their
        # delivery outcomes suffice; only unfinished candidates need the
        # richer grouping/work/reply projection. Nested facades are still
        # followed below even when their routing receipt is completed.
        outcomes = repository.input_statuses_by_id(list(linked)) if linked else {}
        pending = [key for key, state in outcomes.items() if _unfinished(state)]
        work = resident_mailbox.InputProjection(pending, repository).states() if pending else {}
        nested = {}
        for (rid, mid), roots in linked.items():
            target = get_record(rid)
            if family_facade.is_facade(target):
                nested.setdefault(rid, {}).setdefault(mid, set()).update(roots)
            elif _unfinished(work.get((rid, mid), {})):
                candidate(rid, roots)
        # A role is relevant only through its accepted task for this request.
        tasks = family_task_dispatches.request_tasks(facade, unlinked)
        task_states = resident_mailbox.InputProjection(
            [(task['target'], task['message_id']) for task in tasks], repository).states() if tasks else {}
        for task in tasks:
            if _unfinished(task_states.get((task['target'], task['message_id']), {})):
                candidate(task['target'], unlinked[task['facts']['message_id']])
        if unlinked:
            held = get_lineage_data_store().family_quota_reviews.by_keys([fid]).get(fid, {})
            for mid, review in (held.get("requests") or {}).items():
                if mid in unlinked and review.get("status") in {"waiting", "delivering"}:
                    candidate(review.get("member_id", ""), unlinked[mid])
        for rid, messages in nested.items():
            walk(get_record(rid), messages, depth + 1)

    fid = record['resident_id']
    inputs = repository.input_statuses(fid, states=("queued", "failed"))
    ids = set(family_facade.relay_links(record)) | {item['message_id'] for item in inputs}
    walk(record, {mid: {mid} for mid in ids}, 0)
    visible = {rid: records[rid] for rid in candidates
               if resident_access.resident_visible_to_actor(records[rid], actor)}
    snapshots = resident_execution_gate.project_states({
        rid: {"state": {"intake_state": resident_record_domain.resident_intake_state(target)}}
        for rid, target in visible.items()
    }, resident_records=visible)
    targets = []
    for rid, target in visible.items():
        gate = snapshots[rid]['state']['execution_gate']
        if gate.get('state') != 'closed' or gate.get('reason') not in family_quota.QUOTA_REASONS:
            continue
        value = family_quota.quota_switch_state(target, execution_gate=gate)
        role = (target.get('family') or {}).get('role') or 'member'
        targets.append({**value, "target_resident_id": rid,
                        "target_title": target.get('title') or rid, "target_role": role,
                        "target_role_label": {"router": "Router", "steward": "Steward", "member": "成员"}.get(role, role),
                        "can_manage": resident_access.resident_allows(target, actor).get('RestartSession') is True,
                        "request_message_ids": sorted(candidates[rid])})
    return {"targets": sorted(targets, key=lambda item: item['target_resident_id'])}
