"""Real resource folder resolution and ISA directory continuation."""
from types import SimpleNamespace
import pytest

from tests.cases.test_folder_file_addresses import folders, read, RESIDENT
from management_portal.resources import isa
from management_portal.hatg.contracts import InstructionError
from tests.cases.test_workspace_resource_content import workspace, WORKSPACE


def listing(address, **args):
    return isa.list_resource_files(SimpleNamespace(argument={'address': address, **args}.get, caller='alice'))


def test_read_resource_continues_past_500(folders):
    for n in range(601):
        (folders.resident / f'file{n:04}.txt').write_text('x')
    (folders.resident / 'Aa').write_text('x')
    (folders.resident / 'aa').write_text('x')
    address = RESIDENT + '/file'
    first = read(address)
    assert len(first['entries']) == 200
    cursor = first['next_cursor']
    seen = [x['name'] for x in first['entries']]
    while cursor:
        page = listing(address, cursor=cursor, limit=79)
        seen.extend(x['name'] for x in page['entries'])
        cursor = page['next_cursor']
    expected = ['notes'] + sorted(['escape.md', 'resident.md', 'Aa', 'aa'] +
                                [f'file{n:04}.txt' for n in range(601)], key=lambda x: (x.casefold(), x))
    assert seen == expected
    assert 'secrets' not in str(first) and '.claude' not in str(first)


def test_wrong_directory_and_replaced_directory_cursor(folders):
    address = RESIDENT + '/file'
    cursor = listing(address, limit=1)['next_cursor']
    with pytest.raises(ValueError, match='cursor'):
        listing(address + '/notes', cursor=cursor)
    old = folders.resident.with_name('old')
    folders.resident.rename(old)
    folders.resident.mkdir()
    with pytest.raises(ValueError, match='cursor'):
        listing(address, cursor=cursor)


def test_every_page_rechecks_current_reader(folders, monkeypatch):
    address = RESIDENT + '/file'
    cursor = listing(address, limit=1)['next_cursor']
    from management_portal import permission_system
    original = permission_system.allows
    monkeypatch.setattr(permission_system, 'allows', lambda actor, instruction, args, **kw:
                        False if instruction == 'ReadResource' else original(actor, instruction, args, **kw))
    with pytest.raises(PermissionError):
        listing(address, cursor=cursor)


@pytest.mark.parametrize('suffix', ['/resident.md', '/escape.md', '/notes/../resident.md', '/secrets'])
def test_list_refuses_file_symlink_and_hidden_paths(folders, suffix):
    with pytest.raises((ValueError, PermissionError, InstructionError)):
        listing(RESIDENT + '/file' + suffix)


def test_mutations_and_hidden_entries_do_not_shift_page(folders):
    directory = folders.resident / 'notes'
    for name in ['b', 'd', 'f', 'h', '.hidden', 'secrets']:
        (directory / name).write_text('x')
    address = RESIDENT + '/file/notes'
    first = listing(address, limit=2)
    assert [e['name'] for e in first['entries']] == ['b', 'd']
    (directory / 'b').unlink()
    for name in ['a', 'e']:
        (directory / name).write_text('x')
    second = listing(address, cursor=first['next_cursor'], limit=100)
    assert [e['name'] for e in second['entries']] == ['e', 'f', 'h', 'plan.md']
    assert second['next_cursor'] == ''


@pytest.fixture
def directory_workspace(workspace, monkeypatch):
    from management_portal import permission_system
    # Existing fixture's permission provider models viewer on ReadResource;
    # the new action has the same viewer classification, with its own call.
    impl = permission_system._IMPLEMENTATION
    original = impl.permitted
    monkeypatch.setattr(impl, 'permitted', lambda actor, instruction, parameter, ids, **kw:
                        original(actor, 'ReadResource' if instruction == 'ListResourceFiles' else instruction,
                                 parameter, ids, **kw))
    monkeypatch.setattr(impl, 'allows', lambda actor, instruction, args:
                        next(impl.permitted(actor, instruction, 'address', [args['address']], batch=0))[1],
                        raising=False)
    for name in ['a', 'b', 'c']:
        (workspace.root / name).write_text(name)
    return workspace


@pytest.mark.parametrize('change', ['deleted', 'rebound', 'revoked', 'private'])
def test_workspace_current_state_on_every_page(directory_workspace, monkeypatch, change):
    w = directory_workspace
    address = {'workspace_id': WORKSPACE, 'relative_path': ''}
    first = w.service.list_files(address, actor='alice', limit=1)
    from management_portal.resources.workspace_store import get_workspace_store
    store = get_workspace_store()
    if change == 'deleted':
        store.database.write(lambda c: store.mark_deleted(WORKSPACE, deleted_at='2026-11-27T00:00:00Z',
                                                          deleted_by='tester', connection=c))
    elif change == 'rebound':
        root = w.root.with_name('new-workspace')
        root.mkdir()
        store.ensure(WORKSPACE, str(root), move=True)
    elif change == 'revoked':
        w.grants.clear()
    else:
        from management_portal import permission_system
        from management_portal.society_service import lineage_access_policy
        from management_portal.permission_system import temporary
        monkeypatch.setattr(permission_system, 'configured_mode', lambda: permission_system.OPEN)
        monkeypatch.setattr(permission_system.active(), 'current', lambda: (None, None, None), raising=False)
        monkeypatch.setattr(temporary, 'delegated_ids', lambda *a, **k: set())
        monkeypatch.setattr(lineage_access_policy, 'personal_folders',
                            lambda: [(str(w.root), 'lineage_aaaaaaaaaaaa', 'other-owner')])
    with pytest.raises((KeyError, FileNotFoundError, PermissionError, ValueError)):
        w.service.list_files(address, actor='alice', cursor=first['next_cursor'])


