"""Read-only audit of historical generated audio. No speech execution or tools.

Retained solely for immutable media produced before the optional-tool migration.
New tool reports are handled by the ordinary media package owner.
"""
import hashlib
import json
from management_portal.execution.config import execution_state_root
from management_portal.execution.contracts import EXECUTION_ID_RE


def _path(execution_id):
    if not isinstance(execution_id, str) or not EXECUTION_ID_RE.fullmatch(execution_id):
        raise ValueError("Invalid speech execution ID")
    return execution_state_root() / "speech" / execution_id / "receipt.json"


def read_record(execution_id):
    try:
        return json.loads(_path(execution_id).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError("Speech generation not found") from None


def verified_receipt(execution_id, *, actor):
    """Read authoritative evidence and recheck ACL and bytes through ResourceService."""
    from management_portal.resources.service import ResourceService
    record = read_record(execution_id)
    if record["status"] != "completed":
        raise ValueError("Native speech has no completed receipt")
    receipt = record["receipt"]
    data, _mime, _detail = ResourceService().read_content(receipt["audio"]["address"], actor=actor,
                                                       max_bytes=24 * 1024 * 1024)
    if hashlib.sha256(data).hexdigest() != receipt["audio"]["sha256"]:
        raise ValueError("Native audio does not match its generation receipt")
    return receipt

