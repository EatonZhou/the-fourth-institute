"""Portal's local model-transport endpoint and timing lookup (stdlib only).

ExecutionService owns the proxy independently of Workers and native sessions. Routes are signed,
stateless capabilities; they contain an upstream address, never its credential.
Credentials continue to come from the Provider on each request.
"""
from __future__ import annotations
from management_common.portal_paths import path as portal_path
from management_common import portal_network

import base64
import hashlib
import hmac
from http.client import HTTPException
import json
import os
from pathlib import Path
import threading
from datetime import datetime, timezone
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_ROOT = (portal_path("staging_root") / "portal_execution" / "provider-timing")
DEFAULT_PORT = 18419
_local = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def root() -> Path:
    return Path(os.environ.get('PORTAL_PROVIDER_TIMING_ROOT', str(DEFAULT_ROOT)))


def endpoint() -> str:
    return 'http://127.0.0.1:' + str(int(os.environ.get('PORTAL_PROVIDER_TIMING_PORT', DEFAULT_PORT)))


def signing_key() -> bytes:
    directory = root()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / 'key'
    # Publish a complete key atomically; another process may be starting too.
    try:
        return path.read_bytes()
    except FileNotFoundError:
        temporary = directory / f'.key-{os.getpid()}-{threading.get_ident()}'
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(os.urandom(32))
            try:
                os.link(temporary, path)
            except FileExistsError:
                pass
        finally:
            temporary.unlink(missing_ok=True)
        return path.read_bytes()


def encode_route(provider: str, upstream: str, proxy: str = '') -> str:
    parsed = urllib.parse.urlsplit(upstream)
    if parsed.scheme not in {'https', 'http'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Model upstream must be an HTTP(S) base URL without credentials or query')
    payload = base64.urlsafe_b64encode(json.dumps([provider, upstream.rstrip('/'), proxy], separators=(',', ':')).encode()).decode().rstrip('=')
    signature = hmac.new(signing_key(), payload.encode(), hashlib.sha256).hexdigest()
    return payload + '.' + signature


def decode_route(token: str) -> tuple[str, str, str]:
    payload, signature = token.rsplit('.', 1)
    if not hmac.compare_digest(signature, hmac.new(signing_key(), payload.encode(), hashlib.sha256).hexdigest()):
        raise ValueError('Invalid route')
    provider, upstream, proxy = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
    return provider, upstream, proxy


def native_egress() -> str:
    """The same egress configured on Portal's Codex and Claude processes."""
    entries = portal_network.saved_proxy_environment()
    if entries is None:
        entries = portal_network.inherited_proxy_environment()
    return next((entries[key] for key in ('HTTPS_PROXY', 'https_proxy', 'ALL_PROXY', 'all_proxy') if entries.get(key)), '')


def enabled() -> bool:
    """ExecutionService owns the switch and listener; unavailable observation is optional."""
    try:
        with _local.open(endpoint() + '/health', timeout=1.5) as response:
            value = json.load(response)
            return isinstance(value, dict) and value.get('enabled') is True
    except (OSError, ValueError, HTTPException):
        return False


def route_url(provider: str, upstream: str, *, proxy: str = '') -> str:
    parsed = urllib.parse.urlsplit(upstream)
    if parsed.hostname in {'127.0.0.1', 'localhost'} and parsed.path.startswith('/p/'):
        _, upstream, prior_proxy = decode_route(parsed.path[3:])
        proxy = proxy or prior_proxy
    if not enabled():
        return upstream
    return endpoint() + '/p/' + encode_route(provider, upstream, proxy)


def lookup(provider: str, output_id: str) -> dict[str, str]:
    if not output_id:
        return {}
    # Observation failure must not turn a completed model response into a retry.
    try:
        key = (root() / 'key').read_bytes()
        query = urllib.parse.urlencode({'provider': provider, 'id': output_id})
        request = urllib.request.Request(endpoint() + '/timing?' + query,
                                         headers={'Authorization': 'Bearer ' + key.hex()})
        with _local.open(request, timeout=3) as response:
            value = json.load(response)
            if not isinstance(value, dict):
                return {}
            return {key: value[key] for key in ('started_at', 'finished_at')
                    if isinstance(value.get(key), str) and value[key]}
    except (OSError, ValueError, HTTPException):
        return {}


def completed_timing(provider: str, output_id: str) -> dict:
    timing = lookup(provider, output_id)
    if timing:
        timing.setdefault('finished_at', datetime.now(timezone.utc).isoformat(timespec='microseconds'))
    return timing


def attach(params: dict, provider: str, output_id: str = '') -> dict:
    item = params.get('item') or {}
    if item.get('type') not in {'agentMessage', 'reasoning'}:
        return params
    timing = completed_timing(provider, output_id or str(item.get('id') or ''))
    return {**params, 'request_timing': timing} if timing else params
