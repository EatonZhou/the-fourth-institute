import { batchIdentity, batchRefs } from './discovery.js';
import { requireReadable } from '../shared/resources.js';

/** Select a complete authorized renderer candidate. This does not mount UI. */
export function createArchitectureCandidateLoader({ evidenceLoader, explorationLoader }) {
  async function prepare(evidence) {
    const exploration = await explorationLoader.loadDiscovered(evidence);
    return { kind: exploration.status === 'available' ? 'exploration' : 'legacy', evidence, exploration,
      key: JSON.stringify([exploration.batchKey || batchIdentity(evidence), 'current']) };
  }
  async function discover() { return prepare(await evidenceLoader.manifest()); }
  async function revalidate(candidate) {
    if (candidate.kind === 'exploration') return explorationLoader.revalidate(candidate.exploration);
    for (const ref of batchRefs(candidate.evidence)) {
      requireReadable(await evidenceLoader.access(ref), ref);
    }
  }
  return { prepare, discover, revalidate };
}
