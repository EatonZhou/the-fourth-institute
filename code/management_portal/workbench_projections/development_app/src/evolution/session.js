import { loadFacts, baselineKey } from './data.js';
import { initialScene, restoreScenes, reduceScene } from './model.js';

export class EvolutionSession {
  constructor(resources, changed = () => {}) {
    Object.assign(this, { resources, changed, phase: 'loading', facts: null, scenes: {}, drafts: {}, active: true, generation: 0, requiresReload: false });
  }
  get writable() { return this.active && this.phase === 'ready'; }
  purge() { this.facts = null; this.scenes = {}; this.drafts = {}; this.changed({ purge: true }); }
  async refresh(explicit = false, saved) {
    if (!this.active) return false;
    const generation = ++this.generation;
    this.phase = 'checking'; this.changed();
    try {
      const next = await loadFacts(this.resources);
      if (generation !== this.generation || !this.active) return false;
      const newDenied = next.deniedRefs?.some(id => !this.facts?.deniedRefs.includes(id));
      if (next.validity === 'revoked' || (this.facts && newDenied)) {
        this.requiresReload = true; this.phase = 'revoked'; this.purge(); this.message = '来源已不可读取。旧内容、演示和反馈草稿已清除。'; this.changed(); return false;
      }
      const changed = this.facts && baselineKey(next) !== baselineKey(this.facts);
      if (!explicit && (changed || this.requiresReload)) {
        this.requiresReload = true; this.phase = 'frozen'; this.message = '来源已有新版本。当前演示只读；重新读取后从新版本开始。'; this.changed(); return false;
      }
      const same = baselineKey(this.facts) === baselineKey(next);
      this.facts = next;
      if (!same || explicit) {
        this.scenes = restoreScenes(next, saved); this.drafts = saved?.baseline === baselineKey(next) ? saved.drafts || {} : {};
      }
      this.requiresReload = false; this.phase = 'ready'; this.message = ''; this.changed(); return true;
    } catch {
      if (generation !== this.generation || !this.active) return false;
      this.requiresReload = true; this.phase = 'frozen'; this.purge(); this.message = '来源暂不可读取。内容已清除，重新读取后继续。'; this.changed(); return false;
    }
  }
  change(id, action) {
    if (!this.writable) return false;
    const d = this.facts.directions.find(d => d.id === id); if (!d) return false;
    this.scenes[id] = reduceScene(d, this.scenes[id] || initialScene(d), action); return true;
  }
  save(selected) { return { schema: 'evolution-gallery.v1', baseline: baselineKey(this.facts), scenes: this.scenes, drafts: this.drafts, selected }; }
  hide() { this.active = false; this.generation++; this.phase = 'hidden'; this.changed(); }
  destroy() { this.active = false; this.generation++; this.purge(); }
}
