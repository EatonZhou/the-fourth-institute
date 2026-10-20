"""Usage of the turns Claude Code takes on its own, outside Portal's turns.

After a Portal turn ends, Claude Code can start another turn by itself: a
background task it started finished (``<task-notification>``), another Claude
session sent a message, or a session it watches went idle.  No Execution holds
that turn, so no Worker reports its requests.  The ExecutionService reads them
from the session transcript and counts them in the Resident's hourly usage,
not in any turn's usage.

A request counts here only when it lies outside every Execution of its session
and no Execution reported it.  ``execution_store_meta`` keeps, per session, the
time up to which the transcript was counted.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .claude_transcript_compaction import claude_transcript_path
from .native_options import claude_route_config_dir, native_config_dir
from .provider_turn_usage import epoch_ms, read_claude_turn_usage

LOGGER = logging.getLogger(__name__)

# A turn's last transcript entries are written a moment after its requests.
SETTLE_SECONDS = 120
# Execution usage events live 24 hours after their Execution ends; a request
# older than this is no longer checked against them.
HORIZON_SECONDS = 20 * 3600
# Sessions whose last Execution ended this long ago are not read again.
SESSION_ACTIVITY_SECONDS = 7 * 24 * 3600
# A response's entries can begin before the counted-up-to time.
READ_OVERLAP_SECONDS = 600


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _claude_config_dirs(scheduler: Any, route_id: str, binding_ids: list[str]) -> list[Path]:
    """Where Claude Code keeps the session's transcripts, as the Worker configured it.

    Off a subscription route, the directory follows the runtime directory the
    session's requests named; the latest request still kept tells it.
    """

    try:
        config_dir = claude_route_config_dir(route_id)
    except ValueError:
        return []
    if config_dir is not None:
        return [config_dir]
    runtime_root = scheduler.latest_runtime_root(binding_ids)
    return [native_config_dir("claude", runtime_root, binding) for binding in binding_ids]


def record_self_started_claude_usage(agent_data: Any, scheduler: Any, *, now: datetime | None = None) -> int:
    """Count the requests of Claude Code's own turns since the last pass; returns how many."""

    moment = now or datetime.now(timezone.utc)
    horizon_ms = epoch_ms(_iso(moment - timedelta(seconds=HORIZON_SECONDS)))
    settled_ms = epoch_ms(_iso(moment - timedelta(seconds=SETTLE_SECONDS)))
    counted = 0
    sessions = agent_data.provider_session_executions(
        "claude", active_since=_iso(moment - timedelta(seconds=SESSION_ACTIVITY_SECONDS)),
    )
    for session in sessions:
        session_id = session["backend_session_id"]
        resident_id = session["agent_id"].split(":")[-1]
        key = f"self_started_usage:claude:{session_id}"
        try:
            expected = scheduler.usage_watermark(key)
            # A forked session's transcript starts with copies of its parent's
            # entries, timed before Portal recorded the fork.
            since_ms = max(epoch_ms(expected), horizon_ms, epoch_ms(session["created_at"]))
            windows = [(epoch_ms(start), epoch_ms(end) if end else None) for start, end in session["executions"]]
            unfinished = scheduler.earliest_unfinished_execution(resident_id)
            # Requests after an unfinished Execution was accepted are its own.
            cutoff_ms = min([settled_ms, *(start for start, end in windows if end is None),
                             *([epoch_ms(unfinished)] if unfinished else [])])
            if cutoff_ms <= since_ms:
                continue
            config_dirs = _claude_config_dirs(scheduler, session["backend_route_id"], session["binding_ids"])
            path = next((found for config_dir in config_dirs
                         if (found := claude_transcript_path(config_dir, session_id)) is not None), None)
            if path is None or path.stat().st_mtime * 1000 <= since_ms:
                continue
            read_from = datetime.fromtimestamp(since_ms / 1000, timezone.utc) - timedelta(seconds=READ_OVERLAP_SECONDS)
            observed = read_claude_turn_usage(path, since=_iso(read_from))
            requests = []
            for request in observed.requests if observed is not None else ():
                at = epoch_ms(request.occurred_at)
                if since_ms < at <= cutoff_ms and not any(
                    start <= at and (end is None or at <= end) for start, end in windows
                ):
                    requests.append((request.request_id, request.usage, request.occurred_at))
            counted += scheduler.record_unattributed_token_usage(
                key,
                expected=expected,
                watermark=_iso(datetime.fromtimestamp(cutoff_ms / 1000, timezone.utc)),
                request={
                    "provider": "claude", "service_type": "agent_session",
                    "session": {"backend_route_id": session["backend_route_id"]},
                    "runtime_options": {"provider_profile": session["backend_route_id"]},
                    "metadata": {"resident_id": resident_id},
                },
                requests=requests,
            )
        except Exception:  # noqa: BLE001 - one session never stops the others
            LOGGER.warning("Self-started Claude usage of session %s could not be counted", session_id, exc_info=True)
    return counted
