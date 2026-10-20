from pathlib import Path

import pytest

from management_portal.distribution.file_scope import FileScope, preflight


def scope(root, policy=None):
    return FileScope([{'source': str(root)}], policy)


def test_new_runtime_profiles_are_excluded_without_a_numbered_allowlist(tmp_path):
    root = tmp_path / 'workspace'
    for relative in ('impress-runtime/display-100/lo-profile/.lock',
                     'impress-runtime/display-100/lo-profile/user/registrymodifications.xcu',
                     'browser-runtime/profile/Default/Cookies',
                     'browser-runtime/export.pdf', 'impress-runtime/display-100/slides.pdf',
                     'research/profile/result.json'):
        file = root / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text('fixture')
    files = {str(rel) for rel, _, directory in scope(root).walk(root) if not directory}
    assert files == {'browser-runtime/export.pdf', 'impress-runtime/display-100/slides.pdf',
                     'research/profile/result.json'}


def test_checksum_path_keys_relocate_without_changing_schema_keys_or_colliding():
    from management_portal.distribution.sanitize import scrub
    mappings = [('/data/research/run', '${share_root}/outputs/run')]
    value = {'sha256': {'/data/research/run/a.npz': 'digest'},
             'description /data/research/run': 'schema label',
             'path': '/data/research/run/a.npz'}
    assert scrub(value, mappings=mappings) == {
        'sha256': {'${share_root}/outputs/run/a.npz': 'digest'},
        'description /data/research/run': 'schema label',
        'path': '${share_root}/outputs/run/a.npz'}
    with pytest.raises(ValueError, match='collide'):
        scrub({'/data/research/run/a.npz': 'one', '${share_root}/outputs/run/a.npz': 'two'}, mappings=mappings)


def test_internal_artifacts_materialize_with_same_copy_sanitization(tmp_path):
    from management_portal.distribution.engineering import copy_content
    root = tmp_path / 'selected'
    root.mkdir()
    (root / 'history.json').write_text('{"sender":{"kind":"user","id":"original"}}')
    (root / 'alias.json').symlink_to('history.json')
    (root / '.nodeenv').mkdir()
    (root / '.nodeenv/node').symlink_to('/missing/local/runtime')
    policy = scope(root)
    output = tmp_path / 'copy'
    for relative, actual, directory in policy.walk(root):
        target = output / relative
        if directory:
            target.mkdir(parents=True, exist_ok=True)
        else:
            copy_content(actual, target, selected=set())
    assert (output / 'alias.json').is_file() and not (output / 'alias.json').is_symlink()
    assert 'tfi-exported-user' in (output / 'alias.json').read_text()
    assert not (output / '.nodeenv').exists()
    assert len(policy.report()['decisions']) == 1


def test_external_link_requires_exact_resolution_and_rechecks_changes(tmp_path):
    root = tmp_path / 'selected'
    root.mkdir()
    outside = tmp_path / 'outside'
    outside.write_bytes(b'data')
    link = root / 'result'
    link.symlink_to(outside)
    with pytest.raises(ValueError, match='leaves'):
        list(scope(root).walk(root))
    policy = {'external_links': [{'path': str(link), 'target': str(outside), 'reason': 'explicit selected research result'}]}
    assert next(actual for rel, actual, directory in scope(root, policy).walk(root) if not directory) == outside
    replacement = tmp_path / 'replacement'
    replacement.write_text('not approved')
    link.unlink()
    link.symlink_to(replacement)
    with pytest.raises(ValueError, match='leaves'):
        list(scope(root, policy).walk(root))


def test_omission_is_explicit_and_does_not_follow_target(tmp_path):
    root = tmp_path / 'selected'
    root.mkdir()
    link = root / 'external'
    link.symlink_to('/not/available')
    policy = {'omitted_links': [{'path': str(link), 'reason': 'external content not selected'}]}
    checked = preflight([{'source': str(root)}], policy)
    assert checked['regular_files'] == 0
    assert checked['decisions'][0]['action'] == 'omitted_link'
    with pytest.raises(ValueError, match='explicit reason'):
        scope(root, {'omitted_links': [{'path': str(link)}]})


def test_cycles_and_secret_aliases_fail_before_export(tmp_path):
    root = tmp_path / 'selected'
    root.mkdir()
    (root / 'loop').symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match='cycle'):
        list(scope(root).walk(root))
    (root / 'loop').unlink()
    (root / 'auth.json').write_text('secret')
    (root / 'research.json').symlink_to('auth.json')
    with pytest.raises(ValueError, match='authentication/runtime'):
        list(scope(root).walk(root))


def test_selected_shared_target_and_explicit_runtime_exclusion(tmp_path):
    root = tmp_path / 'selected'
    other = tmp_path / 'also_selected'
    root.mkdir()
    other.mkdir()
    (other / 'result.pdf').write_bytes(b'paper')
    (root / 'paper').symlink_to(other, target_is_directory=True)
    (root / 'browser').mkdir()
    (root / 'browser/SingletonSocket').symlink_to('/tmp/nonexistent')
    policy = {'excluded_paths': [{'path': str(root / 'browser'), 'reason': 'local authenticated browser profile'}]}
    selected = FileScope([{'source': str(root)}, {'source': str(other)}], policy)
    files = [str(rel) for rel, actual, directory in selected.walk(root) if not directory]
    assert files == ['paper/result.pdf']
    assert {row['action'] for row in selected.report()['decisions']} == {'materialized', 'excluded'}


def test_exclusions_cannot_hide_unrelated_trees(tmp_path):
    root = tmp_path / 'selected'
    root.mkdir()
    with pytest.raises(ValueError, match='selected trees'):
        scope(root, {'excluded_paths': [{'path': str(tmp_path / 'other'), 'reason': 'not selected'}]})


def test_local_process_socket_is_evidenced_not_copied(tmp_path):
    import socket
    root = tmp_path / 'selected'
    root.mkdir()
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(str(root / 'runtime'))
        selected = scope(root)
        assert all(directory for _, _, directory in selected.walk(root))
        assert selected.report()['decisions'][0]['action'] == 'excluded'


def test_external_metadata_references_use_most_specific_included_copy(tmp_path):
    from management_portal.distribution.file_scope import external_path_mappings
    from management_portal.distribution.sanitize import scrub
    root = tmp_path / 'selected'
    nested = root / 'src'
    nested.mkdir(parents=True)
    outside = tmp_path / 'external'
    outside.mkdir()
    link = nested / 'result'
    link.symlink_to(outside, target_is_directory=True)
    files = [{'source': str(root), 'target': 'data/share_root/Workspaces/root'},
             {'source': str(nested), 'target': 'data/share_root/Workspaces/src'}]
    policy = {'external_links': [{'path': str(link), 'target': str(outside), 'reason': 'selected research'}]}
    mappings = external_path_mappings(files, policy)
    assert mappings == [(str(outside), '${share_root}/Workspaces/src/result')]
    assert external_path_mappings(list(reversed(files)), policy) == mappings
    assert scrub({'result': str(outside / 'data.npz')}, mappings=mappings) == {
        'result': '${share_root}/Workspaces/src/result/data.npz'}


def test_mapping_order_and_recursive_semantics_are_preserved():
    from management_portal.distribution.sanitize import scrub
    mappings = [('/data', '${share_root}/base'), ('/data/results', '${share_root}/specific')]
    assert scrub({'paths': ['/data/results/x', '/data/y']}, mappings=mappings) == {
        'paths': ['${share_root}/specific/x', '${share_root}/base/y']}
