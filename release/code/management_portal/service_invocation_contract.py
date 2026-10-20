from __future__ import annotations

from management_common.operation_errors import OperationError

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any


PLAN_SCHEMA = "tfi.service-invocation-plan.v1"


class ServiceInvocationConflict(OperationError):
    code = "service_invocation.conflict"
    http_status = 409
    commit_state = "not_committed"


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def input_fingerprint(job: Mapping[str, Any], prompt: str) -> str:
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("authorized candidate prompt required")
    identity = {name: job[name] for name in ("created_by", "surface", "search_id", "run_generation")}
    return digest({**identity, "prompt": prompt})


def batch_manifest(job: Mapping[str, Any], prompts: Sequence[str]) -> list[dict[str, str]]:
    if isinstance(prompts, (str, bytes)) or not prompts:
        raise ValueError("a non-empty ordered prompt plan is required")
    return [{"batch_id": f"batch-{index:06d}", "input_fingerprint": input_fingerprint(job, prompt)}
            for index, prompt in enumerate(prompts, 1)]


def validate_manifest(batches: object) -> list[dict[str, str]]:
    if not isinstance(batches, list) or not batches:
        raise ValueError("a non-empty ordered batch manifest is required")
    normalized = []
    for index, batch in enumerate(batches, 1):
        if (not isinstance(batch, dict) or set(batch) != {"batch_id", "input_fingerprint"}
                or batch["batch_id"] != f"batch-{index:06d}"
                or not isinstance(batch["input_fingerprint"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", batch["input_fingerprint"])):
            raise ValueError("batch manifest must contain ordered batch IDs and SHA-256 input fingerprints")
        normalized.append(dict(batch))
    return normalized


def plan_fingerprint(job: Mapping[str, Any], batches: list[dict[str, str]]) -> str:
    identity = {name: job[name] for name in ("created_by", "surface", "search_id", "run_generation")}
    return digest({"schema": PLAN_SCHEMA, **identity, "batches": validate_manifest(batches)})


def plan_fingerprint_matches(job: Mapping[str, Any], batches: list[dict[str, str]], fingerprint: object) -> bool:
    """Verify the canonical versioned plan and its exact manifest."""
    return isinstance(fingerprint, str) and fingerprint == plan_fingerprint(job, batches)
