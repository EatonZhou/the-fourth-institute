const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const { babelParse } = require('./babel_parse.cjs');
const sourcePath = path.resolve(__dirname, '../../../management_portal/static/portal_workbench.js');

function layoutController() {
  const source = fs.readFileSync(sourcePath, 'utf8');
  const ast = babelParse(source, sourcePath);
  const scope = ast.program.body[0].expression.callee.body;
  const create = scope.body.find(node => node.type === 'FunctionDeclaration' && node.id.name === 'create');
  assert(create, 'Locate the production Workbench controller');
  const functions = new Map(create.body.body
    .filter(node => node.type === 'FunctionDeclaration').map(node => [node.id.name, node]));
  const take = name => {
    assert(functions.has(name), `Missing production controller function: ${name}`);
    const node = functions.get(name);
    return source.slice(node.start, node.end);
  };
  const visibleIds = create.body.body.find(node => node.type === 'VariableDeclaration'
    && node.declarations.some(declaration => declaration.id.name === 'visiblePanelIds'));
  assert(visibleIds, 'Locate the production visibility selector');
  const publicController = create.body.body.find(node => node.type === 'ExpressionStatement'
    && node.expression.type === 'AssignmentExpression' && node.expression.left.name === 'controller');
  assert(publicController, 'Locate the production public controller bindings');
  const properties = publicController.expression.right.arguments[0].properties;
  const bindings = ['applyWorkspace', 'hidePanel', 'showPanel', 'getState'].map(name => {
    const node = properties.find(property => property.key.name === name);
    assert(node, `Missing production public binding: ${name}`);
    return source.slice(node.start, node.end);
  });

  // Give unmodified controller functions the same state fixture as the browser
  // regression. All layout helpers remain in the original script's lexical
  // scope; neither placement capture nor restoration is reimplemented here.
  // DOM mounting/rollback, focus, measurements and iframe continuity stay in UI
  // regressions. These two effect boundaries cannot read/write the layout.
  const fixture = `
    global.__ratioController = function () {
      const options = { label: 'ratio fixture' };
      const root = { parentElement: null };
      const canvas = { clientWidth: 1160 };
      const descriptors = new Map(['navigation', 'canvas', 'preview'].map(id => [id, {
        id, canHide: true, defaultHidden: false, capabilities: [],
        minWidth: 180, minHeight: 140,
      }]));
      const panelIds = new Set(descriptors.keys());
      const unavailableBindings = new Set();
      const requiredIds = new Set(['canvas']);
      const placements = {}, savedWorkspaces = {};
      let hidden = new Set(), collapsed = new Set();
      let model = splitNode('horizontal', [
        stackNode(['navigation']), stackNode(['canvas']), stackNode(['preview']),
      ], [1, 2, 1]);
      let focusedStackId = '', activeWorkspaceName = '', activeOpenTargetPanelId = '';
      const layoutLocked = false;
      const render = global.__renderEffect;
      const closeMenu = global.__closeMenuEffect;
      ${source.slice(visibleIds.start, visibleIds.end)}
      ${['hiddenPanelsForLayout', 'normalizeCurrent', 'inheritedLayoutLock', 'isLayoutLocked',
    'applyWorkspace', 'hidePanel', 'showPanel', 'stateSnapshot'].map(take).join('\n')}
      const controller = Object.freeze({ ${bindings.join(', ')} });
      return controller;
    };
  `;
  const context = vm.createContext({ window: {
    PortalUIPrimitives: { escapeHtml: () => { throw new Error('Unexpected DOM formatting'); } },
    __renderEffect: reason => {
      assert.ok(['apply-workspace', 'hide-panel', 'show-panel'].includes(reason));
      return true;
    },
    __closeMenuEffect: () => {},
  } });
  vm.runInContext(source.slice(0, scope.end - 1) + fixture + source.slice(scope.end - 1), context,
    { filename: sourcePath });
  return { controller: context.window.__ratioController(), model: context.window.PortalWorkbench.model };
}

// Stable case: test_59d180cc7313. Losing the saved source/sibling weight in
// hidePanel/showPanel must fail this test; render cannot supply the result.
test('hiding and reopening a split Panel preserves its prior ratio', () => {
  const { controller, model: { split, stack } } = layoutController();
  const state = () => JSON.parse(JSON.stringify(controller.getState()));
  assert.equal(controller.applyWorkspace({
    layout: split('horizontal', [stack(['navigation']), stack(['canvas'])], [2, 5]),
    hidden: ['preview'],
  }), true);

  assert.equal(controller.hidePanel('navigation'), true);
  const hidden = state();
  assert.deepEqual(hidden.hidden.sort(), ['navigation', 'preview']);
  assert.equal(hidden.layout.type, 'stack');
  assert.deepEqual(hidden.layout.children.map(panel => panel.panelId), ['canvas']);

  assert.equal(controller.showPanel('navigation'), true);
  const restored = state();
  // The original browser case's complete assertions, with literal expectations.
  assert.equal(restored.layout.type, 'split');
  assert.equal(restored.layout.orientation, 'horizontal');
  assert.deepEqual(restored.layout.weights, [2, 5]);
  assert.deepEqual(restored.layout.children.map(group => group.children.map(panel => panel.panelId)),
    [['navigation'], ['canvas']]);
  assert.deepEqual(restored.hidden, ['preview']);
});
