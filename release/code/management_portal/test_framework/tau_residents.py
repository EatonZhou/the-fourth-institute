from __future__ import annotations

import json
import os
import secrets
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from management_common.file_lock import lock_stream

from management_portal.test_framework.compatibility import test_actor

TAU_ACTOR = test_actor()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class TauResidentAssignments:
    """Owns the durable test-actor + module -> Resident slot mapping."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._depth = 0

    @contextmanager
    def _locked(self):
        """Hold the mapping for one read-modify-write, across processes.

        Every Test run executor is its own process and each change rewrites
        the whole mapping, so a thread lock alone let one run's write discard
        a Resident another run had just recorded.  The file lock covers other
        processes; the thread lock and depth keep nested use re-entrant.
        """
        with self._lock:
            if self._depth:
                self._depth += 1
                try:
                    yield
                finally:
                    self._depth -= 1
                return
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with open(self.path.with_suffix(".lock"), "a", encoding="utf-8") as handle:
                with lock_stream(handle):
                    self._depth = 1
                    try:
                        yield
                    finally:
                        self._depth = 0

    def _read(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"schema": "tfi.portal-tau-residents.v1", "assignments": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"TAU Resident 映射无法读取：{self.path}") from exc
        if (not isinstance(payload, dict)
                or payload.get("schema") != "tfi.portal-tau-residents.v1"
                or not isinstance(payload.get("assignments"), dict)):
            raise RuntimeError(f"TAU Resident 映射结构无效：{self.path}")
        return payload

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(self.path)

    @staticmethod
    def _new_assignment(module_id: str, *, changed_by: str = "") -> dict[str, Any]:
        return {
            "actor": TAU_ACTOR,
            "module_id": module_id,
            "slot_id": secrets.token_hex(8),
            "resident_id": "",
            "lineage_id": "",
            "changed_by": changed_by,
            "updated_at": _now_iso(),
        }

    def assignment(self, module_id: str) -> dict[str, Any]:
        if not TAU_ACTOR:
            raise ValueError("Configure PORTAL_TEST_ACTOR before assigning test Residents")
        clean_module = str(module_id or "").strip()
        if not clean_module:
            raise ValueError("TAU Resident 映射必须提供 module_id")
        with self._locked():
            payload = self._read()
            current = payload["assignments"].get(clean_module)
            if not isinstance(current, dict) or not str(current.get("slot_id") or ""):
                current = self._new_assignment(clean_module)
                payload["assignments"][clean_module] = current
                self._write(payload)
            return dict(current)

    def list(self, module_ids: list[str] | tuple[str, ...]) -> list[dict[str, Any]]:
        return [self.assignment(module_id) for module_id in dict.fromkeys(module_ids)]

    def rotate(self, module_id: str, *, changed_by: str, backend_route_id: str = "") -> dict[str, Any]:
        if not TAU_ACTOR:
            raise ValueError("Configure PORTAL_TEST_ACTOR before assigning test Residents")
        clean_module = str(module_id or "").strip()
        if not clean_module:
            raise ValueError("TAU Resident 映射必须提供 module_id")
        with self._locked():
            payload = self._read()
            self._release_assignment(payload['assignments'].get(clean_module), actor=changed_by)
            assignment = self._new_assignment(clean_module, changed_by=changed_by)
            assignment["backend_route_id"] = str(backend_route_id or "").strip()
            payload["assignments"][clean_module] = assignment
            self._write(payload)
            return dict(assignment)

    def replace_unusable_resident(
        self,
        module_id: str,
        *,
        expected_resident_id: str,
        changed_by: str,
    ) -> dict[str, Any]:
        if not TAU_ACTOR:
            raise ValueError("Configure PORTAL_TEST_ACTOR before assigning test Residents")
        clean_module = str(module_id or "").strip()
        clean_resident = str(expected_resident_id or "").strip()
        if not clean_module or not clean_resident:
            raise ValueError("TAU Resident 替换必须提供 module_id 和 expected_resident_id")
        with self._locked():
            payload = self._read()
            current = payload["assignments"].get(clean_module)
            if not isinstance(current, dict):
                replacement = self._new_assignment(clean_module, changed_by=changed_by)
                payload["assignments"][clean_module] = replacement
                self._write(payload)
                return dict(replacement)
            if current.get("resident_id") != clean_resident:
                return dict(current)
            self._release_assignment(current, actor=changed_by)
            replacement = self._new_assignment(clean_module, changed_by=changed_by)
            for field in ("backend_route_id", "runtime_options", "configuration_id"):
                if field in current:
                    replacement[field] = current[field]
            payload["assignments"][clean_module] = replacement
            self._write(payload)
            return dict(replacement)

    @staticmethod
    def _release_assignment(assignment: dict | None, *, actor: str) -> None:
        if assignment and assignment.get('resident_id'):
            from management_portal.resident_handoff import release_resident
            release_resident(actor=actor, resident_id=assignment['resident_id'],
                             lineage_id=assignment['lineage_id'])

    def record_identity(self, module_id: str, *, resident_id: str, lineage_id: str,
                        expected_slot_id: str = "") -> dict[str, Any]:
        with self._locked():
            payload = self._read()
            current = payload["assignments"].get(module_id)
            if expected_slot_id and (current or {}).get("slot_id") != expected_slot_id:
                raise RuntimeError("TAU 执行槽位已更换，旧创建回执不能覆盖新槽位")
            if not isinstance(current, dict) or not str(current.get("slot_id") or ""):
                current = self._new_assignment(module_id)
            current.update({
                "resident_id": str(resident_id or ""),
                "lineage_id": str(lineage_id or ""),
                "updated_at": _now_iso(),
            })
            payload["assignments"][module_id] = current
            self._write(payload)
            return dict(current)

    def configure(self, module_ids: list[str], *, backend_route_id: str, service_tier: str, changed_by: str) -> list[dict[str, Any]]:
        with self._locked():
            payload = self._read()
            configured = []
            for module_id in dict.fromkeys(module_ids):
                current = payload["assignments"].get(module_id)
                if not isinstance(current, dict):
                    current = self._new_assignment(module_id, changed_by=changed_by)
                options = {"service_tier": service_tier}
                if current.get("backend_route_id") != backend_route_id or current.get("runtime_options") != options:
                    current.update({
                        "backend_route_id": backend_route_id,
                        "runtime_options": options,
                        "configuration_id": secrets.token_hex(12),
                        "changed_by": changed_by,
                        "updated_at": _now_iso(),
                    })
                payload["assignments"][module_id] = current
                configured.append(dict(current))
            self._write(payload)
            return configured

    @staticmethod
    def scopes(assignment: dict[str, Any]) -> tuple[str, str]:
        module_id = str(assignment["module_id"])
        slot_id = str(assignment["slot_id"])
        return (
            f"portal-tau:{TAU_ACTOR}:{module_id}:{slot_id}",
            f"portal-tau:{TAU_ACTOR}:{module_id}",
        )

    def registered(self) -> list[dict[str, Any]]:
        with self._locked():
            return [dict(item) for item in self._read()["assignments"].values() if item.get("resident_id")]
