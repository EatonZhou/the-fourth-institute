"""What Resident discovery reads from SocietyService, as the ``portal`` service actor."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .evidence import SUMMARY_DUTY, SUMMARY_DUTY_REF, messages_missing_state, project_conversation_messages

SERVICE_ACTOR = "portal"
_ON_PAGE_ABSENCE_CHECKS = 2


class LineageResidentSource:
    """Read-only adapter from SocietyService domains into discovery documents."""

    def __init__(self, service: Any = None, *, service_actor: str = SERVICE_ACTOR) -> None:
        self._service = service
        self.service_actor = service_actor

    @property
    def service(self) -> Any:
        if self._service is None:
            from management_portal.society_service import get_society_service

            self._service = get_society_service()
        return self._service

    def discover(self) -> list[dict[str, Any]]:
        payload = self.service.lineage.list_resident_discovery_catalog(self.service_actor)
        residents = payload.get("residents") if isinstance(payload, dict) else []
        eligible = self.external_resident_ids([r["resident_id"] for r in residents or []])
        global_writers = self.global_summary_resident_ids([r["resident_id"] for r in residents or []])
        for record in residents or []:
            record["discovery_visible"] = record["resident_id"] in eligible
            record["global_summary_managed"] = record["resident_id"] in global_writers
        return sorted(
            [dict(item) for item in residents or [] if isinstance(item, dict)],
            key=lambda item: str(item.get("resident_id") or ""),
        )

    def removed_resident_ids(self, resident_ids: list[str]) -> set[str]:
        """Those of ``resident_ids`` that are deleted, in a deleted Lineage or gone."""
        payload = self.service.lineage.list_removed_discovery_residents(self.service_actor, list(resident_ids))
        return set(payload.get("resident_ids") or []) if isinstance(payload, dict) else set()

    def conversation_snapshot(self, resident_id: str) -> dict[str, Any]:
        payload = self.service.resident.get_resident_conversation_snapshot(resident_id, self.service_actor)
        if not isinstance(payload, dict):
            raise TypeError("conversation snapshot must be an object")
        # A bounded history page can start in the middle of a completed turn.
        # Recover its authoritative state through the same authorized API.
        payload = dict(payload)
        states = payload.get("input_states")
        stateless: set[str] = set()
        if isinstance(states, list) and isinstance(payload.get("messages"), list):
            states = list(states)
            on_page, off_page = messages_missing_state(payload["messages"], states, resident_id)
            for input_id in sorted(off_page):
                try:
                    states.append(self._input_state(resident_id, input_id))
                except KeyError:
                    stateless.add(input_id)  # No state exists for it.
            # Spot-check the page's own absences instead of reading each one:
            # a snapshot that omits an existing state is a contract failure,
            # never a reason to summarize with less evidence.
            for message_id in on_page[:_ON_PAGE_ABSENCE_CHECKS]:
                try:
                    self._input_state(resident_id, message_id)
                except KeyError:
                    continue
                raise ValueError("Conversation snapshot omitted an existing input state")
            stateless.update(on_page)
            payload["input_states"] = states
        messages = project_conversation_messages(payload, resident_id=resident_id, stateless_message_ids=stateless)
        messages = self._without_summary_duty_runs(resident_id, messages)
        projected = {
            "resident_id": str(payload.get("resident_id") or resident_id),
            "cursor": int(payload.get("cursor") or 0),
            "messages": messages,
            "session_state": payload.get("state") if isinstance(payload.get("state"), dict) else {},
        }
        projected["fingerprint"] = hashlib.sha256(
            json.dumps(projected, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return projected

    def external_resident_ids(self, resident_ids: list[str]) -> set[str]:
        """Only standalone Residents and outer facades enter global discovery."""
        from ..state_store import get_lineage_data_store
        with get_lineage_data_store().database.read_current() as connection:
            rows = connection.execute("""SELECT DISTINCT fr.resident_id FROM family_residents fr
                JOIN families f USING(family_id) WHERE fr.resident_id=ANY(%s)
                AND f.deleted_at IS NULL AND (fr.role<>'facade' OR f.personal_ama_id<>'')""", (resident_ids,)).fetchall()
        return set(resident_ids) - {r["resident_id"] for r in rows}

    def global_summary_resident_ids(self, resident_ids: list[str]) -> set[str]:
        """Family members and facades already have their own summary writers."""
        from ..state_store import get_lineage_data_store
        with get_lineage_data_store().database.read_current() as connection:
            rows = connection.execute("""SELECT DISTINCT fr.resident_id FROM family_residents fr
                JOIN families f USING(family_id) WHERE fr.resident_id=ANY(%s)
                AND f.deleted_at IS NULL""", (resident_ids,)).fetchall()
        return set(resident_ids) - {r["resident_id"] for r in rows}

    def personal_assistant_resident_ids(self, resident_ids: list[str]) -> set[str]:
        """Personal Family roles never enter discovery, even when explicitly selected."""
        from ..state_store import get_lineage_data_store
        with get_lineage_data_store().database.read_current() as connection:
            rows = connection.execute("""SELECT DISTINCT fr.resident_id FROM family_residents fr
                JOIN families f USING(family_id) WHERE fr.resident_id=ANY(%s)
                AND f.deleted_at IS NULL AND f.personal_ama_id<>''""", (resident_ids,)).fetchall()
        return {r["resident_id"] for r in rows}

    def _without_summary_duty_runs(self, resident_id: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Leave out the summary writer's own duty runs.

        Writing summaries is the duty itself, not work of the writer's to
        summarize; counting those turns would make every run queue a rewrite
        of the writer's own summary.  The Effect that hands the writer a run
        marks that message with ``SUMMARY_DUTY_REF``; the run is the input and
        every reply to it.
        """

        if resident_id != self._summary_writer():
            return messages
        runs = {
            str(item.get("id") or "") for item in messages
            if item.get("role") == "user"
            and self._business_ref(resident_id, str(item.get("id") or "")) == SUMMARY_DUTY_REF
        }
        return [item for item in messages if str(item.get("turn_id") or "") not in runs] if runs else messages

    @staticmethod
    def _summary_writer() -> str:
        from management_portal.application_targets import summary_resident, ApplicationUnconfigured

        try:
            return summary_resident()
        except (KeyError, ApplicationUnconfigured):
            return ""

    @staticmethod
    def _business_ref(resident_id: str, message_id: str) -> str:
        from management_portal.society_service import resident_conversation_stream

        return resident_conversation_stream.get_repository().message_business_ref(resident_id, message_id)

    def _input_state(self, resident_id: str, message_id: str) -> Any:
        return self.service.resident.get_resident_input_state(resident_id, self.service_actor, message_id)

    def authorized_resident_ids(self, actor: str, resident_ids: list[str]) -> set[str]:
        requested = list(dict.fromkeys(str(value or "").strip() for value in resident_ids if str(value or "").strip()))
        if not requested:
            return set()
        payload = self.service.resident.filter_visible_resident_ids(
            requested, str(actor or "").strip(), self.service_actor,
        )
        values = payload.get("resident_ids") if isinstance(payload, dict) else []
        return {str(value) for value in values or [] if str(value)}