def test_signed_rpc_forwards_actual_actor_and_directory_arguments(folders, tmp_path, monkeypatch):
    import threading
    from management_portal.resources.server import ResourceServiceServer
    from management_portal.resources.transport import ResourceServiceClient
    from management_portal.resources.config import resource_rpc_signing_key_file
    from management_portal.resources.rpc_auth import load_rpc_signing_key, sign_rpc_auth
    from tests.cases.test_resource_service_process import FakePublisher

    socket_path = tmp_path / 'pages.sock'
    key = load_rpc_signing_key(required=True, create=True,
                              path=resource_rpc_signing_key_file(socket_path=socket_path))
    # Keep unrelated index/auto-registration workers out of this socket test.
    monkeypatch.setattr(ResourceServiceServer, '_start_warmup', lambda self: None)
    monkeypatch.setattr(ResourceServiceServer, '_start_background_maintenance', lambda self: None)
    monkeypatch.setenv('PORTAL_RESOURCE_SERVICE_PROCESS', '1')
    server = ResourceServiceServer(socket_path, service=folders.service, publisher=FakePublisher())
    seen = []
    from management_portal import permission_system
    original = permission_system.allows
    def allows(actor, instruction, args, **kw):
        if instruction == 'ListResourceFiles':
            seen.append(actor)
            return actor == 'alice'
        return original(actor, instruction, args, **kw)
    monkeypatch.setattr(permission_system, 'allows', allows)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    assert server.started.wait(2)
    try:
        client = ResourceServiceClient(socket_path, timeout=3)
        address = RESIDENT + '/file'
        page = client.list_files(address, actor='alice', limit=1)
        assert [x['name'] for x in page['entries']] == ['notes']
        next_page = client.list_files(address, actor='alice', cursor=page['next_cursor'], limit=1)
        assert [x['name'] for x in next_page['entries']] == ['escape.md']
        with pytest.raises(PermissionError):
            client.list_files(address, actor='bob', cursor=page['next_cursor'])
        with pytest.raises(ValueError, match='cursor'):
            client.list_files(address, actor='alice', cursor='bad')
        params = {'address': address, 'actor': 'alice'}
        with pytest.raises(PermissionError, match='assertion'):
            server.authenticate('resource.list_files', params,
                                sign_rpc_auth(key, 'resource.list_files', params, actor='bob'))
        assert seen[:3] == ['alice', 'alice', 'bob']
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


@pytest.mark.parametrize('change', ['deleted', 'private'])
def test_resident_current_lifecycle_and_privacy(folders, monkeypatch, change):
    service = folders.service
    first = service.list_files(RESIDENT + '/file', actor='alice', limit=1)
    index = service._index()
    original = index.get_resource.side_effect
    def record(resource_id, *args, **kwargs):
        value = original(resource_id, *args, **kwargs)
        if resource_id == RESIDENT:
            value['attributes'].update({'status': 'deleted'} if change == 'deleted'
                                       else {'personal_owner_id': 'someone-else'})
        return value
    index.get_resource.side_effect = record
    from management_portal.permission_system import temporary
    monkeypatch.setattr(temporary, 'delegated_ids', lambda *a, **k: set())
    with pytest.raises((KeyError, FileNotFoundError, PermissionError, ValueError)):
        service.list_files(RESIDENT + '/file', actor='alice', cursor=first['next_cursor'])


def test_real_par_viewer_grant_explicit_read_deny_and_revoke(folders, monkeypatch):
    from tests.cases.permission_test_support import enable
    from management_portal.permission_system.facts import Fact
    from tests.cases.test_folder_file_addresses import LINEAGE

    p = enable(monkeypatch)
    actor = 'resident_bbbbbbbbbbbb'
    p.place(Fact('resident', actor), Fact('lineage', LINEAGE), Fact('resident', RESIDENT))
    # The default shares reads with everyone. This isolated instance removes
    # that baseline permit to exercise a caller's explicit grant and revocation.
    with p.store.transaction() as connection:
        p.store.delete(connection,
                       where="p_node=%s AND a_node=%s AND r_node=%s AND effect=%s",
                       parameters=('@principals', '@viewer', '@shared', 'permit'))
    address = RESIDENT + '/file'
    with pytest.raises(PermissionError):
        folders.service.list_files(address, actor=actor, limit=1)
    grant = p.grant(actor, RESIDENT, '@viewer')
    first = folders.service.list_files(address, actor=actor, limit=1)
    assert first['next_cursor'] and first['entries'][0]['name'] == 'notes'
    deny = p.deny(actor, RESIDENT, a='ReadResource')
    # An existing custom ReadResource deny cannot be bypassed by the new ISA.
    with pytest.raises(PermissionError):
        folders.service.list_files(address, actor=actor, cursor=first['next_cursor'])
    with p.store.transaction() as connection:
        p.store.delete(connection, where='entry_id=%s', parameters=(deny.entry_id,))
    assert folders.service.list_files(address, actor=actor, cursor=first['next_cursor'])['entries']
    with p.store.transaction() as connection:
        p.store.delete(connection, where='entry_id=%s', parameters=(grant.entry_id,))
    with pytest.raises(PermissionError):
        folders.service.list_files(address, actor=actor, cursor=first['next_cursor'])
