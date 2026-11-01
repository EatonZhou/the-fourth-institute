"""Provider programs follow code; retained state follows the selected instance.

:module: execution.providers
:design: decision_d88306d0bd17
"""
import json
import os
from pathlib import Path
import subprocess
import sys

from tests.cases.instance_test_support import write_instance


def test_provider_programs_and_update_state_have_distinct_owners(tmp_path):
    root = write_instance(tmp_path / 'instance')
    document = json.loads((root / 'instance.json').read_text())
    document['areas'] = {'provider-state': str(tmp_path / 'provider-status')}
    (root / 'instance.json').write_text(json.dumps(document))
    code = Path(__file__).resolve().parents[2]
    probe = "import json\nfrom management_portal import dsh_client, kimi_code_server, opencode_provider_catalog\nfrom management_portal.execution import provider_updates\nprint(json.dumps({'dsh': str(dsh_client.RUNTIME), 'kimi': str(kimi_code_server.DEFAULT_BINARY),\n    'opencode': str(opencode_provider_catalog.RUNTIME), 'programs': str(provider_updates.RUNTIMES),\n    'state': str(provider_updates.STATE_ROOT),\n    'entries': {name: [str(runtime.link()), str(runtime.activation_link())] for name, runtime in\n        [('codex', provider_updates.CodexRuntime()), ('claude', provider_updates.ClaudeRuntime()),\n         ('kimi', provider_updates.KimiRuntime()), ('dsh', provider_updates.DshRuntime())]}}))\n"
    process = subprocess.run([sys.executable, '-c', probe], cwd=code,
                             env={**os.environ, 'PORTAL_INSTANCE_ROOT': str(root)},
                             capture_output=True, text=True, check=True)
    paths = json.loads(process.stdout)
    programs = code.parent / 'dependencies/providers'
    entries = paths.pop('entries')
    for name, entry in {'codex': 'codex', 'claude': 'claude-agent-sdk-current', 'kimi': 'kimi-code-current', 'dsh': 'dsh-runtime-current'}.items():
        assert entries[name] == [str(programs / entry)] * 2
    assert paths == {'dsh': str(programs / 'dsh-runtime-current'),
                     'kimi': str(programs / 'kimi-code-current/bin/kimi'),
                     'opencode': str(programs / 'opencode-current'),
                     'programs': str(programs), 'state': str(tmp_path / 'provider-status')}
    assert not (tmp_path / 'provider-status').exists()
