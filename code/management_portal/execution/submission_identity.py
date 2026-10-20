"""Match the exact original admission digest without rewriting its preimage."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from management_common.json_columns import json_text

from .contracts import ExecutionRequest


def submission_digest(payload: object) -> str:
    """Keep the scheduler's original admission serialization byte-for-byte."""
    return hashlib.sha256(json_text(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    ).encode()).hexdigest()


@dataclass(frozen=True)
class SubmissionIdentity:
    digest: str

    @classmethod
    def from_request(cls, request: ExecutionRequest) -> "SubmissionIdentity":
        # to_payload validates and copies the original request before prepare.
        payload = request.to_payload()
        return cls(submission_digest(payload))

    def matches(self, stored_digest: str) -> bool:
        return stored_digest == self.digest
