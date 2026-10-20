#!/usr/bin/env python3
"""Exercise a live Portal Resident lifecycle through authenticated HTTP APIs."""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from management_common.format_tags import schema_matches
from management_common.json_columns import json_value
from management_common.portal_database import portal_database


ACTIVE_INPUT_STATES = {"queued", "running"}


class _ReadConnection:
    def __init__(self, schema: str) -> None:
        self._context = portal_database(schema=schema).read()
        self._connection = self._context.__enter__()

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        return self._connection.execute(*args, **kwargs)

    def close(self) -> None:
        self._context.__exit__(None, None, None)


def _read_connection(schema: str) -> _ReadConnection:
    return _ReadConnection(schema)


class PortalClient:
    def __init__(self, base_url: str, token: str, *, timeout: float = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = max(1.0, float(timeout))

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={
                "Cookie": f"db_session={self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{method} {path} failed: HTTP {exc.code}: {body}") from exc
        return json.loads(body) if body else {}

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def post(self, path: str, payload: dict[str, Any]) -> Any:
        return self.request("POST", path, payload)


def _query(path: str, **values: str) -> str:
    return path + "?" + urllib.parse.urlencode(values)


def _execute_hatg(
    client: PortalClient,
    instruction: str,
    arguments: dict[str, Any],
    *,
    idempotency_key: str = "",
    expected_version: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Run one HATG ISA instruction and return its result object."""

    call: dict[str, Any] = {
        "schema": "tfi.hatg-instruction.v2",
        "instruction": instruction,
        "arguments": arguments,
    }
    if idempotency_key:
        call["idempotency_key"] = idempotency_key
    if expected_version:
        call["expected_version"] = expected_version
    payload = client.post("/api/instructions/execute", call)
    if not isinstance(payload, dict) or payload.get("status") != "committed":
        raise RuntimeError(f"{instruction} failed: {payload}")
    result = payload.get("result")
    return result if isinstance(result, dict) else {}


def _lineage(client: PortalClient, lineage_id: str) -> dict[str, Any]:
    return _execute_hatg(client, "GetLineage", {"lineage_id": lineage_id}).get("lineage") or {}


def _message_id(payload: Any) -> str:
    if isinstance(payload, list):
        for item in payload:
            nested = _message_id(item)
            if nested:
                return nested
        return ""
    if not isinstance(payload, dict):
        return ""
    direct = str(payload.get("message_id") or "").strip()
    if direct:
        return direct
    for item in payload.values():
        nested = _message_id(item)
        if nested:
            return nested
    return ""


def _revision(lineage: dict[str, Any]) -> int:
    revision = lineage.get("revision")
    if isinstance(revision, bool):
        raise AssertionError(f"Lineage revision is invalid: {revision}")
    try:
        return int(revision)
    except (TypeError, ValueError) as exc:
        raise AssertionError(
            f"Lineage response has no integer revision: {lineage}"
        ) from exc


def _transcript(client: PortalClient, resident_id: str) -> dict[str, Any]:
    return _execute_hatg(client, "GetConversation", {"resident_id": resident_id, "history_limit": 200})


def _input_state(snapshot: dict[str, Any], message_id: str) -> dict[str, Any]:
    for item in snapshot.get("input_states") or []:
        if isinstance(item, dict) and str(item.get("message_id") or "") == message_id:
            return item
    return {}


def _assistant_has_expected(payload: Any, expected: str) -> bool:
    if isinstance(payload, list):
        return any(_assistant_has_expected(item, expected) for item in payload)
    if not isinstance(payload, dict):
        return False
    content_parts = payload.get("content_parts")
    content = str(payload.get("content") or payload.get("text") or "")
    if not content and isinstance(content_parts, list):
        content = "\n".join(
            str(part.get("text") or "")
            for part in content_parts
            if isinstance(part, dict) and part.get("type") == "text"
        )
    role = str(payload.get("role") or "").lower()
    source = str(payload.get("source") or "").lower()
    kind = str(payload.get("kind") or "").lower()
    if expected in content and (
        role in {"assistant", "agent"}
        or source in {"codex_output", "assistant"}
        or kind in {"final_answer", "assistant_message"}
    ):
        return True
    return any(_assistant_has_expected(value, expected) for value in payload.values())


def wait_for_reply(
    client: PortalClient,
    resident_id: str,
    expected: str,
    *,
    label: str = "resident",
    message_id: str = "",
    timeout: int = 240,
) -> str:
    deadline = time.monotonic() + timeout
    last_state = ""
    observed_message_id = message_id
    while time.monotonic() < deadline:
        transcript = _transcript(client, resident_id)
        if not observed_message_id:
            observed_message_id = _message_id(transcript.get("input_states") or [])
        input_state = _input_state(transcript, observed_message_id)
        state = str(input_state.get("state") or "idle")
        if state != last_state:
            print(f"{label} message {observed_message_id or '-'}: {state}", flush=True)
            last_state = state
        if state == "failed":
            raise RuntimeError(
                f"{label} message failed: {input_state.get('error') or input_state}"
            )
        if _assistant_has_expected(transcript, expected) and state not in ACTIVE_INPUT_STATES:
            return observed_message_id
        time.sleep(2)
    raise TimeoutError(
        f"Timed out waiting for {label} reply {expected}; message={observed_message_id}"
    )


def wait_until_resident_idle(client: PortalClient, resident_id: str, timeout: int = 120) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = _transcript(client, resident_id)
        state = snapshot.get("state") if isinstance(snapshot.get("state"), dict) else {}
        if str(state.get("work_state") or "idle") not in ACTIVE_INPUT_STATES:
            return
        time.sleep(2)
    raise TimeoutError(f"Resident did not become idle: {resident_id}")


def execution_proof(
    message_ids: list[str],
) -> list[dict[str, Any]]:
    connection = _read_connection("execution_runtime")
    agent_connection = _read_connection("agent")
    lineage_connection = _read_connection("lineage")
    proofs: list[dict[str, Any]] = []
    for message_id in message_ids:
        completed = lineage_connection.execute(
            "SELECT 1 FROM conversation_input_states WHERE message_id = %s AND state = 'completed' LIMIT 1",
            (message_id,),
        ).fetchone()
        # The input's replies are the trace messages answering it, found in
        # the traces' message projection.
        segment_row = agent_connection.execute(
            "SELECT segment.execution_id FROM chat_message_positions reply "
            "JOIN segments segment ON segment.segment_id = reply.source_id "
            "WHERE reply.reply_to = %s AND reply.source_kind = 'segment' "
            "AND reply.segment_type IN ('model', 'harness') AND segment.execution_id <> '' "
            "ORDER BY segment.finished_at DESC NULLS LAST, segment.segment_id DESC LIMIT 1",
            (message_id,),
        ).fetchone() if completed is not None else None
        execution_id = str(
            segment_row["execution_id"] if segment_row is not None else ""
        )
        if not execution_id:
            raise AssertionError(
                f"Execution relation missing for Resident message: {message_id}"
            )
        row = connection.execute(
            '\n            SELECT execution_id, capability, actor, correlation_id,\n                   request_json, checkpoint_json, result_json, status,\n                   attempts, usage_accounted, acknowledged_at\n              FROM executions\n             WHERE execution_id = %s\n             ORDER BY created_at DESC\n             LIMIT 1\n            ',
            (execution_id,),
        ).fetchone()
        if row is None:
            raise AssertionError(f"Execution missing for Resident message: {message_id}")
        proof = dict(row)
        request = json_value(proof.pop("request_json") or "{}")
        checkpoint = json_value(proof.pop("checkpoint_json") or "{}")
        result = json_value(proof.pop("result_json") or "{}")
        instructions = request.get("system_instructions")
        metadata = request.get("metadata")
        if proof["status"] != "completed":
            raise AssertionError(f"Execution did not complete: {proof}")
        if request:
            if not schema_matches(request.get("schema"), "tfi.execution-request.v8"):
                raise AssertionError(
                    f"Execution used the wrong request contract: {request}"
                )
            if request.get("service_type") != "agent_session":
                raise AssertionError(
                    f"Resident work used the wrong execution service: {request}"
                )
            if not isinstance(instructions, dict):
                raise AssertionError(
                    "Resident work has no materialized system instructions: "
                    f"{request}"
                )
            instruction_content = str(instructions.get("content") or "")
            if '<portal-system-prompt role="resident">' not in instruction_content:
                raise AssertionError(
                    f"Resident work used the wrong upstream instructions: {request}"
                )
            if (
                not isinstance(metadata, dict)
                or metadata.get("business_type") != "resident"
            ):
                raise AssertionError(
                    f"Execution is missing Resident metadata: {request}"
                )
            proof["storage_state"] = "terminal_request_retained"
            proof["service_type"] = request["service_type"]
            proof["provider"] = request["provider"]
            proof["operation"] = request["operation"]
            proof["system_instructions_policy"] = instructions.get(
                "injection_policy"
            )
            proof["request_schema"] = request["schema"]
        else:
            if not str(proof.get("acknowledged_at") or ""):
                raise AssertionError(
                    f"Compacted Execution has no completion acknowledgment: {proof}"
                )
            if int(proof.get("usage_accounted") or 0) != 1:
                raise AssertionError(
                    f"Compacted Execution has not accounted usage: {proof}"
                )
            # Acknowledgement drops the request and checkpoint; the result stays
            # for a reconnecting waiter until terminal retention prunes the row.
            if checkpoint:
                raise AssertionError(
                    f"Acknowledged Execution retained its checkpoint: {proof}"
                )
            if not result:
                raise AssertionError(
                    f"Acknowledged Execution lost its result before retention: {proof}"
                )
            proof["storage_state"] = "acknowledged_compact"
        proofs.append(proof)
    lineage_connection.close()
    agent_connection.close()
    connection.close()
    return proofs


def agent_data_proof(
    source_agent_id: str,
    created_agent_id: str,
) -> dict[str, Any]:
    connection = _read_connection("agent")
    agent_ids = (source_agent_id, created_agent_id)
    executions = [
        dict(row)
        for row in connection.execute(
            "\n            SELECT execution.execution_id,\n                   execution.agent_id,\n                   execution.actor_ref,\n                   execution.provider,\n                   execution.provider_session_ref,\n                   execution.provider_turn_id,\n                   execution.status,\n                   execution.started_at,\n                   execution.finished_at,\n                   execution.usage_json,\n                   COUNT(segment.segment_id) AS segment_count\n              FROM executions AS execution\n              LEFT JOIN segments AS segment\n                ON segment.execution_id = execution.execution_id\n             WHERE execution.agent_id IN (%s, %s)\n               AND execution.provider_turn_id <> ''\n             GROUP BY execution.execution_id\n             ORDER BY execution.started_at\n            ",
            agent_ids,
        )
    ]
    if len(executions) < 3 or any(
        item["status"] != "completed"
        or not item["provider_session_ref"]
        or int(item["segment_count"] or 0) < 2
        or not json_value(item["usage_json"] or "{}")
        for item in executions
    ):
        raise AssertionError(f"Execution facts are incomplete: {executions}")
    missing_sessions = connection.execute(
        '\n        SELECT COUNT(*)\n          FROM executions AS execution\n          LEFT JOIN provider_sessions AS session\n            ON session.provider_session_ref = execution.provider_session_ref\n         WHERE execution.agent_id IN (%s, %s)\n           AND execution.provider_session_ref IS NOT NULL\n           AND session.provider_session_ref IS NULL\n        ',
        agent_ids,
    ).fetchone()[0]
    if int(missing_sessions or 0):
        raise AssertionError("Execution refers to a missing Provider Session")
    inherited_sequence_sharing = connection.execute(
        "\n        SELECT source.trace_sequence_id AS source_sequence_id,\n               target.trace_sequence_id AS target_sequence_id,\n               COUNT(target_member.segment_id) AS target_segment_count,\n               SUM(CASE WHEN source_member.segment_id IS NULL THEN 0 ELSE 1 END)\n                   AS shared_segment_count\n          FROM agents AS source\n          JOIN agents AS target\n            ON target.sequence_kind = 'agent_trace' AND target.agent_id = %s\n          LEFT JOIN sequence_segments AS target_member\n            ON target_member.sequence_id = target.trace_sequence_id\n          LEFT JOIN sequence_segments AS source_member\n            ON source_member.sequence_id = source.trace_sequence_id\n           AND source_member.segment_id = target_member.segment_id\n         WHERE source.sequence_kind = 'agent_trace'\n           AND source.agent_id = %s\n         GROUP BY source.trace_sequence_id, target.trace_sequence_id\n        ",
        (created_agent_id, source_agent_id),
    ).fetchone()
    if (
        inherited_sequence_sharing is None
        or not str(inherited_sequence_sharing["source_sequence_id"] or "")
        or not str(inherited_sequence_sharing["target_sequence_id"] or "")
        or str(inherited_sequence_sharing["source_sequence_id"])
        == str(inherited_sequence_sharing["target_sequence_id"])
        or int(inherited_sequence_sharing["target_segment_count"] or 0) <= 0
        or int(inherited_sequence_sharing["shared_segment_count"] or 0) <= 0
    ):
        raise AssertionError(
            "Agent Trace created by Fork does not reuse canonical Segments: "
            f"{inherited_sequence_sharing}"
        )
    business_fields = connection.execute(
        "\n        SELECT COUNT(*)\n          FROM agents a JOIN sequence_segments m ON m.sequence_id=a.trace_sequence_id JOIN segments s ON s.segment_id=m.segment_id\n         WHERE a.agent_id IN (%s, %s)\n           AND metadata_json LIKE '%%business_type%%'\n        ",
        agent_ids,
    ).fetchone()[0]
    if int(business_fields or 0):
        raise AssertionError("Agent data persisted a business-specific field")
    segment_counts = {
        str(row["segment_type"]): int(row["count"])
        for row in connection.execute(
            '\n            SELECT segment_type, COUNT(*) AS count\n              FROM agents a JOIN sequence_segments m ON m.sequence_id=a.trace_sequence_id JOIN segments s ON s.segment_id=m.segment_id\n             WHERE a.agent_id IN (%s, %s)\n             GROUP BY segment_type\n             ORDER BY segment_type\n            ',
            agent_ids,
        )
    }
    connection.close()
    return {
        "executions": executions,
        "segment_counts": segment_counts,
        "fork_operation_sequence_sharing": dict(inherited_sequence_sharing),
    }


def lineage_state_proof(
    lineage_id: str,
    source_resident_id: str,
    created_resident_id: str,
    created_resident_title: str,
) -> dict[str, Any]:
    connection = _read_connection("lineage")
    lineage_row = connection.execute(
        'SELECT revision, runtime_revision FROM lineages WHERE lineage_id = %s',
        (lineage_id,),
    ).fetchone()
    if lineage_row is None:
        raise AssertionError(f"Lineage definition is missing: {lineage_id}")
    nodes = {}
    for row in connection.execute(
        'SELECT n.node_id,r.title FROM lineage_nodes n JOIN residents r ON r.resident_id=n.node_id WHERE n.lineage_id=%s AND r.deleted_at IS NULL ORDER BY n.node_id',
        (lineage_id,),
    ):
        nodes[str(row["node_id"])] = {"id": row["node_id"], "title": row["title"]}
    if source_resident_id not in nodes or created_resident_id not in nodes:
        raise AssertionError(f"Lineage graph does not contain both Residents: {nodes}")
    if str(nodes[created_resident_id].get("title") or "") != created_resident_title:
        raise AssertionError(
            "Title supplied to Fork was not persisted on the created Resident: "
            f"{nodes[created_resident_id]}"
        )
    resident_ids = {
        str(row["resident_id"])
        for row in connection.execute(
            'SELECT r.resident_id FROM residents r JOIN lineage_nodes n ON n.node_id=r.resident_id WHERE n.lineage_id=%s AND r.deleted_at IS NULL',
            (lineage_id,),
        )
    }
    if not {source_resident_id, created_resident_id}.issubset(resident_ids):
        raise AssertionError(
            f"Lineage Resident records are incomplete: {resident_ids}"
        )
    connection.close()
    return {
        "revision": int(lineage_row["revision"] or 0),
        "runtime_revision": int(lineage_row["runtime_revision"]),
        "resident_records": sorted(resident_ids),
    }


def assert_resident_deleted_from_lineage_state(
    lineage_id: str,
    resident_id: str,
) -> None:
    connection = _read_connection("lineage")
    lineage_row = connection.execute(
        'SELECT 1 FROM lineages WHERE lineage_id = %s',
        (lineage_id,),
    ).fetchone()
    record_count = int(connection.execute(
        'SELECT COUNT(*) FROM residents WHERE resident_id = %s',
        (resident_id,),
    ).fetchone()[0])
    stream_count = int(connection.execute(
        'SELECT COUNT(*) FROM conversation_states WHERE resident_id = %s',
        (resident_id,),
    ).fetchone()[0])
    node_count = int(connection.execute(
        'SELECT COUNT(*) FROM lineage_nodes WHERE lineage_id = %s AND resident_id = %s',
        (lineage_id, resident_id),
    ).fetchone()[0])
    connection.close()
    if lineage_row is None:
        raise AssertionError("Lineage was removed while deleting one Resident")
    if node_count or record_count or stream_count:
        raise AssertionError(
            "Deleted Resident remains in Lineage state: "
            f"node={bool(node_count)}, record={record_count}, "
            f"stream={stream_count}"
        )


def assert_lineage_deleted_from_state(
    lineage_id: str,
    resident_ids: tuple[str, ...],
) -> None:
    connection = _read_connection("lineage")
    identity = connection.execute("SELECT deleted_at FROM lineages WHERE lineage_id=%s", (lineage_id,)).fetchone()
    if identity is None or not identity["deleted_at"]:
        raise AssertionError("Deleted Lineage identity must retain its deletion timestamp")
    live_residents = connection.execute("SELECT COUNT(*) FROM residents r JOIN lineage_nodes n ON n.node_id=r.resident_id WHERE n.lineage_id=%s AND r.deleted_at IS NULL", (lineage_id,)).fetchone()[0]
    lineage_counts = {"live_residents": int(live_residents)}
    placeholders = ",".join("%s" for _ in resident_ids)
    stream_count = int(connection.execute(
        f"SELECT COUNT(*) FROM conversation_states "
        f"WHERE resident_id IN ({placeholders})",
        resident_ids,
    ).fetchone()[0])
    connection.close()
    if any(lineage_counts.values()) or stream_count:
        raise AssertionError(
            f"Deleted Lineage remains in state: {lineage_counts}, "
            f"streams={stream_count}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18086")
    parser.add_argument("--actor", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--label", default="dev")
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args(argv)

    from management_portal import serve_management_portal as portal

    token = portal._issue_session_token(args.actor)
    client = PortalClient(args.base_url, token, timeout=args.timeout)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    marker = stamp.replace("T", "_").replace("Z", "")
    resident_id = ""
    lineage_id = ""
    created_resident_id = ""
    message_ids: list[str] = []
    timings: dict[str, float] = {}
    try:
        started = time.perf_counter()
        created_lineage = _execute_hatg(
            client,
            "CreateLineage",
            {
                "title": f"{args.label} HATG lifecycle smoke {stamp}",
                "project_id": args.project_id,
            },
            idempotency_key=f"smoke:{marker}:create-lineage",
        )
        timings["create_lineage_seconds"] = time.perf_counter() - started
        lineage_id = str(created_lineage.get("lineage_id") or "")
        if not lineage_id:
            raise AssertionError(
                f"CreateLineage did not return lineage_id: {created_lineage}"
            )

        lineage = _lineage(client, lineage_id)
        if str(lineage.get("project_id") or "") != args.project_id:
            raise AssertionError(f"Lineage used the wrong Project: {lineage}")
        started = time.perf_counter()
        created_resident = _execute_hatg(
            client,
            "CreateResident",
            {
                "lineage_id": lineage_id,
                "title": f"{args.label} HATG worker {stamp}",
                "provider": "codex",
            },
            idempotency_key=f"smoke:{marker}:create-resident",
            expected_version={lineage_id: _revision(lineage)},
        )
        resident_id = str(created_resident.get("resident_id") or "")
        if not resident_id:
            raise AssertionError(f"CreateResident did not return a Resident: {created_resident}")
        print(
            f"created Lineage {lineage_id} with Resident {resident_id}",
            flush=True,
        )
        bootstrap_response = _execute_hatg(
            client,
            "DeliverMessage",
            {
                "resident_id": resident_id,
                "message": {
                    "message_id": f"msg_{secrets.token_hex(16)}",
                    "role": "user",
                    "content_parts": [{
                        "type": "text",
                        "text": (
                            f"自动化 {args.label} HATG 冒烟测试 {marker}。"
                            f"只回复 INIT_OK_{marker}，不要创建或修改文件。"
                        ),
                    }],
                },
            },
            idempotency_key=f"smoke:{marker}:start-resident",
        )
        initial_message = wait_for_reply(
            client,
            resident_id,
            f"INIT_OK_{marker}",
            label="main bootstrap",
            message_id=_message_id(bootstrap_response),
            timeout=args.timeout,
        )
        timings["create_resident_and_bootstrap_seconds"] = (
            time.perf_counter() - started
        )
        if not initial_message:
            raise AssertionError("Initial message id was not observed")
        message_ids.append(initial_message)

        started = time.perf_counter()
        main_response = _execute_hatg(
            client,
            "DeliverMessage",
            {
                "resident_id": resident_id,
                "message": {
                    "message_id": f"msg_{secrets.token_hex(16)}",
                    "role": "user",
                    "content_parts": [{
                        "type": "text",
                        "text": f"实时 main 消息测试 {marker}。只回复 MAIN_OK_{marker}。",
                    }],
                },
            },
            idempotency_key=f"smoke:{marker}:deliver-main-input",
        )
        main_message = _message_id(main_response)
        main_message = wait_for_reply(
            client, resident_id, f"MAIN_OK_{marker}", label="main",
            message_id=main_message, timeout=args.timeout,
        )
        if not main_message:
            raise AssertionError("Main message id was not observed")
        message_ids.append(main_message)
        timings["main_turn_seconds"] = time.perf_counter() - started

        lineage = _lineage(client, lineage_id)
        started = time.perf_counter()
        created_resident_title = f"{args.label} HATG Fork result {stamp}"
        fork_response = _execute_hatg(
            client,
            "ForkResident",
            {
                "lineage_id": lineage_id,
                "source_resident_id": resident_id,
                "fork_mode": "recent-5",
                "title": created_resident_title,
            },
            idempotency_key=f"smoke:{marker}:fork-resident",
            expected_version={lineage_id: _revision(lineage)},
        )
        created_resident_id = str(fork_response.get("resident_id") or "")
        if not created_resident_id:
            raise AssertionError(
                f"ForkResident did not return a created Resident ID: {fork_response}"
            )
        print(f"Fork created ordinary Resident {created_resident_id}", flush=True)
        fork_delivery = _execute_hatg(
            client,
            "DeliverMessage",
            {
                "resident_id": created_resident_id,
                "message": {
                    "message_id": f"msg_{secrets.token_hex(16)}",
                    "role": "user",
                    "content_parts": [{
                        "type": "text",
                        "text": (
                            f"实时 HATG fork 消息测试 {marker}。"
                            f"只回复 FORK_OK_{marker}。"
                        ),
                    }],
                },
            },
            idempotency_key=f"smoke:{marker}:start-fork",
        )
        fork_message = _message_id(fork_delivery)
        fork_message = wait_for_reply(
            client, created_resident_id, f"FORK_OK_{marker}", label="fork",
            message_id=fork_message, timeout=args.timeout,
        )
        if not fork_message:
            raise AssertionError("Fork message id was not observed")
        message_ids.append(fork_message)
        timings["fork_and_turn_seconds"] = time.perf_counter() - started

        wait_until_resident_idle(client, created_resident_id, timeout=args.timeout)

        proofs = execution_proof(message_ids)
        agent_proof = agent_data_proof(
            resident_id,
            created_resident_id,
        )
        lineage_proof = lineage_state_proof(
            lineage_id,
            resident_id,
            created_resident_id,
            created_resident_title,
        )

        lineage = _lineage(client, lineage_id)
        started = time.perf_counter()
        _execute_hatg(
            client,
            "DeleteResident",
            {"lineage_id": lineage_id, "resident_id": created_resident_id},
            idempotency_key=f"smoke:{marker}:delete-resident",
        )
        timings["delete_resident_seconds"] = time.perf_counter() - started
        lineage = _lineage(client, lineage_id)
        if any(
            str(item.get("resident_id") or item.get("id") or "")
            == created_resident_id
            for item in lineage.get("nodes") or []
            if isinstance(item, dict)
        ):
            raise AssertionError(
                "Resident created by Fork is still listed in HATG: "
                f"{created_resident_id}"
            )
        assert_resident_deleted_from_lineage_state(
            lineage_id,
            created_resident_id,
        )
        print(f"deleted ordinary Resident {created_resident_id}", flush=True)

        started = time.perf_counter()
        deletion = _execute_hatg(
            client,
            "DeleteLineage",
            {"lineage_id": lineage_id},
            idempotency_key=f"smoke:{marker}:delete-lineage",
            expected_version={lineage_id: _revision(_lineage(client, lineage_id))},
        )
        timings["delete_lineage_seconds"] = time.perf_counter() - started
        assert_lineage_deleted_from_state(
            lineage_id,
            (resident_id, created_resident_id),
        )
        deleted_lineage_id = lineage_id
        lineage_id = ""
        print(json.dumps({
            "lineage_id": deleted_lineage_id,
            "resident_id": resident_id,
            "created_resident_id": created_resident_id,
            "message_ids": message_ids,
            "execution_proof": proofs,
            "agent_data_proof": agent_proof,
            "lineage_state_proof": lineage_proof,
            "lineage_deletion": deletion,
            "timings": timings,
        }, ensure_ascii=False, indent=2), flush=True)
        return 0
    finally:
        if lineage_id:
            try:
                _execute_hatg(
                    client,
                    "DeleteLineage",
                    {"lineage_id": lineage_id},
                    idempotency_key=f"smoke:{marker}:cleanup-lineage",
                )
                print(f"deleted temporary Lineage {lineage_id}", flush=True)
            except Exception as exc:
                print(f"WARNING: temporary Lineage cleanup failed: {exc}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
