(function () {
  "use strict";
  if (window.PortalText) return;

  // Original-text rendering is available before the optional translator loads.
  // HTML slots follow the existing contract: callers escape untrusted values.
  const interpolate = (strings, ...values) => strings.reduce((result, part, index) =>
    result + (index ? String(values[index - 1] ?? "") : "") + part, "");
  const clone = value => Array.isArray(value) ? value.map(clone)
    : value && typeof value === "object" ? Object.fromEntries(Object.entries(value).map(([key, child]) => [key, clone(child)]))
      : value;
  const locale = () => /^en(?:-|$)/i.test(document.documentElement.lang) ? "en" : "zh-CN";
  const normalize = value => ["original", "auto", "zh-CN", "en"].includes(value) ? value : "auto";
  const original = {
    t: (source, values = {}) => String(source ?? "").replace(/\{p(\d+)\}/g, (match, index) =>
      Object.prototype.hasOwnProperty.call(values, "p" + index) ? String(values["p" + index] ?? "") : match),
    text: interpolate,
    html: interpolate,
    markup: source => String(source ?? ""),
    definition: clone,
    message: source => String(source ?? ""),
    locale,
    requestLanguage: () => "original",
    normalize,
    preference: () => "original",
    apply: locale,
    set: async () => { throw new Error("翻译模块不可用，当前显示原文，请刷新后重试。"); },
    picker: () => "",
    renderStatic: () => {},
  };
  let translator;
  let warned = false;
  const facade = Object.fromEntries(Object.entries(original).map(([name, fallback]) => [name, (...args) => {
    if (!translator) return fallback(...args);
    // Preference persistence errors must still reach the settings UI.
    if (name === "set") return translator.set(...args);
    try { return translator[name](...args); }
    catch (error) {
      if (!warned) {
        warned = true;
        console.warn("Portal translation unavailable; displaying original text.", error);
      }
      return fallback(...args);
    }
  }]));
  window.PortalI18n = Object.freeze(facade);
  window.PortalText = Object.freeze({
    normalize,
    hasTranslator: () => Boolean(translator),
    install(api) {
      for (const name of Object.keys(original)) {
        if (typeof api[name] !== "function") throw new TypeError(`Missing translation method: ${name}`);
      }
      translator = api;
    },
  });
})();
