"""Same-ID partial restoration uses owner transactions, not a new resource.

:module: workflow.article
:importance: P1
"""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json

import pytest

from management_portal.resources import article_restore_commit as restore
from management_portal.resources import article_restore_decision as decision
from management_portal.resources import article_restore_source as source
from management_portal.resources import article_restore_preview as preview
from management_portal.resources.registry_store import get_registry_store
from management_common.directory_cleanup_store import DirectoryCleanupStore
from tests.cases.permission_test_support import Fact, permissions  # noqa: F401
from tests.cases.test_article_restore_evidence import ARTICLE, RESIDENT, ACTOR, SELECTOR, history_page

SOURCE = 'workspace_444444444444'
USER = 'recovery-owner'


@pytest.fixture
def world(tmp_path, permissions, monkeypatch):
    from management_portal.society_service import resident_deleted_history, resident_protocol
    project = tmp_path / 'project'
    project.mkdir()
    permissions.users(USER)
    permissions.place(Fact('project', 'Project'), Fact('resident', RESIDENT), Fact('resident', ACTOR),
                      Fact('article', ARTICLE, {'project': 'Project'}), Fact('workspace', SOURCE))
    for principal in (ACTOR, USER):
        for rid, level in ((RESIDENT, '@admin'), (SOURCE, '@viewer'), ('Project', '@admin')):
            permissions.grant(principal, rid, level)
    permissions.grant('@principals', '@shared', '@viewer')
    registry = get_registry_store()
    registry._ensure()
    with registry.database.transaction() as conn:
        conn.execute('INSERT INTO deleted_identities(resource_id) VALUES (%s)', (ARTICLE,))
    old = tmp_path / 'deleted-paper'
    queue = DirectoryCleanupStore()
    with queue.database.transaction() as conn:
        cleanup_id = queue.enqueue(old, owner_id=ARTICLE, operation_id='old-delete', actor=ACTOR, connection=conn)
        conn.execute(f'UPDATE {queue.table} SET completed_at=clock_timestamp() WHERE cleanup_id=%s', (cleanup_id,))
    page = history_page()
    item = page['items'][0]
    call = json.loads(item['input_text'])
    call['arguments']['arguments']['fields']['path'] = str(old)
    item['input_text'] = json.dumps(call)
    wrapper = json.loads(item['output_text'])
    receipt = json.loads(wrapper['output'][0]['text'])
    receipt['result']['resource']['canonical_root'] = str(old)
    wrapper['output'][0]['text'] = json.dumps(receipt)
    item['output_text'] = json.dumps(wrapper)
    monkeypatch.setattr(resident_deleted_history, 'read_history', lambda *a, **kw: deepcopy(page))
    files = {'main.md': '# Original\n部分恢复；删除前最终版本未找齐。\n'.encode(),
             'old.pdf': b'%PDF-1.7 old draft', 'old-source/main.tex': b'old source'}
    address = SOURCE + '/file/candidate'
    def content(location, *, actor, max_bytes):
        assert actor == ACTOR
        relative = location.removeprefix(address).lstrip('/')
        if relative in files:
            return files[relative], 'application/octet-stream', {'target_kind': 'file'}
        prefix = relative + '/' if relative else ''
        names = {}
        for path in files:
            if path.startswith(prefix):
                rest = path[len(prefix):]
                name = rest.split('/')[0]
                names[name] = 'directory' if '/' in rest else 'text'
        data = json.dumps({'entries': [{'name': n, 'kind': k} for n,k in names.items()],
                           'truncated': False, 'hidden_count': 0}).encode()
        return data, 'application/vnd.tfi.directory+json', {'target_kind': 'directory'}
    service = SimpleNamespace(read_content=content,
        parse_address=lambda a: SimpleNamespace(root_id=a.split('/')[0], suffix=tuple(a.split('/')[1:])),
        get=lambda _id, **kw: {'resource_type': 'project', 'attributes': {'path': str(project), 'status': 'active'}})
    description, _ = source.snapshot(service, address, ACTOR)
    args = dict(article_id=ARTICLE, **SELECTOR, source_address=address, source_version=description['version'],
        primary_file='main.md', primary_version=hashlib.sha256(files['main.md']).hexdigest(),
        project_id='Project', target_relative_path='articles/' + ARTICLE,
        decision_resident_id=ACTOR, proposal_message_id='msg_1111', decision_message_id='msg_2222',
        inheritance_digest='0' * 64)
    args['inheritance_digest'] = restore.current_sharing(ACTOR, args)['digest']
    tool = restore.read_tool_registration(article_id=ARTICLE, actor=ACTOR, **SELECTOR)
    scope = restore.scope_for(args, tool, restore.current_sharing(ACTOR, args))
    proposal = {'content_parts': [{'type': 'text', 'text': decision.proposal_text(scope)},
        {'type': 'async_questions', 'answer_message_id': 'msg_2222',
         'questions': [{'title': '同意上述具体共享范围？', 'options': [decision.APPROVE, '不同意']}]}]}
    answer = {'role': 'user', 'author_id': USER, 'origin': {'sender': {'kind': 'user', 'id': USER}},
        'content_parts': [{'type': 'async_question_answer', 'source_message_id': 'msg_1111',
                          'answers': [{'question_index': 0, 'answer': decision.APPROVE}]}]}
    messages = {'msg_1111': proposal, 'msg_2222': answer}
    monkeypatch.setattr(resident_protocol, 'get_resident_message', lambda r,a,m: deepcopy(messages[m]))
    return SimpleNamespace(args=args, service=service, receipt=SimpleNamespace(get=lambda _: None),
        queue=queue, registry=registry, files=files, permissions=permissions, project=project,
        destination=project / 'articles' / ARTICLE, old=old, page=page, messages=messages, scope=scope)


