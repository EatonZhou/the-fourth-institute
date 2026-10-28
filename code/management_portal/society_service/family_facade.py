"""Family facade Residents: one external identity whose messages are relayed to members (decision_44cf587c1721).

A facade is an ordinary Resident assigned the facade role in a Family.  It never runs a turn: intake accepts each message with input state
``queued`` and leaves it for the Family Router.  The Router delivers the
message to the chosen member with ``business_ref`` = ``<facade>/message/<id>``
(the relay mark).  The message enters the facade Conversation when it
arrives, so its sender sees it at once.  When the delivery commits, the facade
message is closed: its input state becomes ``completed`` and a relay link
records which member message carries it.  The facade Conversation
shown to external contacts is projected from these links like any Resident's
Conversation: each request keeps its sender, and the member replies follow as
the facade's own replies, naming the member that wrote them.  Member replies
are never copied into it.
"""
from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from management_common.actor_identity import canonical_actor_id, same_actor
from management_portal.execution.chat_position_index import RELAYED_COPY_PATTERN

from . import family_storage, resident_conversation_stream, resident_lineage, resident_mailbox
from .input_progress import event_input_ids, input_progress
from .domain_ports import resident_domain_port
from .state_store import get_lineage_data_store

# AgentData leaves these copies out of the Mailbox activity it keeps.
RELAY_REF = re.compile(RELAYED_COPY_PATTERN)
RESIDENT_REF = re.compile(r"^resident_[0-9a-f]{12}$")
MAX_PROJECTED_REPLIES = 20


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def facade_configuration(record: Mapping[str, Any] | None) -> dict[str, Any]:
    family = (record or {}).get('family') or {}
    if family.get('role') != 'facade':
        return {}
    return {'family_id': family['family_id'],
            'previous_leader_id': family.get('previous_leader_id', '')}


def is_facade(record: Mapping[str, Any] | None) -> bool:
    return ((record or {}).get('family') or {}).get('role') == 'facade'


def changed_roster_facades(before: Mapping[str, Any] | None, after: Mapping[str, Any]) -> list[str]:
    """Chat snapshots subscribe to facade projections, not Lineage graph changes."""
    def rosters(lineage: Mapping[str, Any]) -> dict[str, tuple]:
        result = {}
        for block in lineage.get("function_blocks") or []:
            family = block.get("family") or {}
            facade = family.get("facade")
            if facade:
                result[facade] = (block["id"], family.get("title"), family.get("router"),
                                  family.get("steward"), tuple(sorted(family.get("members") or [])))
        return result

    old, new = rosters(before or {}), rosters(after)
    return sorted(facade for facade in old.keys() | new.keys() if old.get(facade) != new.get(facade))


def is_steward(record: Mapping[str, Any] | None) -> bool:
    return ((record or {}).get('family') or {}).get('role') == 'steward'


def is_operating_role(record: Mapping[str, Any] | None) -> bool:
    return ((record or {}).get('family') or {}).get('role') in {'router', 'steward'}


def is_own_steward(facade: Mapping[str, Any] | None, actor: object) -> bool:
    from . import resident_record_domain
    actor_id = str(actor or '').strip().lower()
    if not RESIDENT_REF.fullmatch(actor_id):
        return False
    record = resident_record_domain.find_resident(actor_id) or {}
    return is_steward(record) and bool((facade or {}).get('family')) and record['family']['family_id'] == facade['family']['family_id']


def relay_mark(facade_id: str, message_id: str) -> str:
    return f"{facade_id}/message/{message_id}"


def parse_relay_mark(business_ref: object) -> tuple[str, str] | None:
    match = RELAY_REF.fullmatch(str(business_ref or "").strip())
    return (match.group(1), match.group(2)) if match else None


def validate_relay(business_ref: object, actor: str) -> tuple[str, str] | None:
    """The facade message a delivery relays, checked before the delivery is accepted.

    Only the facade message's sender, the facade itself (the Family Router
    runs as the Lineage runtime principal, which is the facade once a leader
    member is familized) or the facade's owner may relay it, so a relay never
    closes someone else's message.  Marks that do not point at a
    facade message are ordinary business references.
    """

    from . import resident_access, resident_record_domain

    source = parse_relay_mark(business_ref)
    if source is None:
        return None
    facade = resident_record_domain.find_resident(source[0])
    if not is_facade(facade):
        return None
    message = resident_conversation_stream.get_repository().message(source[0], source[1])
    if message is None:
        raise KeyError("Family 门面中没有这条消息")
    actor_id = canonical_actor_id(actor)
    if not (
        same_actor(message.get("author_id"), actor_id)
        or same_actor(source[0], actor_id)
        or resident_access.can_manage(facade, actor_id)
    ):
        raise PermissionError("只有门面消息的发送者、门面自身或能管理门面的身份可以转交这条消息")
    return source


MEMBER_SLOT = "members"


def family_members(facade: Mapping[str, Any]) -> list[str]:
    """Residents belonging to the facade's Family, in stable Resident-ID order."""

    family_id = str(facade_configuration(facade).get("family_id") or "")
    if not family_id:
        return []
    with get_lineage_data_store().database.read() as connection:
        return family_storage.members_of(connection, family_id)


def family_member_ids(facade: Mapping[str, Any]) -> set[str]:
    """Residents belonging to the facade's Family as members."""

    return set(family_members(facade))


