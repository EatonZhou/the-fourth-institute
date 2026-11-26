"""Bounded preflight observes evidence without conferring recovery authority.

:module: workflow.article
:importance: P1
"""
from copy import deepcopy
import hashlib
import json
import re
from types import SimpleNamespace

import pytest

from management_common.directory_cleanup_store import DirectoryCleanupStore
from management_portal import permission_system
from management_portal.resources import article_restore_preview as preview
from management_portal.resources.registry_store import get_registry_store
from management_portal.resources.registration import ResourceRegistrationStore
from tests.cases.permission_test_support import Fact, permissions  # noqa: F401
from tests.cases.test_article_restore_evidence import ARTICLE, RESIDENT, ACTOR, SELECTOR, FIELDS, history_page

SOURCE = 'workspace_444444444444'
ADDRESS = SOURCE + '/file/source'


@pytest.fixture
def world(tmp_path, permissions, monkeypatch):
    from management_portal.society_service import resident_deleted_history
    from management_portal.resources import directory_registrations
    project = tmp_path / 'project'
    project.mkdir()
    permissions.place(Fact('project', 'Project'), Fact('resident', RESIDENT), Fact('resident', ACTOR),
                      Fact('article', ARTICLE, {'project': 'Project'}), Fact('workspace', SOURCE))
    for resource, level in ((ARTICLE, '@admin'), (RESIDENT, '@admin'), (SOURCE, '@viewer'), ('Project', '@contributor')):
        permissions.grant(ACTOR, resource, level)
    permissions.grant('@principals', '@shared', '@viewer')
    registry = get_registry_store()
    registry._ensure()
    with registry.database.transaction() as conn:
        conn.execute('INSERT INTO deleted_identities(resource_id) VALUES (%s)', (ARTICLE,))
    queue = DirectoryCleanupStore()
    old = tmp_path / 'old'
    old.mkdir()
    with queue.database.transaction() as conn:
        cleanup = queue.enqueue(old, owner_id=ARTICLE, operation_id='original-delete', actor=ACTOR, connection=conn)
        conn.execute(f'UPDATE {queue.table} SET completed_at=clock_timestamp() WHERE cleanup_id=%s', (cleanup,))
    page = history_page()
    monkeypatch.setattr(resident_deleted_history, 'read_history', lambda *a, **kw: deepcopy(page))
    listing = json.dumps({'entries': [{'name': 'latest.pdf'}], 'truncated': False}).encode()
    pdf = b'%PDF-1.7\nrecovered original provided bytes'
    arguments = dict(article_id=ARTICLE, **SELECTOR, source_address=ADDRESS,
        source_version=hashlib.sha256(listing).hexdigest(), primary_file='latest.pdf',
        primary_version=hashlib.sha256(pdf).hexdigest(), project_id='Project', target_relative_path='articles/' + ARTICLE)
    calls = []
    def content(address, *, actor, max_bytes):
        calls.append(address)
        assert actor == ACTOR and max_bytes == preview.SOURCE_BUDGET
        if address == ADDRESS:
            return listing, 'application/vnd.tfi.directory+json', {'target_kind': 'directory'}
        return pdf, 'application/pdf', {'target_kind': 'file'}
    service = SimpleNamespace(
        parse_address=lambda address: SimpleNamespace(root_id=address.split('/')[0], suffix=tuple(address.split('/')[1:])),
        read_content=content,
        get=lambda identifier, **kw: {'resource_type': 'project', 'attributes': {'path': str(project), 'status': 'active'}})
    receipt = SimpleNamespace(get=lambda _id: None)
    return SimpleNamespace(args=arguments, service=service, receipt=receipt, queue=queue, registry=registry,
                           project=project, permissions=permissions, page=page, calls=calls)


def run(world, **changes):
    return preview.preview(arguments=world.args | changes, actor=ACTOR, service=world.service, receipt_store=world.receipt)


