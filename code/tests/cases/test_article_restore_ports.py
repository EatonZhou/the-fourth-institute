"""Restoration consumes Article representation through its installed feature.

:module: workflow.article
:importance: P1
"""
from copy import deepcopy
from contextlib import contextmanager
import threading
import socket
from types import SimpleNamespace

import pytest

from management_portal import portal_features
from management_portal.article_auto_registration import SOURCE
from management_portal.resources import article_restore_commit as restore
from management_portal.resources import article_restore, article_restore_decision
from tests.cases.test_article_restore_commit import apply, world  # noqa: F401
from tests.cases.test_article_restore_evidence import ARTICLE, ACTOR, SELECTOR
from tests.cases.permission_test_support import permissions  # noqa: F401


@pytest.mark.parametrize('providers', [(), (('article', SOURCE), ('duplicate', SOURCE)),
                                     (('article', object()),)])
def test_restore_requires_one_article_representation_before_creation(world, monkeypatch, providers):
    original = portal_features.contributions
    monkeypatch.setattr(portal_features, 'contributions',
        lambda hook: providers if hook == 'resource.article_registration' else original(hook))
    def forbidden(*args, **kwargs):
        pytest.fail('Unavailable Article representation must fail before preparing files')
    monkeypatch.setattr(restore, '_ArticleRecoveryCreation', forbidden)
    with pytest.raises(ValueError, match='Article.*contribution'):
        apply(world)
    assert not world.destination.exists()
    assert world.registry.get('article', ARTICLE) is None
    assert world.registry.get('article_restorations', ARTICLE) is None


def test_article_source_preserves_receipt_identity_and_recovery_without_registering(world):
    root = world.project / 'prepared'
    root.mkdir()
    root.joinpath('main.md').write_text('# A candidate title\nHistorical material\n')
    recovery = {'mode': 'partial_restore', 'original_author': None,
                'historical_acl': None, 'final_manuscript_verified': False}
    before = deepcopy(recovery)
    document, stored = SOURCE.prepare_restored_document(root, article_id=ARTICLE,
        title='Original receipt title', created_at='2026-11-09T19:50:30Z',
        project_id='Project', recovery=recovery)
    assert document['id'] == ARTICLE and document['title'] == 'Original receipt title'
    assert document['created_at'] == '2026-11-09T19:50:30Z'
    assert document['canonical_root'] == str(root) and document['primary_file'] == 'main.md'
    assert document['project_ids'] == ['Project']
    assert stored['recovery'] == before and 'created_by' not in stored
    assert recovery == before
    assert world.registry.get('article', ARTICLE) is None
    assert world.registry.get('article_restorations', ARTICLE) is None


def test_article_source_rejects_missing_partial_manuscript(world):
    root = world.project / 'empty'
    root.mkdir()
    with pytest.raises(FileNotFoundError, match='main.md'):
        SOURCE.prepare_restored_document(root, article_id=ARTICLE, title='Original',
            created_at='2026-11-09T19:50:30Z', project_id='Project', recovery={})


def _install_readers(monkeypatch, readers):
    original = portal_features.contributions
    monkeypatch.setattr(portal_features, 'contributions',
        lambda hook: readers if hook == 'resource.article_restore_reads' else original(hook))


def test_evidence_consumes_exact_read_capability(world, monkeypatch):
    calls = []
    def read(**kwargs):
        calls.append(kwargs)
        return deepcopy(world.page)
    _install_readers(monkeypatch, (('article', SimpleNamespace(read_tool_evidence=read)),))
    article_restore.read_tool_registration(article_id=ARTICLE, actor=ACTOR, **SELECTOR)
    assert calls == [{'actor': ACTOR, **SELECTOR}]


