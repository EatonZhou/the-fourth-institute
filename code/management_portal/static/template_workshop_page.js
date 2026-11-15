(() => {
  'use strict';
  async function start() {
    const root = document.querySelector('#workshopPage');
    try {
      await window.PortalIdentity.load();
      const params = new URLSearchParams(location.search);
      const panel = window.PortalWorkbench.createPanel('workshop', {root,id:'standaloneWorkshop',
        target:{template_id:params.get('template') || '',store_id:params.get('store') || '',lineage_id:params.get('lineage') || ''},
        emit(event,detail) {
          if (event !== 'workshop-saved') return;
          const url = new URL(location.href); url.searchParams.set('template',detail.template_id);
          url.searchParams.delete('store');
          history.replaceState(null,'',url);
        }});
      await panel.connect();
      window.addEventListener('pagehide',() => panel.destroy(),{once:true});
    } catch (error) { root.textContent = window.PortalI18n.message(error.message || error); }
  }
  document.addEventListener('DOMContentLoaded',() => void start(),{once:true});
})();
