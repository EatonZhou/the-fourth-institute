const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const {JSDOM} = require('jsdom');
const STATIC = path.resolve(__dirname, '../../../management_portal/static');
const source = name => fs.readFileSync(path.join(STATIC, name), 'utf8');
const json = value => JSON.parse(JSON.stringify(value));
const aliases = {theme:'archive.theme',language:'archive.language',page_state:'archive.state',
  interface_history:'archive.history',nav_user:'archive.user',live_tour_session:'archive.tour',
  refresh_channel:'ARCHIVE_REFRESH',refresh_lock_prefix:'ARCHIVE_LOCK:',demo_frame_scope:'archive.frame'};
function fixture(t, compatibility = {browser_identifiers:aliases,format_namespaces:['archive.']}) {
  const dom = new JSDOM('<!doctype html><html><head><title data-portal-site-title="[]"></title></head><body></body></html>',
    {url:'https://canonical.fixture.invalid/',runScripts:'outside-only'});
  t.after(()=>dom.window.close());
  const w=dom.window;
  w.__PORTAL_SITE__={site_name:'Current site',compatibility};
  w.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
  return w;
}
test('configured aliases never redirect native storage or migrate old content', t => {
  const w=fixture(t);
  const old={
    [aliases.page_state+'.operator.home']:JSON.stringify({text:'OLD BUSINESS BODY'}),
    [aliases.theme]:'dark',[aliases.language]:'en',[aliases.interface_history]:JSON.stringify({text:'OLD HISTORY'}),
  };
  for(const [key,value] of Object.entries(old)) w.localStorage.setItem(key,value);
  w.sessionStorage.setItem(aliases.nav_user,'old-actor');
  w.sessionStorage.setItem(aliases.live_tour_session,'OLD SESSION BODY');
  const reads=[];
  const get=w.Storage.prototype.getItem;
  w.Storage.prototype.getItem=function(key){reads.push(key);return get.call(this,key);};
  w.eval(source('portal_state.js'));
  assert.deepEqual(json(w.PortalState.read('home','operator',{})),{});
  w.PortalState.write('home','operator',{text:'CURRENT BODY'});
  w.eval(source('portal_identity.js'));
  assert.equal(w.PortalIdentity.username(),'');
  for(const name of ['portal_text.js','portal_translations.js','portal_i18n.js','portal_theme.js']) w.eval(source(name));
  assert.equal(w.PortalTheme.mode(),'auto');
  assert.equal(w.PortalLiveTourSessionStore.readRaw(),'');
  assert.equal(w.PortalCompatibility.identifier('language'),'tfi.portal.language');
  assert.equal(w.PortalCompatibility.identifier('demo_frame_scope'),'tfi.portal-demo-frame.v1');
  assert.equal(reads.some(key=>key.startsWith('archive.')),false);
  for(const [key,value] of Object.entries(old)) assert.equal(get.call(w.localStorage,key),value);
  assert.equal(get.call(w.sessionStorage,aliases.live_tour_session),'OLD SESSION BODY');
  assert.deepEqual(JSON.parse(get.call(w.localStorage,'tfi.portal.pageState.v1.operator.home')),{text:'CURRENT BODY'});
});
test('schema validation is exact regardless of supplied old prefixes and malformed mappings', t => {
  for(const compatibility of [{browser_identifiers:aliases,format_namespaces:['archive.']},null,
    {browser_identifiers:[],format_namespaces:'archive.'}]) {
    const w=fixture(t,compatibility);
    // A prior compatibility helper must not survive a new canonical load.
    w.PortalCompatibility={identifier:()=> 'archive.state',schemaMatches:()=>true};
    w.eval(source('portal_state.js'));
    const api=w.PortalCompatibility;
    assert(Object.isFrozen(api));
    assert.equal(api.identifier('theme'),'tfi.portal.theme');
    assert.throws(()=>api.identifier('private_token'),/Unknown browser identifier/);
    for(const suffix of ['resource-portal-routes.v1','hatg-primitive-definitions.v1','portal-live-tour.v1']) {
      assert.equal(api.schemaMatches('tfi.'+suffix,'tfi.'+suffix),true);
      assert.equal(api.schemaMatches('archive.'+suffix,'tfi.'+suffix),false);
      assert.equal(api.schemaMatches('tfi.'+suffix+'.extra','tfi.'+suffix),false);
      assert.equal(api.schemaMatches(null,'tfi.'+suffix),false);
    }
  }
});
test('refresh channels and locks use canonical identifiers even with external old mappings', async t => {
  const w=fixture(t);w.eval(source('portal_state.js'));
  const channels=[],locks=[];
  w.BroadcastChannel=class {constructor(name){channels.push(name)} addEventListener(){}removeEventListener(){}close(){}postMessage(){}};
  w.PortalIdentity={username:()=> 'operator'};
  w.navigator.locks={request:async(name,fn)=>{locks.push(name);return fn();}};
  w.eval(source('portal_refresh_coordinator.js'));
  assert.equal(await w.PortalRefreshCoordinator.createSharedFetcher().fetch('value',()=>23),23);
  assert.deepEqual(channels,['tfi-portal-refresh']);
  assert.deepEqual(locks,['tfi-refresh:["tfi-portal-refresh","operator","value"]']);
});
test('public site object filters private fields and fixes immutable empty compatibility', t => {
  const w=fixture(t),paths={share_root:'/current/share',staging_root:'/current/staging',test_results_root:'/current/results',private_path:'/private'};
  const input={site_name:'Current site',paths,compatibility:{browser_identifiers:aliases,format_namespaces:['archive.']},deployment:{secret:'PRIVATE'}};
  w.eval(source('portal_site.js')+'('+JSON.stringify(input)+');');
  const site=w.__PORTAL_SITE__;
  assert.deepEqual(json(site),{site_name:'Current site',paths:{share_root:'/current/share',staging_root:'/current/staging',test_results_root:'/current/results'},compatibility:{browser_identifiers:{},format_namespaces:[]}});
  for(const value of [site,site.paths,site.compatibility,site.compatibility.browser_identifiers,site.compatibility.format_namespaces,w.PortalSite]) assert(Object.isFrozen(value));
  assert.equal(w.PortalSite.name(),'Current site');
  assert.throws(()=>w.eval('"use strict";__PORTAL_SITE__.compatibility.browser_identifiers.theme="archive.theme";'));
});
