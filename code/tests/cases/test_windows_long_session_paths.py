"""Native Windows persistence survives long default instance paths.

:module: operations.deployment
:design: decision_d88306d0bd17
:profiles: local.fixture
:risk: no-external-side-effects
"""
import json
import os
from pathlib import Path

import pytest

from management_common.filesystem_paths import filesystem_path
from management_common import instance_areas
from management_portal import provider_session_relocation as relocation
from management_portal.execution.native_session_compaction import dsh_session_path


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows long-path semantics')
def test_long_dsh_transcript_is_found_relocated_and_copied(tmp_path, monkeypatch):
    from management_common import portal_paths
    root = tmp_path / ('nested-' * 15) / ('provider-' * 8)
    state = root / 'dsh'
    session_id = 'dsh-123abc'
    old, new = r'C:\old\resident\workspace', r'C:\moved\resident\workspace'
    folder = filesystem_path(state / session_id / 'sessions' / relocation.dsh_project_key(old) / session_id)
    folder.mkdir(parents=True)
    body = b'{"type":"user/message","text":"C:\\\\old historical text"}\n'
    transcript = folder / 'session.v4.jsonl'
    transcript.write_bytes((json.dumps({'type': 'session', 'cwd': old})+'\n').encode()+body)
    assert len(str(transcript)) > 260
    assert dsh_session_path(state, session_id).read_bytes().endswith(body)
    monkeypatch.setattr(portal_paths, 'path', lambda name: root)
    result = {'changed': [], 'errors': [], 'conflicts': []}
    relocation._relocate_dsh(relocation._Prefix(old, new), True, result)
    moved = dsh_session_path(state, session_id)
    header, messages = moved.read_bytes().split(b'\n', 1)
    assert json.loads(header)['cwd'] == new and messages == body
    assert not transcript.exists() and not result['errors'] and not result['conflicts']
    copied = tmp_path / 'copy'
    instance_areas._copy_verified(root, copied)
    assert instance_areas.directory_size(copied) == len(moved.read_bytes())
    assert instance_areas._fingerprint(root) == instance_areas._fingerprint(copied)
