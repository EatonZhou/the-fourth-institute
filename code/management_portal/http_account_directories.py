"""Read-only host directory utilities for the account-registration page."""
from urllib.parse import parse_qs

from management_portal import permission_system, http_responses


def account_directories(request, parsed, actor):
    # Keep the exact existing registration policy, including custom denies.
    # No new ISA instruction, permission element or filesystem grant is added.
    if not permission_system.allows(actor, 'RegisterProviderAccount', {}):
        return http_responses._serve_json(request, 403, {'error': '没有新增执行账号的权限。', 'code': 'permission.denied'})
    try:
        query = parse_qs(parsed.query, keep_blank_values=True)
        if set(query) - {'action', 'provider', 'path', 'limit', 'cursor'} or any(len(v) != 1 for v in query.values()):
            raise ValueError('无效的目录查询参数')
        from management_portal.execution import account_directory_discovery as directories
        action = query.get('action', [''])[0]
        if action == 'discover' and set(query) <= {'action', 'provider'}:
            from management_portal.execution.account_registrations import registrations
            excluded = [(row.get('config') or {}).get('account_dir') for row in registrations()]
            result = directories.discover(query.get('provider', [''])[0], excluded=[path for path in excluded if path])
        elif action == 'browse' and set(query) <= {'action', 'path', 'limit', 'cursor'}:
            limit = query.get('limit', [None])[0]
            if limit is not None and (len(limit) > 3 or not limit.isascii() or not limit.isdecimal()):
                raise ValueError('limit 必须是整数')
            result = directories.browse(
                query.get('path', [None])[0], cursor=query.get('cursor', [''])[0],
                limit=int(limit) if limit is not None else None,
            )
        else:
            raise ValueError('无效的目录查询操作')
        return http_responses._serve_json(request, 200, result)
    except Exception as exc:
        return http_responses._serve_exception_json(request, exc)
