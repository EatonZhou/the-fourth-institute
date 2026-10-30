"""Version stamping for Knowledge writes.

A write advances the version block in a Knowledge item's front matter; a version
recovery proves, from the server's retained content changes, which earlier text
the block continues from. Reading the block (knowledge_version_record) belongs
to source parsing, since the index projects it.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from management_common.format_tags import schema_matches

from knowledge_board_lan.knowledge_source import (
    _version_principal,
    _version_timestamp,
    _yaml_front_matter,
    knowledge_version_record,
)

def _principal_from_actor(actor: str) -> dict[str, str]:
    clean_actor = str(actor or "").strip()
    if clean_actor.startswith(("resident_", "agent_", "Agent_")):
        return {"user": "", "agent": clean_actor}
    return {"user": clean_actor, "agent": ""}


def _yaml_scalar(value: object) -> str:
    return json.dumps(str(value or ""), ensure_ascii=False)


def _replace_frontmatter_block(
    md_text: str,
    key: str,
    replacement: list[str],
) -> str:
    text = str(md_text or "")
    match = re.match(
        r"^(\ufeff?---\r?\n)([\s\S]*?)(\r?\n---(?:\r?\n|$))([\s\S]*)$",
        text,
    )
    if not match:
        return text
    newline = "\r\n" if "\r\n" in match.group(1) else "\n"
    lines = match.group(2).splitlines()
    start = next(
        (
            index
            for index, line in enumerate(lines)
            if re.match(rf"^{re.escape(key)}\s*:", line)
        ),
        None,
    )
    if start is not None:
        end = start + 1
        while end < len(lines) and (
            not lines[end].strip()
            or lines[end].startswith((" ", "\t"))
        ):
            end += 1
        del lines[start:end]
        insert_at = start
    else:
        insert_at = next(
            (
                index + 1
                for index, line in enumerate(lines)
                if re.match(r"^created_at\s*:", line)
            ),
            len(lines),
        )
    lines[insert_at:insert_at] = replacement
    frontmatter = newline.join(lines)
    return (
        match.group(1)
        + frontmatter
        + match.group(3)
        + match.group(4)
    )


def stamp_knowledge_version(
    md_text: str,
    *,
    actor: str = "",
    user: str = "",
    agent: str = "",
    previous_text: str = "",
    creating: bool = False,
    now: str = "",
) -> str:
    """Create or advance the canonical version block in Knowledge Markdown."""

    text = str(md_text or "")
    previous = str(previous_text or "")
    previous_meta = _yaml_front_matter(previous)
    previous_version = (
        previous_meta.get("version")
        if isinstance(previous_meta.get("version"), dict)
        else {}
    )
    current_meta = _yaml_front_matter(text)
    if not current_meta:
        if previous_version:
            raise ValueError(
                "Versioned Knowledge updates require complete Markdown with YAML metadata"
            )
        return text
    current_version = (
        current_meta.get("version")
        if isinstance(current_meta.get("version"), dict)
        else {}
    )
    source_version = previous_version or current_version
    recorded = bool(previous_version)
    try:
        previous_number = max(
            1,
            int(source_version.get("number") or 1),
        )
    except (TypeError, ValueError):
        previous_number = 1
    number = 1 if creating or not recorded else previous_number + 1
    changed_at = str(now or "").strip() or datetime.now(
        timezone.utc
    ).isoformat(timespec="seconds").replace("+00:00", "Z")
    principal = {
        **_principal_from_actor(actor),
        **{
            key: value
            for key, value in {
                "user": str(user or "").strip(),
                "agent": str(agent or "").strip(),
            }.items()
            if value
        },
    }
    created_by = (
        _version_principal(source_version.get("created_by"))
        if source_version
        else (principal if creating else {"user": "", "agent": ""})
    )
    created_at = _version_timestamp(
        source_version.get("created_at")
        or previous_meta.get("created_at")
        or current_meta.get("created_at")
        or changed_at
    )
    block = [
        "version:",
        f"  number: {number}",
        f"  created_at: {_yaml_scalar(created_at)}",
        "  created_by:",
        f"    user: {_yaml_scalar(created_by['user'])}",
        f"    agent: {_yaml_scalar(created_by['agent'])}",
        f"  updated_at: {_yaml_scalar(changed_at)}",
        "  updated_by:",
        f"    user: {_yaml_scalar(principal['user'])}",
        f"    agent: {_yaml_scalar(principal['agent'])}",
    ]
    return _replace_frontmatter_block(text, "version", block)


def _knowledge_version_recovery_source(
    resource_id: str, path: Path, current_text: str, change_ids: object,
) -> str:
    """Use an exact server-retained change chain, never a supplied version floor."""
    from management_portal.resources.content_changes import ContentChangeStore

    if (
        not isinstance(change_ids, list)
        or not 1 <= len(change_ids) <= 8
        or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value)
               for value in change_ids)
        or len(set(change_ids)) != len(change_ids)
    ):
        raise ValueError("version_recovery_change_ids requires 1 to 8 distinct change IDs")
    changes = ContentChangeStore().for_changes(change_ids)
    if len(changes) != len(change_ids):
        raise ValueError("Knowledge version recovery evidence is missing")
    previous_after = None
    high_water = 0
    source = ""
    previous_ordinal = -1
    for row in changes:
        change = row["change"]
        if (
            row["ordinal"] <= previous_ordinal
            or not schema_matches(change.get("schema"), expected="tfi.resource-content-change.v1")
            or change.get("change_id") != row["change_id"]
            or change.get("actor") != row["actor"]
            or change.get("execution_id") != row["execution_id"]
            or change.get("resource_id") != resource_id
            or change.get("address") != resource_id
            or str(change.get("path") or "") != str(path)
        ):
            raise ValueError("Knowledge version recovery evidence has a different scope or order")
        previous_ordinal = row["ordinal"]
        for side in ("before", "after"):
            snapshot = change.get(side)
            if not isinstance(snapshot, dict) or not isinstance(snapshot.get("text"), str):
                raise ValueError("Knowledge version recovery snapshot is invalid")
            text = snapshot["text"]
            if hashlib.sha256(text.encode("utf-8")).hexdigest() != snapshot.get("sha256"):
                raise ValueError("Knowledge version recovery snapshot hash is invalid")
            meta = _yaml_front_matter(text)
            version = knowledge_version_record(text)
            retained_version = snapshot.get("version") or {}
            if (version["recorded"] != retained_version.get("recorded")
                    or version["number"] != retained_version.get("number")):
                raise ValueError("Knowledge version recovery snapshot version is invalid")
            if meta and str(meta.get("kb_id") or "") != resource_id:
                raise ValueError("Knowledge version recovery snapshot identity is invalid")
            if version["recorded"] and version["number"] > high_water:
                high_water, source = version["number"], text
        if previous_after is not None and change["before"]["sha256"] != previous_after:
            raise ValueError("Knowledge version recovery evidence is not a continuous content chain")
        previous_after = change["after"]["sha256"]
    if previous_after != hashlib.sha256(current_text.encode("utf-8")).hexdigest():
        raise ValueError("Knowledge version recovery evidence no longer matches current content")
    if high_water <= knowledge_version_record(current_text)["number"]:
        raise ValueError("Knowledge version recovery requires a proven version regression")
    return source
