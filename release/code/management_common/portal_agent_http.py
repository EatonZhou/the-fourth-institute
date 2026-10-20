"""HTTP transport for authenticated Portal agent APIs.

Local control-plane traffic must not depend on a provider's proxy environment.
Do not install a global opener or change the process environment: public Portal
URLs retain urllib's configured proxy policy, and provider traffic is untouched.
"""

from __future__ import annotations

import ipaddress
import urllib.parse
import urllib.request


_DIRECT_NETWORKS = tuple(map(ipaddress.ip_network, (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    "169.254.0.0/16", "::1/128", "fc00::/7", "fe80::/10",
)))


def _is_local_url(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return any(address in network for network in _DIRECT_NETWORKS)


class _PortalProxyHandler(urllib.request.ProxyHandler):
    def proxy_open(self, request, proxy, type):
        if _is_local_url(request.full_url):
            return None
        return super().proxy_open(request, proxy, type)


class _NoAPIRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        # RPC endpoints do not redirect. Never forward bearer/context headers
        # or cookies to another endpoint (or a public proxy) after a redirect.
        return None


def urlopen(request: urllib.request.Request, *, timeout: float):
    """Open one Portal API request without proxying private IPs/loopback."""
    if urllib.parse.urlsplit(request.full_url).scheme not in {"http", "https"}:
        raise ValueError("Portal agent API requires an HTTP(S) URL")
    opener = urllib.request.build_opener(_PortalProxyHandler(), _NoAPIRedirect())
    return opener.open(request, timeout=timeout)