def apply(world, **changes):
    return restore.restore(arguments=world.args | changes, actor=ACTOR, service=world.service,
                           receipt_store=world.receipt, operation_id='recovery-1')


def test_missing_old_ledgers_partial_restore_atomic_and_idempotent(world):
    result = apply(world)
    assert result['resource_id'] == ARTICLE and result['mode'] == 'partial_restore'
    assert result['portal_url'] == '/resource/article?item=' + ARTICLE
    assert result['final_version_recovered'] is False
    assert world.destination.joinpath('old-source/main.tex').read_bytes() == b'old source'
    doc = world.registry.get('article', ARTICLE)
    audit = world.registry.get('article_restorations', ARTICLE)
    assert doc['created_at'] == '2026-11-09T19:50:30Z' and doc['title'] == 'Original'
    assert 'created_by' not in doc and doc['recovery']['original_author'] is None
    assert doc['primary_file'] == 'main.md'
    assert audit['registration_ledger']['retention_reason'] == 'unknown'
    assert audit['hatg_receipt']['retention_reason'] == 'unknown'
    assert apply(world) == result
    from management_common.directory_retention import RetentionStore
    from management_common.directory_paths import cleanup_location
    retention = RetentionStore()
    with retention.database.read() as conn:
        row = conn.execute(f'SELECT owner_id,eligible_at FROM {retention.table} WHERE location=%s',
                           (cleanup_location(world.destination),)).fetchone()
        assert row['owner_id'] == ARTICLE and row['eligible_at'] is None


def test_legacy_replay_returns_canonical_url_without_rewriting_audit(world):
    from management_portal.resources.directory_registrations import directory_writer
    result = apply(world)
    with directory_writer('article_restorations') as writer:
        audit = writer.get('article_restorations', ARTICLE)
        audit['result']['portal_url'] = '/article?article=' + ARTICLE
        writer.put('article_restorations', ARTICLE, audit)
    previous = world.registry.get('article_restorations', ARTICLE)
    replayed = apply(world)
    assert replayed['portal_url'] == '/resource/article?item=' + ARTICLE
    assert replayed == result
    assert world.registry.get('article_restorations', ARTICLE) == previous


