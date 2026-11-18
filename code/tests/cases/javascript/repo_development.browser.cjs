'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const repo = path.resolve(__dirname, '../../..');
const staticRoot = path.join(repo, 'management_portal/static');
const playwright = require(path.join(repo, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const { portalWorkbenchScripts } = require('./portal_workbench_scripts.cjs');
const { portalWorkspaceSurfaceScripts } = require('./portal_workspace_surface_scripts.cjs');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
const routes = JSON.parse(fs.readFileSync(0, 'utf8')).routeScript;
const scripts = ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_i18n.js',
  'portal_ui_primitives.js', 'portal_ui.js', 'portal_overlay.js', 'portal_resource_chips.js',
  'portal_image_editor.js', 'portal_editor_open_service.js', 'portal_command_registry.js',
  'portal_workbench_context.js', ...portalWorkbenchScripts(), ...portalWorkspaceSurfaceScripts(),
  'portal_preview_contract.js', 'portal_preview_runtime.js', 'portal_pdf_preview.js', 'portal_preview_renderers.js',
  'portal_resident_service.js', 'portal_resident_runtime_controller.js', 'resident_protocol.js',
  ...chatWorkspaceScripts(), 'markdown.js', 'portal_document_statistics.js', 'vendor/monaco/vs/loader.js', 'portal_code_editor.js',
  'portal_source_editor_panel.js', 'portal_repository_panels.js', 'repo_workspace_development.js'];
const styles = ['portal_components.css', 'portal_components_markdown.css', 'portal_components_chat_frame.css',
  'portal_components_messages.css', 'portal_components_composer.css', 'portal_components_queue.css',
  'portal_components_resource_chips.css', 'portal_workbench.css', 'portal_source_editor_panel.css',
  'portal_repository_panels.css', 'repo_workspace_development.css', 'portal_design_system.css', 'portal_themes.css'];
const html = `<!doctype html><html><head><meta charset="utf-8">${styles.map(n=>`<link rel="stylesheet" href="/static/${n}">`).join('')}</head><body>
  <main id="host"></main><script src="/routes.js"></script>${scripts.map(n=>`<script src="/static/${n}"></script>`).join('')}</body></html>`;
(async () => {
  const server = http.createServer((req,res) => {
    const url = new URL(req.url,'http://fixture');
    if (url.pathname === '/fixture.pdf') { res.setHeader('Content-Type','application/pdf'); return res.end(fs.readFileSync(path.join(repo,'tests/cases/fixtures/paper-synthetic-means.pdf'))); }
    if (url.pathname === '/') { res.setHeader('Content-Type','text/html'); return res.end(html); }
    if (url.pathname === '/routes.js') { res.setHeader('Content-Type','text/javascript'); return res.end(routes); }
    const file = path.resolve(staticRoot, '.' + url.pathname.replace(/^\/static/, ''));
    if (!url.pathname.startsWith('/static/') || !file.startsWith(staticRoot + path.sep)) { res.writeHead(404); return res.end(); }
    try { res.setHeader('Content-Type',file.endsWith('.css')?'text/css':'text/javascript'); res.end(fs.readFileSync(file)); }
    catch { res.writeHead(404); res.end(); }
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  let browser;
  try {
    browser = await playwright.chromium.launch({headless:true});
    const page = await browser.newPage({viewport:{width:1800,height:1000}});
    page.setDefaultTimeout(10000);
    const errors=[]; page.on('pageerror',error=>errors.push(error.message));
    const origin=`http://127.0.0.1:${server.address().port}`;
    await page.route('**/*', route=>route.request().url().startsWith(origin+'/')?route.continue():route.abort());
    await page.goto(origin);
    await page.evaluate(() => {
      window.notices=[]; window.writes=[];
      window.PortalIdentity={username:()=> 'repo-development-fixture'};
      window.files={ 'main.js':{path:'main.js',content:'const value = 1;\n',hash:'v1',editable:true},
        'README.md':{path:'README.md',content:'# Example\n\n![diagram](fig.png)',hash:'md1',editable:true},
        'main.tex':{path:'main.tex',content:'\\documentclass{article}\n',hash:'tex1',editable:true} };
      window.PortalAPI = {
        get:async url=>{
          const u = new URL(url,location.origin), file=u.searchParams.get('path');
          if (u.pathname.endsWith('/item-chat')) return {binding_state:'unbound',chat_workspace:{schema:'portal.chat_workspace.v3',
            target:{kind:'knowledge_item',id:'repo_123456789abc'},header:{title:'Repo chat'},messages:[],
            composer:{can_send:true,history_scope:'repo-fixture'}}};
          if (u.pathname.endsWith('/file')) { if (!files[file]) throw new Error('Missing file'); return {...files[file]}; }
          if (u.pathname.endsWith('/tree')) return {id:'repo_123456789abc',path:'',entries:Object.values(files).map(f=>({name:f.path,path:f.path,kind:'file'}))};
          if (u.pathname.endsWith('/search')) return {results:Object.values(files).map(f=>({path:f.path,name:f.path}))};
          if (u.pathname.endsWith('/latex')) return {status:'idle',pdf_available:false};
          if (u.pathname.endsWith('/diff')) return {path:file,diff:'diff --git a/main.js b/main.js\n--- a/main.js\n+++ b/main.js\n@@ -1 +1 @@\n-const value = 0;\n+const value = 1;\n'};
          if (u.pathname.endsWith('/workspace')) return window.overview;
          throw new Error('Unexpected read '+url);
        },
        post:async (url,data)=>{
          writes.push({url,data});
          if (url.endsWith('/file')) {
            const f=files[data.path]; if(f.hash!==data.expected_hash) throw Object.assign(new Error('conflict'),{status:409});
            Object.assign(f,{content:data.content,hash:f.hash+'x'}); return {...f};
          }
          if (url.endsWith('/latex')) {
            if (window.compileFailure) throw Object.assign(new Error('compile failed'),{status:422,data:{report:{status:'error',log:'fixture compile diagnostic',pdf_available:true,pdf_url:'/fixture.pdf'}}});
            return {status:'success',log:'fixture compile finished',compiled_at:String(writes.length),pdf_available:true,pdf_url:'/fixture.pdf'};
          }
          throw new Error('Messages must not be sent');
        },
      };
      window.overview={id:'repo_123456789abc',checkout_path:'/fixture/repo',current_branch:'main',
        capabilities:{git:true,write_files:true,search_files:true},status:{changes:[{path:'main.js',status:' M',worktree:'M'}]}};
      window.options={root:document.getElementById('host'),repoId:overview.id,overview,api:PortalAPI,
        onError:e=>notices.push(e.message||String(e)),renderMarkdown:(content,path)=>MD.render(content,{basePath:path,repoId:overview.id})};
      window.session=PortalRepoDevelopment.mount(options);
    });
    await page.evaluate(()=>session.openFile('main.js'));
    await page.waitForSelector('.monaco-editor');
    const rail = await page.locator('.repoDevelopmentTools').boundingBox();
    const workbench = await page.locator('.repoDevelopmentWorkbench').boundingBox();
    assert.ok(rail.width <= 60 && rail.x + rail.width <= workbench.x + 1, 'tools form a narrow left activity rail');
    assert.ok(Math.abs(rail.y - workbench.y) < 2, 'tools consume no row above the editing area');
    assert.ok(workbench.height >= 900, 'workbench uses available viewport height');
    await page.getByRole('button',{name:'刷新文件状态',exact:true}).click();
    const value=await page.evaluate(()=>session.workbench.getPanel('source').activeDocument().content);
    assert.match(value,/const value = 1/);
    await page.evaluate(()=>session.openFile('README.md'));
    await page.locator('[data-activity-view="preview"]').click();
    await page.waitForSelector('.markdownBody h1');
    assert.equal(await page.locator('.markdownBody h1').textContent(),'Example');
    assert.equal(await page.evaluate(()=>writes.length),0,'preview must not write or compile');
    // Select actual source text and add it to the shared composer without sending.
    await page.evaluate(async()=>{session.workbench.focusPanel('source');await session.workbench.getPanel('source').openFile('main.js',{selection:{startLineNumber:1,startColumn:1,endLineNumber:1,endColumn:16}});});
    await page.locator('[data-source-action="chat"]').click();
    await page.waitForFunction(()=>session.workbench.getPanel('chat').workspace.payload().text.includes('const value'));
    const refs=await page.evaluate(()=>session.workbench.getPanel('chat').workspace.payload().text);
    assert.match(refs,/repo_123456789abc/); assert.match(refs,/main.js/); assert.match(refs,/start_line/);
    // A live dirty editor survives a host tab switch and an external modification.
    await page.evaluate(async()=>{await session.openFile('main.js');session.workbench.focusPanel('source');});
    await page.locator('.monaco-editor:visible').first().click();
    await page.keyboard.press('Control+End'); await page.keyboard.type('// local draft');
    assert.equal(await page.evaluate(()=>session.hasDirty()),true);
    await page.evaluate(()=>{window.retained=session.workbench.getPanel('source');document.getElementById('host').replaceChildren();});
    await page.evaluate(()=>{PortalRepoDevelopment.mount(options); files['main.js']={...files['main.js'],content:'const remote = 2;\n',hash:'v2'};});
    await page.evaluate(()=>session.workbench.getPanel('source').refresh());
    assert.equal(await page.evaluate(()=>session.workbench.getPanel('source')===retained),true);
    assert.match(await page.evaluate(()=>retained.activeDocument().content),/local draft/);
    assert.equal(await page.evaluate(()=>writes.length),0);
    // Conflict comparison is explicit; accepting disk restores clean source.
    await page.evaluate(()=>retained.showDiff('main.js'));
    await page.locator('[data-source-action="use-disk"]').click();
    assert.equal(await page.evaluate(()=>session.hasDirty()),false);
    await page.locator('[data-activity-view="git"]').click();
    await page.locator('[data-repository-diff="main.js"]').click();
    await page.waitForSelector('.portalSourceEditorComparison');
    assert.match(await page.locator('.portalSourceEditorComparison').innerText(),/main.js/);
    await page.locator('[data-source-action="close-diff"]').click();
    // Draft LaTeX changes are saved by the compilation action, not by previewing.
    await page.evaluate(async()=>{await session.openFile('main.tex');session.workbench.focusPanel('preview');await session.workbench.getPanel('preview').setDocument(session.workbench.getPanel('source').activeDocument());});
    assert.equal(await page.evaluate(()=>writes.length),0);
    // Compile saves pending edits first and renders the real PDF via the shared viewer.
    await page.evaluate(()=>session.workbench.focusPanel('source'));
    await page.locator('.monaco-editor:visible').first().click();
    await page.keyboard.press('Control+End'); await page.keyboard.type('% edited');
    await page.locator('[data-activity-view="preview"]').click();
    await page.evaluate(()=>session.workbench.getPanel('preview').compile());
    await page.waitForSelector('.portalRepositoryPanel-preview canvas');
    assert.deepEqual(await page.evaluate(()=>writes.map(w=>w.url.split('/').at(-1))),['file','latex']);
    assert.equal(await page.evaluate(()=>session.hasDirty()),false);
    assert.ok((await page.locator('.repositoryPdfHost').boundingBox()).height > 400,
      'PDF preview occupies the remaining viewport rather than collapsing inside the flex host');
    if (process.env.REPO_DEVELOPMENT_EVIDENCE) await page.screenshot({path:process.env.REPO_DEVELOPMENT_EVIDENCE,fullPage:true});
    await page.evaluate(async()=>{window.compileFailure=true; await session.workbench.getPanel('preview').compile();});
    assert.match(await page.locator('.repositoryCompileLog').innerText(),/fixture compile diagnostic/);
    assert.ok(await page.locator('.portalRepositoryPanel-preview canvas').first().isVisible(),'compile errors retain the last PDF');
    // The compile error is expected and surfaced once; no other integration errors.
    assert.equal(await page.evaluate(()=>notices.length),1);
    await page.evaluate(()=>notices.length=0);
    assert.deepEqual(errors,[]);
    assert.deepEqual(await page.evaluate(()=>notices),[]);
    console.log('Repo Workbench/editor/Markdown/PDF/Chat composition, selection reference, conflict/Git diff and save-before-compile passed');
  } finally {await browser?.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
