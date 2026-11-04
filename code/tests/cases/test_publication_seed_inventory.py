"""发布库存来自真实 Git 索引，不带入未选择的 Dev 内容。

:module: operations.deployment
:design: decision_08bb2d9141f9
:profiles: local.fixture
:risk: no-external-side-effects
"""
from pathlib import Path
import hashlib
import json
import subprocess

import pytest

from scripts.portal_seed_inventory import MANIFEST, PREFIX, refresh_staged_inventory, verify_publication_inventory


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.PIPE).decode().strip()


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, 'init', '-q')
    git(tmp_path, 'config', 'user.name', 'Inventory fixture')
    git(tmp_path, 'config', 'user.email', 'fixture@example.invalid')
    seed = tmp_path / PREFIX
    (seed / 'data').mkdir(parents=True)
    rows = []
    for name in ['first.md', 'other.md']:
        content = name.encode()
        (seed / 'data' / name).write_bytes(content)
        rows.append({'path':'data/' + name, 'size':len(content), 'sha256':hashlib.sha256(content).hexdigest()})
    (tmp_path / MANIFEST).write_text(json.dumps({'schema':'fixture', 'inventory':rows}))
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-qm', 'baseline')
    return tmp_path


def staged(root):
    return json.loads(git(root, 'show', ':' + MANIFEST))


def test_selected_seed_content_gets_matching_inventory_without_live_edits(repo):
    source = repo / PREFIX / 'data/first.md'
    source.write_text('staged source')
    git(repo, 'add', PREFIX + 'data/first.md')
    source.write_text('later edit must remain')
    (repo / PREFIX / 'data/other.md').write_text('unselected content')
    manifest = (repo / MANIFEST).read_bytes()
    assert refresh_staged_inventory(repo)
    rows = {r['path']:r for r in staged(repo)['inventory']}
    assert rows['data/first.md']['sha256'] == hashlib.sha256(b'staged source').hexdigest()
    assert rows['data/other.md']['sha256'] == hashlib.sha256(b'other.md').hexdigest()
    assert source.read_text() == 'later edit must remain'
    assert (repo / MANIFEST).read_bytes() == manifest
    assert not refresh_staged_inventory(repo)


def test_old_manifest_rows_cannot_revert_an_already_published_content_hash(repo):
    original = (repo / MANIFEST).read_bytes()
    (repo / PREFIX / 'data/first.md').write_text('new version')
    git(repo, 'add', PREFIX + 'data/first.md')
    refresh_staged_inventory(repo)
    git(repo, 'commit', '-qm', 'first publication')
    # The untouched live manifest is the older snapshot, like a concurrent
    # publisher that prepared inventory before the first publication.
    assert (repo / MANIFEST).read_bytes() == original
    git(repo, 'add', MANIFEST)
    assert refresh_staged_inventory(repo)
    first = next(r for r in staged(repo)['inventory'] if r['path'] == 'data/first.md')
    assert first['sha256'] == hashlib.sha256(b'new version').hexdigest()


def test_add_delete_and_unpublished_ancestors_are_in_the_same_closure(repo):
    baseline = git(repo, 'rev-parse', 'HEAD')
    (repo / PREFIX / 'data/first.md').unlink()
    (repo / PREFIX / 'data/new.md').write_text('new')
    git(repo, 'add', PREFIX)
    git(repo, 'commit', '-qm', 'unpublished source without inventory')
    source = git(repo, 'rev-parse', 'HEAD')
    with pytest.raises(ValueError, match='Release has not advanced'):
        verify_publication_inventory(repo, source, baseline)
    assert refresh_staged_inventory(repo, baseline)
    git(repo, 'commit', '-qm', 'repair closure')
    verify_publication_inventory(repo, 'HEAD', baseline)
    assert {r['path'] for r in staged(repo)['inventory']} == {'data/new.md', 'data/other.md'}