def test_restoration_announces_supported_creation_once(world, monkeypatch):
    from management_portal import resources
    from management_portal.resources.changes import ResourceChange
    changes = []
    def notify(**arguments):
        changes.append(ResourceChange(**arguments))
    monkeypatch.setattr(resources, 'get_resource_service',
                        lambda: SimpleNamespace(notify_source_changed=notify))
    apply(world)
    apply(world)
    assert len(changes) == 1
    assert changes[0].kind == 'created'
    assert changes[0].resource_id == ARTICLE and changes[0].resource_type == 'article'


@pytest.mark.parametrize('fault', ['cache_only', 'pending', 'source', 'shared', 'deny', 'decision', 'forged', 'proposal', 'occupied'])
def test_conflicts_do_not_clear_tombstone_or_publish(world, fault):
    if fault in {'cache_only', 'pending'}:
        with world.queue.database.transaction() as conn:
            if fault == 'cache_only':
                conn.execute(f'UPDATE {world.queue.table} SET location=%s', (str(world.project / 'cache'),))
            else:
                conn.execute(f'UPDATE {world.queue.table} SET completed_at=NULL')
    elif fault == 'source':
        world.files['old-source/main.tex'] = b'changed recursive descendant'
    elif fault == 'shared':
        world.args['inheritance_digest'] = '0' * 64
    elif fault == 'deny':
        world.permissions.deny(ACTOR, ARTICLE, '@admin')
    elif fault == 'decision':
        world.messages['msg_2222']['content_parts'][0]['answers'][0]['answer'] = '不同意'
    elif fault == 'forged':
        world.messages['msg_2222']['origin']['sender']['kind'] = 'resident'
    elif fault == 'proposal':
        world.messages['msg_1111']['content_parts'][0]['text'] = '恢复旧稿'
    else:
        world.destination.mkdir(parents=True)
        (world.destination / 'unrelated').write_text('keep')
    with pytest.raises((ValueError, PermissionError, FileExistsError)):
        apply(world)
    assert world.registry.get('article', ARTICLE) is None
    assert world.registry.inspect_article_identity('article', ARTICLE)['status'] == 'tombstoned'
    assert not (world.destination / 'main.md').exists()
    if fault == 'occupied':
        assert (world.destination / 'unrelated').read_text() == 'keep'


def test_owner_commit_failure_reclaims_only_staged_files(world, monkeypatch):
    from management_portal.resources.registry_store import RegistryWriter
    original = RegistryWriter.restore_article
    def fail(*a, **kw):
        original(*a, **kw)
        raise RuntimeError('injected after registry switch')
    monkeypatch.setattr(RegistryWriter, 'restore_article', fail)
    with pytest.raises(RuntimeError, match='injected'):
        apply(world)
    assert world.registry.get('article', ARTICLE) is None
    assert world.registry.get('article_restorations', ARTICLE) is None
    assert not world.destination.exists()
    assert world.registry.inspect_article_identity('article', ARTICLE)['status'] == 'tombstoned'


def test_ordinary_creation_still_rejects_tombstone(world):
    from management_portal.resources.creation import Creation
    with pytest.raises(KeyError):
        with Creation(ARTICLE, actor=ACTOR, payload={}, operation_id='ordinary').prepare([(world.destination, 'directory')]):
            pytest.fail('ordinary creation reused deleted identity')


