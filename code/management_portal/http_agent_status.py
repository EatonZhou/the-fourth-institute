"""HTTP request handling for Agent Status and account resident controls."""

from __future__ import annotations

from urllib.parse import parse_qs

from management_portal import agent_status_backend


class AgentStatusHttp:
    def __init__(
        self, *, respond, unavailable, resident_api,
        cached_summary, cached_active, cached_scheduled_triggered,
        invalidate_scheduled_triggered, ):
        self.respond = respond
        self.unavailable = unavailable
        self.resident_api = resident_api
        self.cached_summary = cached_summary
        self.cached_active = cached_active
        self.cached_scheduled_triggered = cached_scheduled_triggered
        self.invalidate_scheduled_triggered = invalidate_scheduled_triggered

    def account_residents(self, request, parsed, actor):
        account_id = str((parse_qs(parsed.query).get("account_id") or [""])[0])
        try:
            result = self.resident_api.get_account_resident_summary(account_id, actor=actor)
        except PermissionError as exc:
            return self.respond(request, 403, {"error": str(exc)})
        except (KeyError, ValueError) as exc:
            return self.respond(request, 400, {"error": str(exc)})
        except Exception:
            return self.unavailable(request)
        return self.respond(request, 200, result)

    def summary(self, request, _parsed, actor):
        return self.respond(request, 200, self.cached_summary(actor))

    def usage(self, request, _parsed, actor):
        return self.respond(request, 200, agent_status_backend.get_token_usage_summary(actor))

    def active_agents(self, request, _parsed, actor):
        return self.respond(request, 200, self.cached_active(actor))

    def scheduled_triggered(self, request, parsed, actor):
        if (parse_qs(parsed.query).get("refresh") or [""])[0] == "1":
            self.invalidate_scheduled_triggered(actor)
        return self.respond(request, 200, self.cached_scheduled_triggered(actor))

    def refresh(self, request, _parsed, actor):
        return self.respond(request, 200, agent_status_backend.get_agent_status_summary(actor, force_refresh=True))

    def refresh_usage(self, request, _parsed, actor):
        return self.respond(request, 200, agent_status_backend.get_token_usage_summary(actor, force_refresh=True))

