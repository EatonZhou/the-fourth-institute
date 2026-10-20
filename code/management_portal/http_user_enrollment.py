"""Pre-authentication invitation redemption; account operations live in User ISA."""
from pathlib import Path


def join_page(request, parsed, actor):
    from . import serve_management_portal as portal

    return portal._serve_bytes(request, 200, Path(__file__).with_name("join.html").read_bytes(),
                               "text/html; charset=utf-8", extra_headers={"Referrer-Policy": "no-referrer"})


def _same_origin_json(request, portal):
    # JSON prevents cross-origin form submissions; non-browser clients have no Origin.
    return (request.headers.get("Content-Type", "").split(";")[0].strip().lower() == "application/json"
            and not (request.headers.get("Origin") and not portal._same_origin_request(request)))


def preview_invitation(request, parsed, actor):
    from . import serve_management_portal as portal

    if not _same_origin_json(request, portal):
        return portal._serve_json(request, 403, {"error": "Same-origin JSON request required"})
    try:
        payload = portal._read_bounded_json_object(request, max_bytes=1024)
        if set(payload) != {"invitation_secret"}:
            raise ValueError("Only invitation_secret is accepted")
        return portal._serve_json(request, 200, portal._USER_ENROLLMENT.preview_invitation(payload["invitation_secret"]))
    except PermissionError:
        return portal._serve_json(request, 400, {"error": "Invitation is invalid, expired or already used"})
    except (TypeError, ValueError) as exc:
        return portal._serve_json(request, 400, {"error": str(exc)})


def accept_invitation(request, parsed, actor):
    from . import serve_management_portal as portal

    if not _same_origin_json(request, portal):
        return portal._serve_json(request, 403, {"error": "Same-origin JSON request required"})
    try:
        payload = portal._read_bounded_json_object(request, max_bytes=8192)
        unknown = set(payload) - {"invitation_secret", "password", "secret_sha256", "token_name", "expires_in_days"}
        if unknown:
            raise ValueError("Unsupported registration fields")
        result = portal._USER_ENROLLMENT.accept_invitation(**payload)
        username = result["user"]["username"]
        response = {"username": username, "credential": result["credential"]}
        if result.get("first_admin"):
            # The first account is made a Portal 管理员 in its own transaction (UserEnrollment.create_account).
            portal._PORTAL_ACCESS.audit(username, "first_admin_granted", target=username)
            response["administrator"] = {"granted": True}
        from .personal_ama import provision_account

        ama = provision_account(username)
        # The new User's first conversation partner, so an agent need not search for it.
        response["personal_ama"] = {key: ama[key] for key in ("lineage_id", "resident_id", "error") if ama.get(key)}
        return portal._serve_json(request, 201, response)
    except (PermissionError, FileExistsError):
        return portal._serve_json(request, 400, {"error": "Invitation is invalid, expired or already used"})
    except (TypeError, ValueError) as exc:
        return portal._serve_json(request, 400, {"error": str(exc)})