@pytest.mark.parametrize('fault', ['source', 'prepared', 'symlink', 'decision', 'revocation', 'sharing', 'project_delete', 'cleanup'])
def test_final_owner_transaction_rechecks_preparation_changes(world, monkeypatch, fault):
    original = restore._ArticleRecoveryCreation.publish
    def change(creation, initialize):
        original(creation, initialize)
        if fault == 'source':
            world.files['old-source/main.tex'] = b'later edit'
        elif fault == 'prepared':
            (world.destination / 'old.pdf').write_bytes(b'replaced')
        elif fault == 'symlink':
            (world.destination / 'old.pdf').unlink()
            (world.destination / 'old.pdf').symlink_to(world.project / 'outside')
        elif fault == 'decision':
            world.messages['msg_2222']['content_parts'][0]['answers'][0]['answer'] = '不同意'
        elif fault == 'revocation':
            world.permissions.deny(ACTOR, ARTICLE, '@admin')
        elif fault == 'sharing':
            world.permissions.grant('other', 'Project', '@viewer')
        elif fault == 'project_delete':
            with world.registry.database.transaction() as conn:
                conn.execute('INSERT INTO deleted_identities(resource_id) VALUES (%s)', ('Project',))
        elif fault == 'cleanup':
            with world.queue.database.transaction() as conn:
                conn.execute(f'UPDATE {world.queue.table} SET completed_at=completed_at + interval \'1 second\'')
    monkeypatch.setattr(restore._ArticleRecoveryCreation, 'publish', change)
    with pytest.raises((ValueError, PermissionError, OSError, KeyError)):
        apply(world)
    assert world.registry.get('article', ARTICLE) is None
    assert not world.destination.exists()


def test_crash_after_publish_resumes_same_owned_operation(world, monkeypatch):
    original = restore._ArticleRecoveryCreation.publish
    class Crash(BaseException):
        pass
    def crash(creation, initialize):
        original(creation, initialize)
        raise Crash()
    monkeypatch.setattr(restore._ArticleRecoveryCreation, 'publish', crash)
    with pytest.raises(Crash):
        apply(world)
    inode = world.destination.stat().st_ino
    assert world.registry.get('article', ARTICLE) is None
    monkeypatch.setattr(restore._ArticleRecoveryCreation, 'publish', original)
    assert apply(world)['resource_id'] == ARTICLE
    assert world.destination.stat().st_ino == inode


def test_live_registration_and_wrong_operation_cannot_be_replaced(world):
    apply(world)
    from management_portal.resources.directory_registrations import directory_writer
    with pytest.raises(ValueError, match='Another restoration'):
        restore.restore(arguments=world.args, actor=ACTOR, service=world.service,
                        receipt_store=world.receipt, operation_id='another-operation')
    with directory_writer('article') as writer:
        document = writer.get('article', ARTICLE)
        document['title'] = 'Later legitimate edit'
        writer.put('article', ARTICLE, document)
    with pytest.raises(ValueError, match='changed'):
        apply(world)
    assert world.registry.get('article', ARTICLE)['title'] == 'Later legitimate edit'


@pytest.mark.parametrize('fault', ['input', 'receipt', 'ledger', 'history_denied'])
def test_independent_conflicts_and_history_authorization_still_block(world, monkeypatch, fault):
    if fault == 'input':
        world.page['items'][0]['content_sha256'] = 'b' * 64
    elif fault == 'receipt':
        world.receipt.get = lambda _: {'instruction': 'RegisterResource', 'status': 'failed'}
    elif fault == 'ledger':
        from management_portal.resources.registration import ResourceRegistrationStore
        store = ResourceRegistrationStore()
        fields = json.loads(world.page['items'][0]['input_text'])['arguments']['arguments']['fields']
        store.reserve('article', fields, actor=RESIDENT, operation_id='RegisterResource:original-registration',
                      allocate=lambda: ARTICLE)
        # Incomplete and not deleted is a conflict, not missing historical data.
    else:
        from management_portal.society_service import resident_deleted_history
        def denied(*a, **kw):
            raise PermissionError('actual AMA history denied')
        monkeypatch.setattr(resident_deleted_history, 'read_history', denied)
    with pytest.raises((ValueError, PermissionError, RuntimeError)):
        apply(world)
    assert world.registry.get('article', ARTICLE) is None


