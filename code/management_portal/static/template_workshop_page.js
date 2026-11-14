(() => {
  'use strict';
  async function start() {
    const root = document.querySelector('#templateWorkshop');
    try {
      await window.PortalIdentity.load();
      await window.PortalHatgLoader.load();
      const params = new URLSearchParams(location.search);
      const workshop = window.LineageTemplateWorkshop.mount({root,lineageId:params.get('lineage') || '',templateId:params.get('template') || ''});
      await workshop.load();
      const chat = window.LineageTemplateWorkshopChat.mount({root,workshop});
      await chat.load();
      window.addEventListener('pagehide', () => { chat.destroy(); workshop.destroy(); }, {once:true});
    } catch (error) {
      const node = document.querySelector('#workshopError'); node.hidden = false;
      node.textContent = window.PortalI18n.message(error.message || error);
    }
  }
  document.addEventListener('DOMContentLoaded', () => void start(), {once:true});
})();
