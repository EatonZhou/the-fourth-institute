"""Script groups: scripts that several pages load together, in one order.

``script_groups.json`` lists each group's scripts in load order; it is the
only place that names them, except that portal_nav.js's lazy Chat loader
repeats the workbench parts (a test keeps the two lists equal).
``portal-foundation`` is what pages load first
(shared state, text, translations, i18n, theme), ``portal-shell`` the page
shell after the services a page chooses (overlay, events, refresh, Resident
protocol, conversations, UI primitives, navigation, UI), ``portal-workbench``
the workbench's part scripts and the ``portal_workbench.js`` that creates
workbenches from them, ``portal-workspace-surface`` the part scripts of the
Preview panel, workspace browser, document sessions, document tabs and
selection controllers and the ``portal_workspace_surface.js`` that creates
them, and ``chat-workspace`` the Chat modules and their entry
script. A page names the group where its scripts belong:

* a document holds ``<script data-portal-script-group="chat-workspace"></script>``,
  and the element's other attributes (``defer``) go to each of the scripts;
* an asset list in a JSON block (a native view's ``web.native_view`` assets)
  holds ``{"script-group": "chat-workspace"}`` among its ``scripts``.

``expand_document`` replaces both with the group's scripts before a page
leaves the server: Portal documents (``_serve_bytes``), native view templates
(``development_native_views``) and the test framework's demo frames, which
inline what they load. Browsers and the page bootstrap see ordinary scripts.
The file is read again when it changes, as the pages themselves are.
"""
from __future__ import annotations

import json
from pathlib import Path
import re


GROUPS_FILE = Path(__file__).with_name("script_groups.json")
MARKER = "script-group"

_ELEMENT = re.compile(r'<script\b(?P<attributes>[^>]*?)\s+data-portal-script-group="(?P<group>[^"]+)"(?P<rest>[^>]*)>\s*</script>')
_JSON_BLOCK = re.compile(r'(?P<open><script\b[^>]*\btype="application/json"[^>]*>)(?P<body>.*?)(?P<close></script>)', re.S)
_loaded: tuple[tuple[int, int], dict[str, tuple[str, ...]]] | None = None


def _groups() -> dict[str, tuple[str, ...]]:
    global _loaded
    stat = GROUPS_FILE.stat()
    version = (stat.st_mtime_ns, stat.st_size)
    if _loaded is None or _loaded[0] != version:
        listed = json.loads(GROUPS_FILE.read_text(encoding="utf-8"))
        _loaded = (version, {name: tuple(sources) for name, sources in listed.items()})
    return _loaded[1]


def scripts(group: str) -> tuple[str, ...]:
    """The scripts of ``group`` in load order; an unknown group is an error."""
    try:
        return _groups()[group]
    except KeyError:
        raise KeyError(f"no script group {group!r} in {GROUPS_FILE.name}") from None


def expand_document(text: str) -> str:
    """``text`` with each group a page names replaced by the group's scripts."""
    if MARKER not in text:
        return text

    def element(match: re.Match[str]) -> str:
        line_start = text.rfind("\n", 0, match.start()) + 1
        indent = text[line_start:match.start()]
        separator = "\n" + (indent if not indent.strip() else "")
        attributes = match["attributes"] + match["rest"]
        return separator.join(f'<script{attributes} src="{source}"></script>' for source in scripts(match["group"]))

    def asset_list(match: re.Match[str]) -> str:
        if MARKER not in match["body"]:
            return match[0]
        assets = json.loads(match["body"])
        listed = assets.get("scripts") if isinstance(assets, dict) else None
        if not isinstance(listed, list):
            return match[0]
        assets["scripts"] = [source for entry in listed for source in (
            scripts(entry[MARKER]) if isinstance(entry, dict) and MARKER in entry else (entry,))]
        return match["open"] + json.dumps(assets, indent=2).replace("</", "<\\/") + match["close"]

    return _JSON_BLOCK.sub(asset_list, _ELEMENT.sub(element, text))
