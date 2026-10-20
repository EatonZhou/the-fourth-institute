#!/usr/bin/env python3
"""Prepare the existing Pulse workflow's move to Portal System.

``plan --resident-id ...`` only reads live definitions and prints HATG calls.
The integrator executes those calls and waits for the Resident workspace move.
``copy-history`` validates the file migration; ``--apply`` copies the historical
summaries without deleting the source. Run it only after pausing the four Event
sources and draining the Pulse Resident. No command in this tool mutates a graph.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from management_portal.society_service.lineage_storage import canonical_lineage_folder
from management_portal.portal_pulse_backend import PULSE_MIGRATION_RECEIPT

SOURCE_LINEAGE = "lineage_4374b92c0dd8"
TARGET_LINEAGE = "lineage_ed619225d417"
RESIDENT = "resident_e267c94e7252"
EVENTS = (
    "event_source_6a15828b7c22", "event_source_76e914d4412d",
    "event_source_80080f9bc753", "event_source_95f6bb2015ed",
)
BLOCK_TITLE = "Portal Pulse · 日周月摘要"
PERIODS = {"daily", "weekly", "monthly"}


def _call(instruction: str, arguments: dict, suffix: str) -> dict:
    return {"instruction": instruction, "arguments": arguments,
            "idempotency_key": f"portal-system-pulse-migration:{suffix}"}


def build_plan(source: dict, target: dict) -> dict:
    """Preserve original objects, schedules, queues and paused Event states."""
    if source.get("lineage_id") != SOURCE_LINEAGE or target.get("lineage_id") != TARGET_LINEAGE:
        raise ValueError("Pulse migration requires the original and System Lineages")
    in_source = any(item["id"] == RESIDENT for item in source.get("nodes", []))
    in_target = any(item["id"] == RESIDENT for item in target.get("nodes", []))
    if in_source == in_target:
        raise ValueError("Pulse Resident must belong to exactly one migration endpoint")
    owner = source if in_source else target
    selected = {RESIDENT, *EVENTS}
    components = {item["id"] for item in owner["program"]["components"]}
    if not set(EVENTS) <= components:
        raise ValueError("Pulse's four existing Event sources must move together")
    wires = []
    for item in owner["program"]["wires"]:
        ends = {item["source"], item["target"]}
        if ends & selected:
            if not ends <= selected:
                raise ValueError(f"Pulse has an external wire; review before moving: {item['id']}")
            wires.append(item)
    if len(wires) != 4 or {w["source"] for w in wires} != set(EVENTS):
        raise ValueError("Expected one existing Pulse route per Event")
    runtime = owner.get("program_event_source_runtime", {})
    originally_open = [eid for eid in EVENTS if not runtime.get(eid, {}).get("paused", False)]
    source_root = canonical_lineage_folder(SOURCE_LINEAGE)
    target_root = canonical_lineage_folder(TARGET_LINEAGE).resolve()
    current_workspace = (source_root if in_source else target_root) / "residents" / RESIDENT / "workspace"
    target_workspace = target_root / "residents" / RESIDENT / "workspace"
    old_workspace = f"/share/Projects/AI员工/knowledge_base/agents/{SOURCE_LINEAGE}/residents/{RESIDENT}/workspace"
    old_data = f"/share/Projects/AI员工/knowledge_base/agents/{SOURCE_LINEAGE}/memory/pulse"
    producer = (current_workspace / "tools/pulsetool.py").read_text(encoding="utf-8")
    duty = (current_workspace / "duties/pulse.md").read_text(encoding="utf-8")
    # Exact registered paths only. Producer behavior and publication schema stay intact.
    producer = producer.replace(old_data, str(target_root / "memory/pulse"))
    producer = producer.replace(str(source_root / "memory/pulse"), str(target_root / "memory/pulse"))
    duty = duty.replace(old_workspace, str(target_workspace))
    duty = duty.replace(str(source_root / "residents" / RESIDENT / "workspace"), str(target_workspace))
    update_wires = []
    for wire in wires:
        config = deepcopy(wire["config"])
        prompt = config["invoke_profiles"]["agent"]["arguments"]["prompt"]
        prompt["value"] = prompt["value"].replace(old_workspace, str(target_workspace)).replace(
            str(source_root / "residents" / RESIDENT / "workspace"), str(target_workspace))
        update_wires.append(_call("UpdateWire", {
            "lineage_id": TARGET_LINEAGE, "wire_id": wire["id"], "changes": {"config": config},
        }, wire["id"]))
    blocks = [block for block in target.get("function_blocks", [])
              if selected <= {item["id"] for item in block.get("elements", [])}]
    move = []
    if in_source:
        # Internal wires follow their endpoints; wire IDs are not accepted as object_ids.
        move = [_call("MoveLineageObjects", {"lineage_id": SOURCE_LINEAGE,
            "target_lineage_id": TARGET_LINEAGE, "object_ids": [RESIDENT, *EVENTS]}, "move")]
        move[0]["expected_version"] = {SOURCE_LINEAGE: source["revision"], TARGET_LINEAGE: target["revision"]}
    return {
        "schema": "tfi.portal-pulse-migration.v1",
        "source_lineage_id": SOURCE_LINEAGE, "target_lineage_id": TARGET_LINEAGE,
        "originally_open_events": originally_open,
        "target_workspace": str(target_workspace),
        "pause": [_call("PauseComponent", {"lineage_id": owner["lineage_id"], "component_id": eid, "paused": True},
                        f"pause:{eid}") for eid in originally_open],
        "before_move": "Wait for Pulse Resident idle, empty queue and no pending route deliveries; retain this plan for resume.",
        "move": move,
        "after_move": "Wait for GetMoveOperation.status=completed; verify GetResident.owned_workspace.path equals target_workspace before updating files.",
        "copy_history": {"source": str(source_root / "memory/pulse"),
                         "target": str(target_root / "memory/pulse"),
                         "command": "scripts/portal_pulse_lineage.py copy-history --apply"},
        "workspace_files": [
            _call("UpdateWorkspaceFile", {"address": str(target_workspace / relative), "content": content}, relative)
            for relative, content in (("tools/pulsetool.py", producer), ("duties/pulse.md", duty))
        ],
        "wires": update_wires,
        "function_block": [] if blocks else [_call("Functionalize", {
            "lineage_id": TARGET_LINEAGE, "title": BLOCK_TITLE, "element_ids": [RESIDENT, *EVENTS],
        }, "block")],
        "before_resume": "Activate Pulse backend with System data root; verify GetPulse history and personal publication ownership.",
        "resume": [_call("PauseComponent", {"lineage_id": TARGET_LINEAGE, "component_id": eid, "paused": False},
                         f"resume:{eid}") for eid in originally_open],
    }


def _summary_relative(period: str, key: str) -> Path:
    if period not in PERIODS or not key or Path(key).name != key or key in {".", ".."}:
        raise ValueError("Invalid summary reference in Pulse index")
    return Path("summaries") / period / f"{key}.json"


def copy_history(source: Path, target: Path, *, apply: bool = False) -> dict:
    """Verify all files first; publish the relocated index after all summaries."""
    source, target = source.resolve(), target.resolve()
    if source == target or source in target.parents or target in source.parents:
        raise ValueError("Pulse history endpoints must be separate directories")
    entries = []
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Pulse history contains a symlink: {path.relative_to(source)}")
        if path.is_file():
            relative = path.relative_to(source)
            entries.append((relative, path.read_bytes(), path.stat().st_mode & 0o777))
    if not any(relative == Path("index.json") for relative, body, mode in entries):
        raise ValueError("Pulse source index is missing")
    original_index = next(body for relative, body, mode in entries if relative == Path("index.json"))
    index = json.loads(original_index)
    available = {relative for relative, body, mode in entries}
    for period, key in index.get("latest", {}).items():
        if _summary_relative(period, key) not in available:
            raise ValueError(f"Pulse index points to a missing summary: {period}/{key}")
    for item in index.get("history", []):
        relative = _summary_relative(item["period"], item["key"])
        if relative not in available:
            raise ValueError(f"Pulse history points to a missing summary: {relative}")
        item["path"] = str(target / relative)
    relocated_index = (json.dumps(index, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    desired = [(relative, relocated_index if relative == Path("index.json") else body, mode)
               for relative, body, mode in entries]
    receipt = {"schema": "tfi.portal-pulse-storage-migration.v1",
               "source": str(source), "target": str(target),
               "source_index_sha256": hashlib.sha256(original_index).hexdigest(),
               "target_index_sha256": hashlib.sha256(relocated_index).hexdigest()}
    desired.append((Path(PULSE_MIGRATION_RECEIPT), (json.dumps(receipt, sort_keys=True) + "\n").encode(), 0o600))
    # A later legitimate publication at the destination is never overwritten on retry.
    for relative, body, mode in desired:
        destination = target / relative
        if destination.is_symlink() or not destination.resolve().is_relative_to(target):
            raise ValueError(f"Pulse destination escapes its data root: {relative}")
        if destination.exists() and destination.read_bytes() != body:
            raise FileExistsError(f"Pulse destination differs: {relative}")
    if apply:
        # Changed sources mean the producer was not drained; do not commit an index.
        for relative, body, mode in entries:
            if (source / relative).read_bytes() != body:
                raise RuntimeError("Pulse source changed during migration; stop the producer and retry")
        for relative, body, mode in sorted(desired, key=lambda item: (
            2 if item[0] == Path(PULSE_MIGRATION_RECEIPT) else 1 if item[0] == Path("index.json") else 0)):
            destination = target / relative
            if destination.exists():
                continue
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, temporary = tempfile.mkstemp(dir=destination.parent, prefix=".pulse-migration-")
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(body)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary, mode)
                # Exclusive creation also protects against a concurrent new publication.
                os.link(temporary, destination)
            finally:
                Path(temporary).unlink(missing_ok=True)
    return {"applied": apply, "source": str(source), "target": str(target),
            "files": len(entries), "summary_files": sum(relative.parts[0] == "summaries" for relative in available),
            "latest": index.get("latest", {}),
            "source_index_sha256": hashlib.sha256(original_index).hexdigest(),
            "target_index_sha256": hashlib.sha256(relocated_index).hexdigest()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="Print migration calls; do not execute them")
    plan.add_argument("--resident-id", required=True)
    plan.add_argument("--output", type=Path, help="Save the generated plan, including original open states")
    copy = commands.add_parser("copy-history", help="Validate the historical summaries before copying")
    copy.add_argument("--source", type=Path, default=canonical_lineage_folder(SOURCE_LINEAGE) / "memory/pulse")
    copy.add_argument("--target", type=Path, default=canonical_lineage_folder(TARGET_LINEAGE) / "memory/pulse")
    copy.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "plan":
        from scripts import resident_hatg_instruction as client
        actor = client._resident_id(args.resident_id)
        definitions = []
        for lineage_id in (SOURCE_LINEAGE, TARGET_LINEAGE):
            result = client.execute(actor, {"schema": client.SCHEMA, "instruction": "GetLineage",
                "arguments": {"lineage_id": lineage_id}, "idempotency_key": f"pulse-plan:{uuid.uuid4().hex}"})
            definitions.append(result["result"]["lineage"])
        result = build_plan(*definitions)
    else:
        result = copy_history(args.source, args.target, apply=args.apply)
    if getattr(args, "output", None):
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"plan_path": str(args.output.resolve()), "instruction_calls": sum(
            len(result[key]) for key in ("pause", "move", "workspace_files", "wires", "function_block", "resume"))}))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
