"""The personal Family's lazily provisioned mobile member (decision_d09c55133f12)."""
from __future__ import annotations

import hashlib

from . import family_familize, lineage_aggregate, lineage_resident_nodes, resident_record_domain

TITLE = "手机助手"
INSTRUCTIONS = """# 手机助手

你是用户个人 Family 的手机助手，直接处理微信和 Telegram 消息。
读者在手机聊天中：回复简短、清楚，不依赖本地文件路径、Portal 页面链接或宽表格。
需要专门成员时，请本 Family 的 steward 分派，或告诉用户用 /route <resident_id> 直接联系该成员；
/home 回到个人助手。引用回复和问答保持原成员上下文。保留用户身份和权限边界。
"""


def creation_key(family_id):
    return 'personal-phone-assistant:' + family_id


def is_mobile(facade, message):
    return bool((facade.get('family') or {}).get('personal_ama_id') and
                ((message.get('origin') or {}).get('channel') or {}).get('kind') in {'weixin', 'telegram'})


def ensure(facade, actor):
    """Use the same creation and roster owners as Steward, with a durable creation receipt."""
    from .resident_runtime_domain import read_resident_runtime_settings
    from .templates import familize
    lineage_id = facade['lineage_id']
    family_id = facade['family']['family_id']
    store = lineage_aggregate.lineage_catalog_store().state_store
    receipt = store.resident_creation_operations.get(lineage_id, creation_key(family_id))
    if receipt.get('resident_id'):
        from .family_facade import family_member_ids
        if receipt['resident_id'] in family_member_ids(facade):
            return receipt['resident_id']
    key = int.from_bytes(hashlib.sha256(f'{store.database.schema}:phone:{family_id}'.encode()).digest()[:8],
                         'big', signed=True)
    with store.database.advisory_lock(key):
        graph = lineage_aggregate.read_lineage(lineage_id, actor=actor)
        block = family_familize.family_block(graph, family_id)
        receipt = store.resident_creation_operations.get(lineage_id, creation_key(family_id))
        rid = receipt.get('resident_id')
        if rid:
            record = resident_record_domain.find_resident(rid)
            if not record or record.get('deleted_at') or record['lineage_id'] != lineage_id:
                raise ValueError('手机助手已移出或删除，请在个人 Family 中处理')
        else:
            from management_portal import permission_system
            for instruction in ('CreateResident', 'UpdateFamily'):
                if not permission_system.allows(actor, instruction, {'lineage_id': lineage_id, 'family_id': family_id}):
                    raise PermissionError(f'{instruction} permission is required')
            default = resident_record_domain.find_resident(block['family']['personal_ama_id'])
            settings = read_resident_runtime_settings(default)
            result = lineage_resident_nodes.create_lineage_resident(lineage_id, {
                'title': TITLE, 'creation_key': creation_key(family_id),
                'backend': default['backend'], 'backend_route_id': default.get('backend_route_id', ''),
                'runtime_settings': {key: settings[key] for key in ('model', 'reasoning_effort', 'backend_options')
                                     if key in settings}, 'initial_instructions': INSTRUCTIONS,
            }, actor)
            rid = result['resident']['resident_id']
        familize.join_new_resident(lineage_id, rid, actor, family_id)
        return rid
