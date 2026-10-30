(() => {
  'use strict';

  if (window.LineageTemplatesDialogs) return;

  // The dialogs of the template shelf: opening one with its header, form
  // and footer, showing errors, running work while its buttons are
  // disabled, and closing it with whatever it holds.

  const DEPENDENCIES = Object.freeze([
    'esc', 'pigeonHtml', 't', 'templatesState',
  ]);

  function createDialogs(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`LineageTemplatesDialogs is missing: ${missing.join(', ')}`);
    const {
      esc, pigeonHtml, t, templatesState,
    } = dependencies;

    const dialogs = new Set();

    function dialog(type, title, template = null) {
      const root = document.createElement('div');
      root.className = 'portalDetailOverlay lineageDialogOverlay templateOverlay';
      root.dataset.templateDialog = type;
      root.hidden = true;
      root.innerHTML = `<section class="portalDetailSurface templateSurface templateSurface-${type}" role="dialog" aria-modal="true" aria-label="${esc(title)}" data-portal-detail-surface>
        <header class="portalDetailHeader templateDialogHeader"><div class="templateDialogHeading">${template ? pigeonHtml(template, 'templateGlyph templateHeaderGlyph') : ''}<div><span class="portalDetailEyebrow">TEMPLATE</span><h2>${esc(title)}</h2></div></div>
          <button type="button" class="portalDetailClose" data-action="close" aria-label="${esc(t('关闭'))}">×</button></header>
        <form class="templateForm"><div class="templateDialogContent"></div><p class="templateError" role="alert" hidden></p><footer class="portalDetailFooter templateDialogFooter"></footer></form></section>`;
      document.body.append(root);
      const cleanups = [];
      let closed = false;
      const controller = window.PortalUI.createDialogController({ modal: root, openClass: 'portalDialogOpen', bodyClass: 'agentLineageModalOpen', escapeClose: true, backdropClose: false, requestClose: () => result.close(),
        onOpen: () => { root.hidden = false; }, onClose: () => { root.hidden = true; } });
      const result = {
        root, content: root.querySelector('.templateDialogContent'), footer: root.querySelector('footer'), form: root.querySelector('form'), cleanups,
        error(error) { const node = root.querySelector('.templateError'); node.hidden = !error; node.textContent = error ? window.PortalI18n.message(error.message || error) : ''; },
        close() { if (closed) return; closed = true; controller.close(); cleanups.forEach(cleanup => cleanup()); controller.destroy?.(); root.remove(); dialogs.delete(result); if (templatesState.editor === result) templatesState.editor = null; if (templatesState.application === result) templatesState.application = null; },
        async busy(work) { if (result.working) return; result.working = true; result.error(null); const buttons = [...root.querySelectorAll('footer button')]; buttons.forEach(item => { item.disabled = true; }); try { await work(); } catch (error) { result.error(error); } finally { result.working = false; buttons.forEach(item => { item.disabled = false; }); } },
      };
      root.addEventListener('click', event => { if (event.target.closest('[data-action="close"]')) result.close(); });
      // Escape closes through our owner too, so Monaco and pending previews are disposed.
      root.addEventListener('keydown', event => { if (event.key === 'Escape' && !event.defaultPrevented) { event.stopPropagation(); result.close(); } });
      dialogs.add(result);
      controller.open();
      return result;
    }

    return {
      dialog, dialogs,
    };
  }

  window.LineageTemplatesDialogs = Object.freeze({ createDialogs, dependencies: DEPENDENCIES });
})();
