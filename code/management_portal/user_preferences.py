"""A User's Portal settings and favorite Lineages and Projects."""
from __future__ import annotations

import re

from management_portal import presentation_store


_AGENT_WORKBENCH_LINEAGE_FAVORITE_RE = re.compile(
    r"^lineage:lineage_[0-9a-f]{12}$"
)
_PORTAL_MAX_FAVORITE_LINEAGES = 120


def _portal_bool_preference(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _portal_motion_preference(value: object) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"system", "full", "reduced"} else "system"


def _portal_auto_colors_preference(value: object) -> dict:
    value = value if isinstance(value, dict) else {}
    accent = str(value.get("accent") or "").strip().lower()
    quote_color = str(value.get("quote_color") or "").strip().lower()
    strength = value.get("bubble_intensity")
    # The persisted value is a color, never arbitrary CSS.
    return {
        "accent": accent if re.fullmatch(r"#[0-9a-f]{6}", accent) else "",
        "quote_color": quote_color if re.fullmatch(r"#[0-9a-f]{6}", quote_color) else "",
        "bubble_intensity": int(min(100, max(0, strength)) + .5)
        if type(strength) in (int, float) and float("-inf") < strength < float("inf") else 18,
    }


def _default_portal_user_preferences() -> dict:
    return {
        "appearance": {"motion": "system", "language": "auto"},
        "lineage": {
            "favorite_lineage_ids": [],
        },
        "resource_workspaces": {
            "article": {"last_item_id": ""},
        },
        "onboarding": {"welcome_dismissed": False},
    }


def _limited_preference_string_list(
    value: object,
    *,
    max_entries: int,
    max_item_length: int = 320,
) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_item in value:
        item = str(raw_item or "").strip()[:max_item_length]
        if not item or item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return normalized[-max_entries:]


def _normalize_portal_user_preferences(value: object) -> dict:
    prefs = _default_portal_user_preferences()
    if not isinstance(value, dict):
        return prefs
    appearance = value.get("appearance") if isinstance(value.get("appearance"), dict) else {}
    prefs["appearance"]["motion"] = _portal_motion_preference(appearance.get("motion"))
    from management_portal.localization import normalize_language
    prefs["appearance"]["language"] = normalize_language(appearance.get("language"))
    if "auto_colors" in appearance:
        prefs["appearance"]["auto_colors"] = _portal_auto_colors_preference(appearance["auto_colors"])
    lineage = value.get("lineage") if isinstance(value.get("lineage"), dict) else {}
    prefs["lineage"]["favorite_lineage_ids"] = _limited_preference_string_list(
        lineage.get("favorite_lineage_ids"),
        max_entries=_PORTAL_MAX_FAVORITE_LINEAGES,
        max_item_length=160,
    )
    resource_workspaces = (
        value.get("resource_workspaces")
        if isinstance(value.get("resource_workspaces"), dict)
        else {}
    )
    article_workspace = (
        resource_workspaces.get("article")
        if isinstance(resource_workspaces.get("article"), dict)
        else {}
    )
    last_article_id = str(article_workspace.get("last_item_id") or "").strip()
    prefs["resource_workspaces"]["article"]["last_item_id"] = (
        last_article_id
        if re.fullmatch(r"article_[0-9a-f]{12}", last_article_id)
        else ""
    )
    onboarding = value.get("onboarding") if isinstance(value.get("onboarding"), dict) else {}
    prefs["onboarding"]["welcome_dismissed"] = _portal_bool_preference(onboarding.get("welcome_dismissed"), False)
    return prefs


