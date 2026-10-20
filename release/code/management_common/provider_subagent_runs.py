"""The card a subagent run leaves in the Resident's Chat.

A subagent the Resident's model starts works in a window of its own; its steps
are recorded in a Sequence of their own (``subagent_trace``).  The Chat shows
the run once the Resident's model has its result: what kind of subagent, the
task, the result and a short view of its steps.  The card is an artifact of
the Resident's trace, never part of its context.

Besides what it shows at once, a card carries what the Chat offers on demand:
the full task the model gave (``prompt``), the files the run wrote
(``files``), its token use when the Provider reports it (``usage``), when
each step started and ended, and the key of its own trace (``trace_ref``; a
field named ``*_key`` would be masked in the Execution activity), from which
the Chat reads every step (``GetSubagentRun``).

The Provider adapters follow each run through their live events
(``SubagentRuns``) and give the card as a runtime item (``run_item``).
While the run works they also give its running card (``live``) on the
``subagent/progress`` method: the Chat shows it among the turn's live
activity, never as a message.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping

RESULT_CHARS = 4000
STEP_CHARS = 300
STEPS = 40
PROMPT_CHARS = 8000
FILES = 50
# The steps a running card shows: its live activity is sent on every step.
LIVE_STEPS = 6
# The method of a running card's update; it is no item of the turn.
PROGRESS_METHOD = "subagent/progress"
# A bridge's word that it stopped one run (``params.toolUseId``), so the
# run's card ends stopped; Adapters keep it from the turn.
STOPPING_METHOD = "subagent/stopping"


def _clip(text: object, limit: int) -> tuple[str, bool]:
    value = str(text or "").strip()
    return (value, False) if len(value) <= limit else (value[:limit].rstrip() + "…", True)


# The input that names the file a writing tool changes, by Provider.
PATH_KEYS = ("file_path", "path", "notebook_path", "filePath", "filename", "file")
PATCH_FILE_PREFIXES = ("*** Update File:", "*** Add File:", "*** Delete File:", "*** Move to:")


def _writes_files(tool_name: str) -> bool:
    name = tool_name.lower()
    return "read" not in name and any(word in name for word in ("write", "edit", "patch", "replace", "file_change"))


def changed_files(tool_name: object, arguments: object) -> list[str]:
    """The files a tool call writes, from its input: a path key or a patch's file headers."""

    name = str(tool_name or "")
    if not _writes_files(name):
        return []
    if isinstance(arguments, str):
        arguments = {"input": arguments}
    if not isinstance(arguments, Mapping):
        return []
    paths = [str(arguments[key]).strip() for key in PATH_KEYS
             if isinstance(arguments.get(key), str) and str(arguments[key]).strip()][:1]
    for value in arguments.values():
        if isinstance(value, str) and "*** " in value:
            paths += [line.split(":", 1)[1].strip() for line in value.splitlines()
                      if line.startswith(PATCH_FILE_PREFIXES) and line.split(":", 1)[1].strip()]
    # A Codex file change lists its files under ``changes``.
    changes = arguments.get("changes")
    if isinstance(changes, list):
        paths += [str(change["path"]).strip() for change in changes
                  if isinstance(change, Mapping) and isinstance(change.get("path"), str) and change["path"].strip()]
    return list(dict.fromkeys(paths))


