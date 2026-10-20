import {createWebSimulation} from './web-mechanisms.model.js';

// Authored entries for exact model object identities, not an adapter/prefix guess.
const entry = (defaultMechanismId, alternatives = []) => Object.freeze({
  defaultMechanismId, mechanismIds: Object.freeze([defaultMechanismId, ...alternatives]),
  factory: 'createWebWorkbench',
});
export const WEB_OBJECT_ENTRIES = Object.freeze({
  'interface': entry('web.m.refresh-generation'),
  'router': entry('web.m.router-connection'),
  'web': entry('web.m.web-request'),
  'kernel.isa': entry('web.m.isa-commit-replay'),
  'web.identity': entry('web.m.identity-session'),
  'web.community': entry('web.m.chatroom-revision'),
  'web.workflows': entry('web.m.business-task'),
  'interface.shell': entry('web.m.instruction-retry'),
  'interface.conversation': entry('web.m.resident-cursors'),
  'interface.content': entry('web.m.document-edit'),
  'interface.home': entry('web.m.home-activation'),
  'delivery.backup': entry('web.m.backup-receipt'),
  'http-interface': entry('web.m.http-stream'),
  'interface.shell.api-client': entry('web.m.http-stream'),
  'interface.shell.instruction-client': entry('web.m.instruction-retry'),
  'interface.shell.refresh-coordinator': entry('web.m.refresh-generation'),
  'interface.conversation.resident-store': entry('web.m.resident-cursors'),
  'interface.content.navigation': entry('web.m.document-navigation', ['web.m.document-edit']),
  'interface.content.document-session': entry('web.m.document-edit'),
  'interface.home.component-runner': entry('web.m.home-activation'),
  'interface.home.panel-visibility': entry('web.m.home-activation'),
  'router.deployment-selection': entry('web.m.router-connection'),
  'router.stream-forwarder': entry('web.m.router-connection'),
  'web.asgi-dispatch': entry('web.m.web-request'),
  'kernel.isa.owner-router': entry('web.m.isa-commit-replay'),
  'kernel.isa.operation-ledger': entry('web.m.isa-commit-replay'),
  'web.identity.session-signature': entry('web.m.identity-session'),
  'web.identity.login-challenge': entry('web.m.identity-session'),
  'web.community.chatroom-log': entry('web.m.chatroom-revision'),
  'web.community.resource-discussion': entry('web.m.resource-discussion'),
  'web.community.pulse-publications': entry('web.m.pulse-publication'),
  'web.workflows.business-task': entry('web.m.business-task'),
  'web.workflows.paper-jobs': entry('web.m.paper-recovery'),
  'web.workflows.wall-actions': entry('web.m.wall-action'),
  'web.workflows.archive-client': entry('web.m.archive-errors'),
  'delivery.backup.lineage-binding': entry('web.m.backup-receipt'),
  'delivery.backup.status-projection': entry('web.m.backup-receipt'),
});

export function getWebObjectModes(objectId, content) {
  const declared = Object.hasOwn(WEB_OBJECT_ENTRIES, objectId) ? WEB_OBJECT_ENTRIES[objectId] : null;
  const object = content?.objects?.find(o => o.id === objectId);
  if (!declared || !object || !content.batchKey) return [];
  const mechanisms = new Map((content.mechanisms || []).map(m => [m.id, m]));
  if (content.mechanism) mechanisms.set(content.mechanism.id, content.mechanism);
  return declared.mechanismIds.flatMap(mechanismId => {
    const mechanism = mechanisms.get(mechanismId);
    if (!object.mechanism_ids?.includes(mechanismId) || !mechanism?.title) return [];
    try {
      // Parent authorizes the batch. Validate every displayed mode's references too.
      createWebSimulation({...content, mechanism, mechanisms: undefined});
    } catch { return []; }
    return [{mechanismId, title: mechanism.title, adapter: mechanism.adapter,
      route: {kind: object.kind === 'protocol' ? 'protocol' : 'mechanism', objectId, mechanismId}}];
  });
}

export function resolveWebObjectEntry(objectId, content, viewState) {
  const declared = Object.hasOwn(WEB_OBJECT_ENTRIES, objectId) ? WEB_OBJECT_ENTRIES[objectId] : null;
  const object = content?.objects?.find(o => o.id === objectId);
  if (!declared || !object || !content.batchKey) return null;
  const modes = getWebObjectModes(objectId, content);
  if (object.kind !== 'protocol' && object.child_ids?.length) {
    return {...declared, modes, adapter: null,
      route: {kind: 'composition', objectId, mechanismId: null}};
  }
  let chosen = modes.find(m => m.mechanismId === declared.defaultMechanismId), initialState = null;
  if (viewState?.objectId === objectId && viewState.batchKey === content.batchKey) {
    const remembered = modes.find(m => m.mechanismId === viewState.mechanismId);
    const mechanism = content.mechanism?.id === viewState.mechanismId ? content.mechanism
      : content.mechanisms?.find(m => m.id === viewState.mechanismId);
    if (remembered && mechanism) {
      try {
        createWebSimulation({...content, mechanism, mechanisms: undefined}).restore(viewState);
        chosen = remembered; initialState = structuredClone(viewState);
      } catch { /* Invalid local state does not replace the authored default. */ }
    }
  }
  if (!chosen) return null; // Never promote an available alternative into the default.
  return {...declared, modes, adapter: chosen.adapter, route: chosen.route, initialState};
}