def test_source_budget_and_hidden_or_symbolic_inputs_are_rejected(world):
    original = world.service.read_content
    for alteration in ({'truncated': True}, {'hidden_count': 1},
                       {'entries': [{'name': 'escape', 'kind': 'symlink'}]},
                       {'entries': [{'name': '../escape', 'kind': 'text'}]}):
        def read(*a, **kw):
            body, mime, result = original(*a, **kw)
            if result['target_kind'] == 'directory':
                body = json.dumps(json.loads(body) | alteration).encode()
            return body, mime, result
        world.service.read_content = read
        with pytest.raises(ValueError):
            apply(world)
    assert not world.destination.exists()


def test_concurrent_same_operation_replays_one_commit(world, monkeypatch):
    import contextvars
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    from threading import Event, Lock
    published, admitted, release = Event(), Event(), Event()
    lock = Lock()
    count = 0
    original_prepare = restore._ArticleRecoveryCreation.prepare
    original_publish = restore._ArticleRecoveryCreation.publish
    @contextmanager
    def prepare(creation, *a, **kw):
        nonlocal count
        with lock:
            count += 1
            number = count
        if number == 2:
            admitted.set()
        with original_prepare(creation, *a, **kw) as value:
            yield value
    def publish(creation, initialize):
        original_publish(creation, initialize)
        published.set()
        assert release.wait(8)
    monkeypatch.setattr(restore._ArticleRecoveryCreation, 'prepare', prepare)
    monkeypatch.setattr(restore._ArticleRecoveryCreation, 'publish', publish)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(contextvars.copy_context().run, apply, world)
        assert published.wait(8)
        second = pool.submit(contextvars.copy_context().run, apply, world)
        try:
            assert admitted.wait(8)
        finally:
            release.set()
        result = first.result(timeout=10)
        try:
            repeated = second.result(timeout=10)
        except RuntimeError as exc:
            # Creation's writer fence may refuse a still-active same-process
            # initializer. It must not abort that writer; a later retry reads
            # the one committed receipt.
            assert 'writer ownership is active or unknown' in str(exc)
            repeated = apply(world)
        assert result == repeated
    assert world.registry.get('article_restorations', ARTICLE)['result']['mode'] == 'partial_restore'


def test_after_restore_identity_delete_keeps_files_project_delete_removes(world):
    apply(world)
    from management_portal.resources.directory_registrations import unregister_identity, unregister_tree
    with world.registry.database.transaction() as connection:
        unregister_identity(RESIDENT, connection=connection, actor=ACTOR)
    assert world.destination.joinpath('main.md').is_file()
    # The directory owner still selects Project-owned Article roots for an
    # explicit Project deletion, including the new resource-owned retention.
    with world.registry.database.transaction() as connection:
        result = unregister_tree(roots=[world.project], owner_ids=['Project'], actor=ACTOR,
                                 preserve_resources=False, connection=connection)
    assert ARTICLE in str(result)
    assert world.registry.get('article', ARTICLE) is None


def test_preview_permission_is_not_submit_permission(permissions):
    from management_portal import permission_system
    from management_portal.permission_system.scheme.repository import builtin_documents
    from management_portal.hatg.catalog import declaration
    permissions.place(Fact('project', 'Project'), Fact('resident', RESIDENT), Fact('resident', ACTOR),
                      Fact('article', ARTICLE, {'project': 'Project'}), Fact('workspace', SOURCE))
    args = {'article_id': ARTICLE, 'resident_id': RESIDENT, 'project_id': 'Project', 'source_address': SOURCE}
    for key, rid in args.items():
        permissions.grant(ACTOR, rid, 'PreviewArticleRestore:' + key)
    assert permission_system.allows(ACTOR, 'PreviewArticleRestore', args)
    assert not permission_system.allows(ACTOR, 'RestoreArticle', args)
    assert declaration('RestoreArticle').mutates is True
    assert builtin_documents()


