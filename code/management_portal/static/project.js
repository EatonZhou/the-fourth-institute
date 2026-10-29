(function () {
  "use strict";

  // The Projects page (decision_46c37026e8ea): the list at /projects, a
  // Project at /projects?project=<ID> and its research map at
  // /projects?project=<ID>&view=map[&direction=<ID>], with the Project
  // Resident's conversation beside the Project and the map.  The address is
  // the page's only state: back, forward and opening a link all show what the
  // address names; /projects?direction=<ID> opens that direction on its map.
  if (window.PortalProjectsPage) return;

  const common = () => window.PortalProjectCommon;
  const views = { list: null, home: null, map: null };
  let chat = null;
  let chatResident = "";
  let shown = "";
  let statusVocab = [];
  let routing = 0;
  let currentUrl = "";

  const main = () => document.getElementById("projectsMain");
  const aside = () => document.getElementById("projectsChat");
  let chatPreferences = null;
  const chatState = () => chatPreferences ||= window.PortalPageState.create("projects-chat", {
    user: () => window.PortalIdentity?.username?.() || "anonymous", defaults: { collapsed: false },
  });

  function addressed() {
    const params = new URLSearchParams(window.location.search);
    const value = name => String(params.get(name) || "").trim();
    return { project: value("project"), view: value("view"), direction: value("direction") };
  }

  function mapHref(projectId, directionId = "") {
    return `${common().projectHref(projectId)}&view=map${directionId ? `&direction=${encodeURIComponent(directionId)}` : ""}`;
  }

  const unsaved = () => views.home?.hasUnsavedChanges();

  function navigate(url, { replace = false } = {}) {
    if (unsaved() && !window.confirm(common().t("概况还有未保存的修改，确定离开？"))) return;
    window.history[replace ? "replaceState" : "pushState"]({}, "", url);
    void route();
  }

  function container(kind) {
    const node = main();
    if (shown !== kind) {
      views.map?.destroy();
      Object.keys(views).forEach(key => { views[key] = null; });
      node.replaceChildren();
      const root = document.createElement("div");
      root.className = { list: "projectsListRoot", home: "projectsHomeRoot", map: "projectsMapRoot" }[kind];
      node.append(root);
      document.getElementById("projectsWorkspace").dataset.view = kind;
      shown = kind;
      if (kind === "list") {
        views.list = window.PortalProjectList.create({
          root,
          onOpen: id => navigate(common().projectHref(id)),
          onCreate: () => void createProject(),
        });
      } else if (kind === "home") {
        views.home = window.PortalProjectHome.create({
          root,
          onOpenList: () => navigate("/projects"),
          onOpenMap: id => navigate(mapHref(id)),
          onRenamed: id => navigate(common().projectHref(id), { replace: true }),
          onDeleted: () => navigate("/projects", { replace: true }),
          onChanged: () => {},
        });
      } else {
        views.map = window.PortalProjectMap.create({
          root,
          onBack: () => navigate(common().projectHref(views.map.projectId())),
          // The selected direction stays in the address, without a history entry per click.
          onSelectionChange: direction => {
            const url = mapHref(views.map.projectId(), direction?.direction_id || "");
            window.history.replaceState({}, "", url);
            currentUrl = url;
          },
        });
      }
    }
    return views[kind];
  }

  async function createProject() {
    const result = await window.PortalProjectCreation.open();
    const id = result?.project?.resource_id;
    if (id) navigate(common().projectHref(id));
  }

  function setChatCollapsed(collapsed) {
    const node = aside();
    node.dataset.collapsed = String(collapsed);
    document.getElementById("projectsWorkspace").dataset.chat = collapsed ? "collapsed" : "open";
    const toggle = document.getElementById("projectsChatToggle");
    toggle.setAttribute("aria-expanded", String(!collapsed));
    toggle.querySelector("span").textContent = common().t(collapsed ? "展开对话" : "收起对话");
    chat?.setPresented?.(!collapsed);
  }

  // What the user is looking at goes with each message to the Project Resident.
  function submissionContext() {
    const direction = shown === "map" ? views.map?.selected() : null;
    if (!direction) return null;
    const fields = Object.fromEntries(["direction_id", "title", "stance", "question", "approach", "expectation", "teams"]
      .map(key => [key, direction[key]]));
    return { context_text: `研究地图中选中的方向：${JSON.stringify(fields)}` };
  }

  // The Project Resident's conversation, kept while the same Project stays open.
  function showChat(projectId, team) {
    const node = aside();
    const residentId = String(team?.project_resident_id || "");
    if (!residentId) {
      hideChat();
      return;
    }
    node.hidden = false;
    document.getElementById("projectsWorkspace").dataset.chat ||= "open";
    document.getElementById("projectsChatTitle").textContent = window.PortalI18n.text`${projectId} 的 Project Resident`;
    if (chat && chatResident === residentId) return;
    chat?.destroy?.();
    const body = document.getElementById("projectsChatBody");
    body.replaceChildren();
    chatResident = residentId;
    chat = window.PortalChatWorkspace.panelPackage.create({
      root: body, id: "projectResidentChat",
      target: { resident_id: residentId, lineage_id: team.project_lineage_id || "" },
      title: window.PortalI18n.text`${projectId} · Project Resident`,
      emptyText: common().t("和 Project Resident 讨论这个项目：方向、进展、团队和下一步。"),
      submissionContext,
    });
    setChatCollapsed(Boolean(chatState().read().collapsed));
  }

  function hideChat() {
    chat?.destroy?.();
    chat = null;
    chatResident = "";
    aside().hidden = true;
    delete document.getElementById("projectsWorkspace").dataset.chat;
  }

  function failed(title, error) {
    main().innerHTML = window.PortalUIPrimitives.asyncStateHtml({
      state: "error", title: common().t(title), message: error?.message || String(error),
      actionsHtml: window.PortalI18n.html`<a class="portalButton is-compact" href="/projects">返回项目列表</a>`,
    });
    shown = "";
  }

  async function route() {
    const current = ++routing;
    currentUrl = `${window.location.pathname}${window.location.search}`;
    const { project, view, direction } = addressed();
    if (!project && direction) {
      // A direction's own address: find its Project, then show it on the map.
      try {
        const resolved = await window.PortalInstructionClient.execute("ResolveResource", { address: direction });
        const owner = resolved?.target?.detail?.project_id || resolved?.resource?.project_ids?.[0];
        if (current !== routing) return null;
        if (!owner) throw new Error(common().t("这个研究方向不存在，或你没有读取它的权限"));
        window.history.replaceState({}, "", mapHref(owner, direction));
        return route();
      } catch (error) {
        if (current === routing) failed("研究方向读取失败", error);
        return null;
      }
    }
    if (!project) {
      hideChat();
      document.title = common().t("Project");
      const list = container("list");
      try {
        const projects = await list.load();
        if (current === routing) statusVocab = list.snapshot().statusVocab;
        return projects;
      } catch (error) {
        if (current === routing) failed("项目列表读取失败", error);
        return null;
      }
    }
    if (view === "map") {
      const map = container("map");
      document.title = `${project} · ${common().t("研究地图")}`;
      try {
        const result = await map.load(project, { selected: direction });
        if (current !== routing) return null;
        showChat(project, map.team());
        return result;
      } catch (error) {
        if (current === routing) { hideChat(); failed("研究地图读取失败", error); }
        return null;
      }
    }
    const home = container("home");
    const data = await home.load(project, { statuses: statusVocab }).catch(() => null);
    if (current !== routing) return null;
    if (data) showChat(project, data.team); else hideChat();
    return data;
  }

  function start() {
    document.getElementById("projectsChatToggle").addEventListener("click", () => {
      const collapsed = aside().dataset.collapsed !== "true";
      chatState().write({ collapsed });
      setChatCollapsed(collapsed);
    });
    window.addEventListener("popstate", () => {
      // Staying keeps the address on what the page still shows.
      if (unsaved() && !window.confirm(common().t("概况还有未保存的修改，确定离开？"))) {
        window.history.pushState({}, "", currentUrl);
        return;
      }
      void route();
    });
    window.addEventListener("beforeunload", event => {
      if (unsaved()) { event.preventDefault(); event.returnValue = ""; }
    });
    void route();
  }

  window.PortalProjectsPage = Object.freeze({ route, navigate });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
})();
