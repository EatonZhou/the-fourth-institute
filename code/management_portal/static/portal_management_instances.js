export async function mount(container) {
  const t = text => window.PortalI18n.t(text);
  let destroyed = false, pending = false, timer = null, stoppingSelf = false;
  const controller = new AbortController();
  container.innerHTML = window.PortalI18n.html`
    <section class="settingsSection">
      <header><div><h2>Portal 实例</h2>
        <p>管理此平台上已安装的实例。关闭实例会停止它的服务，保留数据和账号。</p></div>
        <button class="portalButton" type="button" data-instance-refresh>刷新实例</button>
      </header>
      <p data-instance-message role="status" aria-live="polite"></p>
      <div data-instance-list></div>
    </section>`;
  const list = container.querySelector("[data-instance-list]");
  const message = container.querySelector("[data-instance-message]");
  const reload = container.querySelector("[data-instance-refresh]");
  const labels = {running: "运行中", stopped: "已关闭", partial: "部分服务运行中", unavailable: "无法读取实例"};
  const busy = record => ["queued", "running"].includes(record?.status);

  async function request(body) {
    const response = await fetch("/api/admin/instances", {
      method: body ? "POST" : "GET", credentials: "same-origin", signal: controller.signal,
      ...(body ? {headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)} : {})
    });
    const value = await response.json();
    if (!response.ok) throw new Error(value.error || t("实例操作失败"));
    return value;
  }
  const element = (tag, text, parent) => {
    const node = document.createElement(tag);
    node.textContent = text;
    parent.append(node);
    return node;
  };
  function render(items) {
    list.replaceChildren();
    if (!items.length) element("p", t("此平台尚无已安装的实例。"), list);
    for (const item of items) {
      const card = document.createElement("section");
      card.className = "managementInstanceCard";
      list.append(card);
      element("h3", item.name, card);
      element("p", [t(labels[item.status] || item.status), item.current ? t("当前实例") : ""].filter(Boolean).join(" · "), card);
      if (item.error) element("p", item.error, card);
      if (item.start_blocker) element("p", t(item.start_blocker), card);
      if (item.operation?.status === "failed") element("p", item.operation.error || t("实例操作失败"), card);
      if (busy(item.operation)) element("p", t(item.operation.action === "start" ? "正在启动…" : "正在关闭…"), card);
      const links = element("div", "", card);
      links.className = "managementInstanceLinks";
      for (const url of item.urls || []) {
        // The server derives IP addresses from interfaces, never from Host headers.
        const link = element("a", url, links);
        link.href = url; link.target = "_blank"; link.rel = "noopener";
      }
      if (item.local_only && item.urls?.length) element("p", t("此实例目前仅允许服务器本机访问。"), card);
      const actions = element("div", "", card);
      actions.className = "accessActions";
      for (const action of ["start", "stop"]) {
        const button = element("button", t(action === "start" ? "启动实例" : "关闭实例"), actions);
        button.type = "button"; button.className = "portalButton";
        button.disabled = pending || !item[`can_${action}`] || stoppingSelf;
        button.addEventListener("click", () => change(item, action));
      }
    }
  }
  async function refresh() {
    if (destroyed || pending || stoppingSelf) return;
    clearTimeout(timer);
    pending = true; reload.disabled = true;
    try {
      const value = await request();
      if (destroyed) return;
      pending = false;
      render(value.instances);
      message.textContent = "";
      // Refresh state while the page is open, including changes made by the CLI.
      timer = setTimeout(refresh, 5000);
    } catch (error) {
      if (!destroyed) message.textContent = error.message;
    } finally { pending = false; reload.disabled = false; }
  }
  async function change(item, action) {
    if (pending || destroyed || stoppingSelf) return;
    pending = true;
    clearTimeout(timer);
    let failed = false;
    try {
      if (action === "stop" && !(await window.PortalUI.confirm({
        title: t("关闭实例"), message: item.current
          ? t("这会关闭当前页面所在的实例，连接将断开。可从另一个运行实例或服务器命令行再次启动。")
          : `${t("关闭实例")} ${item.name}？`,
        confirmLabel: t("关闭实例"), tone: "danger"
      }))) return;
      list.querySelectorAll("button").forEach(button => { button.disabled = true; });
      reload.disabled = true;
      await request({name: item.name, action});
      if (action === "stop" && item.current) {
        stoppingSelf = true;
        message.textContent = t("关闭请求已提交，当前连接即将断开。可使用其他运行实例的地址继续管理。");
        return;
      }
    } catch (error) {
      if (!destroyed) {
        failed = true;
        message.textContent = error.message;
      }
    } finally {
      pending = false;
      if (!stoppingSelf && !destroyed) { reload.disabled = false; timer = setTimeout(refresh, failed ? 5000 : 500); }
    }
  }
  reload.addEventListener("click", refresh);
  await refresh();
  return {
    refresh, isBusy: () => pending, hasUnsavedChanges: () => false,
    destroy() { destroyed = true; clearTimeout(timer); controller.abort(); }
  };
}
