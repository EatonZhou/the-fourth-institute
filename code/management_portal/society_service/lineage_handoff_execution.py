"""Handoff attempt and delivery transitions; callers own aggregate commits."""

from __future__ import annotations

from typing import Any
from uuid import uuid4
from contextvars import ContextVar
from contextlib import contextmanager

from management_common.process_observation import process_identity, process_is_alive
from .errors import LineageConflictError

from .lineage_values import _text


_WRITER = ContextVar('handoff_execution_writer', default=None)


class HandoffWriter:
    def __init__(self, session):
        self.session = session
        self.claimed = False
        self.handoff_id = ''
        self.receipt = dict(token=uuid4().hex, process=process_identity(), ended=False)

    def check(self):
        try:
            self.session.execute('SELECT 1')
        except Exception as error:
            raise RuntimeError('Handoff execution ownership lost') from error


@contextmanager
def writer_scope(session):
    from management_portal.resources.creation import creation_authority
    writer = HandoffWriter(session)
    token = _WRITER.set(writer)
    try:
        with creation_authority(writer.check):
            yield writer
    finally:
        _WRITER.reset(token)


def assert_owned():
    writer = _WRITER.get()
    if writer is None:
        raise RuntimeError('Handoff execution ownership missing')
    writer.check()


def check_current_writer(lineage=None):
    writer = _WRITER.get()
    if writer is not None:
        writer.check()
        if lineage is not None and writer.claimed:
            handoff = next((edge for edge in lineage.get('edges') or []
                            if edge.get('id') == writer.handoff_id), None)
            if handoff is None:
                raise RuntimeError('Handoff execution ownership lost')
            for item in handoff.get('items') or []:
                attempts = item.get('attempts') or []
                receipt = (attempts[-1].get('result') or {}).get('_writer', {}) if attempts else {}
                if receipt.get('token') != writer.receipt['token'] or receipt.get('ended') is not False:
                    raise RuntimeError('Handoff execution ownership lost')


def owner_receipt(handoff):
    items = handoff.get('items') or []
    attempts = (items[0].get('attempts') or []) if items else []
    return (attempts[-1].get('result') or {}).get('_writer', {}) if attempts else {}


def claim_writer(handoff):
    writer = _WRITER.get()
    writer.check()
    previous = owner_receipt(handoff)
    if (previous.get('token') != writer.receipt['token'] and
            previous.get('ended') is not True and process_is_alive(previous.get('process', {}))):
        raise LineageConflictError('Handoff writer is active or unknown')
    attach_writer(handoff)


def attach_writer(handoff):
    writer = _WRITER.get()
    writer.handoff_id = handoff['id']
    for item in handoff.get('items') or []:
        if item.get('attempts'):
            item['attempts'][-1].setdefault('result', {})['_writer'] = dict(writer.receipt)
    writer.claimed = True


def begin_attempt(handoff: dict[str, Any], now: str) -> None:
    handoff["status"] = "preparing"
    handoff["error"] = ""
    for item in handoff.get("items") or []:
        attempts = item.get("attempts") if isinstance(item.get("attempts"), list) else []
        attempts.append({
            "number": (attempts[-1]["number"] + 1) if attempts else 1,
            "operation_id": uuid4().hex,
            "status": "preparing",
            "method": str((item.get("transfer") or {}).get("method") or "direct"),
            "started_at": now,
            "completed_at": "",
            "error": "",
            "result": {},
        })
        item["attempts"] = attempts
        item["result"] = {}


def fail_attempt(handoff: dict[str, Any], error: str, now: str) -> None:
    handoff["status"] = "failed"
    handoff["error"] = _text(error, 4000)
    for item in handoff.get("items") or []:
        attempts = item.get("attempts") if isinstance(item.get("attempts"), list) else []
        if attempts and attempts[-1].get("status") == "preparing":
            attempts[-1].update({
                "status": "failed", "completed_at": now, "error": _text(error, 4000),
            })


def complete_attempt(
    handoff: dict[str, Any], prepared: list[tuple[dict[str, Any], dict[str, Any]]], now: str,
) -> None:
    handoff["status"] = "ready"
    handoff["error"] = ""
    handoff["ready_at"] = now
    result_by_id = {str(item.get("item_id") or ""): result for item, result in prepared}
    for item in handoff.get("items") or []:
        result = result_by_id.get(str(item.get("item_id") or ""), {})
        item["result"] = result
        target = item.get("target") if isinstance(item.get("target"), dict) else {}
        target.update({
            "workspace_id": str(result.get("target_workspace_id") or ""),
            "path": str(result.get("target_path") or target.get("path") or ""),
            "scope": str(result.get("target_scope") or target.get("scope") or "."),
        })
        item["target"] = target
        attempts = item.get("attempts") if isinstance(item.get("attempts"), list) else []
        if attempts:
            receipt = attempts[-1].get('result', {}).get('_writer')
            attempts[-1].update({
                "status": "ready", "completed_at": now, "error": "",
                "result": {**result, **({'_writer': receipt} if receipt else {})},
            })


