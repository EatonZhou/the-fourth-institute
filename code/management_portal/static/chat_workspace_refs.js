(() => {
  if (window.PortalChatWorkspaceRefs) return;

  // The references a message carries: the Skills, Agents and resources a person
  // picks in the composer's reference popover, the count and summary shown beside
  // the composer, and the selection payload sent with the message.
  const DEPENDENCIES = Object.freeze([
    "escapeHtml",
  ]);

  function normalizeRefs(selection) {
    const refs = selection && typeof selection === "object" ? selection : {};
    const out = {};
    for (const key of ["skills", "agents", "resources"]) {
      if (refs[key] instanceof Set) out[key] = new Set(refs[key]);
      else out[key] = new Set(Array.isArray(refs[key]) ? refs[key] : []);
    }
    return out;
  }

  function refCount(selection) {
    const refs = normalizeRefs(selection);
    return (refs.skills?.size || 0)
      + (refs.agents?.size || 0)
      + (refs.resources?.size || 0);
  }

  function refsPayload(selection) {
    const refs = normalizeRefs(selection);
    const payload = {
      skills: [...refs.skills],
      agents: [...refs.agents],
    };
    const resources = [...refs.resources].map(ref => {
      if (!ref || typeof ref !== "object") return ref;
      return {
        address: String(ref.address || ref.resource_id || ref.id || ""),
      };
    }).filter(ref => typeof ref === "string" ? !!ref : !!ref.address);
    if (resources.length) payload.resources = resources;
    return payload;
  }

  function isThirdPartySkillOption(opt) {
    return String(opt?.id || "").startsWith("third-party/");
  }

  function selectedRefLabels(selection, contextOptions = {}) {
    const refs = normalizeRefs(selection);
    const out = [];
    for (const [key, label] of [["skills", window.PortalI18n.t("技能")], ["agents", "Agent"]]) {
      const map = new Map((Array.isArray(contextOptions[key]) ? contextOptions[key] : []).map(item => [String(item?.id || ""), item]));
      for (const ref of refs[key] || []) {
        const refObject = ref && typeof ref === "object" ? ref : null;
        const id = String(refObject?.id || refObject?.path || refObject?.name || ref || "");
        const item = id ? map.get(id) : null;
        const name = String(item?.name || refObject?.name || refObject?.path || refObject?.id || id || "");
        if (name) out.push(`${label}: ${name}`);
      }
    }
    for (const ref of refs.resources || []) {
      const refObject = ref && typeof ref === "object" ? ref : null;
      const address = String(refObject?.address || refObject?.resource_id || refObject?.id || ref || "");
      const status = String(refObject?.status || "").trim().toLowerCase();
      const title = String(
        status && status !== "resolved"
          ? address
          : refObject?.title || address || "",
      );
      if (title) out.push(window.PortalI18n.text`Resource: ${title}`);
    }
    return out;
  }

  function createRefs(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalChatWorkspaceRefs is missing: ${missing.join(", ")}`);
    const {
      escapeHtml,
    } = dependencies;

    const activeRefsPopoverBindings = new Set();

    function refsPanelHtml(options = {}) {
      return window.PortalI18n.html`<div class="refsSearch">
      <input type="search" class="refsSearchInput" data-refs-search
        aria-label="搜索引用" placeholder="搜索名称、ID、路径或描述"
        autocomplete="off" spellcheck="false" value="${escapeHtml(options.searchQuery || "")}" />
      </div><div data-refs-results>${refsResultsHtml(options)}</div>`;
    }

    function refsResultsHtml(options = {}) {
      const refs = normalizeRefs(options.selection || {});
      const scope = String(options.scope || "default");
      if (options.catalogStatus === "idle" || options.catalogStatus === "loading") {
        return window.PortalI18n.markup('<div class="refsEmpty" role="status">正在加载引用选项…</div>');
      }
      if (options.catalogStatus === "error") {
        return window.PortalI18n.html`<div class="refsEmpty" role="alert">引用选项加载失败，请重试。</div>
        <button type="button" class="portalButton is-compact" data-refs-retry="${escapeHtml(scope)}">重试</button>`;
      }
      const contextOptions = options.contextOptions || {};
      const collapsed = options.collapsed || {};
      const query = String(options.searchQuery || "").trim().toLocaleLowerCase();
      const skillOptions = Array.isArray(contextOptions.skills) ? contextOptions.skills : [];
      const thirdPartySkills = skillOptions.filter(isThirdPartySkillOption);
      const sharedSkills = skillOptions.filter(opt => !isThirdPartySkillOption(opt));
      const sectionHtml = (groupId, key, title, items) => {
        if (query) {
          items = items.filter(item => [item?.name, item?.id, item?.path, item?.description]
            .some(value => String(value || "").toLocaleLowerCase().includes(query)));
          if (!items.length) return "";
        }
        const isCollapsed = !query && !!collapsed[groupId];
        const body = !items.length
          ? window.PortalI18n.html`<div class="refsEmpty">无可选项</div>`
          : window.PortalI18n.html`<div class="refsOptions">
          ${items.map(item => {
            const id = String(item?.id || "");
            const name = String(item?.name || id);
            const titleText = [name, item?.path || "", item?.description || ""].filter(Boolean).join(" · ");
            return window.PortalI18n.html`<label class="refsOption" title="${escapeHtml(titleText)}">
              <input type="checkbox" data-portal-chat-ref="${escapeHtml(key)}" data-ref-scope="${escapeHtml(scope)}" value="${escapeHtml(id)}" ${refs[key]?.has(id) ? "checked" : ""} />
              <span>${escapeHtml(name)}</span>
            </label>`;
          }).join("")}
        </div>`;
        return window.PortalI18n.html`<div class="refsGroup" data-refs-group="${escapeHtml(groupId)}">
        <button type="button" class="refsGroupHead" data-refs-group-toggle="${escapeHtml(groupId)}" data-ref-scope="${escapeHtml(scope)}" aria-expanded="${isCollapsed ? "false" : "true"}" ${query ? "disabled" : ""}>
          <span class="refsGroupLabel">${escapeHtml(title)}</span>
          <span data-portal-icon="${isCollapsed ? "chevron-down" : "chevron-up"}" data-portal-icon-class="refsGroupChevron"></span>
        </button>
        ${isCollapsed ? "" : body}
      </div>`;
      };
      const groups = [
        sectionHtml("thirdPartySkills", "skills", "THIRD-PARTY SKILLS", thirdPartySkills),
        sectionHtml("skills", "skills", "技能", sharedSkills),
        sectionHtml("agents", "agents", "Agent", Array.isArray(contextOptions.agents) ? contextOptions.agents : []),
      ].filter(Boolean).join(window.PortalI18n.markup('<hr class="refsDivider" />'));
      return window.PortalI18n.html`${groups || window.PortalI18n.markup('<div class="refsEmpty" role="status">没有匹配的引用</div>')}
      <div class="refsActions">
        <button type="button" class="portalButton is-compact" data-refs-clear="${escapeHtml(scope)}">Clear</button>
        <button type="button" class="portalButton is-compact" data-refs-done="${escapeHtml(scope)}">Done</button>
      </div>`;
    }

    function refsControlHtml(options = {}) {
      const scope = String(options.scope || "default");
      const count = refCount(options.selection || {});
      return window.PortalI18n.html`<div class="refsWrap">
      <button class="portalButton is-compact ${count ? "is-active" : ""}" type="button" data-refs-toggle="${escapeHtml(scope)}" aria-label="选择引用的 Skills 和 Agents" title="选择引用的 Skills 和 Agents">Refs${count ? ` ${count}` : ""}</button>
      <div class="refsPanel" data-refs-panel="${escapeHtml(scope)}" role="dialog" aria-label="选择 Chat 引用" aria-hidden="true">${refsPanelHtml(options)}</div>
    </div>`;
    }

    function bindRefsControl(root, options = {}) {
      if (!root) return null;
      const scope = String(options.scope || "default");
      const panel = root.querySelector(`[data-refs-panel="${CSS.escape(scope)}"]`);
      const toggle = root.querySelector(`[data-refs-toggle="${CSS.escape(scope)}"]`);
      if (!panel || !toggle) return null;
      const existing = panel.__portalRefsPopoverBinding || toggle.__portalRefsPopoverBinding;
      if (existing) return existing.update(options);

      let currentOptions = options;
      const getSelection = () => normalizeRefs(
        currentOptions.getSelection ? currentOptions.getSelection() : currentOptions.selection || {},
      );
      const setSelection = refs => currentOptions.setSelection?.(normalizeRefs(refs));
      const getCollapsed = () => currentOptions.getCollapsed
        ? (currentOptions.getCollapsed() || {})
        : (currentOptions.collapsed || {});
      const setCollapsed = collapsed => currentOptions.setCollapsed?.(collapsed);
      const searchInput = panel.querySelector("[data-refs-search]");
      let searchQuery = searchInput.value;
      let binding = null;

      const popover = window.PortalOverlay.bindPopover({
        trigger: toggle,
        panel,
        placement: "top-end",
        boundary: root,
        ariaHaspopup: "dialog",
        portal: true,
        closeOnOutside: true,
        closeOnEscape: true,
        onOpen: () => {
          activeRefsPopoverBindings.forEach(other => {
            if (other !== binding) other.close();
          });
          currentOptions.onLoad?.();
          binding.refresh();
          panel.classList.add("open");
          panel.setAttribute("aria-hidden", "false");
        },
        onClose: () => {
          panel.classList.remove("open");
          panel.setAttribute("aria-hidden", "true");
        },
      });

      const bindPanelContents = () => {
        panel.querySelector(`[data-refs-retry="${CSS.escape(scope)}"]`)?.addEventListener("click", () => {
          currentOptions.onLoad?.();
        });
        panel.querySelectorAll(`[data-refs-group-toggle][data-ref-scope="${CSS.escape(scope)}"]`).forEach(btn => {
          btn.addEventListener("click", event => {
            event.preventDefault();
            const groupId = btn.dataset.refsGroupToggle || "";
            const collapsed = { ...getCollapsed(), [groupId]: !getCollapsed()[groupId] };
            setCollapsed(collapsed);
            binding.refresh();
          });
        });
        panel.querySelectorAll(`[data-portal-chat-ref][data-ref-scope="${CSS.escape(scope)}"]`).forEach(input => {
          input.addEventListener("change", () => {
            const refs = getSelection();
            const key = input.dataset.portalChatRef || "";
            if (!refs[key]) refs[key] = new Set();
            if (input.checked) refs[key].add(input.value);
            else refs[key].delete(input.value);
            setSelection(refs);
            renderRefsSummary(root, {
              scope,
              selection: refs,
              contextOptions: currentOptions.contextOptions || {},
            });
            currentOptions.onChange?.(refs);
          });
        });
        panel.querySelector(`[data-refs-done="${CSS.escape(scope)}"]`)?.addEventListener("click", event => {
          binding.close(event);
        });
        panel.querySelector(`[data-refs-clear="${CSS.escape(scope)}"]`)?.addEventListener("click", () => {
          const refs = { skills: new Set(), agents: new Set(), resources: new Set() };
          setSelection(refs);
          binding.refresh();
          currentOptions.onChange?.(refs);
        });
      };

      const refreshPanel = () => {
        panel.querySelector("[data-refs-results]").innerHTML = refsResultsHtml({
          scope,
          selection: getSelection(),
          searchQuery,
          contextOptions: currentOptions.contextOptions || {},
          catalogStatus: currentOptions.catalogStatus,
          collapsed: getCollapsed(),
        });
        bindPanelContents();
        renderRefsSummary(root, {
          scope,
          selection: getSelection(),
          contextOptions: currentOptions.contextOptions || {},
        });
        popover.reposition();
        requestAnimationFrame(() => popover.reposition());
      };

      const handleToggle = event => popover.toggle(event);
      const handleSearch = () => {
        searchQuery = searchInput.value;
        refreshPanel();
        panel.scrollTop = 0;
      };
      searchInput.addEventListener("input", handleSearch);
      toggle.addEventListener("click", handleToggle);
      binding = {
        panel,
        trigger: toggle,
        popover,
        update(nextOptions = {}) {
          currentOptions = nextOptions;
          return binding;
        },
        refresh() {
          refreshPanel();
          return binding;
        },
        open(context) {
          popover.open(context);
          return binding;
        },
        close(context) {
          popover.close(context);
          return binding;
        },
        isOpen: () => popover.isOpen(),
        destroy() {
          searchInput.removeEventListener("input", handleSearch);
          toggle.removeEventListener("click", handleToggle);
          activeRefsPopoverBindings.delete(binding);
          popover.destroy();
          delete panel.__portalRefsPopoverBinding;
          delete toggle.__portalRefsPopoverBinding;
        },
      };
      panel.__portalRefsPopoverBinding = binding;
      toggle.__portalRefsPopoverBinding = binding;
      activeRefsPopoverBindings.add(binding);
      bindPanelContents();
      renderRefsSummary(root, {
        scope,
        selection: getSelection(),
        contextOptions: currentOptions.contextOptions || {},
      });
      return binding;
    }

    function renderRefsSummary(root, options = {}) {
      const scope = String(options.scope || "default");
      const btn = root?.querySelector?.(`[data-refs-toggle="${CSS.escape(scope)}"]`);
      const summary = root?.querySelector?.(`[data-refs-summary="${CSS.escape(scope)}"]`);
      const labels = selectedRefLabels(options.selection || {}, options.contextOptions || {});
      if (btn) {
        btn.textContent = labels.length ? window.PortalI18n.text`Refs ${labels.length}` : "Refs";
        btn.classList.toggle("is-active", labels.length > 0);
      }
      if (!summary) return;
      summary.innerHTML = labels.length
        ? labels.slice(0, 4).map(label => window.PortalI18n.html`<span class="refsChip" title="${escapeHtml(label)}">${escapeHtml(label)}</span>`).join("") + (labels.length > 4 ? window.PortalI18n.html`<span class="refsChip">+${labels.length - 4}</span>` : "")
        : "";
    }

    return Object.freeze({
      normalizeRefs, refCount, refsPayload, selectedRefLabels, refsControlHtml, bindRefsControl,
      renderRefsSummary,
    });
  }

  window.PortalChatWorkspaceRefs = Object.freeze({
    createRefs, dependencies: DEPENDENCIES, normalizeRefs, refCount, refsPayload,
    selectedRefLabels,
  });
})();
