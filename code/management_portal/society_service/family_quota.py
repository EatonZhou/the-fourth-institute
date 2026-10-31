"""Family admission reviews and automatic account availability.

The facade retains a request until its steward explicitly releases it in two
cases: the chosen member's limiting quota is low, or the Router found that no
member's duties cover it - then the steward decides whether a new
member takes it. Platform supervision restores Router/Steward intake and
migrates Family executors' accounts before admitting the next turn.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Mapping

from . import family_facade, family_role_quota_questions, family_storage, resident_execution_work, resident_message_contract
from .domain_ports import execution_port
from .resident_account_policy import (
    MIGRATION_RESERVE_PERCENT, fallback_account, resident_account, sufficient_reserve, migration_target_options,
    reserve_remaining,
)
from .state_store import get_lineage_data_store
from . import lineage_aggregate
from management_common.clock import utc_now_iso

MIGRATION_FIELDS = frozenset({
    "epoch", "attempts", "tried_accounts", "checked_at", "pending_restart", "owner_notified",
    "status", "reason", "account_wait", "question_facade_id", "question_message_id",
})
RECHECK_SECONDS = 60
MAX_MIGRATIONS = 2


def role_migration(record: dict[str, Any]) -> dict[str, Any]:
    """Read this operating role's reserve operation, not Resident settings."""
    state = get_lineage_data_store().family_quota_migrations.get(record['lineage_id'], record['resident_id'])
    _validate_migration(state)
    return state


def _validate_migration(state: dict[str, Any]) -> None:
    if set(state) - MIGRATION_FIELDS:
        raise ValueError("Unknown steward reserve migration fields")


def role_availability(record: dict[str, Any], *, execution_gate: dict[str, Any] | None = None) -> dict[str, Any]:
    from . import resident_record_domain
    state = role_migration(record)
    intake = resident_record_domain.resident_intake_state(record)
    if intake != "open":
        return {"status": "paused", "reason": "接单已关闭；门面开放时平台将恢复，关闭门面可暂停 Family", "intake": intake}
    if state.get("account_wait"):
        return {"status": "unavailable", "reason": state["account_wait"], "intake": intake}
    from .resident_work_recovery import latest_failed_input, recovery_entry
    failed = latest_failed_input(record)
    if failed:
        recovery = recovery_entry(record, failed.get("message_id", ""))
        if not recovery.get("disabled") and (recovery.get("owner_notified") or recovery.get("attempts", 0) >= 2):
            reason = "自动恢复未成功" + ("，已通知 Family 创建人" if recovery.get("owner_notified") else "，等待恢复")
            return {"status": "unavailable", "reason": reason, "intake": intake}
    status, reason = state.get("status"), state.get("reason") or ""
    if not status and execution_gate and execution_gate.get("state") != "open":
        from .resident_execution_gate import HINTS
        status = "unavailable" if execution_gate.get("state") == "closed" else "unknown"
        reason = HINTS.get(execution_gate.get("reason"), "后端执行状态尚未确认")
    return {"status": status or "available", "reason": reason, "intake": intake}


def relay_message(facade_id: str, message_id: str, member_id: str, actor: str,
                  *, quota_reviewed: bool = False, reason: str = "", scope_gap: str = "",
                  scope_reviewed: bool = False) -> dict[str, Any]:
    result = _relay_message(facade_id, message_id, member_id, actor, quota_reviewed=quota_reviewed,
                            reason=reason, scope_gap=scope_gap, scope_reviewed=scope_reviewed)
    if result["status"] in {"waiting_for_quota_review", "waiting_for_steward"}:
        from . import changes
        changes.resident_changed(facade_id)
    return result




