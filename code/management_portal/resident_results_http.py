"""Project results: an authenticated view of the deliverable resource owner."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import parse_qs

from .http_routes import RouteDefinition

PAGE = Path(__file__).with_name("resident_results.html")
RESOURCE_ROUTES = {"deliverable": ("/results", "id")}
NAVIGATION = ({"id": "results", "label": "成果", "href": "/results", "order": 25},)


def _operation(actor, identifier, action, value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{8,128}", value):
        raise ValueError("需要稳定的 operation_id，重试时使用原值")
    key = json.dumps([actor, identifier, action, value], ensure_ascii=False)
    return "deliverable:" + hashlib.sha256(key.encode()).hexdigest()


def contact_author(store, actor, identifier, payload, *, call=None):
    """Send only after an explicit request, with the original actor and revision."""
    if set(payload) != {"revision", "message", "operation_id"}:
        raise ValueError("联系作者需要 revision、message 和 operation_id")
    message = payload["message"]
    if not isinstance(message, str) or not message.strip() or len(message) > 8000:
        raise ValueError("请填写不超过 8000 字的消息")
    revision = payload["revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ValueError("revision 必须是正整数")
    result = store.get(identifier, actor, revision)
    record = result["record"]
    author = record["author_id"]
    if not author:
        raise ValueError("成果作者缺失")
    address = f"{identifier}/revision/{revision}"
    key = _operation(actor, address, "contact", payload["operation_id"])
    from . import isa_client
    call = call or isa_client.call
    # DeliverMessage owns recipient authorization, identity and acceptance.
    arguments = {"resident_id" if author.startswith("resident_") else "user_id": author,
                 "business_ref": address,
                 "message": {"content_parts": [{"type": "text", "text":
                     f"关于成果 {address} 的交流：\n{message.strip()}"}]}}
    return call(actor, "DeliverMessage", arguments, key=key)


def routes(portal):
    from . import http_documents, http_responses, isa_client
    from .http_request_body import _read_request_json
    from .resources.deliverables import DeliverableStore
    from .resources.errors import ResourceVersionConflict, ResourceOperationError
    from .hatg.contracts import InstructionError

    def response(request, fn):
        try:
            value = fn()
        except PermissionError as exc:
            return http_responses._serve_json(request, 403, {"error": str(exc)})
        except (KeyError, FileNotFoundError) as exc:
            return http_responses._serve_json(request, 404, {"error": str(exc)})
        except ResourceVersionConflict as exc:
            return http_responses._serve_json(request, 409, {"error": str(exc)})
        except ResourceOperationError as exc:
            return http_responses._serve_json(request, exc.http_status, {"error": str(exc), "code": exc.code})
        except InstructionError as exc:
            status = 403 if exc.code == "instruction.forbidden" else 409 if "conflict" in exc.code else 400
            return http_responses._serve_json(request, status, {"error": str(exc), "code": exc.code})
        except (ValueError, TypeError) as exc:
            return http_responses._serve_json(request, 400, {"error": str(exc)})
        return http_responses._serve_json(request, 200, value)

    def page(request, parsed, actor):
        return http_documents._serve_authenticated_document(request, 200, PAGE.read_bytes())

    def listing(request, parsed, actor):
        def read():
            query = parse_qs(parsed.query)
            return DeliverableStore().list(actor, project_id=query.get("project_id", [""])[0],
                author_id=query.get("author_id", [""])[0], offset=int(query.get("offset", ["0"])[0]))
        return response(request, read)

    def detail(request, parsed, actor, params):
        return response(request, lambda: DeliverableStore().get(params["id"], actor,
            parse_qs(parsed.query).get("revision", [None])[0]))

    def create(request, parsed, actor):
        def write():
            body = _read_request_json(request)
            if set(body) != {"payload", "operation_id"} or not isinstance(body["payload"], dict):
                raise ValueError("需要 payload 与 operation_id")
            return isa_client.call(actor, "RegisterResource", {"resource_type": "deliverable", "fields": body["payload"]},
                key=_operation(actor, "", "create", body["operation_id"]))
        return response(request, write)

    def update(request, parsed, actor, params):
        def write():
            body = _read_request_json(request)
            if set(body) != {"payload", "expected_revision"} or not isinstance(body["payload"], dict):
                raise ValueError("需要 payload 与 expected_revision")
            if "expected_revision" in body["payload"]:
                raise ValueError("expected_revision 只接受顶层字段")
            return isa_client.call(actor, "UpdateResource", {"resource_id": params["id"],
                "changes": {**body["payload"], "expected_revision": body["expected_revision"]}})
        return response(request, write)

    def contact(request, parsed, actor, params):
        return response(request, lambda: contact_author(DeliverableStore(), actor, params["id"], _read_request_json(request)))

    return (RouteDefinition("GET", "/results", page, "resident-results", page=True),
            RouteDefinition("GET", "/api/results", listing, "resident-results"),
            RouteDefinition("GET", "/api/results/{id}", detail, "resident-results", match="template"),
            RouteDefinition("POST", "/api/results", create, "resident-results"),
            RouteDefinition("POST", "/api/results/{id}/update", update, "resident-results", match="template"),
            RouteDefinition("POST", "/api/results/{id}/contact", contact, "resident-results", match="template"))
