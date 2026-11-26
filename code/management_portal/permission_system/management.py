"""Permission instructions that ask the PAR engine (decision_22227d0ad075).

Scheme, node and entry instructions are answered in ``schemes.py``.  What
is left here needs decisions under the active scheme: a principal's
effective permissions, a check, and temporary credentials; they answer the
same with the permission system on or off.  With it on, the active scheme
decides who may call them (its instruction map): seeing another principal's
permissions is its requirement on ``principal_id``.  Only the issuer or the
recipient revokes a credential, with the system on or off.

Analysis beyond a single decision is an interface only for now: ``explain``
and ``reach`` (decision_b820faf42218 §3.7).
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from . import _principal, temporary
from .requests import RecordNotFound, Unresolved
from .schemes import text as _text


class Management:
    """The instruction surface; ``permissions`` is the running ``PARPermissions``.

    No handler here asks the engine about the caller, so it answers the same
    with the permission system on or off.
    """

    def __init__(self, permissions, *, derive_arguments=None) -> None:
        self.permissions = permissions
        self.derive_arguments = derive_arguments

    def effective(self, actor: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        principal = _text(arguments, "principal_id", required=True)
        scheme, engine, _instance = self.permissions.current()
        path, grants, _paths = engine.grants(principal)
        now = engine.clock()
        coverage = {}
        for node in scheme.trees["A"].nodes:
            if node.startswith("@"):
                reached = grants.coverage(engine.a_path(node), now)
                coverage[node] = {"permit": sorted(reached.permit), "deny": sorted(reached.deny)}
        result = {"principal_id": principal, "scheme_id": scheme.scheme_id, "path": list(path), "coverage": coverage,
                  "entries": [entry.to_dict() for entry in grants.entries if entry.live(now)],
                  "temporary": temporary.listing(engine.store, principal)}
        if getattr(_principal(principal), "kind", "") == "user":
            from .categories import categories

            result["user_category"] = categories().of(principal)
        owner = engine.represents(principal) if engine.represents is not None else ""
        if owner:
            # A personal AMA's Resident holds its owner's rows too; ``path`` lists both.
            result["represents"] = owner
        return result

    def _check(self, actor: str, arguments: Mapping[str, Any], *, instruction_mode: bool) -> dict[str, Any]:
        principal = _text(arguments, "principal_id", required=True)
        scheme, _engine, _instance = self.permissions.current()
        if instruction_mode:
            instruction = _text(arguments, "instruction", required=True)
            call = arguments.get("arguments") or {}
            if not isinstance(call, Mapping):
                raise ValueError("arguments 必须是对象")
            from management_portal.application_tool_contracts import permission_declaration
            # A public check takes the same public arguments as the operation.
            # Internal owner-derived requirement vectors are never caller input.
            permission_declaration(instruction).validate_arguments(call)
            if self.derive_arguments is not None:
                call = self.derive_arguments(instruction, dict(call), actor=actor, principal_id=principal)
                if not isinstance(call, Mapping):
                    raise ValueError("领域权限参数读取必须返回对象")
            from .administrators import passes

            if passes(principal, instruction, call):
                # The Portal 管理员 guarantee answers before the scheme (decision_b820faf42218 §3.1).
                return {"principal_id": principal, "scheme_id": scheme.scheme_id, "allowed": True, "requests": [],
                        "portal_admin": True}
            try:
                items = [(request.action, request.resource)
                         for request in self.permissions._requests(scheme, instruction, call, caller=principal)]
            except Unresolved as exc:
                raise ValueError(str(exc)) from exc
            except RecordNotFound as exc:
                raise KeyError(str(exc)) from exc
        else:
            a = _text(arguments, "a", required=True)
            if a not in scheme.trees["A"].nodes:
                raise ValueError(f"{a} 不是 A 树的节点")
            items = [(a, _text(arguments, "resource_id", required=True))]
        scheme, decisions = self.permissions._decide(principal, items)
        results = []
        for (action, resource), decision in zip(items, decisions):
            item = {"action": action, "resource": resource, "allowed": decision.allowed, "reason": decision.reason}
            if decision.permit is not None:
                item["permit"] = decision.permit.to_dict()
            if decision.deny is not None:
                item["deny"] = decision.deny.to_dict()
            results.append(item)
        return {"principal_id": principal, "scheme_id": scheme.scheme_id,
                "allowed": all(item["allowed"] for item in results), "requests": results}

    def check(self, actor: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if "instruction" in arguments or "arguments" in arguments:
            raise ValueError("指令检查请用 CheckInstructionPermission")
        return self._check(actor, arguments, instruction_mode=False)

    def check_instruction(self, actor: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if "a" in arguments or "resource_id" in arguments:
            raise ValueError("A/资源检查请用 CheckPermission")
        return self._check(actor, arguments, instruction_mode=True)

    # -- temporary credentials --------------------------------------------------------------

    def issue_temporary(self, actor: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        scheme, engine, _instance = self.permissions.current()
        if _text(arguments, "message_id"):
            return temporary.issue_prepared(engine, scheme, issuer=actor,
                recipient=_text(arguments, "principal_id", required=True), a=_text(arguments, "a", required=True),
                resource=_text(arguments, "resource_id", required=True), seconds=arguments.get("expires_in_seconds"),
                message_id=_text(arguments, "message_id"), input_prepared=input_prepared)
        return temporary.issue(engine, scheme, issuer=actor, recipient=_text(arguments, "principal_id", required=True),
                               a=_text(arguments, "a", required=True),
                               resource=_text(arguments, "resource_id", required=True),
                               seconds=arguments.get("expires_in_seconds"), message_id=_text(arguments, "message_id"),
                               input_open=input_open)

    def renew_temporary(self, actor: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        _scheme, engine, _instance = self.permissions.current()
        return temporary.renew(engine.store, _text(arguments, "credential_id", required=True), actor=actor,
                               seconds=arguments.get("expires_in_seconds"))

    def revoke_temporary(self, actor: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        _scheme, engine, _instance = self.permissions.current()
        return temporary.revoke(engine.store, _text(arguments, "credential_id", required=True), actor=actor)

    # -- analysis (interface only) ----------------------------------------------------------

    def explain(self, actor: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Why a request is refused, across every entry that touches it.  Not implemented yet."""
        raise NotImplementedError("拒绝解释尚未实现")

    def reach(self, actor: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """What a principal can do through everyone it may call (decision_b820faf42218 §2).  Not implemented yet."""
        raise NotImplementedError("可达性审计尚未实现")


def input_open(resident_id: str, message_id: str) -> bool:
    """Whether the Resident's input of the message is still open; only SocietyService, which holds inputs, can say."""
    from management_portal.society_service import get_society_service

    return get_society_service().resident.is_resident_input_open(resident_id=resident_id, message_id=message_id) is True


def input_prepared(resident_id: str, message_id: str, issuer: str) -> bool:
    """Society proves the actual issuer and prepared-only state under our message lock."""
    from management_portal.society_service import get_society_service

    return get_society_service().resident.is_resident_input_open_for_issuer(
        resident_id=resident_id, message_id=message_id, issuer=issuer) is True


ACTIONS = {
    "GetEffectivePermissions": "effective",
    "CheckPermission": "check",
    "CreateTemporaryCredential": "issue_temporary",
    "UpdateTemporaryCredential": "renew_temporary",
    "RevokeTemporaryCredential": "revoke_temporary",
    "CheckInstructionPermission": "check_instruction",
}
