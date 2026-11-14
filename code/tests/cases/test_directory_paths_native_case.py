"""Directory locations follow the filesystem, including absent reservations."""
import os
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from management_common import directory_paths as paths
from management_common.directory_cleanup_store import get_directory_cleanup_store
from management_portal.resources import directory_registrations as registrations
from management_portal.resources.registry_store import get_registry_store


@pytest.fixture
def sensitive_root(tmp_path):
    (tmp_path / 'Foo').mkdir()
    if (tmp_path / 'foo').exists():
        pytest.skip('Requires a case-sensitive filesystem')
    return tmp_path


@pytest.fixture
def empty_root(tmp_path):
    root = tmp_path / 'empty'
    root.mkdir()
    return root


def test_existing_case_distinct_directory_keys(sensitive_root):
    upper, lower = sensitive_root / 'Foo', sensitive_root / 'foo'
    lower.mkdir()
    assert paths.location_key(upper) != paths.location_key(lower)
    assert not paths.contains(upper, lower / 'child')
    assert not paths.overlaps(upper, lower)


def test_comparison_batch_reuses_parents_and_next_decision_rechecks(tmp_path, monkeypatch):
    calls = []
    sensitive = True
    def probe(parent):
        calls.append(parent)
        return sensitive
    monkeypatch.setattr(paths, '_directory_case_sensitive', probe)
    root = tmp_path / 'Owner'
    batch = paths.DirectoryComparison()
    for i in range(100):
        assert batch.contains(root, root / str(i))
    assert len(calls) == len(set(calls))
    # A new decision sees a changed native policy; no process/transaction cache.
    sensitive = False
    assert not batch.contains(root, tmp_path / 'owner' / 'child')
    before = len(calls)
    assert paths.DirectoryComparison().contains(root, tmp_path / 'owner' / 'child')
    assert len(calls) > before


def test_absent_case_distinct_directory_keys(sensitive_root):
    upper, lower = sensitive_root / 'Absent', sensitive_root / 'absent'
    assert paths.location_key(upper) != paths.location_key(lower)
    assert not paths.overlaps(upper, lower / 'child')


def test_disjoint_directory_closure_does_not_probe_unrelated_filesystems(tmp_path, monkeypatch):
    probes = []
    original = paths._directory_case_sensitive

    def probe(path):
        probes.append(path)
        return original(path)

    monkeypatch.setattr(paths, '_directory_case_sensitive', probe)
    root = tmp_path / 'owner-one'
    for index in range(100):
        assert not paths.contains(root, tmp_path / f'owner-two-{index}' / 'workspace')
    assert not paths.contains(root, tmp_path)
    assert probes == [], 'deletion closure must not probe every unrelated resource ancestor'
    assert paths.contains(root, root / 'child')
    assert probes, 'potential descendants must still check the native case policy'


def test_case_candidate_rechecks_policy_and_keeps_unknown_policy_errors(tmp_path, monkeypatch):
    root, child = tmp_path / 'Folder', tmp_path / 'folder' / 'child'
    monkeypatch.setattr(paths, '_directory_case_sensitive', lambda _: False)
    assert paths.contains(root, child)
    monkeypatch.setattr(paths, '_directory_case_sensitive', lambda _: True)
    assert not paths.contains(root, child)

    def unknown(_):
        raise OSError('case policy unavailable')

    monkeypatch.setattr(paths, '_directory_case_sensitive', unknown)
    with pytest.raises(OSError, match='case policy unavailable'):
        paths.contains(root, child)


@pytest.mark.parametrize('nested', [False, True])
def test_creation_accepts_case_distinct_location(sensitive_root, nested):
    target = sensitive_root / 'foo'
    if nested:
        target /= 'new'
    with get_registry_store().database.transaction() as connection:
        registrations.assert_empty_location(target, connection=connection)


def test_rename_accepts_case_distinct_destination(sensitive_root):
    source = sensitive_root / 'source'
    source.mkdir()
    (source / 'keep').write_text('moved')
    paths.rename_directory_no_replace(source, sensitive_root / 'foo')
    assert (sensitive_root / 'Foo').is_dir()
    assert (sensitive_root / 'foo' / 'keep').read_text() == 'moved'


