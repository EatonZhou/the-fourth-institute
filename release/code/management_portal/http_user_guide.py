"""Public new-user guide: one entrance (/guide) for humans and agents.

The guide text lives in ``docs/guide/<name>.md`` at the repository root, so the
open-source checkout and the running Portal read the same files. Browsers get an
HTML page with one tab per reader; agents get the Markdown itself.
"""
from __future__ import annotations

import hashlib
import html
import re
from pathlib import Path
from urllib.parse import unquote

from markdown_it import MarkdownIt
from management_common.site_config import site_name

GUIDE_ROOT = Path(__file__).resolve().parent.parent / "docs" / "guide"
GUIDES = ("human", "agent", "admin")
GUIDE_TITLES = {"human": "人类用户", "agent": "Agent 用户", "admin": "管理员"}
AGENT_GUIDE_PATH = "/guide/agent.md"
_LANGUAGES = ("zh-CN", "en")
_MARKDOWN_CACHE_CONTROL = "public, max-age=300"
# "agent.md", "./human.md#加入", or an in-page "#anchor".
_GUIDE_LINK = re.compile(r"(?:\./)?(?:(human|agent|admin)\.md)?(?:#(.+))?")


def guide_source(name: str, language: str = "") -> str:
    """The Markdown of guide ``name``; an English edition ``<name>.en.md`` is used when asked for and present."""
    if name not in GUIDES:
        raise KeyError(name)
    source = (GUIDE_ROOT / (f"{name}.en.md" if language == "en" else f"{name}.md")).read_text(encoding="utf-8")
    # A deployment name is literal prose, including Markdown punctuation and
    # entity-looking text. It must not create links, images or formatting.
    name = re.sub(r"([\\`*_{}\[\]()#+.!|>-])", r"\\\1", html.escape(site_name(), quote=False))
    return source.replace("__PORTAL_SITE_NAME__", name)


def guide_index(origin: str = "") -> str:
    """Plain-text index served to agents at /llms.txt and to Markdown requests for /guide."""
    return (
        "# Portal\n\n"
        "> Portal is a workspace where humans and AI agents work together as Users and Residents.\n\n"
        "## New user guides\n\n"
        f"- [Agent guide]({origin}{AGENT_GUIDE_PATH}): join with an invitation, authenticate with a personal token, "
        "and call HATG instructions over HTTP.\n"
        f"- [Human guide]({origin}/guide/human.md): join, sign in, and find your way around the web interface.\n"
        f"- [Administrator guide]({origin}/guide/admin.md): create the first administrator, then set up and personalize the Portal.\n\n"
        "## API\n\n"
        f"- `POST {origin}/api/instructions/execute` with `Authorization: Bearer <personal token>`; "
        "start with the `GetUser` and `ListInstructions` instructions.\n"
    )


def _language(request, parsed) -> str:
    from urllib.parse import parse_qs

    from .localization import request_locale

    asked = (parse_qs(parsed.query).get("lang") or [""])[0]
    if asked in _LANGUAGES:
        return asked
    return "en" if request_locale(request.headers, default="zh-CN") == "en" else "zh-CN"


def _slug(text: str) -> str:
    """GitHub-style heading anchor, so links written for the repository work on the page too."""
    return re.sub(r"[^\w-]+", "", re.sub(r"\s+", "-", text.strip().lower())) or "section"


def _render(name: str, markdown: str) -> tuple[str, list[tuple[str, str]]]:
    """HTML of one guide plus its second-level headings for the page outline.

    Both guides share one page, so every anchor is prefixed with the guide name;
    links to ``human.md`` / ``agent.md`` (with or without a heading anchor) stay on the page.
    """
    engine = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table")
    tokens = engine.parse(markdown)
    outline, used = [], set()
    for index, token in enumerate(tokens):
        if token.type == "inline":
            for child in token.children or []:
                if child.type == "link_open":
                    match = _GUIDE_LINK.fullmatch(str(child.attrGet("href") or ""))
                    if match and (match.group(1) or match.group(2)):
                        target, anchor = match.group(1) or name, unquote(match.group(2) or "")
                        child.attrSet("href", f"#{target}-{anchor}" if anchor else f"#{target}")
            continue
        if token.type != "heading_open" or token.tag not in {"h2", "h3"}:
            continue
        text = tokens[index + 1].content if index + 1 < len(tokens) else ""
        slug = base = _slug(text)
        count = 1
        while slug in used:
            slug, count = f"{base}-{count}", count + 1
        used.add(slug)
        token.attrSet("id", f"{name}-{slug}")
        if token.tag == "h2":
            outline.append((f"{name}-{slug}", text))
    return engine.renderer.render(tokens, engine.options, {}), outline


def render_guide_page(language: str = "zh-CN") -> bytes:
    template = (Path(__file__).with_name("user_guide.html")).read_text(encoding="utf-8")
    tabs, panels = [], []
    for name in GUIDES:
        body, outline = _render(name, guide_source(name, language))
        title = html.escape(GUIDE_TITLES[name])
        tabs.append(
            f'<a class="guideTab" role="tab" href="#{name}" data-guide-tab="{name}">'
            f'<!--portal-i18n:"{title}"-->{title}</a>'
        )
        items = "".join(f'<li><a href="#{slug}">{html.escape(text)}</a></li>' for slug, text in outline)
        source = f"/guide/{name}.md" + ("?lang=en" if language == "en" else "")
        panels.append(
            # No element is named after the tab, so "#agent" switches tabs without the browser scrolling.
            f'<section class="guidePanel" id="guide-{name}" role="tabpanel" data-guide-panel="{name}">'
            f'<nav class="guideOutline" aria-label="{title}"><ol>{items}</ol>'
            f'<a class="guideSource" href="{source}"><!--portal-i18n:"Markdown 原文"-->Markdown 原文</a></nav>'
            f'<article class="guideArticle">{body}</article></section>'
        )
    page = template.replace("<!--GUIDE_TABS-->", "".join(tabs)).replace("<!--GUIDE_PANELS-->", "".join(panels))
    return page.replace('<html lang="zh-CN">', f'<html lang="{language}" data-guide-language="{language}">', 1).encode("utf-8")


def _wants_markdown(request) -> bool:
    accept = request.headers.get("Accept", "").lower()
    return ("text/markdown" in accept or "text/plain" in accept) and "text/html" not in accept


def guide_page(request, parsed, _actor):
    from . import serve_management_portal as portal

    if _wants_markdown(request):
        return portal._serve_bytes(request, 200, guide_index().encode("utf-8"), "text/markdown; charset=utf-8",
                                   cache_control=_MARKDOWN_CACHE_CONTROL)
    return portal._serve_bytes(request, 200, render_guide_page(_language(request, parsed)), "text/html; charset=utf-8")


def guide_markdown(request, parsed, _actor):
    from . import serve_management_portal as portal

    name = Path(parsed.path).name.removesuffix(".md")
    if name not in GUIDES:
        return portal._send_empty(request, 404)
    body = guide_source(name, _language(request, parsed)).encode("utf-8")
    # The ETag tells an agent whether the guide changed since it last read it; a matching If-None-Match gets 304.
    etag = f'"{hashlib.sha256(body).hexdigest()[:16]}"'
    unchanged = etag in [tag.strip() for tag in request.headers.get("If-None-Match", "").split(",")]
    return portal._serve_bytes(request, 304 if unchanged else 200, b"" if unchanged else body, "text/markdown; charset=utf-8",
                               cache_control=_MARKDOWN_CACHE_CONTROL, extra_headers={
                                   "ETag": etag,
                                   "Content-Language": _language(request, parsed),
                                   "Vary": "Accept-Language, Cookie, X-Portal-Language",
                               })


def client_script(request, _parsed, _actor):
    """The standard-library User client, so an agent can fetch it from the Portal it is joining."""
    from . import serve_management_portal as portal

    source = GUIDE_ROOT.parent.parent / "scripts" / "portal_user.py"
    private_source = source.parent.parent / "management_common" / "file_permissions.py"
    body = source.read_text(encoding="utf-8").replace(
        "_BUNDLED_PRIVATE_FILES = None",
        "_BUNDLED_PRIVATE_FILES = " + repr(private_source.read_text(encoding="utf-8")),
        1,
    ).encode("utf-8")
    return portal._serve_bytes(request, 200, body, "text/x-python; charset=utf-8",
                               cache_control=_MARKDOWN_CACHE_CONTROL)


def llms_txt(request, _parsed, _actor):
    from . import serve_management_portal as portal

    return portal._serve_bytes(request, 200, guide_index().encode("utf-8"), "text/plain; charset=utf-8",
                               cache_control=_MARKDOWN_CACHE_CONTROL)
