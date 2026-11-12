"""Recovery admission shared by Runtime and the Scheduler's ownership paths."""
from __future__ import annotations

from typing import Any

from .provider_catalog import AGENT_PROVIDERS


def checkpoint_resumable(checkpoint: dict[str, Any], request: dict[str, Any] | None = None) -> bool:
    """Validate persisted recovery claims against the actual Adapter contract.

    Older runtimes marked any session+turn pair resumable, including ACP.
    The immutable request supplies the provider when old checkpoints omit it.
    Completed results need no live turn; other service checkpoints retain
    their existing recovery contract.
    """
    if not checkpoint.get("resumable"):
        return False
    if checkpoint.get("provider_completed"):
        return True
    provider = AGENT_PROVIDERS.get(str((request or {}).get("provider") or checkpoint.get("provider") or ""))
    if provider is None or provider.handover_keeps_turn:
        return True
    receipt = checkpoint.get("provider_recovery_state") or {}
    return bool(isinstance(receipt, dict) and receipt.get("phase") == "completed"
                and receipt.get("turn_id") == checkpoint.get("turn_id")
                and isinstance(receipt.get("output"), dict))