def _files(items: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The files the run wrote, in the order it first wrote them, with the tools and how often."""

    files: dict[str, dict[str, Any]] = {}
    for item in items:
        if item.get("kind") != "tool" or item.get("error"):
            continue
        for path in changed_files(item.get("tool_name"), item.get("input")):
            entry = files.setdefault(path, {"path": path, "tools": [], "count": 0})
            entry["count"] += 1
            if item.get("tool_name") not in entry["tools"]:
                entry["tools"].append(str(item.get("tool_name") or ""))
    return list(files.values())[:FILES]


def _usage(value: object) -> dict[str, int]:
    """Token counts a Provider reported for a run, without unknown or negative ones."""

    if not isinstance(value, Mapping):
        return {}
    counts = {}
    for key in ("total_tokens", "input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens", "tool_uses"):
        try:
            number = int(value.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if number > 0:
            counts[key] = number
    return counts


def _step(item: Mapping[str, Any], *, live: bool = False) -> dict[str, Any] | None:
    kind = str(item.get("kind") or "")
    if kind == "message":
        text, _ = _clip(item.get("text"), STEP_CHARS)
        if not text:
            return None
        return {"kind": "message", "text": text, **({"at": item["at"]} if item.get("at") else {})}
    if kind == "tool":
        summary = item.get("input")
        if isinstance(summary, Mapping):
            summary = next(
                (summary[key] for key in ("command", "file_path", "pattern", "path", "url", "query", "description", "prompt")
                 if isinstance(summary.get(key), str) and summary[key].strip()),
                "",
            )
        text, _ = _clip(summary if isinstance(summary, str) else "", STEP_CHARS)
        step = {
            "kind": "tool", "tool_name": str(item.get("tool_name") or "tool"), "text": text,
            "error": bool(item.get("error")),
        }
        for moment in ("started_at", "finished_at"):
            if item.get(moment):
                step[moment] = str(item[moment])
        if live and item.get("running"):
            step["running"] = True
        return step
    return None


def run_item(run: Mapping[str, Any], *, turn_id: str = "", live: bool = False) -> dict[str, Any]:
    """The runtime item that puts a finished run's card in the Chat; ``live`` for its running card."""

    items = list(run.get("items") or [])
    steps = [step for step in (_step(item, live=live) for item in items) if step is not None]
    result, truncated = _clip(run.get("result"), RESULT_CHARS)
    prompt, prompt_truncated = _clip(run.get("prompt"), PROMPT_CHARS)
    card = {
        "tool_use_id": str(run.get("tool_use_id") or ""),
        "trace_ref": str(run.get("trace_ref") or run.get("tool_use_id") or ""),
        "end_id": str(run.get("end_id") or ""),
        "turn_id": str(turn_id or ""),
        "agent_type": str(run.get("agent_type") or ""),
        "description": str(run.get("description") or ""),
        "background": bool(run.get("background")),
        "status": str(run.get("status") or ""),
        "model": str(run.get("model") or ""),
        "started_at": str(run.get("started_at") or ""),
        "finished_at": str(run.get("finished_at") or ""),
        "result": result,
        "result_truncated": truncated,
        "prompt": prompt,
        "prompt_truncated": prompt_truncated,
        "files": _files(items),
        "usage": _usage(run.get("usage")),
        "steps": steps[-(LIVE_STEPS if live else STEPS):],
        "steps_total": len(steps),
    }
    return {
        "type": "subagentRun",
        "id": "subagent-run:" + card["tool_use_id"] + ":" + card["end_id"],
        "run": card,
    }


# Every step of a run as the Chat reads it from the run's own trace.
TRACE_TEXT_CHARS = 6000
TRACE_INPUT_CHARS = 4000
# What the subagent said, its answer above all, reads whole (copied or saved from the Chat).
TRACE_MESSAGE_CHARS = 20000


def _clip_value(value: Any, limit: int) -> Any:
    """``value`` with each long string cut to ``limit`` characters."""

    if isinstance(value, str):
        return _clip(value, limit)[0] if len(value) > limit else value
    if isinstance(value, Mapping):
        return {str(key): _clip_value(item, limit) for key, item in value.items()}
    if isinstance(value, list):
        return [_clip_value(item, limit) for item in value[:200]]
    return value


def _segment_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, Mapping):
        text = content.get("text")
        return "\n".join(str(part) for part in text) if isinstance(text, list) else str(text or "")
    if isinstance(content, list):
        return "\n".join(str(part.get("text") or "") for part in content if isinstance(part, Mapping))
    return ""


# Where a tool result keeps its output: Codex keeps a command's in ``aggregatedOutput``.
TOOL_OUTPUT_KEYS = ("output", "aggregatedOutput", "stdout", "content", "text")


def _tool_output(result: Any) -> str:
    output = result
    if isinstance(result, Mapping):
        output = next((result[key] for key in TOOL_OUTPUT_KEYS if result.get(key) not in (None, "", [])), None)
    if isinstance(output, list):
        return "\n".join(str(part.get("text") or "") if isinstance(part, Mapping) else str(part) for part in output)
    if isinstance(output, Mapping):
        return json.dumps(output, ensure_ascii=False)
    return str(output or "")


