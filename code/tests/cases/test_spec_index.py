"""The release SPEC is a portable index, independent of a host's knowledge base.

:module: spec.design-governance
"""
from pathlib import Path
import re
from urllib.parse import unquote

import pytest

ROOT = Path(__file__).resolve().parents[2]
LINK = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')
RESOURCE = re.compile(r'/r/(?:decision|thinking)_[0-9a-f]{12}')
RESOURCE_FILE = re.compile(r'/r/(?:decision|thinking|repo)_[0-9a-f]{12}/file/([^?#]+)')


def validate_spec_index(text: str, root: Path) -> list[str]:
    lines = text.splitlines()
    assert lines and lines[0].startswith('# '), 'Missing index title'
    targets = []
    for line in lines[1:]:
        if not line.strip():
            continue
        line = line.lstrip(' ')
        assert line.startswith('- '), 'Design prose belongs in linked documents'
        links = list(LINK.finditer(line))
        assert links and LINK.sub('', line[2:]).strip(' ·') == '', 'Expected named index links'
        for link in links:
            target = link[2]
            assert target not in targets, 'Duplicate index target'
            if target.startswith('/'):
                file_match = RESOURCE_FILE.fullmatch(target)
                if file_match:
                    relative = unquote(file_match[1])
                    assert '\\' not in relative and '\x00' not in relative, 'Unsafe resource file path'
                    assert all(part not in {'', '.', '..'} for part in relative.split('/')), 'Unsafe resource file path'
                else:
                    assert RESOURCE.fullmatch(target), 'Expected stable design resource ID or document address'
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


def test_index_accepts_nested_operational_and_design_documents(tmp_path):
    targets = [
        '/r/decision_0123456789ab',
        '/r/repo_0123456789ab/file/docs/accounts/execution-accounts.md',
        '/r/decision_0123456789ab/file/decision_0123456789ab/token-usage.md',
    ]
    text = '# SPEC.md\n- [Design](' + targets[0] + ')\n'
    text += ''.join('  - [Details](' + target + ')\n' for target in targets[1:])
    assert validate_spec_index(text, tmp_path) == targets


@pytest.mark.parametrize('entry', [
    '- [Old host](/share/Projects/private.md)',
    '- [Missing document](docs/missing.md)',
    '- [Escape](../../outside.md)',
    '- [Invalid resource](/r/decision_invalid)',
    '- [Host URL](https://example.com/design.md)',
    '- [Missing resource file](/r/repo_0123456789ab/file/)',
    '- [Traversal](/r/repo_0123456789ab/file/docs/../private.md)',
    '- [Encoded traversal](/r/decision_0123456789ab/file/%2e%2e/private.md)',
    '- [Noncanonical path](/r/repo_0123456789ab/file/docs//design.md)',
    '- [Query](/r/repo_0123456789ab/file/docs/design.md?raw=1)',
    '- [Nested prose](/r/decision_0123456789ab)\n  This is not an index entry.',
    '- [Design](/r/decision_0123456789ab) extra specification prose',
    'Implementation details do not belong in the index.',
    '- [Design](/r/decision_0123456789ab)\n- [Duplicate](/r/decision_0123456789ab)',
])
def test_index_rejects_missing_unsafe_duplicate_or_non_index_content(tmp_path, entry):
    with pytest.raises(AssertionError):
        validate_spec_index('# SPEC.md\n\n' + entry + '\n', tmp_path)
