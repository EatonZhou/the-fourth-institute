from pathlib import Path

import pytest

from management_portal.agent_skill_context import materialize_portal_skills
from management_portal.distribution.core_skills import export_core_skills
from management_portal.distribution import core_skills


def source_rules(tmp_path):
    source = tmp_path/'share/Skills'
    rules = source/'tfi-agent-rule'
    rules.mkdir(parents=True)
    code_root = Path(__file__).resolve().parents[2]
    (rules/'SKILL.md').write_text(f'Core ./common.md source-person {code_root}\n')
    (rules/'common.md').write_text('Common ./SKILL.md\n')
    (rules/'portal-resident.md').write_text('Resident\n')
    (rules/'portal-development.md').write_text('Development\n')
    (rules/'unselected.md').write_text('Unselected\n')
    support = source/'tfi-unified-id'
    (support/'scripts').mkdir(parents=True)
    (support/'SKILL.md').write_text('Resource identity rules\n')
    (support/'scripts/resource_id.py').write_text('print("resource identity CLI")\n')
    for name in core_skills.REQUIRED_SKILLS:
        (source/name).mkdir()
        (source/name/'SKILL.md').write_text(f'{name} rules ./guide.md source-person\n')
        (source/name/'guide.md').write_text('Guide\n')
    (source/'create-skill/scripts/__pycache__').mkdir(parents=True)
    (source/'create-skill/scripts/quick_validate.py').write_text('print("validate")\n')
    (source/'create-skill/scripts/__pycache__/quick_validate.pyc').write_bytes(b'cache')
    return source


def test_core_rules_transitive_dependency_and_native_materialization(tmp_path):
    source = source_rules(tmp_path)
    destination = tmp_path/'package'
    report = export_core_skills(destination, source_root=source, source_usernames=('source-person',))
    assert len(report['files']) == 11
    assert (destination/'data/share_root/Skills/tfi-unified-id/scripts/resource_id.py').is_file()
    output = destination/'data/share_root/Skills'
    assert not (output/'tfi-agent-rule/unselected.md').exists()
    assert (output/'create-skill/scripts/quick_validate.py').is_file()
    assert not (output/'create-skill/scripts/__pycache__').exists()
    assert (output/'manage-knowledge-base/guide.md').is_file()
    for role in report['roles']:
        result = materialize_portal_skills(role, skill_root=str(output))
        assert result.status == 'fresh'
        assert 'TFI portal' in result.content
        assert 'source-person' not in result.content
        assert str(Path(__file__).resolve().parents[2]) not in result.content
        assert '${code_root}' in result.content
    assert 'source-person' in (source/'tfi-agent-rule/SKILL.md').read_text()


def test_core_rules_missing_dependency_is_not_silently_skipped(tmp_path):
    source = source_rules(tmp_path)
    (source/'tfi-agent-rule/common.md').unlink()
    with pytest.raises(ValueError, match='common.md'):
        export_core_skills(tmp_path/'package', source_root=source)


def test_core_rules_reject_symlink(tmp_path):
    source = source_rules(tmp_path)
    common = source/'tfi-agent-rule/common.md'
    common.unlink()
    common.symlink_to(source/'tfi-agent-rule/portal-resident.md')
    with pytest.raises(ValueError, match='common.md'):
        export_core_skills(tmp_path/'package', source_root=source)


def test_required_skill_rejects_symlink(tmp_path):
    source = source_rules(tmp_path)
    guide = source/'manage-knowledge-base/guide.md'
    guide.unlink()
    guide.symlink_to(source/'tfi-agent-rule/portal-resident.md')
    with pytest.raises(ValueError, match='manage-knowledge-base/guide.md'):
        export_core_skills(tmp_path/'package', source_root=source)


def test_default_rules_use_repository_canonical_source(tmp_path, monkeypatch):
    source = source_rules(tmp_path)
    repository = tmp_path/'repository'
    repository.mkdir()
    source.rename(repository/'core_skills')
    monkeypatch.setattr(core_skills, '__file__', str(repository/'management_portal/distribution/core_skills.py'))
    destination = tmp_path/'package'
    report = export_core_skills(destination)
    for role in report['roles']:
        assert materialize_portal_skills(role, skill_root=str(destination/'data/share_root/Skills'),
                                        system_skill_name='tfi-agent-rule').status == 'fresh'
    assert (destination/'data/share_root/Skills/tfi-unified-id/scripts/resource_id.py').is_file()


def test_explicit_source_maps_canonical_rule_paths(tmp_path):
    source = source_rules(tmp_path)
    (source/'tfi-agent-rule/SKILL.md').write_text(f'Core ./common.md {source}/tfi-unified-id\n')
    destination = tmp_path/'package'
    export_core_skills(destination, source_root=source)
    output = destination/'data/share_root/Skills'
    assert {path.name for path in output.iterdir()} == {
        'tfi-agent-rule', 'tfi-unified-id', 'manage-knowledge-base', 'create-skill'}
    assert '${share_root}/Skills/tfi-unified-id' in (output/'tfi-agent-rule/SKILL.md').read_text()


@pytest.mark.parametrize('name', ['tfi-agent-rule', 'tfi-unified-id', 'manage-knowledge-base', 'create-skill'])
def test_explicit_source_rejects_noncanonical_rule_directory(tmp_path, name):
    source = source_rules(tmp_path)
    (source/name).rename(source/'archived-rule')
    with pytest.raises(ValueError, match='Required Portal .* Skill file is unavailable'):
        export_core_skills(tmp_path/'package', source_root=source)
