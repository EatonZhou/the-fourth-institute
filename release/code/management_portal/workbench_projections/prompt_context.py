from __future__ import annotations

import json

from .store import ProjectionError


PROMPT_BUDGET = 14000


def resource_snapshot(address: str, revision: int, value: dict) -> dict:
    reference = {"resource_address": address, "revision": revision}
    if len(json.dumps(value, ensure_ascii=False)) > 2400:
        return {**reference, "read_required": True}
    return {**reference, **value}


def fit_prompt(render, snapshots: dict[str, dict]) -> str:
    prompt = render(snapshots)
    if len(prompt) <= PROMPT_BUDGET:
        return prompt
    references = {
        name: {"resource_address": item["resource_address"], "revision": item["revision"], "read_required": True}
        for name, item in snapshots.items()
    }
    prompt = render(references)
    if len(prompt) > PROMPT_BUDGET:
        raise ProjectionError("projection.prompt_too_large", "需求说明过长，请拆分需求后重试；页面与团队内容已改用资源引用，不会截断用户要求", 400)
    return prompt
