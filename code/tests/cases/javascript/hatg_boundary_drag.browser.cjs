const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || '../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const root = path.resolve(__dirname, '../../../management_portal/static');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.setContent('<div id="nodes" style="position:relative;width:4000px;height:3000px"></div>');
    await page.addStyleTag({ content: '.functionBlockBoundary {position:absolute} header {display:flex} button {font:14px sans-serif}' });
    await page.addScriptTag({ path: path.join(root, 'vendor/d3/d3.v7.9.0.min.js') });
    await page.addScriptTag({ content: 'window.PortalI18n={t:text=>text}' });
    await page.addScriptTag({ content: fs.readFileSync(process.env.HATG_BOUNDARY_REFERENCE || path.join(root, 'lineage_graph_function_blocks.js'), 'utf8') });
    const result = await page.evaluate(() => {
      const nodes = Array.from({ length: 160 }, (_, i) => ({ id: `n${i}`, x: 5 + (i % 16) * 5, y: 5 + Math.floor(i / 16) * 8 }));
      const blocks = Array.from({ length: 80 }, (_, i) => ({ id: `b${i}`, title: `Block ${i}`, member_ids: [`n${i * 2}`, `n${i * 2 + 1}`] }));
      blocks.push({ id: 'parent', title: 'Nested parent', member_ids: [], child_block_ids: ['b0', 'b1'] });
      blocks.push({ id: 'grandparent', title: 'Nested ancestor', member_ids: [], child_block_ids: ['parent'] });
      const state = { model: { functionBlockBoundaries: blocks }, nodeMap: new Map(nodes.map(n => [n.id, n])) };
      const calls = [];
      const api = PortalHatgGraphFunctionBlocks.createFunctionBlocks({
        d3, layers: { nodes: d3.select('#nodes') }, state, worldSize: { width: 4000, height: 3000 },
        nodePosition: n => n, nodeHalfExtents: () => ({ x: 1, y: 1 }), portsForNode: () => [], portPosition: () => null,
        refreshNodeExtentCache() {}, options: { onFunctionBlockMenu: id => calls.push(id), onFunctionBlockCollapse: id => calls.push(`collapse:${id}`) },
      });
      api.renderFunctionBlockBoundaries();
      const element = id => document.querySelector(`[data-function-block-boundary="${id}"]`);
      const title = element('b0').querySelector('button');
      const unchanged = element('b79').outerHTML;
      const observer = new MutationObserver(() => {});
      observer.observe(document.querySelector('#nodes'), { subtree: true, attributes: true, childList: true });
      const start = performance.now();
      for (let i = 0; i < 20; i++) {
        nodes[0].x -= .1;
        nodes[1].y += .05;
        api.renderFunctionBlockBoundaries(new Set(['n0', 'n1']));
      }
      const elapsedMs = performance.now() - start;
      const mutations = observer.takeRecords();
      observer.disconnect();
      const touched = [...new Set(mutations.map(m => m.target.closest?.('[data-function-block-boundary]')?.dataset.functionBlockBoundary).filter(Boolean))];
      const nested = ['b0', 'parent', 'grandparent'].map(id => ({ id, left: parseFloat(element(id).style.left), top: parseFloat(element(id).style.top), right: parseFloat(element(id).style.left) + parseFloat(element(id).style.width) }));
      title.click();
      element('parent').querySelector('.functionBlockBoundaryCollapse').click();
      const intact = element('b79').outerHTML === unchanged && element('b0').querySelector('button') === title;
      // Incremental dragging must not weaken the full render's structure update.
      blocks.splice(79, 1);
      blocks[0].title = 'Renamed block';
      api.renderFunctionBlockBoundaries();
      return { elapsedMs, mutationCount: mutations.length, touched, nested, intact, calls, removed: !element('b79'), renamed: title.textContent };
    });
    console.log(JSON.stringify(result));
    assert.equal(result.intact, true);
    assert.equal(result.removed, true);
    assert.equal(result.renamed, 'Renamed block');
    assert.deepEqual(result.calls, ['b0', 'collapse:parent']);
    assert.ok(result.nested[1].left < result.nested[0].left && result.nested[2].left < result.nested[1].left, 'every nested ancestor follows the moved members');
    if (!process.env.HATG_BOUNDARY_REFERENCE) assert.deepEqual(result.touched.sort(), ['b0', 'grandparent', 'parent'], 'drag must leave unrelated frame DOM untouched');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
