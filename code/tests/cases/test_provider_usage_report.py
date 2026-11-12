import json
from types import SimpleNamespace

import pytest
import yaml

from scripts.provider_weekly_usage import build_report, moment
from scripts.provider_usage_report import markdown, publish_report, write_report

START = '2026-11-01T00:00:00Z'
END = '2026-11-08T00:00:00Z'


def empty_report():
    return build_report({'accounts': []}, START, END)


def test_artifacts_use_deployment_resolver_and_have_verifiable_hashes(tmp_path, monkeypatch):
    from scripts import provider_usage_report as output
    import hashlib
    calls = []
    monkeypatch.setattr(output, 'record_path', lambda value: calls.append(value) or tmp_path)
    monkeypatch.setattr(output, 'current_instance', lambda: SimpleNamespace(reference=lambda p: 'features:usage/' + p.name))
    refs = write_report(empty_report(), 'features:usage')
    assert calls == ['features:usage']
    assert all(ref.startswith('features:usage/') for ref in refs.values())
    manifest = json.loads((tmp_path / 'latest-manifest.json').read_text())
    for kind, ref in refs.items():
        data = (tmp_path / ref.rsplit('/', 1)[-1]).read_bytes()
        assert manifest['sha256'][kind] == hashlib.sha256(data).hexdigest()
    assert not manifest['provider_usage_refresh_requested']
    assert 'error bound is unknown' in (tmp_path / 'latest.md').read_text()


def test_publish_preserves_metadata_checks_version_and_removes_stale_source_paths():
    calls = []
    def call(name, **arguments):
        calls.append((name, arguments))
        if name == 'ReadResource':
            return {'version': 7, 'text': '---\nkb_id: information_x\nproject: P\nlinks:\n  source: ["/obsolete/host/path"]\n---\nOld report'}
        return {'version': 8}
    result = publish_report(call, 'information_x', empty_report(), {'json': 'features:usage/run.json'})
    assert result['version'] == 8
    assert [name for name, _ in calls] == ['ReadResource', 'UpdateResource']
    changes = calls[-1][1]['changes']
    assert changes['expected_version'] == 7
    assert '/obsolete' not in changes['text']
    metadata = yaml.safe_load(changes['text'].split('---', 2)[1])
    assert metadata['project'] == 'P'
    assert 'features:usage/run.json' in metadata['links']['source']


def test_publish_rejects_wrong_resource_identity():
    def call(name, **_):
        assert name == 'ReadResource'
        return {'version': 1, 'text': '---\nkb_id: different\n---\nbody'}
    with pytest.raises(ValueError, match='identity'):
        publish_report(call, 'information_x', empty_report(), {})


def test_source_area_references_use_unified_deployment_projection(tmp_path, monkeypatch):
    from management_common import portal_paths
    p = tmp_path / 'input.jsonl'
    p.write_text(json.dumps({'request_id': 'r', 'occurred_at': START, 'model': 'm', 'usage': {'total_tokens': 9}}))
    calls = []
    original = portal_paths.resolve_directory
    def resolve(value):
        calls.append(value)
        return p if value == 'features:input.jsonl' else original(value)
    monkeypatch.setattr(portal_paths, 'resolve_directory', resolve)
    report = build_report({'accounts': [{'provider': 'p', 'account_id': 'a', 'sources': [
        {'path': 'features:input.jsonl', 'format': 'requests'}]}]}, START, END)
    assert 'features:input.jsonl' in calls
    assert report['accounts'][0]['totals']['total_tokens'] == 9


def test_local_cli_never_requests_provider_usage_and_writes_report(tmp_path, monkeypatch, capsys):
    from scripts import provider_weekly_usage as script, provider_usage_local as local
    from scripts import provider_usage_report as output
    from scripts import resident_hatg_instruction as isa
    calls = []
    def execute(_, body, **kwargs):
        calls.append(body)
        assert body['instruction'] == 'ListProviderAccounts'
        assert body['arguments'] == {'refresh': False}
        return {'result': {'accounts': []}}
    monkeypatch.setattr(isa, 'execute', execute)
    monkeypatch.setattr(local, 'local_manifest', lambda *a, **k: {'accounts': []})
    from scripts import provider_usage_incremental as incremental
    monkeypatch.setattr(incremental, 'record_path', lambda _: tmp_path / 'cache')
    monkeypatch.setattr(output, 'record_path', lambda _: tmp_path)
    monkeypatch.setattr(output, 'current_instance', lambda: SimpleNamespace(reference=lambda p: 'features:usage/' + p.name))
    assert script.main(['--resident-id', 'resident_561324ac1c0c', '--local-records', '--write-report', '--rebuild',
                        '--since', START, '--until', END]) == 0
    assert len(calls) == 1
    assert not json.loads(capsys.readouterr().out)['provider_usage_refresh_requested']
    assert (tmp_path / 'latest.json').is_file()