def test_case_only_rename_on_sensitive_filesystem(sensitive_root):
    source, target = sensitive_root / 'Foo', sensitive_root / 'foo'
    (source / 'keep').write_text('moved')
    paths.rename_directory_no_replace(source, target)
    assert not source.exists()
    assert (target / 'keep').read_text() == 'moved'


def test_absent_cleanup_reservation_does_not_block_case_distinct_location(sensitive_root):
    queue = get_directory_cleanup_store()
    with queue.database.transaction() as connection:
        queue.enqueue(sensitive_root / 'Absent', owner_id='owner', operation_id='cleanup', actor='', connection=connection)
        queue.assert_available(sensitive_root / 'absent' / 'child', connection=connection)


def test_deletion_does_not_unregister_or_remove_case_distinct_neighbor(sensitive_root):
    upper, lower = sensitive_root / 'Foo', sensitive_root / 'foo'
    lower.mkdir()
    (lower / 'keep').write_text('neighbor')
    registrations.register_owned('knowledge', 'skill_111111111111', [upper])
    registrations.register_owned('knowledge', 'skill_222222222222', [lower])
    registrations.unregister_owned('knowledge', 'skill_111111111111', actor='owner')
    queue = get_directory_cleanup_store()
    queue.run_pending()
    assert get_registry_store().get('knowledge', 'skill_222222222222') is not None
    assert (lower / 'keep').read_text() == 'neighbor'
    assert not upper.exists()


def test_hardlinked_files_do_not_alias_directory_locations(sensitive_root):
    upper, lower = sensitive_root / 'File', sensitive_root / 'file'
    upper.write_text('shared inode')
    os.link(upper, lower)
    assert upper.samefile(lower)
    assert paths.location_key(upper) != paths.location_key(lower)
    assert not paths.contains(upper, lower / 'child')


def test_absent_operation_reservations_use_native_case(sensitive_root):
    queue = get_directory_cleanup_store()
    with queue.database.transaction() as connection:
        queue.reserve(sensitive_root / 'Absent', operation_id='one', purpose='create', connection=connection)
        queue.reserve(sensitive_root / 'absent', operation_id='two', purpose='create', connection=connection)
        with pytest.raises(FileExistsError, match='reserved'):
            queue.assert_available(sensitive_root / 'Absent' / 'child', connection=connection)


def test_workspace_locations_keep_case_distinct_identity(sensitive_root):
    from management_portal.resources.workspace_store import get_workspace_store
    store = get_workspace_store()
    store.ensure('workspace_111111111111', str(sensitive_root / 'Foo'))
    store.ensure('workspace_222222222222', str(sensitive_root / 'foo'))
    assert len(store.rows(['workspace_111111111111', 'workspace_222222222222'])) == 2


@pytest.mark.parametrize('occupied', ['directory', 'file', 'dangling_link'])
def test_occupied_target_is_not_replaced(tmp_path, occupied):
    source, target = tmp_path / 'source', tmp_path / 'target'
    source.mkdir()
    if occupied == 'directory':
        target.mkdir()
    elif occupied == 'file':
        target.write_text('keep')
    else:
        target.symlink_to(tmp_path / 'missing', target_is_directory=True)
    with pytest.raises(FileExistsError):
        paths.rename_directory_no_replace(source, target)
    assert source.is_dir()
    assert target.exists() or target.is_symlink()


def test_symlink_target_is_not_directory_ancestry(tmp_path):
    root, outside = tmp_path / 'root', tmp_path / 'outside'
    root.mkdir()
    outside.mkdir()
    (root / 'link').symlink_to(outside, target_is_directory=True)
    assert not paths.contains(root / 'link', outside)
    assert not paths.overlaps(root, outside)


def test_case_insensitive_missing_keys_and_reservations(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, '_directory_case_sensitive', lambda _: False)
    upper, lower = tmp_path / 'Absent', tmp_path / 'absent'
    assert paths.location_key(upper) == paths.location_key(lower)
    assert paths.contains(upper, lower / 'child')
    queue = get_directory_cleanup_store()
    with queue.database.transaction() as connection:
        queue.reserve(upper, operation_id='one', purpose='create', connection=connection)
        with pytest.raises(FileExistsError, match='reserved'):
            queue.assert_available(lower / 'child', connection=connection)