def test_missing_ledger_keeps_tool_facts_without_any_business_write(world, monkeypatch):
    from management_common.postgres_database import PostgresConnection
    original = PostgresConnection.execute
    observed = []
    def only_read(self, query, *a, **kw):
        sql = str(query).strip().upper()
        observed.append(sql)
        assert sql.startswith(('SELECT', 'SET', 'SHOW', 'WITH')), sql
        assert not re.search(r'\b(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE)\b', sql), sql
        return original(self, query, *a, **kw)
    monkeypatch.setattr(PostgresConnection, 'execute', only_read)
    result = run(world)
    assert observed
    assert result['mode'] == 'dry_run' and result['can_apply'] is False and result['apply_supported'] is False
    assert result['tool_evidence']['record']['resource_id'] == ARTICLE
    assert result['registration_ledger']['status'] == 'not_found'
    assert result['registration_ledger']['retention_reason'] == 'unknown'
    assert result['hatg_receipt']['retention_reason'] == 'unknown'
    assert result['identity']['status'] == 'tombstoned'
    assert result['cleanup']['cleanup_terminal'] and result['cleanup']['clear']
    assert result['decision']['status'] == 'unknown'
    assert result['current_inheritance']['private_to_requester'] is False
    assert any(row['p'] == '@principals' for row in result['current_inheritance']['entries'])
    assert not (world.project / 'articles').exists()


@pytest.mark.parametrize('change', ['delete_row', 'fingerprint', 'actor', 'completed', 'deleted'])
def test_independent_ledger_missing_or_conflict_never_erases_tool_facts(world, change):
    store = ResourceRegistrationStore()
    store.reserve('article', FIELDS, actor=RESIDENT, operation_id='RegisterResource:original-registration', allocate=lambda: ARTICLE)
    store.complete(ARTICLE)
    store.forget(ARTICLE)
    with store._database.transaction() as conn:
        if change == 'delete_row':
            conn.execute('DELETE FROM resource_registration_operations')
        else:
            value = False if change in ('completed', 'deleted') else 'conflicting-value'
            conn.execute(f'UPDATE resource_registration_operations SET {change}=%s', (value,))
    result = run(world)
    assert result['tool_evidence']['status'] == 'observed'
    assert result['registration_ledger']['status'] == ('not_found' if change == 'delete_row' else 'conflict')


@pytest.mark.parametrize('fault', ['receipt_status', 'receipt_caller', 'receipt_id', 'bad_tool'])
def test_independent_conflicts_are_preserved(world, fault):
    row = {'instruction': 'RegisterResource', 'status': 'committed', 'caller': RESIDENT, 'references': {'resource_id': ARTICLE}}
    if fault == 'bad_tool':
        world.page['items'][0]['status'] = 'failed'
    else:
        if fault == 'receipt_status': row['status'] = 'failed'
        if fault == 'receipt_caller': row['caller'] = ACTOR
        if fault == 'receipt_id': row['references']['resource_id'] = 'article_555555555555'
    world.receipt.get = lambda _: row
    result = run(world)
    if fault == 'bad_tool':
        assert result['tool_evidence']['status'] == 'conflict'
        assert result['registration_ledger']['status'] == 'unsupported'
    else:
        assert result['hatg_receipt']['status'] == 'conflict'
        assert not result['hatg_receipt']['substitute_commit_evidence']


def test_absent_tool_and_source_budget_are_explicit_without_fabricating_expiry(world):
    world.page['items'] = []
    result = run(world)
    assert result['tool_evidence'] == {
        'status': 'indeterminate', 'reason': 'owner_no_items_without_absence_proof'}
    def oversized(*a, **kw): raise ValueError('File exceeds byte limit')
    world.service.read_content = oversized
    assert run(world)['source'] == {'status': 'unsupported', 'reason': 'source_content_budget'}


@pytest.mark.parametrize('gap,status', [
    ('external_or_unproven_source_requires_separate_authorization', 'unsupported'),
    ('unsupported_segment_type', 'unsupported'),
    ('tool_container_missing_or_exceeds_parse_limit', 'indeterminate'),
    ('no_supported_retained_tool_items', 'unsupported'),
    ('future_owner_gap', 'indeterminate'),
    (None, 'indeterminate'),
])
def test_owner_tool_gaps_do_not_claim_content_was_not_retained(world, monkeypatch, gap, status):
    world.page['items'] = []
    world.page['availability'] = {'gap': gap} if gap else {}
    def unverified(*a, **kw):
        pytest.fail('Unavailable tool evidence must not be used to query registration proofs')
    monkeypatch.setattr(ResourceRegistrationStore, 'verify_deleted', unverified)
    world.receipt.get = unverified
    result = run(world)
    assert result['tool_evidence'] == {
        'status': status, 'reason': gap or 'owner_no_items_without_absence_proof'}
    assert result['registration_ledger']['status'] == 'unsupported'
    assert result['hatg_receipt']['status'] == 'unsupported'
    assert result['decision']['status'] == 'unknown'
    assert result['can_apply'] is False


