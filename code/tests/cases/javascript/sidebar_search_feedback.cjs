'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {startWorkbenchPage}=require('./lineage_workbench_modules.cjs');

test('navigation search exposes its cause, clears safely and stays local to the visit',async t=>{
  const page=startWorkbenchPage({answers:{
    '/api/me':{username:'fixture'},
    '/lineage/api/service-status':{ready:true,state:'ready'},
    '/api/agent-workbench/residents':{ready:true,residents:[],lineages:[],principal_hierarchy:{user_residents:[],projects:[
      {project_id:'alpha',title:'Alpha',lineage_id:'lineage_aaaaaaaaaaaa',lineages:[]},
      {project_id:'beta',title:'Beta',lineage_id:'lineage_bbbbbbbbbbbb',lineages:[]},
    ]}},
  }});
  t.after(page.close);
  const doc=page.document;
  await page.until(()=>doc.querySelectorAll('#agentList .principalProjectCard').length===2);
  const list=()=>doc.getElementById('agentList');
  const input=doc.getElementById('searchInput');
  const search=value=>{input.value=value;input.dispatchEvent(new page.window.Event('input',{bubbles:true}));};
  const part=page.parts.PortalLineageWorkbenchSidebarAgentList;
  const state=part.dependencies.state;
  await page.until(()=>state.loadAllComplete);
  page.window.PortalI18n.apply('zh-CN');
  part.exports.renderAgentList();
  const filterSummary=()=>doc.querySelector('#agentFilterSummary .portalFilterSummary');
  assert.equal(state.projectDisplayFilter,'non-empty');
  assert.equal(filterSummary().hidden,true,'the initial display range does not show a status block');
  for (const mode of ['all','non-empty']) {
    doc.querySelector(`[data-project-display-filter="${mode}"]`).click();
    assert.equal(state.projectDisplayFilter,mode,'the range remains selectable from the filter menu');
    assert.equal(filterSummary().hidden,true,`${mode} does not show a filter summary`);
    assert.equal(doc.getElementById('agentFilterSummary').classList.contains('is-empty'),true);
    assert.equal(doc.getElementById('filterToggleBtn').classList.contains('is-active'),mode==='non-empty');
  }
  state.lineages=[{lineage_id:'lineage_aaaaaaaaaaaa',project_id:'alpha'}];
  state.projectFilter='alpha';part.exports.renderAgentList();
  assert.equal(filterSummary().hidden,false,'a selected Project remains visible as a restriction');
  assert.doesNotMatch(filterSummary().textContent,/显示范围|有内容/);
  filterSummary().querySelector('.portalFilterSummaryClear').click();
  assert.equal(state.projectFilter,'all');
  assert.equal(state.projectDisplayFilter,'non-empty','clearing the Project preserves the display range');
  assert.equal(filterSummary().hidden,true);
  assert.equal(doc.getElementById('searchToggleBtn'),null,'the search field is always shown, without a toggle');
  search('provider');
  assert.match(list().textContent,/正在筛选：provider/);
  assert.match(list().textContent,/匹配 0 个项目/);
  assert.match(list().textContent,/没有与「provider」匹配的项目/);
  list().querySelector('[data-sidebar-show-all]').click();
  assert.equal(input.value,'');
  assert.equal(list().querySelectorAll('.principalProjectCard').length,2);
  assert.equal(filterSummary().hidden,true,'show-all must not leave an all-projects chip behind');
  search('Alpha');
  assert.match(list().textContent,/匹配 1 个项目/);
  assert.equal(list().querySelectorAll('.principalProjectCard').length,1);
  input.dispatchEvent(new page.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
  assert.equal(input.value,'');assert.equal(state.query,'');
  assert.equal(list().querySelectorAll('.principalProjectCard').length,2);
  search('<img src=x onerror=alert(1)>');
  assert.equal(list().querySelector('.sidebarSearchSummary img'),null);
  assert.match(list().textContent,/<img src=x onerror=alert\(1\)>/);
  list().querySelector('[data-sidebar-search-clear]').click();
  part.dependencies.writeLineageUiState({query:'old persisted text',searchOpen:true});
  const sequence=state.loadAllSeq;
  assert.equal(doc.getElementById('refreshBtn').disabled,false,'the refresh action is available');
  doc.getElementById('refreshBtn').click();
  await page.until(()=>state.loadAllSeq>sequence&&state.loadAllComplete);
  assert.equal(state.query,'','refresh must ignore old persisted searches');
  search('Beta');
  input.blur(); assert.equal(state.query,'Beta');
  part.exports.toggleResidentProjectGroup('lineage:beta');assert.equal(state.query,'Beta');
  state.residentSidebarReady=false;state.residentSidebarError='offline';part.exports.renderAgentList();
  assert.match(list().textContent,/Project 读取失败：offline/);
  assert.ok(list().querySelector('[data-sidebar-retry]'));
  assert.equal(list().querySelector('[data-sidebar-show-all]'),null);
  assert.doesNotMatch(list().textContent,/没有与「Beta」匹配/);
  state.residentSidebarError='';part.exports.renderAgentList();
  assert.match(list().textContent,/正在读取 Project/);
  state.residentSidebarReady=true;state.principalHierarchy={projects:[]};state.query='';part.exports.renderAgentList();
  assert.match(list().textContent,/暂无可访问的 Project/);
  search('Alpha');
  page.window.dispatchEvent(new page.window.PageTransitionEvent('pagehide',{persisted:true}));
  assert.equal(state.query,'','leaving clears even when back-forward cache retains the document');
  assert.deepEqual(page.errors,[]);
});
