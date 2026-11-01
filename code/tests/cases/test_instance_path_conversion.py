"""实例离线路径转换保留正文与主机文件。"""
from pathlib import Path

from management_common.instance_manifest import InstanceManifest
from scripts.instance_storage_migration import PrefixMap
from scripts.migrate_instance_database_paths import Conversion


def test_location_conversion_preserves_prose_and_host_locations(tmp_path):
    """路径字段归入区域，消息正文与主机目录保持原样"""
    root = tmp_path / 'instance'
    project = tmp_path / 'projects'
    target = project / 'P/agents/lineage_a/workspace'
    target.mkdir(parents=True)
    host = tmp_path / 'host'
    host.mkdir()
    old = str(project / 'P/knowledge_base/agents/lineage_a/workspace')
    instance = InstanceManifest(root, {'areas': {'projects': str(project)}})
    converter = Conversion(instance, PrefixMap([(old, str(target))]))
    before = {'attributes': {'path': old}, 'text': old, 'prompt': old,
              'message': {'path': old}, 'account_dir': str(host)}
    after = converter.convert(before, 'registry')
    assert after['attributes']['path'] == 'projects:P/agents/lineage_a/workspace'
    assert after['text'] == after['prompt'] == old
    assert after['message'] == before['message']
    assert after['account_dir'] == str(host)


def test_deleted_outside_locations_and_missing_registrations_are_reported(tmp_path):
    """已删除登记的区域外路径不动，缺失的有效登记单列"""
    project = tmp_path / 'projects'
    project.mkdir()
    instance = InstanceManifest(tmp_path / 'instance', {'areas': {'projects': str(project)}})
    converter = Conversion(instance, PrefixMap([]))
    missing = str(project / 'missing')
    assert converter.convert({'path': missing}, 'record')['path'] == missing
    assert converter.missing['record.path'] == {missing}
    deleted = {'path': missing, 'deleted_at': '2026-01-01'}
    assert converter.convert(deleted, 'deleted')['path'] == 'projects:missing'
    outside = str(tmp_path / 'old-host')
    assert converter.convert({'path': outside, 'deleted_at': '2026-01-01'}, 'deleted')['path'] == outside
    assert 'deleted.path' not in converter.missing


def test_prefix_mappings_compose_and_respect_path_boundaries(tmp_path):
    """连续搬迁按完整路径段组合，不误改同名前缀"""
    target = tmp_path / 'instance/data/features/work/file'
    target.parent.mkdir(parents=True)
    target.touch()
    instance = InstanceManifest(tmp_path / 'instance', {})
    converter = Conversion(instance, PrefixMap([('/old', '/middle'), ('/middle', str(target.parent))]))
    assert converter.location('/old/file', 'field') == 'features:work/file'
    assert converter.location('/older/file', 'field') == '/older/file'


def test_json_locations_follow_area_moves_without_rewriting_stored_records(tmp_path, monkeypatch):
    """执行工作目录随清单的区域位置解析，正文和主机路径不改变"""
    import json
    from management_common.json_columns import json_text, json_value
    from tests.cases.instance_test_support import select_instance
    instance = select_instance(monkeypatch, tmp_path / 'instance')
    original = instance.area('features')
    task = original / 'execution/task'
    task.mkdir(parents=True)
    payload = {'cwd': str(task), 'metadata': {'artifact_path': str(task / 'result.txt')},
               'prompt': str(task), 'message': {'path': str(task)}, 'account_dir': str(tmp_path / 'host')}
    encoded = json_text(payload)
    stored = json.loads(encoded)
    assert stored['cwd'] == 'features:execution/task'
    assert stored['metadata']['artifact_path'] == 'features:execution/task/result.txt'
    assert json_value(encoded) == payload
    target = tmp_path / 'relocated-features'
    original.rename(target)
    manifest = instance.root / 'instance.json'
    document = json.loads(manifest.read_text())
    document.setdefault('areas', {})['features'] = str(target)
    manifest.write_text(json.dumps(document))
    decoded = json_value(encoded)
    assert decoded['cwd'] == str(target / 'execution/task')
    assert decoded['metadata']['artifact_path'] == str(target / 'execution/task/result.txt')
    assert decoded['prompt'] == payload['prompt']
    assert decoded['message'] == payload['message']
    assert decoded['account_dir'] == payload['account_dir']
