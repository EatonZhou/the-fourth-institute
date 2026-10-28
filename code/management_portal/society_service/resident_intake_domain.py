"""Resident domain responsibilities: intake."""
from __future__ import annotations

from . import (
    lineage_message_events,
    family_facade,
    invariant_locks,
    message_quotes,
    resident_backend,
    resident_conversation_stream,
    resident_execution_provider,
    resident_held_infos,
    resident_input_scope,
    resident_intake_admission,
    resident_execution_work,
    resident_lineage,
    resident_message_contract,
    resident_state,
    resident_record_domain,
    resident_prompt_domain,
    resident_input_queue_domain,
    resident_lifecycle_domain,
    resident_media_domain,
    resident_runtime_domain,
    resident_session_backend_domain,
)
from .domain_ports import execution_port
from .resident_access import (
    resident_deliverable_to_actor as _resident_deliverable_to_actor,
    resident_visible_to_actor as _resident_visible_to_actor,
)
from management_common.actor_identity import canonical_actor_id
from management_common.skill_paths import normalize_additional_skills
from management_portal.execution.provider_catalog import AGENT_PROVIDERS
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
import copy
import hashlib
from management_portal.execution import slash_commands
from management_portal import agent_context_selection
import secrets

from . import resident_domain_common


@dataclass(frozen=True)
class ActivationPolicy:
    """What an input's ``activation_policy`` asks of intake, decided once per input.

    An input that starts a turn is work of its own; only such an input may be
    a slash command or answer questions.  One that joins the active turn
    steers the turn running now.  An info waits to be shown with the next
    request and never starts a turn.
    """

    name: str
    starts_turn: bool = False
    joins_active_turn: bool = False
    waits_as_info: bool = False


ACTIVATION_POLICIES = {
    "enqueue_and_trigger": ActivationPolicy("enqueue_and_trigger", starts_turn=True),
    "inject_active_turn": ActivationPolicy("inject_active_turn", joins_active_turn=True),
    "attach_to_next": ActivationPolicy("attach_to_next", waits_as_info=True),
}


@dataclass(frozen=True)
class InputRelay:
    """The facade message a Family Router delivery relays; ``source`` is None for any other input."""

    source: tuple[str, str] | None = None
    delegated: bool = False
    sender: str = ""


@dataclass(frozen=True)
class ResidentInput:
    """One input as intake admits it: what was sent, by whom, and how it becomes work."""

    resident_id: str
    actor_id: str
    message: str
    message_id: str
    activation: ActivationPolicy
    isolated: bool
    context_refs: object
    working_directory: str
    runtime_context: str
    additional_skills: list[str]
    # Whether runtime context or skills came with the input, before normalization.
    sent_options: bool
    lineage_id: str
    input_media: object
    protocol_input: Mapping[str, Any] | None
    business_ref: str
    relay: InputRelay
    speaking_member: str
    reply_source_message_id: str = ""
    quote: Mapping[str, Any] | None = None

    @property
    def runs_commands(self) -> bool:
        """Slash commands are answered for an input that starts a turn, unless it is an authorized invocation's."""
        return self.activation.starts_turn and not self.isolated

    @property
    def only_content(self) -> bool:
        """Whether the input carries nothing but its message, as an info must."""
        return not (self.context_refs or self.input_media or self.sent_options)

    @property
    def reader(self) -> str:
        """Whose access reads what the input refers to at a facade.

        A Family's message out goes as its facade, but its attachments and
        references are the member's: they are read with the member's own access.
        """
        return self.speaking_member or self.actor_id

    @property
    def attachment_actor(self) -> str:
        """Whose access reads the attachments and references of an input that is queued."""
        return self.relay.sender or self.speaking_member or self.actor_id

    @property
    def requested_quote(self) -> object:
        return self.protocol_input.get("quote") if isinstance(self.protocol_input, Mapping) else None

    def user_message(self, resident_id: str) -> dict[str, Any]:
        return resident_input_queue_domain.resident_user_message(
            resident_id, self.message, self.actor_id,
            message_id=self.message_id, protocol_input=self.protocol_input,
        )

    def with_business_ref(self, item: dict[str, Any]) -> dict[str, Any]:
        if self.business_ref:
            item["business_ref"] = str(self.business_ref).strip()[:500]
        return item


@dataclass(frozen=True)
class _PreparedInput:
    """What an input to be queued resolves outside the state lock."""

    # A message, unlike a slash command, is accepted together with the Job
    # that carries it (resident_execution_work.admit); its receipt reads that.
    deferred_delivery: bool
    environment: dict[str, Any] | None
    cwd: Path | None
    context_refs: dict[str, Any]
    input_media: list[dict[str, Any]]
    provider_input: str
    goal_command: dict[str, Any]
    last_user_message: str


def existing_resident_delivery(
    resident_id: str,
    message: Mapping[str, Any],
    *,
    bound_job: Mapping[str, Any] | None = None,
    require_execution_binding: bool = False,
) -> bool:
    """Validate an idempotent retry and reject incomplete intake commits."""

    repository = resident_conversation_stream.get_repository()
    message_id = str(message.get("message_id") or "").strip()
    existing = repository.message(resident_id, message_id)
    if existing is not None:
        candidate = dict(message)
        candidate["created_at"] = str(existing.get("created_at") or "")
        repository.validate_message_identity(resident_id, candidate)
    else:
        try:
            previous_input_state = repository.input_state(resident_id, message_id)
        except KeyError:
            previous_input_state = {}
        if str(previous_input_state.get("state") or "") in {
            "completed", "failed", "cancelled",
        }:
            return True
        job = dict(bound_job or {})
        queued = resident_execution_work.input_location(resident_id, message_id, job)
        if queued is None:
            return False
        stored = queued.get("input_message")
        if not isinstance(stored, Mapping):
            return False
        for field in ("message_id", "role", "author_id", "content_parts"):
            if stored.get(field) != message.get(field):
                raise resident_message_contract.MessageIdentityConflict("Resident input changed during idempotent retry")
        queued_message = copy.deepcopy(dict(stored))

        def repair_queued_input() -> None:
            resident_lifecycle_domain.accept_resident_user_message(resident_id, queued_message)
            record = resident_record_domain.find_resident(resident_id)
            if record:
                resident_execution_work.project_latest_conversation_state(record, job)

        invariant_locks.defer_until_state_unlock(repair_queued_input)
        return True
    if not require_execution_binding:
        return True
    try:
        input_state = repository.input_state(resident_id, message_id)
    except KeyError:
        input_state = {}
    if str(input_state.get("state") or "") in {"completed", "failed", "cancelled"}:
        return True
    job = dict(bound_job or {})
    return resident_execution_work.input_location(resident_id, message_id, job) is not None


