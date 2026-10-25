"""Portal identity, preferences, conversations, and workspace HTTP endpoints."""

from __future__ import annotations

import re
from urllib.parse import parse_qs

from management_portal.resources.errors import resource_error_http_response

# Instructions /api/me answers for the current User (access.allows), so pages
# show account management, execution settings and scheduler forms only to
# those the permission system lets through.  Changing the administrator list is for
# Portal 管理员 (access.user_category).  UpdateExecutionSettings is asked for the
# permission switch alone and, with ActivatePermissionScheme, is always true for a
# Portal 管理员 (decision_b820faf42218 §3.1).
PAGE_INSTRUCTIONS = ("CreateUser", "GetExecutionSettings", "UpdateExecutionSettings", "ActivatePermissionScheme",
                     "UpdateProviderAccount", "OpenTerminal", "ListResourceTypes", "UpdateResourceType",
                     "ListProviders", "ListProviderAccounts", "RegisterProviderAccount",
                     "DeleteProviderAccount", "DeleteModelApi")
PAGE_ARGUMENTS = {"UpdateExecutionSettings": {"permission_system_enabled": True}}


class PortalSessionHttp:
    def __init__(
        self, *, respond, exception_response, read_json, session_actor, access, avatar, instance, build_id,
        release_manifest, preferences, set_preferences,
        conversation_payload, workspace_adapter, session_opened=None,
    ):
        self.respond = respond
        self.exception_response = exception_response
        self.read_json = read_json
        self.session_actor = session_actor
        self.access = access
        self.avatar = avatar
        self.instance = instance
        self.build_id = build_id
        self.release_manifest = release_manifest
        self.preferences = preferences
        self.set_preferences = set_preferences
        self.conversation_payload = conversation_payload
        self.workspace_adapter = workspace_adapter
        self.session_opened = session_opened or (lambda _actor: None)

    def me(self, request, _parsed, _actor):
        from management_portal import permission_system, portal_features
        from management_common.principal import actor_principal

        actor = self.session_actor(request) or ""
        principal = actor_principal(actor)
        is_user = principal.kind == "user"
        if actor and is_user:
            # Every page reads /api/me once; it is where an existing session catches up.
            self.session_opened(actor)
        access = self.access().user(actor) if is_user else {}
        if is_user:
            avatar = self.avatar(actor)
        else:
            from management_portal.resident_avatar import get_existing_avatar
            avatar = get_existing_avatar(actor)
        page_arguments = {name: PAGE_ARGUMENTS.get(name) for name in PAGE_INSTRUCTIONS}
        for _feature, instructions in portal_features.contributions("web.page_instructions"):
            page_arguments.update(instructions)
        return self.respond(request, 200, {
            "username": actor,
            "principal": {"kind": principal.kind, "id": actor},
            "avatar": avatar,
            "portal_instance": self.instance,
            "build_id": self.build_id,
            "built_at": str(self.release_manifest.get("built_at") or ""),
            "source_commit": str(self.release_manifest.get("source_commit") or ""),
            "preferences": self.preferences(actor),
            "access": {
                # Page flags, each the answer of the instruction behind it.
                "allows": {name: permission_system.allows(actor, name, arguments) for name, arguments in page_arguments.items()},
                "user_category": permission_system.user_category(actor) if is_user else "",
                # "open" while the permission system is off: credential and policy views hide.
                "permission_system": permission_system.mode(),
                "status": str(access.get("status") or "active"),
                "created_at": str(access.get("created_at") or ""),
            },
        })

    def router_preference(self, request, _parsed, _actor):
        actor = self.session_actor(request) or ""
        return self.respond(request, 200, {
            "ok": True, "username": actor,
            "default_instance": self.instance, "preference": self.instance,
            "effective_instance": self.instance,
            "instances": [{
                "id": self.instance, "label": self.instance.title(),
                "kind": self.instance, "build_id": self.build_id,
            }],
        })

    def preferences_get(self, request, _parsed, _actor):
        actor = self.session_actor(request) or ""
        return self.respond(request, 200, {"preferences": self.preferences(actor)})

    def preferences_post(self, request, _parsed, _actor):
        actor = self.session_actor(request) or ""
        payload = self.read_json(request)
        try:
            preferences = self.set_preferences(actor, payload)
        except PermissionError as exc:
            return self.respond(request, 403, {"error": str(exc)})
        except Exception as exc:
            return self.exception_response(request, exc)
        return self.respond(request, 200, {"preferences": preferences})

    def resident_invocations_diagnostics(self, request, parsed, _actor):
        from management_portal.resident_handoff import diagnose_lineage_invocations

        query = parse_qs(parsed.query)
        try:
            if set(query) != {"lineage_id"} or len(query["lineage_id"]) != 1:
                raise ValueError("One lineage_id is required")
            result = diagnose_lineage_invocations(
                actor=self.session_actor(request) or "",
                lineage_id=query["lineage_id"][0],
            )
            return self.respond(request, 200, result)
        except PermissionError as exc:
            return self.respond(request, 403, {"error": str(exc)})
        except (ValueError, KeyError) as exc:
            return self.respond(request, 400, {"error": str(exc)})

    def conversations_get(self, request, parsed, _actor):
        actor = self.session_actor(request) or ""
        query = parse_qs(parsed.query)
        resident_id = str((query.get("resident") or [""])[0]).strip()
        try:
            return self.respond(request, 200, self.conversation_payload(
                actor, resident_id=resident_id,
                page_size=int((query.get("page_size") or ["50"])[0]),
                page_token=str((query.get("page_token") or [""])[0]),
            ))
        except ValueError as exc:
            return self.respond(request, 400, {"error": str(exc)})
        except Exception as exc:
            return self.exception_response(request, exc)

    def chat_workspace_prepare(self, request, _parsed, _actor):
        actor = self.session_actor(request) or ""
        try:
            return self.respond(request, 200, self.workspace_adapter.prepare_workspace(
                actor, self.read_json(request)
            ))
        except PermissionError as exc:
            return self.respond(request, 403, {"error": str(exc)})
        except (ValueError, KeyError) as exc:
            return self.respond(request, 400, {"error": str(exc)})

    def search_diagnostics(self, request, parsed, _actor):
        from management_portal.shared_search_diagnostics import get_search_run_diagnostics

        qs = parse_qs(parsed.query, keep_blank_values=True)
        try:
            if set(qs) - {"search_id", "generation"} or any(len(values) != 1 for values in qs.values()):
                raise ValueError("only search_id and optional generation are accepted")
            generation = qs.get("generation", [None])[0]
            if generation is not None and not re.fullmatch(r"[1-9][0-9]*", generation):
                raise ValueError("generation must be a positive integer")
            return self.respond(request, 200, get_search_run_diagnostics(
                qs.get("search_id", [""])[0], actor=self.session_actor(request) or "",
                generation=int(generation) if generation is not None else None,
            ))
        except Exception as exc:
            status, payload = resource_error_http_response(exc)
            return self.respond(request, status, {"error": "Search diagnostics unavailable", "code": payload["code"]})