def test_decision_consumes_exact_messages_with_original_actor(world, monkeypatch):
    calls = []
    def read(**kwargs):
        calls.append(kwargs)
        return deepcopy(world.messages[kwargs['message_id']])
    _install_readers(monkeypatch, (('article', SimpleNamespace(read_message=read)),))
    article_restore_decision.verify(world.args, ACTOR, world.scope)
    assert calls == [{'actor': ACTOR, 'resident_id': ACTOR, 'message_id': mid}
                     for mid in ('msg_1111', 'msg_2222')]


@pytest.mark.parametrize('readers', [(), (('article', object()),),
                                      (('article', object()), ('duplicate', object()))])
def test_missing_or_ambiguous_readers_fail_closed(world, monkeypatch, readers):
    _install_readers(monkeypatch, readers)
    with pytest.raises(ValueError, match='Article.*read.*contribution'):
        article_restore.read_tool_registration(article_id=ARTICLE, actor=ACTOR, **SELECTOR)
    with pytest.raises(ValueError, match='Article.*read.*contribution'):
        article_restore_decision.verify(world.args, ACTOR, world.scope)


def test_final_owner_reads_stay_synchronous_inside_permission_transaction(world, monkeypatch):
    from psycopg.errors import LockNotAvailable
    from management_portal import permission_system
    from management_portal.society_service import resident_deleted_history, resident_protocol

    thread_id = threading.get_ident()
    active = {'connection': None, 'permission_connection': None}
    reads = []
    original_writer = restore.directory_writer
    @contextmanager
    def writer(*args, **kwargs):
        with original_writer(*args, **kwargs) as value:
            active['connection'] = value.connection
            try:
                yield value
            finally:
                active['connection'] = None
                active['permission_connection'] = None
    monkeypatch.setattr(restore, 'directory_writer', writer)
    _, engine, _ = permission_system.active().current()
    lock = type(engine.store).lock_version
    def lock_version(store, connection):
        result = lock(store, connection)
        if active['connection'] is connection:
            active['permission_connection'] = connection
        return result
    monkeypatch.setattr(type(engine.store), 'lock_version', lock_version)
    def record(kind, rid, actor):
        assert threading.get_ident() == thread_id and actor == ACTOR
        final = active['connection'] is not None
        if final:
            assert active['permission_connection'] is active['connection']
            # The outer transaction retains the real locks. This does not pass
            # its connection or snapshot to either Society read owner.
            held = active['connection'].execute(
                "SELECT c.relname FROM pg_locks l JOIN pg_class c ON c.oid=l.relation "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE l.pid=pg_backend_pid() AND l.granted AND l.mode='ExclusiveLock' "
                "AND n.nspname=%s", (engine.store.schema,)).fetchall()
            assert {'scheme_nodes', 'scheme_entries', 'scheme_objects'} <= {
                row['relname'] for row in held}
            with pytest.raises(LockNotAvailable):
                with engine.store.transaction() as other:
                    engine.store.execute(other, 'SELECT version FROM {s}.state FOR UPDATE NOWAIT').fetchone()
        reads.append((kind, final, rid))
    def history(rid, actor, **selectors):
        record('tool', rid, actor)
        assert selectors == {'section': 'tool_evidence', 'limit': 1,
                             **{k: v for k, v in SELECTOR.items() if k != 'resident_id'}}
        return deepcopy(world.page)
    def message(rid, actor, mid):
        record('message', rid, actor)
        return deepcopy(world.messages[mid])
    def no_rpc(*args, **kwargs):
        pytest.fail('Recovery reads must not enter the Society read pool')
    monkeypatch.setattr(socket, 'create_connection', no_rpc)
    monkeypatch.setattr(socket.socket, 'connect', no_rpc)
    monkeypatch.setattr(resident_deleted_history, 'read_history', history)
    monkeypatch.setattr(resident_protocol, 'get_resident_message', message)
    apply(world)
    assert [(kind, final) for kind, final, _ in reads] == [
        ('tool', False), ('message', False), ('message', False),
        ('message', True), ('message', True), ('tool', True)]
    assert all(rid == (SELECTOR['resident_id'] if kind == 'tool' else ACTOR)
               for kind, _, rid in reads)
