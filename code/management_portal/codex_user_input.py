from __future__ import annotations

import copy
import secrets
import threading
from typing import Any, Callable

from management_common.provider_user_input import REQUEST_ITEM, RESULT_ITEM, normalize_request, validate_response
from management_common.provider_interactions import normalize_interaction, wire_response


class CodexUserInputRequests:
    def __init__(self, send: Callable, emit: Callable) -> None:
        self._send = send
        self._emit = emit
        self._lock = threading.RLock()
        self._pending: dict[str, dict[str, Any]] = {}
        self._file_changes: dict[tuple[str, str, str], dict[str, Any]] = {}

    def observe_file_change(self, params: dict[str, Any]) -> None:
        """Keep the original item scope for this connection's matching approval."""
        item = params.get("item")
        if not isinstance(item, dict) or item.get("type") != "fileChange":
            return
        identity = (params.get("threadId"), params.get("turnId"), item.get("id"))
        changes = item.get("changes")
        if not all(isinstance(value, str) and value for value in identity):
            return
        if not isinstance(changes, list) or not all(
            isinstance(change, dict) and isinstance(change.get("path"), str)
            for change in changes
        ):
            return
        with self._lock:
            self._file_changes[identity] = {
                "thread_id": identity[0], "turn_id": identity[1], "item_id": identity[2],
                "changes": copy.deepcopy(changes),
            }

    def accept(self, wire_id: object, params: dict[str, Any], method: str = "") -> None:
        request = normalize_interaction(method, params) if method else normalize_request(params)
        with self._lock:
            if any(entry["wire_id"] == wire_id for entry in self._pending.values()):
                raise ValueError("duplicate pending user input request")
            if method == "item/fileChange/requestApproval":
                scope = self._file_changes.get((request["threadId"], request["turnId"], request["itemId"]))
                if scope is not None:
                    # Keep wire parameters untouched. This separate observation is
                    # saved in the request Segment, never reconstructed from logs
                    # or the file's later contents and never added to the answer.
                    request["interaction"]["file_change"] = copy.deepcopy(scope)
            key = secrets.token_hex(16)
            self._pending[key] = {"wire_id": wire_id, "params": request}
            self._event(key, request, REQUEST_ITEM, {"status": "pending"})

    def _event(self, key: str, params: dict[str, Any], item_type: str, outcome: dict[str, Any]) -> None:
        data = {
            "request_id": key, "thread_id": params["threadId"], "turn_id": params["turnId"],
            "item_id": params["itemId"], "is_blocking": params["isBlocking"],
            "questions": copy.deepcopy(params["questions"]), **outcome,
        }
        if params.get("interaction"):
            data["interaction"] = copy.deepcopy(params["interaction"])
        self._emit("item/started" if item_type == REQUEST_ITEM else "item/completed", {
            "threadId": params["threadId"], "turnId": params["turnId"],
            "item": {"id": f"user-input:{key}:{item_type}", "type": item_type, "user_input": data},
        })

    def respond(self, thread_id: str, turn_id: str, value: dict[str, Any]) -> None:
        with self._lock:
            key = str(value.get("request_id") or "")
            entry = self._pending.get(key)
            if entry is None:
                raise ValueError("user input request has expired or was already answered")
            params = entry["params"]
            if params["threadId"] != thread_id or params["turnId"] != turn_id:
                raise ValueError("user input request belongs to a different turn")
            response = validate_response(value, params["questions"], params.get("interaction"))
            cancelled = response["action"] == "cancel"
            envelope = {"id": entry["wire_id"]}
            if params.get("interaction"):
                envelope.update(wire_response(response, params["interaction"]))
            elif cancelled:
                envelope["error"] = {"code": -32800, "message": "User cancelled input request"}
            else:
                envelope["result"] = {"answers": response["answers"]}
            self._send(envelope)
            self._pending.pop(key)
            self._event(key, params, RESULT_ITEM, {
                "status": "cancelled" if cancelled else "declined" if response["action"] == "decline" else "answered",
                "answers": response.get("answers", {}),
                **({"response": envelope.get("result")} if params.get("interaction") else {}),
            })

    def expire(self, thread_id: str = "", *, turn_id: str = "", wire_id: object = None) -> None:
        with self._lock:
            if wire_id is None:
                for identity in list(self._file_changes):
                    if (not thread_id or identity[0] == thread_id) and (not turn_id or identity[1] == turn_id):
                        self._file_changes.pop(identity)
            for key, entry in list(self._pending.items()):
                params = entry["params"]
                if thread_id and params["threadId"] != thread_id:
                    continue
                if turn_id and params["turnId"] != turn_id:
                    continue
                if wire_id is not None and entry["wire_id"] != wire_id:
                    continue
                self._pending.pop(key)
                self._event(key, params, RESULT_ITEM, {"status": "expired", "answers": {}})

    def blocking(self, thread_id: str) -> bool:
        with self._lock:
            return any(entry["params"]["threadId"] == thread_id and entry["params"]["isBlocking"]
                       for entry in self._pending.values())
