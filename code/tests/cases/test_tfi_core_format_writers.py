"""New core envelopes and empty stores use TFI; historical inputs remain intact."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from management_common import principal
from management_portal.hatg.contracts import InstructionCall, InstructionResult
from management_portal import service_invocation_contract as contract
from management_portal.portal_passkeys import PortalPasskeyStore
from management_portal.portal_password_reset import PortalPasswordResetStore
from management_portal.portal_two_factor import PortalTwoFactorStore


def test_new_envelopes_keep_actor_and_idempotency_fields():
    origin = principal.message_origin('alice', initiated_by='bob')
    assert origin['schema'] == 'tfi.message-origin.v1'
    assert origin['sender']['id'] == 'alice' and origin['initiated_by']['id'] == 'bob'
    grouped = principal.mailbox_origin('alice')
    assert grouped['schema'] == 'tfi.message-origin.v2'
    assert grouped['sender'] == {'kind': 'mailbox', 'id': 'mailbox'}
    assert principal.OperationIdentity.for_actor('alice').to_dict()['schema'] == 'tfi.operation-identity.v1'
    payload = {'schema': 'tfi.hatg-instruction.v2', 'instruction': 'GetResident',
               'arguments': {'resident_id': 'resident_111111111111'}, 'idempotency_key': 'frozen',
               'expected_version': 'opaque-version'}
    original = deepcopy(payload)
    new = InstructionCall.from_payload(payload).to_payload()
    assert new == {**payload, 'schema': 'tfi.hatg-instruction.v2'} and payload == original
    with pytest.raises(Exception, match='不支持的调用格式'):
        InstructionCall.from_payload({**payload, 'schema': 'archive.hatg-instruction.v2'})
    result = InstructionResult('inst_test', 'GetResident', 'committed', {'value': 'unchanged'}, replayed=True).to_payload()
    assert result['schema'] == 'tfi.hatg-instruction-result.v2'
    assert result['replayed'] is True and result['result'] == {'value': 'unchanged'}


@pytest.mark.parametrize('cls,tag,field', [
    (PortalPasskeyStore, 'portal-passkeys', 'users'),
    (PortalPasswordResetStore, 'portal-password-resets', 'tokens'),
    (PortalTwoFactorStore, 'portal-two-factor', 'users'),
])
def test_empty_identity_store_uses_new_tag_and_existing_store_retains_bytes(cls, tag, field):
    store = object.__new__(cls)
    store.state = SimpleNamespace(read=lambda default: default)
    assert store._load() == {'schema': f'tfi.{tag}.v1', field: {}}
    record = {'schema': f'tfi.{tag}.v1', field: {'existing': {'opaque': 'retain'}}}
    before = json.dumps(record, sort_keys=True)
    store.state = SimpleNamespace(read=lambda default: record)
    assert store._load() == record
    assert json.dumps(record, sort_keys=True) == before
    record['schema'] = f'archive.{tag}.v1'
    with pytest.raises(RuntimeError):
        store._load()


def test_default_plan_digest_uses_new_tag_and_accepts_frozen_old_preimage():
    identity = {'created_by': 'alice', 'surface': 'knowledge', 'search_id': 'search_test', 'run_generation': 1}
    batches = contract.batch_manifest(identity, ['one', 'two'])
    def wire(tag):
        return hashlib.sha256(json.dumps({'schema': tag, **identity, 'batches': batches},
            ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    assert contract.plan_fingerprint(identity, batches) == wire('tfi.service-invocation-plan.v1')
    assert not contract.plan_fingerprint_matches(identity, batches, wire('archive.service-invocation-plan.v1'))
    assert not contract.plan_fingerprint_matches(identity, contract.batch_manifest(identity, ['changed', 'two']),
                                                 wire('archive.service-invocation-plan.v1'))


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ
