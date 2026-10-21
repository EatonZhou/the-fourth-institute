"""Site-specific device instructions cannot re-enter through broad selections."""
from pathlib import Path

import pytest

from management_portal.distribution.engineering import copy_content
from management_portal.distribution.file_scope import FileScope
from management_portal.distribution.skill_scope import SkillScope, excluded_skill_ids


def skill(root, site_only=False):
    root.mkdir(parents=True)
    marker = '  distribution: site_only\n' if site_only else ''
    (root / 'SKILL.md').write_text('---\nname: example\nmetadata:\n  id: skill_aaaaaaaaaaaa\n' + marker + '---\n# Example\n')
    (root / 'scripts').mkdir()
    (root / 'scripts/helper.py').write_text('print("example")\n')
    return root


def test_parent_selection_excludes_entire_skill_but_keeps_general_skill(tmp_path):
    root = tmp_path / 'workspace'
    local = skill(root / 'local', True)
    public = skill(root / 'general')
    scope = FileScope([{'source': str(root)}])
    files = {str(relative) for relative, _, directory in scope.walk(root) if not directory}
    assert files == {'general/SKILL.md', 'general/scripts/helper.py'}
    assert scope.report()['decisions'][0]['reason'] == 'Skill metadata.distribution is site_only'
    # The direct copy path used by code extras must obey the same rule.
    copy_content(root, tmp_path / 'copy', selected=set())
    assert not (tmp_path / 'copy/local').exists()
    assert (tmp_path / 'copy/general/scripts/helper.py').is_file()
    assert (local / 'SKILL.md').is_file() and (public / 'SKILL.md').is_file()


@pytest.mark.parametrize('relative', ['.', 'SKILL.md', 'scripts/helper.py'])
def test_explicit_selection_and_external_alias_cannot_override_exclusion(tmp_path, relative):
    local = skill(tmp_path / 'device', True)
    selected = local / relative
    assert list(FileScope([{'source': str(selected)}]).walk(selected)) == []
    alias = tmp_path / 'alias'
    alias.symlink_to(selected)
    assert list(FileScope([{'source': str(alias)}]).walk(alias)) == []
    copy_content(selected, tmp_path / 'output', selected=set())
    assert not (tmp_path / 'output').exists()


def test_nested_public_skill_cannot_override_site_only_parent_and_cache_is_per_export(tmp_path):
    local = skill(tmp_path / 'local', True)
    nested = skill(local / 'general')
    assert SkillScope().excluded_root(nested) == local
    marker = local / 'SKILL.md'
    marker.write_text(marker.read_text().replace('  distribution: site_only\n', ''))
    assert SkillScope().excluded_root(nested) is None


@pytest.mark.parametrize('via_dependency', [False, True])
def test_selected_id_is_removed_from_registry_and_files(tmp_path, via_dependency):
    from tests.cases.test_distribution_lineage_options import ExportSource
    from tests.cases.test_distribution_resource_dependencies import selected
    from management_portal.distribution.selection import resolve
    from management_portal.distribution.store import digest
    from types import SimpleNamespace
    local = skill(tmp_path / 'local', True)
    public = skill(tmp_path / 'public')
    source = ExportSource()
    original = source.read_index()
    records = {identity: {'resource_type': 'skill', 'source': {'path': str(root / 'SKILL.md')},
                         'attributes': {'content_root': str(root)}}
               for identity, root in [('skill_aaaaaaaaaaaa', local), ('skill_bbbbbbbbbbbb', public)]}
    source.read_index = lambda: {**original, **records}
    source.data['resource_registry', 'registry_documents'] = [
        {'collection': 'skills', 'document_id': identity, 'document_json': record}
        for identity, record in records.items()]
    source.tables['resource_registry', 'registry_documents'] = SimpleNamespace(columns={})
    snapshot = selected('skill')
    snapshot['resource_types'][0]['instances'] = [] if via_dependency else list(records)
    resolution = {'snapshot_sha256': digest(snapshot), 'additional_ids': list(records)} if via_dependency else None
    result = resolve(snapshot, source, resolution=resolution)
    assert 'skill_aaaaaaaaaaaa' not in result['selected_ids']
    assert 'skill_bbbbbbbbbbbb' in result['selected_ids']
    assert result['excluded_resources'] == [{'id': 'skill_aaaaaaaaaaaa', 'reason': 'site_only_skill'}]
    assert all(row['document_id'] != 'skill_aaaaaaaaaaaa'
               for row in result['tables']['resource_registry', 'registry_documents'])
    assert not any(Path(item['source']).is_relative_to(local) for item in result['files'])
    assert excluded_skill_ids(records, set(records)) == {'skill_aaaaaaaaaaaa'}