def chat_presentation(facade: Mapping[str, Any]) -> dict[str, Any]:
    """What a facade's Chat says about its Family; empty for any other Resident.

    A facade never runs, so its Chat has no session of its own to show; until
    a message goes through it, the Chat says what the facade is and who is
    behind it.
    """

    if not is_facade(facade):
        return {}
    from . import resident_record_domain

    members = []
    for member_id in family_members(facade):
        member = resident_record_domain.find_resident(member_id) or {}
        members.append({"resident_id": member_id, "title": str(member.get("title") or member_id)})
    family_id = str(facade_configuration(facade).get("family_id") or "")
    with get_lineage_data_store().database.read() as connection:
        roster = family_storage.roles_of(connection, family_id)
    from . import resident_catalog, resident_execution_gate
    from .family_owners import recipients
    role_ids = roster["router"] + roster["steward"]
    role_records = {r["resident_id"]: r for r in resident_catalog.get_catalog().get_many(role_ids)}
    gates = resident_execution_gate.project_states({
        rid: {"state": {"intake_state": resident_record_domain.resident_intake_state(record)}}
        for rid, record in role_records.items()
    }, resident_records=role_records)
    has_contact = bool(recipients(facade, creator_only=True))
    roles = []
    for role in ("router", "steward"):
        for resident_id in roster[role] or [""]:
            record = role_records.get(resident_id) or {}
            from .family_quota import role_availability
            availability = (role_availability(record, execution_gate=gates[resident_id]["state"]["execution_gate"])
                            if record else {"status": "unavailable", "reason": "角色不存在"})
            roles.append({"resident_id": resident_id, "title": str(record.get("title") or resident_id or role), "role": role,
                          "availability": availability})
    roles.extend({**member, "role": "member"} for member in members)
    return {
        "family_id": family_id,
        "title": str(facade.get("title") or resident_lineage.resident_id(facade)),
        "members": members,
        "nodes": roles,
        "alert_contact": {"status": "configured" if has_contact else "missing"},
        "lineage_id": resident_lineage.lineage_id(facade),
    }


def family_steward_id(facade: Mapping[str, Any]) -> str:
    """The steward of the facade's Family, if it has one."""

    family_id = str(facade_configuration(facade).get("family_id") or "")
    if not family_id:
        return ""
    with get_lineage_data_store().database.read() as connection:
        stewards = family_storage.roles_of(connection, family_id)["steward"]
    return stewards[0] if stewards else ""


def info_forward_message_id(facade_id: str, info_message_id: str) -> str:
    """The steward's copy of one facade info; forwarding it again changes nothing."""

    import hashlib

    digest = hashlib.sha256(f"family-info\0{facade_id}\0{info_message_id}".encode("utf-8")).hexdigest()
    return "msg_" + digest[:32]


def tell_steward(facade_id: str, steward_id: str, initiated_by: str, content: str, **options: Any) -> dict[str, Any]:
    """Send a Family-internal note to the steward as the facade (decision_44cf587c1721).

    The Family shows the outside one identity, so whatever causes a note - a
    user's Familize, an info sent to the facade, a member deleted -
    the steward hears it from the facade.  Whoever caused it is recorded only
    as the initiator, so the steward never becomes one of that user's
    conversations.
    """

    from management_common.operation_identity import operation_identity_scope
    from management_common.principal import OperationIdentity

    if not options.pop("_family_rendered", False):
        from .family_tasks import render
        from . import resident_record_domain
        record = resident_record_domain.find_resident(steward_id)
        trigger = options.pop("task_trigger", "organization")
        facts = options.pop("task_facts", {"change": content})
        if record and (record.get("family") or {}).get("role") == "steward":
            content = render(record, trigger, facts)
    sender = facade_id or initiated_by
    identity = OperationIdentity.for_actor(sender, initiated_by=initiated_by or sender)
    with operation_identity_scope(identity):
        return resident_domain_port.deliver_resident_message_content(steward_id, sender, content, **options)


def forward_info(facade: Mapping[str, Any], message: Mapping[str, Any], actor: str) -> dict[str, Any] | None:
    """Hand an info message the facade received to the Family's steward (decision_44cf587c1721).

    Whom an info concerns depends on what it says, not on which member the
    next request goes to, so the steward gets it at once and decides which
    members need it.  Like every internal note it comes from the facade; the
    original sender is named in its context.  The facade keeps showing it like
    any Resident shows an info: with the next request.
    """

    steward_id = family_steward_id(facade)
    facade_id = str(facade.get("resident_id") or "")
    info_id = str(message.get("message_id") or "")
    text = "\n".join(
        str(part.get("text") or "")
        for part in message.get("content_parts") or []
        if isinstance(part, Mapping) and part.get("type") == "text" and part.get("text")
    ).strip()
    if not steward_id or not facade_id or not info_id or not text:
        return None
    forward_id = info_forward_message_id(facade_id, info_id)
    try:
        return tell_steward(
            facade_id,
            steward_id,
            actor,
            text,
            message_id=forward_id,
            runtime_context=(
                f"这是 {actor} 发给 Family 门面 {facade_id} 的 info 消息（背景更新），门面已把它转给你。"
                "只处理与你的分工、图维护、队列和恢复职责相关的事项；工作资料由工作者维护，"
                "你不承担记忆同步或消息广播，不回复发送者。"
            ),
            business_ref=f"{facade_id}/info/{info_id}",
        )
    except ValueError:
        # Forwarded already, before notes came from the facade: the copy stays as it is.
        if resident_conversation_stream.get_repository().message(steward_id, forward_id):
            return None
        raise


def is_relay_target(facade: Mapping[str, Any], resident_id: str) -> bool:
    """Business members and this Family's steward can own facade requests.

    The steward owns organization work without becoming a business roster member.
    """
    return bool(resident_id) and (
        resident_id in family_member_ids(facade) or resident_id == family_steward_id(facade))


