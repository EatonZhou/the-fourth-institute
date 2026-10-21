"""Canonical security readers reject retired tags and preserve all authority guards."""
import copy
import hashlib
import hmac
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from management_common import principal, resource_turn_artifacts, rpc_auth
from management_portal.hatg.contracts import InstructionCall, InstructionError
from management_portal.portal_access_control import PortalAccessStore
from management_portal.portal_passkeys import PortalPasskeyStore
from management_portal.portal_password_reset import PortalPasswordResetStore
from management_portal.portal_two_factor import PortalTwoFactorStore


@pytest.mark.parametrize('prefix', ['archive', 'tfi'])
def test_origin_identity_and_call_preserve_authority_and_payload(prefix):
    """来源、操作身份和指令读取保持权限与载荷，退役标签被拒绝且输入不变"""
    for origin in (principal.message_origin('alice'), principal.mailbox_origin('alice')):
        version = origin['schema'].rsplit('.', 1)[1]
        origin['schema'] = f'{prefix}.message-origin.{version}'
        original = copy.deepcopy(origin)
        if prefix == 'archive':
            with pytest.raises(ValueError):
                principal.validate_message_origin(origin)
        else:
            assert principal.validate_message_origin(origin)['sender'] == origin['sender']
        assert origin == original
        canonical = {**origin, 'schema': f'tfi.message-origin.{version}'}
        with pytest.raises(ValueError):
            principal.validate_message_origin({**canonical, 'producer': {'kind': 'user', 'id': 'alice'}})
        with pytest.raises(ValueError):
            principal.validate_message_origin({**origin, 'schema': f'{prefix}.message-origin.v3'})
    identity = principal.OperationIdentity.for_actor('alice', initiated_by='bob').to_dict()
    identity['schema'] = f'{prefix}.operation-identity.v1'
    original = copy.deepcopy(identity)
    if prefix == 'archive':
        with pytest.raises(ValueError):
            principal.OperationIdentity.from_dict(identity)
    else:
        parsed = principal.OperationIdentity.from_dict(identity)
        assert parsed.principal.id == 'alice' and parsed.initiated_by.id == 'bob'
    assert identity == original
    with pytest.raises(ValueError):
        principal.OperationIdentity.from_dict({**identity, 'schema': f'{prefix}.operation-identity.v2'})
    with pytest.raises(ValueError):
        principal.OperationIdentity.from_dict({**identity, 'schema': 'tfi.operation-identity.v1', 'producer': {'kind': 'user', 'id': 'bob'}})
    call = {'schema': f'{prefix}.hatg-instruction.v2', 'instruction': 'GetResident',
            'arguments': {'resident_id': 'resident_111111111111'},
            'idempotency_key': 'frozen-key', 'expected_version': 'unchanged-token'}
    original = copy.deepcopy(call)
    if prefix == 'archive':
        with pytest.raises(InstructionError):
            InstructionCall.from_payload(call)
    else:
        parsed = InstructionCall.from_payload(call)
        assert parsed.arguments == call['arguments']
        assert parsed.idempotency_key == 'frozen-key'
        assert parsed.expected_version == 'unchanged-token'
    assert call == original
    canonical = {**call, 'schema': 'tfi.hatg-instruction.v2'}
    for changes in ({'schema': f'{prefix}.hatg-instruction.v3'}, {'arguments': []}, {'caller': 'bob'}):
        with pytest.raises(InstructionError):
            InstructionCall.from_payload({**canonical, **changes})


def _signed_wire(prefix, *, params=None):
    """Independent wire encoder: do not share the production signing helper."""
    key = b'compatibility-test-key-only-32-bytes'
    params = {'value': 'frozen'} if params is None else params
    auth = {'schema': f'{prefix}.resource-rpc-auth.v1', 'actor': 'alice', 'issued_at': 1000, 'nonce': 'nonce-1'}
    canonical = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    preimage = {**auth, 'method': 'read', 'params_sha256': hashlib.sha256(canonical(params)).hexdigest()}
    auth['signature'] = hmac.new(key, canonical(preimage), hashlib.sha256).hexdigest()
    return key, params, auth


