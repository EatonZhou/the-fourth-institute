"""Route the three approved HTTPS origins through DirtyBase's TLS relays."""
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

_ENABLED = Path("/etc/systemd/system/multi-user.target.wants/db-kimi-direct.service")
_PORTS = {"api.kimi.com": 943, "api.moonshot.cn": 944, "api.deepseek.com": 945}


def transport_url(url: str) -> str:
    origin = urlsplit(url)
    port = _PORTS.get(origin.netloc.lower())
    if origin.scheme != "https" or not port or not _ENABLED.is_file():
        return url
    return urlunsplit(("http", f"127.0.0.1:{port}", origin.path, origin.query, origin.fragment))
