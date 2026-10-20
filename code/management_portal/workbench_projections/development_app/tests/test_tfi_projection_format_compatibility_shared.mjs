import test from 'node:test';
import assert from 'node:assert/strict';
import * as formats from '../src/shared/format-tags.js';

test('only complete canonical tags with exact versions are accepted, without rewriting inputs', () => {
  const expected = 'tfi.portal-architecture.v1';
  assert.equal(formats.schemaMatches(expected, expected), true);
  for (const actual of ['archive.portal-architecture.v1', 'tfi.portal-architecture.v2',
    'tfi.portal-architecture.v01', 'tfi.portal-architecture.v1.extra',
    'tfi.portal-architecture-exploration.v1', 'other.portal-architecture.v1',
    ' tfi.portal-architecture.v1', 'TFI.portal-architecture.v1', expected + '\n',
    null, undefined, 1, {}, [expected], new String(expected)]) {
    const document = Object.freeze({schema: actual, binding: 'unchanged'}), before = JSON.stringify(document);
    assert.equal(formats.schemaMatches(document.schema, expected), false);
    assert.equal(formats.schemaMatches(document.schema, expected, ['archive.']), false);
    assert.equal(JSON.stringify(document), before);
  }
  assert.equal(Object.hasOwn(formats, 'configureFormatNamespaces'), false);
});

test('expected tags must specify a full canonical name and exact version', () => {
  for (const expected of [undefined, null, 1, {}, 'tfi.', 'tfi.portal', 'tfi.portal.v0',
    'tfi.portal.v01', 'tfi.portal.v1.extra', 'tfi.portal.v1\n', 'archive.portal.v1']) {
    assert.throws(() => formats.schemaMatches(expected, expected), TypeError);
  }
});