def _relay_message(facade_id: str, message_id: str, member_id: str, actor: str,
                   *, quota_reviewed: bool, reason: str, scope_gap: str = "",
                   scope_reviewed: bool = False) -> dict[str, Any]:
    from . import outward_messages, resident_conversation_stream, resident_protocol
    from management_common.async_questions import answered_set
    from . import resident_record_domain

    facade = resident_record_domain.find_resident(facade_id)
    if not family_facade.is_facade(facade):
        raise ValueError("目标必须是 Family 门面")
    steward_id = family_facade.family_steward_id(facade)
    approved = quota_reviewed
    if quota_reviewed and not reason.strip():
        raise ValueError("额度复核需要填写判断理由")
    # The steward's arrangement of a request no member owns, like its quota review, is a review.
    arranging = scope_reviewed
    if scope_reviewed and not reason.strip():
        raise ValueError("安排成员需要填写判断理由")
    if (approved or arranging) and not family_facade.is_own_steward(facade, actor):
        raise PermissionError("只有本 Family 的 steward 可以批准复核")
    mark = family_facade.relay_mark(facade_id, message_id)
    if not (approved or arranging):
        family_facade.validate_relay(mark, actor)
    scope_gap = str(scope_gap or "").strip()
    repository = resident_conversation_stream.get_repository()
    message = repository.message(facade_id, message_id)
    if not message:
        raise KeyError("门面消息不存在")
    # An inner facade routes without running a model; its final member owns quota admission.
    to_steward = bool(steward_id) and member_id == steward_id
    to_facade = family_facade.is_facade(resident_record_domain.find_resident(member_id))
    if not family_facade.is_relay_target(facade, member_id):
        raise ValueError("所选 Resident 已不属于这个 Family")
    state = repository.input_state(facade_id, message_id)
    if state.get("state") == "cancelled":
        # Its sender or an owner stopped it while it was being routed: the routing just ends.
        return {"status": "withdrawn", "member_id": member_id}
    if state.get("state") == "failed":
        raise ValueError("门面请求已失败，不能放行")
    arranged = (get_lineage_data_store().family_quota_reviews.get(facade['lineage_id'], facade_id)
                .get("scope_requests") or {}).get(message_id) or {}
    link = family_facade.relay_links(facade).get(message_id)
    if arranged.get("status") == "arranged" and not (approved or arranging):
        # A replayed Router relay finds the steward's arrangement in place.
        return {"status": "arranged", "member_id": arranged["member_id"], "message_id": (link or {}).get("message_id", "")}
    if link:
        if link["resident_id"] != member_id:
            raise ValueError("该门面请求已经投给其他成员")
        return {"status": "relayed", "member_id": member_id, "message_id": link["message_id"]}
    from . import family_chat_preferences
    family_chat_preferences.validate_target(facade, message, member_id)
    # The platform validates the original delegation before restoring its
    # sender. Review messages, separately, speak as the facade.
    from management_common.operation_identity import operation_identity_scope
    from management_common.principal import OperationIdentity

    pending_notices = []
    existing_review = (get_lineage_data_store().family_quota_reviews.get(facade['lineage_id'], facade_id)
                       .get("requests") or {}).get(message_id)
    accounts = [] if existing_review or to_facade else list(execution_port.service().accounts().get("accounts") or [])

    def prepare(reviews):
        state = repository.input_state(facade_id, message_id).get("state")
        if state == "cancelled":
            return {"status": "withdrawn", "member_id": member_id}
        if state == "failed":
            raise ValueError("门面请求已失败，不能放行")
        scopes = reviews.setdefault("scope_requests", {})
        scope = scopes.get(message_id) or {}
        if scope.get("status") == "waiting":
            if not arranging:
                if not repository.message(steward_id, scope["steward_message_id"]):
                    pending_notices.append(dict(
                        facade_id=facade_id, steward_id=steward_id, initiated_by=actor,
                        content="", task_trigger="recruit", task_facts={"facade": facade_id,
                            "message_id": message_id, "member_id": scope["suggested_member_id"],
                            "gap": scope["gap"], "reason": scope["reason"]},
                        message_id=scope["steward_message_id"], business_ref=f"{facade_id}/scope-review/{message_id}",
                    ))
                return {"status": "waiting_for_steward", "resident_id": facade_id, "message_id": message_id,
                        "member_id": scope["suggested_member_id"], "steward_message_id": scope["steward_message_id"]}
            scope.update({"status": "arranged", "member_id": member_id, "arranged_by": actor,
                          "reason": reason.strip()[:500], "arranged_at": utc_now_iso()})
        elif arranging and scope.get("status") != "arranged":
            raise ValueError("该请求没有等待安排成员的职责空缺")
        elif scope_gap and not scope and not approved and steward_id:
            # No member's duties cover it: the steward decides who takes it, maybe a new member.
            review_id = resident_message_contract.derived_message_identity(
                "family-scope-review", facade_id, message_id,
            )
            scopes[message_id] = {"status": "waiting", "suggested_member_id": member_id, "gap": scope_gap[:300],
                                  "reason": reason.strip()[:300], "steward_message_id": review_id,
                                  "created_at": utc_now_iso()}
            if not repository.message(steward_id, review_id):
                pending_notices.append(dict(
                    facade_id=facade_id, steward_id=steward_id, initiated_by=actor,
                    content="", task_trigger="recruit", task_facts={"facade": facade_id,
                        "message_id": message_id, "member_id": member_id,
                        "gap": scope_gap[:300], "reason": reason.strip()[:300]},
                    message_id=review_id, business_ref=f"{facade_id}/scope-review/{message_id}",
                ))
            return {"status": "waiting_for_steward", "resident_id": facade_id, "message_id": message_id,
                    "member_id": member_id, "steward_message_id": review_id}
        entries = reviews.setdefault("requests", {})
        review = entries.get(message_id) or {}
        if review and review["member_id"] != member_id:
            # A typed answer to an outward Steward question could previously
            # be selected for the sole business member, then fail admission
            # because that member has no question set. No copy was accepted;
            # let its verified original recipient retry instead of retaining
            # the failed selection forever. Accepted copies still use reassign.
            source_id = answered_set(message)
            if (not to_steward or not source_id or review.get("status") != "delivering"
                    or repository.message(review["member_id"], message_id) is not None
                    or repository.message(review["member_id"], source_id) is not None):
                raise ValueError("该门面请求已经选定其他成员")
            review = {}
        if review.get("status") == "relayed":
            return dict(review["result"])
        if approved and review.get("status") not in {"waiting", "delivering"}:
            raise ValueError("该请求没有待放行的额度复核")
        if not review:
            account = resident_account(resident_record_domain.find_resident(member_id) or {}, accounts)
            # The steward keeps its own reserve (ensure_role_reserve) and does not review itself.
            remaining = None if to_steward or to_facade else reserve_remaining(account)
            review = {"member_id": member_id, "account_id": account.get("account_id", ""),
                      "quota_remaining_percent": remaining, "created_at": utc_now_iso()}
            entries[message_id] = review
            if remaining is not None and remaining < MIGRATION_RESERVE_PERCENT:
                review["status"] = "waiting"
        if to_facade and review.get("status") == "waiting":
            # Retire an earlier review of a facade's unused account, retaining
            # its original observation; the inner Family still checks its worker.
            review.update(status="delivering", quota_not_applicable="family_facade")
        if review.get("status") == "waiting" and not approved:
            if not steward_id:
                raise ValueError("低周额度请求需要 Family steward 复核")
            review_id = resident_message_contract.derived_message_identity(
                "family-quota-review", facade_id, message_id,
            )
            if not repository.message(steward_id, review_id):
                pending_notices.append(dict(
                    facade_id=facade_id, steward_id=steward_id, initiated_by=actor,
                    content="", task_trigger="quota", task_facts={"facade": facade_id,
                        "message_id": message_id, "member_id": member_id, "account_id": review["account_id"],
                        "quota_remaining_percent": review.get("quota_remaining_percent")},
                    message_id=review_id, business_ref=f"{facade_id}/quota-review/{message_id}",
                ))
            return {"status": "waiting_for_quota_review", "resident_id": facade_id,
                    "message_id": message_id, "member_id": member_id,
                    "steward_message_id": review_id}
        if approved:
            review["reviewed_by"] = actor
            review["reason"] = reason.strip()
        if repository.input_state(facade_id, message_id).get("state") in {"cancelled", "failed"}:
            raise ValueError("门面请求已取消或失败，不能放行")
        review["status"] = "delivering"
        return {"status": "delivering", "member_id": member_id}

    with get_lineage_data_store().family_quota_reviews.transaction(facade['lineage_id'], facade_id) as reviews:
        admission = prepare(reviews)
    for notice in pending_notices:
        family_facade.tell_steward(**notice)
    if admission["status"] != "delivering":
        return admission
    # An answer to a member's questions keeps the question set's answer ID,
    # so the member's Conversation knows the set answered.
    delivered_id = message_id if answered_set(message) else resident_message_contract.derived_message_identity(
        "family-relay", facade_id, message_id, member_id,
    )
    # The validated facade request delegates this unchanged delivery on
    # behalf of its original author. The approving steward remains the
    # initiator, never the author of the member's input.
    author = str(message["author_id"])
    with operation_identity_scope(OperationIdentity.for_actor(author, initiated_by=actor)):
        result = outward_messages.deliver_to_resident(
            {"resident_id": member_id, "message": {
                "role": "user", "message_id": delivered_id,
                "content_parts": message["content_parts"],
            }, "activation_policy": "enqueue_and_trigger"}, author,
            delivery_context=resident_protocol.ResidentDeliveryContext(
                lineage_id=facade["lineage_id"], business_ref=mark,
            ),
        )
    # Delivery and its after-commit effects run without the review-row lock.
    family_facade.complete_relay((facade_id, message_id), member_id, str(result.get("message_id") or delivered_id))
    with get_lineage_data_store().family_quota_reviews.transaction(facade['lineage_id'], facade_id) as reviews:
        review = reviews["requests"][message_id]
        withdrawn = repository.input_state(facade_id, message_id).get("state") == "cancelled"
        review["status"] = "withdrawn" if withdrawn else "relayed"
        review["result"] = ({"status": "withdrawn", "member_id": member_id} if withdrawn else
                            {"status": "relayed", "member_id": member_id,
                             "message_id": str(result.get("message_id") or delivered_id)})
        return dict(review["result"])