@pytest.mark.parametrize('missing', ['article_id', 'resident_id', 'source_address', 'project_id'])
def test_restore_demands_each_exact_action_slot(permissions, missing):
    from management_portal import permission_system
    permissions.place(Fact('project', 'Project'), Fact('resident', RESIDENT), Fact('resident', ACTOR),
                      Fact('article', ARTICLE, {'project': 'Project'}), Fact('workspace', SOURCE))
    args = {'article_id': ARTICLE, 'resident_id': RESIDENT, 'project_id': 'Project', 'source_address': SOURCE}
    for key, rid in args.items():
        if key != missing:
            permissions.grant(ACTOR, rid, 'RestoreArticle:' + key)
    if missing != 'source_address':
        assert not permission_system.allows(ACTOR, 'RestoreArticle', args)
    # Builtin shared viewer rules can already satisfy source read; an exact
    # deny must override both those rules and an explicit leaf permit.
    permissions.grant(ACTOR, args[missing], 'RestoreArticle:' + missing)
    assert permission_system.allows(ACTOR, 'RestoreArticle', args)
    permissions.deny(ACTOR, args[missing], 'RestoreArticle:' + missing)
    assert not permission_system.allows(ACTOR, 'RestoreArticle', args)


def test_existing_permanent_article_grant_not_silently_accepted(world):
    world.permissions.grant('unexpected-reader', ARTICLE, '@viewer')
    with pytest.raises(ValueError, match='direct Article permits'):
        apply(world)
    assert world.registry.get('article', ARTICLE) is None


def test_current_denies_are_preserved_and_no_creator_grant_is_minted(world):
    from management_portal import permission_system
    world.permissions.deny('blocked-reader', ARTICLE, '@viewer')
    sharing = restore.current_sharing(ACTOR, world.args)
    world.args['inheritance_digest'] = sharing['digest']
    tool = restore.read_tool_registration(article_id=ARTICLE, actor=ACTOR, **SELECTOR)
    scope = restore.scope_for(world.args, tool, sharing)
    world.messages['msg_1111']['content_parts'][0]['text'] = decision.proposal_text(scope)
    apply(world)
    rows = permission_system.manage('ListPermissionEntries',
        {'r': ARTICLE, 'r_scope': 'exact', 'temporary': False, 'limit': 256}, actor=ACTOR)['entries']
    assert any(row['p'] == 'blocked-reader' and row['effect'] == 'deny' for row in rows)
    assert not any(row['effect'] == 'permit' for row in rows)


def test_user_revocation_and_unlinked_yes_do_not_authorize_sharing(world):
    world.messages['msg_2222']['content_parts'] = [{'type': 'text', 'text': '同意'}]
    with pytest.raises(ValueError, match='explicitly approved'):
        apply(world)
    world.messages['msg_2222']['quote'] = {'message_id': 'msg_1111'}
    world.permissions.deny(USER, SOURCE, '@viewer')
    with pytest.raises(PermissionError, match='deciding User'):
        apply(world)


def test_replaced_destination_is_not_removed_by_failed_restore(world, monkeypatch):
    original = restore._ArticleRecoveryCreation.publish
    retained = world.project / 'original-owned-inode'
    def replace(creation, initialize):
        original(creation, initialize)
        world.destination.rename(retained)
        world.destination.mkdir()
        (world.destination / 'unrelated').write_text('do not delete')
    monkeypatch.setattr(restore._ArticleRecoveryCreation, 'publish', replace)
    with pytest.raises(RuntimeError, match='cleanup pending'):
        apply(world)
    assert (world.destination / 'unrelated').read_text() == 'do not delete'
    assert retained.joinpath('main.md').exists()
    assert world.registry.get('article', ARTICLE) is None
    with world.queue.database.read() as conn:
        assert conn.execute(f'SELECT 1 FROM {world.queue.reservations}').fetchone()


def test_decision_and_parameter_budgets(world):
    with pytest.raises(ValueError, match='selector'):
        apply(world, decision_message_id='msg_' + 'a' * 200)
    world.messages['msg_1111']['content_parts'][0]['text'] += 'x' * 65536
    with pytest.raises(ValueError, match='proposal'):
        apply(world)
    assert not world.destination.exists()
