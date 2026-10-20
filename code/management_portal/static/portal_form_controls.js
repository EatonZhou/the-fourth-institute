(function (global) {
  "use strict";

  if (global.PortalFormControls) return;

  const searchFieldInstances = new WeakMap();

  const elementOf = ref => window.PortalUIPrimitives.elementOf(ref);

  function bindFilterDropdown(options = {}) {
    const button = elementOf(options.button);
    const panel = elementOf(options.panel);
    if (!button || !panel) return () => {};
    if (!global.PortalOverlay) {
      throw new Error("PortalOverlay must be loaded before PortalFormControls popovers.");
    }
    const openClass = options.openClass || "open";
    const popover = global.PortalOverlay.bindPopover({
      trigger: button,
      panel,
      anchor: options.anchor,
      boundary: options.boundary,
      placement: options.placement || "bottom-start",
      margin: options.margin,
      gap: options.gap,
      closeOnEscape: true,
      closeOnOutside: true,
      onOpen: event => {
        panel.classList.add(openClass);
        panel.setAttribute("aria-hidden", "false");
        options.onToggle?.(true, event);
      },
      onClose: event => {
        panel.classList.remove(openClass);
        panel.setAttribute("aria-hidden", "true");
        options.onToggle?.(false, event);
        options.onClose?.(event);
      },
    });
    const onButtonClick = event => {
      event.stopPropagation();
      popover.toggle(event);
    };
    button.addEventListener("click", onButtonClick);
    const dispose = () => {
      button.removeEventListener("click", onButtonClick);
      popover.destroy();
    };
    dispose.open = context => popover.open(context);
    dispose.close = context => popover.close(context);
    dispose.toggle = context => popover.toggle(context);
    dispose.isOpen = () => popover.isOpen();
    return dispose;
  }

  function normalizeFilterSummaryItems(value) {
    return (Array.isArray(value) ? value : [])
      .map(item => {
        if (item === undefined || item === null || item === "") return null;
        if (typeof item !== "object") return { label: "", value: String(item) };
        const label = String(item.label || "").trim();
        const itemValue = String(item.value ?? item.text ?? "").trim();
        return itemValue ? { label, value: itemValue } : null;
      })
      .filter(Boolean);
  }

  function bindFilterSummary(options = {}) {
    const target = elementOf(options.target);
    if (!target) return { sync() {}, destroy() {} };
    const root = document.createElement("div");
    root.className = "portalFilterSummary";
    root.setAttribute("aria-live", "polite");
    target.replaceChildren(root);

    const sync = () => {
      const items = normalizeFilterSummaryItems(
        typeof options.items === "function" ? options.items() : options.items,
      );
      const active = typeof options.isActive === "function"
        ? Boolean(options.isActive())
        : items.length > 0;
      target.classList.toggle("is-empty", !active);
      root.hidden = !active;
      root.replaceChildren();
      if (!active) return;

      const title = document.createElement("span");
      title.className = "portalFilterSummaryTitle";
      title.textContent = String(options.title || window.PortalI18n.t("当前筛选"));
      root.appendChild(title);

      const chips = document.createElement("span");
      chips.className = "portalFilterSummaryChips";
      const visibleItems = items.length ? items : [{ label: "", value: "已应用" }];
      visibleItems.forEach(item => {
        const chip = document.createElement("span");
        chip.className = "portalFilterSummaryChip";
        if (item.label) {
          const label = document.createElement("span");
          label.className = "portalFilterSummaryChipLabel";
          label.textContent = `${item.label}：`;
          chip.appendChild(label);
        }
        chip.appendChild(document.createTextNode(item.value));
        chips.appendChild(chip);
      });
      root.appendChild(chips);

      const clear = document.createElement("button");
      clear.className = "portalFilterSummaryClear";
      clear.type = "button";
      clear.textContent = String(options.clearLabel || window.PortalI18n.t("清除"));
      clear.title = String(options.clearTitle || window.PortalI18n.t("清除全部筛选"));
      clear.setAttribute("aria-label", clear.title);
      clear.addEventListener("click", event => {
        const result = options.onClear?.(event);
        Promise.resolve(result).finally(() => queueMicrotask(sync));
      });
      root.appendChild(clear);
    };

    sync();
    return {
      element: root,
      sync,
      destroy() {
        target.replaceChildren();
        target.classList.remove("is-empty");
      },
    };
  }

  function isClearableInputCandidate(input) {
    if (!(input instanceof HTMLInputElement)) return false;
    if (input.dataset.clearableInput === "0") return false;
    if (input.disabled) return false;
    const type = (input.getAttribute("type") || "text").toLowerCase();
    if (!["text", "search"].includes(type)) return false;
    if (input.dataset.clearableInput === "1") return true;
    if (type === "search") return true;
    const labelText = Array.from(input.labels || []).map(label => label.textContent || "").join(" ");
    const context = input.closest("[data-search-control]");
    if (context) return true;
    const haystack = [
      input.id,
      input.name,
      typeof input.className === "string" ? input.className : "",
      input.placeholder,
      input.getAttribute("aria-label"),
      labelText,
    ].join(" ").toLowerCase();
    return /(^|[\s_-])(search|query|filter|keyword|find)([\s_-]|$)/.test(haystack) || /搜索|筛选|关键词|查找/.test(haystack);
  }

  function enhanceClearableInput(input) {
    if (!isClearableInputCandidate(input)) return;
    const existingWrapper = input.closest(".portalClearableInput");
    if (existingWrapper) {
      const existingButton = existingWrapper.querySelector(".portalClearableInputBtn");
      if (existingButton) existingButton.hidden = false;
      return;
    }

    const computed = global.getComputedStyle(input);
    const wrapper = document.createElement("span");
    wrapper.className = "portalClearableInput";
    if (computed.flexGrow !== "0" || computed.flexShrink !== "1" || computed.flexBasis !== "auto") {
      wrapper.style.flex = computed.flex;
    }
    if (computed.minWidth && computed.minWidth !== "0px") wrapper.style.minWidth = computed.minWidth;
    if (computed.maxWidth && computed.maxWidth !== "none") wrapper.style.maxWidth = computed.maxWidth;
    if (computed.justifySelf && computed.justifySelf !== "auto") wrapper.style.justifySelf = computed.justifySelf;
    if (computed.alignSelf && computed.alignSelf !== "auto") wrapper.style.alignSelf = computed.alignSelf;

    const button = document.createElement("button");
    button.type = "button";
    button.className = "portalClearableInputBtn";
    button.setAttribute("aria-label", window.PortalI18n.t("清空搜索"));
    button.title = window.PortalI18n.t("清空");
    button.dataset.portalIcon = "close";

    input.parentNode.insertBefore(wrapper, input);
    wrapper.appendChild(input);
    wrapper.appendChild(button);
    input.classList.add("portalClearableInputField");

    button.addEventListener("click", () => {
      if (!input.value) return;
      input.value = "";
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      input.focus();
    });
  }

  function mountSearchField(root) {
    if (!(root instanceof Element)) return null;
    if (searchFieldInstances.has(root)) return searchFieldInstances.get(root);
    const input = root.querySelector("[data-portal-search-input], input");
    if (!(input instanceof HTMLInputElement)) return null;

    root.classList.add("portalSearchField");
    input.dataset.portalSearchInput = "1";
    input.dataset.clearableInput = "1";
    if (!input.getAttribute("type")) input.type = "search";
    root.querySelectorAll(":scope > label").forEach(label => label.classList.add("portalSrOnly"));

    let glyph = root.querySelector(":scope > [data-portal-search-icon]");
    if (!glyph) {
      glyph = global.PortalUIPrimitives?.createIcon?.("search", "portalSearchFieldIcon");
      if (glyph) {
        glyph.dataset.portalSearchIcon = "1";
        root.prepend(glyph);
      }
    } else {
      glyph.classList.add("portalSearchFieldIcon");
    }
    enhanceClearableInput(input);
    const controller = Object.freeze({ element: root, input, icon: glyph || null });
    searchFieldInstances.set(root, controller);
    return controller;
  }

  function initialize() {
    const start = () => {
      global.PortalUIPrimitives.registerEnhancer({
        id: "form-controls", selector: "[data-portal-search-field], input",
        enhance: node => node.matches("[data-portal-search-field]") ? mountSearchField(node) : enhanceClearableInput(node),
      });
    };
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
    else start();
  }

  global.PortalFormControls = Object.freeze({
    bindFilterDropdown,
    bindFilterSummary,
    mountSearchField,
  });
  initialize();
})(window);