def auto_migration_enabled(record: dict) -> bool:
    """The immediate Family owns its migration policy; ordinary Residents have none."""
    family = record.get("family") or {}
    if family.get("role") not in {"member", "router", "steward"} or record.get("deleted_at"):
        return False
    with get_lineage_data_store().database.read() as connection:
        row = connection.execute("SELECT auto_migrate FROM families WHERE family_id=%s AND deleted_at IS NULL",
                                 (family["family_id"],)).fetchone()
    return bool(row and row["auto_migrate"])


def auto_migration_priority(record: dict) -> list[str]:
    """Read the same immediate Family policy for admission and failure recovery."""
    family = record.get("family") or {}
    if family.get("role") not in {"member", "router", "steward"} or record.get("deleted_at"):
        return []
    with get_lineage_data_store().database.read() as connection:
        row = connection.execute("SELECT auto_migrate_priority FROM families WHERE family_id=%s AND deleted_at IS NULL",
                                 (family["family_id"],)).fetchone()
    return list(row["auto_migrate_priority"]) if row else []


def _follow_facade_quota_release(record: dict[str, Any], facade: dict[str, Any]) -> dict[str, Any]:
    """Router and Steward share the facade's manual quota release.

    A person releases a Family on its facade; the roles that actually run for
    it then keep working on the used-up account's credits like the facade
    would. The role keeps its own binding and revision so the Execution
    contract (binding_id, revision, enabled) is unchanged; a facade without a
    release leaves the role's own setting alone.
    """
    from . import resident_backend, resident_input_queue_domain, resident_record_domain
    release = resident_record_domain.resident_quota_override(facade)
    if not release or resident_record_domain.resident_quota_override(record).get("enabled") is release["enabled"]:
        return record
    with resident_record_domain.state_lock(record["resident_id"]):
        current = resident_record_domain.find_resident_canonical(record["resident_id"])
        if not current or current.get("deleted_at"):
            return record
        policy = resident_record_domain.resident_quota_override(current)
        if policy.get("enabled") is not release["enabled"]:
            current["quota_reserve_override"] = {
                "binding_id": resident_backend.execution_binding_id(current),
                "enabled": release["enabled"],
                "revision": int(policy.get("revision") or 0) + 1,
            }
            resident_record_domain.save_resident_record(current, commit_configuration=True)
    resident_input_queue_domain.sync_resident_quota_overrides([current])
    return resident_record_domain.find_resident(record["resident_id"]) or current


