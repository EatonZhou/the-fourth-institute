#!/usr/bin/env python3
"""External User client: read the agent guide, redeem an invitation, or execute HATG instructions.

Standard library only, so an agent can fetch it from the Portal it joins: <Portal>/guide/portal_user.py
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
from functools import lru_cache
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


# The download endpoint fills this from the same common source used in the repo.
_BUNDLED_PRIVATE_FILES = None


@lru_cache(maxsize=1)
def _private_storage():
    if _BUNDLED_PRIVATE_FILES is not None:
        import types
        module = types.ModuleType("portal_private_files")
        exec(compile(_BUNDLED_PRIVATE_FILES, "<portal-file-permissions>", "exec"), module.__dict__)
        return module
    if __package__ in (None, ""):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from management_common import file_permissions
    return file_permissions


def _save_credentials(path, saved, parser, *, create=False):
    try:
        payload = (json.dumps(saved, indent=2) + "\n").encode("utf-8")
        if create:
            with _private_storage().create_private_file(path) as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
        else:
            _private_storage().atomic_write_private_bytes(path, payload)
    except (OSError, ValueError):
        parser.error("Cannot securely store credentials")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _post(base, path, payload, token="", accept="application/json"):
    headers = {"Content-Type": "application/json", "Accept": accept}
    if token:
        headers["Authorization"] = "Bearer " + token
    return Request(base + path, data=json.dumps(payload).encode(), headers=headers, method="POST")


def request(base, path, payload, token=""):
    try:
        with build_opener(NoRedirect()).open(_post(base, path, payload, token), timeout=60) as response:
            return json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: {exc.read(8192).decode(errors='replace')}") from None


def events(response):
    """Server-sent events of a streaming instruction as (event name, JSON data)."""
    name, data = "message", []
    for raw in response:
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:
            if data:
                try:
                    yield name, json.loads("\n".join(data))
                except json.JSONDecodeError:
                    pass
            name, data = "message", []
        elif line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())


def watch(base, payload, token):
    """Print each streamed item as one JSON line; reconnect after the last cursor until the stream ends."""
    arguments = dict(payload["arguments"])
    while True:
        try:
            req = _post(base, "/api/instructions/execute", {**payload, "arguments": arguments}, token, "text/event-stream")
            with build_opener(NoRedirect()).open(req, timeout=330) as response:
                for name, value in events(response):
                    if name == "batch":
                        for item in (value or {}).get("items") or []:
                            print(json.dumps(item, ensure_ascii=False), flush=True)
                            if isinstance(item, dict) and item.get("cursor") is not None:
                                arguments["after_cursor"] = item["cursor"]
                    elif name == "error":
                        raise RuntimeError(json.dumps(value, ensure_ascii=False))
                    elif name == "end":
                        return
        except HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code}: {exc.read(8192).decode(errors='replace')}") from None
        except TimeoutError:
            continue


def endpoint_url(value):
    if not isinstance(value, str) or any(ord(char) <= 32 or ord(char) == 127 for char in value):
        raise ValueError("Invalid Portal endpoint URL")
    parsed = urlsplit(value)
    if not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("Use a base URL without credentials, path, query or fragment")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}):
        raise ValueError("HTTPS is required, except for a local SSH tunnel")
    port = parsed.port
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    default_port = 443 if parsed.scheme == "https" else 80
    authority = host if port is None or port == default_port else f"{host}:{port}"
    return f"{parsed.scheme}://{authority}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Portal HTTPS base URL")
    parser.add_argument("--credentials", type=Path, help="Private local token file (not needed for guide)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("guide", help="Print this Portal's guide for agents (no credentials sent)")
    join = sub.add_parser("join", help="Read an invitation link/code without echoing it")
    join.add_argument("--name", default="External assistant")
    join.add_argument("--expires-in-days", type=int, default=90, help="Token lifetime, 1-365 days (default 90)")
    call = sub.add_parser("call", help="Execute one instruction and print its JSON result")
    call.add_argument("instruction")
    call.add_argument("--arguments", default="{}", help="JSON arguments (never include secrets)")
    call.add_argument("--idempotency-key", default="")
    call.add_argument("--expected-version", default="", help="Fail instead of changing the target if its version moved on")
    listing = sub.add_parser("list", help="List instructions, optionally of one category")
    listing.add_argument("--category", default="")
    schema = sub.add_parser("schema", help="Print the parameters and result of one instruction")
    schema.add_argument("instruction")
    stream = sub.add_parser("watch", help="Run a streaming (Watch*) instruction; one JSON line per item")
    stream.add_argument("instruction")
    stream.add_argument("--arguments", default="{}", help="JSON arguments (never include secrets)")
    sub.add_parser("trust-url", help="Trust --url as another entrance to this Portal (local only; verify its ownership first)")
    sub.add_parser("untrust-url", help="Remove --url from trusted entrances (local only; does not revoke the token)")
    args = parser.parse_args()
    try:
        base = endpoint_url(args.url)
    except ValueError as error:
        parser.error(str(error))
    if args.command == "guide":
        req = Request(base + "/guide/agent.md", headers={"Accept": "text/markdown"})
        with build_opener(NoRedirect()).open(req, timeout=60) as response:
            print(response.read().decode("utf-8"))
        return
    if args.credentials is None:
        parser.error("--credentials is required for " + args.command)
    path = args.credentials.expanduser()
    if path.exists() or path.is_symlink():
        try:
            saved = json.loads(_private_storage().read_private_bytes(path))
            if not isinstance(saved, dict):
                raise ValueError("Invalid credentials")
        except (OSError, ValueError):
            parser.error("Credentials require an owned private regular file (POSIX mode 0600 or Windows private ACL)")
        # Legacy files trust only their original URL; an explicit list is authoritative,
        # including an empty list after all entrances have been removed.
        urls = saved.get("trusted_urls", [saved["url"]])
        if not isinstance(urls, list) or not all(isinstance(url, str) for url in urls):
            parser.error("Invalid trusted_urls in credential file")
        trusted = list(dict.fromkeys(endpoint_url(url) for url in urls))
        if args.command in {"trust-url", "untrust-url"}:
            if args.command == "trust-url" and base not in trusted:
                trusted.append(base)
            elif args.command == "untrust-url":
                trusted = [url for url in trusted if url != base]
            saved["trusted_urls"] = trusted
            _save_credentials(path, saved, parser)
            print("Trusted Portal entrances: " + (", ".join(trusted) or "(none)"))
            print("Local configuration updated; no credentials were sent.")
            return
        if base not in trusted:
            parser.error("Untrusted Portal entrance. Verify it belongs to your Portal, then run trust-url with this --url and --credentials.")
    elif args.command == "join":
        saved = {"url": base, "trusted_urls": [base], "token": "dbu_" + secrets.token_urlsafe(32), "status": "pending"}
        _save_credentials(path, saved, parser, create=True)
    else:
        parser.error("Credential file does not exist; redeem an invitation first")
    if args.command == "join":
        if saved.get("status") == "active":
            parser.error("This credential is already registered")
        invitation = getpass.getpass("Invitation link or code: ").strip()
        if "#" in invitation:
            invitation = parse_qs(urlsplit(invitation).fragment).get("invite", [""])[0]
        result = request(base, "/api/auth/invitations/accept", {
            "invitation_secret": invitation, "secret_sha256": hashlib.sha256(saved["token"].encode()).hexdigest(),
            "token_name": args.name, "expires_in_days": args.expires_in_days,
        })
        saved.update(status="active", username=result["username"], credential=result["credential"])
        _save_credentials(path, saved, parser)
        print(f"Joined as {saved['username']}. Credential stored in {path}.")
        expires_at = (result.get("credential") or {}).get("expires_at")
        if expires_at:
            print("Token expires at " + datetime.fromtimestamp(expires_at, timezone.utc).isoformat(timespec="minutes")
                  + "; create a replacement with CreateUserAccessToken before then.")
        ama = result.get("personal_ama") or {}
        if ama.get("resident_id"):
            print(f"Your Ask Me Anything Resident: {ama['resident_id']} (Lineage {ama.get('lineage_id', '')}).")
        command = f"{sys.argv[0]} --url {base} --credentials {path}"
        identity = json.dumps({"user_id": saved["username"]}, ensure_ascii=False)
        print(f"Next: confirm your identity with `{command} call GetUser --arguments '{identity}'`,")
        print(f"then read the agent guide with `{command} guide` ({base}/guide/agent.md).")
        return
    if args.command == "list":
        instruction, arguments = "ListInstructions", ({"category": args.category} if args.category else {})
    elif args.command == "schema":
        instruction, arguments = "GetInstructionSchema", {"instruction": args.instruction}
    else:
        instruction, arguments = args.instruction, json.loads(args.arguments)
    payload = {"schema": "tfi.hatg-instruction.v2", "instruction": instruction, "arguments": arguments}
    if getattr(args, "idempotency_key", ""):
        payload["idempotency_key"] = args.idempotency_key
    if getattr(args, "expected_version", ""):
        payload["expected_version"] = args.expected_version
    if args.command == "watch":
        watch(base, payload, saved["token"])
        return
    print(json.dumps(request(base, "/api/instructions/execute", payload, saved["token"]), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
