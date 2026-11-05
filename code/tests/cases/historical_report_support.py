"""Seed pre-retirement snapshots for compatibility tests, never a public API.

This helper only establishes historical data. Tests exercise current readers,
CAS mutations, cleanup and ACLs; new deliverables use explanation_target.
"""
from management_portal import home_report_store
from management_portal.home_report_origin import verified_source
from management_portal.home_report_quality import bind, fingerprint


def seed_historical_report(store, identifier, payload, actor='alice'):
    snapshot = bind(home_report_store.build_report(payload['target'], actor=actor, read=store.read),
                    payload.get('quality'), author=actor)
    snapshot['authoring'] = verified_source(actor, payload.get('origin_context'),
        payload.get('origin_key'), snapshot['target'], read=store.read)
    snapshot['origin_key'] = payload.get('origin_key')
    snapshot['version'] = fingerprint(snapshot)
    with store.lock(identifier):
        return store._create_locked(identifier, payload, actor, snapshot)
