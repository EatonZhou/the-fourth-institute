"""Pre-authentication invitation redemption; account operations live in User ISA."""
from pathlib import Path

from management_portal import (
    http_auth, http_documents, http_instance, http_request_body, http_responses, web_accounts,
)
from management_portal.http_routes import RouteDefinition


def setup_status(request, parsed, actor):
    """Read the enrollment authority; never create or return an invitation."""
    return http_responses._serve_json(request, 200, {
        "instance": http_instance._PORTAL_INSTANCE,
        "configured": web_accounts._USER_ENROLLMENT.has_accounts(),
    })


def join_page(request, parsed, actor):
    return http_documents._serve_bytes(request, 200, Path(__file__).with_name("join.html").read_bytes(),
                                       "text/html; charset=utf-8", extra_headers={"Referrer-Policy": "no-referrer"})


def _same_origin_json(request):
    # JSON prevents cross-origin form submissions; non-browser clients have no Origin.
    return (request.headers.get("Content-Type", "").split(";")[0].strip().lower() == "application/json"
            and not (request.headers.get("Origin") and not http_auth._same_origin_request(request)))


def preview_invitation(request, parsed, actor):
    if not _same_origin_json(request):
        return http_responses._serve_json(request, 403, {"error": "Same-origin JSON request required"})
    try:
        payload = http_request_body._read_bounded_json_object(request, max_bytes=1024)
        if set(payload) != {"invitation_secret"}:
            raise ValueError("Only invitation_secret is accepted")
        return http_responses._serve_json(request, 200, web_accounts._USER_ENROLLMENT.preview_invitation(payload["invitation_secret"]))
    except PermissionError:
        return http_responses._serve_json(request, 400, {"error": "Invitation is invalid, expired or already used"})
    except (TypeError, ValueError) as exc:
        return http_responses._serve_json(request, 400, {"error": str(exc)})


def accept_invitation(request, parsed, actor):
    if not _same_origin_json(request):
        return http_responses._serve_json(request, 403, {"error": "Same-origin JSON request required"})
    try:
        payload = http_request_body._read_bounded_json_object(request, max_bytes=8192)
        unknown = set(payload) - {"invitation_secret", "password", "secret_sha256", "token_name", "expires_in_days"}
        if unknown:
            raise ValueError("Unsupported registration fields")
        result = web_accounts._USER_ENROLLMENT.accept_invitation(**payload)
        username = result["user"]["username"]
        response = {"username": username, "credential": result["credential"]}
        if result.get("first_admin"):
            # The first account is made a Portal 管理员 in its own transaction (UserEnrollment.create_account).
            web_accounts._PORTAL_ACCESS.audit(username, "first_admin_granted", target=username)
            response["administrator"] = {"granted": True}
        from .personal_ama import provision_account

        ama = provision_account(username)
        # The new User's first conversation partner, so an agent need not search for it.
        response["personal_ama"] = {key: ama[key] for key in ("lineage_id", "resident_id", "error") if ama.get(key)}
        return http_responses._serve_json(request, 201, response)
    except (PermissionError, FileExistsError):
        return http_responses._serve_json(request, 400, {"error": "Invitation is invalid, expired or already used"})
    except (TypeError, ValueError) as exc:
        return http_responses._serve_json(request, 400, {"error": str(exc)})


def routes() -> tuple[RouteDefinition, ...]:
    """The join page and invitation redemption."""
    return (
        RouteDefinition("GET", "/join", join_page, "portal-public-get", phase="public"),
        RouteDefinition("GET", "/api/auth/setup-status", setup_status, "portal-public-get", phase="public"),
        RouteDefinition("POST", "/api/auth/invitations/accept", accept_invitation, "portal-public-post", phase="public"),
        RouteDefinition("POST", "/api/auth/invitations/preview", preview_invitation, "portal-public-post", phase="public"),
    )
