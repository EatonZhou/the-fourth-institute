"""Exported actor identifiers obey native Principal syntax; labels stay labels."""
from management_common.principal import Principal, message_origin, validate_message_origin
from management_portal.distribution.sanitize import ANONYMOUS_LABEL, ANONYMOUS_USER_ID, scrub


def test_human_principal_and_display_label_have_separate_semantics():
    before = {'kind': 'user', 'id': 'original-human'}
    assert Principal.from_dict(scrub(before)) == Principal('user', ANONYMOUS_USER_ID)
    assert before['id'] == 'original-human'
    identity = scrub({'type': 'human', 'id': 'original-human', 'name': 'Original Human', 'title': 'Original Human'})
    assert identity == {'type': 'human', 'id': ANONYMOUS_USER_ID, 'name': ANONYMOUS_LABEL, 'title': ANONYMOUS_LABEL}
    assert scrub({'actor_ref': 'original-human', 'display_name': 'Original Human'}) == {
        'actor_ref': ANONYMOUS_USER_ID, 'display_name': ANONYMOUS_LABEL}


def test_native_message_origin_remains_valid_and_other_actor_kinds_are_unchanged():
    resident = 'resident_111111111111'
    origin = message_origin('human-one', initiated_by=resident)
    cleaned = scrub(origin)
    assert validate_message_origin(cleaned) == cleaned
    assert cleaned['sender'] == {'kind': 'user', 'id': ANONYMOUS_USER_ID}
    assert cleaned['producer'] == {'kind': 'service', 'id': 'portal'}
    assert cleaned['initiated_by'] == {'kind': 'resident', 'id': resident}
    for actor in (resident, 'portal', 'ai-socket-relay', 'mailbox'):
        assert scrub(actor, field='actor_ref') == actor
    assert scrub({'actor_ref': 'human-two', 'recipient_users': ['human-one', 'human-two']}) == {
        'actor_ref': ANONYMOUS_USER_ID, 'recipient_users': [ANONYMOUS_USER_ID]}


def test_legacy_export_repair_is_structural_and_idempotent():
    legacy = {'origin': {'schema': 'tfi.message-origin.v1', 'sender': {'kind': 'user', 'id': 'TFI portal'},
                         'producer': {'kind': 'service', 'id': 'portal'}},
              'actor_ref': 'TFI portal', 'display_name': 'TFI portal',
              'content_parts': [{'type': 'text', 'text': 'TFI portal is the displayed author.'}]}
    repaired = scrub(legacy)
    assert validate_message_origin(repaired['origin']) == repaired['origin']
    assert repaired['actor_ref'] == ANONYMOUS_USER_ID
    assert repaired['display_name'] == ANONYMOUS_LABEL
    assert repaired['content_parts'] == legacy['content_parts']
    assert scrub(repaired) == repaired
    import json
    archived = scrub({'payload_json': json.dumps(legacy)})
    assert isinstance(archived['payload_json'], str)
    assert json.loads(archived['payload_json']) == repaired


def test_collapsed_human_recipient_set_remains_native_valid_without_dropping_messages():
    from management_common.principal import message_recipients
    recipients = [{'kind': 'user', 'id': 'human-one'}, {'kind': 'resident', 'id': 'resident_111111111111'},
                  {'kind': 'user', 'id': 'human-two'}]
    messages = [{'message_id': str(i), 'recipients': recipients, 'content_parts': [{'type': 'text', 'text': 'human-one human-two'}]} for i in range(2)]
    cleaned = scrub(messages)
    assert len(cleaned) == 2
    for before, after in zip(messages, cleaned):
        assert after['content_parts'] == before['content_parts']
        assert message_recipients(after) == [{'kind': 'user', 'id': ANONYMOUS_USER_ID}, recipients[1]]
    assert scrub(cleaned) == cleaned
    assert scrub(messages, anonymize=False) == messages
    # A corrupt duplicate Resident is not silently concealed by human anonymization.
    invalid = scrub({'recipients': [recipients[1], recipients[1]]})
    import pytest
    with pytest.raises(ValueError, match='once'):
        message_recipients(invalid)