@pytest.mark.parametrize('prefix', ['archive', 'tfi'])
def test_rpc_uses_signed_schema_and_retains_tamper_expiry_replay_guards(prefix, monkeypatch):
    """RPC拒绝签名正确的退役标签，规范签名仍防篡改、过期和重放"""
    key, params, auth = _signed_wire(prefix)
    original = copy.deepcopy(auth)
    if prefix == 'archive':
        with pytest.raises(PermissionError, match='invalid Portal RPC authentication'):
            rpc_auth.verify_rpc_auth(key, 'read', params, auth, now=1000)
        assert auth == original
        return
    assert rpc_auth.verify_rpc_auth(key, 'read', params, auth, now=1000) == ('alice', 'nonce-1', 1000)
    assert auth == original
    for changes in ({'schema': ('tfi' if prefix == 'archive' else 'archive') + '.resource-rpc-auth.v1'},
                    {'schema': f'{prefix}.resource-rpc-auth.v2'}, {'actor': 'bob'}, {'nonce': 'nonce-2'}, {'issued_at': 999}):
        with pytest.raises(PermissionError):
            rpc_auth.verify_rpc_auth(key, 'read', params, {**auth, **changes}, now=1000)
    for method, altered, now in [('write', params, 1000), ('read', {'value': 'changed'}, 1000), ('read', params, 1031)]:
        with pytest.raises(PermissionError):
            rpc_auth.verify_rpc_auth(key, method, altered, auth, now=now)
    monkeypatch.setattr(rpc_auth.time, 'time', lambda: 1000)
    receiver = rpc_auth.RpcAuthenticator(Path('/unused/test.sock'), key=key)
    assert receiver.authenticate('read', params, auth) == 'alice'
    with pytest.raises(PermissionError, match='replayed'):
        receiver.authenticate('read', params, auth)
    # New requests sign the canonical tag, covered by an independent encoder.
    assert rpc_auth.sign_rpc_auth(key, 'read', params, actor='alice', issued_at=1000, nonce='nonce-1') == _signed_wire('tfi')[2]


@pytest.mark.parametrize('prefix', ['archive', 'tfi'])
@pytest.mark.parametrize('cls,tag,field', [(PortalPasskeyStore, 'portal-passkeys', 'users'),
    (PortalPasswordResetStore, 'portal-password-resets', 'tokens'), (PortalTwoFactorStore, 'portal-two-factor', 'users')])
def test_identity_store_read_is_nonwriting_and_keeps_shape_validation(prefix, cls, tag, field):
    """身份存储读取拒绝退役标签，规范记录仍校验版本和形状且不写回"""
    record = {'schema': f'{prefix}.{tag}.v1', field: {'existing': {'opaque': 'retained'}}}
    original = copy.deepcopy(record)
    store = object.__new__(cls)
    store.state = SimpleNamespace(read=lambda default: record)
    if prefix == 'archive':
        with pytest.raises(RuntimeError):
            store._load()
        assert record == original
        return
    assert store._load() == record and record == original
    record['schema'] = f'{prefix}.{tag}.v2'
    with pytest.raises(RuntimeError):
        store._load()
    record['schema'] = original['schema']
    record[field] = []
    with pytest.raises(RuntimeError):
        store._load()


@pytest.mark.parametrize('prefix', ['archive', 'tfi'])
def test_access_reader_preserves_payload_and_rejects_invalid_fields(prefix):
    """访问记录读取保留载荷并拒绝退役标签、错误版本和未知字段"""
    store = object.__new__(PortalAccessStore)
    record = {'schema': f'{prefix}.portal-access.v1', 'users': {}, 'updated_at': 'fixed-token'}
    original = copy.deepcopy(record)
    if prefix == 'archive':
        with pytest.raises(ValueError):
            store._normalize(record)
        assert record == original
        return
    assert store._normalize(record)['updated_at'] == 'fixed-token' and record == original
    for changes in ({'schema': f'{prefix}.portal-access.v2'}, {'users': []}, {'extra': True}):
        with pytest.raises(ValueError):
            store._normalize({**record, **changes})


@pytest.mark.parametrize('prefix', ['archive', 'tfi'])
def test_resource_change_reader_keeps_execution_actor_and_frozen_text(prefix):
    """资源变更读取保留冻结文本，拒绝退役格式及不匹配的执行者和执行ID"""
    change = {'schema': f'{prefix}.resource-content-change.v1', 'execution_id': 'execution-test', 'actor': 'alice',
              'change_id': 'change_test', 'resource_id': 'resource_test', 'address': 'resource_test',
              'path': 'test.txt', 'created_at': 'fixed',
              'before': {'version': 'v1', 'sha256': 'old-sha', 'text': 'before\n'},
              'after': {'version': 'v2', 'sha256': 'new-sha', 'text': 'after\n'}}
    original = copy.deepcopy(change)
    if prefix == 'archive':
        with pytest.raises(ValueError):
            resource_turn_artifacts.content_part([change], execution_id='execution-test', actor='alice')
        assert change == original
        return
    result = resource_turn_artifacts.content_part([change], execution_id='execution-test', actor='alice')
    assert '-before\n+after\n' in result['diff'] and change == original
    for changes in ({'schema': f'{prefix}.resource-content-change.v2'}, {'actor': 'bob'}, {'execution_id': 'other'}):
        with pytest.raises(ValueError):
            resource_turn_artifacts.content_part([{**change, **changes}], execution_id='execution-test', actor='alice')
