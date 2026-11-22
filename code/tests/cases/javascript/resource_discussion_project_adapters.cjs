'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {babelParse, traverse} = require('./babel_parse.cjs');

// Evaluate the actual host hooks, isolating only their HTTP transports. The
// shared panel's real DOM, lifecycle and sends are covered by the browser case.
(async () => {
  for (const file of ['article_workspace_chat.js', 'knowledge_board_chat.js', 'repo_workspace_development.js']) {
    const source = fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static', file), 'utf8');
    const configs = [];
    traverse(babelParse(source, file), {ObjectExpression(p) {
      if (p.node.properties.some(prop => prop.key?.name === 'resourceProjectContext')) configs.push(p.node);
    }});
    assert.equal(configs.length, 1);
    const hooks = new Set(['resourceProjectContext', 'resolveResident', 'resolveBeforeSend', 'resolveBeforeSettings', 'initialContext']);
    const expression = '({' + configs[0].properties.filter(prop => hooks.has(prop.key?.name)).map(prop => source.slice(prop.start, prop.end)).join(',') + '})';
    const calls = [];
    const api = {get: async url => calls.push({method: 'GET', url}), post: async (url, body) => calls.push({method: 'POST', url, body})};
    const options = vm.runInNewContext(expression, {URLSearchParams, window: {PortalAPI: api}, api,
      knowledgeApi: {request: async (url, opts) => calls.push({method: opts?.method || 'GET', url, body: opts?.body ? JSON.parse(opts.body) : undefined})},
      repoId: 'repo_fixture', overview: {}, articleRoot: () => null, selectedArticle: () => null});
    assert.equal(options.resourceProjectContext, true);
    for (const project_id of [undefined, '项目 &/二', '@none']) {
      const target = {kb_id: 'resource_fixture', ...(project_id ? {project_id} : {})};
      await options.resolveResident(target);
      const query = new URL(calls.at(-1).url, 'http://fixture').searchParams;
      assert.equal(query.get('kb_id'), target.kb_id);
      assert.equal(query.get('project_id'), project_id || null);
      await options.resolveBeforeSend(target, {plainText: 'question'});
      assert.equal(calls.at(-1).body.project_id, project_id);
      assert.equal(calls.at(-1).body.purpose, 'send');
      assert.equal(calls.at(-1).body.message, 'question');
      await options.resolveBeforeSettings(target);
      assert.equal(calls.at(-1).body.project_id, project_id);
      assert.equal(calls.at(-1).body.purpose, 'settings');
      assert.match(options.initialContext(target, {initial_context: {context_text: 'fresh project summary'}}).context_text, /fresh project summary/);
    }
    console.log('PASS ' + file + ': optional/encoded/@none project in GET, send, settings; fresh initial context');
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
