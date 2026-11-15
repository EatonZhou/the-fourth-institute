"""HTTP requests reach local processes directly and retain host egress policy."""
import ipaddress
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener


class _LoopbackProxyHandler(ProxyHandler):
    def proxy_open(self, request, proxy, type):
        host = (urlsplit(request.full_url).hostname or '').lower().rstrip('.')
        if host == 'localhost' or host.endswith('.localhost'):
            return None
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
                address = address.ipv4_mapped
            if address.is_loopback:
                return None
        return super().proxy_open(request, proxy, type)


def urlopen(request, *, timeout, follow_redirects=True):
    # Select per request (also after redirects), without changing global urllib
    # state, process environment, VPN settings or public API proxy behavior.
    from urllib.request import HTTPRedirectHandler
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    handlers = [_LoopbackProxyHandler()]
    if not follow_redirects:
        handlers.append(NoRedirect())
    return build_opener(*handlers).open(request, timeout=timeout)