def test_each_existing_parent_supplies_its_own_case_rule(tmp_path, monkeypatch):
    sensitive, insensitive = tmp_path / 'sensitive', tmp_path / 'insensitive'
    sensitive.mkdir()
    insensitive.mkdir()
    monkeypatch.setattr(paths, '_existing_directory_case_sensitive', lambda parent: parent != insensitive)
    assert paths.location_key(sensitive / 'Foo') != paths.location_key(sensitive / 'foo')
    assert paths.location_key(insensitive / 'Foo') == paths.location_key(insensitive / 'foo')
    assert paths.location_key(insensitive / 'Absent' / 'Foo') == paths.location_key(insensitive / 'absent' / 'foo')


@pytest.mark.parametrize('sensitive', [False, True])
def test_macos_volume_capabilities_include_sensitive_volumes(empty_root, monkeypatch, sensitive):
    import ctypes
    import struct
    def getattrlist(path, attributes, result, size, options):
        assert path == os.fsencode(empty_root)
        assert struct.unpack('=HHIIIII', attributes) == (5, 0, 0, 0x80020000, 0, 0, 0)
        assert size == 36 and options == 0
        result[0], result[1], result[5] = size, 0x100 if sensitive else 0, 0x100
        return 0
    monkeypatch.setattr(paths, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(ctypes, 'CDLL', lambda *a, **kw: SimpleNamespace(getattrlist=getattrlist))
    assert paths._directory_case_sensitive(empty_root / 'not-yet-created') is sensitive
    assert list(empty_root.iterdir()) == []


@pytest.fixture
def windows_case_query(monkeypatch):
    import sys
    from management_common import secure_paths
    flags = {'sensitive': False}
    def query(handle, info_class, result, size):
        assert handle == 17 and info_class == 23
        result._obj.value = int(flags['sensitive'])
        return True
    native_os = SimpleNamespace(**vars(os))
    native_os.name = 'nt'
    monkeypatch.setattr(paths, 'os', native_os)
    monkeypatch.setattr(paths, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setitem(sys.modules, 'msvcrt', SimpleNamespace(get_osfhandle=lambda fd: fd))
    monkeypatch.setattr(secure_paths, 'open_directory', lambda _: nullcontext(17))
    monkeypatch.setattr(secure_paths, '_windows_api', lambda: SimpleNamespace(GetFileInformationByHandleEx=query))
    return flags


@pytest.mark.parametrize('sensitive', [False, True])
def test_windows_per_directory_case_flag(empty_root, windows_case_query, sensitive):
    windows_case_query['sensitive'] = sensitive
    assert paths._directory_case_sensitive(empty_root) is sensitive
    assert list(empty_root.iterdir()) == []


def test_windows_missing_sensitive_descendant_reports_inheritance_boundary(empty_root, windows_case_query):
    windows_case_query['sensitive'] = True
    with pytest.raises(OSError, match='missing Windows directory'):
        paths._directory_case_sensitive(empty_root / 'absent')
    windows_case_query['sensitive'] = False
    assert paths._directory_case_sensitive(empty_root / 'absent') is False


@pytest.mark.skipif(os.name == 'nt', reason='POSIX read-only lookup fallback')
def test_readonly_lookup_does_not_confuse_hardlinks_with_case_aliases(empty_root, monkeypatch):
    (empty_root / 'File').write_text('shared inode')
    if (empty_root / 'fILE').exists():
        pytest.skip('Requires case-sensitive storage')
    os.link(empty_root / 'File', empty_root / 'fILE')
    monkeypatch.setattr(paths, 'sys', SimpleNamespace(platform='unknown'))
    assert paths._directory_case_sensitive(empty_root)


def test_readonly_lookup_recognizes_one_case_aliased_entry(empty_root, monkeypatch):
    from pathlib import Path
    (empty_root / 'Foo').mkdir()
    original = Path.lstat
    def lookup(path, *args, **kwargs):
        if str(path) == str(empty_root / 'fOO'):
            path = empty_root / 'Foo'
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'lstat', lookup)
    assert paths._directory_case_sensitive(empty_root) is False


def test_case_alias_lookup_ignores_incomplete_windows_enumeration_identity(empty_root, monkeypatch):
    from pathlib import Path
    (empty_root / 'Foo').mkdir()
    original = Path.lstat

    def lookup(path, *args, **kwargs):
        if str(path) == str(empty_root / 'fOO'):
            path = empty_root / 'Foo'
        return original(path, *args, **kwargs)

    # FindFirstFile enumeration metadata has no device/inode identity on
    # Windows, even though a direct path query returns both fields.
    entry = SimpleNamespace(name='Foo', stat=lambda **_: SimpleNamespace(st_dev=0, st_ino=0))
    native_os = SimpleNamespace(**vars(os))
    native_os.scandir = lambda _: nullcontext([entry])
    monkeypatch.setattr(paths, 'os', native_os)
    monkeypatch.setattr(Path, 'lstat', lookup)
    assert paths._directory_case_sensitive(empty_root / 'not-created' / 'nested') is False


def test_empty_directory_policy_matches_native_lookup_without_leaving_files(empty_root):
    sensitive = paths._directory_case_sensitive(empty_root)
    assert list(empty_root.iterdir()) == []
    (empty_root / 'Foo').mkdir()
    assert sensitive == (not (empty_root / 'foo').exists())


@pytest.mark.skipif(paths.sys.platform != 'linux', reason='Linux directory casefold flags')
@pytest.mark.parametrize('sensitive', [False, True])
def test_linux_empty_directory_casefold_flag(empty_root, monkeypatch, sensitive):
    import ctypes
    import fcntl
    def fstatfs(descriptor, filesystem):
        filesystem[0] = 0xef53  # ext4, whose policy can differ per directory
        return 0
    def ioctl(descriptor, request, flags):
        flags[0] = 0 if sensitive else 0x40000000
    monkeypatch.setattr(ctypes, 'CDLL', lambda *a, **kw: SimpleNamespace(fstatfs=fstatfs))
    monkeypatch.setattr(fcntl, 'ioctl', ioctl)
    assert paths._directory_case_sensitive(empty_root / 'missing') is sensitive
    assert list(empty_root.iterdir()) == []


@pytest.mark.skipif(os.name == 'nt', reason='POSIX unsupported filesystem boundary')
def test_unknown_empty_filesystem_reports_policy_without_probe_files(empty_root, monkeypatch):
    monkeypatch.setattr(paths, 'sys', SimpleNamespace(platform='unknown'))
    with pytest.raises(OSError, match='Cannot determine filesystem case sensitivity'):
        paths._directory_case_sensitive(empty_root)
    assert list(empty_root.iterdir()) == []


@pytest.mark.parametrize('name', ['员工资料_123', 'ÉßİıΣσςKſ', '123_456', 'ＡｂＣ', '😀项目'])
def test_non_ascii_without_ascii_letters_supplies_no_case_evidence(empty_root, monkeypatch, name):
    from pathlib import Path
    (empty_root / name).write_text('unchanged')
    monkeypatch.setattr(paths, 'sys', SimpleNamespace(platform='unknown'))
    def unexpected_lookup(*args, **kwargs):
        pytest.fail('A name without ASCII letters cannot supply an alternate spelling')
    monkeypatch.setattr(Path, 'lstat', unexpected_lookup)
    with pytest.raises(OSError, match='Cannot determine filesystem case sensitivity'):
        paths._existing_directory_case_sensitive(empty_root)


@pytest.mark.parametrize('name,alternate', [
    ('Foo', 'fOO'), ('员工Foo_ßİK', '员工fOO_ßİK'),
    ('éAΣzı', 'éaΣZı'), ('项目_123a', '项目_123A'),
])
def test_case_lookup_swaps_only_ascii_letters(empty_root, monkeypatch, name, alternate):
    from pathlib import Path
    (empty_root / name).write_text('entry')
    original = Path.lstat
    lookups = []
    def lookup(path, *args, **kwargs):
        lookups.append(path.name)
        if path.name == alternate:
            raise FileNotFoundError(str(path))
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'lstat', lookup)
    assert paths._existing_directory_case_sensitive(empty_root)
    assert lookups == [name, alternate]


def test_large_non_ascii_directory_probes_only_useful_entry(empty_root, monkeypatch):
    from pathlib import Path
    for index in range(256):
        (empty_root / f'员工资料_{index}').touch()
    (empty_root / '员工Foo').touch()
    original = Path.lstat
    lookups = []
    def lookup(path, *args, **kwargs):
        lookups.append(path.name)
        if path.name == '员工fOO':
            raise FileNotFoundError(str(path))
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'lstat', lookup)
    assert paths._existing_directory_case_sensitive(empty_root)
    assert lookups == ['员工Foo', '员工fOO']
