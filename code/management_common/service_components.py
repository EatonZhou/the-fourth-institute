"""Built-in service launchers; other functional services supply an instance program."""
import re

MODULES = {
    # Telepus (management_portal.telepus) is part of the Portal code; an
    # installation runs these two only when it registers the components.
    'telepus-telegram-router': 'management_portal.telepus.router_service',
    'telepus-portal-refresh': 'management_portal.telepus.refresh_service',
    'resource': 'management_portal.resources.server',
    'execution': 'management_portal.execution.service',
    'society': 'management_portal.society_service.server',
    'worker': 'management_portal.execution.worker_manager',
    'kimi': 'management_portal.kimi_code_server',
    'kimi-code': 'management_portal.kimi_code_server',
    'claude': 'management_portal.claude_code_daemon',
    'claude-code': 'management_portal.claude_code_daemon',
    'gateway': 'management_portal.portal_deployment_router',
    'ai-socket-relay': 'management_portal.ai_socket_relay',
}


def is_builtin(component: str) -> bool:
    return component in {'postgres', 'nats', 'web', 'dev-web', *MODULES} or bool(
        re.fullmatch(r'codex-app-server@[A-Za-z0-9_-]+', component))
