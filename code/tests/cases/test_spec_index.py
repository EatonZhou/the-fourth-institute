"""The release SPEC is a portable index, independent of a host's knowledge base.

:module: spec.design-governance
"""
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[2]
LINK = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')
RESOURCE = re.compile(r'/r/(?:decision|thinking)_[0-9a-f]{12}')


def validate_spec_index(text: str, root: Path) -> list[str]:
    lines = text.splitlines()
    assert lines and lines[0].startswith('# '), 'Missing index title'
    targets = []
    for line in lines[1:]:
        if not line.strip():
            continue
        assert line.startswith('- '), 'Design prose belongs in linked documents'
        links = list(LINK.finditer(line))
        assert links and LINK.sub('', line[2:]).strip(' ·') == '', 'Expected named index links'
        for link in links:
            target = link[2]
            assert target not in targets, 'Duplicate index target'
            if target.startswith('/'):
                assert RESOURCE.fullmatch(target), 'Expected stable design resource ID'
            else:
                path = (root / target).resolve()
                assert path.is_relative_to(root.parent.resolve()), 'Path escapes repository'
                assert path.is_file(), f'Missing linked document: {target}'
            targets.append(target)
    assert targets, 'Empty index'
    return targets


def test_repository_spec_links_are_portable_and_local_documents_exist():
    targets = validate_spec_index((ROOT / 'SPEC.md').read_text(), ROOT)
    assert '/r/decision_ef80c6d3b5ee' in targets
    assert 'docs/design/local-development.md' in targets
    assert 'docs/testing.md' in targets


def test_index_accepts_resource_and_repository_copy(tmp_path):
    code = tmp_path / 'code'; code.mkdir()
    (tmp_path / 'design.md').write_text('# Design\n')
    text = '# SPEC.md\n\n- [Design](/r/decision_0123456789ab) · [Copy](../design.md)\n'
    assert validate_spec_index(text, code) == ['/r/decision_0123456789ab', '../design.md']


@pytest.mark.parametrize('entry', [
    '- [Old host](/share/Projects/private.md)',
    '- [Missing document](docs/missing.md)',
    '- [Escape](../../outside.md)',
    '- [Invalid resource](/r/decision_invalid)',
    '- [Design](/r/decision_0123456789ab) extra specification prose',
    'Implementation details do not belong in the index.',
    '- [Design](/r/decision_0123456789ab)\n- [Duplicate](/r/decision_0123456789ab)',
])
def test_index_rejects_missing_unsafe_duplicate_or_non_index_content(tmp_path, entry):
    with pytest.raises(AssertionError):
        validate_spec_index('# SPEC.md\n\n' + entry + '\n', tmp_path)
