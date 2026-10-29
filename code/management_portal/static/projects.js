(function () {
  "use strict";

  // The Projects page (decision_46c37026e8ea): the list at /projects and a
  // Project at /projects?project=<ID>, with the Project Resident's
  // conversation beside the Project.  The address is the page's only state:
  // back, forward and opening a link all show what the address names.
  if (window.PortalProjectsPage) return;

  const common = () => window.PortalProjectCommon;
  let list = null;
  let home = null;
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
    return { project: String(params.get("project") || "").trim() };
  }

  function navigate(url, { replace = false } = {}) {
    if (home?.hasUnsavedChanges() && !window.confirm(common().t("概况还有未保存的修改，确定离开？"))) return;
    window.history[replace ? "replaceState" : "pushState"]({}, "", url);
    void route();
  }

  function container(kind) {
    const node = main();
    if (shown !== kind) {
      node.replaceChildren();
      const root = document.createElement("div");
      root.className = kind === "list" ? "projectsListRoot" : "projectsHomeRoot";
      node.append(root);
      shown = kind;
      if (kind === "list") {
        list = window.PortalProjectList.create({
          root,
          onOpen: id => navigate(common().projectHref(id)),
          onCreate: () => void createProject(),
        });
        home = null;
      } else {
        home = window.PortalProjectHome.create({
          root,
          onOpenList: () => navigate("/projects"),
          onRenamed: id => navigate(common().projectHref(id), { replace: true }),
          onDeleted: () => navigate("/projects", { replace: true }),
          onChanged: () => {},
        });
        list = null;
      }
    }
    return kind === "list" ? list : home;
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

  // The Project Resident's conversation, kept while the same Project stays open.
  function showChat(projectData) {
    const node = aside();
    const residentId = String(projectData?.team?.project_resident_id || "");
    if (!residentId) {
      hideChat();
      return;
    }
    node.hidden = false;
    document.getElementById("projectsWorkspace").dataset.chat ||= "open";
    document.getElementById("projectsChatTitle").textContent = window.PortalI18n.text`${projectData.project.resource_id} 的 Project Resident`;
    if (chat && chatResident === residentId) return;
    chat?.destroy?.();
    const body = document.getElementById("projectsChatBody");
    body.replaceChildren();
    chatResident = residentId;
    chat = window.PortalChatWorkspace.panelPackage.create({
      root: body, id: "projectResidentChat",
      target: { resident_id: residentId, lineage_id: projectData.team.project_lineage_id || "" },
      title: window.PortalI18n.text`${projectData.project.resource_id} · Project Resident`,
      emptyText: common().t("和 Project Resident 讨论这个项目：方向、进展、团队和下一步。"),
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

  async function route() {
    const current = ++routing;
    currentUrl = `${window.location.pathname}${window.location.search}`;
    const { project } = addressed();
    if (!project) {
      hideChat();
      document.title = common().t("Project");
      const view = container("list");
      try {
        const projects = await view.load();
        if (current === routing) statusVocab = view.snapshot().statusVocab;
        return projects;
      } catch (error) {
        if (current !== routing) return null;
        main().innerHTML = window.PortalUIPrimitives.asyncStateHtml({
          state: "error", title: common().t("项目列表读取失败"), message: error?.message || String(error),
        });
        shown = "";
        return null;
      }
    }
    const view = container("home");
    const data = await view.load(project, { statuses: statusVocab }).catch(() => null);
    if (current !== routing) return null;
    if (data) showChat(data); else hideChat();
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
      if (home?.hasUnsavedChanges() && !window.confirm(common().t("概况还有未保存的修改，确定离开？"))) {
        window.history.pushState({}, "", currentUrl);
        return;
      }
      void route();
    });
    window.addEventListener("beforeunload", event => {
      if (home?.hasUnsavedChanges()) { event.preventDefault(); event.returnValue = ""; }
    });
    void route();
  }

  window.PortalProjectsPage = Object.freeze({ route, navigate });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
})();