@pytest.mark.parametrize('mode', ['tree', 'immutable_file', 'immutable_artifact'])
def test_production_generate_obeys_skill_scope_and_rejects_immutable_bypass(tmp_path, monkeypatch, mode):
    import shutil
    from management_portal.distribution import engineering
    source = tmp_path / 'source'
    for name in ['LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md', 'scripts/install_tfi_release', 'scripts/tfi_release_install.py']:
        file = source / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text('test content\n')
    shutil.copytree(Path(__file__).resolve().parents[2] / 'config/resource-types', source / 'config/resource-types')
    monkeypatch.setattr(engineering, 'source_files', lambda root: [p.relative_to(root) for p in root.rglob('*') if p.is_file()])
    local = skill(tmp_path / 'skills/local', True)
    skill(tmp_path / 'skills/general')
    item = {'source': str(local.parent), 'target': 'data/share_root/Skills'}
    if mode == 'immutable_file':
        file = local / 'scripts/helper.py'
        item.update(source=str(file), immutable_file={'sha256': engineering.checksum(file), 'size': file.stat().st_size})
    elif mode == 'immutable_artifact':
        item['immutable_artifact'] = True
    destination = tmp_path / 'package'
    kwargs = dict(source_root=source, destination=destination, snapshot={},
                  resolved={'files': [item]}, database_sql='-- fixture\n')
    if mode == 'tree':
        result = engineering.generate(**kwargs)
        assert (destination / 'data/share_root/Skills/general/SKILL.md').is_file()
        assert not (destination / 'data/share_root/Skills/local').exists()
        assert any(row['reason'] == 'Skill metadata.distribution is site_only' for row in result['file_scope']['decisions'])
    else:
        with pytest.raises(ValueError, match='(site-only Skill|private paths)'):
            engineering.generate(**kwargs)


@pytest.mark.parametrize('marker', ['.tfi-project', '.env', '.ssh'])
def test_fixed_project_artifact_can_be_reused_without_allowing_private_files(tmp_path, monkeypatch, marker):
    import shutil
    from management_portal.distribution import engineering
    source = tmp_path / 'source'
    for name in ['LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md', 'scripts/install_tfi_release', 'scripts/tfi_release_install.py']:
        file = source / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text('test content\n')
    shutil.copytree(Path(__file__).resolve().parents[2] / 'config/resource-types', source / 'config/resource-types')
    monkeypatch.setattr(engineering, 'source_files', lambda root: [p.relative_to(root) for p in root.rglob('*') if p.is_file()])
    project = tmp_path / 'fixed/data/share_root/Projects/example'
    project.mkdir(parents=True)
    (project / marker).write_text('example\n')
    item = {'source': str(project.parent), 'target': 'data/share_root/Projects', 'immutable_artifact': True}
    destination = tmp_path / 'rebuild'
    kwargs = dict(source_root=source, destination=destination, snapshot={},
                  resolved={'files': [item]}, database_sql='-- unchanged\n')
    if marker == '.tfi-project':
        result = engineering.generate(**kwargs)
        output = destination / 'data/share_root/Projects/example/.tfi-project'
        assert output.read_bytes() == (project / marker).read_bytes()
        assert any(row['path'] == output.relative_to(destination).as_posix() for row in result['inventory'])
    else:
        with pytest.raises(ValueError, match='private paths'):
            engineering.generate(**kwargs)
