"""Public login and password reset HTTP routes."""

import sys
import time

from management_portal.http_routes import RouteDefinition


class PublicAuthHttp:
    def __init__(
        self, *, same_origin, empty, respond, bounded_json, bounded_form,
        password_reset_user, password_resets, access, credentials,
        request_client_key, load_users, user_allowed, verify_credentials,
        two_factor, start_login_challenge, issue_session_token,
        set_login_challenge_cookie, set_session_cookie, request_cookie,
        login_challenge_cookie, verify_login_challenge,
        clear_login_challenge_cookie, login_challenge_username,
    ):
        self.same_origin = same_origin
        self.empty = empty
        self.respond = respond
        self.bounded_json = bounded_json
        self.bounded_form = bounded_form
        self.password_reset_user = password_reset_user
        self.password_resets = password_resets
        self.access = access
        self.credentials = credentials
        self.request_client_key = request_client_key
        self.load_users = load_users
        self.user_allowed = user_allowed
        self.verify_credentials = verify_credentials
        self.two_factor = two_factor
        self.start_login_challenge = start_login_challenge
        self.issue_session_token = issue_session_token
        self.set_login_challenge_cookie = set_login_challenge_cookie
        self.set_session_cookie = set_session_cookie
        self.request_cookie = request_cookie
        self.login_challenge_cookie = login_challenge_cookie
        self.verify_login_challenge = verify_login_challenge
        self.clear_login_challenge_cookie = clear_login_challenge_cookie
        self.login_challenge_username = login_challenge_username

    def password_reset_request(self, request, parsed, actor):
        if not self.same_origin(request):
            return self.empty(request, 403)
        started = time.monotonic()
        generic_message = "如果该邮箱对应有效的 Portal 账户，重置链接已经发送。"
        try:
            payload = self.bounded_json(request, max_bytes=4096)
            email = str(payload.get("email") or "").strip()[:255]
            user = self.password_reset_user(email)
            sent = self.password_resets().request(
                username=str(user.get("username") or "") if user else "",
                email=email,
                client_key=self.request_client_key(request),
            )
            if sent and user:
                self.access().audit(
                    str(user["username"]), "password_reset_requested",
                    target=str(user["username"]),
                )
        except Exception as exc:
            print(f"Portal password reset request failed: {type(exc).__name__}", file=sys.stderr)
        time.sleep(max(0.0, 0.12 - (time.monotonic() - started)))
        return self.respond(request, 202, {"message": generic_message})

    def password_reset_confirm(self, request, parsed, actor):
        if not self.same_origin(request):
            return self.empty(request, 403)
        try:
            payload = self.bounded_json(request, max_bytes=8192)
            new_password = str(payload.get("new_password") or "")
            confirmation = str(payload.get("new_password_confirmation") or "")
            if new_password != confirmation:
                raise ValueError("两次输入的密码不一致。")
            self.credentials().validate_password(new_password)
            username = self.password_resets().consume(str(payload.get("token") or ""))
            if username not in self.load_users() or not self.user_allowed(username):
                raise PermissionError("Password reset link is invalid or expired.")
            self.credentials().set_password(username, new_password)
            self.access().upsert_user(username, actor=username, revoke_sessions=True)
            self.password_resets().invalidate_user(username)
            self.password_resets().notify_password_changed(
                str(self.access().user(username).get("email") or "")
            )
            self.access().audit(username, "password_reset_completed", target=username)
            return self.respond(request, 200, {"message": "密码已重置，请使用新密码登录。"})
        except PermissionError:
            return self.respond(request, 400, {"error": "重置链接无效或已经失效。"})
        except ValueError as exc:
            return self.respond(request, 400, {"error": str(exc)})
        except Exception as exc:
            print(f"Portal password reset failed: {type(exc).__name__}", file=sys.stderr)
            return self.respond(request, 500, {"error": "暂时无法重置密码，请稍后重试。"})

    def login(self, request, parsed, actor):
        if not self.same_origin(request):
            return self.empty(request, 403)
        form = self.bounded_form(request)
        username = form.get("username", "").strip()
        password = form.get("password", "")
        if self.verify_credentials(username, password) and self.user_allowed(username):
            try:
                requires_two_factor = self.two_factor().enabled(username)
            except RuntimeError as exc:
                print(f"Portal two-factor state error: {exc}", file=sys.stderr)
                return self.empty(request, 503)
            if requires_two_factor:
                challenge_token = self.start_login_challenge(username)
                request.send_response(302)
                self.set_login_challenge_cookie(request, challenge_token)
                request.send_header("Location", "/login/verify")
                request.end_headers()
                return
            token = self.issue_session_token(username)
            request.send_response(302)
            self.set_session_cookie(request, token)
            request.send_header("Location", "/")
            request.end_headers()
        else:
            request.send_response(302)
            request.send_header("Location", "/login?error=1")
            request.end_headers()

    def login_verify(self, request, parsed, actor):
        if not self.same_origin(request):
            return self.empty(request, 403)
        form = self.bounded_form(request)
        challenge_token = self.request_cookie(request, self.login_challenge_cookie)
        try:
            verified = self.verify_login_challenge(challenge_token, form.get("code", ""))
        except RuntimeError as exc:
            print(f"Portal two-factor verification error: {exc}", file=sys.stderr)
            return self.empty(request, 503)
        if verified and self.user_allowed(verified[0]):
            username, factor = verified
            token = self.issue_session_token(username)
            self.access().audit(
                username, "two_factor_login_succeeded", target=username,
                details={"factor": factor},
            )
            request.send_response(302)
            self.set_session_cookie(request, token)
            self.clear_login_challenge_cookie(request)
            request.send_header("Location", "/")
            request.end_headers()
            return
        request.send_response(302)
        if not self.login_challenge_username(challenge_token):
            self.clear_login_challenge_cookie(request)
            request.send_header("Location", "/login?error=1")
        else:
            request.send_header("Location", "/login/verify?error=1")
        request.end_headers()

    def routes(self):
        """Signing in and resetting a password."""
        return (
            RouteDefinition("POST", "/api/auth/password-reset/request", self.password_reset_request, "portal-public-post", phase="public"),
            RouteDefinition("POST", "/api/auth/password-reset/confirm", self.password_reset_confirm, "portal-public-post", phase="public"),
            RouteDefinition("POST", "/login", self.login, "portal-public-post", phase="public"),
            RouteDefinition("POST", "/login/verify", self.login_verify, "portal-public-post", phase="public"),
        )
