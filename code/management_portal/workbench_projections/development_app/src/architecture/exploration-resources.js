import { resourceAddress, requireReadable } from '../shared/resources.js';

// Architecture is authored documentation of the workspace. Historical origin
// metadata is not a prerequisite for reading it or opening today's source.
export function workspaceSource(ref) {
  return String(ref).replace(/^(repo_[a-f0-9]{12})\/commit\/[^/]+\/file\//, '$1/file/');
}

export function createArchitectureReader(resources, signal) {
  const pending = new Map(), refs = new Set();
  const check = () => { if (signal?.aborted) throw new DOMException('已关闭', 'AbortError'); };
  const addressOf = ref => resourceAddress(typeof ref === 'string' ? ref : ref?.address || '');
  async function access(ref) {
    check(); const address = addressOf(ref);
    requireReadable(await resources.resolve(address), address); check(); refs.add(address);
  }
  async function text(ref) {
    check(); const address = addressOf(ref);
    if (!address) throw new Error('架构说明缺少来源地址');
    if (!pending.has(address)) pending.set(address, (async () => {
      const result = requireReadable(await resources.read(address, 'text'), address); check();
      if (typeof result.data !== 'string') throw new Error('来源内容不是文本：' + address);
      refs.add(address); return result.data;
    })());
    return pending.get(address);
  }
  async function json(ref) {
    const value = await text(ref);
    try { return JSON.parse(value); }
    catch { throw new Error('架构说明不是有效的 JSON：' + addressOf(ref)); }
  }
  return { access, text, json, refs: () => [...refs] };
}
