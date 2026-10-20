import test from 'node:test';
import assert from 'node:assert/strict';
import {data as matrix} from '../src/matrix/catalog.js';
import {createCatalog} from '../src/semantic/catalog.js';

test('graph and snapshot model identities match the same research reports', () => {
  const graph = {nodes:[{title:'模型 Steward｜GR00T N1.7',resident_id:'resident_111111111111'}]};
  const semantic = createCatalog({snapshot:[['A100','GR00T N1.7']],snapshotEvidence:{}});
  for (const catalog of [matrix, semantic]) {
    const [model] = catalog.modelsFromGraph(graph);
    assert.equal(model.key, 'gr00tn17');
    assert.equal(model.residentId, 'resident_111111111111');
    assert.equal(catalog.relevantMethod({text:'GR00T N1.7 uses this method'}, model), true);
    assert.equal(catalog.relevantMethod({text:'GROOT trial'}, model), true);
    assert.equal(catalog.relevantMethod({text:'unrelated Motus measurement'}, model), false);
    assert.equal(catalog.relevantMethod({text:'OpenPI trial'}, {key:'pi05'}), true);
    assert.equal(catalog.relevantMethod({text:'Efficient-WAM full model'}, {key:'efficientwam'}), true);
  }
});
