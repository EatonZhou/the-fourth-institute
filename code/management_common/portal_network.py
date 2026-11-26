"""Manifest-owned listener policy and private Provider proxy settings.

Absent proxy keys preserve the host egress; explicit empty values select direct
access. Listener ports and bind addresses belong only to instance.json.
"""
from __future__ import annotations

import ipaddress
import hashlib
import json
import os
from pathlib import Path
import shlex
import socket
import tempfile
from urllib.parse import urlsplit

from management_common.file_lock import lock_stream
from management_common.instance_manifest import load

LOOPBACK_NETWORKS = ("127.0.0.0/8", "::1/128")
DEFAULT_PORT = 18084
PROXY_FIELDS = {"http": "HTTP_PROXY", "https": "HTTPS_PROXY", "all": "ALL_PROXY", "no_proxy": "NO_PROXY"}
PROXY_KEYS = frozenset((*PROXY_FIELDS.values(), *(k.lower() for k in PROXY_FIELDS.values())))
PROVIDER_EGRESS_ENV = "TFI_PROVIDER_EGRESS"


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


def host_routed_environment(environment):
    """Leave outbound routing to the host for this child process."""
    return {key: value for key, value in environment.items() if key.upper() not in PROXY_FIELDS.values()}


def proxy_environment_disabled(instance=None) -> bool:
    if os.environ.get(PROVIDER_EGRESS_ENV) == "tunnel":
        return True
    return (instance or load()).site_settings().get("deployment", {}).get("proxy_environment") == "disabled"


def inherited_proxy_environment(instance=None) -> dict[str, str]:
    """Keep the existing Provider egress file ahead of inherited process values."""
    if proxy_environment_disabled(instance):
        return {}
    entries = {k: v for k, v in os.environ.items() if k in PROXY_KEYS}
    path = (instance or load()).directory('secrets') / 'host/provider-egress.env'
    entries.update({k: v for k, v in _parse(_read(path)).items() if k in PROXY_KEYS})
    return entries


def saved_proxy_environment(instance=None) -> dict[str, str] | None:
    if proxy_environment_disabled(instance):
        return {}
    entries = _parse(_read((instance or load()).directory('secrets') / 'network.env'))
    if not any(key in entries for key in PROXY_KEYS):
        return None
    return _proxy_env(_proxy_values(entries))


def read_settings() -> dict:
    """Manifest-owned access and editable outbound proxy settings."""
    raw = _read(config_path())
    entries = _parse(raw)
    saved = any(key in entries for key in PROXY_KEYS)
    disabled = proxy_environment_disabled()
    source = {} if disabled else (entries if saved else inherited_proxy_environment())
    access = router_access()
    mode, port = access["mode"], access["port"]
    return {
        "access": {"mode": mode, "port": port, "urls": _access_urls(port, mode)},
        "proxy": _proxy_values(source),
        "proxy_source": "disabled" if disabled else ("saved" if saved else "environment"),
        "revision": hashlib.sha256(raw + json.dumps(_proxy_values(source), sort_keys=True).encode()).hexdigest(),
    }


class ProxyConflict(ValueError):
    pass


def _validate_proxy(proxy) -> dict[str, str]:
    if not isinstance(proxy, dict) or set(proxy) != set(PROXY_FIELDS):
        raise ValueError("请提供完整的代理设置")
    result = {}
    for field, value in proxy.items():
        if (not isinstance(value, str) or len(value) > 8192
                or any(ord(char) < 32 or ord(char) == 127 for char in value)):
            raise ValueError("代理设置包含无效字符或内容过长")
        value = value.strip()
        if value and field != 'no_proxy':
            try:
                url = urlsplit(value)
                valid = (url.scheme in {'http', 'https', 'socks4', 'socks4a', 'socks5', 'socks5h'}
                         and url.hostname and url.path in {'', '/'} and not url.query and not url.fragment
                         and (url.port is None or 1 <= url.port <= 65535)
                         and not any(char.isspace() for char in value) and '\\' not in value)
            except ValueError:
                valid = False
            if not valid:
                raise ValueError("代理 URL 必须包含有效的协议、主机和端口，不可包含路径或查询参数")
        result[field] = value
    return result


def update_proxy(proxy, *, expected_revision: str) -> dict:
    """Atomically replace proxy fields; None restores inherited egress.

    Listener configuration is never changed. Existing consumers read the same
    private EnvironmentFile when starting their next process.
    """
    if not isinstance(expected_revision, str) or not expected_revision:
        raise ValueError("请先读取网络设置再保存")
    if proxy is not None and proxy_environment_disabled():
        raise ValueError("此实例已禁用应用代理")
    values = None if proxy is None else _validate_proxy(proxy)
    path = config_path()
    with _lock(path) as stream, lock_stream(stream):
        if path.is_symlink():
            raise ValueError("代理配置文件不能是符号链接")
        if read_settings()['revision'] != expected_revision:
            raise ProxyConflict("代理设置已被修改，请重新读取后再保存")
        raw = _read(path)
        # Keep unrelated entries and comments verbatim.
        owned = PROXY_KEYS | {'PORTAL_NETWORK_' + key for key in PROXY_FIELDS.values()}
        lines = [line for line in raw.decode('utf-8').splitlines()
                 if line.partition('=')[0].strip() not in owned]
        if values is not None:
            entries = {**_proxy_env(values), **{'PORTAL_NETWORK_' + key: values[field]
                                              for field, key in PROXY_FIELDS.items()}}
            lines.extend(f'{key}={json.dumps(value, ensure_ascii=False)}' for key, value in entries.items())
        data = ('\n'.join(lines) + '\n').encode('utf-8')
        if len(data) > 65536:
            raise ValueError("代理设置内容过长")
        descriptor, name = tempfile.mkstemp(prefix='.network-', dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, 'wb') as output:
                from management_common.configuration_files import preserve_config_permissions
                preserve_config_permissions(temporary, path)
                if os.name != 'nt':
                    temporary.chmod(0o600)
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return read_settings()


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
