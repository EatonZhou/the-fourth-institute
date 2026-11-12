"""Item graph links beyond an index page; synthetic metadata/files only.

:design: decision_3ece26686464
"""
from unittest.mock import Mock

import pytest

from knowledge_board_lan import knowledge_backend as knowledge


@pytest.mark.parametrize('root_late', [False, True])
def test_item_graph_resolves_late_page_without_losing_actor(tmp_path, monkeypatch, root_late):
    root_id = 'idea_aaaaaaaaaaaa'
    target_id = 'idea_bbbbbbbbbbbb'
    root_path = tmp_path / 'root.md'
    target_path = tmp_path / 'target.md'
    root_path.write_text(f'---\nlinks:\n  related: [{target_id}]\n---\nRoot')
    target_path.write_text('Target')

    def record(identifier, path, title):
        return {'resource_id': identifier, 'resource_type': 'idea', 'facets': ['knowledge'],
                'title': title, 'source': {'path': str(path)},
                'attributes': {'importance': 3, 'project_ids': ['fixture']}}

    root = record(root_id, root_path, 'Root')
    target = record(target_id, target_path, '')
    target['attributes']['filename'] = 'Target fallback title'
    filler = [record(f'idea_{index:012x}', tmp_path / 'not-read.md', f'Filler {index}')
              for index in range(5000)]
    rows = filler + [root, target] if root_late else [root] + filler + [target]
    visible = {'alice': rows, 'bob': [root]}
    calls = []

    def query(*, actor, offset=0, limit=5000, **kwargs):
        assert kwargs == {'facets': ['knowledge'], 'include_attributes': True}
        calls.append((actor, offset, limit))
        return {'resources': visible[actor][offset:offset + limit], 'total': len(visible[actor])}

    service = Mock()
    service.query.side_effect = query
    monkeypatch.setattr(knowledge, 'get_resource_service', lambda: service)
    monkeypatch.setattr(knowledge, '_ITEM_GRAPH_CACHE', {})
    revoked = set()

    def resolve(identifier, *, active_only, actor):
        assert identifier == root_id and active_only is False
        if actor in revoked:
            raise PermissionError('root access revoked')
        return {'kb_id': root_id, 'path': str(root_path), 'title': 'Root'}

    monkeypatch.setattr(knowledge, 'resolve_item_record', resolve)
    graph = knowledge.build_item_graph(root_id, actor='alice')
    assert {node['kb_id'] for node in graph['nodes']} == {root_id, target_id}
    assert graph['edges'] == [{'source': root_id, 'target': target_id, 'relation': 'related', 'depth': 1}]
    assert graph['unresolved_links'] == 0
    target_node = next(node for node in graph['nodes'] if node['kb_id'] == target_id)
    assert target_node['title'] == 'Target fallback title'
    assert target_node['type'] == 'idea' and target_node['project'] == 'fixture'
    assert all('path' not in node for node in graph['nodes'])
    assert calls == [('alice', 0, 5000), ('alice', 5000, 5000)]
    assert knowledge.build_item_graph(root_id, actor='alice') == graph
    assert len(calls) == 2  # Cache remains scoped to its actor; no extra index read.
    other = knowledge.build_item_graph(root_id, actor='bob')
    assert [node['kb_id'] for node in other['nodes']] == [root_id]
    assert other['edges'] == [] and other['unresolved_links'] == 1
    assert calls[-1] == ('bob', 0, 5000)
    revoked.add('alice')
    with pytest.raises(PermissionError, match='revoked'):
        knowledge.build_item_graph(root_id, actor='alice')