def test_cli_instance_selection_uses_host_registry(tmp_path, monkeypatch, capsys):
    from management_common import host_instances
    from scripts import provider_weekly_usage as script
    import os
    manifest = tmp_path / 'manifest.json'
    manifest.write_text('{"accounts": []}')
    selected = []
    monkeypatch.setenv('PORTAL_INSTANCE_ROOT', 'original-instance')
    monkeypatch.setattr(host_instances, 'find', lambda name: selected.append(name) or SimpleNamespace(root=tmp_path))
    monkeypatch.setattr(script, 'record_path', lambda _: manifest)
    from scripts import provider_usage_incremental as incremental
    monkeypatch.setattr(incremental, 'record_path', lambda _: tmp_path / 'cache')
    assert script.main(['--instance', 'release-test', '--manifest', 'features:input.json', '--rebuild',
                        '--since', START, '--until', END]) == 0
    assert selected == ['release-test']
    assert os.environ['PORTAL_INSTANCE_ROOT'] == str(tmp_path)
    assert json.loads(capsys.readouterr().out)['accounts'] == []


def test_named_extra_codex_limit_is_scoped_to_its_model():
    from scripts.provider_usage_native import quota_rows as native_rows
    from scripts.provider_usage_quota_scope import scope_quota_rows

    def quota_rows(payload):
        return scope_quota_rows(native_rows(payload), payload)

    snapshot ={'observed_at': START, 'accounts': [{'account_id': 'a', 'rate_limits': {
        'limit_id': 'codex', 'primary': {'used_percent': 10, 'resets_at': END, 'window_duration_mins': 10080},
        'by_limit_id': {'codex': {'limit_name': '', 'primary': {'used_percent': 10, 'resets_at': END,
                                                                'window_duration_mins': 10080}},
                        'codex_bengalfox': {'limit_name': 'GPT-5.3-Codex-Spark', 'primary': {
                            'used_percent': 0, 'resets_at': END, 'window_duration_mins': 10080}}}}}]}
    scopes = {(row['quota_group'], tuple(row.get('models') or [])) for row in quota_rows(snapshot)}
    assert scopes == {('codex', ()), ('codex_bengalfox', ('gpt-5.3-codex-spark',))}


def test_reset_endpoint_jitter_is_bounded_not_transitively_merged():
    from scripts.provider_usage_cycles import normalize_quota_endpoints
    from datetime import timedelta
    base = moment(END)
    quotas = [{'resets_at': (base + timedelta(seconds=s)).isoformat(), 'window_duration_mins': 10080}
              for s in (0, 30, 60, 90)]
    normalized = normalize_quota_endpoints(quotas, moment)
    assert len({q['resets_at'] for q in normalized}) == 2
    assert quotas[1]['resets_at'] != normalized[1]['resets_at']  # Input evidence is not modified.


def test_cycle_bounds_indexes_tokens_instead_of_rescanning_per_reset():
    from scripts.provider_usage_cycles import cycle_bounds
    from datetime import timedelta
    base = moment(START)
    events = [{'at': (base + timedelta(minutes=i)).isoformat(), 'model': 'm', 'usage': {'total_tokens': 1}}
              for i in range(1000)]
    # Ten observed refreshes: remaining falls within a block, returns to 100%, then usage resumes.
    # A moved reset endpoint alone is not a boundary, so the fixture carries percentages.
    quotas = [{'at': (base + timedelta(minutes=10*i)).isoformat(),
               'resets_at': (base + timedelta(days=7+i//10)).isoformat(), 'window_duration_mins': 10080,
               'remaining_percent': 100 - 5 * (i % 10)}
              for i in range(100)]
    calls = []
    def parse(value):
        calls.append(value)
        return moment(value)
    result = cycle_bounds(events, quotas, parse)
    assert result
    assert len(calls) < 2000
