import { createExecutionWorkbench } from './execution-workbench.js';
import { webWorkbenchFactories, WEB_OBJECT_ENTRIES } from './web-mechanisms.js';
import { projectionWorkbenchFactories } from './projection-mechanisms.js';
import { createNativeWorkbench } from './native-workbench.js';
import { NATIVE_ADAPTER, BUDGET_ADAPTER, NATIVE_OPERATIONS, NATIVE_OBJECT_ENTRIES } from './native-workbench.model.js';
import { executionControlFactories } from './execution-control-mechanisms.js';
import { executionControlObjectDefaults, executionControlObjectFactories } from './execution-control-mechanisms.entry.js';
import { schedulingMechanismFactories, schedulingObjectFactories, schedulingObjectDefaults } from './scheduling-mechanisms.js';
import { infrastructureRendererRegistry, infrastructureObjectEntries } from './infrastructure-mechanisms.js';
import { createAgentDataWorkbench } from './agent-data-mechanisms.js';
import { AGENT_DATA_ADAPTERS } from './agent-data-mechanisms.model.js';
import { createProviderMechanisms } from './provider-mechanisms.js';
import { PROVIDER_ADAPTERS, PROVIDER_OBJECT_ENTRIES } from './provider-mechanisms.model.js';
import { createProviderPreflight } from './provider-preflight.js';
import { PREFLIGHT_ID, PREFLIGHT_OBJECT_ENTRIES } from './provider-preflight.model.js';
import { catalogMechanismFactories, catalogObjectEntries, catalogObjectModes } from './provider-catalog.js';
import { providerSessionMechanismFactories, getProviderSessionWorkbenchEntry } from './provider-session-mechanisms.js';
import { lineageMechanismFactories, lineageReadyObjectEntries } from './lineage-mechanisms.js';
import { HARNESS_FACTORIES, HARNESS_OBJECT_ENTRIES } from './harness-mechanisms.js';
import { harnessOffloadingFactories, harnessPipelineObjectFactories, harnessPipelineObjectModes } from './harness-offloading.js';
import { resourceMechanismFactories, resourceObjectEntries, resourceObjectModes } from './resource-mechanisms.js';

const providerSessionLeaves = ['execution.providers.catalog',
  'execution.providers.sessions.resolve', 'execution.providers.sessions.bindings',
  'execution.providers.adapters'];

