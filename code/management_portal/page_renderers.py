from __future__ import annotations

from pathlib import Path
import re


PORTAL_ROOT = Path(__file__).resolve().parents[1]


def render_knowledge_html(
    *,
    resource_category: str = "knowledge",
    hide_detail_graph: bool = False,
    portal_root: Path = PORTAL_ROOT,
) -> bytes:
    category = str(resource_category or "knowledge").strip().lower()
    if category not in {"knowledge", "skill", "article", "experiment"}:
        raise ValueError(f"unsupported Knowledge resource category: {category}")
    html = (
        Path(portal_root) / "knowledge_board_lan" / "knowledge_board.html"
    ).read_text(encoding="utf-8")
    title = {
        "knowledge": "Knowledge",
        "skill": "Skill",
        "article": "Article",
        "experiment": "Experiment",
    }[category]
    html = re.sub(
        r"<title\b[^>]*>[^<]*</title>",
        f'<title data-portal-site-title=\'["{title}","Resource"]\'>{title} — Resource</title>',
        html,
        count=1,
    )
    html = html.replace(
        'data-portal-nav-active="knowledge"',
        'data-portal-nav-active="resource"',
        1,
    )
    html = html.replace(
        'data-resource-tabs data-resource-category="knowledge"',
        f'data-resource-tabs data-resource-category="{category}"',
        1,
    )
    body_attributes: list[str] = []
    if hide_detail_graph:
        body_attributes.append('data-resource-detail-hide-graph="1"')
    if body_attributes:
        # The page's body tag carries more classes than portalBoardShell
        # (knowledgeInitializing until the board has loaded).
        html = re.sub(
            r"<body\b([^>]*)>",
            lambda match: f'<body{match.group(1)} {" ".join(body_attributes)}>',
            html,
            count=1,
        )
    return html.encode("utf-8")