def relay_delegates(source: tuple[str, str] | None, member_id: str) -> bool:
    """A checked relay authorizes delivery to a member of the same Family (门面委托授权).

    Organization requests and answers to its messages can go to the own steward.
    """

    from . import resident_record_domain

    if source is None:
        return False
    facade = resident_record_domain.find_resident(source[0])
    if not facade:
        return False
    return is_relay_target(facade, member_id)


def relay_sender(source: tuple[str, str] | None, message: Mapping[str, Any] | None) -> str:
    """The sender whose access a relay's attachments and references are read with; empty if none.

    A message sent to the facade may carry images, files and references like a
    message to any Resident; the facade checks the sender's right to each when
    it arrives.  The member reads them as if the sender had sent the message
    to it directly, but only when the relay carries the facade message
    unchanged: a relay that adds or alters anything is read with the
    relaying actor's own access.  Another Family's message is its member's,
    sent as that Family's facade: it arrived checked with the member's
    access, and is read with it again.
    """

    if source is None or not isinstance(message, Mapping):
        return ""
    stored = resident_conversation_stream.get_repository().message(source[0], source[1])
    if stored is None or message.get("content_parts") != stored.get("content_parts"):
        return ""
    author = str(stored.get("author_id") or "")
    origin = stored.get("origin") if isinstance(stored.get("origin"), Mapping) else {}
    initiator = origin.get("initiated_by") if isinstance(origin.get("initiated_by"), Mapping) else {}
    member = str(initiator.get("id") or "") if initiator.get("kind") == "resident" else ""
    if member and member != author:
        from .outward_messages import speaks_for

        if speaks_for(author, member):
            return member
    return author


def complete_relay(source: tuple[str, str], member_id: str, member_message_id: str) -> None:
    """Close the facade message and remember which member message carries it; repeatable."""

    from . import invariant_locks

    # A nested facade accepts its input in an after-unlock effect. Do not
    # publish a link to that input before the earlier admission effect runs:
    # GetInputState follows the link and must always find the accepted holder.
    invariant_locks.defer_until_state_unlock(lambda: _complete_relay(source, member_id, member_message_id))


def _complete_relay(source: tuple[str, str], member_id: str, member_message_id: str) -> None:
    from . import resident_record_domain

    facade_id, facade_message_id = source
    facade = resident_record_domain.find_resident(facade_id)
    if not facade:
        return
    store = get_lineage_data_store()
    lineage_id = resident_lineage.lineage_id(facade)
    repository = resident_conversation_stream.get_repository()
    with store.family_quota_reviews.transaction(lineage_id, facade_id):
        current = repository.input_state(facade_id, facade_message_id).get("state")
        if current == "queued":
            with store.family_relays.transaction(lineage_id, facade_id) as state:
                links = state.setdefault("links", {})
                links.setdefault(facade_message_id, {
                    "resident_id": member_id, "message_id": member_message_id, "relayed_at": _now(),
                })
            show_request(facade_id, facade_message_id)
            repository.set_input_state(facade_id, facade_message_id, "completed")
    if current == "queued":
        from . import family_recovery
        family_recovery.relay_completed(facade, facade_message_id)
    if current == "cancelled":
        # A withdrawal wins admission; cancel the accepted copy outside the review transaction.
        from . import resident_protocol
        message = repository.message(facade_id, facade_message_id)
        resident_protocol.cancel_resident_input(member_id, str(message["author_id"]), member_message_id)


def show_request(facade_id: str, message_id: str) -> None:
    """Put a facade request in the facade Conversation; repeatable.

    A request shows when it arrives, while the Router still chooses a member;
    a request accepted before this rule shows when it is relayed.
    """

    repository = resident_conversation_stream.get_repository()
    message = repository.message(facade_id, message_id)
    if message is None or message.get("activated_at"):
        return
    # The public message leaves out business_ref; a nested facade's message
    # carries the outer relay mark, and the stored message must not change.
    business_ref = repository.message_business_ref(facade_id, message_id)
    if business_ref:
        message = {**message, "business_ref": business_ref}
    repository.commit_user_message(facade_id, message, activated_at=_now())


def relayed_outcomes(request_ids: Mapping[str, list[str]]) -> dict[str, dict[str, Any]]:
    """The latest outcome of each facade's requests, as its members finished them.

    ``request_ids`` maps Residents to the requests one sender made there; only
    facades with relays are answered.  A facade never runs a turn, so a request
    is answered when the member that took it finishes: the result is that
    member's input state under the facade request's ID, or ``{}`` while none
    has finished (decision_44cf587c1721).  The newest relays are checked.
    """

    records = get_lineage_data_store().family_relays.by_keys(list(request_ids))
    repository = resident_conversation_stream.get_repository()
    # Many requests pass the same facades and members: read each Resident once.
    relays = _RelayBatch({})
    outcomes: dict[str, dict[str, Any]] = {}
    for facade_id, state in records.items():
        links = state.get("links") if isinstance(state.get("links"), Mapping) else {}
        carried: dict[tuple[str, str], str] = {}
        by_member: dict[str, list[str]] = {}
        for request_id in list(request_ids.get(facade_id) or [])[-MAX_ACTIVITY_LINKS:]:
            link = links.get(str(request_id))
            if not isinstance(link, Mapping):
                continue
            # A nested Family's request is answered by the member that does the work.
            _, (member_id, member_message_id) = relays.holder(
                str(link.get("resident_id") or ""), str(link.get("message_id") or ""),
            )
            if member_id and member_message_id:
                carried[(member_id, member_message_id)] = str(request_id)
                by_member.setdefault(member_id, []).append(member_message_id)
        latest: dict[str, Any] = {}
        if by_member:
            for member_id, member_state in repository.latest_notifiable_input_states_for_messages(by_member).items():
                request_id = carried.get((member_id, str(member_state.get("message_id") or "")))
                if request_id and (str(member_state.get("updated_at") or ""), request_id) > (
                    str(latest.get("updated_at") or ""), str(latest.get("message_id") or ""),
                ):
                    latest = {**member_state, "message_id": request_id}
        outcomes[facade_id] = latest
    return outcomes


