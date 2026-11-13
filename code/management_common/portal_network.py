"""Manifest-owned listener policy and private Provider proxy settings.

Absent proxy keys preserve the host egress; explicit empty values select direct
access. Listener ports and bind addresses belong only to instance.json.
"""
from __future__ import annotations

import ipaddress
import os
from pathlib import Path
import shlex
import socket

from management_common.file_lock import lock_stream
from management_common.instance_manifest import load

LOOPBACK_NETWORKS = ("127.0.0.0/8", "::1/128")
DEFAULT_PORT = 18084
PROXY_FIELDS = {"http": "HTTP_PROXY", "https": "HTTPS_PROXY", "all": "ALL_PROXY", "no_proxy": "NO_PROXY"}
PROXY_KEYS = frozenset((*PROXY_FIELDS.values(), *(k.lower() for k in PROXY_FIELDS.values())))


def config_path() -> Path:
    return load().directory('secrets') / 'network.env'


def _read(path: Path) -> bytes:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return b""
    if len(data) > 65536:
        raise ValueError("Network configuration is too large")
    return data


def _parse(data: bytes) -> dict[str, str]:
    result = {}
    for line in data.decode("utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator:
            raise ValueError("Invalid network environment file")
        parts = shlex.split(value, comments=True)
        if len(parts) > 1:
            raise ValueError("Invalid network environment value")
        result[key.strip()] = parts[0] if parts else ""
    return result


def _networks(values) -> list[str]:
    if not isinstance(values, list) or len(values) > 256:
        raise ValueError("允许网段必须是 IP 或 CIDR 列表（最多 256 项）")
    result = []
    for value in [*values, *LOOPBACK_NETWORKS]:
        if not isinstance(value, str) or not value.strip() or len(value) > 64:
            raise ValueError("允许网段必须是有效的 IPv4/IPv6 地址或 CIDR")
        try:
            network = str(ipaddress.ip_network(value.strip(), strict=False))
        except ValueError:
            raise ValueError("允许网段必须是有效的 IPv4/IPv6 地址或 CIDR") from None
        if network not in result:
            result.append(network)
    return result


def allowed_networks(instance=None) -> list[str]:
    instance = instance or load()
    if ipaddress.ip_address(instance.document['network']['bind']).is_loopback:
        return list(LOOPBACK_NETWORKS)
    settings = instance.site_settings().get('deployment', {}).get('router', {})
    return _networks(settings.get('allowed_networks', ['0.0.0.0/0', '::/0']))


def router_access() -> dict:
    network = load().document['network']
    mode = 'local' if ipaddress.ip_address(network['bind']).is_loopback else 'remote'
    return {'mode': mode, 'port': network['entry_port']}


def router_port() -> int:
    return router_access()["port"]


def _access_urls(port: int, mode: str) -> list[str]:
    urls = [f"http://127.0.0.1:{port}"]
    if mode != "remote":
        return urls
    addresses = set()
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM):
            addresses.add(item[4][0])
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            addresses.add(probe.getsockname()[0])
    except OSError:
        pass
    for address in sorted(addresses):
        if not ipaddress.ip_address(address).is_loopback and address != "0.0.0.0":
            urls.append(f"http://{address}:{port}")
    return urls[:9]


def _proxy_env(proxy: dict[str, str]) -> dict[str, str]:
    result = {}
    for field, key in PROXY_FIELDS.items():
        value = proxy[field] or (proxy["all"] if field in {"http", "https"} else "")
        result[key] = result[key.lower()] = value
    return result


def _proxy_values(entries: dict[str, str]) -> dict[str, str]:
    # Preserve the administrator's empty per-protocol field separately from its
    # effective ALL_PROXY fallback in the same atomic EnvironmentFile.
    return {field: entries.get("PORTAL_NETWORK_" + key, entries.get(key, entries.get(key.lower(), "")))
            for field, key in PROXY_FIELDS.items()}


def inherited_proxy_environment(instance=None) -> dict[str, str]:
    """Keep the existing Provider egress file ahead of inherited process values."""
    entries = {k: v for k, v in os.environ.items() if k in PROXY_KEYS}
    path = (instance or load()).directory('secrets') / 'host/provider-egress.env'
    entries.update({k: v for k, v in _parse(_read(path)).items() if k in PROXY_KEYS})
    return entries


def saved_proxy_environment(instance=None) -> dict[str, str] | None:
    entries = _parse(_read((instance or load()).directory('secrets') / 'network.env'))
    if not any(key in entries for key in PROXY_KEYS):
        return None
    return _proxy_env(_proxy_values(entries))


def read_settings() -> dict:
    """Access range, port, reachable addresses and proxy, as the installation configured them."""
    entries = _parse(_read(config_path()))
    saved = any(key in entries for key in PROXY_KEYS)
    source = entries if saved else inherited_proxy_environment()
    access = router_access()
    mode, port = access["mode"], access["port"]
    return {
        "access": {"mode": mode, "port": port, "urls": _access_urls(port, mode)},
        "proxy": _proxy_values(source),
        "proxy_source": "saved" if saved else "environment",
    }


def _lock(path: Path):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    return os.fdopen(descriptor, "a+b")


def initialize_allowed_networks(values: list[str]) -> None:
    from management_common.configuration_files import atomic_json
    networks = _networks(values)
    instance = load()
    path = instance.root / 'site.json'
    with _lock(path) as stream, lock_stream(stream):
        site = instance.site_settings()
        site.setdefault('deployment', {}).setdefault('router', {})['allowed_networks'] = networks
        atomic_json(path, site)
