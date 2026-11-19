"""Pure Effect configuration and candidate review rules."""

from copy import deepcopy
import hashlib
import json
from typing import Any

from management_common.principal import actor_principal
from management_portal.hatg.contracts import InstructionError

from .lineage_effect_script import MAX_SCRIPT_CHARS
from .lineage_program_vocabulary import EFFECT_CONFIG_FIELDS, EFFECT_ENDPOINT_RE, MAX_EFFECT_ENDPOINTS
from .lineage_values import _text


def normalize_script(config):
    raw_script = config.get("script")
    if raw_script is None:
        raw_script = ""
    if not isinstance(raw_script, str):
        raise ValueError("Effect 的 script 必须是文本")
    script = raw_script.replace("\r\n", "\n").replace("\r", "\n")
    if len(script) > MAX_SCRIPT_CHARS:
        raise ValueError(f"Effect 脚本最长 {MAX_SCRIPT_CHARS} 字符")
    raw_endpoints = config.get("endpoints") or []
    if not isinstance(raw_endpoints, list) or len(raw_endpoints) > MAX_EFFECT_ENDPOINTS:
        raise ValueError(f"Effect endpoints 必须是最多 {MAX_EFFECT_ENDPOINTS} 个名称的数组")
    endpoints: list[str] = []
    for raw in raw_endpoints:
        name = _text(raw, 40)
        if not EFFECT_ENDPOINT_RE.fullmatch(name) or name in endpoints:
            raise ValueError(f"Effect endpoint 名称无效或重复：{name or '（空）'}")
        endpoints.append(name)
    return {"script": script, "endpoints": endpoints}


def normalize_effect_config(config: dict[str, Any]) -> dict[str, Any]:
    """Shape of an Effect: its script and named endpoints.

    Syntax, instructions, arguments and references are reported by
    diagnostics, so a half-written script can be saved.
    """

    unknown = sorted(set(config) - EFFECT_CONFIG_FIELDS)
    if unknown:
        raise ValueError("Effect config 只包含 script、endpoints、failure_recipients、script_draft、script_review_required；不接受：" + "、".join(unknown))
    result = normalize_script(config)
    if 'script_review_required' in config and not isinstance(config['script_review_required'], bool):
        raise ValueError('script_review_required 必须是布尔值')
    if config.get('script_review_required'):
        result['script_review_required'] = True
    if 'script_draft' in config:
        result['script_draft'] = shape(config['script_draft'])
    if "failure_recipients" in config:
        recipients = config["failure_recipients"]
        if not isinstance(recipients, list) or len(recipients) > 20:
            raise ValueError("Effect failure_recipients 必须是最多 20 个用户名的数组")
        users = []
        for username in recipients:
            if not isinstance(username, str) or not username.strip() or len(username) > 120:
                raise ValueError("Effect 失败通知收件人必须是用户名")
            username = username.strip()
            if actor_principal(username).kind != "user":
                raise ValueError("Effect 失败通知收件人只能是 User")
            if username not in users:
                users.append(username)
        result["failure_recipients"] = users
    return result


def digest(draft):
    return hashlib.sha256(json.dumps({k: v for k, v in draft.items() if k != 'hash'}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def shape(raw):
    if not isinstance(raw, dict) or set(raw) - {'description', 'script', 'endpoints', 'requested_by', 'confirm_by', 'hash'}:
        raise ValueError('Effect 草稿字段无效')
    description = raw.get('description', '')
    if not isinstance(description, str) or len(description) > 20000:
        raise ValueError('Effect 描述必须是不超过 20000 字符的文本')
    value = normalize_script(raw)
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


def reconcile(items, previous, actor, *, key='id', reviewed_effect_id=None):
    """Called inside both owners' write boundary; client review metadata is untrusted."""
    before = {item.get(key): item for item in previous}
    for item in items:
        if item.get('kind') != 'effect':
            continue
        config = item.setdefault('config', {})
        old = before.get(item.get(key), {}).get('config', {})
        pending = old.get('script_draft')
        if reviewed_effect_id is not None and item.get(key) == reviewed_effect_id:
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
