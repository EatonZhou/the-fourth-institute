"""Runtime readers accept canonical formats and reject retired tags without writes."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal.execution.contracts import (
    ExecutionRequest, ExecutionInputControl, ExecutionTurnSettingsControl,
)
from management_portal.execution.context_seed import context_seed
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.society_service.state_store import LINEAGE_DATA_SCHEMA, LineageDataStore
from management_portal.society_service import resident_conversation_records


@pytest.fixture(params=['archive', 'tfi', 'wrong_version'])
def namespace(request):
    return request.param


def tagged(old, namespace):
    if namespace == 'wrong_version':
        return 'tfi.' + old.split('.', 1)[1].rsplit('.v', 1)[0] + '.v999'
    return namespace + '.' + old.split('.', 1)[1]


def checked(namespace, read):
    if namespace != 'tfi':
        with pytest.raises((ValueError, RuntimeError)):
            read()
        return None
    return read()


def execution_request():
    return ExecutionRequest.new(actor='tfi-reader-test', prompt='local fixture',
        service_type='agent_session', provider='codex', operation='turn')


@pytest.mark.parametrize('kind', ['request', 'input', 'settings'])
def test_execution_contract_round_trip(namespace, kind):
    request = execution_request()
    obj = {'request': request,
           'input': ExecutionInputControl.new(execution_id=request.execution_id, actor=request.actor,
                       input=[{'type': 'text', 'text': 'fixture'}]),
           'settings': ExecutionTurnSettingsControl.new(execution_id=request.execution_id, actor=request.actor,
                           settings={'service_tier': 'priority'})}[kind]
    assert obj.schema.startswith('tfi.')
    payload = obj.to_payload()
    payload['schema'] = tagged(obj.schema, namespace)
    original = deepcopy(payload)
    parsed = checked(namespace, lambda: type(obj).from_payload(payload))
    if parsed is not None:
        assert parsed.to_payload() == original
        if kind == 'request':
            with pytest.raises(ValueError):
                ExecutionRequest.from_payload({**payload, 'schema_version': 999})
    assert payload == original


def test_seed_preserves_payload_and_validates_references(namespace):
    seed = {'schema': tagged('archive.provider-context-seed.v1', namespace),
            'segments': [{'segment_id': 'segment_fixture'}], 'prompt': 'frozen', 'portable_prompt': 'portable'}
    request = SimpleNamespace(metadata={'fork_context_input': seed})
    before = deepcopy(seed)
    result = checked(namespace, lambda: context_seed(request))
    if result is not None:
        assert result == before
        request.metadata['fork_context_input'] = {**seed, 'segments': [{}]}
        with pytest.raises(ValueError, match='Segment reference'):
            context_seed(request)
    assert seed == before


def test_artifact_reads_preserve_digest_and_scope(tmp_path, namespace):
    from management_portal.society_service.effect_artifacts import ArtifactStore
    store = ArtifactStore(tmp_path / 'artifacts')
    value = {'schema': 'archive.business.v1', 'fixture': [1, 2]}
    ref = store.put('lineage_fixture', 'activation_fixture', value, purpose='recovery')
    original = deepcopy(ref)
    ref['schema'] = tagged(original['schema'], namespace)
    result = checked(namespace, lambda: store.read('lineage_fixture', 'activation_fixture', ref))
    if result is not None:
        assert result == value
        with pytest.raises(ValueError, match='foreign'):
            store.read('lineage_other', 'activation_fixture', ref)
        with pytest.raises(ValueError, match='integrity'):
            store.read('lineage_fixture', 'activation_fixture', {**ref, 'size_bytes': 0})
    assert {**ref, 'schema': original['schema']} == original


def test_transport_sanitizer_alias_and_privacy(namespace):
    from management_portal.provider_transport_diagnostics import sanitize_transport_observation
    value = {'schema': tagged('archive.provider-transport.v1', namespace),
             'phase': 'opening_response', 'outcome': 'failed', 'prompt': 'private-fixture',
             'request': {'characters': 7, 'authorization': 'private-fixture'}}
    result = sanitize_transport_observation(value)
    if namespace != 'tfi':
        assert result is None
    else:
        assert result['schema'] == 'tfi.provider-transport.v1'
        assert result['request']['characters'] == 7
        assert 'private-fixture' not in json.dumps(result)


@pytest.mark.parametrize('kind', ['packet', 'operator', 'runtime', 'definitions', 'resident'])
def test_society_readers_keep_non_schema_validation(tmp_path, monkeypatch, namespace, kind):
    from management_portal.society_service import (
        lineage_primitives as packets, lineage_operator_runtime as operators,
        lineage_state_domains as states, lineage_primitive_definitions as definitions,
        resident_catalog, resident_storage,
    )
    if kind == 'packet':
        value = {'schema': tagged(packets.PACKET_SCHEMA, namespace), 'message': {'text': 'fixture'}}
        read = lambda: packets.normalize_packet(value).to_dict()
    elif kind == 'operator':
        value = operators.empty_operator_runtime('resident_aaaaaaaaaaaa')
        value['schema'] = tagged(operators.OPERATOR_RUNTIME_SCHEMA, namespace)
        read = lambda: operators._normalize_runtime(value, 'resident_aaaaaaaaaaaa')
    elif kind == 'runtime':
        value = {'schema': tagged(states.RUNTIME_STATE_SCHEMA, namespace),
                 'lineage_id': 'lineage_fixture', 'runtime_revision': 0}
        read = lambda: states.validate_runtime_state(value)
    elif kind == 'definitions':
        value = json.loads(definitions._DEFINITIONS_PATH.read_text())
        value['schema'] = tagged(definitions.SCHEMA, namespace)
        path = tmp_path / 'definitions.json'
        path.write_text(json.dumps(value))
        monkeypatch.setattr(definitions, '_DEFINITIONS_PATH', path)
        read = definitions._load_and_validate
    else:
        rid, lid, wid = 'resident_aaaaaaaaaaaa', 'lineage_bbbbbbbbbbbb', 'workspace_cccccccccccc'
        value = {'schema': tagged(resident_storage.RESIDENT_RECORD_SCHEMA, namespace),
                 'resident_id': rid, 'lineage_id': lid, 'workspace_path': str(tmp_path),
                 'owned_workspace_id': wid, 'owned_workspace': {
                     'workspace_id': wid, 'path': str(tmp_path / 'residents' / rid / 'workspace')}}
        catalog = resident_catalog.ResidentCatalog(tmp_path, LineageDataStore())
        read = lambda: catalog._normalize(value)
    before = deepcopy(value)
    result = checked(namespace, read)
    assert value == before
    if result is not None:
        assert result
        if kind == 'packet':
            value['unexpected'] = True
        elif kind == 'operator':
            value['operator_id'] = 'other_operator'
        elif kind == 'runtime':
            value['runtime_revision'] = -1
        elif kind == 'definitions':
            value['default_types'] = {'unexpected': 'value'}
            path.write_text(json.dumps(value))
        else:
            value['session_id'] = 'forbidden-persisted-session'
        with pytest.raises(ValueError):
            read()


@pytest.mark.parametrize('reader', ['evidence', 'notification'])
def test_conversation_readers(namespace, reader):
    from management_portal.society_service.resident_context.evidence import project_conversation_messages
    value = {'schema': tagged('archive.resident-messages-state.v1', namespace),
             'messages': [], 'input_states': [], 'event_type': 'message.appended',
             'resident_id': 'resident_aaaaaaaaaaaa', 'cursor': 5}
    before = deepcopy(value)
    result = checked(namespace, lambda: (project_conversation_messages(value)
        if reader == 'evidence' else resident_conversation_records.conversation_event_notification(value)))
    if namespace == 'tfi':
        assert result == ([] if reader == 'evidence' else value)
    assert value == before


@pytest.mark.parametrize('reader', ['harness', 'auto_policy'])
def test_experiment_readers_retain_authorization_guards(namespace, reader):
    from management_portal.execution.harness import AgentHarness
    from management_portal.execution.harness_auto_policy import AdaptiveHarnessController
    from tests.cases.test_harness_auto_policy import _invocation
    invocation = _invocation(policy_overrides={'offload_trigger_bytes': 32768})
    invocation.request.metadata['experiment_schema'] = tagged('archive.agent-harness-live-experiment.v1', namespace)
    controller = AdaptiveHarnessController()
    read = (lambda: AgentHarness()._config(invocation)) if reader == 'harness' else (
        lambda: controller.resolve(invocation, {}))
    result = checked(namespace, read)
    if result is not None:
        invocation.request.metadata.pop('experiment_run_id')
        with pytest.raises(ValueError, match='controlled experiment'):
            read()


@pytest.mark.parametrize('factory,table,old', [
    (AgentDataStore, 'agent_store_meta', 'archive.agent-data.v18'),
    # The retired prefix on the current Lineage data version.
    (LineageDataStore, 'lineage_meta', 'archive.' + LINEAGE_DATA_SCHEMA.split('.', 1)[1]),
])
def test_existing_postgres_marker_is_read_only(factory, table, old, namespace):
    """Existing schema, row version and data remain unchanged through startup."""
    store = factory()
    marker = tagged(old, namespace)
    def facts():
        with store.database.read() as connection:
            return [dict(row) for row in connection.execute(
                f'SELECT key,value,xmin::text AS row_version FROM {table} ORDER BY key').fetchall()]
    original = facts()
    original_marker = next(row['value'] for row in original if row['key'] == 'schema')
    try:
        with store.database.transaction() as connection:
            connection.execute(f"UPDATE {table} SET value=%s WHERE key='schema'", (marker,))
        before = facts()
        checked(namespace, factory)
        assert facts() == before
    finally:
        # Test-only namespace; never leave the synthetic tag for other fixtures.
        with store.database.transaction() as connection:
            connection.execute(f"UPDATE {table} SET value=%s WHERE key='schema'", (original_marker,))
        assert [{k: v for k, v in row.items() if k != 'row_version'} for row in facts()] == [
            {k: v for k, v in row.items() if k != 'row_version'} for row in original]
        factory()


@pytest.mark.parametrize('kind', ['input', 'settings'])
def test_turn_pump_control_receipt_routes_to_original_owner(namespace, kind):
    from management_portal.execution.turn_input_pump import TurnInputPump
    tag = f'archive.execution-{ "input" if kind == "input" else "turn-settings" }-control.v1'
    control = {'schema': tagged(tag, namespace), 'action': 'steer',
               'control_id': 'control-fixture', 'input': [{'type': 'text', 'text': 'fixture'}]}
    context, processor, deliver = Mock(), Mock(), Mock(return_value=(True, ''))
    context.next_turn_control.return_value = None
    processor.next_control.return_value = control
    pump = TurnInputPump(context=context, event_processor=processor, deliver=deliver)
    pump.turn_started('turn-fixture')
    assert pump.pump_once()
    if namespace != 'tfi':
        context.record_turn_control_result.assert_not_called()
        processor.record_control_result.assert_called_once()
    else:
        expected = (False, 'turn_settings_unsupported') if kind == 'settings' else (True, '')
        context.record_turn_control_result.assert_called_once_with(control, *expected)
        processor.record_control_result.assert_not_called()
        if kind == 'settings':
            deliver.assert_not_called()


@pytest.mark.parametrize('kind', ['input', 'settings'])
def test_codex_adapter_control_receipts_use_execution_owner(namespace, kind):
    from management_portal.execution.backend_contracts import BackendInvocation, BackendContext
    from management_portal.execution.codex_backend import CodexBackend
    from management_portal.execution.contracts import SessionOptions
    from management_portal.execution.session_manager import RuntimeSession
    tag = f'archive.execution-{ "input" if kind == "input" else "turn-settings" }-control.v1'
    control = {'schema': tagged(tag, namespace), 'control_id': 'control-fixture'}
    receipts = []
    client = Mock()
    def run_turn(*args, **kwargs):
        item = kwargs['turn_control_provider']()
        kwargs['turn_control_result_callback'](item, True, '')
        return SimpleNamespace(thread_id='019f0000-0000-7000-8000-000000000472',
                               turn_id='turn-fixture', response='done', token_usage={})
    client.run_turn.side_effect = run_turn
    request = replace(execution_request(), cwd='/tmp', session=SessionOptions(
        mode='persistent', binding_id='provider:fixture', backend_route_id='one'))
    CodexBackend(client_factory=lambda _: client).execute(BackendInvocation(
        request=request, session=RuntimeSession(mode='persistent', binding_id='',
            backend_session_id='019f0000-0000-7000-8000-000000000472',
            backend_route_id='one', is_new_backend_session=False),
        user_prompt=request.prompt, system_context='', attach_system_context=False,
        is_new_backend_session=False), BackendContext(
            emit_event=lambda *args: None, cancellation_requested=lambda: False,
            set_interrupt_target=lambda *args: None, turn_control_provider=lambda: control,
            turn_control_result_callback=lambda *args: receipts.append(args)))
    assert receipts == ([(control, True, '')] if namespace == 'tfi' else [])


def test_scheduler_settings_receipt_discriminator(namespace, tmp_path):
    from management_portal.execution.scheduler import Scheduler
    from management_portal.execution.contracts import SessionOptions
    from management_portal.agent_prompt_context import portal_system_instructions
    from management_portal.agent_skill_context import PORTAL_SYSTEM_SKILL, PORTAL_ROLE_FILES
    skill = tmp_path / PORTAL_SYSTEM_SKILL
    skill.mkdir()
    for name in ('SKILL.md', PORTAL_ROLE_FILES['resident']):
        (skill / name).write_text('Fixture rules for the settings receipt contract.')
    scheduler = Scheduler(dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0)
    capabilities = ['agent_session:codex']
    scheduler.register_worker(worker_id='dev:worker:tfi-fixture', build_id='fixture', capabilities=capabilities)
    request = replace(execution_request(), session=SessionOptions(mode='stateless', backend_route_id='zero'),
                      system_instructions=portal_system_instructions('resident', skill_root=str(tmp_path)))
    scheduler.submit(request)
    claim = scheduler.claim(worker_id='dev:worker:tfi-fixture', capabilities=capabilities)
    owner = {'worker_id': 'dev:worker:tfi-fixture', 'claim_token': claim['claim_token']}
    scheduler.start(request.execution_id, **owner)
    control = ExecutionTurnSettingsControl.new(execution_id=request.execution_id, actor=request.actor,
                                              settings={'service_tier': 'priority'})
    control = replace(control, schema=tagged(control.schema, namespace))
    if namespace != 'tfi':
        with pytest.raises(ValueError):
            scheduler.submit_turn_settings_control(control)
        return
    receipt = scheduler.submit_turn_settings_control(control)
    assert receipt['status'] == 'pending'
    scheduler.complete_input_control(control.control_id, execution_id=request.execution_id,
                                     delivered=True, **owner)
    events = scheduler.snapshot(request.execution_id)['events']
    assert any(event['type'] == 'turn_settings_control_delivered' for event in events)
    assert not any(event['type'] == 'input_control_delivered' for event in events)


def test_service_invocation_projection_tag_read_without_fingerprint_or_row_changes(namespace, monkeypatch):
    from management_portal import service_invocation_contract as contract
    from management_portal.society_service import service_invocation_storage as storage
    from management_portal.society_service.service_invocation_store import ServiceInvocationStore
    store = ServiceInvocationStore()
    job = {'created_by': 'tfi-reader-test', 'surface': 'knowledge',
           'search_id': 'fixture-search', 'run_generation': 1}
    batches = contract.batch_manifest(job, ['local fixture'])
    fingerprint = contract.plan_fingerprint(job, batches)
    record = {**job, 'schema': contract.PLAN_SCHEMA, 'status': 'prepared',
              'plan_fingerprint': fingerprint, 'batches': [{**batches[0], 'status': 'prepared'}]}
    store.reserve(job, 'knowledge', record)
    def facts():
        with store.database.snapshot() as connection:
            return {table: [dict(row) for row in connection.execute(
                f'SELECT to_jsonb(t) AS data, xmin::text AS row_version FROM {table} t').fetchall()]
                    for table in ('service_invocations', 'service_search_heads', 'service_run_fences')}
    before = facts()
    # Schema is currently a decoder projection, not a persisted DB column.
    # Exercise that real decoder at both namespace boundaries; do not alter
    # the writer/fingerprint contract or invent a legacy stored schema row.
    marker = tagged(contract.PLAN_SCHEMA, namespace)
    monkeypatch.setattr(storage, 'PLAN_SCHEMA', marker)
    monkeypatch.setattr(storage, 'write', Mock(side_effect=AssertionError('reader wrote plan')))
    if namespace != 'tfi':
        with pytest.raises(contract.ServiceInvocationConflict, match='contract'):
            store.read_plan(job, 'knowledge')
    else:
        result = store.read_plan(job, 'knowledge')
        assert result['schema'] == marker
        assert result['plan_fingerprint'] == fingerprint
        assert result['batches'][0]['plan_fingerprint'] == fingerprint
        assert contract.PLAN_SCHEMA == 'tfi.service-invocation-plan.v1'
        assert contract.plan_fingerprint(job, batches) == fingerprint
    assert facts() == before


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ
