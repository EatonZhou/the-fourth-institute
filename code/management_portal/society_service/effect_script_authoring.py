"""Version-bound Effect review in each resource's write transaction."""

from management_portal.hatg.contracts import InstructionError

from .effect_script_candidate import review_config, template_items


def review(inv, *, template=False):
    identity = inv.argument('template_id' if template else 'lineage_id')
    effect_id = inv.argument('effect_id')
    version = inv.expected_version_for(identity)
    if version is None:
        raise ValueError('确认脚本必须携带读取时的 expected_version')
    def edit(body):
        current = body.get('version' if template else 'revision')
        if str(current) != str(version):
            raise InstructionError('instruction.conflict', '资源已变化，请重新读取')
        items = template_items(body) if template else body['program']['components']
        item = next((item for item in items if item.get('key' if template else 'id') == effect_id and item.get('kind') == 'effect'), None)
        if item is None:
            raise ValueError('Effect 不存在')
        item['config'] = review_config(item['config'], inv.caller, inv.argument('draft_hash'), inv.argument('decision'))
        return body
    if template:
        from .templates import store
        from .templates.marketplace import get_template

        store.save({}, inv.caller, template_id=identity, transform=edit, reviewed_effect_id=effect_id)
        return {'template': get_template(identity)}

    from . import lineage_graph_edits, lineage_state

    with lineage_state.reviewing_effect(identity, effect_id):
        lineage, _ = lineage_graph_edits.commit_lineage_edit(identity, inv.caller, edit, reason='确认 Effect 脚本草稿')
    return {'lineage': lineage}
