(() => {
  "use strict";

  const namespace = "lineage-workbench.workspaces";
  const esc = value => window.PortalUIPrimitives.escapeHtml(String(value ?? ""));
  const icon = name => window.PortalUIPrimitives.iconHtml(name);
  const button = (action, glyph, label, pressed, folder) => window.PortalI18n.html`<button type="button" class="portalButton is-icon is-quiet" data-workspace-action="${action}" title="${esc(label)}" aria-label="${esc(label)}" ${pressed === undefined ? "" : `aria-pressed="${pressed}"`} ${folder ? `data-workspace-directory="${esc(folder)}"` : ""}>${icon(glyph)}</button>`;
  const directoryKey = path => `directory:${path}`;

  function mount({ root, onOpen, onRefresh }) {
    let items = [];
    let locks = [];
    let resourceItem = null;
    let residentId = "";
    let residentItems = [];
    let residentNote = "";
    let residentPending = false;
    let loaded = false;
    let busy = false;
    let destroyed = false;
    let error = "";
    let selected = "";
    let selectionRevision = 0;
    let revision = 0;
    const closed = new Set();
    const expanded = new Map();
    const directories = new Map();
    const events = new AbortController();
    root.classList.add("portalPersonalWorkspaces");
    root.innerHTML = window.PortalI18n.html`<div class="paneHead"><div class="paneTitle">Workspace</div><div class="paneHeadActions">${button("refresh", "refresh", window.PortalI18n.t("重新读取当前 Resident 的工作区、个人工作区和已展开的文件夹"))}${button("create", "plus", window.PortalI18n.t("新建个人工作区，用于整理多个文件夹"))}</div></div><div class="paneBody portalPersonalWorkspaceList"></div>`;
    const list = root.querySelector(".portalPersonalWorkspaceList");
    const membership = key => {
      if (!expanded.has(key)) expanded.set(key, new Set());
      return expanded.get(key);
    };
    const note = text => window.PortalI18n.html`<div class="portalFileInlineNote" role="status">${esc(text)}</div>`;
    const report = failure => { error = String(failure?.message || failure); render(); };
    const directoryLock = path => locks.find(item => item.value.folders[0] === path);
    const lockButton = path => button("lock-directory", "lock", directoryLock(path)
      ? window.PortalI18n.t("解锁此目录，不再单独保留；当前 Resident 绑定或个人分组中的目录仍会显示")
      : window.PortalI18n.t("锁定此目录，切换 Resident 后仍保留，刷新或重新登录后也可继续使用"), Boolean(directoryLock(path)), path);
    const visibleItems = () => {
      const bound = residentItems.filter(item => !resourceItem || item.root.workspace_id !== resourceItem.key);
      const current = [...bound, ...(resourceItem ? [resourceItem] : []), ...items];
      const paths = new Set(current.flatMap(item => item.value.folders));
      const retained = locks.filter(item => !paths.has(item.value.folders[0])).map(item => ({
        key: directoryKey(item.value.folders[0]), value: item.value, readOnly: true, source: "locked",
      }));
      return [...retained, ...current];
    };

    function resourceLocation(path) {
      const normalize = value => String(value || "").replaceAll("\\", "/").replace(/\/+$/, "");
      const target = normalize(path);
      const holding = [resourceItem?.root, ...residentItems.map(item => item.root), ...locks.map(item => item.value.resource_root)]
        .filter(item => item?.path && item.directory_id)
        .map(item => ({ root: item, base: normalize(item.path) }))
        .filter(item => target === item.base || target.startsWith(`${item.base}/`))
        .sort((a, b) => b.base.length - a.base.length)[0];
      // All registered roots use ReadResource's directory boundary and hidden
      // file filtering. They need no enclosing Workspace registration.
      if (!holding || !["resident_folder", "lineage_folder", "workspace"].includes(holding.root.root_kind)) return null;
      const relative = target.slice(holding.base.length).replace(/^\//, "");
      return { root: holding.root, relative,
        resourceAddress: `${holding.root.directory_id}/file${relative ? `/${relative}` : ""}` };
    }
    const previewRequest = path => {
      const location = resourceLocation(path);
      // Keep the existing editable path preview for Workspace files.
      return { path, source: "workspace", ...(location && location.root.root_kind !== "workspace"
        ? { resourceAddress: location.resourceAddress } : {}) };
    };

    function tree(path, depth, open, isRoot = false, resourceRoot = false) {
      const entry = { path, name: path.split("/").filter(Boolean).at(-1) || path, type: "directory", depth };
      const data = directories.get(path);
      const row = window.PortalFileTree.rowHtml(entry, {
        expanded: open, isLoading: () => data?.loading === true,
        getClasses: () => isRoot ? "is-personal-workspace-root" : "",
      });
      let html = isRoot
        ? window.PortalI18n.html`<div class="portalPersonalWorkspaceFolder" data-workspace-folder="${esc(path)}">${row}${resourceRoot ? "" : `<div class="portalPersonalWorkspaceFolderActions">${lockButton(path)}${button("remove-folder", "close", window.PortalI18n.t("从工作区移除此文件夹，不删除磁盘文件"))}</div>`}</div>`
        : row;
      if (!open.has(path)) return html;
      if (!data || data.loading) return html + note("正在读取文件夹…");
      if (data.error) html += note(`读取失败：${data.error}`);
      if (!data.children.length && !data.nextCursor && !data.error) return html + note("空文件夹");
      for (const child of data.children) {
        const childPath = `${data.path.replace(/\/$/, "")}/${child.name}`;
        html += child.type === "directory"
          ? tree(childPath, depth + 1, open)
          : window.PortalFileTree.rowHtml({ ...child, path: childPath, depth: depth + 1 }, {
            isSelected: item => item.path === selected,
          });
      }
      if (data.nextCursor || data.loadingMore || data.error) {
        const label = data.loadingMore ? "正在加载…" : data.error ? (data.nextCursor ? "重试加载更多" : "重试") : "加载更多";
        html += window.PortalI18n.html`<div class="portalFileInlineNote"><button type="button" class="portalButton is-compact"
          data-workspace-directory-page="${esc(path)}" aria-disabled="${!!data.loadingMore}">${window.PortalI18n.t(label)}</button></div>`;
      }
      return html;
    }

    function render() {
      if (destroyed) return;
      const visible = visibleItems();
      const directoriesPending = [...directories.values()].some(data => data.loading)
        || visible.some(item => !closed.has(item.key)
          && item.value.folders.some(path => membership(item.key).has(path) && !directories.has(path)));
      root.dataset.workspaceResidentId = residentId;
      root.setAttribute("aria-busy", String(busy || (!loaded && !error) || residentPending || directoriesPending));
      window.PortalFileTree.preserveViewport(list, () => {
        list.querySelectorAll("[data-workspace-tree]").forEach(window.PortalFileTree.dispose);
        list.innerHTML = (error ? note(error) : "") + (residentNote ? note(residentNote) : "") + (!loaded ? note("正在读取工作区…") : visible.length ? visible.map(item => {
          const open = !closed.has(item.key);
          const readOnly = item.readOnly === true;
          return window.PortalI18n.html`<section class="portalPersonalWorkspace portalNavSection" data-workspace-key="${esc(item.key)}" data-workspace-source="${esc(item.source || "personal")}">
            <div class="portalPersonalWorkspaceHeading portalNavSectionHead"><button type="button" class="portalPersonalWorkspaceToggle" data-workspace-action="toggle" aria-expanded="${open}" title="展开或收起 ${esc(item.value.name)}"><span class="portalNavSectionLabel">${esc(item.value.name)}</span>${item.value.folders.length > 1 ? `<span class="portalNavSectionCount">${item.value.folders.length}</span>` : ""}${icon(open ? "chevron-down" : "chevron-right")}</button><div class="portalPersonalWorkspaceActions">${readOnly ? lockButton(item.value.folders[0]) : button("add-folder", "plus", window.PortalI18n.t("向此工作区添加文件夹路径")) + button("remove", "close", window.PortalI18n.t("移除此工作区，不删除磁盘文件"))}</div></div>
            ${item === resourceItem ? note(item.key) : ""}
            ${open ? window.PortalI18n.html`<div class="portalFileTree portalNavSectionBody" role="tree" aria-label="${esc(item.value.name)} 的文件夹" data-workspace-tree>${item.value.folders.length ? item.value.folders.map(path => tree(path, 0, membership(item.key), true, readOnly)).join("") : note(window.PortalI18n.t("点击 + 添加文件夹路径"))}</div>` : ""}</section>`;
        }).join("") : note("点击 + 创建工作区，整理你常用的文件夹。"));
        list.querySelectorAll("[data-workspace-tree]").forEach(treeRoot => {
          const key = treeRoot.closest("[data-workspace-key]").dataset.workspaceKey;
          window.PortalFileTree.bindRows({
            root: treeRoot, membership: membership(key), render,
            afterToggle: path => {
              if (membership(key).has(path)) void readDirectory(path);
              else discardUnseenDirectory(path);
            },
            open: async path => {
              const opening = ++selectionRevision;
              error = "";
              render();
              try {
                const result = await onOpen(path, previewRequest(path));
                if (opening !== selectionRevision || destroyed) return;
                if (result?.error) {
                  report(new Error(window.PortalI18n.text`无法打开 ${path}：${result.error.message || result.error}`));
                  return;
                }
                if (result?.stale || result?.accepted === false) return;
                if (result && "payload" in result && result.payload == null) return;
                selected = path;
                render();
              } catch (failure) {
                if (opening === selectionRevision && !destroyed) report(new Error(window.PortalI18n.text`无法打开 ${path}：${failure.message || failure}`));
              }
            },
            openMenu: ({ path, trigger, event }) => window.PortalPreview.openFileMenu({
              event, trigger, request: previewRequest(path),
              onOpen: request => onOpen(request.path, request),
            }),
          });
        });
      });
      root.querySelectorAll("[data-workspace-action]").forEach(node => { node.disabled = busy; });
    }

    function discardUnseenDirectory(path) {
      const open = visibleItems().filter(item => !closed.has(item.key));
      const shown = (item, directory) => item.value.folders.some(root => {
        if (directory !== root && !directory.startsWith(`${root.replace(/\/$/, "")}/`)) return false;
        const expandedPaths = membership(item.key);
        if (!expandedPaths.has(root)) return false;
        let ancestor = directory;
        while (ancestor !== root && ancestor.length > root.length) {
          if (!expandedPaths.has(ancestor)) return false;
          ancestor = ancestor.slice(0, ancestor.lastIndexOf("/")) || "/";
        }
        return ancestor === root;
      });
      for (const child of directories.keys()) {
        if ((child === path || child.startsWith(`${path.replace(/\/$/, "")}/`))
          && !open.some(item => shown(item, child))) directories.delete(child);
      }
    }

    async function readDirectory(path, { more = false } = {}) {
      const previous = directories.get(path);
      if (previous?.loading || previous?.loadingMore) return;
      if (more ? !previous?.nextCursor : previous && !previous.error) return;
      const cursor = more ? previous.nextCursor : "";
      const data = more ? previous : { path, children: [], nextCursor: "" };
      data.loading = !more;
      data.loadingMore = more;
      data.error = "";
      directories.set(path, data);
      renderDirectoryPage(path);
      const current = () => !destroyed && directories.get(path) === data;
      try {
        const location = resourceLocation(path);
        const listing = location ? await window.PortalResidentService.listDirectory(location.root.directory_id, location.relative, { cursor }) : null;
        const payload = listing ? { kind: "directory", path, children: listing.entries || [], nextCursor: listing.next_cursor }
          : await window.PortalFileLinks.load(path);
        if (!current()) return;
        if (payload.kind !== "directory") throw new Error("该路径不是文件夹");
        if (cursor && (payload.path !== path || payload.nextCursor === cursor)) throw new Error("无法继续读取目录。");
        const known = new Set(data.children.map(child => child.name));
        data.path = payload.path;
        data.children.push(...(payload.children || []).filter(child => !known.has(child.name)));
        data.nextCursor = payload.nextCursor || "";
      } catch (failure) {
        if (current()) data.error = String(failure.message || failure);
      } finally {
        if (current()) {
          data.loading = false;
          data.loadingMore = false;
          renderDirectoryPage(path);
          if (!data.error) {
            const openFolders = visibleItems().filter(item => !closed.has(item.key) && membership(item.key).has(path));
            const prefix = `${data.path.replace(/\/$/, "")}/`;
            for (const child of data.children) {
              const childPath = prefix + child.name;
              if (child.type === "directory" && openFolders.some(item => membership(item.key).has(childPath))) {
                void readDirectory(childPath);
              }
            }
          }
        }
      }
    }

    function renderDirectoryPage(path) {
      const before = document.activeElement;
      const focused = list.contains(before) && before.dataset.workspaceDirectoryPage === path;
      const section = focused ? before.closest("[data-workspace-key]")?.dataset.workspaceKey : null;
      window.PortalFileTree.preserveViewport(list, render);
      if (section == null) return;
      const scope = list.querySelector(`[data-workspace-key="${CSS.escape(section)}"]`);
      (scope?.querySelector(`[data-workspace-directory-page="${CSS.escape(path)}"]`)
        || scope?.querySelector(`[data-portal-file-directory="${CSS.escape(path)}"]`))?.focus({ preventScroll: true });
    }

    async function load(force = false) {
      if (destroyed || busy || (loaded && !force)) return;
      const current = ++revision;
      busy = true;
      error = "";
      render();
      try {
        if (force) directories.clear();
        const [result] = await Promise.all([
          window.PortalIdentity.listUIAssociations(namespace),
          force ? onRefresh?.() : null,
        ]);
        if (destroyed || current !== revision) return;
        const records = (result.items || []).filter(item => typeof item.value?.name === "string").map(item => ({
          ...item, value: { ...item.value, folders: [...new Set((item.value.folders || []).filter(path => typeof path === "string" && /^(?:\/|[a-z]:[\\/]|\\\\[^\\]+\\[^\\]+)/i.test(path)))] },
        }));
        items = records.filter(item => item.value.kind !== "locked-directory");
        locks = records.filter(item => item.value.kind === "locked-directory" && item.value.folders.length === 1);
        for (const item of locks) {
          const path = item.value.folders[0];
          if (!expanded.has(directoryKey(path))) membership(directoryKey(path)).add(path);
        }
        loaded = true;
      } catch (failure) { error = `工作区加载失败：${failure.message || failure}`; }
      finally { busy = false; render(); }
      for (const item of visibleItems()) {
        if (closed.has(item.key)) continue;
        for (const path of membership(item.key)) void readDirectory(path);
      }
    }

    async function act(action, item, folder) {
      if (busy) return;
      if (action === "refresh") return load(true);
      if (action === "toggle") {
        if (!closed.delete(item.key)) {
          closed.add(item.key);
          item.value.folders.forEach(discardUnseenDirectory);
        }
        render();
        if (!closed.has(item.key)) for (const path of membership(item.key)) void readDirectory(path);
        return;
      }
      busy = true;
      error = "";
      render();
      try {
        if (action === "lock-directory") {
          const lock = directoryLock(folder);
          if (lock) {
            await window.PortalIdentity.removeUIAssociation(namespace, lock.key);
            locks = locks.filter(candidate => candidate.key !== lock.key);
          } else {
            const location = resourceLocation(folder);
            const resourceRoot = location ? { directory_id: location.root.directory_id,
              root_kind: location.root.root_kind, path: location.root.path } : null;
            const value = { ...(resourceRoot ? { resource_root: resourceRoot } : {}), kind: "locked-directory", name: item.readOnly ? item.value.name : folder.split("/").filter(Boolean).at(-1) || folder, folders: [folder] };
            const result = await window.PortalIdentity.putUIAssociation(namespace, window.PortalUIPrimitives.randomUUID(), value);
            locks.push(result.association);
            membership(directoryKey(folder)).add(folder);
            void readDirectory(folder);
          }
          return;
        }
        let key = item?.key;
        let value = item?.value;
        if (action === "create") {
          const name = await window.PortalUI.prompt({ title: window.PortalI18n.t("新建 Workspace"), label: window.PortalI18n.t("工作区名称"), confirmLabel: "创建" });
          if (!name?.trim()) return;
          key = window.PortalUIPrimitives.randomUUID();
          value = { name: name.trim(), folders: [] };
        } else if (action === "add-folder") {
          const path = await window.PortalUI.prompt({ title: window.PortalI18n.t("添加文件夹"), label: window.PortalI18n.t("服务器上的文件夹绝对路径"), placeholder: "/path/to/folder", confirmLabel: "添加" });
          if (!path?.trim()) return;
          const payload = await window.PortalFileLinks.load(path.trim(), { meta: true });
          if (payload.kind !== "directory") throw new Error("请选择一个存在且有权访问的文件夹");
          value = { ...value, folders: [...new Set([...value.folders, payload.path])] };
        } else if (action === "remove-folder") {
          value = { ...value, folders: value.folders.filter(path => path !== folder) };
        } else if (action === "remove") {
          if (!await window.PortalUI.confirm({ title: window.PortalI18n.t("移除工作区"), message: window.PortalI18n.text`移除“${value.name}”及其文件夹导航记录？磁盘上的文件保持不变。`, confirmLabel: window.PortalI18n.t("移除") })) return;
          await window.PortalIdentity.removeUIAssociation(namespace, key);
          items = items.filter(candidate => candidate.key !== key);
          expanded.delete(key);
          closed.delete(key);
          return;
        } else return;
        const result = await window.PortalIdentity.putUIAssociation(namespace, key, value);
        items = [...items.filter(candidate => candidate.key !== key), result.association].sort((a, b) => a.key.localeCompare(b.key));
        if (action === "remove-folder") {
          for (const path of membership(key)) if (path === folder || path.startsWith(`${folder}/`)) membership(key).delete(path);
        }
      } catch (failure) { error = String(failure.message || failure); }
      finally { busy = false; render(); }
    }

    root.addEventListener("click", event => {
      const page = event.target.closest("[data-workspace-directory-page]");
      if (page && !busy) {
        const path = page.dataset.workspaceDirectoryPage;
        void readDirectory(path, { more: Boolean(directories.get(path)?.nextCursor) });
        return;
      }
      const target = event.target.closest("[data-workspace-action]");
      if (!target) return;
      const key = target.closest("[data-workspace-key]")?.dataset.workspaceKey;
      void act(target.dataset.workspaceAction, visibleItems().find(item => item.key === key), target.dataset.workspaceDirectory || target.closest("[data-workspace-folder]")?.dataset.workspaceFolder);
    }, { signal: events.signal });
    render();
    return Object.freeze({
      load,
      setResident({ id = "", roots = null, error: failure = "" } = {}) {
        if (destroyed) return;
        const nextItems = (roots || []).filter(item => /^(?:\/|[a-z]:[\\/]|\\\\[^\\]+\\[^\\]+)/i.test(String(item.path || ""))).map(item => ({
          key: directoryKey(item.path),
          readOnly: true,
          source: "resident",
          value: { name: item.label || item.title || item.path, folders: [item.path] },
          root: item,
        }));
        for (const item of residentItems) {
          if (directoryLock(item.value.folders[0])) continue;
          if (nextItems.some(next => next.key === item.key && next.value.folders[0] === item.value.folders[0])) continue;
          expanded.delete(item.key);
          closed.delete(item.key);
        }
        if (residentId !== id) {
          for (const item of residentItems) {
            const path = item.root.path;
            for (const cached of directories.keys()) {
              if (cached === path || cached.startsWith(`${path.replace(/\/$/, "")}/`)) directories.delete(cached);
            }
          }
        }
        residentId = id;
        residentItems = nextItems;
        residentPending = Boolean(residentId && roots === null && !failure);
        residentNote = !residentId ? "" : failure ? `工作区读取失败：${failure}`
          : roots === null ? "正在读取当前 Resident 的工作区…"
          : !nextItems.length ? "当前 Resident 没有绑定工作区。" : "";
        for (const item of residentItems) {
          const path = item.root.path;
          if (!expanded.has(item.key)) membership(item.key).add(path);
          const failure = item.root.exists === false ? "工作区目录不存在" : item.root.is_dir === false ? "该路径不是文件夹" : "";
          // Lazy root metadata does not contain a directory listing. Retain
          // any loaded listing, and let expanded roots fetch missing content.
          if (failure || (item.root.children_loaded !== false && Array.isArray(item.root.entries))) {
            directories.set(path, { path, children: [...(item.root.entries || [])], nextCursor: item.root.next_cursor || "", error: failure });
          }
        }
        render();
        if (loaded) for (const item of residentItems) {
          if (closed.has(item.key)) continue;
          for (const path of membership(item.key)) void readDirectory(path);
        }
      },
      async openResource({ workspaceId, title, path }) {
        if (resourceItem?.key !== workspaceId) {
          const previous = resourceItem?.root?.path;
          resourceItem = null;
          if (previous) discardUnseenDirectory(previous);
          directories.delete(path);
        }
        resourceItem = { key: workspaceId, readOnly: true, source: "resource", value: { name: title || workspaceId, folders: [path] },
          root: { directory_id: workspaceId, path, root_kind: "workspace" } };
        closed.delete(workspaceId);
        membership(workspaceId).add(path);
        await load();
        await readDirectory(path);
        render();
      },
      destroy() {
        destroyed = true;
        revision++;
        events.abort();
        root.removeAttribute("aria-busy");
        delete root.dataset.workspaceResidentId;
        list.querySelectorAll("[data-workspace-tree]").forEach(window.PortalFileTree.dispose);
        root.replaceChildren();
      },
    });
  }

  window.PortalPersonalWorkspaces = Object.freeze({ mount });
})();
