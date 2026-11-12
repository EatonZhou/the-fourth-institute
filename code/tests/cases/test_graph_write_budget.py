"""Graph write work budgets and delta semantics.

:module: lineage.team
"""
from management_portal.society_service import graph_storage, edge_storage


class Connection:
    def __init__(self, rows=()):
        self.rows = rows
        self.statements = []
        self.batches = []

    def execute(self, sql, args):
        self.statements.append((sql, args))
        return self

    def fetchall(self):
        return self.rows

    def executemany(self, sql, args):
        self.batches.append((sql, args))


def test_unchanged_members_do_not_write():
    rows = [{'element_id': str(i), 'element_kind': 'resident', 'element_key': None} for i in range(1000)]
    c = Connection(rows)
    graph_storage._reconcile(c, 'function_block_elements', 'function_block_id', 'block', ('element_id',), rows)
    assert len(c.statements) == 1
    assert not c.batches


def test_member_delta_removes_changes_and_adds():
    c = Connection([{'element_id': 'old', 'element_kind': 'resident', 'element_key': None},
                    {'element_id': 'edit', 'element_kind': 'resident', 'element_key': None}])
    rows = [{'element_id': 'edit', 'element_kind': 'resident', 'element_key': 'new'},
            {'element_id': 'add', 'element_kind': 'component', 'element_key': None}]
    graph_storage._reconcile(c, 'function_block_elements', 'function_block_id', 'block', ('element_id',), rows)
    assert c.batches[0][1] == [('block', 'old')]
    assert c.batches[1][1] == [['block', 'edit', 'resident', 'new'], ['block', 'add', 'component', None]]


def test_ordinary_edges_do_not_issue_empty_handoff_deletes():
    c = Connection()
    edge_storage.put_many(c, [{'id': str(i)} for i in range(1000)])
    assert len(c.statements) == 1
    assert not c.batches


def test_handoff_type_change_removes_stored_items():
    c = Connection([{'edge_id': 'edge', 'item_id': 'item', 'position': 0,
                     **{edge_storage.column(k, v): None for k, v in edge_storage.ITEM_FIELDS.items()}}])
    edge_storage.put_many(c, [{'id': 'edge', 'kind': 'route'}])
    assert len(c.statements) == 1
    assert c.batches[0][1] == [('edge', 'item')]
