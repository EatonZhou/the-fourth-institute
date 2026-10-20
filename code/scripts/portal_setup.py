#!/usr/bin/env python3
"""Set up a new Portal from the host it runs on.

``first-admin`` prints a one-time setup link. Whoever opens it joins through the
normal join page; the first account a Portal makes is its first Portal
管理员. It works only while the Portal has no account yet. Run it with the same
environment as the Portal Web service (PORTAL_DATABASE_URL and, when set,
PORTAL_DATABASE_NAMESPACE), or pass that service's environment file with --env-file.
"""
import argparse
import hashlib
import os
import secrets
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def load_environment(path):
    for line in Path(path).expanduser().read_text().splitlines():
        key, separator, value = line.strip().removeprefix("export ").partition("=")
        if separator and key.strip() and not key.lstrip().startswith("#"):
            os.environ.setdefault(key.strip(), (shlex.split(value) or [""])[0])


def first_admin(args):
    from management_portal.portal_access_control import PortalCredentialStore, get_portal_access_store
    from management_portal.user_enrollment import UserEnrollment

    enrollment = UserEnrollment(get_portal_access_store(), PortalCredentialStore())
    if args.if_needed and enrollment.has_accounts():
        # Desktop startup must not replace credentials or issue invitations for
        # an already configured instance.
        print('{"status":"already_configured"}')
        return
    secret = secrets.token_urlsafe(32)
    invitation = enrollment.create_invitation(
        "portal-setup", args.username, hashlib.sha256(secret.encode()).hexdigest(),
        display_name=args.display_name, expires_in_days=1, first_admin=True,
    )
    expires = datetime.fromtimestamp(invitation["expires_at"], timezone.utc).isoformat(timespec="minutes")
    print(f"Setup link for the first administrator {invitation['username']} (valid until {expires}, usable once):")
    print(f"  {args.url}/join#invite={secret}")
    print("Open it in a browser, choose a password, then sign in. The link is shown only now;")
    print("running this command again replaces it. Next steps: " + f"{args.url}/guide#admin")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env-file", help="Environment file of the Portal Web service")
    sub = parser.add_subparsers(dest="command", required=True)
    admin = sub.add_parser("first-admin", help="Print a one-time link that creates the first administrator")
    admin.add_argument("--username", required=True)
    admin.add_argument("--display-name", default="")
    admin.add_argument("--url", required=True, help="Address people use to open this Portal, e.g. https://portal.example")
    admin.add_argument("--if-needed", action="store_true", help="Leave existing accounts unchanged")
    args = parser.parse_args()
    args.url = args.url.rstrip("/")
    if args.env_file:
        load_environment(args.env_file)
    if not os.environ.get("PORTAL_DATABASE_URL"):
        parser.error("PORTAL_DATABASE_URL is not set; run with the Portal Web service's environment or pass --env-file")
    try:
        first_admin(args)
    except (PermissionError, FileExistsError, ValueError) as error:
        print(error, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
