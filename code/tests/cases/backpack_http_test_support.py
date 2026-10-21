"""Exercise Principal Backpack through the registered HTTP route."""

from urllib.parse import urlparse


def backpack_request(server, request, *, write=False):
    parsed = urlparse(request.path)
    return server._PORTAL_HTTP_ROUTES.dispatch(
        "POST" if write else "GET", parsed.path, request, parsed, "",
        phase="protected",
    )