@pytest.mark.parametrize('failure', ['permission', 'ama', 'absent_ordinal'])
def test_owner_exceptions_are_not_reclassified_as_tool_gaps(world, monkeypatch, failure):
    from management_portal.society_service import resident_deleted_history
    from management_portal.permission_system import personal_ama
    error = {'permission': PermissionError('history denied'),
             'ama': personal_ama.NotFound(RESIDENT),
             'absent_ordinal': KeyError('Tool ordinal is not a member of this Resident trace')}[failure]
    def fail(*a, **kw):
        raise error
    monkeypatch.setattr(resident_deleted_history, 'read_history', fail)
    with pytest.raises(type(error)) as raised:
        run(world)
    assert raised.value is error
    assert not world.calls


def test_source_partial_listing_and_wrong_pdf_remain_visible(world):
    original = world.service.read_content
    def partial(address, **kw):
        data, mime, resolved = original(address, **kw)
        if address == ADDRESS:
            data = json.dumps({'entries': [], 'truncated': True}).encode()
        else:
            data, mime = b'wrong bytes', 'text/plain'
        return data, mime, resolved
    world.service.read_content = partial
    result = run(world)['source']
    assert result['truncated'] and not result['recursive_tree_verified']
    assert {'source_version', 'primary_version', 'primary_not_pdf'} <= set(result['conflicts'])


@pytest.mark.parametrize('role,resource', [('@admin', ARTICLE), ('@admin', RESIDENT),
                                         ('@viewer', SOURCE), ('@contributor', 'Project')])
def test_each_permission_denial_aborts_before_content_reads(world, role, resource):
    world.permissions.deny(ACTOR, resource, role)
    with pytest.raises(PermissionError):
        run(world)
    assert not world.calls


def test_history_ama_and_source_denials_are_not_missing_evidence(world, monkeypatch):
    from management_portal.society_service import resident_deleted_history
    from management_portal.permission_system import personal_ama
    original = resident_deleted_history.read_history
    def private(*a, **kw): raise personal_ama.NotFound(RESIDENT)
    monkeypatch.setattr(resident_deleted_history, 'read_history', private)
    with pytest.raises(personal_ama.NotFound): run(world)
    monkeypatch.setattr(resident_deleted_history, 'read_history', original)
    def denied(*a, **kw): raise PermissionError('source denied')
    world.service.read_content = denied
    with pytest.raises(PermissionError, match='source denied'): run(world)


@pytest.mark.parametrize('change', [dict(apply=True), dict(decision_message_id='msg_fake'),
    dict(target_relative_path='../outside'), dict(primary_file='/secret'), dict(primary_file='a//b'),
    dict(ordinal=True), dict(tool_index=-1), dict(expected_hash='short'), dict(source_address='@shared')])
def test_parameters_cannot_expand_evidence_scope(world, change):
    with pytest.raises(ValueError): run(world, **change)
    assert not world.calls


def test_source_versions_scope_changes_and_current_sharing_are_recomputed(world):
    before = run(world)
    altered = run(world, source_version='f' * 64)
    assert 'source_version' in altered['source']['conflicts']
    assert altered['scope_digest'] != before['scope_digest']
    target = run(world, target_relative_path='articles/different')
    assert target['scope_digest'] != before['scope_digest']
    world.permissions.grant('@members', 'Project', '@viewer')
    after = run(world)
    assert after['current_inheritance']['digest'] != before['current_inheritance']['digest']
    assert after['scope_digest'] != before['scope_digest']
    assert after['decision']['status'] == 'unknown'


