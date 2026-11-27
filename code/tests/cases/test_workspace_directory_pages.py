"""Directory continuation through real Repo/Article owners and their HTTP adapters."""
from urllib.parse import urlencode, urlparse

import pytest

from management_portal import article_files, article_registration, http_articles, http_repo, repo_workspace_files
from knowledge_board_lan import knowledge_details
from tests.cases.test_repo_workspace_backend import _git, _repo


def collect(read, **kwargs):
    rows = []
    cursor = None
    for _ in range(50):
        page = read(cursor=cursor, **kwargs)
        assert page['truncated'] == bool(page['next_cursor'])
        rows.extend(page['entries'])
        cursor = page['next_cursor']
        if not cursor:
            return rows
    pytest.fail('continuation did not terminate')


def test_repo_continuation_keeps_git_visibility_and_stable_ties(tmp_path):
    repo_id, root = _repo(tmp_path)
    for i in range(1021):
        (root / f'file-{i:04}.txt').touch()
    for name in ('A', 'a', 'SS', 'ß'):
        (root / name).mkdir()
        (root / name / 'item').touch()
    (root / '.gitignore').write_text('ignored/\n')
    (root / 'ignored').mkdir()
    (root / 'ignored' / 'item').touch()
    rows = collect(lambda **kw: repo_workspace_files.list_repo_tree(repo_id, **kw), limit=400)
    names = [r['name'] for r in rows]
    assert len(names) == len(set(names)) == 1028
    assert names[:5] == ['A', 'a', 'src', 'SS', 'ß']
    assert '.git' not in names and 'ignored' not in names
    assert rows == sorted(rows, key=lambda r: (r['kind'] != 'directory', r['name'].casefold(), r['name']))
    page = repo_workspace_files.list_repo_tree(repo_id, limit=8)
    # Deleting an earlier entry cannot shift an offset and skip the next entry.
    (root / 'file-0000.txt').unlink()
    (root / 'file-0001a.txt').touch()
    rest = collect(lambda **kw: repo_workspace_files.list_repo_tree(repo_id, **kw), limit=400)
    follow = repo_workspace_files.list_repo_tree(repo_id, cursor=page['next_cursor'], limit=2000)
    key = lambda r: (r['kind'] != 'directory', r['name'].casefold(), r['name'])
    assert follow['entries'] == [r for r in rest if key(r) > key(page['entries'][-1])]
    with pytest.raises(ValueError, match='cursor'):
        repo_workspace_files.list_repo_tree(repo_id, 'src', cursor=page['next_cursor'])


@pytest.fixture
def manuscript(tmp_path):
    root = tmp_path / 'article'
    root.mkdir()
    (root / 'main.md').write_text('# Manuscript\n')
    record = article_registration.register_article(root, primary_file='main.md')
    return record['id'], root


def test_article_recursion_passes_old_limit_and_search_visits_later_pages(manuscript):
    article_id, root = manuscript
    (root / 'sections').mkdir()
    for i in range(2503):
        (root / 'sections' / f'{i:04}.md').touch()
    (root / 'z-last.md').write_text('continuation-needle')
    (root / 'build.log').touch()
    (root / 'alias.md').symlink_to(root / 'main.md')
    rows = collect(lambda **kw: article_files.list_article_files(article_id, **kw), limit=800)
    paths = [r['path'] for r in rows]
    assert len(paths) == len(set(paths)) == 2506
    assert paths[0] == 'sections'
    assert paths[-1] == 'z-last.md'
    assert next(r for r in rows if r['path'] == 'main.md')['primary']
    assert 'build.log' not in paths and 'alias.md' not in paths
    search = article_files.search_article_files(article_id, 'continuation-needle')
    assert [r['path'] for r in search['results']] == ['z-last.md']
    assert not search['truncated']
    # The knowledge detail must not quietly drop files after its old 2000 cap.
    detail = knowledge_details._candidate_article_files(root)
    assert root / 'z-last.md' in detail
    assert len(detail) > 2500


def test_article_cursor_rejects_replaced_binding_and_malformed_pages(manuscript):
    article_id, root = manuscript
    (root / 'second.md').touch()
    cursor = article_files.list_article_files(article_id, limit=1)['next_cursor']
    root.rename(root.with_name('original'))
    root.mkdir()
    (root / 'main.md').write_text('# Replacement\n')
    with pytest.raises(ValueError, match='cursor'):
        article_files.list_article_files(article_id, cursor=cursor)
    for value in ('garbage', 'e30='):
        with pytest.raises(ValueError, match='cursor'):
            article_files.list_article_files(article_id, cursor=value)
    for value in (0, -1, True, '3'):
        with pytest.raises(ValueError, match='limit'):
            article_files.list_article_files(article_id, limit=value)


@pytest.mark.parametrize('kind', ['repo', 'article'])
def test_http_continuation_rechecks_authority_before_read(kind, tmp_path, manuscript):
    if kind == 'repo':
        resource_id, _ = _repo(tmp_path)
        http = object.__new__(http_repo.RepoHttp)
        operation = http.workspace_get
        params = {'repo_id': resource_id, 'action': 'tree'}
    else:
        resource_id, root = manuscript
        (root / 'second.md').touch()
        http = object.__new__(http_articles.ArticlesHttp)
        http.session_actor = lambda request: 'reader'
        operation = http.workspace_read
        params = {'article_id': resource_id, 'action': 'files'}
    allowed = True
    checks = []
    def require(target, actor):
        checks.append((target, actor))
        if not allowed:
            raise PermissionError('revoked')
    http.require_resource = require
    http.respond = lambda request, status, body: (status, body)
    http.exception_response = lambda request, error: (403, {'error': str(error)})
    query = {'limit': 1}
    status, first = operation(None, urlparse('/?' + urlencode(query)), 'reader', params)
    assert status == 200 and first['next_cursor']
    query['cursor'] = first['next_cursor']
    status, second = operation(None, urlparse('/?' + urlencode(query)), 'reader', params)
    assert status == 200 and second['entries'] != first['entries']
    allowed = False
    assert operation(None, urlparse('/?' + urlencode(query)), 'reader', params)[0] == 403
    assert checks == [(resource_id, 'reader')] * 3