def _facade_of(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """The live facade of a Family Resident's Family."""
    from . import resident_record_domain
    with get_lineage_data_store().database.read() as connection:
        facade_ids = family_storage.roles_of(connection, record["family"]["family_id"])["facade"]
    facade = resident_record_domain.find_resident(facade_ids[0]) if facade_ids else None
    return facade if facade and not facade.get("deleted_at") else None


def _restore_role_intake(record: dict[str, Any]) -> dict[str, Any] | None:
    """Keep internal operating roles open unless their whole Family is paused.

    Reuse the intake transition owner; this never recreates a deleted role,
    requeues a cancelled input or restarts a Session. The roles follow the
    facade's manual quota release, also while the Family is paused.
    """
    from . import resident_management_domain
    from . import resident_record_domain
    if not family_facade.is_operating_role(record) or record.get("deleted_at"):
        return None
    facade = _facade_of(record)
    if not facade:
        return None
    record = _follow_facade_quota_release(record, facade)
    if resident_record_domain.resident_intake_state(facade) != "open":
        return None
    if resident_record_domain.resident_intake_state(record) != "open":
        if resident_record_domain.lifecycle_operations().pending(record["resident_id"]):
            return record  # The migration owns its temporary intake hold.
        resident_management_domain.transition_resident_state(record["resident_id"], record["resident_id"],
            transition="open", allow_quota_override=False)
        record = resident_record_domain.find_resident(record["resident_id"])
    return record


def reserve_candidate(record: dict[str, Any]) -> dict[str, Any] | None:
    """All Family executors share admission; only internal roles auto-open intake."""
    from . import resident_record_domain
    from .resident_work_recovery import _intake_allows_recovery
    if family_facade.is_operating_role(record):
        return _restore_role_intake(record)
    family = record.get("family") or {}
    if family.get("role") != "member" or record.get("deleted_at") or not _intake_allows_recovery(record):
        return None
    with get_lineage_data_store().database.read() as connection:
        facades = family_storage.roles_of(connection, family["family_id"])["facade"]
    facade = resident_record_domain.find_resident(facades[0]) if facades else None
    return record if facade and not facade.get("deleted_at") and _intake_allows_recovery(facade) else None


def ensure_role_reserve(record: dict[str, Any], accounts: list[dict[str, Any]] | None = None) -> bool:
    """Return whether the next turn must wait for a safe account migration."""
    from . import resident_record_domain, resident_restart_domain
    from .resident_work_recovery import _intake_allows_recovery

    record = reserve_candidate(record)
    if not record or not auto_migration_enabled(record):
        return False
    resident_id = record["resident_id"]
    if resident_record_domain.lifecycle_operations().pending(resident_id):
        return True
    job = resident_execution_work.read(record)
    if resident_execution_work.is_active(job) and (
        not resident_execution_work.can_preserve_for_session_restart(job) or job.get("stop_requested_at")
    ):
        return False  # Let a turn already admitted to Execution finish.
    if accounts is None:
        try:
            accounts = list(execution_port.service().accounts().get("accounts") or [])
        except Exception:
            logging.getLogger(__name__).exception("Family role account service unavailable for %s", resident_id)
            return True
    account = resident_account(record, accounts)
    migration = role_migration(record)
    if not account:
        reason = "尚未配置可用的 AI 账号；请在设置中连接 AI 并设为默认。原任务已保留，配置后将自动继续。"
        if record.get("backend_route_id"):
            reason = "当前绑定的 AI 账号不可用；请检查账号设置。原任务已保留，不会自动改用其他账号。"
        if migration.get("account_wait") != reason:
            with get_lineage_data_store().family_quota_migrations.transaction(record['lineage_id'], resident_id) as state:
                state["account_wait"] = reason
        return True
    if migration.get("account_wait"):
        with get_lineage_data_store().family_quota_migrations.transaction(record['lineage_id'], resident_id) as state:
            state.pop("account_wait", None)
    remaining = reserve_remaining(account)
    if (remaining is not None and remaining > MIGRATION_RESERVE_PERCENT
            or remaining is None and sufficient_reserve(account)):
        if role_migration(record):
            with get_lineage_data_store().family_quota_migrations.transaction(record['lineage_id'], resident_id) as healthy:
                asked = (healthy.get("question_facade_id"), healthy.get("question_message_id"))
                healthy.clear()
            family_role_quota_questions.close(*asked)
        return False
    if remaining is None:
        # Stale observations cannot clear an outstanding incident; a manual
        # quota release still lets the turn run.
        return (bool(role_migration(record).get("status"))
                and resident_record_domain.resident_quota_override(record).get("enabled") is not True)
    epoch = f"{account['account_id']}:{[(w.get('window'), w.get('resets_at')) for w in (account.get('quota_reserve') or {}).get('windows') or []]}"
    now = time.time()
    expired: tuple[Any, Any] = (None, None)
    with resident_record_domain.state_lock(resident_id), get_lineage_data_store().family_quota_migrations.transaction(record['lineage_id'], resident_id) as migration:
        current = resident_record_domain.find_resident(resident_id)
        if (not current or current["lineage_id"] != record["lineage_id"]
                or not _intake_allows_recovery(current) or not auto_migration_enabled(current)):
            return False
        current_job = resident_execution_work.read(current)
        if (resident_account(current, accounts).get("account_id") != account.get("account_id")
                or (resident_execution_work.is_active(current_job)
                    and not resident_execution_work.can_preserve_for_session_restart(current_job))):
            return False
        _validate_migration(migration)
        if migration.get("epoch") != epoch:
            expired = (migration.get("question_facade_id"), migration.get("question_message_id"))
            migration.clear()
            migration.update({"epoch": epoch, "attempts": 0, "tried_accounts": []})
        # A manual quota release (the role's own, or the facade's for Router
        # and Steward) lets the turn run on the current account's credits when
        # no safe account takes it; a safe account is still preferred.
        released = resident_record_domain.resident_quota_override(current).get("enabled") is True
        if not released and now - float(migration.get("checked_at") or 0) < RECHECK_SECONDS:
            return True
        migration["checked_at"] = now
        migration.update(status="waiting", reason=f"后端额度剩余 {remaining:g}%，正在寻找可用账号")
        payload = migration.get("pending_restart")
        if not payload and int(migration["attempts"]) < MAX_MIGRATIONS:
            target = fallback_account(accounts, str(account["provider"]), str(account.get("backend_route_id") or ""),
                                      migration["tried_accounts"], reserve=True, priority=auto_migration_priority(current),
                                      subscription_only=True)
            if target:
                migration["attempts"] += 1
                migration["tried_accounts"].append(target["account_id"])
                from .resident_work_recovery import latest_failed_input, recovery_entry
                failed_id = str(latest_failed_input(current).get("message_id") or "")
                resume_failed = not resident_execution_work.is_active(current_job) and not bool(recovery_entry(current, failed_id).get("disabled"))
                payload = {**migration_target_options(target, str(account["provider"]), account), "active_execution_policy": "wait", "resume_failed_input": resume_failed,
                           "idempotency_key": f"steward-reserve:{resident_id}:{epoch}:{migration['attempts']}"}
                migration["pending_restart"] = payload
                migration.update(status="migrating", reason="正在保留上下文并切换可用后端")
        if not payload and released:
            # Keep the window's attempts so the release does not retry
            # migrations; only the waiting state ends.
            migration.pop("status", None)
            migration.pop("reason", None)
    family_role_quota_questions.close(*expired)  # A new quota window asks again.
    if not payload and released:
        return False
    if payload:
        if resident_record_domain.lifecycle_operations().pending(resident_id):
            return True
        resident_restart_domain.restart_resident_session(resident_id, resident_id, payload, portal_reason="steward_weekly_reserve")
        with resident_record_domain.state_lock(resident_id), get_lineage_data_store().family_quota_migrations.transaction(record['lineage_id'], resident_id) as committed:
            if committed.get("pending_restart") == payload:
                committed["pending_restart"] = None
    elif not migration.get("owner_notified"):
        from . import lineage_human_notifications
        from .family_owners import recipients
        owners = recipients(record, creator_only=True)
        reason = f"当前账号额度剩余 {remaining:g}%，没有安全备用后端或迁移已失败两次；原任务保留等待。"
        with get_lineage_data_store().family_quota_migrations.transaction(record['lineage_id'], resident_id) as blocked:
            blocked.update(status="unavailable", reason=reason)
        if owners:
            lineage_human_notifications.create_notification(
                record["lineage_id"], title=f"Family 角色需要可用后端：{record.get('title') or resident_id}",
                message=reason,
                recipients=owners, severity="warning", created_by="portal",
                notification_id=f"steward-reserve:{resident_id}:{epoch}",
            )
            facade = _facade_of(record) if family_facade.is_operating_role(record) else None
            asked = ""
            if facade:
                # The people managing the facade decide in its Conversation:
                # credits, wait for the reset, or another account.
                asked = family_role_quota_questions.ask(record, facade, account, remaining, epoch)
            else:
                from .outward_messages import message_creator
                message_creator(record, identity=f"role-reserve:{resident_id}:{epoch}",
                                text=f"Family 内部角色 {record.get('title') or resident_id} 暂不可用。{reason}请补充可用账号后恢复。")
            with resident_record_domain.state_lock(resident_id), get_lineage_data_store().family_quota_migrations.transaction(record['lineage_id'], resident_id) as committed:
                if committed.get("epoch") == epoch:
                    committed["owner_notified"] = True
                    if asked:
                        committed.update(question_facade_id=facade["resident_id"], question_message_id=asked)
    return True


def answer_role_quota_question(facade_id: str, message_id: str, actor: str) -> None:
    """Carry out a delivered answer to a facade's role quota question, then say what happened.

    Continuing on credits is the facade's manual quota release, made by the
    person answering with their own right to manage the facade; its Router
    and Steward follow it (_follow_facade_quota_release).
    """
    from . import resident_access, resident_conversation_stream, resident_management_domain, resident_record_domain
    questions = family_role_quota_questions
    answer = resident_conversation_stream.get_repository().message(facade_id, message_id)
    if questions.question_of(facade_id, answer) is None:
        return
    choice = questions.choice_of(answer)
    if choice == questions.CONTINUE:
        facade = resident_record_domain.find_resident(facade_id) or {}
        if not resident_access.can_manage_resident(dict(facade), actor):
            text = "你没有管理这个门面的权限，未放行。请让有管理权限的人选择。"
        else:
            try:
                if resident_record_domain.resident_quota_override(facade).get("enabled") is not True:
                    resident_management_domain.transition_resident_state(facade_id, actor, transition="open")
            except (KeyError, PermissionError, ValueError) as exc:
                text = f"放行失败：{exc}"
            else:
                released = resident_record_domain.resident_quota_override(
                    resident_record_domain.find_resident(facade_id) or {}).get("enabled") is True
                text = ("已对门面人工放行。Router 和 steward 会跟随放行，用当前账号的 credits 继续工作；"
                        "之后在门面上关闭接单即可取消放行。" if released else
                        "已提交放行，但回读门面时还没有生效，请在 Family 状态中检查接单设置。")
    elif choice == questions.WAIT:
        text = "好的，保持等待。额度重置或补上可用账号后，Family 会自动继续，排队的工作不会丢。"
    elif choice == questions.ADD_ACCOUNT:
        text = "请在[首页](/)的 AI 账号中新增或启用一个剩余额度超过 10% 的账号；补好后 Family 会自动换过去继续。"
    else:
        text = f"未识别的选择：{choice or '（空）'}，没有做任何改动。"
    questions.post_result(facade_id, message_id, actor, text)


def check_role_availability(lineage_id: str, family_id: str, actor: str) -> dict[str, Any]:
    """ISA entry for the Family's explicit operating-role supervision Effect."""
    from .family_familize import read_family
    from . import resident_record_domain

    lineage, block = read_family(lineage_id, family_id, actor)
    family = block["family"]
    if actor not in {family["facade"], family["steward"]}:
        lineage_aggregate.ensure_manager(lineage, actor)
    facade = resident_record_domain.find_resident(family["facade"])
    if facade.get("deleted_at") or resident_record_domain.resident_intake_state(facade) != "open":
        return {"family_id": block["id"], "paused": True, "waiting_count": 0}
    roles = [resident_record_domain.find_resident(family[role]) for role in ("router", "steward")]
    restore_role_intakes(roles)
    # Intake maintenance must not poll accounts or migrate idle roles. Quota
    # admission belongs to the execution queue; report existing incidents only.
    waiting = sum(bool((state := role_migration(r)).get("status") or state.get("account_wait")) for r in roles)
    return {"family_id": block["id"], "paused": False, "waiting_count": waiting,
            "roles": [{"resident_id": r["resident_id"], **role_availability(resident_record_domain.find_resident(r["resident_id"]))}
                      for r in roles]}


def restore_role_intakes(residents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Restore role intake without querying accounts; lifecycle holds still win."""
    roles = []
    for record in residents:
        try:
            restored = _restore_role_intake(record)
            if restored:
                roles.append(restored)
        except Exception:
            logging.getLogger(__name__).exception("Family role intake recovery failed for %s", (record or {}).get("resident_id"))
    return roles


def reconcile_role_reserves(residents: list[dict[str, Any]] | None = None) -> int:
    """Retry quota admission for durable pending inputs, never for idle roles."""
    if residents is None:
        residents = resident_execution_work.pending_records()
    roles = []
    for snapshot in residents:
        record = reserve_candidate(snapshot)
        if not record or not auto_migration_enabled(record):
            continue
        job = resident_execution_work.read(record)
        if (resident_execution_work.is_active(job)
                and resident_execution_work.can_preserve_for_session_restart(job)
                and not job.get("stop_requested_at")):
            roles.append(record)
    if not roles:
        return 0
    try:
        accounts = list(execution_port.service().accounts().get("accounts") or [])
    except Exception:
        logging.getLogger(__name__).exception("Family role reserve scan deferred: account service unavailable")
        return 0
    waiting = 0
    for record in roles:
        try:
            waiting += int(ensure_role_reserve(record, accounts))
        except Exception:
            logging.getLogger(__name__).exception("Family role reserve check deferred for %s", record.get("resident_id"))
    return waiting