def test_target_symlink_occupancy_and_late_permission_revocation(world):
    (world.project / 'articles').symlink_to(world.project.parent, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'): run(world)
    (world.project / 'articles').unlink()
    target = world.project / world.args['target_relative_path']
    target.mkdir(parents=True)
    assert run(world)['target']['status'] == 'conflict'
    original = world.service.read_content
    def revoke(*a, **kw):
        result = original(*a, **kw)
        world.permissions.deny(ACTOR, ARTICLE, '@admin')
        return result
    world.service.read_content = revoke
    with pytest.raises(PermissionError): run(world)


def test_owner_missing_tables_do_not_initialize_or_report_clear(tmp_path):
    from management_common.portal_database import portal_database
    from management_portal.resources.registry_store import RegistryStore
    registry = RegistryStore()
    registry.database = portal_database(schema='absent_preview_registry')
    queue = DirectoryCleanupStore(create=False)
    queue.database = portal_database(schema='absent_preview_cleanup')
    queue.table = f'"{queue.database.schema}".cleanup'
    queue.reservations = f'"{queue.database.schema}".path_reservations'
    assert registry.inspect_article_identity('article', ARTICLE)['status'] == 'indeterminate'
    assert queue.inspect_restore(owner_id=ARTICLE, path=tmp_path)['status'] == 'indeterminate'
    with queue.database.read() as conn:
        assert conn.execute('SELECT to_regclass(%s)', (queue.table,)).fetchone()[0] is None
    with registry.database.read() as conn:
        table = f'"{registry.database.schema}".registry_documents'
        assert conn.execute('SELECT to_regclass(%s)', (table,)).fetchone()[0] is None


def test_owner_partial_schema_and_pending_cleanup_do_not_imply_restorable(world):
    queue = world.queue
    with queue.database.transaction() as conn:
        queue.enqueue(world.project, owner_id=ARTICLE, operation_id='unfinished-delete', actor=ACTOR, connection=conn)
    observation = queue.inspect_restore(owner_id=ARTICLE, path=world.project / 'articles/new')
    assert not observation['cleanup_terminal'] and not observation['clear']
    assert observation['overlapping_pending'] == 1
    # Existing schema missing one required table stays untouched by observation.
    from management_common.portal_database import portal_database
    queue = DirectoryCleanupStore(create=False)
    queue.database = portal_database(schema='preview_partial_cleanup')
    queue.table = f'"{queue.database.schema}".cleanup'
    queue.reservations = f'"{queue.database.schema}".path_reservations'
    queue.database.write(queue._initialize)
    with queue.database.transaction() as conn:
        conn.execute(f'DROP TABLE {queue.reservations}')
    result = queue.inspect_restore(owner_id=ARTICLE, path=world.project / 'articles/new')
    assert result['missing'] == ['reservations'] and not result['clear']
    with queue.database.read() as conn:
        assert conn.execute('SELECT to_regclass(%s)', (queue.reservations,)).fetchone()[0] is None


def test_preview_output_budget_and_project_mismatch_are_not_approved(world, monkeypatch):
    result = run(world, project_id='Project')
    assert result['decision']['status'] == 'unknown'
    world.permissions.place(Fact('project', 'OtherProject'))
    world.permissions.grant(ACTOR, 'OtherProject', '@contributor')
    changed = run(world, project_id='OtherProject')
    assert 'target_project_differs_from_original' in changed['conflicts']
    assert changed['scope_digest'] != result['scope_digest']
    monkeypatch.setattr(preview, 'OUTPUT_BUDGET', 64)
    with pytest.raises(ValueError, match='response budget'): run(world)


def test_owner_snapshot_exact_ids_revision_and_competition(world):
    registry = world.registry
    before = registry.revision('article')
    with registry.writer('article') as writer:
        writer.put('article', 'article_555555555555', {'resource_id': 'article_555555555555'})
    revision = registry.revision('article')
    assert revision != before
    assert registry.inspect_article_identity('article', ARTICLE)['status'] == 'tombstoned'
    assert registry.inspect_article_identity('article', 'article_555555555555')['status'] == 'conflict'
    assert registry.inspect_article_identity('article', 'article_666666666666')['status'] == 'not_found'
    assert registry.revision('article') == revision
    with pytest.raises(ValueError): registry.inspect_article_identity('resident', ARTICLE)
    target = world.project / world.args['target_relative_path']
    assert world.queue.inspect_restore(owner_id=ARTICLE, path=target)['clear']
    # A committed competing reservation is visible to the next observation.
    with world.queue.database.transaction() as conn:
        world.queue.reserve(target.parent, operation_id='concurrent-move', purpose='move', connection=conn)
    observation = world.queue.inspect_restore(owner_id=ARTICLE, path=target)
    assert observation['overlapping_reservations'] == 1 and not observation['clear']
    # Truncating before that reservation must never report the path as safe.
    with world.queue.database.transaction() as conn:
        world.queue.reserve(world.project.parent / 'unrelated', operation_id='aaa', purpose='move', connection=conn)
    observation = world.queue.inspect_restore(owner_id=ARTICLE, path=target, limit=1)
    assert observation['status'] == 'indeterminate' and observation['truncated'] and not observation['clear']


def test_recovery_is_absent_from_isa_catalog_and_handlers():
    from management_portal.resources import isa
    from management_portal.hatg.catalog import instruction_catalog
    for name in ('CheckArticleRestore', 'RestoreArticle'):
        assert name not in instruction_catalog()
        assert name not in isa.HANDLERS
