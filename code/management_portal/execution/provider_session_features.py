"""Session controls shipped with the selected native runtime edition."""
import os

from management_common.code_environment import provider_entry
from management_common.instance_manifest import current_instance

MARKER = "PORTAL_SESSION_CONTROLS"
ENTRIES = {
    "codex": "codex_binary", "claude": "claude_app_server_current",
    "kimi": "kimi_runtime_current", "antigravity": "antigravity_runtime_current",
    "opencode": "opencode_runtime_current", "dsh": "dsh_runtime_current",
}


def session_features_enabled(provider):
    if not os.environ.get("PORTAL_INSTANCE_ROOT"):
        return False
    entry = provider_entry(ENTRIES[provider], current_instance())
    if provider == "codex" and not entry.is_dir():
        return (entry.resolve().parent / MARKER).is_file()
    return (entry / MARKER).is_file()
