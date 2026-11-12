"""Pulse homepage reads current display rows, not the entire publication history."""
import json

from management_portal import portal_pulse_backend as pulse, pulse_publication_store as store


def test_home_history_is_bounded_and_keeps_old_current_source_publication(tmp_path, monkeypatch):
    monkeypatch.setattr(pulse, '_pulse_root', lambda: tmp_path)
    (tmp_path/'index.json').write_text(json.dumps({'latest': {'daily': 'current'}}))
    directory = tmp_path/'summaries/daily'
    directory.mkdir(parents=True)
    (directory/'current.json').write_text(json.dumps({
        'period': 'daily', 'key': 'current', 'content': {'user_private_updates': {
            'alice': {'items': [{'id': 'one', 'text': 'mine'}, {'id': 'two', 'text': 'unpublished'}]},
            'bob': {'items': [{'id': 'hidden', 'text': 'private bob'}]},
        }}}))
    with store.database().transaction(store.LOCK) as connection:
        for i in range(250):
            store.add(connection, dict(publication_id=f'pub_{i:04}', owner='alice' if i == 0 else 'bob',
                source_period='daily', source_key='current' if i == 0 else 'historical',
                source_item_id='one', text=f'public {i}', project='', evidence_paths=[],
                published_at=f'2026-11-12T12:{i // 60:02}:{i % 60:02}Z'))
    decoded = []
    original_decode = store.decode_times
    def decode(row, fields):
        decoded.append(row['publication_id'])
        return original_decode(row, fields)
    monkeypatch.setattr(store, 'decode_times', decode)
    original_index = pulse._read_index
    reads = []
    def index():
        reads.append(True)
        return original_index()
    monkeypatch.setattr(pulse, '_read_index', index)
    result = pulse.get_home_payload('alice')
    assert [p['publication_id'] for p in result['publications']] == [f'pub_{i:04}' for i in range(249, 219, -1)]
    private = result['periods']['daily']['mine']['items']
    assert [p['id'] for p in private] == ['one', 'two']
    assert private[0]['published'] and not private[1]['published']
    assert result['status']['latest'] == {'daily': 'current'}
    assert len(decoded) == 31, 'Only latest public rows and this viewer current-source rows are decoded'
    assert len(reads) == 1, 'Content and status must use the same index snapshot'