def _merge_portal_user_preferences(current: dict, patch: object) -> dict:
    next_preferences = _normalize_portal_user_preferences(current)
    patch_preferences = (
        patch.get("preferences")
        if isinstance(patch, dict) and isinstance(patch.get("preferences"), dict)
        else patch
    )
    if not isinstance(patch_preferences, dict):
        return next_preferences
    appearance = (
        patch_preferences.get("appearance")
        if isinstance(patch_preferences.get("appearance"), dict)
        else {}
    )
    if "motion" in appearance:
        next_preferences["appearance"]["motion"] = _portal_motion_preference(
            appearance.get("motion")
        )
    if "language" in appearance:
        from management_portal.localization import normalize_language
        next_preferences["appearance"]["language"] = normalize_language(appearance.get("language"))
    if "auto_colors" in appearance:
        colors = appearance["auto_colors"]
        next_preferences["appearance"]["auto_colors"] = _portal_auto_colors_preference({
            **next_preferences["appearance"].get("auto_colors", {}),
            **(colors if isinstance(colors, dict) else {"accent": "", "quote_color": "", "bubble_intensity": 18}),
        })
    onboarding = patch_preferences.get("onboarding") if isinstance(patch_preferences.get("onboarding"), dict) else {}
    if "welcome_dismissed" in onboarding:
        next_preferences["onboarding"]["welcome_dismissed"] = _portal_bool_preference(
            onboarding.get("welcome_dismissed"), next_preferences["onboarding"]["welcome_dismissed"],
        )
    lineage = (
        patch_preferences.get("lineage")
        if isinstance(patch_preferences.get("lineage"), dict)
        else {}
    )
    if "favorite_lineage_ids" in lineage:
        next_preferences["lineage"]["favorite_lineage_ids"] = (
            _limited_preference_string_list(
                lineage.get("favorite_lineage_ids"),
                max_entries=_PORTAL_MAX_FAVORITE_LINEAGES,
                max_item_length=160,
            )
        )
    resource_workspaces = (
        patch_preferences.get("resource_workspaces")
        if isinstance(patch_preferences.get("resource_workspaces"), dict)
        else {}
    )
    article_workspace = (
        resource_workspaces.get("article")
        if isinstance(resource_workspaces.get("article"), dict)
        else {}
    )
    if "last_item_id" in article_workspace:
        last_article_id = str(article_workspace.get("last_item_id") or "").strip()
        next_preferences["resource_workspaces"]["article"]["last_item_id"] = (
            last_article_id
            if re.fullmatch(r"article_[0-9a-f]{12}", last_article_id)
            else ""
        )
    return next_preferences


def _portal_user_preferences(actor: str) -> dict:
    actor_id = str(actor or "").strip()
    return _normalize_portal_user_preferences(presentation_store.preferences(actor_id)) if actor_id else _default_portal_user_preferences()


def _set_portal_user_preferences(actor: str, patch: object) -> dict:
    actor_id = str(actor or "").strip()
    if not actor_id:
        raise PermissionError("需要登录后修改用户设置")
    return presentation_store.update_preferences(actor_id, lambda current: _merge_portal_user_preferences(current, patch))


def _is_canonical_lineage_favorite(selection_id: object) -> bool:
    value = str(selection_id or "").strip()
    return bool(
        _AGENT_WORKBENCH_LINEAGE_FAVORITE_RE.fullmatch(value)
        or (value.startswith("project:") and 0 < len(value[8:]) <= 256
            and value[8:] == value[8:].strip()
            and not any(ord(char) < 32 for char in value))
    )


def _normalize_lineage_favorites(items: object) -> list[str]:
    if not isinstance(items, list):
        raise ValueError("favorites must be an array")
    normalized: list[str] = []
    represented: set[str] = set()
    for raw_item in items:
        item = str(raw_item or "").strip()
        if not _is_canonical_lineage_favorite(item):
            raise ValueError("favorite address does not match current contract")
        if item in represented:
            continue
        represented.add(item)
        normalized.append(item)
    return normalized


def _lineage_favorite_payload(actor: str) -> dict[str, list[str]]:
    actor_id = str(actor or "").strip()
    return {"favorites": _normalize_lineage_favorites(presentation_store.favorite_ids(actor_id, "lineage")) if actor_id else []}


def _set_lineage_favorite(actor: str, selection_id: str, favorite: bool) -> dict[str, list[str]]:
    actor_id = str(actor or "").strip()
    item_id = str(selection_id or "").strip()
    if not actor_id:
        raise PermissionError("需要登录后操作")
    if not _is_canonical_lineage_favorite(item_id):
        raise ValueError("selection_id must be a canonical lineage or project address")
    return {"favorites": presentation_store.set_favorite(actor_id, "lineage", item_id, favorite)}
