"""The author CLI delegates to the single authenticated HTTP review owner."""
from scripts import submit_explanation_review as client
from scripts.build_explanation_package import build
import pytest
import io
import json


def test_prepare_cli_requires_explicit_creation_and_never_supplies_another_identity(monkeypatch, capsys):
    import sys
    actor = 'resident_111111111111'
    calls = []
    def request(current, path, body):
        calls.append((current, path, body))
        return {'status': 'prepared', 'quality': 'high', 'kind': 'page'}
    monkeypatch.setattr(client, 'request', request)
    args = ['submit_explanation_review.py', '--resident-id', actor, '--prepare', 'page', '--task', 'Observe the central relation']
    monkeypatch.setattr(sys, 'argv', args)
    with pytest.raises(SystemExit) as error: client.main()
    assert error.value.code == 2 and not calls
    capsys.readouterr()
    monkeypatch.setattr(sys, 'argv', args + ['--quality', 'high'])
    client.main()
    assert json.loads(capsys.readouterr().out)['status'] == 'prepared'
    assert calls == [(actor, 'reviews/prepare', {'kind': 'page', 'quality': 'high', 'task': 'Observe the central relation'})]
    monkeypatch.setattr(sys, 'argv', args + ['--quality', 'high', '--read', 'a' * 64])
    with pytest.raises(SystemExit) as error: client.main()
    assert error.value.code == 2 and len(calls) == 1


def test_builder_requires_quality_before_writing_or_submitting(tmp_path, monkeypatch, capsys):
    import sys
    from scripts import build_explanation_package as builder
    (tmp_path/'explanation.md').write_text('A fixed explanation.', encoding='utf-8')
    calls = []
    monkeypatch.setattr(client, 'submit', lambda *a, **k: calls.append(a))
    monkeypatch.setattr(sys, 'argv', ['build_explanation_package.py', str(tmp_path),
        '--address', 'workspace_0123456789ab/file/example', '--title', 'Example',
        '--task', 'Explain cause', '--supplement', 'explanation.md'])
    with pytest.raises(SystemExit) as stopped:
        builder.main()
    assert stopped.value.code == 2
    assert '--quality' in capsys.readouterr().err
    assert not (tmp_path/'manifest.json').exists()
    assert calls == []


def test_builder_retains_fixed_package_and_fails_when_high_submission_fails(tmp_path, monkeypatch, capsys):
    import sys
    from scripts import build_explanation_package as builder, submit_explanation_review as sender
    (tmp_path/'explanation.md').write_text('A fixed explanation.', encoding='utf-8')
    monkeypatch.setattr(sender, 'submit', lambda *a, **k: {'status': 'submission_failed'})
    monkeypatch.setattr(sys, 'argv', ['build_explanation_package.py', str(tmp_path),
        '--address', 'workspace_0123456789ab/file/example', '--title', 'Example', '--task', 'Explain cause',
        '--quality', 'high', '--supplement', 'explanation.md'])
    with pytest.raises(SystemExit) as stopped:
        builder.main()
    assert stopped.value.code == 1
    assert (tmp_path/'manifest.json').is_file()
    assert 'submission_failed' in capsys.readouterr().out

def test_cli_passes_fixed_request_without_claiming_verdict():
    calls = []
    def request(actor, path, body):
        calls.append((actor, path, body))
        return {'status': 'submitted', 'id': 'a'*64}
    result = client.submit('workspace_111111111111/file/lesson/manifest.json', 'a'*64,
                           '看懂因果', 'resident_111111111111', previous_review_id='b'*64, call=request)
    assert result['status'] == 'submitted'
    assert calls[0][2]['previous_review_id'] == 'b'*64
    assert 'author' not in calls[0][2] and 'status' not in calls[0][2]
    assert calls[0][0] == 'resident_111111111111'

def test_low_builder_is_markdown_and_medium_does_not_claim_review(tmp_path):
    (tmp_path/'reading.md').write_text('# 解释')
    (tmp_path/'interaction.html').write_text('<button>计算</button>')
    value = build(tmp_path, 'workspace_111111111111/file/lesson', title='理解', quality='low', supplement='reading.md')
    assert value['quality'] == 'low' and 'review' not in value
    with pytest.raises(ValueError, match='Markdown'):
        build(tmp_path, 'workspace_111111111111/file/lesson', title='理解', quality='low', interaction='interaction.html')
    assert build(tmp_path, 'workspace_111111111111/file/lesson', title='理解', quality='medium', interaction='interaction.html')['quality'] == 'medium'


def test_cli_uses_formal_current_resident_lineage_header(monkeypatch):
    actor, lineage = 'resident_111111111111', 'lineage_222222222222'
    monkeypatch.setattr(client.isa, '_resident_id', lambda supplied: actor if supplied == actor else None)
    monkeypatch.setattr(client.isa, '_token', lambda current: 'fixture-only-token')
    def execute(current, call, **kwargs):
        assert current == actor and call['arguments'] == {'resident_id': actor}
        return {'result': {'resident': {'resident_id': actor, 'lineage_id': lineage}}}
    monkeypatch.setattr(client.isa, 'execute', execute)
    requests = []
    def open_request(req, **kwargs):
        requests.append(req)
        assert req.get_header('X-portal-lineage-id') == lineage
        assert req.get_header('Authorization') == 'Bearer fixture-only-token'
        return io.BytesIO(json.dumps({'review': None}).encode())
    monkeypatch.setattr(client, 'urlopen', open_request)
    assert client.request(actor, 'reviews?id='+'0'*64) is None
    monkeypatch.setattr(client.isa, 'execute', lambda *a, **kw: {'result': {'resident': {'resident_id': 'resident_333333333333', 'lineage_id': lineage}}})
    with pytest.raises(ValueError, match='无法核实'):
        client.request(actor, 'reviews?id='+'0'*64)
    assert len(requests) == 1


def test_builder_completes_formal_authoring_before_high_review(tmp_path, monkeypatch):
    import sys
    from scripts import build_explanation_package as builder
    (tmp_path/'explanation.md').write_text('Changed fixed explanation.', encoding='utf-8')
    calls = []
    monkeypatch.setattr(client, 'accept_authoring', lambda *args: calls.append(('authoring', args)))
    monkeypatch.setattr(client, 'submit', lambda *args, **kw: calls.append(('review', args)) or {'status': 'submitted'})
    monkeypatch.setattr(sys, 'argv', ['build_explanation_package.py', str(tmp_path), '--address',
        'workspace_0123456789ab/file/example', '--title', 'Example', '--task', 'Explain cause',
        '--quality', 'high', '--supplement', 'explanation.md', '--authoring-id', 'a'*64])
    builder.main()
    assert [entry[0] for entry in calls] == ['authoring', 'review']
    assert calls[0][1][2:4] == ('high', 'a'*64)
