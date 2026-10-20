"""Device terminal HTTP routes."""

from urllib.parse import parse_qs

from management_portal import local_terminal_backend


class TerminalHttp:
    def __init__(self, *, session_actor, read_json, respond, exception_response):
        self.session_actor = session_actor
        self.read_json = read_json
        self.respond = respond
        self.exception_response = exception_response

    def read(self, request, parsed, _actor):
        qs = parse_qs(parsed.query)
        session_id = str((qs.get("session_id") or [""])[0]).strip()
        cursor = str((qs.get("cursor") or ["0"])[0]).strip()
        actor = self.session_actor(request) or ""
        try:
            return self.respond(request, 200, local_terminal_backend.read_terminal(actor, session_id, cursor))
        except KeyError as exc:
            return self.respond(request, 404, {"error": str(exc)})
        except Exception as exc:
            return self.exception_response(request, exc)

    def start(self, request, _parsed, _actor):
        payload = self.read_json(request)
        actor = self.session_actor(request) or ""
        try:
            device_id = str(payload.get("device_id") or "")
            if not device_id:
                # This route only reaches Devices; the host shell has its own route.
                raise KeyError("device_id is required")
            result = local_terminal_backend.open_terminal(
                actor,
                device_id=device_id,
                session_id=str(payload.get("session_id") or ""),
                cols=payload.get("cols"),
                rows=payload.get("rows"),
            )
            return self.respond(request, 200, result)
        except KeyError as exc:
            return self.respond(request, 404, {"error": str(exc)})
        except PermissionError as exc:
            return self.respond(request, 403, {"error": str(exc)})
        except ValueError as exc:
            return self.respond(request, 400, {"error": str(exc)})
        except Exception as exc:
            return self.exception_response(request, exc)

    def input(self, request, _parsed, _actor):
        return self._io(request, "input")

    def control(self, request, _parsed, _actor):
        return self._io(request, "control")

    def _io(self, request, operation):
        payload = self.read_json(request)
        actor = self.session_actor(request) or ""
        try:
            if operation == "input":
                result = local_terminal_backend.write_terminal(
                    actor,
                    str(payload.get("session_id") or ""),
                    str(payload.get("input") or ""),
                )
            else:
                result = local_terminal_backend.control_terminal(
                    actor,
                    str(payload.get("session_id") or ""),
                    str(payload.get("action") or ""),
                    payload.get("cols"),
                    payload.get("rows"),
                    payload.get("cursor"),
                    payload.get("cwd"),
                )
            return self.respond(request, 200, result)
        except KeyError as exc:
            return self.respond(request, 404, {"error": str(exc)})
        except PermissionError as exc:
            return self.respond(request, 403, {"error": str(exc)})
        except ValueError as exc:
            return self.respond(request, 400, {"error": str(exc)})
        except Exception as exc:
            return self.exception_response(request, exc)