def _persist_synchronous_command_exchange(
    resident_id: str,
    actor: str,
    content: str,
    message_id: str,
    result: dict[str, Any],
    *,
    user_message: Mapping[str, Any] | None = None,
) -> None:
    persisted_user_message = resident_lifecycle_domain.persist_resident_user_message(
        resident_id,
        dict(user_message) if isinstance(user_message, Mapping) else resident_input_queue_domain.build_message(
            "user", content, actor, message_id=message_id
        ),
    )
    input_message_id = str(persisted_user_message.get("message_id") or message_id)
    resident_lifecycle_domain.append_conversation_message(
        resident_id,
        resident_input_queue_domain.portal_command_message(
            result,
            actor,
            reply_to_message_id=input_message_id,
        ),
    )
    resident_conversation_stream.get_repository().set_input_state(
        resident_id,
        input_message_id,
        "completed",
    )


def _queue_resident_compaction(
    record: dict[str, Any],
    actor_id: str,
    message_id: str,
    *,
    user_message: Mapping[str, Any] | None = None,
) -> None:
    now = resident_domain_common.now_iso()
    resident_id = resident_domain_common.record_resident_id(record)
    session_id = resident_backend.record_session_id(record)
    running = resident_input_queue_domain.active_resident_execution(record)
    user_item = (
        dict(user_message)
        if isinstance(user_message, Mapping)
        else resident_input_queue_domain.build_message("user", "/compact", actor_id, message_id=message_id)
    )
    input_message_id = str(user_item.get("message_id") or message_id)
    item = resident_execution_work.new_compact_item(
        actor_id,
        session_id,
        "Resident Session",
        now,
        input_message_id,
    )
    item["input_message"] = copy.deepcopy(user_item)
    if running:
        queue = resident_execution_work.normalized_queue(running)
        item.pop("execution_id", None)
        queue.append(item)
        running["queue"] = queue
        running["updated_at"] = now
        resident_execution_work.write(record, running)
        resident_lifecycle_domain.mark_resident_input_queued(resident_id, user_item)
        record["updated_at"] = now
        resident_record_domain.save_resident_record(record)
        return
    job = {
        **item,
        "started_at": now,
        "updated_at": now,
        "error": "",
        "queue": [],
    }
    resident_execution_work.write(record, job)
    record["session_state"] = resident_record_domain.record_backend_session_state(record)
    record["updated_at"] = now
    resident_record_domain.save_resident_record(record)


def _context_refs_include_image(value: object) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            name = str(key).lower()
            text = str(item).lower() if isinstance(item, str) else ""
            if name in {"mime", "mime_type", "content_type"} and text.startswith("image/"):
                return True
            if name in {"path", "file_path", "name", "filename", "url"} and any(
                text.split("?", 1)[0].endswith(suffix) for suffix in resident_domain_common.RESIDENT_IMAGE_SUFFIXES
            ):
                return True
            if _context_refs_include_image(item):
                return True
    elif isinstance(value, list):
        return any(_context_refs_include_image(item) for item in value)
    return False


def _ensure_backend_accepts_context_refs(record: dict[str, Any], context_refs: object) -> None:
    if not resident_execution_provider.provider_for(record).capabilities.supports_media and _context_refs_include_image(context_refs):
        raise ValueError("Claude Code + DeepSeek 当前不支持图片输入；请改用文字或文本文件")


def _send_synchronous_provider_message(
    record: dict[str, Any],
    actor_id: str,
    message: str,
    *,
    message_id: str,
    user_message: Mapping[str, Any],
) -> dict:
    """Record a deterministic provider invocation as one Agent Execution."""
    provider = resident_execution_provider.provider_for(record)
    resident_id = resident_domain_common.record_resident_id(record)

    # Keep the Resident registry lock out of the Lineage mutation boundary.
    # Resident registration takes Lineage -> Resident locks, so the reverse
    # order here could deadlock with a concurrent node creation.

    from uuid import uuid4
    from management_portal.execution.agent_data_store import get_agent_data_store
    from management_portal.execution import execution_inputs
    execution_id = "execution-" + uuid4().hex[:24]
    store = get_agent_data_store()
    started_at = resident_domain_common.now_iso()
    with store.database.transaction():
        store.begin_execution(execution_id=execution_id, agent_id=resident_id,
                              actor_ref=actor_id, provider="portal", started_at=started_at)
        execution_inputs.attach(store, execution_id, [{
            "message_id": message_id, "role": "primary", "accepted_at": started_at,
        }])
    try:
        outcome = provider.deliver(
            resident_execution_provider.DeliveryRequest(
                lineage_id=str(record.get("lineage_id") or ""),
                resident_id=str(record.get("resident_id") or ""),
                message=message,
                actor_id=actor_id,
                resident=record,
                delivery=user_message,
            )
        )
        reply = outcome.reply.strip()
        if not reply:
            raise RuntimeError("Synchronous ExecutionProvider did not produce a reply")

        with resident_record_domain.state_lock(resident_id):
            current = resident_record_domain.find_resident(str(record.get("resident_id") or ""))
            if not current:
                raise KeyError("resident not found")
            if not _resident_deliverable_to_actor(current, actor_id):
                raise KeyError("resident not found")
            if not resident_execution_provider.provider_for(
                current
            ).capabilities.synchronous_delivery:
                raise ValueError("Resident 已不再使用同步 ExecutionProvider")

            user_item = dict(user_message)
            resident_lifecycle_domain.append_conversation_message(
                resident_domain_common.record_resident_id(current),
                {**resident_input_queue_domain.build_message(
                    "agent",
                    reply,
                    message_id=resident_message_contract.derived_message_identity(
                        "portal-resident-reply",
                        str(user_item.get("message_id") or ""),
                        "final",
                    ),
                    reply_to_message_id=str(user_item.get("message_id") or ""),
                ), "execution_id": execution_id},
            )
            store.finish_execution(execution_id, status="completed", finished_at=resident_domain_common.now_iso())
            resident_conversation_stream.get_repository().set_input_state(
                resident_domain_common.record_resident_id(current),
                str(user_item.get("message_id") or ""),
                "completed",
            )
            current["session_state"] = "active"
            current["last_error"] = ""
            current["updated_at"] = resident_domain_common.now_iso()
            resident_record_domain.save_resident_record(current)

    except Exception:
        if (store.execution(execution_id) or {}).get("status") not in {"completed", "failed", "cancelled"}:
            store.finish_execution(execution_id, status="failed", finished_at=resident_domain_common.now_iso())
        raise

    return _resident_message_delivery_result(
        str(record.get("resident_id") or ""),
        message_id,
    )


def _resident_message_cwd(
    record: dict[str, Any],
    working_directory: object,
) -> Path:
    raw = str(working_directory or "").strip()
    default = resident_runtime_domain.resident_working_directory(record)
    if not raw:
        return default
    path = Path(raw).expanduser()
    if not path.is_absolute() or not path.is_dir():
        raise ValueError("Resident 工作目录必须是已存在的绝对目录")
    resolved = path.resolve(strict=False)
    allowed_roots = {
        Path(str(item.get("path") or "")).expanduser().resolve(strict=False)
        for item in resident_record_domain.resident_workspace_roots(record)
        if str(item.get("path") or "").strip()
    }
    if resolved not in allowed_roots:
        raise PermissionError("Resident 工作目录必须是已绑定的 Workspace")
    return resolved


