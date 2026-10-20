(function () {
  "use strict";
  if (window.PortalText.hasTranslator()) return;

  const KEY = window.PortalCompatibility.identifier("language");
  const COOKIE = "portal_language";
  const catalog = window.__PORTAL_TRANSLATIONS__ || {};
  const htmlCache = new WeakMap();
  const markupTemplates = new Map();
  const staticNodes = [];
  const staticAttributes = [];
  const staticTexts = [];
  const registeredStatic = new WeakSet();
  let messagePatterns;
  let fragmentCatalog;
  const normalize = window.PortalText.normalize;
  let preference = normalize(window.__PORTAL_LANGUAGE__ || readPreference());

  function readPreference() {
    try { return localStorage.getItem(KEY) || "auto"; } catch { return "auto"; }
  }
  function locale() {
    if (preference === "original") return "zh-CN";
    if (preference !== "auto") return preference;
    for (const language of navigator.languages || [navigator.language]) {
      if (/^zh(?:-|$)/i.test(language)) return "zh-CN";
      if (/^en(?:-|$)/i.test(language)) return "en";
    }
    return "zh-CN";
  }
  function pickerValue() { return preference === "auto" ? locale() : preference; }
  function requestLanguage() { return preference === "original" ? "original" : locale(); }
  function t(source, values = {}) {
    const input = String(source ?? "");
    const key = input.trim();
    if (!key) return input;
    const translated = preference === "original" ? key : (catalog[key]?.[locale()] ?? key);
    // One replacement pass prevents placeholder-like user content being interpreted.
    const result = translated.replace(/\{p(\d+)\}/g, (match, index) =>
      Object.prototype.hasOwnProperty.call(values, "p" + index) ? String(values["p" + index] ?? "") : match);
    return input.slice(0, input.length - input.trimStart().length) + result
      + input.slice(input.trimEnd().length);
  }
  function text(strings, ...values) {
    const source = strings.reduce((result, part, index) => result + (index ? `{p${index - 1}}` : "") + part, "");
    return t(source, Object.fromEntries(values.map((value, index) => ["p" + index, value])));
  }
  function message(source) {
    const value = String(source ?? "");
    if (preference === "original") return value;
    if (catalog[value]) return t(value);
    if (!messagePatterns) {
      messagePatterns = Object.keys(catalog).filter(key => /\{p\d+\}/.test(key)).map(key => {
        const fields = [...key.matchAll(/\{(p\d+)\}/g)].map(match => match[1]);
        // A CJK character is as specific as two Latin letters, so a short Chinese
        // phrase such as "指令名无效：{p0}" still qualifies as a message shape.
        const literal = [...key.replace(/\{p\d+\}/g, "")].reduce((total, character) => total + (character.codePointAt(0) >= 0x2e80 ? 2 : 1), 0);
        if (literal < 8 || new Set(fields).size !== fields.length) return null;
        const escaped = key.split(/(\{p\d+\})/).map(part => /^\{p\d+\}$/.test(part) ? "([\\s\\S]*?)" : part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("");
        return { key, fields, pattern: new RegExp("^" + escaped + "$"), length: literal };
      }).filter(Boolean).sort((left, right) => right.length - left.length);
    }
    for (const item of messagePatterns) {
      const match = item.pattern.exec(value);
      if (match) return t(item.key, Object.fromEntries(item.fields.map((field, index) => [field, match[index + 1]])));
    }
    return value;
  }
  function escapeText(value) {
    return String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  // A phrase's meaning does not depend on its expression position in a
  // larger HTML template. Renumber display placeholders locally for fallback.
  function fragment(source) {
    if (catalog[source.trim()]) return t(source);
    const fields = [];
    const normalized = source.replace(/\{(p\d+)\}/g, (_match, field) => {
      if (!fields.includes(field)) fields.push(field);
      return `{p${fields.indexOf(field)}}`;
    });
    if (!fields.length) return t(source);
    if (!fragmentCatalog) {
      fragmentCatalog = new Map();
      for (const [key, translations] of Object.entries(catalog)) {
        const indices = [];
        const renumber = value => value.replace(/\{(p\d+)\}/g, (match, field) => {
          const index = indices.indexOf(field);
          return index < 0 ? match : `{p${index}}`;
        });
        key.replace(/\{(p\d+)\}/g, (_match, field) => { if (!indices.includes(field)) indices.push(field); });
        if (indices.length) fragmentCatalog.set(renumber(key), Object.fromEntries(
          Object.entries(translations).map(([language, value]) => [language, renumber(value)])));
      }
    }
    const translated = fragmentCatalog.get(normalized.trim())?.[locale()];
    if (!translated) return t(source);
    return source.slice(0, source.length - source.trimStart().length)
      + translated.replace(/\{p(\d+)\}/g, (match, index) => fields[index] ? `{${fields[index]}}` : match)
      + source.slice(source.trimEnd().length);
  }
  function html(strings, ...values) {
    // Original mode preserves literal markup as well as its mixed-language copy.
    if (preference === "original") return strings.reduce((result, part, index) =>
      result + (index ? String(values[index - 1] ?? "") : "") + part, "");
    const dynamicTags = [...strings.join("PORTALI18N").matchAll(/<\/?PORTALI18N/g)].length;
    let cached = dynamicTags ? null : htmlCache.get(strings);
    if (!cached || cached.locale !== locale()) {
      const source = strings.reduce((result, part, index) => result + (index ? `PORTALI18NSLOT${index - 1}END` : "") + part, "")
        .replace(/(<\/?)PORTALI18NSLOT(\d+)END/g, (match, prefix, index) =>
          /^[a-z][a-z0-9-]*$/i.test(String(values[index])) ? prefix + values[index] : match);
      const skeleton = source.replace(/PORTALI18NSLOT\d+END/g, (slot, offset) => {
        const prefix = source.slice(0, offset);
        const rawTag = [...prefix.matchAll(/<\/?(textarea|script|style|title)\b[^>]*>/gi)].at(-1);
        const inRawText = rawTag && !rawTag[0].startsWith("</");
        return inRawText || prefix.lastIndexOf("<") > prefix.lastIndexOf(">") ? slot : `<!--${slot}-->`;
      });
      const template = document.createElement("template");
      // Parsing only the literal skeleton keeps every dynamic value outside translation.
      const leading = source.trimStart().match(/^<(tr|td|th|thead|tbody|tfoot|col|caption|option)\b/i)?.[1]?.toLowerCase();
      const wrappers = { tr: ["<table><tbody>", "</tbody></table>", "tbody"], td: ["<table><tbody><tr>", "</tr></tbody></table>", "tr"], th: ["<table><tbody><tr>", "</tr></tbody></table>", "tr"], thead: ["<table>", "</table>", "table"], tbody: ["<table>", "</table>", "table"], tfoot: ["<table>", "</table>", "table"], col: ["<table><colgroup>", "</colgroup></table>", "colgroup"], caption: ["<table>", "</table>", "table"], option: ["<select>", "</select>", "select"] };
      const wrapper = wrappers[leading];
      template.innerHTML = wrapper ? wrapper[0] + skeleton + wrapper[1] : skeleton;
      const root = wrapper ? template.content.querySelector(wrapper[2]) : template.content;
      for (const parent of [root, ...root.querySelectorAll("*")]) {
        if (parent.closest?.("script,style,pre,code,textarea,[translate='no']")) continue;
        let group = [];
        const flush = () => {
          if (!group.length) return;
          const original = group.map(node => node.data).join("").replace(/PORTALI18NSLOT(\d+)END/g, "{p$1}");
          const translated = fragment(original).replace(/\{p(\d+)\}/g, "PORTALI18NSLOT$1END");
          group[0].before(document.createTextNode(translated));
          group.forEach(node => node.remove());
          group = [];
        };
        for (const node of [...parent.childNodes]) {
          if (node.nodeType === Node.TEXT_NODE || (node.nodeType === Node.COMMENT_NODE && /^PORTALI18NSLOT\d+END$/.test(node.data))) group.push(node);
          else flush();
        }
        flush();
      }
      for (const element of root.querySelectorAll("*")) {
        if (element.closest("[translate='no']")) continue;
        for (const name of ["title", "aria-label", "placeholder", "alt", "data-portal-tooltip", "data-panel-title"]) {
          if (!element.hasAttribute(name)) continue;
          const source = element.getAttribute(name).replace(/PORTALI18NSLOT(\d+)END/g, "{p$1}");
          element.setAttribute(name, fragment(source).replace(/\{p(\d+)\}/g, "PORTALI18NSLOT$1END"));
        }
      }
      const translated = (wrapper ? root.innerHTML : template.innerHTML)
        .replace(/<!--(PORTALI18NSLOT\d+END)-->/g, "$1")
        // DOM parsing lowercases attribute names and assigns empty values to
        // bare boolean attributes. Restore literal expression slots afterwards.
        .replace(/\b(?:portali18nslot\d+end)+=""/gi, match => match.slice(0, -3))
        .replace(/portali18nslot(\d+)end/gi, "PORTALI18NSLOT$1END");
      cached = { locale: locale(), html: translated };
      htmlCache.set(strings, cached);
    }
    return cached.html.replace(/PORTALI18NSLOT(\d+)END/g, (_match, index) => String(values[index] ?? ""));
  }
  function markup(source) {
    if (!markupTemplates.has(source)) markupTemplates.set(source, Object.freeze([source]));
    return html(markupTemplates.get(source));
  }
  function definition(value) {
    const fields = new Set(["title", "description", "label", "help", "text", "placeholder", "enabled_label", "disabled_label", "category_title", "owner_title", "purpose"]);
    function visit(item) {
      if (Array.isArray(item)) return item.map(visit);
      if (!item || typeof item !== "object") return item;
      return Object.fromEntries(Object.entries(item).map(([key, child]) => [key,
        fields.has(key) && typeof child === "string" ? t(child) : visit(child)]));
    }
    return visit(value);
  }
  function renderStatic(root = document) {
    if (root === document && document.readyState === "loading") return;
    // Keep only connected projections when a lazy panel is replaced or closed.
    for (const entries of [staticNodes, staticAttributes, staticTexts]) {
      for (let index = entries.length - 1; index >= 0; index--) {
        if (!entries[index][0].isConnected) {
          registeredStatic.delete(entries[index][0]);
          entries.splice(index, 1);
        }
      }
    }
    {
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_COMMENT);
      let marker;
      while ((marker = walker.nextNode())) {
        if (!marker.data.startsWith("portal-i18n:") || marker.nextSibling?.nodeType !== Node.TEXT_NODE) continue;
        if (registeredStatic.has(marker.nextSibling)) continue;
        try {
          staticNodes.push([marker.nextSibling, JSON.parse(marker.data.slice(12))]);
          registeredStatic.add(marker.nextSibling);
        } catch { /* Unrecognized comments are inert. */ }
      }
      for (const element of root.querySelectorAll("[data-portal-i18n-attrs], [data-portal-i18n-text]")) {
        if (registeredStatic.has(element)) continue;
        if (element.dataset.portalI18nAttrs) staticAttributes.push([element, JSON.parse(element.dataset.portalI18nAttrs)]);
        if (element.dataset.portalI18nText) staticTexts.push([element, JSON.parse(element.dataset.portalI18nText)]);
        registeredStatic.add(element);
      }
    }
    for (const [node, source] of staticNodes) if (node.isConnected) node.data = t(source);
    for (const [element, attrs] of staticAttributes) if (element.isConnected) {
      for (const [name, source] of Object.entries(attrs)) element.setAttribute(name, t(source));
    }
    for (const [element, source] of staticTexts) if (element.isConnected) element.textContent = t(source);
  }
  function apply(mode) {
    preference = normalize(mode);
    document.documentElement.lang = locale();
    document.documentElement.dataset.portalLanguagePreference = preference;
    try { localStorage.setItem(KEY, preference); } catch { /* Cookies still carry the preference. */ }
    document.cookie = `${COOKIE}=${preference}; Path=/; Max-Age=31536000; SameSite=Lax${location.protocol === "https:" ? "; Secure" : ""}`;
    window.PortalI18n.renderStatic();
    document.querySelectorAll("select[data-portal-language]").forEach(select => {
      select.value = pickerValue();
      select.setAttribute("aria-label", t("Language"));
      const label = select.closest(".portalLanguagePicker")?.querySelector("span");
      if (label) label.textContent = t("Language");
      const original = select.querySelector('option[value="original"]');
      if (original) original.textContent = "Whatever";
    });
    window.dispatchEvent(new CustomEvent("portal:language-changed", { detail: { preference, locale: locale() } }));
    return locale();
  }
  async function set(mode, { reload = true } = {}) {
    const next = normalize(mode);
    if (window.PortalIdentity?.peek?.()?.username) {
      await window.PortalIdentity.savePreferences({ appearance: { language: next } }, { immediate: true });
    }
    window.PortalI18n.apply(next);
    if (reload) location.reload();
  }
  function picker() {
    return `<label class="portalLanguagePicker"><span>${escapeText(t("Language"))}</span><select data-portal-language aria-label="${escapeText(t("Language"))}"><option value="original">Whatever</option><option value="zh-CN">中文</option><option value="en">English</option></select></label>`;
  }
  window.PortalText.install(Object.freeze({ t, text, html, markup, definition, message, locale, requestLanguage, normalize, preference: pickerValue, apply, set, picker, renderStatic }));
  document.documentElement.lang = locale();
  function mount() {
    window.PortalI18n.renderStatic();
    // Only explicit public headers own a top-level picker. Authenticated
    // navigation can mount later and keeps its picker inside the user menu.
    const header = document.querySelector(".portalNavTop[data-portal-public-language]");
    if (header && !header.querySelector("[data-portal-language]")) header.insertAdjacentHTML("beforeend", window.PortalI18n.picker());
    document.querySelectorAll("select[data-portal-language]").forEach(select => { select.value = pickerValue(); });
  }
  document.addEventListener("change", async event => {
    const select = event.target.closest?.("select[data-portal-language]");
    if (!select) return;
    select.disabled = true;
    try { await set(select.value, { reload: Boolean(window.PortalIdentity?.peek?.()?.username) }); }
    catch (error) {
      select.value = pickerValue();
      window.PortalUI?.toast?.(error.message || t("Unable to save setting."), "error");
    } finally { select.disabled = false; }
  });
  window.addEventListener("portal:identity-changed", event => {
    const language = event.detail?.identity?.preferences?.appearance?.language;
    if (language !== undefined) window.PortalI18n.apply(language);
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount, { once: true });
  else mount();
})();
