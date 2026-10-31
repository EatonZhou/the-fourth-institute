from __future__ import annotations
from management_common.format_tags import schema_matches

from collections.abc import Mapping
from dataclasses import dataclass
import re


RESIDENT_ID = re.compile(r"resident_[0-9a-f]{12}\Z")
USER_ID = re.compile(r"[A-Za-z0-9_.@+-]{1,80}\Z")
SERVICE_IDS = frozenset({"portal", "ai-socket-relay"})
# Message senders that are not Principals keep their IDs out of User names:
# a User called "mailbox" would read as the author of Mailbox group messages
# (decision_15f7df737ed5).
NON_PRINCIPAL_SENDER_IDS = frozenset({"mailbox"})


def validate_user_id(value: object) -> str:
    identifier = str(value or "").strip()
    if (
        not USER_ID.fullmatch(identifier)
        or identifier.startswith("@")
        or identifier.casefold().startswith("resident_")
        or identifier.casefold() in SERVICE_IDS
        or identifier.casefold() in NON_PRINCIPAL_SENDER_IDS
    ):
        raise ValueError("invalid or reserved User principal ID")
    return identifier


@dataclass(frozen=True)
class Principal:
    kind: str
    id: str

    def __post_init__(self) -> None:
        identifier = str(self.id or "").strip()
        if self.kind == "user":
            validate_user_id(identifier)
        elif self.kind == "resident":
            if not RESIDENT_ID.fullmatch(identifier):
                raise ValueError("invalid Resident principal ID")
        elif self.kind == "service":
            if identifier not in SERVICE_IDS:
                raise ValueError("unregistered service identity")
        else:
            raise ValueError("unsupported principal kind")
        object.__setattr__(self, "id", identifier)

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "id": self.id}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Principal:
        if not isinstance(value, Mapping) or set(value) != {"kind", "id"}:
            raise ValueError("principal requires kind and id")
        return cls(kind=str(value["kind"]), id=str(value["id"]))


def actor_principal(actor: object) -> Principal:
    if isinstance(actor, Principal):
        return actor
    identifier = str(actor or "").strip()
    if RESIDENT_ID.fullmatch(identifier):
        return Principal("resident", identifier)
    if identifier in SERVICE_IDS:
        return Principal("service", identifier)
    return Principal("user", identifier)


def is_user_or_resident(actor: object) -> bool:
    """A User or a Resident acts here, not a Portal service; an invalid ID raises ``ValueError``."""

    return bool(actor) and actor_principal(actor).kind in {"user", "resident"}


def authenticated_principal(auth: Mapping[str, object]) -> Principal:
    kind = {"human": "user", "user": "user", "agent": "resident"}.get(str(auth.get("type") or ""))
    if kind is None:
        raise PermissionError("unsupported authenticated principal type")
    try:
        return Principal(kind, str(auth.get("id") or ""))
    except ValueError as exc:
        raise PermissionError("authenticated principal namespace mismatch") from exc


def message_origin(
    sender: object,
    *,
    initiated_by: object = "",
    producer_service: str = "portal",
) -> dict[str, object]:
    principal = actor_principal(sender)
    origin: dict[str, object] = {
        "schema": "tfi.message-origin.v1",
        "sender": principal.to_dict(),
        "producer": Principal("service", producer_service).to_dict(),
    }
    if initiated_by:
        origin["initiated_by"] = actor_principal(initiated_by).to_dict()
    return origin


MAILBOX_SENDER = {"kind": "mailbox", "id": "mailbox"}
MAILBOX_ORIGIN_SCHEMA = "tfi.message-origin.v2"


def mailbox_origin(initiated_by: object, *, producer_service: str = "portal") -> dict[str, object]:
    """Origin of a Mailbox group message (decision_15f7df737ed5).

    The Mailbox is only a kind of message sender: it is never a Principal,
    cannot sign in, own anything or call instructions.  ``initiated_by`` is
    the authenticated organizer who merged the messages.
    """

    return {
        "schema": MAILBOX_ORIGIN_SCHEMA,
        "sender": dict(MAILBOX_SENDER),
        "producer": Principal("service", producer_service).to_dict(),
        "initiated_by": actor_principal(initiated_by).to_dict(),
    }


def _validate_mailbox_origin(value: Mapping[str, object]) -> dict[str, object]:
    if set(value) != {"schema", "sender", "producer", "initiated_by"}:
        raise ValueError("a Mailbox origin needs sender, producer and initiated_by")
    if dict(value.get("sender") or {}) != MAILBOX_SENDER:
        raise ValueError("message-origin.v2 is only for the Mailbox sender")
    producer = Principal.from_dict(value.get("producer") or {})
    if producer.kind != "service":
        raise ValueError("message producer must be a service identity")
    initiated_by = Principal.from_dict(value.get("initiated_by") or {})
    return {
        "schema": MAILBOX_ORIGIN_SCHEMA,
        "sender": dict(MAILBOX_SENDER),
        "producer": producer.to_dict(),
        "initiated_by": initiated_by.to_dict(),
    }


