from scripts.provider_usage_coverage import coverage


def test_direct_api_terminal_evidence_is_not_mislabelled_missing_raw():
    record = {'execution_id':'e', 'session_id':'s', 'started_at':'2026-11-01T00:00:00Z',
              'native_link_basis':'provider_session'}
    result=coverage({'accounts':[{'provider':'deepseek_primary', 'account_id':'api',
        'resident_records':[record], 'execution_requests':[{'request_id':'e'}]}]})
    assert result['providers']['deepseek_primary']['execution_result_only'] == 1
    assert result['unlocated_executions'] == []


def test_presence_audit_separates_missing_from_token_agreement(tmp_path):
    p=tmp_path/'s.jsonl'; p.write_text('')
    r={'execution_id':'e','session_id':'s','started_at':'2026-11-01T00:00:01Z',
       'finished_at':'2026-11-01T00:00:02Z','usage':{'total_tokens':10},
       'native_link_basis':'durable_native_artifact'}
    baseline={'since':'2026-11-01T00:00:00Z','until':'2026-11-02T00:00:00Z',
              'accounts':[{'resident_comparisons':[{'execution_id':'e','evidence_source_present':False}]}]}
    result=coverage({'accounts':[{'provider':'claude','account_id':'a','resident_records':[r],
        'sources':[{'format':'claude','path':str(p),'session_id':'s'}]}]},baseline)
    assert result['baseline_summary']=={'previously_unlocated':1,'now_located':1,'tokens_differ':1}
    assert result['months'][0]['native_source_located']==1


def test_unlocated_reason_does_not_assert_raw_was_deleted():
    records=[{'execution_id':'e1','session_id':'execution-e1','started_at':'2026-10-01T00:00:00Z'},
             {'execution_id':'e2','session_id':'known-native','started_at':'2026-09-01T00:00:00Z'}]
    result=coverage({'accounts':[{'provider':'codex','account_id':'a','resident_records':records}]})
    assert [r['reason'] for r in result['unlocated_executions']]==[
        'logical_placeholder_without_proven_native_link', 'known_session_source_not_located_in_configured_roots']
    assert result['providers']['codex'].get('native_link_recovered',0)==0


def test_human_summary_preserves_coverage_limits():
    from scripts.provider_usage_coverage import markdown
    text=markdown({'providers':{'codex':{'executions_with_usage':10,'native_source_located':9,'source_unlocated':1}},
                   'baseline_summary':{'previously_unlocated':2,'now_located':2,'tokens_equal':2}})
    assert '| codex | 10 | 9 | 0 | 1 |' in text
    assert 'All other historical rows' in text and 'not token agreement' in text


def test_terminal_evidence_crossing_window_is_not_absent_just_because_request_was_excluded():
    r={'execution_id':'e','session_id':'s','started_at':'2026-11-01T00:00:00Z',
       'evidence_kind':'execution_result'}
    result=coverage({'accounts':[{'provider':'deepseek_primary','account_id':'api','resident_records':[r]}]})
    assert result['providers']['deepseek_primary']['execution_result_only']==1
    assert result['unlocated_executions']==[]


def test_unreadable_provenance_is_kept_as_an_audit_diagnostic(tmp_path):
    p=tmp_path/'s.jsonl';p.write_text('')
    result=coverage({'accounts':[{'provider':'claude','account_id':'a','sources':[
        {'format':'claude','path':str(p),'session_id':'s','provenance_scan_failed':True}]}]})
    assert result['source_diagnostics']==[{'provider':'claude','account_id':'a',
        'discovery_gaps':['native_provenance_metadata_unreadable']}]


def test_conflicts_remain_visible_even_when_formal_session_file_exists(tmp_path):
    p=tmp_path/'s.jsonl';p.write_text('')
    result=coverage({'accounts':[{'provider':'codex','account_id':'a','resident_records':[
        {'execution_id':'e','session_id':'s','native_link_conflict':True}],
        'sources':[{'format':'codex','path':str(p),'session_id':'s'}]}]})
    assert result['conflicting_links'][0]['source_present'] is True
    assert result['unlocated_executions']==[]
