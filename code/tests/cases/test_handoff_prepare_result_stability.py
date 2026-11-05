"""Preparing a durable Handoff returns the same item contract as reading it.

:module: resident.fork-handoff
:design: decision_7ef392b5f60d
"""
from copy import deepcopy
from pathlib import Path
import subprocess
from uuid import uuid4

from management_portal.society_service import lineage_handoffs, lineage_aggregate
from tests.cases.isa_test_support import run
from tests.cases.test_cross_owner_storage_integration import ACTOR, owners  # noqa: F401
from tests.cases.test_wall04_residual_20260919 import LOCAL_POPEN, team  # noqa: F401


def test_prepare_items_match_durable_read_and_ready_replay(team, monkeypatch):
    """首次准备、Get 及不同幂等键 ready 重放的交接项完全相同。"""
    def local_git_only(arguments, *args, **kwargs):
        assert arguments[:2] == ['git', '-C']
        assert Path(arguments[2]).resolve().is_relative_to(team.owners.root)
        assert arguments[3] in {'rev-parse', 'status', 'branch', 'rev-list', 'log'}
        return LOCAL_POPEN(arguments, *args, **kwargs)

    monkeypatch.setattr(subprocess, 'Popen', local_git_only)
    owner_items = []
    execute = lineage_handoffs.execute_handoff

    def observe(*args, **kwargs):
        result = execute(*args, **kwargs)
        owner_items.append(deepcopy(result['handoff']['items']))
        return result

    monkeypatch.setattr(lineage_handoffs, 'execute_handoff', observe)
    created = lineage_handoffs.create_handoff(team.lineage_id, {
        'source_resident_id': team.source['resident_id'],
        'target_resident_id': team.target['resident_id'],
        'items': [{'source': {'path': team.source['default_cwd']},
                   'transfer': {'mode': 'direct', 'parameters': {'exclude': ['fixture-private']}}}],
        'execute_now': False,
        'expected_revision': lineage_aggregate.read_lineage(team.lineage_id, actor=ACTOR)['revision'],
    }, ACTOR)
    arguments = {'lineage_id': team.lineage_id, 'handoff_id': created['handoff']['id']}
    first = run('PrepareWorkspaceHandoff', arguments, actor=ACTOR, key=uuid4().hex)
    assert first.status == 'committed', first.to_payload()
    assert first.result['status'] == 'ready', first.result
    original = deepcopy(first.result['items'])
    read = run('GetWorkspaceHandoff', {'handoff_id': arguments['handoff_id']}, actor=ACTOR, key=uuid4().hex)
    replay = run('PrepareWorkspaceHandoff', arguments, actor=ACTOR, key=uuid4().hex)
    assert read.status == replay.status == 'committed', (read.to_payload(), replay.to_payload())
    assert read.result['handoff']['items'] == replay.result['items']
    assert replay.result['items'] == original
    assert owner_items == [original, original]
    assert first.result['items'] == original
    assert original[0]['transfer']['parameters']['exclude'] == ['fixture-private']
    assert len(original[0]['attempts']) == 1
