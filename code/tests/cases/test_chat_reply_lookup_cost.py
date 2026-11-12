"""Reply placement reads only the execution, not every older trace output."""
import json

from management_portal.execution import chat_reply_placement
from tests.cases.test_conversation_input_work import delivered_work, team, owners  # noqa: F401


def test_reply_placement_uses_execution_index_and_skips_unneeded_context(delivered_work):
    rid, repo, store, head, appended, execution, _ = delivered_work
    for index in range(60):
        store.display_chat_message(rid, {
            'message_id': f'older-{index}', 'role': 'agent', 'author_id': rid,
            'reply_to_message_id': head, 'execution_id': f'other-execution-{index}',
            'content_parts': [{'type': 'text', 'text': 'earlier output'}],
        })
    repo.append_message(rid, {
        'message_id': 'current-output', 'role': 'agent', 'author_id': rid,
        'reply_to_message_id': head, 'execution_id': execution,
        'content_parts': [{'type': 'text', 'text': 'current answer'}],
    })
    segment = store.chat_message_provenance(rid, 'current-output')['segment_id']
    plans = []
    with store.database.read() as connection:
        # This fixture is tiny; exercise the indexed plan used by the live
        # large table instead of its cheaper fixture-only sequential scan.
        connection.execute('SET LOCAL enable_seqscan=off')
        class Measured:
            def execute(self, query, parameters):
                row = connection.execute('EXPLAIN (ANALYZE, FORMAT JSON) ' + query, parameters).fetchone()
                raw = row['QUERY PLAN']
                plans.append((json.loads(raw) if isinstance(raw, str) else raw)[0]['Plan'])
                return connection.execute(query, parameters)

        measured = Measured()
        assert chat_reply_placement.reply_placements(measured, [segment]) == {segment: appended}
        context = chat_reply_placement.message_contexts(measured, [segment], ['current-output'])
        assert 'conversation_context' not in context[segment]

    def nodes(plan):
        yield plan
        for child in plan.get('Plans', []):
            yield from nodes(child)

    # A trace-ordered nested loop would read each historical Segment for this
    # one answer. Bound actual reads rather than require one planner shape.
    reads = [node for node in nodes(plans[0]) if node.get('Relation Name') == 'segments']
    assert sum(node['Actual Loops'] * (node['Actual Rows'] + node.get('Rows Removed by Filter', 0))
               for node in reads) < 20, json.dumps([
                   {key: node.get(key) for key in ('Alias', 'Index Name', 'Actual Rows', 'Actual Loops', 'Rows Removed by Filter')}
                   for node in reads])
    # Ordinary execution output has no context search at all.
    candidates = [node for node in nodes(plans[1]) if node.get('Alias') == 'candidate']
    assert all(node['Actual Loops'] == 0 for node in candidates)
