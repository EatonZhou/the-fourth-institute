"""Kimi Code and DSH compactions reach AgentData like Claude Code's.

Both Providers compact a session inside their own process and record it in
their own session files.  Portal reads the newest compaction after every turn
(also one that fails) and records it with the time from which later work stays
after it, so a Fork inherits the Provider's real context.

:module: resident.fork-handoff
:importance: P1
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from management_portal.execution import backends
from management_portal.execution.native_session_compaction import (
    dsh_session_path,
    kimi_wire_path,
    read_dsh_latest_compaction,
    read_kimi_latest_compaction,
)

KIMI_SESSION = "session_4504a143-ddff-4ac4-8351-32684bb50d32"
DSH_SESSION = "dsh-b3d2a92b3102122e334ed8b737ed8d19"


def _write(path: Path, records: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return path


def _kimi_wire(home: Path, compactions: list[dict]) -> Path:
    records = [
        {"type": "metadata", "protocol_version": "1.5", "created_at": "1790776406747"},
        {"type": "context.append_message", "agentId": "main",
         "message": {"role": "user", "content": [{"type": "text", "text": "old task"}]}, "time": "1790776406873"},
        *compactions,
        {"type": "context.append_message", "agentId": "main",
         "message": {"role": "user", "content": [{"type": "text", "text": "later"}]}, "time": "1790776500000"},
    ]
    return _write(home / "sessions" / "wd_workspace_1e88406b14b9" / KIMI_SESSION / "agents" / "main" / "wire.jsonl",
                  records)


def test_kimi_newest_compaction_becomes_one_summary_message(tmp_path) -> None:
    """Kimi Code 与 DSH 的会话压缩记入 AgentData"""
    wire = _kimi_wire(tmp_path, [
        {"type": "context.apply_compaction", "agentId": "main", "summary": "first",
         "compactedCount": 3, "time": "1790776410000"},
        {"type": "context.apply_compaction", "agentId": "sub-1", "contextSummary": "a subagent's own context",
         "compactedCount": 2, "time": "1790776420000"},
        {"type": "context.apply_compaction", "agentId": "main", "contextSummary": "handoff with kept requests",
         "summary": {"role": "assistant", "content": [{"type": "text", "text": "short"}]},
         "count": 5, "time": "1790776430000"},
    ])

    assert kimi_wire_path(tmp_path, KIMI_SESSION) == wire
    assert kimi_wire_path(tmp_path, "../escape") is None
    compaction = read_kimi_latest_compaction(wire)

    assert compaction is not None
    assert compaction.created_at == compaction.retain_from == "2026-09-30T13:53:50.000Z"
    assert compaction.replacement_history == ({
        "type": "message", "role": "user",
        "content": [{"type": "input_text", "text": "handoff with kept requests"}],
    },)
    assert compaction.ref.startswith("kimi:")


def test_kimi_session_without_compaction_has_none(tmp_path) -> None:
    """Kimi Code 与 DSH 的会话压缩记入 AgentData"""
    assert read_kimi_latest_compaction(_kimi_wire(tmp_path, [])) is None


def _dsh_log(state: Path, *, kept: bool) -> Path:
    def message(seq: int, kind: str, text: str, time: int, **extra) -> dict:
        return {"type": kind, "seq": seq, "time": time, "surfaceOp": "append",
                "data": {"role": kind.split("/")[0], "source": {"kind": "user"},
                         "content": [{"type": "text", "text": text}]}, **extra}

    records = [
        {"type": "session", "version": 4, "id": DSH_SESSION, "createdAt": 1790762129682},
        message(1, "system/message", "portal system prompt", 1790762129700),
        message(2, "user/message", "old task", 1790762129711),
        message(3, "assistant/message", "old answer", 1790762130000),
        {"type": "compaction/start", "seq": 4, "time": 1790762140000, "data": {"compactionId": "c1", "turn": None}},
    ]
    if kept:
        records += [
            message(5, "user/message", "recent request kept", 1790762135000),
            message(6, "assistant/message", "recent answer kept", 1790762136000),
        ]
    records += [
        {"type": "compaction/summary", "seq": 7, "time": 1790762141000,
         "data": {"compactionId": "c1", "shadowedTokenCount": 10}},
        {"type": "user/message", "seq": 8, "time": 1790762142000,
         "surfaceOp": {"op": "replace", "startSeq": 2, "endSeq": 3 if kept else 3},
         "data": {"role": "user", "source": {"kind": "compact-checkpoint", "compactionId": "c1"},
                  "content": [{"type": "text", "text": "summary of the old task"}]}},
        {"type": "compaction/end", "seq": 9, "time": 1790762142500, "data": {"compactionId": "c1", "turn": None}},
    ]
    root = state / DSH_SESSION / "sessions" / "--share-workspace--" / DSH_SESSION
    _write(root / "session.v3.jsonl", records[:2])
    return _write(root / "session.v4.jsonl", records)


@pytest.mark.parametrize("kept,retain_from", [
    (True, "2026-09-30T09:55:35.000Z"),   # the recent messages after the replaced range stay
    (False, "2026-09-30T09:55:42.000Z"),  # nothing kept: later work starts at the checkpoint
])
def test_dsh_checkpoint_replaces_its_range_and_keeps_later_messages(tmp_path, kept, retain_from) -> None:
    """Kimi Code 与 DSH 的会话压缩记入 AgentData"""
    log = _dsh_log(tmp_path, kept=kept)

    assert dsh_session_path(tmp_path, DSH_SESSION) == log
    compaction = read_dsh_latest_compaction(log)

    assert compaction is not None
    assert compaction.ref == "dsh:c1"
    assert compaction.created_at == "2026-09-30T09:55:42.000Z"
    assert compaction.retain_from == retain_from
    assert compaction.replacement_history[0]["content"][0]["text"] == "summary of the old task"


class _Recording:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def record_compaction(self, **kwargs) -> None:
        self.calls.append(kwargs)


def test_dsh_backend_records_its_session_compaction(tmp_path, monkeypatch) -> None:
    """Kimi Code 与 DSH 的会话压缩记入 AgentData"""
    from management_portal import dsh_client

    _dsh_log(tmp_path, kept=True)
    monkeypatch.setattr(dsh_client, "STATE", tmp_path)
    context = _Recording()

    backends.DshBackend().record_provider_compaction(
        context=context, backend_session_id=DSH_SESSION,  # type: ignore[arg-type]
        backend_route_id="deepseek_primary", config_dir="",
    )

    assert [(call["provider"], call["provider_compaction_ref"], call["retain_from"]) for call in context.calls] == [
        ("dsh", "dsh:c1", "2026-09-30T09:55:35.000Z"),
    ]


def test_kimi_backend_records_its_session_compaction(tmp_path, monkeypatch) -> None:
    """Kimi Code 与 DSH 的会话压缩记入 AgentData"""
    _kimi_wire(tmp_path, [{"type": "context.apply_compaction", "agentId": "main",
                           "contextSummary": "summary", "compactedCount": 3, "time": "1790776430000"}])
    monkeypatch.setenv("PORTAL_KIMI_CODE_HOME", str(tmp_path))
    context = _Recording()

    backends.KimiBackend().record_provider_compaction(context=context, backend_session_id=KIMI_SESSION)  # type: ignore[arg-type]

    assert [(call["provider"], call["created_at"], call["provider_compaction_ref"][:5]) for call in context.calls] == [
        ("kimi", "2026-09-30T13:53:50.000Z", "kimi:"),
    ]


def test_kimi_turn_that_fails_still_records_its_compaction() -> None:
    """Kimi Code 与 DSH 的会话压缩记入 AgentData"""
    invocation = SimpleNamespace(
        request=SimpleNamespace(runtime_options={}, permission_profile="", cwd="/work", operation="turn",
                                input_media=(), metadata={}, execution_id="execution-kimi",
                                session=SimpleNamespace(binding_id="resident:kimi")),
        session=SimpleNamespace(backend_session_id=KIMI_SESSION, backend_route_id=""),
        recovery_checkpoint={}, system_context="", user_prompt="continue",
        attach_system_context=False, is_new_backend_session=False, harness_tools=(),
    )
    client = SimpleNamespace(run_turn=mock.Mock(side_effect=RuntimeError("Kimi turn failed")))
    with mock.patch.object(backends, "shared_kimi_client", return_value=client), \
            mock.patch.object(backends.KimiBackend, "record_provider_compaction") as record:
        with pytest.raises(RuntimeError, match="Kimi turn failed"):
            backends.KimiBackend().execute(invocation, mock.Mock())  # type: ignore[arg-type]

    record.assert_called_once()
    assert record.call_args.kwargs["backend_session_id"] == KIMI_SESSION
