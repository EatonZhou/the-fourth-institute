import hashlib
import json
import pytest

from scripts import submit_explanation_review as review

AUTHOR = 'resident_111111111111'
REVIEWER = 'resident_222222222222'
ADDRESS = 'workspace_111111111111/file/lesson/manifest.json'
DATA = json.dumps({'schema': 'portal-explanation/v1', 'title': '比较两条路线'})
SHA = hashlib.sha256(DATA.encode()).hexdigest()


def client(monkeypatch, residents=None):
    monkeypatch.setattr(review.isa, '_resident_id', lambda value: value)
    calls = []
    def call(actor, body):
        assert actor == AUTHOR
        calls.append(body)
        result = {'GetInstructionSchema': {}, 'ReadResource': {'text': DATA},
                  'SearchResidents': {'residents': residents if residents is not None else [
                      {'resident_id': REVIEWER, 'title': review.REVIEWER_TITLE}]},
                  'DeliverMessage': {'message_id': 'msg_review', 'accepted': True}}[body['instruction']]
        return {'status': 'committed', 'result': result}
    return calls, call


def test_submission_is_pinned_independent_and_retry_safe(monkeypatch):
    calls, call = client(monkeypatch)
    one = review.submit(ADDRESS, SHA, '看懂路线代价', AUTHOR, call=call)
    two = review.submit(ADDRESS, SHA, '看懂路线代价', AUTHOR, call=call)
    sent = [c for c in calls if c['instruction'] == 'DeliverMessage']
    assert one == two and one['status'] == 'submitted'
    assert sent[0]['idempotency_key'] == sent[1]['idempotency_key']
    assert sent[0]['arguments']['resident_id'] == REVIEWER
    assert 'temporary_credentials' not in sent[0]['arguments']
    request = json.loads(sent[0]['arguments']['message']['content_parts'][0]['text'])
    assert request['manifest'] == {'address': ADDRESS, 'sha256': SHA}
    assert request['author_resident_id'] == AUTHOR


def test_changed_bytes_never_dispatch(monkeypatch):
    calls, call = client(monkeypatch)
    with pytest.raises(ValueError, match='bytes'):
        review.submit(ADDRESS, '0' * 64, '看懂路线代价', AUTHOR, call=call)
    assert not any(c['instruction'] == 'DeliverMessage' for c in calls)


@pytest.mark.parametrize('residents', [[], [{'resident_id': AUTHOR, 'title': review.REVIEWER_TITLE}],
    [{'resident_id': REVIEWER, 'title': review.REVIEWER_TITLE},
     {'resident_id': 'resident_333333333333', 'title': review.REVIEWER_TITLE}]])
def test_missing_self_or_ambiguous_reviewer_never_dispatch(monkeypatch, residents):
    calls, call = client(monkeypatch, residents)
    with pytest.raises(ValueError, match='independent'):
        review.submit(ADDRESS, SHA, '看懂路线代价', AUTHOR, call=call)
    assert not any(c['instruction'] == 'DeliverMessage' for c in calls)
