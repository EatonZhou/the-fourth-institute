"""An isolated input's sender can observe its state after identity deletion.

:module: resident.conversation
"""
from types import SimpleNamespace
import pytest
from management_portal.society_service import resident_protocol as protocol


@pytest.mark.parametrize('state', ['cancelled', 'running'])
def test_deleted_input_owner_reads_authoritative_state(monkeypatch, state):
    monkeypatch.setattr(protocol.resident_input_scope, 'permits', lambda *args: True)
    monkeypatch.setattr(protocol, 'get_portal_access_store', lambda: SimpleNamespace(is_service_actor=lambda *_: False))
    monkeypatch.setattr(protocol.resident_record_domain, 'resolve_resident_runtime_target',
                        lambda *args, **kwargs: (_ for _ in ()).throw(KeyError('resident not found')))
    monkeypatch.setattr(protocol.resident_catalog, 'get_catalog', lambda: SimpleNamespace(
        get_historical=lambda *_: {'deleted_at': '2026-11-06T00:00:00Z'}))
    monkeypatch.setattr(protocol, '_stored_resident_input_state', lambda rid, mid, **kwargs: {'message_id': mid, 'state': state})
    assert protocol.get_resident_input_state('resident_0123456789ab', 'sender', 'original') == {'message_id': 'original', 'state': state}


def test_deletion_does_not_grant_other_callers_receipts(monkeypatch):
    monkeypatch.setattr(protocol.resident_input_scope, 'permits', lambda *args: False)
    monkeypatch.setattr(protocol, 'get_portal_access_store', lambda: SimpleNamespace(is_service_actor=lambda *_: False))
    monkeypatch.setattr(protocol.resident_record_domain, 'resolve_resident_runtime_target',
                        lambda *args, **kwargs: (_ for _ in ()).throw(KeyError('resident not found')))
    monkeypatch.setattr(protocol.resident_catalog, 'get_catalog', lambda: SimpleNamespace(
        get_historical=lambda *_: {'deleted_at': '2026-11-06T00:00:00Z'}))
    monkeypatch.setattr(protocol, 'get_portal_access_store', lambda: SimpleNamespace(
        is_service_actor=lambda *_: False, is_active=lambda *_: False))
    monkeypatch.setattr(protocol, '_stored_resident_input_state', lambda *args, **kwargs: pytest.fail('unauthorized read'))
    with pytest.raises(KeyError):
        protocol.get_resident_input_state('resident_0123456789ab', 'other', 'original')


def test_original_sender_retains_only_its_input_receipt(monkeypatch):
    monkeypatch.setattr(protocol.resident_input_scope, 'permits', lambda *args: False)
    monkeypatch.setattr(protocol, 'get_portal_access_store', lambda: SimpleNamespace(
        is_service_actor=lambda *_: False, is_active=lambda *_: True))
    monkeypatch.setattr(protocol.resident_record_domain, 'resolve_resident_runtime_target',
                        lambda *args, **kwargs: (_ for _ in ()).throw(KeyError('resident not found')))
    monkeypatch.setattr(protocol.resident_catalog, 'get_catalog', lambda: SimpleNamespace(
        get_historical=lambda *_: {'deleted_at': '2026-11-06T00:00:00Z'}))
    monkeypatch.setattr(protocol.resident_conversation_stream, 'get_repository', lambda: SimpleNamespace(
        message=lambda *_: {'role': 'user', 'author_id': 'sender'}))
    def state(rid, mid, **options):
        assert not options['include_progress'] and not options['include_context']
        return {'message_id': mid, 'state': 'cancelled'}
    monkeypatch.setattr(protocol, '_stored_resident_input_state', state)
    assert protocol.get_resident_input_state('resident_0123456789ab', 'sender', 'original', include_context=True)['state'] == 'cancelled'
    with pytest.raises(KeyError):
        protocol.get_resident_input_state('resident_0123456789ab', 'other', 'original')
