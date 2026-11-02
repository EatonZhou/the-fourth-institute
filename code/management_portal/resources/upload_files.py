"""Upload file cleanup for the browser and internal lifecycle consumers."""
from __future__ import annotations


def remove_upload(upload_id: str) -> dict:
    from management_common.upload_retention import deleted
    from management_portal.hatg.contracts import InstructionError
    from .isa import _upload_dir, read_upload

    directory = _upload_dir(upload_id)
    try:
        read_upload(upload_id)
    except InstructionError as error:
        if error.code != "instruction.not_found":
            raise
        # The browser may retry a removal after its response was lost.
        return {"upload_id": upload_id, "deleted": False}
    deleted(directory)
    return {"upload_id": upload_id, "deleted": True}
