"""One invalid index reference must not hide authorized, valid WALL documents.

:module: workflow.wall
:importance: P1
"""
import json
from pathlib import Path

import pytest

from management_portal import wall_backend as wall, wall_documents as documents
from tests.cases.test_wall import wall_project  # noqa: F401


@pytest.mark.parametrize('reference_kind', ['parent', 'absolute', 'symlink'])
def test_bad_index_reference_is_reported_while_valid_cards_and_sources_remain_readable(wall_project, monkeypatch, reference_kind):
    private = wall_project.parent/'private.md'
    private.write_text('# Private\nNever read this outside the registered root.\n')
    reference = '../private.md'
    if reference_kind == 'absolute':
        reference = str(private)
    elif reference_kind == 'symlink':
        (wall_project/'linked.md').symlink_to(private)
        reference = 'linked.md'
    spec = wall_project/'SPEC.md'
    spec.write_text(spec.read_text() + f'\n[Bad companion]({reference})\n')
    original_read = Path.read_text

    def guarded_read(path, *args, **kwargs):
        assert path.resolve() != private.resolve(), 'outside document must never be read'
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', guarded_read)
    wall._scan(wall.project('portal', 'owner'), 'owner')
    board = wall.board('portal', 'owner')
    assert {p['title'] for p in board['blocks']} >= {'Existing', 'Improve', 'Keep'}
    assert len(board['errors']) == 1
    assert board['errors'][0]['source_id'] == 'SPEC.md'
    assert board['errors'][0]['reference'] == reference
    assert 'private.md' in board['errors'][0]['error'] or 'linked.md' in board['errors'][0]['error']
    assert 'Never read this' not in json.dumps(board)
    point = next(p for p in board['blocks'] if p['title'] == 'Existing')
    assert wall.block('portal', point['id'], 'owner')['block']['title'] == 'Existing'
    assert wall.source_document('portal', 'design.md', 'owner')['content'] == original_read(wall_project/'design.md')
    with pytest.raises((KeyError, PermissionError)):
        wall.source_document('portal', reference, 'owner')
    with pytest.raises(PermissionError):
        wall.source_document('portal', 'design.md', 'outsider')
    # A cached permissive scan must not turn strict export enumeration permissive.
    with pytest.raises((PermissionError, ValueError)):
        documents.index_sources(wall_project, spec_only=True)
    spec.write_text('# Spec\n[Design](design.md)\n')
    wall._scan(wall.project('portal', 'owner'), 'owner')
    assert not wall.board('portal', 'owner')['errors']


def test_bad_companion_link_keeps_canonical_resource_and_later_valid_link(tmp_path):
    (tmp_path/'SPEC.md').write_text(
        '- [Canonical](/r/decision_0123456789ab) · [Copy](../outside.md)\n'
        '- [Bad](../outside.md) · [Valid](design.md)\n')
    (tmp_path/'design.md').write_text('# Public design\n')
    errors = []
    sources = documents.index_sources(tmp_path, errors=errors)
    assert {s.address for s in sources} == {'decision_0123456789ab', 'design.md'}
    assert len(errors) == 2
    with pytest.raises(PermissionError):
        documents.index_sources(tmp_path)


def test_permissive_scan_still_rejects_an_index_file_outside_root(tmp_path):
    private = tmp_path.parent/(tmp_path.name+'-outside.md')
    private.write_text('[Private](private.md)\n')
    (tmp_path/'SPEC.md').symlink_to(private)
    with pytest.raises(PermissionError):
        documents.index_sources(tmp_path, errors=[])


def test_repairing_link_target_recovers_without_rewriting_index(tmp_path):
    private = tmp_path.parent/(tmp_path.name+'-private.md')
    private.write_text('Outside body')
    local = tmp_path/'design.md'
    local.write_text('# Public design')
    link = tmp_path/'linked.md'
    link.symlink_to(private)
    (tmp_path/'SPEC.md').write_text('[Design](linked.md)\n')
    errors = []
    assert not documents.index_sources(tmp_path, errors=errors)
    assert errors
    link.unlink()
    link.symlink_to(local)
    errors = []
    assert [s.address for s in documents.index_sources(tmp_path, errors=errors)] == ['design.md']
    assert not errors