def relay_source_of(member_id: str, member_message_id: str) -> tuple[str, str] | None:
    """The facade message a member message carries, when the facade recorded that relay."""

    from . import resident_record_domain

    source = parse_relay_mark(
        resident_conversation_stream.get_repository().message_business_ref(member_id, member_message_id)
    )
    if source is None:
        return None
    facade = resident_record_domain.find_resident(source[0])
    if not is_facade(facade):
        return None
    link = relay_links(facade).get(source[1]) or {}
    if link.get("resident_id") != member_id or link.get("message_id") != member_message_id:
        return None
    return source


MAX_ACTIVITY_LINKS = 20
# A message passes at most this many facades; the Family Router refuses deeper relays.
MAX_RELAY_HOPS = 4


def relay_sources_of(member_id: str, member_message_id: str) -> list[tuple[str, str]]:
    """Every facade message a member message carries, innermost first.

    In a nested Family the inner facade's message was itself relayed from the
    outer facade, so the member's work answers both.
    """

    sources: list[tuple[str, str]] = []
    current = (member_id, member_message_id)
    while len(sources) < MAX_RELAY_HOPS:
        source = relay_source_of(*current)
        if source is None or source in sources:
            break
        sources.append(source)
        current = source
    return sources


def relay_holder(resident_id: str, message_id: str) -> tuple[list[str], tuple[str, str]]:
    """Follow relays down from a message: the facades it passed and the Resident message that does the work.

    Until an inner facade's Router relays it, the inner facade holds it.
    """

    return _RelayBatch({}).holder(resident_id, message_id)


class _RelayBatch:
    """Relay graph facts for one projection; each Resident is read once."""

    def __init__(self, links: Mapping[str, Mapping[str, Any]]) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self.links: dict[str, dict[str, Any]] = {}
        self.holders = {
            message_id: self.holder(str(link.get("resident_id") or ""), str(link.get("message_id") or ""))
            for message_id, link in links.items()
        }

    def record(self, resident_id: str) -> dict[str, Any]:
        from . import resident_record_domain
        if resident_id not in self.records:
            self.records[resident_id] = resident_record_domain.find_resident(resident_id) or {}
        return self.records[resident_id]

    def member_view(self, resident_id: str) -> dict[str, str]:
        return {"title": str(self.record(resident_id).get("title") or "")}

    def holder(self, resident_id: str, message_id: str) -> tuple[list[str], tuple[str, str]]:
        via: list[str] = []
        current = (resident_id, message_id)
        while len(via) < MAX_RELAY_HOPS:
            record = self.record(current[0])
            if not is_facade(record):
                break
            if current[0] not in self.links:
                self.links[current[0]] = relay_links(record)
            link = self.links[current[0]].get(current[1]) or {}
            if not link.get("resident_id") or not link.get("message_id") or current[0] in via:
                break
            via.append(current[0])
            current = (str(link["resident_id"]), str(link["message_id"]))
        return via, current


def relay_activity(facade_id: str) -> dict[str, Any] | None:
    """The facade's work as seen by ``resident.work_started``: a member running one of its relayed messages.

    ``None`` when the Resident is not a facade.  Only the most recent relays
    are checked; an older relay still running is reported once a newer one
    finishes or when it is among the latest.
    """

    from . import resident_record_domain

    facade = resident_record_domain.find_resident(facade_id)
    if not is_facade(facade):
        return None
    links = sorted(
        relay_links(facade).items(), key=lambda item: str(item[1].get("relayed_at") or ""), reverse=True,
    )[:MAX_ACTIVITY_LINKS]
    repository = resident_conversation_stream.get_repository()
    batch = _RelayBatch(dict(links))
    states = resident_mailbox.InputProjection(
        [holder for _, holder in batch.holders.values()], repository).states()
    for facade_message_id, (_, holder) in batch.holders.items():
        member_id, member_message_id = holder
        state = states.get(holder, {})
        if str(state.get("state") or "") == "running":
            return {
                "active": True,
                "message_id": facade_message_id,
                "started_at": str(state.get("updated_at") or ""),
                "status": "running",
                "member_resident_id": member_id,
            }
    return {"active": False}


def withdraw_request(facade: Mapping[str, Any], message_id: str, actor: str, *, manager: bool) -> None:
    """Withdraw a facade request no member has yet: being routed, waiting for members or for its quota review.

    Its sender or a facade owner withdraws it; the Router's later choice then
    relays nothing (family_quota.relay_message reports it withdrawn).
    """

    from management_common.actor_identity import same_actor

    facade_id = resident_lineage.resident_id(facade)
    repository = resident_conversation_stream.get_repository()
    with get_lineage_data_store().family_quota_reviews.transaction(resident_lineage.lineage_id(facade), facade_id):
        message = repository.message(facade_id, message_id)
        if not message or message.get("role") != "user":
            raise KeyError("queue item not found")
        if message_id in relay_links(facade):
            raise ValueError("这条请求已经交给成员，请刷新后在成员处撤回或停止")
        if str(repository.input_state(facade_id, message_id).get("state") or "") != "queued":
            raise ValueError("这条请求已经结束，不能再撤回")
        if not (manager or same_actor(message.get("author_id"), actor)):
            raise PermissionError("只有能管理门面的身份或这条请求的发送者可以撤回")
        repository.set_input_state(facade_id, message_id, "cancelled", updated_at=_now())


