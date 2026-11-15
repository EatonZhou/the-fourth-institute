"""Candidate scripts and version-bound review; runtime reads only active config."""
from contextvars import ContextVar
from copy import deepcopy
import hashlib
import json

from management_portal.hatg.contracts import InstructionError

_reviewing = ContextVar('effect_script_review', default=False)


def digest(draft):
    return hashlib.sha256(json.dumps({k: v for k, v in draft.items() if k != 'hash'}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def shape(raw):
    from .lineage_program_components import normalize_effect_config
    if not isinstance(raw, dict) or set(raw) - {'description', 'script', 'endpoints', 'requested_by', 'confirm_by', 'hash'}:
        raise ValueError('Effect 草稿字段无效')
    description = raw.get('description', '')
    if not isinstance(description, str) or len(description) > 20000:
        raise ValueError('Effect 描述必须是不超过 20000 字符的文本')
    value = normalize_effect_config({k: raw.get(k, '' if k == 'script' else []) for k in ('script', 'endpoints')})
    from management_common.principal import actor_principal
    for key in ('requested_by', 'confirm_by'):
        if raw.get(key):
            if not isinstance(raw[key], str) or actor_principal(raw[key]).kind not in {'user', 'resident'}:
                raise ValueError('脚本发起者和确认人必须是用户或 Resident ID')
            value[key] = actor_principal(raw[key]).id
    if raw.get('hash'):
        if not isinstance(raw['hash'], str) or len(raw['hash']) != 64:
            raise ValueError('脚本草稿 hash 无效')
        value['hash'] = raw['hash']
    return {**value, 'description': description}


def reconcile(items, previous, actor, key='id'):
    """Called inside both owners' write boundary; client review metadata is untrusted."""
    before = {item.get(key): item for item in previous}
    for item in items:
        if item.get('kind') != 'effect':
            continue
        config = item.setdefault('config', {})
        old = before.get(item.get(key), {}).get('config', {})
        pending = old.get('script_draft')
        if _reviewing.get():
            continue
        if old.get('script_review_required'):
            config['script_review_required'] = True
            if 'script_draft' not in config and any(config.get(k, '' if k == 'script' else []) != old.get(k, '' if k == 'script' else []) for k in ('script', 'endpoints')):
                raise ValueError('已确认脚本的修改需要新的描述与候选草稿，不能沿用旧确认')
        if pending and ('script_draft' not in config or any(config.get(k, '' if k == 'script' else []) != old.get(k, '' if k == 'script' else []) for k in ('script', 'endpoints'))):
            raise ValueError('存在待确认脚本；请确认或取消草稿，不能绕过确认替换运行版本')
        if 'script_draft' in config:
            draft = shape(config['script_draft'])
            requester = pending['requested_by'] if pending else actor
            confirmer = draft.get('confirm_by') or (pending or {}).get('confirm_by') or requester
            if pending and confirmer != pending['confirm_by'] and actor != requester:
                raise ValueError('只有发起者可以更换确认人')
            draft.update(requested_by=requester, confirm_by=confirmer)
            draft['hash'] = digest(draft)
            config['script_draft'] = draft
            config['script_review_required'] = True
            # Starting a review never changes the currently executable version.
            for field in ('script', 'endpoints'):
                config[field] = deepcopy(old.get(field, '' if field == 'script' else []))


def template_items(template):
    return (template or {}).get('body', {}).get('components', [])


def require_publishable(template):
    if any(item.get('config', {}).get('script_draft') for item in template_items(template)):
        raise ValueError('模板包含待确认的 Effect 脚本；请确认或取消后再发布')


def review_config(config, actor, expected_hash, decision):
    draft = config.get('script_draft')
    if not draft or digest(draft) != expected_hash:
        raise InstructionError('instruction.conflict', '候选脚本已变化，请重新读取并确认')
    allowed = {draft['confirm_by']} if decision == 'confirm' else {draft['confirm_by'], draft['requested_by']}
    if actor not in allowed:
        raise InstructionError('instruction.forbidden', '当前调用者不是指定确认人')
    result = deepcopy(config)
    if decision == 'confirm':
        if not draft['script'].strip():
            raise ValueError('候选脚本尚未编写')
        from .lineage_effect_script import analyze
        from .lineage_program_effect_rules import validate_effect_argument
        from management_portal.hatg.catalog import instruction_catalog
        analysis = analyze(draft['script'], declaration_for=instruction_catalog().get,
                           declared_endpoints=draft['endpoints'], validate_argument=validate_effect_argument)
        if analysis.diagnostics:
            raise ValueError('脚本校验失败：' + '; '.join(str(item) for item in analysis.diagnostics))
        result.update(script=draft['script'], endpoints=draft['endpoints'])
    elif decision != 'cancel':
        raise ValueError('decision 必须是 confirm 或 cancel')
    result.pop('script_draft')
    return result


def review(inv, *, template=False):
    from . import lineage_graph_edits
    from .templates import store, get
    identity = inv.argument('template_id' if template else 'lineage_id')
    version = inv.expected_version_for(identity)
    if version is None:
        raise ValueError('确认脚本必须携带读取时的 expected_version')
    def edit(body):
        current = body.get('version' if template else 'revision')
        if str(current) != str(version):
            raise InstructionError('instruction.conflict', '资源已变化，请重新读取')
        items = template_items(body) if template else body['program']['components']
        item = next((item for item in items if item.get('key' if template else 'id') == inv.argument('effect_id') and item.get('kind') == 'effect'), None)
        if item is None:
            raise ValueError('Effect 不存在')
        item['config'] = review_config(item['config'], inv.caller, inv.argument('draft_hash'), inv.argument('decision'))
        return body
    token = _reviewing.set(True)
    try:
        if template:
            store.save({}, inv.caller, template_id=identity, transform=edit)
            return {'template': get(identity)}
        lineage, _ = lineage_graph_edits.commit_lineage_edit(identity, inv.caller, edit, reason='确认 Effect 脚本草稿')
        return {'lineage': lineage}
    finally:
        _reviewing.reset(token)
