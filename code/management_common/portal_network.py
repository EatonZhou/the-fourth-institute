"""Site network policy shared by the management page, Router and process launchers.

The private environment file is also systemd's final EnvironmentFile. Absence of
proxy keys preserves inherited egress; explicit empty keys select direct access.
"""
from __future__ import annotations

import hashlib
import ipaddress
import os
from pathlib import Path
import shlex
import socket
import tempfile
from urllib.parse import urlsplit

from management_common.file_lock import lock_stream
from management_common import file_permissions
from management_common.portal_paths import resolve_directory
from management_common.site_config import read_site_config

LOOPBACK_NETWORKS = ("127.0.0.0/8", "::1/128")
DEFAULT_PORT = 18084
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")
PROXY_FIELDS = {"http": "HTTP_PROXY", "https": "HTTPS_PROXY", "all": "ALL_PROXY", "no_proxy": "NO_PROXY"}
PROXY_KEYS = frozenset((*PROXY_FIELDS.values(), *(k.lower() for k in PROXY_FIELDS.values())))


class VersionConflict(ValueError):
    """Another administrator changed this file since it was read."""


def _deployment_file(key: str) -> Path | None:
    deployment = read_site_config().get("deployment", {})
    if not isinstance(deployment, dict):
        raise ValueError("deployment must be an object")
    if key not in deployment:
        return None
    return resolve_directory(deployment[key])


def _default_config_file(name: str) -> Path:
    return Path.home() / ".config" / "tfi" / name


def config_path() -> Path:
    if "PORTAL_NETWORK_CONFIG" in os.environ:
        path = Path(os.environ["PORTAL_NETWORK_CONFIG"])
    else:
        path = _deployment_file("network_config") or _default_config_file("portal-network.env")
    if not path.is_absolute():
        raise ValueError("PORTAL_NETWORK_CONFIG must be an absolute path")
    return path


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