def validate_message_origin(value: Mapping[str, object]) -> dict[str, object]:
    if schema_matches(value.get("schema"), "tfi.message-origin.v2"):
        return _validate_mailbox_origin(value)
    if not schema_matches(value.get("schema"), "tfi.message-origin.v1"):
        raise ValueError("invalid message origin schema")
    if set(value) - {"schema", "sender", "producer", "initiated_by"}:
        raise ValueError("unsupported message origin fields")
    result: dict[str, object] = {"schema": value["schema"]}
    for key in ("sender", "producer", "initiated_by"):
        if key == "initiated_by" and key not in value:
            continue
        raw = value.get(key)
        if not isinstance(raw, Mapping):
            raise ValueError(f"message origin requires {key}")
        principal = Principal.from_dict(raw)
        if key == "producer" and principal.kind != "service":
            raise ValueError("message producer must be a service identity")
        result[key] = principal.to_dict()
    return result


def message_attribution(message: Mapping[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    author_id = str(message.get("author_id") or "").strip()
    raw = message.get("origin")
    if raw is not None:
        if not isinstance(raw, Mapping):
            raise ValueError("message origin must be an object")
        origin = validate_message_origin(raw)
        sender_id = origin["sender"]["id"]
        if author_id and author_id != sender_id:
            raise ValueError("message author disagrees with authenticated sender")
        author_id = sender_id
        result["origin"] = origin
    if author_id:
        result["author_id"] = author_id
    return result


MAX_MESSAGE_RECIPIENTS = 20


def message_recipients(message: Mapping[str, object]) -> list[dict[str, str]]:
    """Whom a message is addressed to; empty when it names no one (decision_c524c775e0be).

    Only a message a Resident sends to a user, and one a Family sends out as
    its facade, names its recipients; each is a User or a Resident, once.
    """

    raw = message.get("recipients")
    if raw is None or raw == []:
        return []
    if not isinstance(raw, (list, tuple)) or len(raw) > MAX_MESSAGE_RECIPIENTS:
        raise ValueError(f"message recipients must be a list of at most {MAX_MESSAGE_RECIPIENTS}")
    recipients: list[dict[str, str]] = []
    for item in raw:
        principal = Principal.from_dict(item if isinstance(item, Mapping) else {})
        if principal.kind not in {"user", "resident"}:
            raise ValueError("a message recipient is a User or a Resident")
        if principal.to_dict() in recipients:
            raise ValueError("a message names each recipient once")
        recipients.append(principal.to_dict())
    return recipients


# A quote carries at most this much of the quoted message's text, from its start.
MAX_QUOTE_TEXT = 1000
QUOTE_FIELDS = ("message_id", "author_id", "text")


def message_quote(message: Mapping[str, object]) -> dict[str, str]:
    """The message a user message quotes; empty when it quotes none (decision_c524c775e0be ``quote``).

    The platform fills in the author and the opening text when it accepts the
    message, and they never change.  A relayed copy names the quoted message
    only where the member's own Conversation has it; otherwise it keeps the
    author and the text alone.
    """

    raw = message.get("quote")
    if raw is None or raw == {}:
        return {}
    if not isinstance(raw, Mapping) or set(raw) - set(QUOTE_FIELDS):
        raise ValueError("a message quote has only message_id, author_id and text")
    message_id = str(raw.get("message_id") or "").strip()
    author_id = str(raw.get("author_id") or "").strip()
    text = str(raw.get("text") or "")
    if len(message_id) > 120 or any(character.isspace() for character in message_id):
        raise ValueError("invalid quoted message ID")
    if not author_id or len(author_id) > 200 or not text.strip() or len(text) > MAX_QUOTE_TEXT:
        raise ValueError(f"a message quote names its author and up to {MAX_QUOTE_TEXT} characters of text")
    quote = {"message_id": message_id} if message_id else {}
    quote.update(author_id=author_id, text=text)
    return quote


@dataclass(frozen=True)
class OperationIdentity:
    principal: Principal
    initiated_by: Principal
    producer: Principal = Principal("service", "portal")

    def __post_init__(self) -> None:
        if self.producer.kind != "service":
            raise ValueError("operation producer must be a service identity")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "tfi.operation-identity.v1",
            "principal": self.principal.to_dict(),
            "initiated_by": self.initiated_by.to_dict(),
            "producer": self.producer.to_dict(),
        }

    @classmethod
    def for_actor(cls, actor: object, *, initiated_by: object = "") -> OperationIdentity:
        principal = actor_principal(actor)
        return cls(principal, actor_principal(initiated_by) if initiated_by else principal)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> OperationIdentity:
        if not isinstance(value, Mapping) or set(value) != {"schema", "principal", "initiated_by", "producer"}:
            raise ValueError("invalid operation identity fields")
        if not schema_matches(value["schema"], "tfi.operation-identity.v1"):
            raise ValueError("invalid operation identity schema")
        principals = {}
        for key in ("principal", "initiated_by", "producer"):
            if not isinstance(value[key], Mapping):
                raise ValueError(f"operation identity requires {key}")
            principals[key] = Principal.from_dict(value[key])
        return cls(**principals)


def execution_identity(agent_id: str, requested_by: str) -> OperationIdentity:
    executor = Principal("resident", agent_id) if RESIDENT_ID.fullmatch(agent_id) else Principal("service", "portal")
    return OperationIdentity(executor, actor_principal(requested_by))
