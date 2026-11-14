"""What a Resident's duties put in its memory (decision_13725a7900f6).

A duty may carry a prompt at one of four memory levels:

- ``trigger``: joins the task of each wire in the duty that triggers the holder;
- ``turn``: goes before the message on every turn;
- ``session``: goes with the duty list at session start and after compaction;
- ``on_demand``: is written to ``duty-manuals.md`` in the holder's own
  Workspace; the duty list carries a one-line pointer to it.

The duty list itself is generated from the duty records, so adding, removing
or reordering a duty changes what the Resident is told without anyone
editing a prompt.
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)
LEVEL_LABELS = {"trigger": "触发时", "turn": "每轮", "session": "会话开始和压缩后", "on_demand": "按需读取"}
MANUAL_FILE = "duty-manuals.md"
SESSION_PART = "duty_memory"
TURN_PART = "duty_memory_turn"
PART_LABELS = {SESSION_PART: "职责清单与说明", TURN_PART: "职责约束（每轮）"}


def held_duties(resident_id: str) -> list[dict[str, Any]]:
    from . import lineage_aggregate
    return lineage_aggregate.lineage_catalog_store().held_duties(resident_id)


def manual_path(record: dict[str, Any]) -> Path:
    from . import resident_record_domain
    return resident_record_domain.resident_owned_workspace_path(record) / MANUAL_FILE


def _write_manual(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".duty-manuals-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def materialize(record: dict[str, Any], duties: list[dict[str, Any]]) -> dict[str, str]:
    """The session and every-turn texts for these duties; refreshes the manual file."""
    path = manual_path(record)
    several = len({duty["lineage_id"] for duty in duties}) > 1
    lines, sessions, turns, manuals = [], [], [], []
    for index, duty in enumerate(duties, 1):
        name = duty["description"] or duty["title"]
        memory = duty.get("memory") or {}
        level = memory.get("level")
        note = {
            "trigger": "（具体指示随触发送达）",
            "turn": "（约束每轮附在消息前）",
            "session": "（说明见下）",
            "on_demand": f"（完整说明在 {path} 的“{name}”一节，需要时读取）",
        }.get(level, "")
        where = f" · {duty['lineage_id']}" if several else ""
        lines.append(f"{index}. {name}{note}{where}")
        if level == "session":
            sessions.append(f"## {name}\n{memory['prompt']}")
        elif level == "turn":
            turns.append(f"- {name}：{memory['prompt']}")
        elif level == "on_demand":
            manuals.append(f"## {name}\n\n{memory['prompt']}")
    try:
        if manuals:
            _write_manual(path, "\n\n".join([
                "# 职责说明（按需读取）",
                "本文件由平台根据职责记录生成；修改请在 Resident Menu 的职责页进行，直接编辑会被覆盖。",
                *manuals,
            ]) + "\n")
        elif path.exists():
            path.unlink()
    except OSError as error:
        # The turn still carries the duty list; the pointer names the file to fix.
        LOGGER.warning("Duty manual %s was not refreshed: %s", path, error)
    texts = {}
    if lines:
        texts[SESSION_PART] = "\n\n".join([
            "# 你的职责\n本清单由平台根据职责记录生成，按先后顺序排列；职责在 Resident Menu 的职责页增加、撤下和排序。\n"
            + "\n".join(lines),
            *sessions,
        ])
    if turns:
        texts[TURN_PART] = "职责约束：\n" + "\n".join(turns)
    return texts


def injection_parts(record: dict[str, Any], *, session_placement: str) -> list[Any]:
    from management_portal.execution.contracts import InjectionPart

    resident_id = str(record.get("resident_id") or "")
    duties = held_duties(resident_id) if resident_id else []
    texts = materialize(record, duties)
    parts = []
    if SESSION_PART in texts:
        parts.append(InjectionPart(SESSION_PART, texts[SESSION_PART], "session_start_and_after_compaction",
                                   session_placement))
    if TURN_PART in texts:
        parts.append(InjectionPart(TURN_PART, texts[TURN_PART], "every_turn", "prompt_prefix"))
    return parts


def trigger_prompt(lineage: dict[str, Any], wire_id: str, target_id: str) -> str:
    """The trigger-time prompt of the duty that owns this wire into its holder."""
    for block in lineage.get("function_blocks") or []:
        duty = block.get("duty") or {}
        memory = duty.get("memory") or {}
        if (duty.get("holder_id") == target_id and memory.get("level") == "trigger"
                and any(element.get("id") == wire_id for element in block.get("elements") or [])):
            return f"职责：{duty.get('description') or block.get('title')}\n{memory['prompt']}"
    return ""
