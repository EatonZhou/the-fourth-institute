from unittest import mock

import pytest

from knowledge_board_lan import knowledge_backend
from skill_board_lan import skill_backend
from management_common.directory_cleanup_store import get_directory_cleanup_store
from management_portal.resources.directory_registrations import register_owned
from management_portal.resources.registry_store import get_registry_store

SKILL = 'skill_0123456789ab'


@pytest.mark.parametrize('origin', ['created', 'adopted'])
@pytest.mark.parametrize('single_file', [True, False])
def test_skill_unregister_by_origin(tmp_path, monkeypatch, origin, single_file):
    root = tmp_path / 'skill'
    root.mkdir()
    primary = root / 'SKILL.md'
    primary.write_text('# Example')
    companion = root / 'references' / 'guide.md'
    companion.parent.mkdir()
    companion.write_text('guide')
    target = primary if single_file else root
    register_owned('knowledge', SKILL, [target], origin=origin)
    record = {'resource_id': SKILL, 'resource_type': 'skill',
              'source': {'path': str(primary)},
              'attributes': {'content_root': str(root), 'single_file': single_file}}
    monkeypatch.setattr(skill_backend, '_resolve_skill_record', lambda *a, **kw: record)
    status, result = skill_backend.delete_skill(SKILL)
    assert status == 200
    assert get_registry_store().get('knowledge', SKILL) is None
    assert target.exists()
    assert bool(result['cleanup_ids']) == (origin == 'created')
    get_directory_cleanup_store().run_pending()
    assert target.exists() == (origin == 'adopted')
    assert companion.exists() == (single_file or origin == 'adopted')


def test_skill_delete_requires_root_address():
    status, _ = skill_backend.delete_skill(SKILL + '/file/SKILL.md')
    assert status == 400


def test_knowledge_delete_delegates_skill_packages_to_skill_backend():
    result = {'deleted': True, 'resource_type': 'skill', 'skill_id': SKILL,
              'kb_id': SKILL, 'cleanup_ids': ['cleanup_test']}
    with (
        mock.patch.object(knowledge_backend, 'resolve_item_record',
                          return_value={'kb_id': SKILL, 'type': 'skill'}),
        mock.patch.object(knowledge_backend.skill_backend, 'delete_skill',
                          return_value=(200, result)) as delete,
    ):
        deleted = knowledge_backend.delete_item(SKILL)
    assert deleted == {**result, 'project_ids': []}
    delete.assert_called_once_with(SKILL, actor='')
