'use strict';

// Read the native link in the current document before the original action.
// This does not navigate, scroll, focus, wait for business readiness, or infer
// anything about a previous failed action. Use the existing browser observer
// to retain the action's original page/frame/document and request identities.
async function readNativeNavigationTarget(page, { moduleId }) {
  if (typeof moduleId !== 'string' || !/^[a-zA-Z0-9_.:-]{1,100}$/.test(moduleId)) {
    throw new TypeError('InvalidNativeNavigationModule');
  }
  return page.evaluate(moduleId => {
    const visible = element => {
      if (!element?.isConnected || !element.getClientRects().length) return false;
      for (let p = element; p; p = p.parentElement) {
        const style = getComputedStyle(p);
        if (p.hidden || p.inert || style.display === 'none' || style.visibility !== 'visible' || Number(style.opacity) === 0) return false;
      }
      return true;
    };
    const matches = [...document.querySelectorAll('[data-portal-nav] > a.portalModuleLink[data-module-link]')]
      .filter(link => link.dataset.moduleLink === moduleId);
    const candidates = matches.filter(visible);
    const result = {
      sampled_at: new Date().toISOString(), module_id: moduleId,
      document_pathname: location.pathname, match_count: matches.length,
      visible_count: candidates.length, target: null, ready_for_native_click: false,
    };
    if (candidates.length !== 1) return result;
    const link = candidates[0], rect = link.getBoundingClientRect();
    const x = rect.x + rect.width / 2, y = rect.y + rect.height / 2;
    const hit = document.elementFromPoint(x, y);
    const url = new URL(link.href);
    const sameOrigin = url.origin === location.origin && !url.username && !url.password;
    const disabled = link.getAttribute('aria-disabled') === 'true';
    const samePageContext = !link.target || link.target === '_self';
    result.target = {
      // Do not emit foreign URLs or credentials. Only the native same-origin
      // destination is needed by the caller's exact waitForURL predicate.
      href: sameOrigin ? url.href : null, same_origin: sameOrigin,
      pathname: sameOrigin ? url.pathname : null, label: link.textContent.trim(),
      disabled, same_page_context: samePageContext,
      in_viewport: rect.width > 0 && rect.height > 0 && x >= 0 && x < innerWidth && y >= 0 && y < innerHeight,
      hit: !!hit && (hit === link || link.contains(hit)),
    };
    result.ready_for_native_click = sameOrigin && samePageContext && !disabled && result.target.in_viewport && result.target.hit;
    return result;
  }, moduleId);
}

module.exports = { readNativeNavigationTarget };