def _version(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


def allowed_networks(path: Path | None = None) -> list[str]:
    entries = _parse(_read(path or config_path()))
    mode = entries.get("PORTAL_ACCESS_MODE")
    if mode == "local":
        return list(LOOPBACK_NETWORKS)
    if mode == "remote":
        return ["0.0.0.0/0", "::/0"]
    if mode is not None:
        raise ValueError("无效的 Portal 访问范围")
    value = entries.get("PORTAL_ALLOWED_NETWORKS", "")
    return _networks(value.split(",") if value else [])


def _access(entries: dict[str, str]) -> tuple[str, int, bool]:
    mode = entries.get("PORTAL_ACCESS_MODE")
    legacy = mode is None and "PORTAL_ALLOWED_NETWORKS" in entries
    if mode is None:
        value = entries.get("PORTAL_ALLOWED_NETWORKS", "")
        networks = _networks(value.split(",") if value else [])
        mode = "remote" if any(not ipaddress.ip_network(value).is_loopback for value in networks) else "local"
    if mode not in {"local", "remote"}:
        raise ValueError("访问范围必须是 local 或 remote")
    raw_port = entries.get("PORTAL_PORT", str(DEFAULT_PORT))
    if not raw_port.isascii() or not raw_port.isdecimal() or not 1024 <= int(raw_port) <= 65535:
        raise ValueError("Portal 端口必须是 1024 到 65535 的整数")
    return mode, int(raw_port), legacy


def router_access(path: Path | None = None) -> dict:
    entries = _parse(_read(path or config_path()))
    mode, port, legacy = _access(entries)
    return {"mode": mode, "port": port, "legacy_restriction": legacy,
            "explicit_mode": "PORTAL_ACCESS_MODE" in entries,
            "explicit_port": "PORTAL_PORT" in entries}


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


def _text(value) -> str:
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("代理设置必须是单行文本（最多 4096 字符）")
    return value.strip()


def _proxy(value) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != set(PROXY_FIELDS):
        raise ValueError("代理设置需要 http、https、all、no_proxy 四个字段")
    result = {key: _text(item) for key, item in value.items()}
    for key in ("http", "https", "all"):
        if not result[key]:
            continue
        try:
            parsed = urlsplit(result[key])
            valid = parsed.scheme in {"http", "https"} and parsed.hostname and (parsed.port is None or 0 < parsed.port <= 65535)
            if not valid or parsed.path not in {"", "/"} or parsed.query or parsed.fragment or any(c.isspace() for c in result[key]):
                raise ValueError()
        except ValueError:
            raise ValueError("代理地址必须是有效的 http:// 或 https:// 地址，不能包含路径、查询或片段") from None
    hosts = []
    for host in [*result["no_proxy"].split(","), *LOOPBACK_HOSTS]:
        host = host.strip()
        if any(c.isspace() or c in "'\"\\=$;" for c in host):
            raise ValueError("不走代理的地址须以逗号分隔，不能包含空格或控制字符")
        if host and host not in hosts:
            hosts.append(host)
    result["no_proxy"] = ",".join(hosts)
    return result


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


def inherited_proxy_environment() -> dict[str, str]:
    """Keep the existing Provider egress file ahead of inherited process values."""
    configured = os.environ.get("PORTAL_PROVIDER_EGRESS_ENV_FILE")
    site_path = None if configured else _deployment_file("provider_egress_env_file")
    path = Path(configured) if configured else site_path or _default_config_file("provider-egress.env")
    entries = {k: v for k, v in os.environ.items() if k in PROXY_KEYS}
    if configured or site_path is not None:
        # An explicitly configured missing file is an error, not a direct fallback.
        raw = path.read_bytes()
    else:
        raw = _read(path)
    entries.update({k: v for k, v in _parse(raw).items() if k in PROXY_KEYS})
    return entries


def saved_proxy_environment() -> dict[str, str] | None:
    entries = _parse(_read(config_path()))
    if not any(key in entries for key in PROXY_KEYS):
        return None
    return _proxy_env(_proxy_values(entries))


def _settings(data: bytes) -> dict:
    entries = _parse(data)
    saved = any(key in entries for key in PROXY_KEYS)
    source = entries if saved else inherited_proxy_environment()
    proxy = _proxy_values(source)
    mode, port, legacy = _access(entries)
    return {
        "version": _version(data),
        "access": {"mode": mode, "port": port, "urls": _access_urls(port, mode),
                   "legacy_restriction": legacy},
        "proxy": proxy,
        "proxy_source": "saved" if saved else "environment",
        "activation": {"access": "router_restart", "proxy": "new_processes"},
    }


def read_settings() -> dict:
    return _settings(_read(config_path()))


def _serialize(entries: dict[str, str]) -> bytes:
    def quote(value):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    data = ("# Portal network settings; managed by Portal administration.\n" +
            "".join(f"{key}={quote(value)}\n" for key, value in sorted(entries.items()))).encode("utf-8")
    if len(data) > 65536:
        raise ValueError("网络配置内容过长，请缩短代理地址或地址列表")
    return data


def _write(path: Path, data: bytes) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".portal-network-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            # This is a private environment file (proxy URLs may contain secrets).
            # Apply owner-only permissions before bytes, not a chmod-only Windows check.
            file_permissions.set_created_file_permissions(temporary, 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _lock(path: Path):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    return os.fdopen(descriptor, "a+b")


def initialize_allowed_networks(values: list[str]) -> None:
    networks = _networks(values)
    path = config_path()
    with _lock(path) as stream, lock_stream(stream):
        entries = _parse(_read(path))
        if "PORTAL_ALLOWED_NETWORKS" not in entries:
            entries["PORTAL_ALLOWED_NETWORKS"] = ",".join(networks)
            _write(path, _serialize(entries))


def update_settings(payload) -> dict:
    if not isinstance(payload, dict) or set(payload) not in ({"version", "access"}, {"version", "proxy"}):
        raise ValueError("每次保存需要 version 和一组网络设置")
    if not isinstance(payload["version"], str) or not payload["version"]:
        raise ValueError("缺少配置版本，请重新读取")
    if "access" in payload:
        access = payload["access"]
        if not isinstance(access, dict) or set(access) != {"mode", "port"}:
            raise ValueError("访问设置需要范围和端口")
        mode, port = access["mode"], access["port"]
        if mode not in {"local", "remote"} or type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError("访问范围或端口无效")
        changes = {"PORTAL_ACCESS_MODE": mode, "PORTAL_PORT": str(port)}
    else:
        proxy = _proxy(payload["proxy"])
        changes = _proxy_env(proxy)
        changes.update({"PORTAL_NETWORK_" + key: proxy[field] for field, key in PROXY_FIELDS.items()})
    path = config_path()
    with _lock(path) as stream, lock_stream(stream):
        data = _read(path)
        if payload["version"] != _version(data):
            raise VersionConflict("网络设置已由其他操作修改，请重新读取后保存")
        entries = _parse(data)
        if "access" in payload and port != _access(entries)[1]:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    probe.bind(("0.0.0.0" if mode == "remote" else "127.0.0.1", port))
                except OSError as exc:
                    raise ValueError(f"端口 {port} 暂不可用：{exc.strerror or exc}") from exc
        entries.update(changes)
        if "access" in payload:
            entries.pop("PORTAL_ALLOWED_NETWORKS", None)
        # Validate the response before committing so read errors cannot hide a successful write.
        data = _serialize(entries)
        result = _settings(data)
        _write(path, data)
        return result
