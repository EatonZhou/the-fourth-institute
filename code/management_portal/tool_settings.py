"""Tool settings project the existing Resident configuration, without a second registry."""
from __future__ import annotations

from .hatg.contracts import InstructionCall, InstructionContext, InstructionError, invalid


def _resident_id(value):
    import re
    if not isinstance(value, str) or not re.fullmatch(r"resident_[0-9a-f]{12}", value):
        raise invalid("请选择一个有效的 Resident")
    return value


def _call(name, arguments, context):
    from .hatg import routing
    return routing.execute(InstructionCall(name, arguments), context).raise_for_error()


def _can_edit(caller, resident_id):
    from . import permission_system
    return permission_system.allows(caller, 'UpdateResidentSettings', {'resident_id': resident_id})


def view(context: InstructionContext, resident_id=None):
    from .application_tools import list_tools
    from .execution.resident_capability_settings import desired_capability_config
    result = {'schema': 'tfi.tool-settings.v1', 'applications': list_tools(context.caller)['tools']}
    if resident_id is None:
        return result
    resident_id = _resident_id(resident_id)
    stored = _call('GetResidentSettings', {'resident_id': resident_id}, context)
    config = desired_capability_config(stored['settings'])
    result['resident'] = {'resident_id': resident_id, 'title': resident_id,
        'settings_revision': stored['settings_revision'], 'capability_config': config,
        'can_edit': _can_edit(context.caller, resident_id), 'application': stored.get('application', {})}
    result['connections'] = [dict(item, title=item['ref']) for item in config['mcp']]
    return result


def save(context, payload):
    from .execution.resident_capability_settings import normalize_capability_config
    if not isinstance(payload, dict) or set(payload) != {'resident_id', 'expected_revision', 'capability_config'}:
        raise invalid('保存需要 Resident、读取版本和工具配置')
    resident_id = _resident_id(payload['resident_id'])
    if not isinstance(payload['expected_revision'], str) or not payload['expected_revision']:
        raise invalid('请先读取当前工具配置再保存')
    config = normalize_capability_config(payload['capability_config'])
    return _call('UpdateResidentSettings', {'resident_id': resident_id,
        'expected_revision': payload['expected_revision'], 'changes': {'capability_config': config}}, context)


def discover(context, payload, *, is_cancelled=None):
    if not isinstance(payload, dict) or set(payload) != {'resident_id', 'ref'}:
        raise invalid('发现工具需要 Resident 和连接资源地址')
    resident_id = _resident_id(payload['resident_id'])
    ref = payload['ref']
    if not isinstance(ref, str) or not ref.strip() or len(ref) > 512:
        raise invalid('请输入 MCP 连接资源地址')
    # Configuring this Resident is the authority to prepare its tools. Neither
    # a viewer nor a caller-supplied identity may turn resource reads into execution.
    _call('GetResidentSettings', {'resident_id': resident_id}, context)
    if not _can_edit(context.caller, resident_id):
        raise InstructionError('instruction.forbidden', '没有管理该 Resident 工具的权限')
    from .execution.resident_mcp import discover_mcp_directory
    return discover_mcp_directory(ref.strip(), caller=resident_id, is_cancelled=is_cancelled)
