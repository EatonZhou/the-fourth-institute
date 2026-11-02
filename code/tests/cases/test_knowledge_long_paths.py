"""Knowledge I/O keeps logical identities across native long file paths.

:module: resource.service
:design: decision_3ece26686464, decision_4aa13bc21e8e
"""
import shutil
from pathlib import Path

from management_common.directory_paths import rename_directory_no_replace, remove_path
from management_common.filesystem_paths import filesystem_path
from management_portal.resources.creation import _identity, _assert_identity, _sync
from management_portal.resources.indexed_content import read_resolved_resource_content
from knowledge_board_lan import knowledge_backend as backend, knowledge_source as source
from knowledge_board_lan.knowledge_details import _indexed_item_files


def exercise_long_knowledge_path(root: Path):
    """Also runnable in the native installed runtime without a model turn."""
    directory = root / ('project-' + 'p' * 72) / ('nested-' + 'n' * 72) / 'information'
    filesystem_path(directory).mkdir(parents=True)
    title = 'Knowledge-' + 't' * 95
    resource_id = 'information_123456789abc'
    target = directory / ('20261103__information__' + title + '__' + resource_id + '.md')
    stage = directory / '.stage'
    original = '---\nkb_id: ' + resource_id + '\ntype: information\nproject: LongPath\n---\n# ' + title + '\n\nOriginal\n'
    try:
        assert len(str(target)) > 260
        filesystem_path(stage).write_text(original, encoding='utf-8', newline='\n')
        identity = _identity(stage)
        _sync(stage)
        rename_directory_no_replace(stage, target)
        _assert_identity(target, identity)
        assert backend._valid_item_path(str(target)) == target
        assert source._read_file_text(str(target)) == original
        resolved = {'target_kind': 'information', 'resource_type': 'information', 'resolved_path': str(target)}
        data, _media, returned = read_resolved_resource_content(resolved)
        assert data.decode() == original and returned['resolved_path'] == str(target)
        assert source._top_level_markdown_files(directory) == [target]
        assert source._candidate_entry_files(directory) == [target]
        assert source._bounded_index_files(directory) == ([target], False)
        item = source._get_md_item_cached(target, directory, [target], 'LongPath', 'test', {'information'}, {})
        assert item['kb_id'] == resource_id and item['path'] == str(target) and item['mtime'] > 0
        assert all(not str(path).startswith('\\\\?\\') for path in source._candidate_entry_files(directory))
        records = _indexed_item_files({'resource_id': resource_id, 'resource_type': 'information'}, target, directory)
        assert records[0]['address'] == resource_id and records[0]['name'] == target.name
        backend._write_text_atomic(target, original.replace('Original', 'Updated'))
        assert source._read_file_text(str(target)).endswith('Updated\n')
        filesystem_path(stage).write_text('must survive collision')
        try:
            rename_directory_no_replace(stage, target)
        except FileExistsError:
            pass
        else:
            raise AssertionError('Publication replaced an existing file')
        assert filesystem_path(stage).read_text() == 'must survive collision'
        assert source._read_file_text(str(target)).endswith('Updated\n')
        remove_path(target)
        assert not filesystem_path(target).exists() and filesystem_path(stage).is_file()
        return {'path_length': len(str(target)), 'publish': True, 'read': True,
                'discovery': True, 'detail': True, 'update': True, 'collision_preserved': True, 'cleanup': True}
    finally:
        shutil.rmtree(filesystem_path(root))


def test_long_knowledge_file_publish_discover_detail_and_update(tmp_path):
    exercise_long_knowledge_path(tmp_path / 'long-path-fixture')
