import hashlib
from pathlib import Path
import pytest

from scripts.build_explanation_package import build


def test_pins_actual_bytes_and_omits_unselected_modules(tmp_path):
    (tmp_path / 'interaction.html').write_text('<button>比较</button>', encoding='utf-8')
    result = build(tmp_path, 'workspace_0123456789ab/file/lesson', title='解释', interaction='interaction.html')
    assert result['interaction']['sha256'] == hashlib.sha256((tmp_path / 'interaction.html').read_bytes()).hexdigest()
    assert result['interaction']['address'] == 'workspace_0123456789ab/file/lesson/interaction.html'
    assert 'video' not in result and 'video_plan' not in result


def test_cannot_pin_outside_package(tmp_path):
    package = tmp_path / 'package'
    package.mkdir()
    (tmp_path / 'outside.html').write_text('private')
    (package / 'link.html').symlink_to(tmp_path / 'outside.html')
    for filename in ('../outside.html', 'link.html', str(tmp_path / 'outside.html')):
        with pytest.raises(ValueError):
            build(package, 'workspace_0123456789ab/file/lesson', title='解释', interaction=filename)


def test_builder_cli_pins_only_explicit_review_assets(tmp_path, monkeypatch, capsys):
    import json
    import sys
    from scripts import build_explanation_package as builder
    (tmp_path/'notes.md').write_text('Explanation')
    (tmp_path/'alignment.json').write_text('{"start":0,"end":2}')
    (tmp_path/'source.txt').write_text('Actual fixture source')
    (tmp_path/'private.txt').write_text('Not selected')
    monkeypatch.setattr(sys, 'argv', ['build_explanation_package.py', str(tmp_path),
        '--address', 'workspace_0123456789ab/file/lesson', '--title', '解释', '--task', '理解对齐',
        '--quality', 'medium', '--supplement', 'notes.md', '--review-asset', 'alignment.json',
        '--review-asset', 'source.txt', '--review-asset', 'alignment.json'])
    builder.main()
    result = json.loads((tmp_path/'manifest.json').read_text())
    assert result['review_assets'] == [{'address': 'workspace_0123456789ab/file/lesson/'+name,
        'sha256': hashlib.sha256((tmp_path/name).read_bytes()).hexdigest()} for name in ('alignment.json', 'source.txt')]
    assert json.loads(capsys.readouterr().out)['review']['status'] == 'not_required'


@pytest.mark.parametrize('filename', ['../outside', '/absolute', 'link', 'directory/linked'])
def test_extra_review_assets_cannot_escape_or_follow_symlinks(tmp_path, filename):
    (tmp_path/'notes.md').write_text('Explanation')
    (tmp_path/'link').symlink_to(tmp_path/'notes.md')
    (tmp_path/'directory').mkdir()
    (tmp_path/'directory'/'linked').symlink_to(tmp_path/'notes.md')
    with pytest.raises(ValueError):
        build(tmp_path, 'workspace_0123456789ab/file/lesson', title='解释', supplement='notes.md', review_assets=[filename])
