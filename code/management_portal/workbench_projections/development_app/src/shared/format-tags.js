/** Never normalizes input documents, descriptor bindings or persisted digests. */
export function schemaMatches(actual, expected) {
  if (typeof expected !== 'string' || expected.trim() !== expected || !/^tfi\.[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)*\.v[1-9][0-9]*$/.test(expected)) {
    throw new TypeError('expected must be a complete tfi format tag');
  }
  return typeof actual === 'string' && actual === expected;
}
