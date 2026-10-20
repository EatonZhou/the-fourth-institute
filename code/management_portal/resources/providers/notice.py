from __future__ import annotations

import json
from .common import canonical_record


def install_resolver(service):
    from management_portal.notice_board_backend import notice_board

    def resolve(address, *, actor="", **_context):
        resource_id = address.split("/", 1)[0]
        post = notice_board.get_post(actor, resource_id)["post"]
        record = canonical_record(resource_id, post)
        return {"address": address, "root_id": resource_id, "resource_type": "notice",
                "target_kind": "notice", "record": {**record, "resource_id": resource_id, "resource_type": "notice"},
                "detail": post, "exists": True, "content_kind": "json", "filename": resource_id + ".json"}

    def read(resolved, **_context):
        return json.dumps(resolved["detail"], ensure_ascii=False).encode(), "application/json", {"filename": resolved["filename"]}

    service.register_resolver("notice", resolve, read_content=read)
