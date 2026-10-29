"""Deployment labels leave Principal validation and historical messages intact."""
import copy
import json
from pathlib import Path
import subprocess

from management_common import principal_presentation
from management_portal.resources.portal import portal_route_script

from management_portal.society_service import resident_conversation_records


def test_recipient_labels_are_scoped_read_only_and_escape_in_chat(monkeypatch):
    labels = {'user': {'tfi-exported-user': 'TFI portal', 'unrelated': 'not exposed',
                       'unsafe-label': '<img src=x onerror=alert(1)>'}}
    monkeypatch.setattr(principal_presentation, '_labels', lambda: labels)
    inputs = [{'message_id': 'msg_' + str(i), 'role': 'agent',
               'author_id': 'resident_111111111111',
               'content_parts': [{'type': 'text', 'text': 'Unchanged historical text'}],
               'recipients': [{'kind': kind, 'id': identity}]}
              for i, (kind, identity) in enumerate([
                  ('user', 'tfi-exported-user'), ('user', 'ordinary-user'),
                  ('user', 'unsafe-label')])]
    before = copy.deepcopy(inputs)
    messages = [resident_conversation_records.protocol_message(m) for m in inputs]
    assert inputs == before
    assert messages[0]['recipient_labels'] == {'user': {'tfi-exported-user': 'TFI portal'}}
    assert 'recipient_labels' not in messages[1]
    assert principal_presentation.recipient_labels([{'kind': 'service', 'id': 'tfi-exported-user'}]) == {}
    for original, message in zip(inputs, messages):
        assert message['recipients'] == original['recipients']
        assert message['content_parts'] == original['content_parts']
    result = subprocess.run(['node', str(Path(__file__).with_name('javascript') / 'chat_recipient_labels.cjs')],
        input=json.dumps({'routeScript': portal_route_script().decode(), 'messages': messages,
                         'expected': ['发给 TFI portal', '发给 ordinary-user',
                                      '发给 <img src=x onerror=alert(1)>']}),
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_without_deployment_labels_does_not_rename_source_users(monkeypatch):
    monkeypatch.setattr(principal_presentation, '_labels', lambda: {})
    assert principal_presentation.recipient_labels([{'kind': 'user', 'id': 'tfi-exported-user'}]) == {}


def test_sender_and_recipient_share_labels_without_account_lookup(monkeypatch):
    from management_common.principal import message_origin
    monkeypatch.setattr(principal_presentation, '_labels', lambda: {
        'user': {'tfi-exported-user': 'TFI portal'}})
    messages = []
    for identity in ('tfi-exported-user', 'ordinary-user', 'resident_111111111111', 'portal'):
        source = {'message_id': 'sender_' + identity, 'role': 'user',
                  'content_parts': [{'type': 'text', 'text': 'Historical message'}],
                  'origin': message_origin(identity)}
        message = resident_conversation_records.protocol_message(source)
        assert message['origin'] == source['origin']
        if identity == 'tfi-exported-user':
            assert message['sender_label'] == 'TFI portal'
        else:
            assert 'sender_label' not in message
        messages.append(message)
    legacy = resident_conversation_records.protocol_message({'message_id': 'legacy', 'role': 'user',
        'author_id': 'tfi-exported-user', 'content_parts': [{'type': 'text', 'text': 'Legacy'}]})
    assert legacy['sender_label'] == 'TFI portal'
    assert legacy['author_id'] == 'tfi-exported-user' and 'origin' not in legacy
    result = subprocess.run(['node', str(Path(__file__).with_name('javascript') / 'chat_recipient_labels.cjs')],
        input=json.dumps({'routeScript': portal_route_script().decode(), 'messages': messages[:2],
                         'expected': [], 'senderTitles': ['TFI portal', 'ordinary-user']}),
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
