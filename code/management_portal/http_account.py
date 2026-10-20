"""Authenticated account security and avatar HTTP routes."""

from __future__ import annotations

import sys
from urllib.parse import parse_qs

from management_portal import user_avatar


class AccountHttp:
    def __init__(
        self, *, respond, exception, serve_bytes, same_origin,
        access, two_factor, passkeys,
        change_password, read_bounded_json, issue_token, session_cookie,
        request_is_https, require_reauthentication, passkey_context,
        begin_two_factor, confirm_two_factor,
        send_empty, user_allowed,
    ):
        self.respond = respond
        self.exception = exception
        self.serve_bytes = serve_bytes
        self.same_origin = same_origin
        self._access = access
        self._two_factor = two_factor
        self._passkeys = passkeys
        self.change_password = change_password
        self.read_bounded_json = read_bounded_json
        self.issue_token = issue_token
        self.session_cookie = session_cookie
        self.request_is_https = request_is_https
        self.require_reauthentication = require_reauthentication
        self.passkey_context = passkey_context
        self.begin_two_factor = begin_two_factor
        self.confirm_two_factor = confirm_two_factor
        self.send_empty = send_empty
        self.user_allowed = user_allowed

    @property
    def access(self):
        return self._access()

    @property
    def two_factor(self):
        return self._two_factor()

    @property
    def passkeys(self):
        return self._passkeys()

    @staticmethod
    def _image_owner(field, target):
        """The store and subject an image address names: user, resident, lineage or project."""

        if field == "user":
            return user_avatar.user_avatar_store(), target
        if field == "resident":
            from management_portal.resident_avatar import resident_avatar_store
            return resident_avatar_store(), target
        from management_portal.avatar_store import scope_avatar_store, scope_subject
        return scope_avatar_store(), scope_subject(field, target)

    def avatar_get(self, request, parsed, actor):
        # Reading an avatar is never a permission question: any signed-in
        # viewer sees every existing avatar of a User, Resident, Lineage or
        # Project, whoever the viewer is.  Changing one goes through the
        # owner's Update instruction (UpdateUser, UpdateResident, ...).
        try:
            store = user_avatar.user_avatar_store()
            if parsed.path.endswith("/image"):
                query = parse_qs(parsed.query)
                owners = set(query) - {"v"}
                if "v" not in query or len(owners) != 1 or any(len(values) != 1 for values in query.values()):
                    raise ValueError("Invalid avatar address")
                field = owners.pop()
                owner, subject = self._image_owner(field, query[field][0])
                # The address names the image by its content hash, so it never changes.
                return self.serve_bytes(
                    request, 200, owner.image(subject, query["v"][0]), "image/png",
                    cache_control="private, max-age=31536000, immutable",
                    extra_headers={"X-Content-Type-Options": "nosniff"},
                )
            query = parse_qs(parsed.query, keep_blank_values=True)
            if query:
                if len(query) != 1 or any(len(values) != 1 for values in query.values()):
                    raise ValueError("Specify one avatar principal")
                if set(query) & {"lineage", "project"}:
                    # Without its own avatar a Lineage or Project shows its initial.
                    from management_portal.avatar_store import scope_avatar_store, scope_subject
                    (field, (target,)), = query.items()
                    return self.respond(request, 200, {"avatar": scope_avatar_store().read(scope_subject(field, target))})
                if set(query) != {"principal"}:
                    raise ValueError("Specify one avatar principal")
                target = query["principal"][0]
                from management_common.principal import actor_principal
                identity = actor_principal(target)
                if identity.kind == "resident":
                    # Without an avatar a Resident shows its initial, like a Lineage or Project.
                    from management_portal.resident_avatar import get_existing_avatar
                    avatar = get_existing_avatar(target)
                elif identity.kind == "user":
                    avatar = store.get(target)
                else:
                    raise ValueError("Avatar principal must be a User or Resident")
                return self.respond(request, 200, {"avatar": avatar})
            if not actor:
                return self.respond(request, 403, {"error": "Authenticated User required"})
            return self.respond(request, 200, {
                "avatar": store.get(actor),
                "sprites": [user_avatar.sprite(index) for index in range(len(user_avatar.SPRITE_RARITIES))],
            })
        except PermissionError as exc:
            return self.respond(request, 403, {"error": str(exc)})
        except FileNotFoundError as exc:
            return self.respond(request, 404, {"error": str(exc)})
        except ValueError as exc:
            return self.respond(request, 400, {"error": str(exc)})

    def security_get(self, request, parsed, actor):
        return self.respond(request, 200, {'two_factor': self.two_factor.status(actor), 'passkeys': self.passkeys.status(actor)})

    def public_passkey_options(self, request, _parsed, _actor):
        if not self.same_origin(request):
            return self.send_empty(request, 403)
        try:
            rp_id, origin = self.passkey_context(request)
            result = self.passkeys.begin_authentication(rp_id=rp_id, origin=origin)
            return self.respond(request, 200, result)
        except (PermissionError, ValueError) as exc:
            return self.respond(request, 400, {'error': str(exc)})
        except Exception as exc:
            print(f'Portal Passkey authentication setup error: {exc}', file=sys.stderr)
            return self.respond(request, 500, {'error': 'Unable to start Passkey authentication.'})

    def public_passkey_verify(self, request, _parsed, _actor):
        if not self.same_origin(request):
            return self.send_empty(request, 403)
        try:
            payload = self.read_bounded_json(request, max_bytes=131_072)
            username = self.passkeys.finish_authentication(
                ceremony_token=str(payload.get('ceremony_token') or ''),
                credential=payload.get('credential') or {},
            )
            if not self.user_allowed(username):
                raise PermissionError('Passkey authentication failed.')
            token = self.issue_token(username)
            self.access.audit(username, 'passkey_login_succeeded', target=username)
            return self.respond(
                request, 200, {'authenticated': True, 'username': username},
                extra_headers={'Set-Cookie': self.session_cookie(token, secure=self.request_is_https(request))},
            )
        except (PermissionError, ValueError):
            return self.respond(request, 403, {'error': 'Passkey authentication failed.'})
        except Exception as exc:
            print(f'Portal Passkey authentication error: {exc}', file=sys.stderr)
            return self.respond(request, 403, {'error': 'Passkey authentication failed.'})

    def password_post(self, request, parsed, actor):
        actor = actor or ''
        try:
            result = self.change_password(actor, self.read_bounded_json(request, max_bytes=16384))
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        except ValueError as exc:
            return self.respond(request, 400, {'error': str(exc)})
        except Exception as exc:
            return self.exception(request, exc)
        token = self.issue_token(actor)
        return self.respond(request, 200, result, extra_headers={'Set-Cookie': self.session_cookie(token, secure=self.request_is_https(request))})

    def passkey_options_post(self, request, parsed, actor):
        try:
            payload = self.read_bounded_json(request, max_bytes=16384)
            self.require_reauthentication(actor, payload)
            rp_id, origin = self.passkey_context(request)
            access = self.access.user(actor)
            result = self.passkeys.begin_registration(actor, display_name=str(access.get('display_name') or actor), rp_id=rp_id, origin=origin)
            self.access.audit(actor, 'passkey_registration_started', target=actor)
            return self.respond(request, 200, result)
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        except ValueError as exc:
            return self.respond(request, 400, {'error': str(exc)})
        except Exception as exc:
            print(f'Portal Passkey registration setup error: {exc}', file=sys.stderr)
            return self.respond(request, 500, {'error': 'Unable to start Passkey registration.'})

    def passkey_verify_post(self, request, parsed, actor):
        try:
            payload = self.read_bounded_json(request, max_bytes=131072)
            credential = self.passkeys.finish_registration(actor, ceremony_token=str(payload.get('ceremony_token') or ''), credential=payload.get('credential') or {}, name=str(payload.get('name') or ''))
            self.access.audit(actor, 'passkey_registered', target=actor, details={'credential_id': credential['credential_id']})
            return self.respond(request, 200, {'credential': credential})
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        except ValueError as exc:
            return self.respond(request, 400, {'error': str(exc)})
        except Exception as exc:
            print(f'Portal Passkey registration error: {exc}', file=sys.stderr)
            return self.respond(request, 400, {'error': 'Unable to register this Passkey.'})

    def passkey_delete_post(self, request, parsed, actor):
        try:
            payload = self.read_bounded_json(request, max_bytes=16384)
            credential_id = str(payload.get('credential_id') or '').strip()
            if not credential_id:
                raise ValueError('Passkey credential is required.')
            if not self.passkeys.delete_credential(actor, credential_id):
                raise KeyError('Passkey not found.')
            self.access.audit(actor, 'passkey_deleted', target=actor, details={'credential_id': credential_id})
            return self.respond(request, 200, {'deleted': True})
        except KeyError as exc:
            return self.respond(request, 404, {'error': str(exc.args[0])})
        except ValueError as exc:
            return self.respond(request, 400, {'error': str(exc)})
        except Exception as exc:
            print(f'Portal Passkey deletion error: {exc}', file=sys.stderr)
            return self.respond(request, 500, {'error': 'Unable to delete this Passkey.'})

    def two_factor_setup_post(self, request, parsed, actor):
        try:
            payload = self.read_bounded_json(request, max_bytes=16384)
            if self.two_factor.enabled(actor):
                raise ValueError('Two-factor authentication is already enabled.')
            self.require_reauthentication(actor, payload)
            result = self.begin_two_factor(actor)
            self.access.audit(actor, 'two_factor_setup_started', target=actor)
            return self.respond(request, 200, result)
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        except ValueError as exc:
            return self.respond(request, 400, {'error': str(exc)})
        except Exception as exc:
            print(f'Portal two-factor setup error: {exc}', file=sys.stderr)
            return self.respond(request, 500, {'error': 'Unable to start two-factor setup.'})

    def two_factor_confirm_post(self, request, parsed, actor):
        try:
            payload = self.read_bounded_json(request, max_bytes=16384)
            recovery_codes = self.confirm_two_factor(actor, str(payload.get('setup_token') or ''), str(payload.get('code') or ''))
            user = self.access.upsert_user(actor, actor=actor, revoke_sessions=True)
            self.access.audit(actor, 'two_factor_enabled', target=actor)
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        except (RuntimeError, ValueError) as exc:
            return self.respond(request, 400, {'error': str(exc)})
        token = self.issue_token(actor)
        return self.respond(request, 200, {'enabled': True, 'recovery_codes': recovery_codes, 'session_version': user['session_version']}, extra_headers={'Set-Cookie': self.session_cookie(token, secure=self.request_is_https(request))})

    def two_factor_recovery_codes_post(self, request, parsed, actor):
        try:
            payload = self.read_bounded_json(request, max_bytes=16384)
            self.require_reauthentication(actor, payload)
            recovery_codes = self.two_factor.regenerate_recovery_codes(actor)
            from .user_enrollment import revoke_user_tokens
            revoke_user_tokens(actor)
            self.access.audit(actor, 'two_factor_recovery_codes_regenerated', target=actor)
            return self.respond(request, 200, {'recovery_codes': recovery_codes})
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        except (RuntimeError, ValueError) as exc:
            return self.respond(request, 400, {'error': str(exc)})

    def two_factor_disable_post(self, request, parsed, actor):
        try:
            payload = self.read_bounded_json(request, max_bytes=16384)
            self.require_reauthentication(actor, payload)
            if not self.two_factor.disable(actor):
                raise ValueError('Two-factor authentication is not enabled.')
            user = self.access.upsert_user(actor, actor=actor, revoke_sessions=True)
            self.access.audit(actor, 'two_factor_disabled', target=actor)
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        except (RuntimeError, ValueError) as exc:
            return self.respond(request, 400, {'error': str(exc)})
        token = self.issue_token(actor)
        return self.respond(request, 200, {'enabled': False, 'session_version': user['session_version']}, extra_headers={'Set-Cookie': self.session_cookie(token, secure=self.request_is_https(request))})
