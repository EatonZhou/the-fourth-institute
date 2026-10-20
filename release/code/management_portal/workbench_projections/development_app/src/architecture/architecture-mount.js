import { createResources } from '../shared/resources.js';
import { createEvidenceLoader } from './data.js';
import { createExplorationLoader } from './exploration-data.js';
import { createArchitectureCandidateLoader } from './exploration-entry.js';

/** The default entry chooses from discovery; development delivery is separate. */
export async function mountDiscoveredArchitecture(root, context, { mountLegacy, mountExploration, candidateLoader = null }) {
  const resources = context.resources || createResources(context.sdk, { signal: context.signal });
  const evidenceLoader = createEvidenceLoader({ ...context, resources });
  const explorationLoader = createExplorationLoader({ resources, signal: context.signal });
  const candidates = candidateLoader || createArchitectureCandidateLoader({ evidenceLoader, explorationLoader });
  let active = null, generation = 0, disposed = false, hidden = false, latestFocus = null;
  const records = new Set();
  const status = root.ownerDocument.createElement('p'); status.setAttribute('role', 'status');
  function notify(text) {
    if (active?.api.notify) active.api.notify(text);
    else { status.textContent = text; if (!status.isConnected) root.prepend(status); }
  }
  function destroyRecord(record) {
    if (!record) return; record.visible = false; record.controller.abort(); record.api?.destroy();
    for (const clean of record.cleanup) clean(); records.delete(record);
  }
  async function retain(error) {
    if (!active || disposed) return;
    try {
      await candidates.revalidate(active.candidate);
      if (active.candidate.kind === 'exploration') await active.api.session.revalidate();
      notify('新事实批次未通过核验，保留先前仍可读的固定批次：' + error.message);
    } catch { active.visible = false; active.api.hide(); notify('原批次来源也已不可读，视图已清空。'); }
  }
  function childContext(record) {
    return { ...context, resources, signal: record.controller.signal,
      onDispose(fn) { record.cleanup.push(fn); },
      onFocus(fn) { record.receive = fn; },
      watch: context.watch ? (refs, fn) => context.watch(refs, (...args) => record.visible && !hidden && !disposed ? fn(...args) : undefined) : undefined,
      architectureEntry: {
        refresh,
        async acceptEvidence(evidence) {
          if (disposed || record !== active) return false;
          // Declared exploration is validated in full before a legacy reader
          // can adopt its base. Invalid available never falls back to legacy.
          let token = generation;
          try {
            const candidate = await candidates.prepare(evidence);
            if (token !== generation || disposed) return true;
            if (candidate.kind !== 'exploration') { record.candidate = candidate; return false; }
            token = ++generation; await install(candidate, token); return true;
          } catch (error) { if (token === generation) await retain(error); throw error; }
        },
      },
    };
  }
  async function install(candidate, token) {
    if (token !== generation || disposed || hidden) return;
    const previous = active;
    // Root-level notifications may describe unrelated writes. An unchanged
    // verified candidate must not reinstall the exploration or its reading UI.
    if (previous?.candidate.key === candidate.key && previous.visible && previous.api.session?.snapshot().status !== 'error') {
      await candidates.revalidate(previous.candidate);
      if (previous.api.session) await previous.api.session.revalidate();
      return;
    }
    if (candidate.kind === 'exploration' && active?.candidate.kind === 'exploration') {
      const record = active;
      await record.api.acceptCandidate(candidate);
      if (token !== generation || disposed || hidden) return;
      if (previous !== record) { previous?.api.hide(); if (previous) previous.visible = false; }
      record.candidate = candidate; active = record; record.visible = true;
      root.replaceChildren(record.element); await record.api.show();
      if (previous && previous !== record) destroyRecord(previous);
      if (previous !== record && latestFocus) record.receive?.(...latestFocus);
      return;
    }
    const element = root.ownerDocument.createElement('div'); element.style.cssText = 'height:100%;min-height:0;min-width:0';
    const record = { candidate, element, controller: new AbortController(), cleanup: [], visible: false, receive: null, api: null };
    records.add(record);
    try {
      // Mount in a private container: neither late old callbacks nor failed
      // candidates can erase the other renderer's DOM.
      record.api = candidate.kind === 'exploration'
        ? await mountExploration(element, childContext(record), { candidate })
        : await mountLegacy(element, childContext(record), { evidence: candidate.evidence });
      if (token !== generation || disposed || hidden) { destroyRecord(record); return; }
      if (record.api.session?.snapshot().status === 'error' || record.api.getReadingState?.().ready === false) throw new Error('候选视图未能装载');
      if (previous) { previous.api.hide(); previous.visible = false; }
      active = record; record.visible = true; root.replaceChildren(element); await record.api.show();
      if (token !== generation || disposed || hidden) return;
      if (previous) destroyRecord(previous);
      if (candidate.exploration.status === 'missing') notify('此固定批次的深入探索仍待完成；当前仅显示已登记的基础架构。');
      if (latestFocus) record.receive?.(...latestFocus);
    } catch (error) {
      destroyRecord(record);
      if (token === generation && !disposed) {
        active = previous;
        if (previous) {
          root.replaceChildren(previous.element);
          try { await candidates.revalidate(previous.candidate); previous.visible = !hidden; if (!hidden) await previous.api.show(); }
          catch { previous.api.hide(); previous.visible = false; }
        }
      }
      throw error;
    }
  }
  async function refresh() {
    const token = ++generation;
    if (!active) notify('正在读取并核验架构事实…');
    try { const candidate = await candidates.discover(); if (token === generation) await install(candidate, token); }
    catch (error) { if (token === generation) { if (active) await retain(error); else notify('架构事实暂不可用：' + error.message); } }
  }
  context.onFocus?.((...args) => { latestFocus = args; if (!disposed) active?.receive?.(...args); });
  const api = {
    getReadingState: () => active?.api.session?.snapshot() || active?.api.getReadingState?.() || { status: 'empty' },
    async refresh() { if (!hidden && !disposed) await refresh(); },
    hide() { hidden = true; generation++; if (active) { active.visible = false; active.api.hide(); } },
    async show() {
      if (disposed) return; hidden = false;
      if (!active) { await refresh(); return; }
      active.visible = true; await active.api.show();
    },
    destroy() { if (disposed) return; disposed = true; generation++; for (const record of [...records]) destroyRecord(record); root.replaceChildren(); },
  };
  context.onDispose?.(api.destroy); context.signal?.addEventListener('abort', api.destroy, { once: true });
  await refresh(); return api;
}
