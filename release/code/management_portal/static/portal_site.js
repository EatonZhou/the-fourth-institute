(function (site) {
  "use strict";
  // Public data supplied per response by management_common.site_config.
  // The browser does not own a second default or a persisted configuration.
  site = Object.freeze({
    site_name: site.site_name,
    storage_namespace: site.storage_namespace,
    paths: Object.freeze(Object.fromEntries(
      ["share_root", "staging_root", "test_results_root"]
        .filter(key => Object.hasOwn(site.paths || {}, key))
        .map(key => [key, site.paths[key]])
    )),
    compatibility: Object.freeze({
      browser_identifiers: Object.freeze({}),
      format_namespaces: Object.freeze([]),
    }),
  });
  window.__PORTAL_SITE__ = site;
  let currentTitle = null;
  const name = () => site.site_name;
  const translate = source => window.PortalI18n?.t(source) ?? source;
  const title = (prefix = "", separator = " — ") => prefix ? `${prefix}${separator}${name()}` : name();

  function renderTitle() {
    const element = document.querySelector("title[data-portal-site-title]");
    if (currentTitle) {
      const prefix = typeof currentTitle.prefix === "function" ? currentTitle.prefix() : currentTitle.prefix;
      document.title = title(prefix, currentTitle.separator);
    } else if (element) {
      const separator = element.dataset.portalSiteSeparator ?? " — ";
      const parts = JSON.parse(element.dataset.portalSiteTitle);
      element.textContent = title(parts.map(translate).join(separator), separator);
    }
  }
  function setTitle(prefix, separator = " — ") {
    currentTitle = { prefix, separator };
    renderTitle();
  }
  function apply(root = document) {
    root.querySelectorAll("[data-portal-site-name]").forEach(element => { element.textContent = name(); });
    renderTitle();
  }
  window.PortalSite = Object.freeze({ name, title, setTitle, apply });
  window.addEventListener("portal:language-changed", () => apply());
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => apply(), { once: true });
  else apply();
})
