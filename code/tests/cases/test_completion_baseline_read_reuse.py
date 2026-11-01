from management_portal.society_service import lineage_state


def test_graph_edit_reads_each_resident_completion_once(monkeypatch):
    """Many completion subscriptions share one observation per Resident per edit."""
    reads, writes = [], []

    def latest(resident_id, actor):
        reads.append((resident_id, actor))
        return {'message_id': f'message-{len(reads)}', 'completed_at': '2026-11-01T10:00:00Z'}

    monkeypatch.setattr(lineage_state.resident_protocol, 'get_latest_completed_message_reference', latest)
    monkeypatch.setattr(lineage_state.lineage_program_observations,
                        'establish_resident_completion_subscription_baseline',
                        lambda lineage_id, event_id, **kw: writes.append((event_id, kw)) or {'status': 'baseline'})
    requests = [{'resident_id': resident, 'subject_resident_id': resident, 'event_source_id': source,
                 'locator': f'resident:{resident}', 'subscriber_ids': [f'wire-{source}'], 'per_resident': True}
                for source in ('first', 'second', 'third') for resident in ('resident_a', 'resident_b')]
    assert len(lineage_state._establish_resident_completion_baselines('lineage_a', 'actor', requests)) == 6
    assert reads == [('resident_a', 'actor'), ('resident_b', 'actor')]
    assert len(writes) == 6
    assert [kw['message_id'] for _, kw in writes] == ['message-1', 'message-2'] * 3
    # Another edit needs a fresh observation; no global or persistent cache.
    lineage_state._establish_resident_completion_baselines('lineage_a', 'actor', requests[:1])
    assert reads[-1] == ('resident_a', 'actor') and len(reads) == 3