def trace_steps(segments: list[Mapping[str, Any]], *, offset: int = 0) -> list[dict[str, Any]]:
    """The steps of a run's own trace (``subagent_trace`` Segments), for its full timeline.

    ``input`` is the task as the subagent received it, ``tool`` a call with its
    whole input and output, ``message`` what it said (``final`` its answer)
    and ``thinking`` its reasoning; what the Provider injected is left out.
    Each step's ``index`` is its Segment's place in the trace (``offset`` is
    the first one's), so a reader that follows a run reads on from there.
    """

    steps: list[dict[str, Any]] = []
    for index, segment in enumerate(segments, start=offset):
        kind = str(segment.get("segment_type") or "")
        meta = segment.get("metadata") if isinstance(segment.get("metadata"), Mapping) else {}
        content = segment.get("content")
        moments = {name: str(segment[name]) for name in ("started_at", "finished_at") if segment.get(name)}
        at = str(segment.get("finished_at") or segment.get("started_at") or "")
        if kind == "tool" and isinstance(content, Mapping):
            arguments = content.get("input")
            if isinstance(arguments, Mapping) and set(arguments) == {"input"}:
                arguments = arguments["input"]
            output, truncated = _clip(_tool_output(content.get("result")), TRACE_TEXT_CHARS)
            error = content.get("error")
            steps.append({
                "index": index, "kind": "tool", "tool_name": str(content.get("tool_name") or meta.get("tool_name") or "tool"),
                "input": _clip_value(arguments, TRACE_INPUT_CHARS), "output": output, "output_truncated": truncated,
                "error": bool(error) or segment.get("status") == "failed",
                **({"error_text": _clip(error if isinstance(error, str) else json.dumps(error, ensure_ascii=False), 1000)[0]}
                   if error and not isinstance(error, bool) else {}),
                "status": str(segment.get("status") or ""), "files": changed_files(content.get("tool_name"), arguments),
                **moments,
            })
        elif kind == "model":
            text, truncated = _clip(_segment_text(content), TRACE_MESSAGE_CHARS)
            if not text:
                continue
            reasoning = meta.get("kind") == "reasoning"
            steps.append({
                "index": index, "kind": "thinking" if reasoning else "message", "text": text, "text_truncated": truncated, "at": at,
                **({"final": True} if meta.get("kind") == "final_answer" else {}),
            })
        elif kind == "input":
            text, truncated = _clip(_segment_text(content), PROMPT_CHARS)
            if text:
                steps.append({"index": index, "kind": "input", "text": text, "text_truncated": truncated, "at": at})
    return steps


def content_part(item: dict[str, Any], execution_id: str) -> dict[str, Any]:
    run = copy.deepcopy(item["run"])
    identity = hashlib.sha256(
        f"subagent-run:{execution_id}:{run['tool_use_id']}:{run.get('end_id') or ''}".encode()
    ).hexdigest()[:24]
    return {"type": "subagent_run", "execution_id": execution_id, "segment_id": "segment_" + identity, **run}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class SubagentRuns:
    """The runs of the subagents a Resident's model started, from live events.

    ``start`` when the model's call starts a subagent, ``observe`` each item
    of the subagent's own steps, ``finish`` when the model has the result:
    it returns the card's runtime item.  A run the model resumes or that
    notifies again ends again, with the steps since its last end.  ``live``
    after ``start`` and each ``observe`` gives the running card when it
    changed.
    """

    def __init__(self) -> None:
        self._runs: dict[str, dict[str, Any]] = {}

    def start(self, key: str, *, tool_use_id: str, agent_type: str = "", description: str = "",
              background: bool = False, model: str = "", prompt: str = "", trace_ref: str = "") -> None:
        """A run the model started; ``trace_ref`` is its steps' trace key when that differs from ``key``."""

        self._runs[key] = {
            "tool_use_id": tool_use_id, "agent_type": agent_type, "description": description,
            "background": background, "model": model, "prompt": prompt, "trace_ref": trace_ref or key,
            "started_at": _now(), "items": [], "calls": {}, "usage": {}, "running": True,
        }

    def knows(self, key: str) -> bool:
        return key in self._runs

    def forget(self, key: str) -> None:
        """A run that cannot end again (it was stopped)."""

        self._runs.pop(key, None)

    def describe(self, key: str, **fields: object) -> None:
        """What the Provider says about a run after it started (its kind, task, prompt, model or trace)."""

        run = self._runs.get(key)
        if run is not None:
            run.update({name: value for name, value in fields.items()
                        if name in {"agent_type", "description", "model", "prompt", "trace_ref"} and value})

    def report_usage(self, key: str, usage: Mapping[str, Any]) -> None:
        """The run's token use so far, as the Provider reports it (a running total)."""

        run = self._runs.get(key)
        if run is not None:
            run["usage"] = {**run.get("usage", {}), **_usage(usage)}

    def in_background(self, key: str) -> None:
        """The Provider said the run went on in the background (its call did not say)."""

        run = self._runs.get(key)
        if run is not None:
            run["background"] = True

    def mark_running(self, key: str, running: bool = True) -> None:
        """Whether the run's work goes on, as the Provider reports its task (started again, ended)."""

        run = self._runs.get(key)
        if run is not None:
            run["running"] = running

    def ran_in_background(self, key: str) -> bool:
        """Whether the run went on in the background (its call returned before it ended)."""

        return bool(self._runs.get(key, {}).get("background"))

    def running_in_background(self) -> list[str]:
        """The runs that went on in the background and whose work has not ended."""

        return [key for key, run in self._runs.items() if run.get("background") and run.get("running")]

    def observe(self, key: str, item: Mapping[str, Any], *, model: str = "") -> None:
        """One completed or started item of the subagent's steps (Provider item shape)."""

        run = self._runs.get(key)
        if run is None:
            return
        if model:
            run["model"] = model
        kind = item.get("type")
        moment = str(item.get("timestamp") or "") or _now()
        if kind == "toolCall":
            call = str(item.get("id") or "")
            step = run["calls"].get(call) if call else None
            if step is None:
                step = {"kind": "tool", "tool_name": "tool", "input": None, "error": False, "started_at": moment}
                run["items"].append(step)
                if call:
                    run["calls"][call] = step
            if item.get("name"):
                step["tool_name"] = str(item["name"])
            if item.get("input") is not None:
                step["input"] = item["input"]
            if item.get("error") or item.get("status") == "failed":
                step["error"] = True
            # A call runs until its result: Claude's call has no status, Kimi's says running.
            step["running"] = str(item.get("status") or "running") in {"running", "inProgress", "in_progress"}
            if not step["running"]:
                step.setdefault("finished_at", moment)
        elif kind == "agentMessage" and str(item.get("text") or "").strip():
            run["items"].append({"kind": "message", "text": str(item["text"]), "at": moment})

    def live(self, key: str, *, turn_id: str = "") -> dict[str, Any] | None:
        """The running card of a run the model waits on; ``None`` when it did not change since the last one."""

        run = self._runs.get(key)
        if run is None:
            return None
        card = run_item({**run, "status": "running", "result": "", "end_id": "live"}, turn_id=turn_id, live=True)
        seen = json.dumps(card["run"], sort_keys=True, ensure_ascii=False)
        if run.get("live_seen") == seen:
            return None
        run["live_seen"] = seen
        return card

    def finish(self, key: str, *, status: str = "completed", result: object = None, end_id: str = "",
               turn_id: str = "") -> dict[str, Any] | None:
        """The card of a run whose result the model now has; ``None`` for a run never started here.

        Without a result of its own the run's last message is its result.
        """

        run = self._runs.get(key)
        if run is None:
            return None
        items, run["items"], run["calls"], run["live_seen"], run["running"] = run["items"], [], {}, "", False
        usage, run["usage"] = run.get("usage", {}), {}
        if not str(result or "").strip():
            result = next((item["text"] for item in reversed(items) if item["kind"] == "message"), "")
        card = run_item({
            **run, "items": items, "status": status, "result": result, "usage": usage,
            "end_id": end_id or key, "finished_at": _now(),
        }, turn_id=turn_id)
        run["started_at"] = _now()
        return card
