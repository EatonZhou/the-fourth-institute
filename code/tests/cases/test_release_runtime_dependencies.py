import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_common.release_dependencies import declaration, profiles
from scripts import tfi_release_dependencies as dependencies


def test_selected_capabilities_cannot_be_omitted_from_the_dependency_contract():
    manifest = {'features': ['tau'], 'resource_dependencies': {'types': ['article']}}
    manifest['runtime_dependencies'] = declaration(manifest['features'], ['article'])
    assert set(profiles(manifest)) == {'core', 'tau', 'article'}
    manifest['runtime_dependencies']['profiles'].remove('article')
    with pytest.raises(ValueError, match='omits'):
        profiles(manifest)
    assert profiles({}) == []


def test_tex_download_integrity_failure_cannot_activate_tools(tmp_path, monkeypatch):
    import io
    from scripts import article_dependencies as article
    monkeypatch.setattr(article.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(article.platform, 'machine', lambda: 'x86_64')
    monkeypatch.setattr(article.urllib.request, 'urlopen', lambda *a, **kw: io.BytesIO(b'wrong archive'))
    process = Mock()
    monkeypatch.setattr(article, 'run_process', process)
    with pytest.raises(ValueError, match='checksum mismatch'):
        article.prepare_tex(tmp_path)
    assert not (tmp_path / '.TinyTeX').exists()
    process.assert_not_called()


def test_article_doctor_rejects_missing_float_barrier_package(monkeypatch):
    from scripts import article_dependencies as article
    monkeypatch.setattr(article.shutil, 'which', lambda name, **_: '/private/bin/' + name)
    available = False

    def process(command, **kwargs):
        missing = command[-1] == 'placeins.sty' and not available
        return SimpleNamespace(returncode=1 if missing else 0,
                               stdout='' if missing else '/private/tex/' + command[-1], stderr='')

    monkeypatch.setattr(article, 'run_native_capture', process)
    failed = [item['name'] for item in article.inventory() if not item['ok']]
    assert failed == ['article: placeins.sty']
    available = True
    assert all(item['ok'] for item in article.inventory())


def test_missing_packages_prepare_private_python_and_failed_doctor_blocks_activation(tmp_path):
    from scripts import tfi_release_install as installer
    target = {'install_root': str(tmp_path), 'python': '/operator/shared/bin/python'}
    state = {'target': target, 'manifest_sha256': 'a' * 64}
    manifest = {'runtime_dependencies': declaration(['article']), 'features': ['article']}
    (tmp_path / 'runtime').mkdir()
    commands = []
    healthy = False

    def process(command, **kwargs):
        commands.append(command)
        if 'install' in command:
            assert command[0] == target['python']
            assert str(tmp_path / 'runtime/dependencies') in command[-1]
            return SimpleNamespace(returncode=0)
        ok = healthy and '/runtime/dependencies/' in command[0]
        return SimpleNamespace(returncode=0 if ok else 1, stdout=json.dumps([{'name': 'checked', 'ok': ok}]))

    api = SimpleNamespace(run_process=process, run_utf8_capture=process, clean_environment=lambda _: {}, native_tool=lambda name: name,
        save_stage=lambda value, *_: installer.write_json(tmp_path / 'installation.json', value))
    with pytest.raises(RuntimeError, match='services were not started'):
        dependencies.prepare(api, state, manifest)
    assert 'dependencies' not in state
    assert dependencies.runtime_python(target) == target['python']
    healthy = True
    dependencies.prepare(api, state, manifest)
    assert state['dependencies']['source'] == 'instance-owned-bootstrap'
    assert dependencies.runtime_python(target).startswith(str(tmp_path / 'runtime/dependencies'))
    # A different engineering generation cannot activate a previous dependency receipt.
    state['manifest_sha256'] = 'b' * 64
    installer.write_json(tmp_path / 'installation.json', state)
    assert dependencies.runtime_python(target) == target['python']


def test_selected_skill_copies_whole_registered_package_and_relocates_primary(tmp_path):
    from management_portal.distribution.selection import resource_file_scope
    from management_portal.distribution.engineering import copy_content
    from management_portal.distribution.sanitize import scrub
    root = tmp_path / 'source/skill'
    (root / 'scripts').mkdir(parents=True)
    (root / 'SKILL.md').write_text('# Skill\n')
    (root / 'scripts/tool.py').write_text('print("hello")\n')
    record = {'resource_type': 'skill', 'source': {'path': str(root / 'SKILL.md')},
              'attributes': {'content_root': str(root)}}
    scope = resource_file_scope('skill_0123456789ab', record)
    destination = tmp_path / 'generated' / scope['target']
    copy_content(scope['source'], destination, selected=set())
    relocated = scrub(record, mappings=[(str(root), str(destination))])
    assert Path(relocated['source']['path']).read_text() == '# Skill\n'
    assert (Path(relocated['attributes']['content_root']) / 'scripts/tool.py').is_file()


def test_core_manifest_uses_target_skill_directory_in_a_fresh_interpreter(tmp_path):
    import os
    import subprocess
    import sys
    from management_portal.distribution.core_skills import canonical_resources, export_core_skills
    from management_common.portal_paths import KEYS
    destination = tmp_path / 'package'
    export_core_skills(destination)
    share = destination / 'data/share_root'
    config = tmp_path / 'paths.json'
    config.write_text(json.dumps({key: str(share if key == 'share_root' else tmp_path / key) for key in KEYS}))
    result = subprocess.run([sys.executable, '-B', '-c',
        'from skill_board_lan import skill_backend as s; print(s.SHARED_SKILLS_ROOT); print(len(s._iter_shared_skill_roots()))'],
        env={**os.environ, 'PORTAL_PATHS_CONFIG': str(config)}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [str(share / 'Skills'), str(len(canonical_resources()))]
    for identity, source in canonical_resources().items():
        assert identity in (share / 'Skills' / source.name / 'SKILL.md').read_text()


def test_selected_article_preserves_compiler_configuration_without_other_articles_or_reports(tmp_path):
    from tests.cases.test_distribution_lineage_options import ExportSource
    from tests.cases.test_distribution_resource_dependencies import selected
    from management_portal.distribution.selection import resolve
    from management_portal.distribution.database import SCHEMAS
    source = ExportSource()
    identity = 'article_0123456789ab'
    primary = tmp_path / 'main.tex'
    primary.write_text('\\documentclass[fontset=fandol]{ctexbook}\n')
    original = source.read_index()
    source.read_index = lambda: {**original, identity: {'resource_type': 'article',
        'attributes': {'canonical_root': str(tmp_path)}, 'source': {'path': str(primary)}}}
    settings = {'article_id': identity, 'compiler': 'xelatex', 'primary_file': 'main.tex',
                'shell_escape': False, 'use_project_latexmkrc': False}
    source.tables['article', 'build_settings'] = SimpleNamespace(columns={})
    source.data['article', 'build_settings'] = [settings, {**settings, 'article_id': 'article_ffffffffffff'}]
    source.data['article', 'build_reports'] = [{'article_id': identity, 'log': 'private source runtime'}]
    snapshot = selected('article')
    snapshot['resource_types'][0]['instances'] = [identity]
    resolved = resolve(snapshot, source)
    assert 'article' in SCHEMAS
    assert resolved['tables']['article', 'build_settings'] == [settings]
    assert ('article', 'build_reports') not in resolved['tables']


def test_selected_core_index_relocates_to_the_naturally_exported_rules(tmp_path):
    from tests.cases.test_distribution_lineage_options import ExportSource
    from tests.cases.test_distribution_resource_dependencies import selected
    from management_portal.distribution.core_skills import canonical_resources, export_core_skills
    from management_portal.distribution.selection import resolve
    from management_portal.distribution.sanitize import scrub
    from scripts.tfi_release_install import map_directories
    source = ExportSource()
    identity, canonical = next(iter(canonical_resources().items()))
    record = {'resource_type': 'skill', 'source': {'path': '/old-site/rules/SKILL.md'},
              'attributes': {'content_root': '/old-site/rules'}}
    original = source.read_index()
    source.read_index = lambda: {**original, identity: record}
    snapshot = selected('skill')
    snapshot['resource_types'][0]['instances'] = [identity]
    resolved = resolve(snapshot, source)
    package = tmp_path / 'generated'
    export_core_skills(package)
    portable = json.dumps(scrub(record, mappings=resolved['path_mappings']))
    installed = json.loads(map_directories(portable, {'${share_root}': str(package / 'data/share_root')}))
    assert Path(installed['source']['path']).is_file()
    assert Path(installed['attributes']['content_root']).name == canonical.name
    assert identity in Path(installed['source']['path']).read_text()
    assert not any('/old-site/' in item['source'] for item in resolved['files'])
