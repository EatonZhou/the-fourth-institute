"""Read the native conversation store for usage and committed compact checkpoints."""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from .native_session_compaction import NativeCompaction, summary_history


def _pb_fields(buf):
    pos, end = 0, len(buf)
    while pos < end:
        tag = shift = 0
        while pos < end:
            b = buf[pos]
            pos += 1
            tag |= (b & 0x7F) << shift
            if not (b & 0x80):
                break
            shift += 7
        field, wtype = tag >> 3, tag & 7
        if wtype == 0:
            val = shift = 0
            while pos < end:
                b = buf[pos]
                pos += 1
                val |= (b & 0x7F) << shift
                if not (b & 0x80):
                    break
                shift += 7
            yield field, wtype, val
        elif wtype == 1:
            pos += 8
        elif wtype == 2:
            ln = shift = 0
            while pos < end:
                b = buf[pos]
                pos += 1
                ln |= (b & 0x7F) << shift
                if not (b & 0x80):
                    break
                shift += 7
            yield field, wtype, buf[pos:pos + ln]
            pos += ln
        elif wtype == 5:
            pos += 4
        else:
            return


def _step_usage(blob):
    """Extract canonical single-request token usage from CortexStepGeneratorMetadata."""
    vals = {}
    for f1, w1, v1 in _pb_fields(blob):
        if f1 == 1 and w1 == 2:
            for f2, w2, v2 in _pb_fields(v1):
                if f2 == 4 and w2 == 2:
                    for f3, w3, v3 in _pb_fields(v2):
                        if w3 == 0:
                            vals[f3] = int(v3)
    uncached = vals.get(2, 0) + vals.get(4, 0)
    cached = vals.get(5, 0)
    thinking = vals.get(9, 0)
    output = vals.get(3, 0) or (vals.get(10, 0) + thinking)
    input_tokens = uncached + cached
    usage = {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "output_tokens": output,
        "reasoning_output_tokens": thinking,
        "total_tokens": input_tokens + output,
    }
    return {k: v for k, v in usage.items() if v > 0}


def _read_steps(env, sid, after_idx=-1):
    """Read per-step usage rows from Antigravity's conversation SQLite database."""
    home = env.get("GEMINI_HOME") if isinstance(env, dict) else ""
    db_path = Path(home) / "antigravity-acp" / "conversations" / f"{sid}.db" if home and sid else None
    if not db_path or not db_path.is_file():
        return after_idx, []
    try:
        with sqlite3.connect(db_path, timeout=1.0) as conn:
            rows = conn.execute(
                "SELECT idx, data FROM gen_metadata WHERE idx > ? ORDER BY idx",
                (after_idx,),
            ).fetchall()
    except sqlite3.Error:
        return after_idx, []
    if not rows:
        return after_idx, []
    steps = [u for _, data in rows if isinstance(data, (bytes, memoryview)) and (u := _step_usage(bytes(data)))]
    return int(rows[-1][0]), steps


def _latest_checkpoint(env, sid):
    """Read the committed Cortex checkpoint, never a model reply or a usage estimate."""
    if not sid or Path(sid).name != sid:
        raise ValueError("Invalid Antigravity session ID")
    db = Path(env["GEMINI_HOME"]) / "antigravity-acp" / "conversations" / f"{sid}.db"
    with closing(sqlite3.connect(db.absolute().as_uri() + "?mode=ro", uri=True, timeout=2)) as conn:
        row = conn.execute("SELECT idx, step_payload FROM steps WHERE step_type=23 AND status=3 "
                           "ORDER BY idx DESC LIMIT 1").fetchone()
    if not row:
        return None
    # CortexStep.checkpoint (30), checkpoint summary (5) and metadata.created_at (5/1).
    fields = {k: v for k, wire, v in _pb_fields(row[1]) if wire == 2}
    checkpoint = dict((k, v) for k, wire, v in _pb_fields(fields[30]) if wire == 2)
    summary = checkpoint[5].decode("utf-8").strip()
    metadata = {k: v for k, wire, v in _pb_fields(fields[5]) if wire == 2}
    timestamp = {k: v for k, wire, v in _pb_fields(metadata[1]) if wire == 0}
    created = datetime.fromtimestamp(timestamp[1] + timestamp.get(2, 0) / 1e9, timezone.utc).isoformat()
    if not summary:
        raise ValueError("Empty Antigravity checkpoint summary")
    return NativeCompaction(f"{sid}:checkpoint:{row[0]}", created, created, summary_history(summary))
