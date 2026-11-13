(() => {
  'use strict';
  const scopes=new Set(['library','ccf','recent','favorites']);
  const scopeOf=value=>scopes.has(value)?value:'library';
  const key=route=>JSON.stringify(route);
  function create({state,paperApi,loadLibrary,loadDetail,cancelPdfRendering,renderWorkspace,showCcfFace,syncModeTabs,writeUiState,alertError}) {
    let current=null,restoring=false,sequence=0,paperSnapshot=null;
    history.scrollRestoration='manual';
    const scrolls=new Map();
    const main=()=>document.getElementById('workspaceMain');
    function parse(){const p=new URL(location.href).searchParams,scope=scopeOf(p.get('scope'));return p.get('paper')?{kind:'paper',scope,id:p.get('paper')}:p.get('semantic')?{kind:'search',scope:'library',id:p.get('semantic')}:p.get('collector')?{kind:'collector',scope:'library',id:p.get('collector')}:{kind:p.get('view')==='list'?'list':'home',scope};}
    function address(route){const u=new URL(location.href);for(const field of ['paper','semantic','collector','scope','view'])u.searchParams.delete(field);if(route.kind!=='home'||route.scope!=='library')u.searchParams.set('scope',route.scope);if(route.kind==='paper')u.searchParams.set('paper',route.id);else if(route.kind==='search')u.searchParams.set('semantic',route.id);else if(route.kind==='collector')u.searchParams.set('collector',route.id);else if(route.kind!=='home'||route.scope!=='library')u.searchParams.set('view',route.kind);return u.pathname+u.search+u.hash;}
    function save(){if(!current)return;const scroll=main()?.scrollTop||0;scrolls.set(key(current),scroll);history.replaceState({...history.state,paperNav:{route:current,scroll,principal:state.principalId}},'',location.href);}
    function commit(route,push=true,snapshot=true){if(push&&key(route)!==key(current)){if(snapshot)save();history.pushState({...history.state,paperNav:{route,scroll:0,principal:state.principalId}},'',address(route));}else history.replaceState({...history.state,paperNav:{route,scroll:history.state?.paperNav?.scroll||0,principal:state.principalId}},'',address(route));current=route;state.paperNavigationRoute=route;}
    function prepare(route){state.paperHome=route.kind==='home';state.paperHomeReady=false;state.leftFace=route.kind!=='paper'&&route.scope==='ccf'?'ccf':'library';document.dispatchEvent(new CustomEvent('portal:paper-scope-restore',{detail:{scope:route.scope}}));state.paperListScope=route.scope;}
    async function restore(route,{push=false,scroll=0}={}){
      if(!await cancelPdfRendering())return false;
      const request=++sequence;restoring=true;
      try{
        if(push)commit(route,true);else {current=route;state.paperNavigationRoute=route;}
        prepare(route);state.libraryRequestSeq++;state.detailRequestSeq++;
        state.selectedId='';state.detail=null;state.ccfSelectedPaper=null;
        state.activeSemanticSearchId=route.kind==='search'?route.id:'';
        state.activeCollectorJobId=route.kind==='collector'?route.id:'';
        state.semanticSearch?.setActiveId?.(state.activeSemanticSearchId,{notify:false});
        state.query='';state.filters={};state.mode=route.kind==='paper'?'reader':'library';
        const search=document.getElementById('searchInput');if(search)search.value='';
        if(route.kind==='paper')await loadDetail(route.id,{fromNavigation:true});
        else if(route.scope==='ccf'){await showCcfFace({remember:false});state.libraryLoading=false;}
        else {
          await loadLibrary({keepDetail:false,hydrateDetail:false});
          if(request!==sequence)return false;
          if(!['search','collector'].includes(route.kind))state.paperOverview={...state.library?.summary};
          if(['recent','favorites'].includes(route.scope)){
            const papers=[];let cursor='',offset=0,more=true;
            while(more){const result=await paperApi.request(route.scope==='recent'?`/paper/api/recent-reading?limit=50&cursor=${encodeURIComponent(cursor)}`:`/paper/api/library?compact=1&rows_only=1&favorite=1&limit=50&offset=${offset}`);if(request!==sequence)return false;papers.push(...result.papers);offset=papers.length;cursor=result.next_cursor||'';more=route.scope==='recent'?!!cursor:result.papers.length===50;}
            state.library={...state.library,papers,summary:{...state.library.summary,personal_scope:route.scope,visible_count:papers.length}};
            (state.paperPersonalLists||={})[route.scope]=papers;
          }
        }
        if(request!==sequence)return false;
        if(route.kind==='home'&&route.scope==='ccf'&&!state.paperOverview){const root=await paperApi.request('/paper/api/library?limit=1');if(request!==sequence)return false;state.paperOverview={...root.summary};}
        state.paperHomeReady=route.kind==='home';syncModeTabs();writeUiState();renderWorkspace();
        requestAnimationFrame(()=>{if(request===sequence&&main())main().scrollTop=scroll;});return true;
      }finally{if(request===sequence)restoring=false;}
    }
    function openPaperLocation(id){if(restoring)return;const route={kind:'paper',scope:scopeOf(state.paperListScope||current?.scope),id};state.paperHome=false;state.paperHomeReady=false;const captured=paperSnapshot===key(current);paperSnapshot=null;commit(route,true,!captured);}
    function beginPaperOpen(){sequence++;restoring=false;state.libraryRequestSeq++;state.libraryLoading=false;}
    function selectResult(kind,id){if(restoring)return;const route={kind,scope:'library',id};state.paperHome=false;state.paperHomeReady=false;state.paperListScope='library';commit(route);}
    const goList=scope=>restore({kind:'list',scope:scopeOf(scope)},{push:true});
    function seed(){const route=parse();prepare(route);current=route;state.paperNavigationRoute=route;
      state.mode=route.kind==='paper'?'reader':'library';state.selectedId=route.kind==='paper'?route.id:'';state.detail=null;
      state.activeSemanticSearchId=route.kind==='search'?route.id:'';state.activeCollectorJobId=route.kind==='collector'?route.id:'';
      const knownSource=!!document.referrer&&new URL(document.referrer).origin===location.origin;
      if(route.kind==='paper'&&!history.state?.paperNav&&(history.length<=1||!knownSource)){const home={kind:'home',scope:route.scope};history.replaceState({...history.state,paperNav:{route:home,scroll:0,principal:state.principalId}},'',address(home));history.pushState({...history.state,paperNav:{route,scroll:0,principal:state.principalId}},'',address(route));}
      else commit(route,false);
      return route;
    }
    window.addEventListener('popstate',()=>{const entry=history.state?.paperNav;const route=entry?.principal===state.principalId?entry.route:parse();restore(route,{scroll:entry?.scroll??scrolls.get(key(route))??0}).catch(alertError);});
    document.addEventListener('click',event=>{if(current?.kind==='paper'||event.target.closest('[data-paper-favorite],[data-favorite]'))return;if(event.target.closest('.paperRow,[data-paper-id]')){save();paperSnapshot=key(current);}},true);
    return {seed,restore,goList,openPaperLocation,beginPaperOpen,selectResult,get restoring(){return restoring;},get current(){return current;}};
  }
  function decorateHome(state){
    const host=document.getElementById('workspaceMain');if(!host)return;
    const existing=host.querySelector(':scope > .paperHomeOverview');
    if([...new URL(location.href).searchParams].length||String(state.query||'').trim()||Object.values(state.filters||{}).some(value=>String(value||'').trim())||state.registeredFilter||!state.paperHome||!state.paperHomeReady||state.paperNavigationRoute?.kind!=='home'||state.libraryLoading||(state.paperListScope!=='ccf'&&(state.library?.summary?.semantic_search||state.library?.summary?.paper_collection))){existing?.remove();return;}
    if(existing)return;
    const summary=state.paperOverview||state.library?.summary||{},grid=document.createElement('div');grid.className='overviewGrid paperHomeOverview';
    for(const [field,label]of [['paper_count','Registered papers'],['analysis_count','Analysis ready'],['review_count','AI reviews']]){const item=document.createElement('div');item.className='metric';const value=document.createElement('div');value.className='metricValue';value.textContent=Number(summary[field]||0);const name=document.createElement('div');name.className='metricLabel';name.textContent=window.PortalI18n.t(label);item.append(value,name);grid.append(item);}host.prepend(grid);
  }
  window.PortalPaperNavigation={create,decorateHome,current:null};
})();
