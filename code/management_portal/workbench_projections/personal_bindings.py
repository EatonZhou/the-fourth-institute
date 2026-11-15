"""Reserved personal declarations are migration sources, never shared roles."""
import re

from .store import ProjectionError

PATTERN = re.compile(r'^surface-chat:([^:]+):paper:(.+)$')


def personal_scope(scope):
    return PATTERN.fullmatch(scope) if isinstance(scope, str) else None


def declarations(bindings):
    result = {}
    for alias, binding in bindings.items():
        for scope, resident in binding.get('residents', {}).items():
            if not personal_scope(scope):
                continue
            if scope in result:
                raise ProjectionError('projection.role_ambiguous', '个人会话存在重复 scope：' + scope, 409)
            result[scope] = dict(alias=alias, lineage_id=binding['lineage_id'], resident_id=resident)
    return result


def shared_bindings(bindings):
    result = {}
    for alias, binding in bindings.items():
        residents = {k: v for k, v in binding.get('residents', {}).items() if not personal_scope(k)}
        # Keep empty ordinary aliases: their declared shared Lineage still matters.
        personal_only = bool(binding.get('residents')) and not residents and not binding.get('events')
        if personal_only and alias != 'main':
            continue
        result[alias] = {**binding, 'residents': residents}
    return result


def check_changes(current, prepared):
    before, after = declarations(current), declarations(prepared)
    if before != after:
        raise ProjectionError('projection.personal_association_migration_required',
                              '个人会话须通过用户关联迁移入口维护，不能写入或移除共享角色', 409)