// Explicit domain implementations. Registration is not a claim of reader
// acceptance; a shared name never substitutes for distinct mechanism content.
export const mechanismFactories = Object.freeze({
  'execution.lifecycle.v1': (root, options) => createExecutionWorkbench(root, {
    ...options, ports: { ...options.ports, select: id => options.ports.select({ objectId: id }) },
  }),
  ...webWorkbenchFactories,
  ...projectionWorkbenchFactories,
  ...Object.fromEntries([NATIVE_ADAPTER, BUDGET_ADAPTER, ...Object.keys(NATIVE_OPERATIONS)]
    .map(adapter => [adapter, createNativeWorkbench])),
  ...infrastructureRendererRegistry,
  ...HARNESS_FACTORIES,
  ...harnessOffloadingFactories,
  ...Object.fromEntries(AGENT_DATA_ADAPTERS.map(adapter => [adapter, createAgentDataWorkbench])),
  ...Object.fromEntries(Object.keys(PROVIDER_ADAPTERS).map(adapter => [adapter, createProviderMechanisms])),
});
export const mechanismIdFactories = Object.freeze({
  [PREFLIGHT_ID]: createProviderPreflight,
  ...executionControlFactories, ...schedulingMechanismFactories,
  ...providerSessionMechanismFactories,
  ...catalogMechanismFactories,
  ...lineageMechanismFactories, ...resourceMechanismFactories,
});
// Authored defaults do not replace a restored workbench's active mode.
export const objectEntries = Object.freeze({
  ...schedulingObjectDefaults,
  ...executionControlObjectDefaults,
  ...Object.fromEntries(Object.entries(WEB_OBJECT_ENTRIES).map(([id, entry]) => [id, entry.defaultMechanismId])),
  ...Object.fromEntries(Object.entries(infrastructureObjectEntries).map(([id, entry]) => [id, entry.defaultMechanismId])),
  ...Object.fromEntries(providerSessionLeaves.map(id => [id, getProviderSessionWorkbenchEntry(id).defaultMechanismId])),
  ...Object.fromEntries(Object.entries({ ...PROVIDER_OBJECT_ENTRIES, ...NATIVE_OBJECT_ENTRIES })
    .filter(([, entry]) => entry.defaultMechanismId).map(([id, entry]) => [id, entry.defaultMechanismId])),
  ...Object.fromEntries(Object.entries(PREFLIGHT_OBJECT_ENTRIES)
    .filter(([, entry]) => entry.defaultMechanismId).map(([id, entry]) => [id,
      Object.freeze([...new Set([entry.defaultMechanismId, PROVIDER_OBJECT_ENTRIES[id]?.defaultMechanismId].filter(Boolean))])])),
  // Both defaults are authored: newer batches start at runtime catalog;
  // older fixed batches retain the previously delivered settings view.
  ...Object.fromEntries(Object.entries(catalogObjectEntries).map(([id, chosen]) =>
    [id, Object.freeze([chosen, getProviderSessionWorkbenchEntry(id).defaultMechanismId])])),
  ...Object.fromEntries(Object.entries(HARNESS_OBJECT_ENTRIES)
    .filter(([, entry]) => entry.defaultMechanismId).map(([id, entry]) => [id, entry.defaultMechanismId])),
  ...lineageReadyObjectEntries, ...resourceObjectEntries,
  'kernel.isa.directory': 'web.m.isa-discovery-routing',
  'execution.agent-data.records': 'agent-data.segment-identity',
  'execution.agent-data.traces': 'agent-data.trace-revision',
  'execution.agent-data.executions': 'agent-data.execution-receipt',
  'execution.agent-data.views': 'agent-data.read-views',
  'execution.agent-data.projector.inputs': 'agent-data.actual-input',
  'execution.agent-data.projector.events': 'agent-data.acceptance-projection',
  'execution.agent-data.projector.outputs': 'agent-data.completed-content',
  'projection-family': 'web.m.focus-revision',
  'interface.workbenches.panel-runtime': 'web.m.panel-lifecycle',
  'interface.workbenches.layout-runtime': 'web.m.panel-lifecycle',
  'interface.workbenches.projection-host.sdk': 'web.m.focus-revision',
  'web.projections.bound-dispatch': 'web.m.projection-scope',
  'web.projections.semantic-validation': 'web.m.semantic-validation',
  'web.projections.generation-save': 'web.m.generation-save',
  'web.projections.ui-association': 'web.m.ui-association',
});
export const objectModes = Object.freeze({
  ...catalogObjectModes, ...harnessPipelineObjectModes,
  ...resourceObjectModes,
  'projection-family': Object.freeze([
    'web.m.focus-revision', 'web.m.feedback-confirmation',
    'projection.mechanism.assembly', 'projection.mechanism.opaque-bridge',
    'projection.mechanism.host-focus-order', 'projection.mechanism.region-detail-order',
    'projection.mechanism.feedback-budget',
  ]),
});
export function mechanismFactory(mechanism, { byId = mechanismIdFactories, byAdapter = mechanismFactories } = {}) {
  return byId[mechanism.id] || byAdapter[mechanism.adapter] || null;
}

// Object workbenches own their internal modes. Explicit routes into another
// author's mechanism (for example the full lifecycle) still use that factory.
export function objectWorkbenchFactory(object, mechanism) {
  return harnessPipelineObjectModes[object.id]?.includes(mechanism.id)
      && harnessPipelineObjectModes[object.id].every(id => object.mechanism_ids.includes(id))
      && harnessPipelineObjectFactories[object.id]
    || schedulingMechanismFactories[mechanism.id] && schedulingObjectFactories[object.id]
    || executionControlObjectFactories[object.id]
      && (executionControlFactories[mechanism.id] || mechanism.id === 'execution.lifecycle')
      && executionControlObjectFactories[object.id]
    || null;
}