def _resident_message_delivery_result(
    resident_id: str = "",
    message_id: str = "",
) -> dict[str, Any]:
    repository = resident_conversation_stream.get_repository()
    clean_message_id = str(message_id or "").strip()
    if not clean_message_id:
        raise ValueError("Resident delivery acknowledgement requires message_id")
    projection = repository.current_projection(resident_id)
    stored_message = repository.message(resident_id, clean_message_id)
    result = {
        "resident_id": str(resident_id or "").strip(),
        "message_id": clean_message_id,
        "state": dict(projection.get("state") or {}),
        "queue": copy.deepcopy(dict(projection.get("queue") or {})),
        "cursor": int(projection.get("cursor") or 0),
    }
    if stored_message is not None:
        # Admission may move the input between Mailbox and Chat while the receipt is built.
        # The first read already decoded the public message; do not race a second lookup.
        result["message"] = stored_message
    try:
        result["input_state"] = repository.input_state(
            resident_id,
            clean_message_id,
        )
    except KeyError:
        pass
    return result


def _save_admission_observations(record: Mapping[str, Any]) -> None:
    """Admission changes only these observations, never identity or Workspace."""
    from .resident_catalog import get_catalog
    def write(connection):
        connection.execute("UPDATE residents SET updated_at=%s WHERE resident_id=%s",
                           (record["updated_at"], record["resident_id"]))
        connection.execute("UPDATE resident_sessions SET session_state=%s WHERE resident_id=%s",
                           (resident_record_domain.record_backend_session_state(record), record["resident_id"]))
        if "last_error" in record:
            connection.execute("UPDATE resident_observation_cache SET last_error=%s WHERE resident_id=%s",
                               (record["last_error"], record["resident_id"]))
    get_catalog().state_store.database.write(write)


def _execution_account_routes(
    accounts: Iterable[Mapping[str, Any]],
) -> dict[tuple[str, str], str]:
    routes: dict[tuple[str, str], str] = {}
    for account in accounts:
        account_id = str(account.get("account_id") or "").strip()
        provider = str(account.get("provider") or "").strip()
        route_id = str(account.get("backend_route_id") or "").strip()
        if not account_id or not provider:
            raise ValueError("Execution account catalog entry is incomplete")
        key = (provider, route_id)
        previous = routes.get(key)
        if previous and previous != account_id:
            raise ValueError("Execution account routes must be unique per provider")
        routes[key] = account_id
        routes.setdefault((account_id, ""), account_id)
    return routes


def _resident_quota_account_resolver(
    accounts: Iterable[Mapping[str, Any]],
) -> Callable[[Mapping[str, Any], Mapping[str, Any]], str]:
    """Resolve account ownership once per distinct unbound Resident route."""

    account_routes = _execution_account_routes(accounts)
    resolved_defaults: dict[tuple[str, str], str] = {}

    def resolve(
        record: Mapping[str, Any],
        binding: Mapping[str, Any],
    ) -> str:
        provider = resident_backend.execution_provider(record)
        # A fresh release deliberately has no source Model API route. Such a
        # Resident is unassigned, not an error in every account's read-only view.
        if not provider:
            return ""
        route_id = resident_backend.execution_account_route(record, binding)
        if route_id or (provider, route_id) in account_routes:
            return str(account_routes.get((provider, route_id)) or "")

        configuration = record.get("backend_binding") or {}
        provider_profile = str(
            record.get("provider_profile")
            or configuration.get("provider_profile")
            or ""
        ).strip()
        selector = (provider, provider_profile)
        if selector not in resolved_defaults:
            account = execution_port.service().resolve_account(
                provider,
                runtime_options={
                    "provider_profile": provider_profile,
                },
            )
            account_id = str(account.get("account_id") or "").strip()
            resolved_route = str(account.get("backend_route_id") or "").strip()
            if (
                account_id
                and account_routes.get((provider, resolved_route)) != account_id
            ):
                raise ValueError(
                    "Resolved Execution account is absent from the account catalog"
                )
            resolved_defaults[selector] = account_id
        return resolved_defaults[selector]

    return resolve


def _require_execution_account(
    account_id: str,
    accounts: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    for account in accounts:
        if account["account_id"] == account_id:
            return dict(account)
    raise ValueError("Unknown Execution account")


@resident_lineage.access_snapshot_scope()
def get_account_resident_summary(account_id: str, actor: str) -> dict[str, Any]:
    operator = canonical_actor_id(actor)
    if not operator:
        raise PermissionError("需要登录后查看账号节点详情")
    account_id = str(account_id or "").strip()
    if not account_id:
        raise ValueError("account_id required")
    accounts = execution_port.service().accounts().get("accounts", [])
    _require_execution_account(account_id, accounts)
    resolve_account = _resident_quota_account_resolver(accounts)
    # Only the Residents the caller can read (GetResident) are counted, whoever the caller is.
    residents = [
        record for record in resident_lineage.all()
        if isinstance(record, dict) and _resident_visible_to_actor(record, operator)
    ]
    bindings = resident_backend.session_routes_for_bindings([
        resident_backend.execution_binding_id(record) for record in residents
    ])
    selected = [
        record for record in residents
        if resolve_account(
            record,
            bindings.get(resident_backend.execution_binding_id(record)) or {},
        ) == account_id
    ]
    snapshots = resident_conversation_stream.get_repository().state_snapshots([
        resident_domain_common.record_resident_id(record) for record in selected
    ])
    intake = {"open": 0, "closed": 0, "unknown": 0}
    # The page opens or closes these with OpenIntake resident_ids.
    intake_ids: dict[str, list[str]] = {"open": [], "closed": []}
    work = {"idle": 0, "queued": 0, "running": 0, "stopping": 0, "unknown": 0}
    latest_input = {"failed": 0, "cancelled": 0}
    queued_messages = no_session = exempt = 0
    for record in selected:
        snapshot = snapshots.get(resident_domain_common.record_resident_id(record)) or {}
        state = snapshot.get("state") or {}
        intake_state = state.get("intake_state", "unknown")
        if intake_state == "closed":
            intake["closed"] += 1
        else:
            intake[intake_state if intake_state == "open" else "unknown"] += 1
        if intake_state in intake_ids:
            intake_ids[intake_state].append(resident_domain_common.record_resident_id(record))
        work_state = state.get("work_state", "unknown")
        work[work_state if work_state in work else "unknown"] += 1
        queued_messages += max(0, int(state.get("queued_count") or 0))
        last_state = (snapshot.get("latest_input_state") or {}).get("state")
        if last_state in latest_input:
            latest_input[last_state] += 1
        binding = bindings.get(resident_backend.execution_binding_id(record)) or {}
        no_session += int(not binding.get("backend_session_id"))
        exempt += int(resident_record_domain.resident_quota_override(record).get("enabled") is True)
    quota = next(
        (
            item.get("quota_reserve")
            for item in accounts
            if item["account_id"] == account_id
        ),
        None,
    ) or {"status": "unknown"}
    return {
        "account_id": account_id, "observed_at": resident_domain_common.now_iso(),
        "scope": "visible", "total": len(selected),
        "intake": intake, "open_resident_ids": intake_ids["open"], "closed_resident_ids": intake_ids["closed"],
        "work": work, "latest_input": latest_input,
        "queued_messages": queued_messages, "without_session": no_session,
        "quota_exempt": exempt, "quota": quota,
    }


@resident_lineage.access_snapshot_scope()
def persist_resident_input(
    resident_id: str,
    actor: str,
    content: str,
    context_refs: object = None,
    working_directory: str = "",
    runtime_context: str = "",
    additional_skills: object = (),
    activation_policy: str = "enqueue_and_trigger",
    lineage_id: str = "",
    input_media: object = None,
    message_id: str = "",
    protocol_input: Mapping[str, Any] | None = None,
    business_ref: str = "",
    reply_source_message_id: str = "",
) -> dict:
    """Admit one input to the Resident and make it the work its activation policy asks for."""
    from .outward_messages import speaking_for
    actor_id = canonical_actor_id(actor)
    if not actor_id:
        raise ValueError("需要登录后发送")
    message = str(content or "")
    if not message.strip():
        raise ValueError("content required")
    message_id = resident_message_contract.message_identity(message_id)
    activation = ACTIVATION_POLICIES[resident_message_contract.activation_policy(activation_policy)]
    isolated = resident_input_scope.permits(resident_id, actor_id, message_id)
    if isolated:
        _check_isolated_input(resident_id, message, message_id, activation, carries_more=bool(
            context_refs or input_media or working_directory or runtime_context or additional_skills
        ))
    # Keyword arguments are evaluated in order: normalization precedes the relay checks.
    request = ResidentInput(
        resident_id=resident_id, actor_id=actor_id, message=message, message_id=message_id,
        activation=activation, isolated=isolated, context_refs=context_refs, working_directory=working_directory,
        runtime_context=resident_prompt_domain.normalized_runtime_context(runtime_context),
        additional_skills=normalize_additional_skills(additional_skills),
        sent_options=bool(runtime_context or additional_skills),
        lineage_id=lineage_id, input_media=input_media, protocol_input=protocol_input, business_ref=business_ref,
        relay=_input_relay(business_ref, actor_id, resident_id, protocol_input),
        speaking_member=speaking_for(actor_id),
        reply_source_message_id=reply_source_message_id,
    )
    # A request may quote one message of this Conversation; an info carries
    # only what it says.  The quote is checked and filled in once, here.
    if request.requested_quote not in (None, {}) and (activation.waits_as_info or isolated):
        raise ValueError("info 消息和授权调用的输入不带引用")
    quote_record = resident_record_domain.find_resident_for_admission(resident_id) or {"resident_id": resident_id}
    return _admit_input(_with_accepted_quote(request, quote_record), quote_record)


def _check_isolated_input(
    resident_id: str, message: str, message_id: str, activation: ActivationPolicy, *, carries_more: bool,
) -> None:
    """An authorized invocation's input is exactly the message the invocation authorized."""

    if activation.joins_active_turn:
        raise ValueError("Isolated input cannot join another active turn")
    profile = resident_input_scope.get(resident_id, message_id)["profile"]
    if hashlib.sha256(message.encode()).hexdigest() != profile["input_sha256"]:
        raise ValueError("Resident input differs from its authorized invocation")
    if carries_more:
        raise ValueError("Isolated input context must be contained in the authorized message")


def _input_relay(
    business_ref: str, actor_id: str, resident_id: str, protocol_input: Mapping[str, Any] | None,
) -> InputRelay:
    # A Family Router delivery carries the facade message it relays; the
    # facade message authorizes delivery to members of that Family.
    source = family_facade.validate_relay(business_ref, actor_id) if business_ref else None
    delegated = family_facade.relay_delegates(source, resident_id)
    # A relay that carries its facade message unchanged reads that message's
    # attachments and references with its sender's access, as the facade
    # checked them on arrival; anything else is read with the relayer's own.
    sender = family_facade.relay_sender(source, protocol_input) if delegated else ""
    return InputRelay(source=source, delegated=delegated, sender=sender)


def _with_accepted_quote(request: ResidentInput, quote_record: Mapping[str, Any]) -> ResidentInput:
    requested_quote = request.requested_quote
    protocol_input = request.protocol_input
    if requested_quote in (None, {}) and request.relay.source is None and family_facade.is_facade(quote_record):
        from .resident_questions import answered_set

        # An answer to a member's questions shown on a facade quotes them, so
        # it goes straight back to that member (family_facade.quoted_member).
        asked_in = answered_set(protocol_input)
        if asked_in and message_quotes.quote_of(quote_record, asked_in):
            requested_quote = {"message_id": asked_in}
    quote = message_quotes.accept(
        quote_record, requested_quote, reader=request.reader, message_id=request.message_id,
        relay_source=request.relay.source if request.relay.delegated else None,
        reply_source_message_id=request.reply_source_message_id,
    )
    if isinstance(protocol_input, Mapping) and (quote or "quote" in protocol_input):
        protocol_input = {**{key: value for key, value in protocol_input.items() if key != "quote"},
                          **({"quote": quote} if quote else {})}
    return replace(request, protocol_input=protocol_input, quote=quote)


def _admit_input(request: ResidentInput, quote_record: Mapping[str, Any]) -> dict[str, Any]:
    """Admit the input under the Resident's state lock; then deliver it at once or queue it."""

    has_media = bool(request.input_media or resident_media_domain.RESIDENT_IMAGE_MARKER_RE.search(request.message))
    environment = _execution_environment(request, quote_record, has_media)
    resident_id, message_id = request.resident_id, request.message_id
    with resident_intake_admission.scope(resident_id, message_id), resident_record_domain.state_lock(resident_id):
        record = _deliverable_record(request)
        if record.get("deleted_at"):
            raise ValueError("该 Resident 已删除，不能接收消息")
        request = _with_question_answer(request, record)
        if family_facade.is_facade(record):
            return _deliver_to_facade(request, record, environment)
        if request.activation.waits_as_info:
            return _hold_info(request, record)
        capabilities = resident_execution_provider.provider_for(record).capabilities
        if request.activation.joins_active_turn and _already_in_active_turn(request, record, capabilities):
            return _receipt(request)
        _require_supported_input(request, capabilities)
        if capabilities.synchronous_delivery:
            started = _start_synchronous_delivery(request, environment or record)
            if started is None:
                return _receipt(request)
        else:
            started, input_record = None, copy.deepcopy(record)
    if started is not None:
        return _deliver_synchronously(request, *started)
    return _queue_input(request, input_record, environment, has_media)


def _execution_environment(
    request: ResidentInput, quote_record: Mapping[str, Any], has_media: bool,
) -> dict[str, Any] | None:
    # File attachments/explicit cwd require the execution environment. Plain
    # messages need only persisted identity/configuration; resolve paths later.
    if (has_media or request.working_directory or request.message.lstrip().startswith("/")
            or resident_execution_provider.provider_for(quote_record).capabilities.synchronous_delivery):
        return resident_record_domain.find_resident(request.resident_id)
    return None


def _deliverable_record(request: ResidentInput) -> dict[str, Any]:
    """The Resident as admission reads it; the actor must be allowed to deliver to it."""

    record = resident_record_domain.find_resident_for_admission(request.resident_id)
    if not record:
        raise KeyError("resident not found")
    if not request.isolated and not request.relay.delegated and not _resident_deliverable_to_actor(record, request.actor_id):
        raise KeyError("resident not found")
    return record


def _save_admitted_record(record: dict[str, Any]) -> None:
    if record.get("default_cwd"):
        resident_record_domain.save_resident_record(record)
    else:
        _save_admission_observations(record)


def _with_question_answer(request: ResidentInput, record: dict[str, Any]) -> ResidentInput:
    """An answer to the Resident's questions becomes the message its question set asks for."""

    from .resident_questions import prepare_question_answer

    question_answer = prepare_question_answer(
        resident_domain_common.record_resident_id(record), request.protocol_input,
        resident_conversation_stream.get_repository(),
        facade=record if family_facade.is_facade(record) else None,
        relay_source=request.relay.source if request.relay.sender else None,
    )
    if question_answer is None:
        return request
    if not request.activation.starts_turn:
        raise ValueError("问题回答需要 enqueue_and_trigger")
    message, protocol_input = question_answer
    if request.quote:
        protocol_input = {**protocol_input, "quote": request.quote}
    return replace(request, message=message, protocol_input=protocol_input, context_refs=None)


def _receipt(request: ResidentInput, *, deferred_delivery: bool = False) -> dict[str, Any]:
    resident_intake_admission.committed(request.resident_id, request.message_id)
    # A Job write projects its durable input only after every outer state
    # scope releases. Identity is available immediately, including to
    # adapters that copy this receipt; only projections wait for unlock.
    if invariant_locks.lock_held():
        deferred_result: dict[str, Any] = {
            "resident_id": request.resident_id,
            "message_id": request.message_id,
        }
        if not request.activation.starts_turn:
            deferred_result["activation_policy"] = request.activation.name
        invariant_locks.defer_until_state_unlock(
            lambda: deferred_result.update(_resolved_receipt(request, deferred_delivery))
        )
        return deferred_result
    return _resolved_receipt(request, deferred_delivery)


def _resolved_receipt(request: ResidentInput, deferred_delivery: bool) -> dict[str, Any]:
    resident_id, message_id = request.resident_id, request.message_id
    if request.activation.joins_active_turn:
        # Recheck under the same queue lock used by promotion. A turn may
        # finish while context is prepared; the accepted input stays queued.
        with resident_record_domain.state_lock(resident_id):
            current_record = resident_record_domain.find_resident_for_admission(resident_id)
            current = resident_execution_work.read(current_record) if current_record else {}
            inject = bool(resident_execution_work.is_active(current) and any(
                resident_input_queue_domain.queue_item_matches(item, message_id)
                for item in resident_execution_work.normalized_queue(current)
            ))
        if inject:
            return {
                **resident_input_queue_domain.inject_queued_input_into_active_turn(
                    resident_id, request.actor_id, message_id, delivered=True,
                ),
                "activation_policy": request.activation.name,
            }
    result = (resident_input_queue_domain.accepted_delivery(resident_id, message_id) if deferred_delivery else
              _resident_message_delivery_result(resident_id=resident_id, message_id=message_id))
    if request.relay.source is not None:
        # The member now holds the relayed message; close the facade copy.
        family_facade.complete_relay(request.relay.source, resident_id, message_id)
    if not request.activation.starts_turn:
        result = dict(result)
        result["activation_policy"] = request.activation.name
        if deferred_delivery or (result.get("input_state") or {}).get("state") in {"queued", "running"}:
            result["injected"] = False
            result["delivery_status"] = "next_turn"
    return result


def _deliver_to_facade(
    request: ResidentInput, record: dict[str, Any], environment: dict[str, Any] | None,
) -> dict[str, Any]:
    """A Family facade never runs a turn: its requests wait for the
    Family Router, and an info goes to the Family's steward at once."""

    if request.activation.joins_active_turn or request.isolated:
        raise ValueError("Family 门面只接收普通消息和 info 消息")
    if request.activation.waits_as_info and not request.only_content:
        raise ValueError("info 消息只包含消息内容")
    record_id = resident_domain_common.record_resident_id(record)
    # A request may carry whatever a message to any Resident may; the
    # sender's right to each attachment and reference is checked here,
    # and the member the Router chooses reads them (decision_44cf587c1721).
    if request.input_media:
        resident_media_domain.resident_input_media(
            request.input_media, actor=request.reader, resident_id=record_id, content=request.message,
            workspace_roots=resident_runtime_domain.resident_media_workspace_roots(environment or record),
        )
    if request.context_refs:
        resolve_context_refs(request.context_refs, request.reader, request.message)
    facade_item = request.with_business_ref(request.user_message(record_id))
    facade_message = copy.deepcopy(facade_item)
    facade_message_id = str(facade_message.get("message_id") or "")
    if request.activation.waits_as_info:
        if not existing_resident_delivery(record_id, facade_item):
            # Held only to be shown with the facade's next request.
            resident_held_infos.hold(record, facade_item)

            def accept_info() -> None:
                # Handed to the steward on arrival; the facade's trace gets it
                # with the facade's next request (resident_held_infos.release_with).
                resident_lifecycle_domain.accept_resident_user_message(request.resident_id, facade_message)
                resident_conversation_stream.get_repository().set_input_state(
                    request.resident_id, facade_message_id, "completed",
                )

            invariant_locks.defer_until_state_unlock(accept_info)
        # A retried delivery forwards again; the steward's copy has a fixed ID.
        invariant_locks.defer_until_state_unlock(
            lambda: family_facade.forward_info(record, facade_message, request.actor_id)
        )
        return _receipt(request)
    from . import family_recovery

    if existing_resident_delivery(record_id, facade_item):
        invariant_locks.defer_until_state_unlock(lambda: family_recovery.handle(record, facade_message_id))
        return _receipt(request)

    def accept_for_router() -> None:
        resident_lifecycle_domain.accept_resident_user_message(request.resident_id, facade_message)
        family_recovery.register(record, facade_message)
        resident_conversation_stream.get_repository().set_input_state(
            request.resident_id, facade_message_id, "queued",
        )
        # Infos that came before this request are shown with it.
        resident_held_infos.release_with(record, facade_message_id)
        # Its sender sees it at once; the Router still chooses a member.
        family_facade.show_request(request.resident_id, facade_message_id)
        family_recovery.handle(record, facade_message_id)

    invariant_locks.defer_until_state_unlock(accept_for_router)
    invariant_locks.defer_until_state_unlock(
        lambda: lineage_message_events.wake_watchers(record, facade_message_id)
    )
    return _receipt(request)


def _hold_info(request: ResidentInput, record: dict[str, Any]) -> dict[str, Any]:
    """An info message only waits for the next request; it never starts a turn."""

    if request.isolated:
        raise ValueError("授权调用的输入不能作为 info 消息")
    if not request.only_content:
        raise ValueError("info 消息只包含消息内容")
    record_id = resident_domain_common.record_resident_id(record)
    info_item = request.with_business_ref(request.user_message(record_id))
    if existing_resident_delivery(record_id, info_item):
        return _receipt(request)
    resident_held_infos.hold(record, info_item)
    held_message = copy.deepcopy(info_item)
    held_message_id = str(held_message.get("message_id") or "")

    def accept_info() -> None:
        resident_lifecycle_domain.accept_resident_user_message(request.resident_id, held_message)
        resident_conversation_stream.get_repository().set_input_state(
            request.resident_id, held_message_id, "queued",
        )

    invariant_locks.defer_until_state_unlock(accept_info)
    invariant_locks.defer_until_state_unlock(
        lambda: lineage_message_events.wake_watchers(record, held_message_id)
    )
    return _receipt(request)


def _already_in_active_turn(request: ResidentInput, record: dict[str, Any], capabilities: Any) -> bool:
    """Check that the input can join the running turn; True when a retry finds it delivered already."""

    if not capabilities.supports_active_turn_input:
        raise ValueError("该 Resident 不支持注入当前工作，请使用正常发送")
    current = resident_execution_work.read(record)
    user_item = request.with_business_ref(request.user_message(request.resident_id))
    if existing_resident_delivery(
        request.resident_id, user_item, bound_job=current, require_execution_binding=True,
    ):
        return True
    if (
        not resident_execution_work.is_active(current)
        or resident_execution_work.is_execution_held(current)
        or not resident_execution_work.submitted_execution_id(current)
        or current.get("stop_requested_at")
        or current.get("execution_terminal_status") in {"completed", "failed", "cancelled"}
        or resident_execution_work.is_compact(current)
    ):
        raise ValueError("当前 Resident 没有可注入的活动工作，请使用正常发送")
    return False


def _require_supported_input(request: ResidentInput, capabilities: Any) -> None:
    if request.context_refs and not capabilities.supports_context:
        raise ValueError("该 Resident ExecutionProvider 不接受 Context 引用")
    if request.runtime_context and not capabilities.supports_context:
        raise ValueError("该 Resident ExecutionProvider 不接受 runtime context")
    if request.additional_skills and not capabilities.supports_skills:
        raise ValueError("该 Resident ExecutionProvider 不加载执行层 Skill")
    input_media = request.input_media
    if input_media is not None and not isinstance(input_media, (list, tuple)):
        raise ValueError("input_media must be an array")
    for media in input_media or ():
        media_type = str(
            (media.get("type") or media.get("media_type") or media.get("kind") or "image")
            if isinstance(media, dict) else "image"
        ).strip().casefold()
        if media_type == "file" and not capabilities.local_tools:
            raise ValueError("该 Resident ExecutionProvider 不读取本地文件附件")
        if media_type != "file" and not capabilities.supports_media:
            raise ValueError("该 Resident ExecutionProvider 不读取图片输入")


def _start_synchronous_delivery(
    request: ResidentInput, record: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Persist an input a synchronous ExecutionProvider answers and mark it running.

    Returns the Resident and the persisted message, or None for a retry
    that finds the input delivered already.
    """

    record_id = resident_domain_common.record_resident_id(record)
    message = request.with_business_ref(request.user_message(record_id))
    if existing_resident_delivery(record_id, message):
        return None
    message = request.with_business_ref(resident_lifecycle_domain.persist_resident_user_message(record_id, message))
    resident_conversation_stream.get_repository().set_input_state(record_id, request.message_id, "running")
    record["session_state"] = "running"
    record["updated_at"] = resident_domain_common.now_iso()
    _save_admitted_record(record)
    started = copy.deepcopy(record)
    resident_intake_admission.committed(request.resident_id, request.message_id)
    return started, message


def _deliver_synchronously(
    request: ResidentInput, record: dict[str, Any], message: dict[str, Any],
) -> dict[str, Any]:
    """Let the synchronous ExecutionProvider answer; its failure fails the input."""

    try:
        _send_synchronous_provider_message(
            record,
            request.actor_id,
            message_quotes.with_quote(request.message, request.quote),
            message_id=request.message_id,
            user_message=message,
        )
    except Exception as exc:
        with resident_record_domain.state_lock(request.resident_id):
            resident_conversation_stream.get_repository().set_input_state(
                request.resident_id,
                request.message_id,
                "failed",
                error=resident_record_domain.runtime_error_payload(str(exc)),
            )
            current = resident_record_domain.find_resident_for_admission(request.resident_id)
            if current:
                current["session_state"] = "active"
                current["last_error"] = str(exc)
                current["updated_at"] = resident_domain_common.now_iso()
                _save_admitted_record(current)
        raise
    return _receipt(request)


def _queue_input(
    request: ResidentInput, input_record: dict[str, Any], environment: dict[str, Any] | None, has_media: bool,
) -> dict[str, Any]:
    """Resolve the input outside the state lock, then answer it as a command or queue it as work."""

    prepared = _prepared_input(request, input_record, environment, has_media)
    resident_id, message_id = request.resident_id, request.message_id
    with resident_intake_admission.scope(resident_id, message_id), resident_record_domain.state_lock(resident_id):
        record = _deliverable_record(request)
        if prepared.environment is not None:
            # Keep current admission facts, adding only the prepared filesystem view.
            for field in ("default_cwd", "workspace_path", "workspace_root", "workspace_bindings", "workspace_paths", "owned_workspace"):
                if field in prepared.environment:
                    record[field] = prepared.environment[field]
        runtime_settings = resident_runtime_domain.read_resident_runtime_settings(record)
        answered = _answer_command(request, record, runtime_settings, prepared)
        if answered is not None:
            return answered
        running, initialization_blocked = _work_ahead(record)
        if not resident_backend.record_session_id(record) and not running:
            return _bootstrap_session(request, record, prepared)
        return _enqueue(request, record, prepared, runtime_settings, running, initialization_blocked)


def _prepared_input(
    request: ResidentInput, record: dict[str, Any], environment: dict[str, Any] | None, has_media: bool,
) -> _PreparedInput:
    message = request.message
    cwd = _resident_message_cwd(environment, request.working_directory) if environment is not None else None
    _ensure_backend_accepts_context_refs(record, request.context_refs)
    context_refs = resolve_context_refs(request.context_refs, request.attachment_actor, message)
    media_roots = resident_runtime_domain.resident_media_workspace_roots(environment) if has_media else ()
    facade_record = (resident_record_domain.find_resident(request.relay.source[0])
                     if request.relay.sender and request.input_media else None)
    if facade_record:
        # The facade accepted attached paths within its own Workspaces.
        media_roots = (*media_roots, *resident_runtime_domain.resident_media_workspace_roots(facade_record))
    attachments = resident_media_domain.resident_input_media(
        request.input_media,
        actor=request.attachment_actor,
        resident_id=resident_domain_common.record_resident_id(record),
        content=message,
        workspace_roots=media_roots,
    )
    if attachments:
        environment = environment or resident_record_domain.find_resident(request.resident_id)
        attachments = resident_media_domain.stage_resident_input_images(
            attachments, resident_record_domain.sessions_path(environment or record), request.message_id,
        )
    input_media = [item for item in attachments if item["media_type"] == "image"]
    provider_input = message_quotes.with_quote(
        resident_media_domain.resident_message_with_file_references(message, attachments), request.quote,
    )
    goal_command = slash_commands.parse_goal_command(message)
    last_user_message = last_user_message_text(request.resident_id) if goal_command.get("action") == "get" else ""
    return _PreparedInput(
        deferred_delivery=not message.lstrip().startswith("/"),
        environment=environment,
        cwd=cwd,
        context_refs=context_refs,
        input_media=input_media,
        provider_input=provider_input,
        goal_command=goal_command,
        last_user_message=last_user_message,
    )


def _answer_command(
    request: ResidentInput, record: dict[str, Any], runtime_settings: dict[str, Any], prepared: _PreparedInput,
) -> dict[str, Any] | None:
    """Answer the slash command the input is; None when the input is work for a turn."""

    scope_label = "Resident Session"
    result: dict[str, Any] = {}
    goal_payload = (
        resident_record_domain.resident_goal_payload(
            record,
            job=resident_execution_work.read(record),
            last_user_message=prepared.last_user_message,
        )
        if prepared.goal_command.get("action") == "get"
        else {}
    )
    # Every agent Provider answers the slash commands it declares, with or without a session.
    if request.runs_commands and resident_backend.execution_backend(record) in AGENT_PROVIDERS:
        result = resident_session_backend_domain.handle_resident_native_command(
            record,
            request.message,
            runtime_settings,
            session_id=resident_backend.record_session_id(record),
            cwd=str(prepared.cwd),
            actor=request.actor_id,
            can_update_runtime=True,
            can_manage_permissions=True,
            scope_label=scope_label,
            updated_at=resident_domain_common.now_iso(),
            token_usage=resident_record_domain.session_token_usage_payload(record),
            goal=goal_payload,
        )
    if result.get("native_compact"):
        return _queue_compaction_command(request, record, prepared)
    if request.runs_commands and not result.get("handled") and not result.get("native_goal"):
        result = slash_commands.handle_slash_command(
            request.message,
            runtime_settings,
            scope_label=scope_label,
            token_usage=resident_record_domain.session_token_usage_payload(record),
            backend=resident_backend.execution_backend(record),
        )
    if result.get("handled"):
        return _answer_portal_command(request, record, result, prepared)
    return None


def _queue_compaction_command(
    request: ResidentInput, record: dict[str, Any], prepared: _PreparedInput,
) -> dict[str, Any]:
    record_id = resident_domain_common.record_resident_id(record)
    command_message = request.user_message(record_id)
    if not existing_resident_delivery(
        record_id,
        command_message,
        bound_job=resident_execution_work.read(record),
        require_execution_binding=True,
    ):
        _queue_resident_compaction(record, request.actor_id, request.message_id, user_message=command_message)
    return _receipt(request, deferred_delivery=prepared.deferred_delivery)


def _answer_portal_command(
    request: ResidentInput, record: dict[str, Any], result: dict[str, Any], prepared: _PreparedInput,
) -> dict[str, Any]:
    record_id = resident_domain_common.record_resident_id(record)
    command_message = request.user_message(record_id)
    if existing_resident_delivery(record_id, command_message):
        return _receipt(request, deferred_delivery=prepared.deferred_delivery)
    if result.get("updates"):
        resident_runtime_domain.write_resident_runtime_settings(record, result["updates"])
    command_payload = result.get("command_payload")
    if (
        isinstance(command_payload, dict)
        and command_payload.get("type") == "codex_runtime_control"
        and command_payload.get("command") == "fast"
    ):
        result["active_turn_settings_update"] = resident_input_queue_domain.submit_active_turn_settings(
            record,
            request.actor_id,
            control_key=f"slash-fast:{request.message_id}",
            service_tier=str(command_payload.get("service_tier") or ""),
            source="slash_fast",
        )
    _persist_synchronous_command_exchange(
        record_id,
        request.actor_id,
        request.message,
        request.message_id,
        result,
        user_message=command_message,
    )
    record["updated_at"] = resident_domain_common.now_iso()
    _save_admitted_record(record)
    return _receipt(request, deferred_delivery=prepared.deferred_delivery)


def _work_ahead(record: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """The Job the input waits behind, and whether the Resident waits for its initialization to recover."""

    from . import resident_initialization
    initialization_blocked = resident_initialization.blocked(record)
    running = resident_input_queue_domain.active_resident_execution(record)
    if initialization_blocked:
        running = resident_execution_work.read(record)
        running['queue'] = resident_initialization.waiting_inputs(record, running)
        if not running.get('operation'):
            running = {'queue': running['queue'], 'operation': 'context_fork_create',
                       'outcome': 'failed', 'error': str(record.get('last_error') or '等待初始化恢复')}
    return running, initialization_blocked


def _bootstrap_session(request: ResidentInput, record: dict[str, Any], prepared: _PreparedInput) -> dict[str, Any]:
    """The first input of a Resident without a session runs as the turn that creates it."""

    queue_bootstrap_resident_session(
        record,
        message=prepared.provider_input,
        actor_id=request.actor_id,
        message_id=request.message_id,
        context_refs=prepared.context_refs,
        input_media=prepared.input_media,
        working_directory=prepared.cwd,
        runtime_context=request.runtime_context,
        activation_policy=request.activation.name,
        business_ref=request.business_ref,
        lineage_id=request.lineage_id,
        delivery_message_id=request.message_id if prepared.deferred_delivery else "",
        user_message=request.user_message(resident_domain_common.record_resident_id(record)),
    )
    record["session_state"] = resident_record_domain.record_backend_session_state(record)
    record["last_error"] = ""
    record["updated_at"] = resident_domain_common.now_iso()
    _save_admitted_record(record)
    return _receipt(request, deferred_delivery=prepared.deferred_delivery)


def _enqueue(
    request: ResidentInput,
    record: dict[str, Any],
    prepared: _PreparedInput,
    runtime_settings: dict[str, Any],
    running: dict[str, Any],
    initialization_blocked: bool,
) -> dict[str, Any]:
    """Queue the input behind the Job that runs, or start a Job with it."""

    now = resident_domain_common.now_iso()
    intake_state = resident_record_domain.resident_intake_state(record)
    user_item = request.with_business_ref(request.user_message(resident_domain_common.record_resident_id(record)))
    if existing_resident_delivery(
        resident_domain_common.record_resident_id(record),
        user_item,
        bound_job=running,
        require_execution_binding=True,
    ):
        return _receipt(request, deferred_delivery=prepared.deferred_delivery)
    queue_item = _queue_item(request, record, prepared, runtime_settings, user_item, now)
    record_message = _user_message_recorder(
        request.activation,
        starts_now=not running and not initialization_blocked and intake_state == resident_state.INTAKE_OPEN,
    )
    if not prepared.deferred_delivery:
        invariant_locks.defer_until_state_unlock(lambda: record_message(request.resident_id, user_item))
        invariant_locks.defer_until_state_unlock(
            lambda: lineage_message_events.wake_watchers(record, str(user_item.get("message_id") or ""))
        )
    if running:
        running[resident_state.INTAKE_FIELD] = intake_state
        queue = resident_execution_work.normalized_queue(running)
        queue_item.pop("execution_id", None)
        queue.append(queue_item)
        running["queue"] = queue
        running["updated_at"] = now
        job = running
    else:
        job = {
            **queue_item,
            "execution_id": queue_item["execution_id"],
            "started_at": now,
            "updated_at": now,
            "error": "",
            "queue": [],
        }
        job[resident_state.INTAKE_FIELD] = intake_state
    if prepared.deferred_delivery:
        resident_execution_work.admit(record, job, user_item)
    else:
        resident_execution_work.write(record, job)
    if not running:
        record["session_state"] = "queued" if intake_state == resident_state.INTAKE_CLOSED else "running"
    record["updated_at"] = now
    _save_admitted_record(record)
    return _receipt(request, deferred_delivery=prepared.deferred_delivery)


def _queue_item(
    request: ResidentInput,
    record: dict[str, Any],
    prepared: _PreparedInput,
    runtime_settings: dict[str, Any],
    user_item: dict[str, Any],
    now: str,
) -> dict[str, Any]:
    provider_message = resident_media_domain.provider_message_with_native_media(
        record,
        prepared.provider_input,
        prepared.input_media,
    )
    prompt_seed = {
        "user_message_id": str(user_item.get("message_id") or ""),
        "raw_message": request.message,
        "message": provider_message,
        "effective_message": provider_message,
        "actor": request.actor_id,
        "context_refs": prepared.context_refs,
        "input_media": prepared.input_media,
        "runtime_context": request.runtime_context,
        "additional_skills": request.additional_skills,
        **resident_runtime_domain.resident_session_prompt_controls(runtime_settings),
    }
    queue_item = resident_execution_work.new_item(
        user_item,
        request.message,
        provider_message,
        now,
        prepared.context_refs,
        prompt_cwd=str(prepared.cwd) if prepared.cwd is not None else "",
        input_media=prepared.input_media,
    )
    queue_item.update(prompt_seed)
    queue_item["actor"] = request.actor_id
    queue_item["activation_policy"] = request.activation.name
    if request.lineage_id:
        queue_item["lineage_id"] = str(request.lineage_id).strip()
    return request.with_business_ref(queue_item)


def _user_message_recorder(
    activation: ActivationPolicy, *, starts_now: bool,
) -> Callable[[str, Mapping[str, Any]], Any]:
    """An input that starts its turn now enters the Conversation at once; any other waits in the Mailbox."""

    if starts_now and activation.starts_turn:
        return resident_lifecycle_domain.persist_resident_user_message
    return resident_lifecycle_domain.accept_resident_user_message


def require_no_pending_lifecycle(resident_id: str) -> None:
    """Folder writes wait until a pending move has relocated the Resident folder."""

    if resident_record_domain.lifecycle_operations().pending(resident_id):
        raise ValueError("Resident 生命周期操作正在完成，请稍后编辑")


def last_user_message_text(resident_id: str) -> str:
    message = resident_conversation_stream.get_repository().latest_message(
        resident_id,
        role="user",
    )
    return resident_domain_common.protocol_message_text(message) if isinstance(message, dict) else ""


def resolve_context_refs(
    selection: object,
    actor: str = "",
    content: object = "",
) -> dict[str, Any]:
    return agent_context_selection.resolve(
        selection,
        actor=actor,
        content=content,
    )


def queue_bootstrap_resident_session(
    record: dict[str, Any],
    *,
    message: str,
    actor_id: str,
    context_refs: object = None,
    input_media: object = None,
    working_directory: Path | None = None,
    operation: str = "",
    append_user_message: bool = True,
    runtime_context: str = "",
    message_id: str = "",
    user_message: Mapping[str, Any] | None = None,
    activation_policy: str = "enqueue_and_trigger",
    business_ref: str = "",
    lineage_id: str = "",
    delivery_message_id: str = "",
) -> dict[str, Any]:
    message = str(message or "")
    if not message.strip():
        raise ValueError("message required")

    now = resident_domain_common.now_iso()
    resolved_refs = context_refs if isinstance(context_refs, dict) else {}
    resolved_input_media = (
        list(input_media) if isinstance(input_media, list) else []
    )
    provider_message = resident_media_domain.provider_message_with_native_media(
        record,
        message,
        resolved_input_media,
    )
    user_message_id = str(message_id or "").strip()
    user_item: dict[str, Any] = {}
    if append_user_message:
        user_item = (
            dict(user_message)
            if isinstance(user_message, Mapping)
            else resident_input_queue_domain.build_message(
                "user", message, actor_id, message_id=message_id,
            )
        )
        if business_ref:
            user_item["business_ref"] = str(business_ref).strip()[:500]
        if existing_resident_delivery(
            resident_domain_common.record_resident_id(record),
            user_item,
            bound_job=resident_execution_work.read(record),
            require_execution_binding=True,
        ):
            return resident_execution_work.read(record)
        user_message_id = str(user_item.get("message_id") or "")
    bootstrap_execution_id = f"execution-{secrets.token_hex(12)}"
    job = {
        "execution_id": bootstrap_execution_id,
        "user_message_id": user_message_id,
        "raw_message": message,
        "message": provider_message,
        "effective_message": provider_message,
        "actor": actor_id,
        "context_refs": resolved_refs,
        "input_media": resolved_input_media,
        "runtime_context": resident_prompt_domain.normalized_runtime_context(runtime_context),
        "started_at": now,
        "updated_at": now,
        "error": "",
        "queue": [],
        "bootstrap_session": True,
        "activation_policy": activation_policy,
    }
    if business_ref:
        job["business_ref"] = str(business_ref).strip()[:500]
    if lineage_id:
        job["lineage_id"] = str(lineage_id).strip()
    if operation:
        job["operation"] = operation
    if working_directory is not None:
        job["prompt_cwd"] = str(working_directory)
        job["working_directory"] = str(working_directory)
    job[resident_state.INTAKE_FIELD] = resident_record_domain.resident_intake_state(record)
    if user_item:
        job["input_message"] = copy.deepcopy(user_item)
        record_message = _user_message_recorder(
            ACTIVATION_POLICIES[activation_policy],
            starts_now=resident_record_domain.resident_intake_state(record) == resident_state.INTAKE_OPEN,
        )
        if not delivery_message_id:
            invariant_locks.defer_until_state_unlock(
                lambda: record_message(resident_domain_common.record_resident_id(record), user_item)
            )
    if delivery_message_id:
        resident_execution_work.admit(record, job, user_item)
    else:
        resident_execution_work.write(record, job)
    return job


@resident_lineage.access_snapshot_scope()
def deliver_resident_message_content(
    resident_id: str,
    actor: str,
    content: str,
    context_refs: object = None,
    working_directory: str = "",
    input_media: object = None,
    **options: Any,
) -> dict[str, Any]:
    """Send to one exact Resident; genealogy never participates in routing."""

    target = resident_record_domain.resolve_resident_admission_target(
        resident_id,
        actor,
        require_visible=not resident_input_scope.permits(resident_id, actor, options.get("message_id", "")),
        delivery=True,
    )
    result = persist_resident_input(
        target.resident_id,
        actor,
        content,
        context_refs,
        working_directory,
        input_media=input_media,
        **options,
    )
    return dict(result)
