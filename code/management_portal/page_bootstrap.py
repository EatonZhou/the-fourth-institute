"""Early, dependency-free recovery for Portal document initialization."""

from functools import lru_cache
from html import escape
from html.parser import HTMLParser
import json
from pathlib import Path, PureWindowsPath
import re


_SCRIPT = Path(__file__).parent / "static" / "portal_page_boot.js"
_SITE_SCRIPT = Path(__file__).parent / "static" / "portal_site.js"


@lru_cache(maxsize=1)
def _site_source(mtime_ns: int, size: int) -> bytes:
    return _SITE_SCRIPT.read_bytes()


class _SiteAttributes(HTMLParser):
    def handle_starttag(self, tag, attributes):
        self.attributes = dict(attributes)


def _public_paths() -> dict[str, str]:
    """Project only the native file-preview roots from deployment owners."""
    from management_common import portal_paths
    from management_common.site_config import read_site_config

    def directory(key, value):
        # Validate traversal before resolution. Windows accepts either native
        # separator; the browser receives the resolved path with forward slashes.
        def invalid(text):
            return (not isinstance(text, str) or not text.strip()
                    or re.search(r"[\\\x00-\x1f\x7f-\x9f]", text)
                    or ".." in text.split("/"))

        authored = (value.replace("\\", "/") if isinstance(value, str)
                    and isinstance(portal_paths.PORTAL_ROOT, PureWindowsPath) else value)
        if invalid(authored):
            raise ValueError(f"Invalid public directory: {key}")
        try:
            resolved = portal_paths.resolve_directory(value)
        except (OSError, RuntimeError, ValueError):
            raise ValueError(f"Invalid public directory: {key}") from None
        text = resolved.as_posix()
        if invalid(text) or not resolved.is_absolute() or resolved.parent == resolved:
            raise ValueError(f"Invalid public directory: {key}")
        return text

    deployment = read_site_config().get("deployment", {})
    if not isinstance(deployment, dict):
        raise ValueError("deployment must be an object")
    native_ui = deployment.get("native_ui", {})
    if not isinstance(native_ui, dict):
        raise ValueError("deployment.native_ui must be an object")
    # Read the owner's current raw values once; resolving them here preserves
    # installation-root anchoring while permitting validation before normalization.
    values = portal_paths.path_values()
    return {
        "share_root": directory("share_root", values["share_root"]),
        "staging_root": directory("staging_root", values["staging_root"]),
        "test_results_root": directory("test_results_root", native_ui.get(
            "test_results_root", str(portal_paths.PORTAL_ROOT / "test-results"))),
    }


def _public_compatibility() -> dict:
    """Browser storage and format identifiers are canonical, not site options."""
    return {"browser_identifiers": {}, "format_namespaces": []}


def inject_site_name(data: bytes) -> bytes:
    """Brand explicitly marked Portal documents, including anonymous pages."""
    if b"data-portal-site-title" not in data or b'id="portalSiteScript"' in data:
        return data
    head = re.search(rb"<head\b[^>]*>", data, re.IGNORECASE)
    if head is None:
        return data
    from management_common.site_config import site_name, browser_storage_namespace

    name = site_name()

    def render_field(match):
        opening, tag, _, closing = match.groups()
        if "data-portal-site-title" not in opening and "data-portal-site-name" not in opening:
            return match.group(0)
        attributes = _SiteAttributes()
        attributes.feed(opening)
        attrs = attributes.attributes
        if tag == "title" and "data-portal-site-title" in attrs:
            separator = attrs.get("data-portal-site-separator", " — ")
            prefix = separator.join(json.loads(attrs["data-portal-site-title"]))
            value = prefix + separator + name if prefix else name
        elif "data-portal-site-name" in attrs:
            value = name
        else:
            return match.group(0)
        return opening + escape(value) + closing

    # Only dedicated plain-text fields are replaced; resource titles and user
    # content never undergo a document-wide product-name substitution.
    rendered = re.sub(r"(<(title|a|span)\b[^>]*>)([^<]*)(</\2>)", render_field, data.decode("utf-8"))
    data = rendered.encode("utf-8")
    config = json.dumps({
        "site_name": name, "paths": _public_paths(),
        "storage_namespace": browser_storage_namespace(),
        "compatibility": _public_compatibility(),
    }, ensure_ascii=True)
    for character, escaped in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026")):
        config = config.replace(character, escaped)
    config = config.encode("ascii")
    stat = _SITE_SCRIPT.stat()
    source = _site_source(stat.st_mtime_ns, stat.st_size).replace(b"</script", b"<\\/script")
    script = b'<script id="portalSiteScript">' + source + b"(" + config + b");</script>"
    return data[:head.end()] + script + data[head.end():]


class _BootManifest(HTMLParser):
    def __init__(self):
        super().__init__()
        self.assets = []
        self.checks = []
        self.has_navigation = False

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        url = attrs.get("src") if tag == "script" else (
            attrs.get("href") if tag == "link" and attrs.get("rel") == "stylesheet" else None
        )
        if url and url.startswith("/static/"):
            self.assets.append(url)
        if "data-portal-site-header" in attrs:
            self.has_navigation = True
        if attrs.get("data-portal-boot-required") and attrs.get("id"):
            self.checks.append({"id": attrs["id"], "label": attrs["data-portal-boot-required"]})


@lru_cache(maxsize=1)
def _boot_source(mtime_ns: int, size: int) -> bytes:
    return _SCRIPT.read_bytes()


def inject_page_bootstrap(data: bytes) -> bytes:
    """Brand marked documents; add recovery only to application shells."""
    data = inject_site_name(data)
    if b"/static/portal_theme.js" not in data or b'id="portalPageBootScript"' in data:
        return data
    head = re.search(rb"<head\b[^>]*>", data, re.IGNORECASE)
    if head is None:
        return data
    manifest = _BootManifest()
    manifest.feed(data.decode("utf-8"))
    if not manifest.has_navigation and not manifest.checks:
        return data
    config = json.dumps({
        "assets": list(dict.fromkeys(manifest.assets)),
        "checks": manifest.checks,
        "navigation": manifest.has_navigation,
    }, ensure_ascii=True).replace("<", "\\u003c").encode("ascii")
    stat = _SCRIPT.stat()
    source = _boot_source(stat.st_mtime_ns, stat.st_size).replace(b"</script", b"<\\/script")
    script = b'<script id="portalPageBootScript">(' + source + b")(" + config + b");</script>"
    return data[:head.end()] + script + data[head.end():]
