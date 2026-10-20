from management_common.format_tags import schema_matches


def test_legacy_alias_is_exact_and_never_rewrites_input():
    record = {'schema': 'archive.execution-request.v8', 'value': {'schema':'opaque'}}
    assert not schema_matches(record['schema'], 'tfi.execution-request.v8')
    assert schema_matches('tfi.execution-request.v8', 'tfi.execution-request.v8')
    assert record == {'schema': 'archive.execution-request.v8', 'value': {'schema':'opaque'}}
    for value in ['archive.execution-request.v7', 'tfi.execution-request.v9', 'other.execution-request.v8', 'ARCHIVE.execution-request.v8', '', None, {}, ['tfi.execution-request.v8']]:
        assert not schema_matches(value, 'tfi.execution-request.v8')


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ
