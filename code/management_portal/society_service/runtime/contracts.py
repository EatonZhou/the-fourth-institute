from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def _required_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _canonical_id(
    value: object,
    field_name: str,
    prefix: str,
) -> str:
    text = _required_text(value, field_name)
    if not text.startswith(f"{prefix}_"):
        raise ValueError(
            f"{field_name} must be a canonical {prefix}_* ID"
        )
    return text


@dataclass(frozen=True)
class ResidentWakeRequest:
    resident_id: str

    def validate(self) -> "ResidentWakeRequest":
        if not re.fullmatch(r"resident_[0-9a-f]{12}", self.resident_id):
            raise ValueError("resident_id must be a canonical resident ID")
        return self

    def to_payload(self) -> dict[str, str]:
        self.validate()
        return {"resident_id": self.resident_id}

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "ResidentWakeRequest":
        return cls(resident_id=str(payload.get("resident_id") or "")).validate()


@dataclass(frozen=True)
class ResidentTerminalFact:
    resident_id: str
    actor: str
    message_id: str
    terminal_status: str = "completed"
    completed_at: str = ""
    lineage_id: str = ""
    business_ref: str = ""

    def validate(self) -> "ResidentTerminalFact":
        _canonical_id(self.resident_id, "resident_id", "resident")
        _required_text(self.actor, "actor")
        _required_text(self.message_id, "message_id")
        if self.lineage_id:
            _canonical_id(
                self.lineage_id,
                "lineage_id",
                "lineage",
            )
        if len(str(self.business_ref or "")) > 500:
            raise ValueError("business_ref must not exceed 500 characters")
        if self.terminal_status not in TERMINAL_STATUSES:
            raise ValueError(
                "terminal_status must be completed, failed, or cancelled"
            )
        return self

    def to_payload(self) -> dict[str, Any]:
        self.validate()
        return {
            "resident_id": self.resident_id,
            "actor": self.actor,
            "message_id": self.message_id,
            "terminal_status": self.terminal_status,
            "completed_at": self.completed_at,
            "lineage_id": self.lineage_id,
            "business_ref": self.business_ref,
        }

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any],
    ) -> "ResidentTerminalFact":
        status = str(
            payload.get("terminal_status") or "completed"
        ).strip().lower()
        status = {
            "canceled": "cancelled",
            "interrupted": "cancelled",
        }.get(status, status)
        return cls(
            resident_id=str(payload.get("resident_id") or ""),
            actor=str(payload.get("actor") or ""),
            message_id=str(payload.get("message_id") or ""),
            terminal_status=status,
            completed_at=str(payload.get("completed_at") or ""),
            lineage_id=str(payload.get("lineage_id") or ""),
            business_ref=str(payload.get("business_ref") or ""),
        ).validate()
