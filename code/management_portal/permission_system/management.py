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

    def __init__(self, permissions) -> None:
        self.permissions = permissions

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
        return result

    def check(self, actor: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        principal = _text(arguments, "principal_id", required=True)
        scheme, _engine, _instance = self.permissions.current()
        instruction = _text(arguments, "instruction")
        if instruction:
            call = arguments.get("arguments") or {}
            if not isinstance(call, Mapping):
                raise ValueError("arguments 必须是对象")
            from .administrators import passes

            if passes(principal, instruction, call):
                # The Portal 管理员 guarantee answers before the scheme (decision_b820faf42218 §3.1).
                return {"principal_id": principal, "scheme_id": scheme.scheme_id, "allowed": True, "requests": [],
                        "portal_admin": True}
            try:
                items = [(request.action, request.resource)
                         for request in self.permissions._requests(scheme, instruction, call)]
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

    # -- temporary credentials --------------------------------------------------------------

    def issue_temporary(self, actor: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        scheme, engine, _instance = self.permissions.current()
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

    def list_temporary(self, actor: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        principal = _text(arguments, "principal_id", required=True)
        _scheme, engine, _instance = self.permissions.current()
        return {"credentials": temporary.listing(engine.store, principal)}

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


ACTIONS = {
    "GetEffectivePermissions": "effective",
    "CheckPermission": "check",
    "IssueTemporaryCredential": "issue_temporary",
    "RenewTemporaryCredential": "renew_temporary",
    "RevokeTemporaryCredential": "revoke_temporary",
    "ListTemporaryCredentials": "list_temporary",
}
