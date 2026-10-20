(() => {
  "use strict";

  if (window.PortalBoardShell) return;

  const element = ref => window.PortalUIPrimitives.elementOf(ref);

  function dateFallback(value, options = {}) {
    return options.fallback ?? (value == null ? "" : String(value));
  }

  function formatDate(value, options = {}) {
    const fallback = dateFallback(value, options);
    if (value === undefined || value === null || value === "") return fallback;
    if (options.unixSeconds && (!Number.isFinite(Number(value)) || Number(value) <= 0)) return fallback;
    return window.PortalUI.formatDateTime(value, {
      unixSeconds: options.unixSeconds,
      style: "calendar-date",
      shortYear: options.shortYear,
      invalidFallback: fallback,
    });
  }

  function formatDateTime(value, options = {}) {
    const fallback = dateFallback(value, options);
    if (value === undefined || value === null || value === "") return fallback;
    if (options.unixSeconds && (!Number.isFinite(Number(value)) || Number(value) <= 0)) return fallback;
    return window.PortalUI.formatDateTime(value, {
      unixSeconds: options.unixSeconds,
      style: "calendar-minute",
      shortYear: options.shortYear,
      separator: options.separator,
      invalidFallback: fallback,
    });
  }

  function formatBytes(value) {
    return window.PortalUI.formatBytes(value, {
      maximumUnit: "TB",
      precision: "compact",
    });
  }

  function setFilterActive(buttonRef, active) {
    const button = element(buttonRef);
    if (!button) return;
    button.classList.toggle("active", !!active);
    let dot = button.querySelector(".filter-active-dot");
    if (active && !dot) {
      dot = document.createElement("span");
      dot.className = "filter-active-dot";
      dot.setAttribute("aria-hidden", "true");
      button.appendChild(dot);
    } else if (!active && dot) {
      dot.remove();
    }
  }

  function optionRecord(item, options) {
    const value = String(typeof options.value === "function" ? options.value(item) : item?.value ?? item ?? "");
    const label = String(typeof options.label === "function" ? options.label(item) : item?.label ?? value);
    const count = typeof options.count === "function" ? options.count(item) : item?.count;
    return { item, value, label, count };
  }

  function appendOptionContent(label, record) {
    label.appendChild(document.createTextNode(` ${record.label}`));
    if (record.count === undefined || record.count === null || record.count === "") return;
    const count = document.createElement("span");
    count.className = "portalBoardFilterCount";
    count.textContent = `(${record.count})`;
    label.append(document.createTextNode(" "), count);
  }

  function renderCheckboxGroup(options = {}) {
    const container = element(options.container);
    if (!container) return;
    const records = (options.items || []).map(item => optionRecord(item, options));
    const values = records.map(record => record.value);
    const selected = options.selected instanceof Set ? new Set([...options.selected].map(String)) : null;
    container.replaceChildren();

    if (!records.length && options.emptyText) {
      const empty = document.createElement("span");
      empty.className = "portalBoardFilterEmpty";
      empty.textContent = String(options.emptyText);
      container.appendChild(empty);
      return;
    }

    if (options.showAll !== false) {
      const allLabel = document.createElement("label");
      allLabel.className = "filter-option";
      const allInput = document.createElement("input");
      allInput.type = "checkbox";
      allInput.value = options.allValue || "__all__";
      allInput.checked = selected === null || selected.size === records.length;
      allInput.indeterminate = selected !== null && selected.size > 0 && selected.size < records.length;
      allInput.addEventListener("change", event => {
        options.onChange?.(event.target.checked ? null : new Set(), event);
      });
      allLabel.appendChild(allInput);
      appendOptionContent(allLabel, {
        label: options.allLabel || window.PortalI18n.t("全选"),
        count: options.showAllCount === false ? null : records.length,
      });
      container.appendChild(allLabel);
    }

    records.forEach(record => {
      const label = document.createElement("label");
      label.className = "filter-option";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.value = record.value;
      input.checked = selected === null || selected.has(record.value);
      input.addEventListener("change", event => {
        const next = selected === null ? new Set(values) : new Set(selected);
        if (event.target.checked) next.add(record.value);
        else next.delete(record.value);
        options.onChange?.(next.size === records.length ? null : next, event, record.item);
      });
      label.appendChild(input);
      appendOptionContent(label, record);
      container.appendChild(label);
    });
  }

  function renderRadioGroup(options = {}) {
    const container = element(options.container);
    if (!container) return;
    const current = String(options.selected ?? "");
    container.replaceChildren();
    (options.items || []).map(item => optionRecord(item, options)).forEach(record => {
      const label = document.createElement("label");
      label.className = "filter-option";
      const input = document.createElement("input");
      input.type = "radio";
      input.name = String(options.name || "portal_board_filter");
      input.value = record.value;
      input.checked = record.value === current;
      input.addEventListener("change", event => {
        if (event.target.checked) options.onChange?.(record.value, event, record.item);
      });
      label.appendChild(input);
      appendOptionContent(label, record);
      container.appendChild(label);
    });
  }

  function create(options = {}) {
    const disposers = [];
    const api = window.PortalAPI.createClient(options.api || {});
    let filterOptions = null;
    let filterSummary = null;

    document.body?.classList.add("portalBoardShell");
    window.PortalNav?.mount?.();

    const controller = {
      api,
      formatBytes,
      formatDate: (value, formatOptions = {}) => formatDate(value, {
        ...(options.formats?.date || {}),
        ...formatOptions,
      }),
      formatDateTime: (value, formatOptions = {}) => formatDateTime(value, {
        ...(options.formats?.dateTime || {}),
        ...formatOptions,
      }),
      renderCheckboxGroup,
      renderRadioGroup,
      bindSearch(searchOptions = {}) {
        const input = element(searchOptions.input);
        if (!input) return () => {};
        const normalize = typeof searchOptions.normalize === "function"
          ? searchOptions.normalize
          : value => String(value || "");
        const onInput = event => searchOptions.onChange?.(normalize(input.value), event);
        input.addEventListener("input", onInput);
        const dispose = () => input.removeEventListener("input", onInput);
        disposers.push(dispose);
        return dispose;
      },
      bindFilter(nextOptions = {}) {
        filterOptions = nextOptions;
        filterSummary?.destroy?.();
        filterSummary = nextOptions.summaryTarget
          ? window.PortalFormControls.bindFilterSummary({
              target: nextOptions.summaryTarget,
              title: nextOptions.summaryTitle,
              items: nextOptions.summary,
              isActive: nextOptions.isActive,
              onClear: nextOptions.onClear,
            })
          : null;
        const dispose = window.PortalFormControls.bindFilterDropdown({
          button: nextOptions.button,
          panel: nextOptions.panel,
          wrapSelector: nextOptions.wrapSelector,
          onToggle: nextOptions.onToggle,
          onClose: nextOptions.onClose,
        });
        disposers.push(dispose);
        controller.syncFilterState();
        return dispose;
      },
      syncFilterState() {
        if (!filterOptions) return;
        const active = typeof filterOptions.isActive === "function"
          ? filterOptions.isActive()
          : !!filterOptions.active;
        setFilterActive(filterOptions.button, active);
        filterSummary?.sync?.();
      },
      destroy() {
        filterSummary?.destroy?.();
        disposers.splice(0).forEach(dispose => dispose());
      },
    };
    return controller;
  }

  window.PortalBoardShell = { create };
})();