def relay_links(facade: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    state = get_lineage_data_store().family_relays.get(resident_lineage.lineage_id(facade), resident_lineage.resident_id(facade))
    return {
        str(key): dict(value)
        for key, value in (state.get("links") or {}).items()
        if isinstance(value, Mapping)
    }


def project_input_state(
    facade: Mapping[str, Any], message_id: str, state: dict[str, Any], *,
    include_progress: bool = False, include_context: bool = False,
) -> dict[str, Any]:
    """The external request follows its worker, not the facade's routing receipt.

    Keep the facade request identity and the worker's canonical reply IDs;
    GetMessage already resolves those replies through the facade projection.
    """
    from . import resident_protocol

    facade_id = resident_lineage.resident_id(facade)
    _, holder = relay_holder(facade_id, message_id)
    if holder != (facade_id, message_id):
        projected = resident_protocol._stored_resident_input_state(
            *holder, include_progress=include_progress, include_context=include_context,
        )
        return {**projected, "message_id": message_id}
    if include_context:
        # A facade has no Provider context before handing the request on.
        state = {**state, "in_context": False}
    if include_progress and state.get("state") == "queued":
        request = resident_conversation_stream.get_repository().message(facade_id, message_id)
        if request:
            shown = project_messages(facade, [request], input_states={message_id: "queued"})
            if shown[0].get("relay"):
                state = {**state, "progress": shown[0]["relay"]}
    return state


def project_snapshot(facade: Mapping[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    """Show the facade Conversation like any Resident's: each request followed by the Family's reply.

    Each request carries its own progress (routing, its place in the member's
    queue, the member's live work), so several requests in flight each show
    theirs. Family-wide display activity is separate from that request state.
    """

    from . import family_activity

    facade_id = resident_lineage.resident_id(facade)
    snapshot = family_activity.project_states({facade_id: snapshot})[facade_id]
    states = {
        str(item.get("message_id") or ""): str(item.get("state") or "")
        for item in snapshot.get("input_states") or []
        if isinstance(item, Mapping)
    }
    snapshot["messages"] = project_messages(facade, list(snapshot.get("messages") or []), input_states=states)
    snapshot.update(_project_runtime(facade, snapshot, states))
    snapshot["family_facade"] = {**facade_configuration(facade), **chat_presentation(facade)}
    return snapshot


# A facade request the Router has not relayed yet.
ROUTING_STATE = "routing"
INFO_MARKS = ("held_info", "released_with", "held_info_steward")


def project_messages(
    facade: Mapping[str, Any], messages: list[Any], *, input_states: Mapping[str, str] | None = None,
) -> list[Any]:
    """Each request keeps its sender and carries its relay; its member replies follow it as the facade's replies.

    Replies, intermediate outputs included, are read from the member
    Conversation on every projection, never copied.  With ``input_states``
    (a live snapshot) unfinished requests carry their progress, and a request
    still waiting for the Router carries ``relay.state == "routing"``.
    """

    facade_id = resident_lineage.resident_id(facade)
    links = relay_links(facade)
    held = get_lineage_data_store().family_quota_reviews.by_keys([facade_id]).get(facade_id, {})
    from . import family_recovery
    recoveries = family_recovery.records(facade)
    reviews, scopes = held.get("requests") or {}, held.get("scope_requests") or {}
    repository = resident_conversation_stream.get_repository()
    states = dict(input_states or {})
    unlinked = [(facade_id, str(message["message_id"])) for message in messages
                if isinstance(message, dict) and message.get("role") == "user" and message.get("message_id")
                and message["message_id"] not in links and message["message_id"] not in states
                and not any(message.get(mark) for mark in INFO_MARKS)]
    if unlinked:
        states.update({message_id: value.get("state", "")
                       for (_, message_id), value in repository.input_states_by_id(unlinked).items()})
    members: dict[str, dict[str, Any]] | None = {} if input_states is not None else None
    member_ids = family_members(facade) if input_states is not None else []
    selected = {str(message.get("message_id") or ""): links[str(message["message_id"])]
                for message in messages if isinstance(message, dict) and message.get("role") == "user"
                and str(message.get("message_id") or "") in links}
    projected = _relay_views(selected, repository, members)
    result: list[Any] = []
    for message in messages:
        result.append(message)
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        message_id = str(message.get("message_id") or "")
        if (message_id not in links and states.get(message_id) == "cancelled"
                and not any(message.get(mark) for mark in INFO_MARKS)):
            result[-1] = _withdrawn_request(message)
            continue
        if message_id in recoveries:
            message["relay"] = family_recovery.view(facade, recoveries[message_id])
            continue
        link = links.get(message_id)
        if not link:
            if (input_states or {}).get(message_id) == "queued" and not any(message.get(mark) for mark in INFO_MARKS):
                review = reviews.get(message_id) or {}
                if (scopes.get(message_id) or {}).get("status") == "waiting":
                    # No member's duties cover it: the steward is deciding who takes it.
                    message["relay"] = {"state": "scope_review"}
                elif review.get("status") == "waiting":
                    message["relay"] = {"state": "quota_review", "resident_id": review["member_id"],
                                        "weekly_remaining_percent": review.get("weekly_remaining_percent")}
                elif quoted := quoted_member(facade, message):
                    # A quote of a member's message goes to that member without the Router.
                    message["relay"] = {"state": "delivering", "resident_id": quoted, **_member_view(quoted)}
                else:
                    message["relay"] = {"state": ROUTING_STATE}
            continue
        relay, reply_messages = projected[message_id]
        message["relay"] = relay
        if relay.get("withdrawn"):
            result[-1] = _withdrawn_request(message)
            continue
        result.extend(_facade_reply(facade_id, reply, relay, message_id) for reply in reply_messages)
    return result


def _withdrawn_request(message: Mapping[str, Any]) -> dict[str, Any]:
    """Keep a facade's place in history without displaying the withdrawn body."""
    projected = {key: value for key, value in message.items()
                 if key not in {"content", "quote", "context_refs", "attachments", "input_media"}}
    return {**projected, "content_parts": [], "withdrawn": True}


def relayed_rounds(facade: Mapping[str, Any], message_ids: list[str]) -> dict[str, tuple[dict[str, Any], list[dict[str, Any]]]]:
    """For each relayed request: the member that took it and that member's replies.

    Requests not relayed yet are left out.
    """

    links = relay_links(facade)
    repository = resident_conversation_stream.get_repository()
    return _relay_views({message_id: links[message_id] for message_id in message_ids if message_id in links}, repository)


# The facade's own work phases: the Router is choosing a member, or the member
# has the message but has not started it.
ROUTING_PHASE = "family_routing"
WAITING_PHASE = "family_waiting"


def _project_runtime(
    facade: Mapping[str, Any], snapshot: Mapping[str, Any], input_states: Mapping[str, str],
) -> dict[str, Any]:
    """The facade's work state: a member running one of its requests, waiting for one, or the Router routing.

    The live activity belongs to each request (``relay.activities``), so the
    facade itself shows none.
    """

    base = dict(snapshot.get("state") or {})
    repository = resident_conversation_stream.get_repository()
    links = relay_links(facade)
    waiting: tuple[str, str, str] | None = None
    recent = dict(sorted(links.items(), key=lambda item: str(item[1].get("relayed_at") or ""), reverse=True)[:MAX_ACTIVITY_LINKS])
    batch = _RelayBatch(recent)
    states = resident_mailbox.InputProjection(
        [holder for _, holder in batch.holders.values()], repository).states()
    member_states: dict[str, dict[str, Any]] = {}
    for facade_message_id in recent:
        _, (member_id, member_message_id) = batch.holders[facade_message_id]
        if (member_id, member_message_id) not in states:
            continue
        input_state = states[(member_id, member_message_id)]
        state = str(input_state.get("state") or "")
        progress_id = str(input_state.get("grouped_into_message_id") or member_message_id)
        if state not in {"queued", "running"}:
            continue
        try:
            if member_id not in member_states:
                member_states[member_id] = repository.current_state(member_id)
            member_state = member_states[member_id]
        except KeyError:
            continue
        if state == "running" and member_state.get("active_message_id") == progress_id:
            return {
                "state": {
                    **base,
                    "work_state": str(member_state.get("work_state") or "running"),
                    "work_phase": str(member_state.get("work_phase") or ""),
                    "active_message_id": facade_message_id,
                    "family_member": {"resident_id": member_id, **_member_view(member_id)},
                },
                "activities": [],
            }
        if state == "queued" and waiting is None:
            waiting = (facade_message_id, member_id, member_message_id)
    if waiting is not None:
        facade_message_id, member_id, _ = waiting
        return {"state": {
            **base, "work_state": "queued", "work_phase": WAITING_PHASE, "active_message_id": facade_message_id,
            "family_member": {"resident_id": member_id, **_member_view(member_id)},
        }, "activities": []}
    unrelayed = [message_id for message_id, state in input_states.items() if state == "queued" and message_id not in links]
    if not unrelayed:
        return {}
    relays = {str(message.get("message_id") or ""): message.get("relay") or {}
              for message in snapshot.get("messages") or [] if isinstance(message, Mapping)}
    delivering = [relays[message_id] for message_id in unrelayed if (relays.get(message_id) or {}).get("state") == "delivering"]
    if len(delivering) < len(unrelayed):
        return {"state": {**base, "work_state": "queued", "work_phase": ROUTING_PHASE, "active_message_id": ""},
                "activities": []}
    # A quote can identify its recipient without the Router.
    member = delivering[0]
    return {"state": {
        **base, "work_state": "queued", "work_phase": WAITING_PHASE, "active_message_id": unrelayed[0],
        "family_member": {"resident_id": member["resident_id"], "title": str(member.get("title") or "")},
    }, "activities": []}


def projected_reply(facade: Mapping[str, Any], message_id: str) -> dict[str, Any] | None:
    """A member message shown in the facade Conversation, as shown there, looked up by its message ID."""

    from . import personal_history
    historical = personal_history.message(facade, message_id)
    if historical is not None:
        return historical
    found = relayed_reply(facade, message_id)
    if found is None:
        return None
    worker_id = found["worker_id"]
    relay = {"resident_id": worker_id, "title": _member_view(worker_id).get("title", ""),
             "via": [{"resident_id": item, **_member_view(item)} for item in found["via"]]}
    return _facade_reply(resident_lineage.resident_id(facade), found["reply"], relay, found["request_message_id"])


def relayed_reply(facade: Mapping[str, Any], message_id: str) -> dict[str, Any] | None:
    """A member message shown in the facade Conversation: the request it answers, who took it, and the message.

    Every Agent message a member writes answering a relayed request shows,
    intermediate outputs included.  ``member_id`` is the Family's member that
    took the request; ``worker_id`` the Resident that wrote the message, which
    differs in a nested Family, reached ``via`` its facades.
    """

    clean = str(message_id or "")
    if not clean:
        return None
    repository = resident_conversation_stream.get_repository()
    carried: dict[str, dict[str, tuple[str, str, list[str]]]] = {}
    for request_id, link in relay_links(facade).items():
        member_id = str(link.get("resident_id") or "")
        via, (worker_id, worker_message_id) = relay_holder(member_id, str(link.get("message_id") or ""))
        carried.setdefault(worker_id, {})[worker_message_id] = (request_id, member_id, via)
    # A message lives in one Conversation: each Resident doing the Family's work is read once.
    for worker_id, requests in carried.items():
        reply = repository.message(worker_id, clean)
        if reply is None or reply.get("role") != "agent":
            continue
        reply_to = str(reply.get("reply_to_message_id") or "")
        found = requests.get(reply_to)
        if found is None:
            members = resident_mailbox.members_of({"resident_id": worker_id}, reply_to)
            candidates = [(worker_id, item) for item in requests if item in members]
            sources = resident_mailbox.InputProjection(candidates, repository).reply_sources()
            found = next((requests[key[1]] for key, source in sources.items() if source[1] == reply_to), None)
        if found is None:
            continue
        request_id, member_id, via = found
        return {"request_message_id": request_id, "member_id": member_id, "worker_id": worker_id,
                "via": via, "reply": reply}
    return None


def projection_targets(event: Mapping[str, Any]) -> list[str]:
    """The facades whose projected Conversation changes with this member Conversation event."""

    resident_id = str(event.get("resident_id") or "")
    if not resident_id:
        return []
    from . import family_activity

    message_ids = event_input_ids(event)
    expanded = list(dict.fromkeys([
        *message_ids,
        *(member for message_id in message_ids
          for member in resident_mailbox.members_of({"resident_id": resident_id}, message_id)),
    ]))
    targets = [
        facade_id
        for message_id in expanded
        if _carries_relay_mark(resident_id, message_id)
        for facade_id, _ in relay_sources_of(resident_id, message_id)
    ]
    return list(dict.fromkeys([*targets, *family_activity.projection_targets(event)]))


_UNMARKED: dict[tuple[str, str], None] = {}
_UNMARKED_LIMIT = 4096


def _carries_relay_mark(resident_id: str, message_id: str) -> bool:
    # business_ref never changes once a message is stored, so a message without
    # a relay mark is remembered and not read again.
    key = (resident_id, message_id)
    if key in _UNMARKED:
        return False
    repository = resident_conversation_stream.get_repository()
    if parse_relay_mark(repository.message_business_ref(resident_id, message_id)) is not None:
        return True
    if repository.message(resident_id, message_id) is not None:
        if len(_UNMARKED) >= _UNMARKED_LIMIT:
            _UNMARKED.pop(next(iter(_UNMARKED)))
        _UNMARKED[key] = None
    return False


def _relay_views(
    links: Mapping[str, Mapping[str, Any]], repository: Any,
    members: dict[str, dict[str, Any]] | None = None,
) -> dict[str, tuple[dict[str, Any], list[dict[str, Any]]]]:
    """Project selected rounds with one batched state/reply read, preserving input order."""
    if not links:
        return {}
    batch = _RelayBatch(links)
    rounds = resident_mailbox.InputProjection(
        [holder for _, holder in batch.holders.values()], repository).rounds(limit=MAX_PROJECTED_REPLIES)
    result = {}
    for message_id, link in links.items():
        via, (member_id, member_message_id) = batch.holders[message_id]
        round_ = rounds[(member_id, member_message_id)]
        state = str(round_["input_state"].get("state") or "queued")
        relay: dict[str, Any] = {
            "resident_id": member_id, **batch.member_view(member_id),
            "message_id": member_message_id, "state": state,
            "relayed_at": str(link.get("relayed_at") or ""),
        }
        group_id = str(round_["input_state"].get("grouped_into_message_id") or "")
        if group_id:
            relay["grouped_into_message_id"] = group_id
        if round_.get("withdrawn"):
            relay["withdrawn"] = True
        if via:
            relay["via"] = [{"resident_id": item, **batch.member_view(item)} for item in via]
        moved = [str(item.get("resident_id") or "") for item in link.get("reassigned_from") or [] if isinstance(item, Mapping)]
        if moved:
            relay["reassigned_from"] = [{"resident_id": item, **batch.member_view(item)} for item in moved if item]
        if members is not None and state in {"queued", "running"}:
            relay.update(input_progress(repository, member_id, group_id or member_message_id, state, members))
        result[message_id] = relay, round_["replies"]
    return result


def _facade_reply(
    facade_id: str,
    reply: Mapping[str, Any],
    relay: Mapping[str, Any],
    request_message_id: str,
) -> dict[str, Any]:
    projected = copy.deepcopy(dict(reply))
    # Interactive requests belong to the worker's execution, including their
    # answered/expired state. Project that state before changing authorship.
    resident_conversation_stream.get_repository()._tool_status_messages(
        [projected], resident_id=str(relay.get("resident_id") or ""),
    )
    for part in projected.get("content_parts") or []:
        if isinstance(part, dict) and part.get("type") == "async_questions":
            # The facade shows the member's set as the member has it: open
            # until the member's Conversation holds its answer.
            try:
                state = resident_conversation_stream.get_repository().question_set_state(
                    str(relay.get("resident_id") or ""), str(reply.get("message_id") or ""),
                )
            except ValueError:
                # A reply read by ID after its Conversation was cleared asks nothing any more.
                state = "expired"
            if state != "open":
                part["state"] = state
    origin = dict(projected.get("origin") or {})
    origin["sender"] = {"kind": "resident", "id": facade_id}
    projected.update(
        author_id=facade_id,
        origin=origin,
        reply_to_message_id=request_message_id,
        family_reply={
            "resident_id": str(relay.get("resident_id") or ""),
            "title": str(relay.get("title") or ""),
            "message_id": str(reply.get("message_id") or ""),
            "via": list(relay.get("via") or []),
            **({"reassigned_from": list(relay["reassigned_from"])} if relay.get("reassigned_from") else {}),
            "request_message_id": request_message_id,
        },
    )
    return projected


def _member_view(resident_id: str) -> dict[str, str]:
    from . import resident_record_domain

    record = resident_record_domain.find_resident(resident_id) or {}
    return {"title": str(record.get("title") or "")}


# -- quote replies (decision_c524c775e0be ``quote``, decision_44cf587c1721) -------------


def _quoted_owner(facade: Mapping[str, Any], quoted_id: str) -> str:
    """The member of this Family a quoted message belongs to; "" when it belongs to none.

    A request quoted from this facade - the sender's own included - belongs to
    no member: the member that took it may have been the wrong one, so the
    Router chooses again with the quoted text.
    """

    facade_id = resident_lineage.resident_id(facade)
    stored = resident_conversation_stream.get_repository().message(facade_id, quoted_id)
    if stored is None:
        from . import personal_history
        historical = personal_history.message(facade, quoted_id)
        if historical is not None:
            return historical["family_history"]["resident_id"]
        # A member reply shown here goes back to the member of this Family that took its request.
        found = relayed_reply(facade, quoted_id)
        return str(found["member_id"]) if found else ""
    return _outward_quote_owner(facade, stored)


def _outward_quote_owner(facade: Mapping[str, Any], stored: Mapping[str, Any]) -> str:
    from .outward_messages import identities_of

    initiator = (stored.get("origin") or {}).get("initiated_by") or {}
    if stored.get("role") != "agent" or not stored.get("recipients") or initiator.get("kind") != "resident":
        return ""
    # A message a member sent out through the facade goes back to the member
    # of this Family that holds its writer: the writer, or its inner facade.
    # The steward asks the owner for decisions the same way, and hears the answer (decision_15f7df737ed5).
    takers = family_member_ids(facade) | {family_steward_id(facade)} - {""}
    return next((item for item in identities_of(str(initiator.get("id") or "")) if item in takers), "")


def answers_steward(facade: Mapping[str, Any], message: Mapping[str, Any] | None) -> bool:
    """Whether a facade request quotes a message this Family's steward sent, and so goes to the steward."""

    steward_id = family_steward_id(facade)
    return bool(steward_id) and quoted_member(facade, message) == steward_id


def quoted_member(facade: Mapping[str, Any], message: Mapping[str, Any] | None) -> str:
    """The member a request that quotes a message goes to directly; "" when the Router chooses as usual.

    A quote of a member's reply or of a message a member sent out through the
    facade goes to the member that wrote it, without the Router's model
    (decision_44cf587c1721); a quote of a request, the sender's own included,
    is routed as usual.  A member that left the Family, was deleted or cannot
    read the request's images or files is passed over.
    """

    from management_common.principal import message_quote

    from . import resident_execution_provider
    from . import resident_record_domain

    if not isinstance(message, Mapping) or not is_facade(facade):
        return ""
    quoted_id = str(message_quote(message).get("message_id") or "")
    member_id = _quoted_owner(facade, quoted_id) if quoted_id else ""
    if not member_id:
        # Inner conversations may hold only the quote's excerpt. Resolve the
        # original outward record through accepted, persisted relay links,
        # never through caller-supplied text or an unverified business_ref.
        repository = resident_conversation_stream.get_repository()
        for holder, source_id in relay_sources_of(
                resident_lineage.resident_id(facade), str(message.get("message_id") or "")):
            original = repository.message(holder, source_id) or {}
            if (original.get("author_id") != message.get("author_id")
                    or original.get("content_parts") != message.get("content_parts")):
                break
            source_quote = str(message_quote(original).get("message_id") or "")
            outward = repository.message(holder, source_quote) if source_quote else None
            if outward:
                member_id = _outward_quote_owner(facade, outward)
                if member_id:
                    break
    if not member_id or (member_id not in family_member_ids(facade) and member_id != family_steward_id(facade)):
        return ""
    record = resident_record_domain.find_resident(member_id)
    if not record or record.get("deleted_at"):
        return ""
    capabilities = resident_execution_provider.provider_for(record).capabilities
    kinds = {
        str(part.get("type") or "") for part in message.get("content_parts") or [] if isinstance(part, Mapping)
    }
    if ("image" in kinds and not capabilities.supports_media) or ("file" in kinds and not capabilities.local_tools):
        return ""
    return member_id


def relay_quote(source: tuple[str, str] | None, member_id: str) -> dict[str, str]:
    """The quote a relayed copy carries: the facade message's, pointed at the member's own Conversation.

    Where the member's Conversation has the quoted message - the member's own
    reply, or its copy of the request it took - the copy quotes that message;
    otherwise it keeps only the author and the excerpt.
    """

    from management_common.principal import message_quote

    from .message_quotes import quote_of
    from . import resident_record_domain

    if source is None:
        return {}
    facade_id, facade_message_id = source
    stored = resident_conversation_stream.get_repository().message(facade_id, facade_message_id)
    quote = message_quote(stored) if stored else {}
    if not quote:
        return {}
    excerpt = {"author_id": quote["author_id"], "text": quote["text"]}
    quoted_id = quote.get("message_id", "")
    facade, member = resident_record_domain.find_resident(facade_id), resident_record_domain.find_resident(member_id)
    if not quoted_id or not facade or not member:
        return excerpt
    link = relay_links(facade).get(quoted_id) or {}
    if link.get("resident_id") == member_id and link.get("message_id"):
        quoted_id = str(link["message_id"])
    return quote_of(member, quoted_id) or excerpt
