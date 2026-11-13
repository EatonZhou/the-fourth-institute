"""Source anchors, concurrent replies, author checks and HTTP permission boundary."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch, Mock
import uuid

import pytest
from management_portal import article_comments as comments
from management_portal.http_articles import ArticlesHttp


def create(article, source='before 😀 selected text after'):
    quote = 'selected text'
    start = len(source[:source.index(quote)].encode('utf-16-le')) // 2
    with patch.object(comments.article_files, 'read_article_file', return_value={'content': source, 'path': 'main.tex'}):
        return comments.mutate(article, 'alice', {'action': 'create', 'path': 'main.tex',
            'start': start, 'end': start + len(quote), 'quote': quote, 'text': 'Please clarify'})


def test_shared_threads_concurrency_authorship_and_versions():
    article = 'article_' + uuid.uuid4().hex[:12]
    t = create(article)
    assert t['anchor']['start'] == 10
    assert t['anchor']['prefix'] == 'before 😀 '
    assert comments.list_threads('article_' + uuid.uuid4().hex[:12]) == []
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda n: comments.mutate(article, 'bob', {'action': 'reply', 'thread_id': t['id'], 'text': f'reply {n}'}), range(4)))
    t = comments.list_threads(article)[0]
    assert len(t['messages']) == 5 and t['version'] == 5
    with pytest.raises(PermissionError):
        comments.mutate(article, 'bob', {'action': 'edit', 'thread_id': t['id'], 'version': t['version'], 'message_id': t['messages'][0]['id'], 'text': 'forged'})
    with pytest.raises(comments.CommentConflict):
        comments.mutate(article, 'alice', {'action': 'resolve', 'thread_id': t['id'], 'version': 1})
    t = comments.mutate(article, 'bob', {'action': 'resolve', 'thread_id': t['id'], 'version': t['version']})
    with pytest.raises(comments.CommentConflict):
        comments.mutate(article, 'alice', {'action': 'reply', 'thread_id': t['id'], 'text': 'late'})
    t = comments.mutate(article, 'alice', {'action': 'reopen', 'thread_id': t['id'], 'version': t['version']})
    t = comments.mutate(article, 'alice', {'action': 'edit', 'thread_id': t['id'], 'version': t['version'], 'message_id': t['messages'][0]['id'], 'text': 'clarified'})
    assert t['messages'][0]['text'] == 'clarified'
    t = comments.mutate(article, 'alice', {'action': 'delete', 'thread_id': t['id'], 'version': t['version'], 'message_id': t['messages'][0]['id']})
    assert t['messages'][0]['deleted'] and len(t['messages']) == 5


def test_reject_changed_source_invalid_offsets_and_paths():
    with patch.object(comments.article_files, 'read_article_file', return_value={'content': 'changed', 'path': 'main.tex'}):
        with pytest.raises(ValueError):
            comments.mutate('unused', 'alice', {'action': 'create', 'path': 'main.tex', 'start': 0, 'end': 3, 'quote': 'old', 'text': 'x'})
    with patch.object(comments.article_files, 'read_article_file', side_effect=ValueError('outside article root')):
        with pytest.raises(ValueError):
            comments.mutate('unused', 'alice', {'action': 'create', 'path': '../other.tex'})


def test_comments_http_enforces_read_and_write_permissions():
    http = object.__new__(ArticlesHttp)
    http.session_actor = lambda r: 'viewer'
    http.require_resource = Mock()
    http.respond = lambda r, code, body: (code, body)
    http.read_json = Mock(return_value={'action': 'create', 'author': 'forged'})
    http.exception_response = lambda r, e: (_ for _ in ()).throw(e)
    params = {'article_id': 'article_123456789abc'}
    with patch('management_portal.http_articles.permission_system.allows', return_value=False), patch.object(comments, 'list_threads', return_value=[]) as read, patch.object(comments, 'mutate') as write:
        assert http.comments(SimpleNamespace(command='GET'), None, None, params)[1]['can_write'] is False
        assert http.comments(SimpleNamespace(command='POST'), None, None, params)[0] == 403
        write.assert_not_called()
        http.require_resource.side_effect = PermissionError('private')
        assert http.comments(SimpleNamespace(command='GET'), None, None, params)[0] == 403
        assert read.call_count == 1
