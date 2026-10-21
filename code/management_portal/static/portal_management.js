(function () {
  "use strict";

  if (window.PortalManagementPanel) return;

  function panelMarkup() {
    return window.PortalI18n.html`
      <div class="managementTabs" role="tablist" aria-label="Portal Management 分栏">
        <button class="portalButton is-primary" type="button" role="tab" data-management-tab="users" aria-selected="true">用户</button>
        <button class="portalButton" type="button" role="tab" data-management-tab="backend" aria-selected="false">AI Backend</button>
        <button class="portalButton" type="button" role="tab" data-management-tab="execution" aria-selected="false">执行设置</button>
        <button class="portalButton" type="button" role="tab" data-management-tab="resource-types" aria-selected="false">资源类型</button>
        <button class="portalButton" type="button" role="tab" data-management-tab="schemes" aria-selected="false">权限方案</button>
        <button class="portalButton" type="button" role="tab" data-management-tab="entries" aria-selected="false">权限表项</button>
        <button class="portalButton" type="button" role="tab" data-management-tab="paths" aria-selected="false">数据目录</button>
        <button class="portalButton" type="button" role="tab" data-management-tab="network" aria-selected="false">网络设置</button>
        <button class="portalButton" type="button" role="tab" data-management-tab="instances" aria-selected="false">Portal 实例</button>
      </div>
      <div data-management-permission-host role="tabpanel" hidden></div>
      <div class="settingsNotice" data-access-notice role="status" hidden></div>
      <section class="settingsSection portalAccountSection" data-management-users aria-label="Portal user access">
        <header>
          <div><h2>User access</h2><p>管理账户资料与业务关联；各类资源的实际权限在“权限表项”分栏中查看。 <span data-access-password-reset-status></span></p></div>
          <button class="portalButton is-primary is-compact" data-access-create-user-button type="button">New user</button>
        </header>
        <div data-user-invitations></div>
        <div class="accessWorkspace">
          <div class="accessUsers" aria-label="Portal users">
            <div class="accessColumnTitle">Users</div>
            <div class="accessUserList" data-access-user-list></div>
          </div>
          <form class="accessEditor" data-access-editor hidden>
            <div class="accessEditorHeader">
              <div><strong data-access-editor-name></strong><small data-access-editor-meta></small></div>
            </div>
            <div class="managementUserPermissions" aria-label="选中用户的权限">
              <button class="portalButton" type="button" data-access-view-permissions>查看权限表项</button>
            </div>
            <div class="accessFieldGrid">
              <label class="portalField"><span>Display name</span><input data-access-display-name autocomplete="off" /></label>
              <label class="portalField"><span>Recovery email</span><input data-access-email type="email" autocomplete="email" /></label>
              <label class="portalField"><span>Status</span><select data-access-status><option value="active">Active</option><option value="disabled">Disabled</option></select></label>
              <label class="portalField"><span>User 分类</span><select data-access-category><option value="portal_admin">Portal 管理员</option><option value="member">一般用户</option><option value="intern">实习用户</option></select></label>
            </div>
            <p class="accessEditorNote" data-access-admin-note hidden></p>
            <div class="accessActions">
              <button class="portalButton is-primary" type="submit">Save access</button>
              <button class="portalButton" data-access-reset-password-button type="button">Reset password</button>
              <button class="portalButton" data-access-revoke-sessions-button type="button">Revoke sessions</button>
              <button class="portalButton is-danger" data-access-remove-user-button type="button">Delete user</button>
            </div>
          </form>
          <div class="accessEmpty" data-access-empty>Select a user to edit access.</div>
        </div>
        <details class="accessAudit"><summary>Recent access audit</summary><div data-access-audit-list></div></details>
      </section>

      <section class="settingsSection" data-management-paths aria-label="Portal 数据目录">
        <h2>数据目录</h2>
        <p>相对路径以 Portal 安装目录为基准。修改目录需要复制数据并重启 Portal 服务；原目录会保留，以便故障时恢复。</p>
        <form data-management-paths-form>
          <div class="accessFieldGrid">
            <label class="portalField"><span>共享资料</span><input data-path-key="share_root" required /></label>
            <label class="portalField"><span>临时运行数据</span><input data-path-key="staging_root" required /></label>
            <label class="portalField"><span>Portal 实例</span><input data-path-key="instances_root" required /></label>
            <label class="portalField"><span>账户数据</span><input data-path-key="accounts_root" required /></label>
            <label class="portalField"><span>密钥目录</span><input data-path-key="secrets_dir" required /></label>
          </div>
          <button class="portalButton is-primary" type="submit">迁移并保存</button>
        </form>
        <p data-management-paths-status role="status"></p>
      </section>
      <section class="settingsSection" data-management-execution aria-label="执行设置">
        <h2>执行设置</h2>
        <div class="managementExecutionGroup">
          <h3>权限</h3>
          <label class="settingsPreferenceRow">
            <span class="settingsPreferenceCopy"><strong>权限系统</strong> <span class="accessBadge" data-permission-system-state hidden></span>
              <small>勾选即开启：每次调用都按启用的权限方案检查权限；开启时先把方案的实例补齐到 Portal 现有对象。不勾选即关闭：已登录的 User 与 Resident 可以查看、使用、修改和删除所有对象，“权限表项”分栏隐藏，权限方案不参与判定；个人 AMA、个人秘密、撤回自己的消息和个人待办仍只属于本人。全系统生效，几秒内到达所有 Portal 服务。</small>
            </span>
            <input type="checkbox" role="switch" data-execution-setting="permission_system_enabled" disabled />
          </label>
          <label class="settingsPreferenceRow">
            <span class="settingsPreferenceCopy"><strong>权限方案</strong>
              <small>权限系统按启用的方案判定。每个方案保存自己的实例（权限树和表项）；换用其他方案时先把它的实例补齐到 Portal 现有对象，再切换，各方案实例中的修改都原样保留。方案内容在“权限方案”分栏查看和编辑。</small>
            </span>
            <select data-permission-scheme disabled></select>
          </label>
        </div>
        <div class="managementExecutionGroup">
          <h3>记录</h3>
          <label class="settingsPreferenceRow">
            <span class="settingsPreferenceCopy"><strong>模型请求时间记录</strong>
              <small>全系统生效。记录可以可靠取得的请求时间，未知时间留空。开关不改变模型请求路径，也不会中断正在进行的请求。</small>
            </span>
            <input type="checkbox" role="switch" data-execution-setting="model_request_timing_enabled" disabled />
          </label>
          <label class="settingsPreferenceRow">
            <span class="settingsPreferenceCopy"><strong>Harness 记录</strong>
              <small>全系统生效。记录每轮使用的 Harness 策略和结果，并每小时收集已结束轮次的模型调用，供 Harness 评估与演化使用。关闭后不再写入新记录，已有记录按保留天数清理。</small>
            </span>
            <input type="checkbox" role="switch" data-execution-setting="harness_recording_enabled" disabled />
          </label>
        </div>
        <div class="managementExecutionGroup" data-management-embeddings>
          <h3>可选功能</h3>
          <label class="settingsPreferenceRow">
            <span class="settingsPreferenceCopy"><strong>Resident 语义搜索</strong>
              <small>默认关闭。开启后使用本地模型做向量检索；未安装时会提示缺少的包和模型，并可一键安装。关闭时仍可使用普通关键词搜索。</small>
            </span>
            <input type="checkbox" role="switch" data-embeddings-enabled disabled />
          </label>
          <p data-embeddings-status role="status"></p>
        </div>
        <div class="managementExecutionGroup">
          <h3>服务与自动化</h3>
          <div data-management-telepus>
            <label class="settingsPreferenceRow">
              <span class="settingsPreferenceCopy"><strong>Telepus</strong>
                <small>连接 Telegram 与 Portal。关闭后停止 Telepus 的三个服务并取消开机自启；已有联系人和数据保留。</small>
              </span>
              <input type="checkbox" role="switch" data-telepus-enabled disabled />
            </label>
            <p data-telepus-status role="status"></p>
          </div>
          <div data-management-wall-scheduling>
            <label class="settingsPreferenceRow">
              <span class="settingsPreferenceCopy"><strong>WALL 定时任务</strong>
                <small>控制所有 WALL 工程的新定时分析、全面回归和全量发布。关闭后不启动新的定时任务；手动操作和已经运行的任务继续。</small>
              </span>
              <input type="checkbox" role="switch" data-wall-scheduling-enabled disabled />
            </label>
            <p data-wall-scheduling-status role="status"></p>
          </div>
          <div data-management-feature-switch="architecture_audit">
            <label class="settingsPreferenceRow">
              <span class="settingsPreferenceCopy"><strong>架构检查</strong>
                <small>控制 Portal 开发 Lineage 的架构核对脚本。关闭后不再启动新的检查，已有报告保留；正在执行的检查继续完成。</small>
              </span>
              <input type="checkbox" role="switch" data-feature-switch-enabled disabled />
            </label>
            <p data-feature-switch-status role="status"></p>
          </div>
          <div data-management-feature-switch="pulse">
            <label class="settingsPreferenceRow">
              <span class="settingsPreferenceCopy"><strong>Portal Pulse</strong>
                <small>控制每日、每周、每月及手动汇总触发。关闭后不再启动新的汇总，已有动态和汇总保留；正在执行的任务继续完成。</small>
              </span>
              <input type="checkbox" role="switch" data-feature-switch-enabled disabled />
            </label>
            <p data-feature-switch-status role="status"></p>
          </div>
        </div>
        <div class="managementExecutionGroup">
          <h3>保留天数</h3>
          <label class="settingsPreferenceRow">
            <span class="settingsPreferenceCopy"><strong>Harness 记录与产物</strong>
              <small>超过这个天数的 Harness 策略记录、模型调用记录，以及没有被对话引用的 Harness 产物，在下次清理时删除。至少 21 天，覆盖 Harness 评估使用的最长时间窗口。</small>
            </span>
            <span class="managementDays"><input type="number" inputmode="numeric" min="21" max="365" step="1" data-execution-setting="harness_retention_days" disabled /><span>天</span></span>
          </label>
        </div>
        <p data-management-execution-status role="status">正在读取设置…</p>
      </section>

      <dialog class="accessDialog" data-access-create-user-dialog aria-label="New Portal user">
        <form data-access-create-user-form method="dialog">
          <header><h2>New Portal user</h2><button class="portalButton is-icon" data-access-close-create-user type="button" aria-label="Close">×</button></header>
          <div class="accessFieldGrid">
            <label class="portalField"><span>Username</span><input data-access-create-username required pattern="[A-Za-z0-9_.@+\\-]{1,80}" autocomplete="off" /></label>
            <label class="portalField"><span>Display name</span><input data-access-create-display-name autocomplete="off" /></label>
            <label class="portalField"><span>Recovery email</span><input data-access-create-email type="email" autocomplete="email" /></label>
            <label class="portalField accessFieldWide"><span>Initial password</span><input data-access-create-password type="password" required maxlength="256" autocomplete="new-password" aria-label="Initial password" aria-describedby="access-create-password-requirements" /><small id="access-create-password-requirements">${window.PortalI18n.t(window.PortalIdentity.passwordRequirements)}</small></label>
          </div>
          <section class="managementCreationPermissions" aria-label="权限">
            <h3>权限</h3>
            <p data-access-create-permission-status role="status">新账号的权限由启用的权限方案决定：本人对自己的账号和个人空间有 admin，其余按方案规则。</p>
            <button class="portalButton" type="button" data-access-create-open-credentials hidden>查看该账号的权限表项</button>
          </section>
          <footer><button class="portalButton" data-access-cancel-create-user type="button">Cancel</button><button class="portalButton is-primary" data-access-create-submit type="submit">Create user</button></footer>
        </form>
      </dialog>
      <dialog class="accessDialog accessPasswordDialog" data-access-reset-password-dialog aria-label="Reset password">
        <form data-access-reset-password-form method="dialog">
          <header><h2>Reset password</h2><button class="portalButton is-icon" data-access-close-reset-password type="button" aria-label="Close">×</button></header>
          <p class="accessDialogMessage" data-access-reset-password-user></p>
          <label class="portalField"><span>New password</span><input data-access-reset-password-input type="password" required maxlength="256" autocomplete="new-password" aria-label="New password" aria-describedby="access-reset-password-requirements" /><small id="access-reset-password-requirements">${window.PortalI18n.t(window.PortalIdentity.passwordRequirements)}</small></label>
          <footer><button class="portalButton" data-access-cancel-reset-password type="button">Cancel</button><button class="portalButton is-primary" type="submit">Reset password</button></footer>
        </form>
      </dialog>
      <dialog class="accessDialog accessRemovalDialog" data-access-remove-user-dialog aria-label="Delete user">
        <form data-access-remove-user-form method="dialog">
          <header><h2>Delete user</h2><button class="portalButton is-icon" data-access-close-remove-user type="button" aria-label="Close">×</button></header>
          <p class="accessDialogMessage" data-access-remove-user-name></p>
          <p class="accessDialogMessage">Permanently remove the account's credentials, Portal preferences, favorites, and username-scoped uploads. To stop sign-in for a while instead, set its status to Disabled.</p>
          <div class="accessDeleteConfirmation" data-access-delete-confirmation>
            <p>Shared Lineage and Project resources, conversation history, audit records, and a non-reusable username tombstone will be retained.</p>
            <label class="portalField"><span data-access-delete-confirm-label></span><input data-access-delete-confirm-input autocomplete="off" /></label>
          </div>
          <footer><button class="portalButton" data-access-cancel-remove-user type="button">Cancel</button><button class="portalButton is-danger" data-access-remove-user-submit type="submit">Delete user and data</button></footer>
        </form>
      </dialog>`;
  }

  function mountNetwork(container) {
    const t = window.PortalI18n.t;
    container.innerHTML = window.PortalI18n.html`
      <div class="managementNetwork">
        <header class="managementNetworkHeader">
          <h2>网络设置</h2>
          <button class="portalButton" type="button" data-network-reload>重新读取（保留草稿）</button>
        </header>
        <p data-network-status role="status" aria-live="polite"></p>
        <section class="managementNetworkSection" aria-label="Portal 访问地址">
          <h3>Portal 访问地址</h3>
          <p>当前页面：<code data-network-current-url></code></p>
          <div data-network-urls class="managementNetworkUrls"></div>
          <p class="managementNetworkHint">其他设备须使用这台电脑的可达地址；若经过反向代理，请使用代理提供的地址。主机防火墙仍可能限制连接。</p>
        </section>
        <form data-network-form="access" class="managementNetworkSection">
          <fieldset disabled>
            <legend>访问范围</legend>
            <label class="portalField"><span>允许从哪里访问</span><select data-network-field="access_mode"><option value="local">仅本机</option><option value="remote">允许其他设备访问</option></select></label>
            <details class="managementNetworkAdvanced"><summary>高级设置：端口</summary><label class="portalField"><span>Portal 端口</span><input data-network-field="port" type="number" min="1024" max="65535" step="1" inputmode="numeric" /></label></details>
            <p data-network-legacy-note class="managementNetworkHint" hidden>当前仍沿用旧版网段限制。保存新范围后，旧规则会移除；选择允许其他设备访问时，请使用主机防火墙控制具体来源。</p>
            <p class="managementNetworkHint">保存后 Router 将重启，访问可能短暂中断；若从其他设备切换为“仅本机”，请在本机继续访问。</p>
            <button class="portalButton is-primary" type="submit">保存访问设置</button>
          </fieldset>
          <p data-network-message="access" role="status" aria-live="polite"></p>
        </form>
        <form data-network-form="proxy" class="managementNetworkSection">
          <fieldset disabled>
            <legend>网络代理</legend>
            <p data-network-source></p>
            <p id="management-network-proxy-help">代理地址支持 http:// 或 https://。HTTP、HTTPS 可分别设置，“统一代理”用于其余未单独设置的请求。代理地址全部留空时直接连接。</p>
            <div class="managementNetworkFields" aria-describedby="management-network-proxy-help">
              <label class="portalField"><span>HTTP 代理 URL</span><input data-network-field="http" type="url" autocomplete="off" spellcheck="false" placeholder="http://proxy.example:8080" /></label>
              <label class="portalField"><span>HTTPS 代理 URL</span><input data-network-field="https" type="url" autocomplete="off" spellcheck="false" placeholder="http://proxy.example:8080" /></label>
              <label class="portalField"><span>统一代理 URL</span><input data-network-field="all" type="url" autocomplete="off" spellcheck="false" placeholder="http://proxy.example:8080" /></label>
              <label class="portalField"><span>不使用代理的地址</span><input data-network-field="no_proxy" autocomplete="off" spellcheck="false" placeholder="localhost,127.0.0.1,::1,.example.com" /><small>以逗号分隔；localhost、127.0.0.1、::1 始终不使用代理。</small></label>
            </div>
            <p class="managementNetworkHint">保存的代理供新启动的 Portal 服务和 AI 进程使用。已运行的服务需要重启后生效。</p>
            <button class="portalButton is-primary" type="submit">保存网络代理</button>
          </fieldset>
          <p data-network-message="proxy" role="status" aria-live="polite"></p>
        </form>
      </div>`;
    const groups = ["access", "proxy"];
    const proxyKeys = ["http", "https", "all", "no_proxy"];
    const field = key => container.querySelector(`[data-network-field="${key}"]`);
    const forms = Object.fromEntries(groups.map(group => [group, container.querySelector(`[data-network-form="${group}"]`)]));
    const messages = Object.fromEntries(groups.map(group => [group, container.querySelector(`[data-network-message="${group}"]`)]));
    const reload = container.querySelector("[data-network-reload]");
    const status = container.querySelector("[data-network-status]");
    const source = container.querySelector("[data-network-source]");
    const urls = container.querySelector("[data-network-urls]");
    const legacyNote = container.querySelector("[data-network-legacy-note]");
    container.querySelector("[data-network-current-url]").textContent = window.location.origin;
    const updateUrls = data => {
      urls.replaceChildren();
      for (const url of data.access.urls) {
        const row = document.createElement("div");
        row.className = "managementNetworkUrl";
        const input = document.createElement("input");
        input.className = "portalControl";
        input.readOnly = true;
        input.value = url;
        input.setAttribute("aria-label", t("Portal 访问地址"));
        const button = document.createElement("button");
        button.type = "button";
        button.className = "portalButton";
        button.textContent = t("复制");
        button.addEventListener("click", async () => {
          try {
            if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(url);
            else { input.select(); if (!document.execCommand("copy")) throw new Error(); }
            status.textContent = t("访问地址已复制。");
          } catch { status.textContent = t("无法自动复制，请手动选择地址复制。"); input.select(); }
        });
        row.append(input, button);
        urls.append(row);
      }
      legacyNote.hidden = !data.access.legacy_restriction;
    };
    const baselines = {};
    let version = "", busy = false, conflicted = false, destroyed = false;
    const requests = new AbortController();
    const values = group => group === "access" ? { mode: field("access_mode").value, port: field("port").value }
      : Object.fromEntries(proxyKeys.map(key => [key, field(key).value]));
    const snapshot = group => JSON.stringify(values(group));
    const dirty = group => baselines[group] !== undefined && snapshot(group) !== baselines[group];
    const serverValues = (group, data) => group === "access" ? { mode: data.access.mode, port: String(data.access.port) }
      : Object.fromEntries(proxyKeys.map(key => [key, data.proxy[key]]));
    const applyGroup = (group, data) => {
      if (group === "access") { field("access_mode").value = data.access.mode; field("port").value = String(data.access.port); }
      else proxyKeys.forEach(key => { field(key).value = data.proxy[key]; });
    };
    const updateSource = data => {
      source.textContent = data.proxy_source === "saved"
        ? t("当前使用已保存的代理设置。")
        : t("当前代理来自服务环境。首次保存网络代理后，将使用此处的显式配置。");
    };
    const controls = () => {
      if (destroyed) return;
      container.setAttribute("aria-busy", String(busy));
      reload.disabled = busy;
      for (const group of groups) forms[group].querySelector("fieldset").disabled = busy || !version || conflicted;
    };
    const request = async body => {
      const response = await fetch("/api/admin/network", {
        method: body === undefined ? "GET" : "POST", credentials: "same-origin", signal: requests.signal,
        ...(body === undefined ? {} : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const error = new Error(typeof data.error === "string" ? data.error : t("无法读取或保存网络设置，请重试。"));
        error.status = response.status;
        throw error;
      }
      if (typeof data.version !== "string" || !data.version || !["local", "remote"].includes(data.access?.mode)
        || !Number.isInteger(data.access?.port) || !Array.isArray(data.access?.urls)
        || !data.access.urls.every(value => typeof value === "string")
        || !proxyKeys.every(key => typeof data.proxy?.[key] === "string")
        || !["environment", "saved"].includes(data.proxy_source)) {
        throw new Error(t("服务器未返回完整的网络设置，请重新读取。"));
      }
      return data;
    };
    const read = async () => {
      if (busy || destroyed) return;
      const keepDraft = Object.fromEntries(groups.map(group => [group, dirty(group)]));
      busy = true; controls(); status.textContent = t("正在读取网络设置…");
      try {
        const data = await request();
        if (destroyed) return;
        for (const group of groups) {
          if (!keepDraft[group]) applyGroup(group, data);
          baselines[group] = JSON.stringify(serverValues(group, data));
          messages[group].textContent = "";
        }
        version = data.version; conflicted = false; updateSource(data); updateUrls(data);
        status.textContent = Object.values(keepDraft).some(Boolean)
          ? t("已读取最新设置，未保存的草稿已保留；请检查后再次保存。") : "";
      } catch (error) {
        if (!destroyed) status.textContent = error.message;
      } finally { busy = false; controls(); }
    };
    for (const group of groups) {
      forms[group].addEventListener("submit", async event => {
        event.preventDefault();
        if (destroyed || busy || !version || conflicted) return;
        const changes = group === "access"
          ? { access: { mode: field("access_mode").value, port: Number(field("port").value) } }
          : { proxy: Object.fromEntries(proxyKeys.map(key => [key, field(key).value.trim()])) };
        busy = true; controls(); messages[group].textContent = t("正在保存…");
        try {
          const data = await request({ version, ...changes });
          if (destroyed) return;
          // The other group may hold a draft: only the saved group accepts canonical values.
          applyGroup(group, data); baselines[group] = snapshot(group); version = data.version;
          if (group === "proxy") updateSource(data);
          else updateUrls(data);
          status.textContent = "";
          messages[group].textContent = group === "access"
            ? t("访问设置已保存；Router 即将重启，页面可能短暂断开。")
            : t("网络代理已保存，供新启动的服务和 AI 进程使用；已运行的服务需重启。");
        } catch (error) {
          if (!destroyed) {
            conflicted = error.status === 409;
            messages[group].textContent = conflicted
              ? t("设置已被其他人修改。请点击“重新读取”，检查保留的草稿后再次保存。") : error.message;
          }
        } finally { busy = false; controls(); }
      });
    }
    reload.addEventListener("click", read);
    void read();
    return {
      isBusy: () => busy,
      hasUnsavedChanges: () => groups.some(dirty),
      destroy() { destroyed = true; requests.abort(); },
    };
  }

  async function mount(root, options = {}) {
    if (!root) throw new Error("Portal Management panel host is unavailable.");
    const me = options.me || await window.PortalIdentity.load();
    const allows = me.access?.allows || {};
    const portalAdmin = me.access?.user_category === "portal_admin";
    if (!portalAdmin && !["CreateUser", "GetExecutionSettings", "UpdateExecutionSettings", "ListResourceTypes", "ListProviders", "RegisterProviderAccount"].some(name => allows[name] === true)) {
      throw new Error("Portal Management needs permission for CreateUser or GetExecutionSettings.");
    }
    root.innerHTML = panelMarkup();
    const {mountEnrollment} = await import("/static/portal_user_enrollment.js");
    const enrollment = mountEnrollment(root.querySelector("[data-user-invitations]"), {invitations: true});
    const nodes = new Map();
    const $ = name => {
      if (!nodes.has(name)) nodes.set(name, root.querySelector(`[data-access-${name}]`));
      return nodes.get(name);
    };
    const userCategoryTitle = category => window.PortalI18n.t(
      {portal_admin: "Portal 管理员", member: "一般用户", intern: "实习用户"}[category] || category);
    const state = { access: null, selectedUsername: "" };
    let destroyed = false;
    let pending = 0;
    let confirming = false;
    let currentView = "users";
    let currentPrincipalId = "";
    let navigationVersion = 0;
    const entries = new Map();
    const userDrafts = new Map();
    const layout = document.createElement("div");
    layout.className = "managementLayout";
    const tabs = root.querySelector(".managementTabs");
    tabs.setAttribute("aria-orientation", "vertical");
    const stage = root.querySelector("[data-management-permission-host]");
    stage.hidden = false;
    stage.removeAttribute("role");
    stage.className = "managementStage";
    for (const view of ["users", "backend", "execution", "resource-types", "schemes", "entries", "paths", "network", "instances"]) {
      const pane = document.createElement("section");
      pane.className = "managementView"; pane.dataset.managementView = view;
      pane.setAttribute("role", "tabpanel"); pane.hidden = view !== "users";
      const container = document.createElement("div");
      container.dataset.managementComponent = view;
      pane.append(container); stage.append(pane);
      entries.set(view, {pane, container, instance: null, promise: null, target: "", invalidation: null, syncing: false});
    }
    entries.get("users").pane.append(root.querySelector("[data-management-users]"));
    const executionSection = root.querySelector("[data-management-execution]");
    // Both views use the same setting, authorization, confirmation and save path.
    const permissionRow = executionSection.querySelector('[data-execution-setting="permission_system_enabled"]').closest("label").cloneNode(true);
    permissionRow.querySelector("small").textContent = window.PortalI18n.t("关闭时方案不参与权限判定；开启时使用下方选定方案。");
    const permissionStatus = document.createElement("p");
    permissionStatus.setAttribute("role", "status");
    permissionStatus.dataset.permissionSettingsStatus = "";
    permissionStatus.hidden = true;
    entries.get("schemes").pane.prepend(permissionRow, permissionStatus);
    entries.get("execution").pane.append(executionSection);
    entries.get("paths").pane.append(root.querySelector("[data-management-paths]"));
    // With the permission system off there are no permission entries to manage;
    // the scheme page stays and explains that schemes take effect once it is on.
    const permissionViews = new Set(["entries"]);
    let permissionsOff = me.access?.permission_system === "open";
    if (permissionsOff) {
      tabs.querySelectorAll("[data-management-tab]").forEach(tab => { tab.hidden = permissionViews.has(tab.dataset.managementTab); });
      $("view-permissions").hidden = true;
      root.querySelector(".managementCreationPermissions h3").hidden = true;
      root.querySelector("[data-management-users] header p").firstChild.textContent = `${window.PortalI18n.t("管理账户资料与业务关联；权限系统关闭时不限制访问。")} `;
    }
    layout.append(tabs, stage); root.prepend(layout);
    tabs.querySelector('[data-management-tab="users"]').hidden = allows.CreateUser !== true;
    tabs.querySelector('[data-management-tab="resource-types"]').hidden = allows.ListResourceTypes !== true && !permissionsOff;
    tabs.querySelector('[data-management-tab="backend"]').hidden = allows.ListProviders !== true && allows.ListProviderAccounts !== true && allows.UpdateProviderAccount !== true && allows.RegisterProviderAccount !== true && !permissionsOff;
    tabs.querySelector('[data-management-tab="paths"]').hidden = me.access?.user_category !== "portal_admin";
    tabs.querySelector('[data-management-tab="network"]').hidden = !portalAdmin;
    tabs.querySelector('[data-management-tab="instances"]').hidden = !portalAdmin;
    const pathsForm = root.querySelector("[data-management-paths-form]");
    const pathsStatus = root.querySelector("[data-management-paths-status]");
    const pathInputs = [...pathsForm.querySelectorAll("[data-path-key]")];
    let pathsRead = false, pathMigrationState = "";
    let savedPaths = {};
    const readPaths = async () => {
      if (pathsRead) return;
      pathsRead = true;
      try {
        const response = await fetch("/api/admin/paths", {credentials: "same-origin"});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || window.PortalI18n.t("无法读取目录配置"));
        savedPaths = result.paths;
        pathInputs.forEach(input => { input.value = savedPaths[input.dataset.pathKey] || ""; });
        if (pathMigrationState !== "completed") pathsStatus.textContent = "";
      } catch (error) { pathsRead = false; pathsStatus.textContent = error.message; }
    };
    const pollPathMigration = async jobId => {
      if (destroyed) return;
      try {
        const response = await fetch(`/api/admin/paths/migration?id=${encodeURIComponent(jobId)}`, {credentials: "same-origin"});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || window.PortalI18n.t("无法读取迁移进度"));
        pathMigrationState = result.state; const labels = {queued: window.PortalI18n.t("排队中"), stopping: window.PortalI18n.t("正在停止服务"), copying: window.PortalI18n.t("正在复制并校验数据"), switching: window.PortalI18n.t("正在切换目录"), restarting: window.PortalI18n.t("正在重启服务"), completed: window.PortalI18n.t("迁移完成"), failed: window.PortalI18n.t("迁移失败")};
        pathsStatus.textContent = `${labels[result.state] || result.state}${result.error ? `：${result.error}` : ""}`;
        if (result.state === "completed" || result.state === "failed") {
          sessionStorage.removeItem("portal-path-migration");
          pathsForm.querySelector('[type="submit"]').disabled = false;
          if (result.state === "completed") { pathsRead = false; await readPaths(); }
          return;
        }
      } catch (_) { pathsStatus.textContent = window.PortalI18n.t("Portal 服务暂不可用，迁移状态将在服务恢复后刷新。"); }
      setTimeout(() => pollPathMigration(jobId), 2000);
    };
    pathsForm.addEventListener("submit", async event => {
      event.preventDefault();
      const proposed = Object.fromEntries(pathInputs.map(input => [input.dataset.pathKey, input.value.trim()]));
      const changed = pathInputs.filter(input => proposed[input.dataset.pathKey] !== savedPaths[input.dataset.pathKey]);
      if (!changed.length) { pathsStatus.textContent = window.PortalI18n.t("目录没有变化。"); return; }
      const description = changed.map(input => `${input.previousElementSibling?.textContent || input.dataset.pathKey}: ${savedPaths[input.dataset.pathKey]} → ${proposed[input.dataset.pathKey]}`).join("\n");
      if (!window.confirm(window.PortalI18n.text`要迁移现有数据并重启 Portal 服务吗？\n\n${description}\n\n迁移期间 Portal 将暂时不可用。`)) {
        pathsStatus.textContent = window.PortalI18n.t("未保存新路径。修改目录必须同时迁移数据。");
        pathInputs.forEach(input => { input.value = savedPaths[input.dataset.pathKey] || ""; });
        return;
      }
      const submit = pathsForm.querySelector('[type="submit"]');
      submit.disabled = true;
      pathsStatus.textContent = window.PortalI18n.t("正在启动独立迁移任务…");
      try {
        const response = await fetch("/api/admin/paths", {method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json"}, body: JSON.stringify({paths: proposed, migrate: true})});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || window.PortalI18n.t("无法启动迁移"));
        sessionStorage.setItem("portal-path-migration", result.id);
        pollPathMigration(result.id);
      } catch (error) { pathsStatus.textContent = error.message; submit.disabled = false; }
    });
    const pendingPathJob = sessionStorage.getItem("portal-path-migration");
    if (pendingPathJob) pollPathMigration(pendingPathJob);
    // Portal-wide execution settings; read when the tab first opens, each control saved on change.
    const included = name => !Array.isArray(window.__PORTAL_FRONTEND__?.distribution_features) || window.__PORTAL_FRONTEND__.distribution_features.includes(name);
    const executionInputs = [...root.querySelectorAll("[data-execution-setting]")].filter(input => {
      const feature = input.dataset.executionSetting.startsWith("harness_") ? "harness_recording" : input.dataset.executionSetting === "model_request_timing_enabled" ? "model_request_timing" : null;
      if (!feature || included(feature)) return true;
      input.closest("label").hidden = true;
      return false;
    });
    const executionStatus = executionSection.querySelector("[data-management-execution-status]");
    const embeddingsSection = executionSection.querySelector("[data-management-embeddings]");
    const embeddingsToggle = embeddingsSection.querySelector("[data-embeddings-enabled]");
    const embeddingsStatus = embeddingsSection.querySelector("[data-embeddings-status]");
    embeddingsSection.hidden = !included("resident_semantic_search") || me.access?.user_category !== "portal_admin";
    const telepusSection = executionSection.querySelector("[data-management-telepus]");
    const telepusToggle = telepusSection.querySelector("[data-telepus-enabled]");
    const telepusStatus = telepusSection.querySelector("[data-telepus-status]");
    telepusSection.hidden = !portalAdmin || allows.GetTelepusStatus !== true;
    const maySwitchTelepus = allows.SetTelepusEnabled === true;
    let telepusBusy = false;
    const renderTelepus = status => {
      telepusToggle.checked = status.enabled === true;
      telepusToggle.disabled = telepusBusy || !maySwitchTelepus || status.available !== true
        || (status.missing?.length > 0 && !status.enabled);
      const incomplete = (status.units || []).filter(unit => !unit.active || !unit.enabled).map(unit => unit.name);
      telepusStatus.textContent = status.available !== true
        ? window.PortalI18n.t("Telepus 服务状态不可用：{p0}", {p0: status.error || "systemd"})
        : status.missing?.length
          ? window.PortalI18n.t("缺少 Telepus 服务：{p0}。", {p0: status.missing.join("、")})
          : status.complete ? window.PortalI18n.t("Telepus 已开启，三个服务正在运行。")
            : status.enabled ? window.PortalI18n.t("Telepus 部分开启；需检查：{p0}。", {p0: incomplete.join("、")})
              : window.PortalI18n.t("Telepus 已关闭，三个服务已停止并取消开机自启。");
    };
    const readTelepus = async () => {
      if (telepusSection.hidden || destroyed || telepusBusy) return;
      try {
        const result = await window.PortalInstructionClient.execute("GetTelepusStatus", {});
        if (!destroyed) renderTelepus(result.status);
      } catch (error) {
        if (!destroyed) { telepusToggle.disabled = true; telepusStatus.textContent = error.message; }
      }
    };
    telepusToggle.addEventListener("change", async () => {
      const requested = telepusToggle.checked;
      telepusBusy = true;
      telepusToggle.disabled = true;
      telepusStatus.textContent = window.PortalI18n.t(requested ? "正在启动 Telepus 服务…" : "正在停止 Telepus 服务…");
      try {
        const result = await window.PortalInstructionClient.execute("SetTelepusEnabled", {enabled: requested},
          {idempotencyKey: `telepus-enabled:${globalThis.PortalUIPrimitives.randomUUID()}`});
        if (!destroyed) { telepusBusy = false; renderTelepus(result.status); }
      } catch (error) {
        if (!destroyed) {
          telepusBusy = false;
          await readTelepus();
          telepusStatus.textContent = error.message;
        }
      }
    });
    const wallSchedulingSection = executionSection.querySelector("[data-management-wall-scheduling]");
    const wallSchedulingToggle = wallSchedulingSection.querySelector("[data-wall-scheduling-enabled]");
    const wallSchedulingStatus = wallSchedulingSection.querySelector("[data-wall-scheduling-status]");
    wallSchedulingSection.hidden = !portalAdmin;
    const wallSchedulingRequest = async enabled => {
      const response = await fetch("/api/admin/wall-scheduling", {
        method: enabled === undefined ? "GET" : "POST", credentials: "same-origin",
        ...(enabled === undefined ? {} : {headers: {"Content-Type": "application/json"}, body: JSON.stringify({enabled})}),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || window.PortalI18n.t("无法读取 WALL 定时任务状态"));
      return result;
    };
    const readWallScheduling = async () => {
      if (wallSchedulingSection.hidden || destroyed) return;
      try {
        const result = await wallSchedulingRequest();
        if (destroyed) return;
        wallSchedulingToggle.checked = result.enabled === true;
        wallSchedulingToggle.disabled = false;
        wallSchedulingStatus.textContent = window.PortalI18n.t(result.enabled ? "WALL 定时任务已开启。" : "WALL 定时任务已暂停；各工程的计划配置保留。");
      } catch (error) {
        wallSchedulingToggle.disabled = true;
        wallSchedulingStatus.textContent = error.message;
      }
    };
    wallSchedulingToggle.addEventListener("change", async () => {
      const enabled = wallSchedulingToggle.checked;
      wallSchedulingToggle.disabled = true;
      try {
        const result = await wallSchedulingRequest(enabled);
        wallSchedulingToggle.checked = result.enabled === true;
        wallSchedulingToggle.disabled = false;
        wallSchedulingStatus.textContent = window.PortalI18n.t(result.enabled ? "WALL 定时任务已开启。" : "WALL 定时任务已暂停；各工程的计划配置保留。");
      } catch (error) {
        await readWallScheduling();
        wallSchedulingStatus.textContent = error.message;
      }
    });
    const featureSwitches = [...executionSection.querySelectorAll("[data-management-feature-switch]")].map(section => {
      const name = section.dataset.managementFeatureSwitch;
      const toggle = section.querySelector("[data-feature-switch-enabled]");
      const status = section.querySelector("[data-feature-switch-status]");
      section.hidden = !portalAdmin || (name === "pulse" && !included("pulse"));
      const request = async enabled => {
        const response = await fetch(enabled === undefined
          ? `/api/admin/feature-switch?name=${encodeURIComponent(name)}` : "/api/admin/feature-switch", {
          method: enabled === undefined ? "GET" : "POST", credentials: "same-origin",
          ...(enabled === undefined ? {} : {headers: {"Content-Type": "application/json"}, body: JSON.stringify({name, enabled})}),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || window.PortalI18n.t("无法读取功能状态"));
        return result;
      };
      const render = result => {
        toggle.checked = result.enabled === true;
        toggle.indeterminate = result.partial === true;
        toggle.disabled = false;
        status.textContent = window.PortalI18n.t(result.partial ? "部分触发源已暂停，请重新切换以统一状态。"
          : result.enabled ? "已开启。" : "已关闭；已有结果保留。");
      };
      const read = async () => {
        if (section.hidden || destroyed) return;
        try {
          const result = await request();
          if (!destroyed) render(result);
        } catch (error) {
          if (!destroyed) { toggle.disabled = true; status.textContent = error.message; }
        }
      };
      toggle.addEventListener("change", async () => {
        const enabled = toggle.checked;
        toggle.disabled = true;
        try { if (!destroyed) render(await request(enabled)); }
        catch (error) { await read(); status.textContent = error.message; }
      });
      return read;
    });
    const readFeatureSwitches = () => featureSwitches.forEach(read => { void read(); });
    let embeddingPoll = 0;
    const embeddingRequest = async (url, body) => {
      const response = await fetch(url, {
        method: body === undefined ? "GET" : "POST", credentials: "same-origin",
        ...(body === undefined ? {} : {headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)}),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || window.PortalI18n.t("无法读取语义搜索设置"));
      return result;
    };
    let embeddingInstalling = false;
    const renderEmbeddings = state => {
      embeddingInstalling = state.job?.state === "running";
      embeddingsToggle.checked = state.enabled === true;
      embeddingsToggle.disabled = state.job?.state === "running";
      const missing = Array.isArray(state.missing) ? state.missing : [];
      embeddingsStatus.textContent = state.job?.state === "running"
        ? window.PortalI18n.t("正在后台安装语义搜索包和模型；完成后请再次打开开关。")
        : state.job?.state === "failed"
          ? window.PortalI18n.text`安装失败：${state.job.error || window.PortalI18n.t("请查看安装日志")}`
          : state.platform_issue ? window.PortalI18n.text`此设备暂不支持自动安装语义搜索：${state.platform_issue}`
          : state.active ? window.PortalI18n.t("语义搜索已开启。") : missing.length
            ? window.PortalI18n.text`语义搜索已关闭。缺少：${missing.join("、")}。`
            : window.PortalI18n.t("语义搜索已关闭；所需包和模型已就绪。");
      window.clearTimeout(embeddingPoll);
      if (state.job?.state === "running" && !destroyed) {
        embeddingPoll = window.setTimeout(() => { void readEmbeddings(); }, 2500);
      }
    };
    const readEmbeddings = async () => {
      if (embeddingsSection.hidden || destroyed) return;
      try { renderEmbeddings(await embeddingRequest("/api/admin/embeddings")); }
      catch (error) { embeddingsStatus.textContent = error.message; }
    };
    embeddingsToggle.addEventListener("change", async () => {
      const requested = embeddingsToggle.checked;
      embeddingsToggle.disabled = true;
      try {
        const state = await embeddingRequest("/api/admin/embeddings", {enabled: requested});
        renderEmbeddings(state);
        if (requested && state.needs_install) {
          const approved = await window.PortalUI.confirm({
            title: window.PortalI18n.t("安装 Resident 语义搜索？"),
            message: window.PortalI18n.text`本机缺少：${state.missing.join("、")}。安装会下载模型并安装 Python 包；完成后请再次打开开关。`,
            confirmLabel: window.PortalI18n.t("安装"),
          });
          if (approved) {
            await embeddingRequest("/api/admin/embeddings/install", {});
            await readEmbeddings();
          }
        }
      } catch (error) { embeddingsStatus.textContent = error.message; await readEmbeddings(); }
      finally {
        if (!destroyed && !embeddingInstalling) embeddingsToggle.disabled = false;
      }
    });
    const switchMessages = {
      model_request_timing_enabled: on => on ? window.PortalI18n.t("请求时间记录已开启。") : window.PortalI18n.t("请求时间记录已关闭，缺失的时间保持未知。"),
      harness_recording_enabled: on => on ? window.PortalI18n.t("Harness 记录已开启。") : window.PortalI18n.t("Harness 记录已关闭，已有记录按保留天数清理。"),
      permission_system_enabled: on => on ? window.PortalI18n.t("权限系统已开启，启用方案的实例已补齐。") : window.PortalI18n.t("权限系统已关闭，方案暂不参与判定。"),
    };
    let executionRead = false;
    let savedExecution = {};
    let permissionSaving = false;
    let permissionRevision = 0;
    const showSetting = input => {
      const value = savedExecution[input.dataset.executionSetting];
      if (input.type === "checkbox") input.checked = value === true;
      else input.value = typeof value === "number" ? String(value) : "";
      const state = input.dataset.executionSetting === "permission_system_enabled" && input.closest("label").querySelector("[data-permission-system-state]");
      if (state) {
        state.hidden = typeof value !== "boolean";
        state.textContent = window.PortalI18n.t(value ? "当前：已开启" : "当前：已关闭");
      }
    };
    // Only the given controls are redrawn, so a value typed into another one survives a save.
    const acceptExecution = (settings, inputs) => {
      for (const input of inputs) {
        if (typeof settings?.[input.dataset.executionSetting] !== (input.type === "checkbox" ? "boolean" : "number")) {
          throw new Error(window.PortalI18n.t("未能读取执行设置"));
        }
      }
      const accepted = {...settings};
      // An unrelated setting's older response cannot undo a newer permission toggle.
      if (!inputs.some(input => input.dataset.executionSetting === "permission_system_enabled")) delete accepted.permission_system_enabled;
      savedExecution = {...savedExecution, ...accepted};
      inputs.forEach(showSetting);
      if (typeof accepted.permission_system_enabled === "boolean") syncPermissionSystem(accepted.permission_system_enabled);
    };
    // The scheme in use; activating another one brings its saved instance up to date first (ActivatePermissionScheme).
    const schemeSelect = executionSection.querySelector("[data-permission-scheme]");
    let activeScheme = "";
    // The settings read and the scheme list read answer in either order; their failures share the status line.
    const readNotes = {settings: "", schemes: ""};
    const showReadNote = (key, text) => {
      readNotes[key] = text;
      executionStatus.textContent = [readNotes.settings, readNotes.schemes].filter(Boolean).join(" ");
    };
    // Portal 管理员 always list the schemes (8fd7ed6f); anyone else a scheme refuses sees the refusal as it is.
    const readSchemes = () => {
      void window.PortalInstructionClient.execute("ListPermissionSchemes", {}).then(result => {
        if (destroyed) return;
        const schemes = Array.isArray(result.schemes) ? result.schemes : [];
        activeScheme = schemes.find(item => item.active)?.scheme_id || "";
        schemeSelect.replaceChildren(...schemes.map(item => {
          const option = document.createElement("option");
          option.value = item.scheme_id;
          option.textContent = item.title || item.scheme_id;
          option.selected = item.active === true;
          return option;
        }));
        schemeSelect.disabled = !schemes.length;
        readNotes.schemes = "";
      }).catch(error => { if (!destroyed) showReadNote("schemes", error.message); });
    };
    // The 授权差 of an activation (b820 §5.12), as rows of the confirmation: the scheme page's wording, with
    // the A levels titled from the scheme's rules when they can be read. It only informs.
    const authDiffRows = async (schemeId, report) => {
      if (!report) return [];
      try {
        await window.PortalUIPrimitives.loadAsset("/static/portal_permission_schemes.js", {ready: () => window.PortalPermissionSchemes});
        const rules = report.groups?.items?.length
          ? (await window.PortalInstructionClient.execute("GetPermissionScheme", {scheme_id: schemeId}).catch(() => null))?.rules : null;
        const parts = window.PortalPermissionSchemes.authDiffParts(report, rules);
        const t = window.PortalI18n.t;
        return [
          {label: t("授权差"), value: parts.head}, {label: t("未算完"), value: parts.incomplete},
          ...parts.groups.map(item => ({label: item.kindLabel, value: item.text})), {label: t("其余"), value: parts.more},
          ...parts.remapped.map(item => ({label: t("归类改变"), value: `${item.instruction}：${item.lines.join(t("；"))}`})), {label: t("其余"), value: parts.remappedMore},
        ];
      } catch (_error) { return []; }
    };
    schemeSelect.addEventListener("change", async () => {
      const schemeId = schemeSelect.value;
      if (!schemeId || schemeId === activeScheme) return;
      pending++;
      schemeSelect.disabled = true;
      try {
        const impact = await window.PortalInstructionClient.execute("ActivatePermissionScheme", {scheme_id: schemeId, dry_run: true, diff_limit: 8});
        if (destroyed) return;
        const authRows = await authDiffRows(schemeId, impact.auth_diff);
        if (destroyed) return;
        // While the system is off, activating only records the scheme; its instance is brought up to date when it is turned on.
        const t = window.PortalI18n.t;
        const work = impact.regenerate
          ? t("这份方案还没有按当前规则生成的实例，将按 Portal 现有对象生成：{p0} 个节点、{p1} 条表项。", {p0: impact.added_nodes || 0, p1: impact.added_entries || 0})
          : t("先把这份方案的实例补齐到 Portal 现有对象：新增 {p0} 条、删除 {p1} 条表项，移动 {p2} 个节点；实例中的修改原样保留。",
            {p0: impact.added_entries || 0, p1: impact.removed_entries || 0, p2: impact.moved_nodes || 0});
        const approved = await window.PortalUI.confirm(permissionsOff ? {
          title: t("开启权限系统时使用 {p0}？", {p0: impact.title || schemeId}),
          message: [t("权限系统仍保持关闭，现在不影响任何判定；在“执行设置”中开启权限系统时，Portal 按这份方案判定。"), work].join(" "),
          metadata: authRows, confirmLabel: t("设为开启时使用"),
        } : {
          title: t("启用权限方案「{p0}」？", {p0: impact.title || schemeId}),
          message: [work, t("之后所有判定按这份方案进行。")].join(" "),
          metadata: authRows, confirmLabel: t("启用"), tone: "danger",
        });
        if (destroyed) return;
        if (!approved) { schemeSelect.value = activeScheme; return; }
        if (!permissionsOff) executionStatus.textContent = t("正在补齐权限实例…");
        await window.PortalInstructionClient.execute("ActivatePermissionScheme", {scheme_id: schemeId},
          {idempotencyKey: `permission-scheme:${globalThis.PortalUIPrimitives.randomUUID()}`});
        if (destroyed) return;
        activeScheme = schemeId;
        executionStatus.textContent = t(permissionsOff ? "开启权限系统时将使用这份方案。" : "权限方案已切换。");
      } catch (error) {
        if (!destroyed) { schemeSelect.value = activeScheme; executionStatus.textContent = error.message; }
      } finally {
        pending--;
        if (!destroyed) schemeSelect.disabled = false;
      }
    });
    // /api/me asks UpdateExecutionSettings about the switch alone, which a Portal 管理员 always passes
    // whatever the scheme (b820 §3.1); the switch shows /api/me's mode, so a scheme that refuses
    // GetExecutionSettings cannot take it away.
    const permissionSwitches = executionInputs.filter(input => input.dataset.executionSetting === "permission_system_enabled");
    const switchInput = permissionSwitches[0];
    const maySwitch = allows.UpdateExecutionSettings === true;
    const syncPermissionSystem = enabled => {
      if (destroyed || typeof enabled !== "boolean") return;
      permissionRevision++;
      permissionsOff = !enabled;
      savedExecution.permission_system_enabled = enabled;
      permissionSwitches.forEach(input => { showSetting(input); input.disabled = !maySwitch || permissionSaving; });
      tabs.querySelector('[data-management-tab="entries"]').hidden = !enabled;
      $("view-permissions").hidden = !enabled;
      root.querySelector(".managementCreationPermissions h3").hidden = !enabled;
      root.querySelector("[data-management-users] header p").firstChild.textContent = `${window.PortalI18n.t(enabled
        ? "管理账户资料与业务关联；各类资源的实际权限在“权限表项”分栏中查看。"
        : "管理账户资料与业务关联；权限系统关闭时不限制访问。")} `;
      entries.get("schemes").instance?.setSystemEnabled?.(enabled);
    };
    const showPermissionMessage = message => {
      executionStatus.textContent = message;
      permissionStatus.textContent = message;
      permissionStatus.hidden = !message;
    };
    const savePermissionSystem = async value => {
      if (!maySwitch || permissionSaving || destroyed) { permissionSwitches.forEach(showSetting); return; }
      permissionSaving = true;
      pending++;
      permissionSwitches.forEach(input => { input.disabled = true; });
      try {
        const approved = await window.PortalUI.confirm(value
          ? {title: window.PortalI18n.t("开启权限系统？"), message: window.PortalI18n.t("先把启用方案的实例补齐到 Portal 现有对象，再对所有调用恢复检查。对象较多时需要等待一会儿。"), confirmLabel: window.PortalI18n.t("开启")}
          : {title: window.PortalI18n.t("关闭权限系统？"), message: window.PortalI18n.t("所有已登录的 User 与 Resident 将可以查看、使用、修改和删除所有对象。权限方案的实例和临时凭证保留，重新开启时补齐后继续生效。"), confirmLabel: window.PortalI18n.t("关闭"), tone: "danger"});
        if (!approved || destroyed) return;
        showPermissionMessage(window.PortalI18n.t(value ? "正在重建权限实例…" : "正在关闭权限系统…"));
        const result = await window.PortalInstructionClient.execute("UpdateExecutionSettings", {permission_system_enabled: value},
          {idempotencyKey: `execution-settings:${globalThis.PortalUIPrimitives.randomUUID()}`});
        if (destroyed) return;
        acceptExecution(result.settings, permissionSwitches);
        showPermissionMessage(switchMessages.permission_system_enabled(savedExecution.permission_system_enabled));
        if (!savedExecution.permission_system_enabled) readSchemes();
      } catch (error) {
        if (!destroyed) showPermissionMessage(error.message);
      } finally {
        pending--;
        permissionSaving = false;
        if (!destroyed) permissionSwitches.forEach(input => { showSetting(input); input.disabled = !maySwitch; });
      }
    };
    if (typeof me.access?.permission_system === "string") syncPermissionSystem(!permissionsOff);
    const readExecutionSettings = () => {
      if (executionRead) { void readTelepus(); void readWallScheduling(); readFeatureSwitches(); return; }
      executionRead = true;
      void readEmbeddings();
      void readTelepus();
      void readWallScheduling();
      readFeatureSwitches();
      readSchemes();
      if (maySwitch && typeof me.access?.permission_system === "string" && typeof savedExecution.permission_system_enabled !== "boolean") {
        savedExecution.permission_system_enabled = me.access.permission_system !== "open";
        showSetting(switchInput);
        switchInput.disabled = false;
      }
      const revision = permissionRevision;
      void window.PortalInstructionClient.execute("GetExecutionSettings", {}).then(result => {
        if (destroyed) return;
        const settings = {...result.settings};
        if (revision !== permissionRevision) settings.permission_system_enabled = savedExecution.permission_system_enabled;
        acceptExecution(settings, executionInputs);
        showReadNote("settings", "");
        executionInputs.forEach(input => { input.disabled = permissionSwitches.includes(input) && (!maySwitch || permissionSaving); });
      }).catch(error => {
        if (destroyed) return;
        // Opening the tab again reads again.
        executionRead = false;
        showReadNote("settings", switchInput.disabled ? error.message
          : window.PortalI18n.t("{p0} 权限系统开关仍可使用。", {p0: error.message}));
      });
    };
    for (const input of executionInputs) {
      input.addEventListener("change", async () => {
        const field = input.dataset.executionSetting;
        let value = input.checked;
        if (field === "permission_system_enabled") { await savePermissionSystem(value); return; }
        if (input.type === "number") {
          value = Number(input.value);
          const minimum = Number(input.min), maximum = Number(input.max);
          if (!input.value.trim() || !Number.isInteger(value) || value < minimum || value > maximum) {
            executionStatus.textContent = window.PortalI18n.t("请输入 {p0}–{p1} 之间的整数天数", {p0: minimum, p1: maximum});
            showSetting(input);
            return;
          }
          if (value === savedExecution[field]) return;
        }
        pending++;
        input.disabled = true;
        try {
          const result = await window.PortalInstructionClient.execute("UpdateExecutionSettings", {[field]: value},
            {idempotencyKey: `execution-settings:${globalThis.PortalUIPrimitives.randomUUID()}`});
          if (destroyed) return;
          acceptExecution(result.settings, [input]);
          executionStatus.textContent = window.PortalI18n.t(switchMessages[field]?.(value) || "保留天数已保存。");
        } catch (error) {
          if (!destroyed) { showSetting(input); executionStatus.textContent = error.message; }
        } finally {
          pending--;
          if (!destroyed) input.disabled = false;
        }
      });
    }
    const noticeElement = $("notice"); root.prepend(noticeElement);
    const baselines = new WeakMap();
    const snapshot = form => JSON.stringify([...form.querySelectorAll("input,select,textarea")].map(input => (
      input.type === "checkbox" || input.type === "radio" ? input.checked : input.value
    )));
    const remember = form => baselines.set(form, snapshot(form));
    const dirty = form => baselines.has(form) && snapshot(form) !== baselines.get(form);
    const accountDialogs = new Map([...root.querySelectorAll("dialog")].map(dialog => [dialog,
      window.PortalUI.createDialogController({
        modal: dialog, escapeClose: true, initialFocus: () => dialog.querySelector("input"),
        requestClose: async () => {
          const form = dialog.querySelector("form");
          if (!(await allowLeave([form]))) return;
          closeAccountDialog(dialog); form.reset();
        },
      }),
    ]));
    const openAccountDialog = dialog => accountDialogs.get(dialog).open();
    const closeAccountDialog = dialog => accountDialogs.get(dialog).close({force: true});
    const activeForms = () => [
      ...(!$("editor").hidden ? [$("editor")] : []),
      ...[...root.querySelectorAll("dialog[open] form")],
    ];
    const isBusy = () => pending > 0 || enrollment.isBusy() || [...entries.values()].some(entry => entry.syncing || entry.instance?.isBusy?.() === true);
    const hasUnsavedChanges = () => {
      saveUserDraft();
      return enrollment.hasUnsavedChanges() || activeForms().some(dirty) || userDrafts.size > 0 || [...entries.values()].some(entry => entry.instance?.hasUnsavedChanges?.() === true);
    };
    const saveUserDraft = () => {
      if (!state.selectedUsername || $("editor").hidden) return;
      const form = $("editor");
      if (dirty(form)) userDrafts.set(state.selectedUsername, {values: snapshot(form), baseline: baselines.get(form)});
      else userDrafts.delete(state.selectedUsername);
    };
    const allowLeave = async (forms = null) => {
      if (destroyed || confirming) return false;
      if (isBusy()) { notice(window.PortalI18n.t("操作正在处理，请稍后再试。"), "error"); return false; }
      if (!(forms ? forms.some(dirty) : hasUnsavedChanges())) return true;
      confirming = true;
      const dialogs = [...root.querySelectorAll("dialog[open]")];
      dialogs.forEach(closeAccountDialog);
      try {
        const approved = await window.PortalUI.confirm({title: window.PortalI18n.t("放弃未保存的修改？"), message: window.PortalI18n.t("离开后将清除未保存的修改。"), confirmLabel: window.PortalI18n.t("放弃修改"), tone: "danger"});
        return approved && !destroyed && !isBusy();
      } finally {
        if (!destroyed) dialogs.forEach(openAccountDialog);
        confirming = false;
      }
    };
    const run = callback => async event => {
      event?.preventDefault?.();
      if (destroyed || confirming || isBusy()) return;
      pending++;
      try { await callback(event); } finally { pending--; }
    };
    const disposePermission = () => {
      navigationVersion++;
      for (const entry of entries.values()) {
        entry.instance?.destroy?.(); entry.instance = null;
      }
    };
    const showView = () => {
      for (const [view, entry] of entries) entry.pane.hidden = view !== currentView;
      root.querySelectorAll("[data-management-tab]").forEach(tab => {
        const selected = tab.dataset.managementTab === currentView;
        tab.setAttribute("aria-selected", String(selected)); tab.classList.toggle("is-primary", selected);
      });
    };
    const ensureEntry = async (view, principalId) => {
      const entry = entries.get(view);
      if (!entry.promise) {
        entry.target = principalId;
        entry.container.textContent = window.PortalI18n.t("正在加载…");
        entry.promise = Promise.resolve().then(async () => {
          if (view === "network") return mountNetwork(entry.container);
          if (view === "instances") return (await import("/static/portal_management_instances.js")).mount(entry.container);
          if (view === "resource-types") return (await import("/static/portal_management_resource_types.js")).mount(entry.container, {me});
          if (view === "backend") return (await import("/static/portal_management_backend.js")).mount(entry.container, {me, accountId: options.accountId});
          return window.PortalNav.mountPermissionManagement({container: entry.container, principalId, view, onSystemChange: syncPermissionSystem});
        }).then(instance => {
          if (destroyed) { instance?.destroy?.(); return null; }
          entry.instance = instance;
          return instance;
        }).catch(error => {
          if (!destroyed) entry.container.textContent = error.message || window.PortalI18n.t("无法加载权限管理。");
          entry.promise = null;
          return null;
        });
      }
      return entry.promise;
    };
    const refreshEntry = async entry => {
      if (!entry.invalidation || !entry.instance?.refresh || entry.syncing || entry.instance.isBusy?.()) return;
      const invalidation = entry.invalidation;
      const selected = entry.instance.getPrincipalId?.() ?? entry.target;
      if (invalidation.principalId && invalidation.principalId !== selected) return;
      entry.syncing = true;
      try {
        const refreshed = await entry.instance.refresh(invalidation);
        if (refreshed !== false && entry.invalidation === invalidation) entry.invalidation = null;
      } catch (error) { notice(error.message || window.PortalI18n.t("部分内容待刷新，请稍后重试。"), "error"); }
      finally { entry.syncing = false; }
    };
    const invalidate = ({views = [], principalId, reason = "changed"} = {}) => {
      for (const view of views) {
        const entry = entries.get(view);
        if (entry && view !== "users") entry.invalidation = {principalId, reason};
      }
    };
    const openSection = async ({view = "users", principalId, accountId, createAccount = false} = {}) => {
      if (!entries.has(view)) throw new Error("Unknown Portal Management view.");
      if (["network", "instances"].includes(view) && !portalAdmin) return false;
      if (permissionsOff && permissionViews.has(view)) { view = "users"; principalId = undefined; }
      if (destroyed || confirming) return false;
      const current = entries.get(currentView);
      const targetPrincipal = principalId === undefined
        ? (view === "users" || currentView === "users" ? state.selectedUsername : (current.instance?.getPrincipalId?.() ?? currentPrincipalId))
        : String(principalId || "");
      const entry = entries.get(view);
      if (view === "users" && targetPrincipal !== state.selectedUsername) {
        if (pending > 0) { notice(window.PortalI18n.t("账户操作正在处理，请稍后切换用户。"), "error"); return false; }
        if (!accessUser(targetPrincipal)) { notice(window.PortalI18n.t("用户目录中没有该账户。"), "error"); return false; }
        saveUserDraft(); state.selectedUsername = targetPrincipal;
        renderUserList(); renderEditor();
      }
      const principalView = view === "entries" || view === "schemes";
      if (principalView && entry.instance && targetPrincipal !== (entry.instance.getPrincipalId?.() ?? entry.target)) {
        if (entry.syncing || entry.instance.isBusy?.()) { notice(window.PortalI18n.t("目标资料正在处理，请稍后切换。"), "error"); return false; }
        if (!entry.instance.setPrincipalId) { notice(window.PortalI18n.t("该视图暂不支持保留草案切换主体。"), "error"); return false; }
      }
      const version = ++navigationVersion;
      const previousView = currentView;
      currentView = view; currentPrincipalId = targetPrincipal; showView();
      if (view === "users") return true;
      if (view === "execution") { readExecutionSettings(); return true; }
      if (view === "paths") { readPaths(); return true; }
      const alreadyMounted = Boolean(entry.instance);
      const instance = await ensureEntry(view, targetPrincipal);
      if (!instance || destroyed || version !== navigationVersion) return false;
      if (view === "schemes" && alreadyMounted) await instance.refreshSystem?.();
      if (view === "backend") {
        instance.focusAccount?.(accountId);
        if (createAccount) instance.openAccountEditor?.();
      }
      if (principalView && targetPrincipal !== (instance.getPrincipalId?.() ?? entry.target)) {
        if (entry.syncing || instance.isBusy?.() || !instance.setPrincipalId) return false;
        entry.syncing = true;
        try {
          if (await instance.setPrincipalId(targetPrincipal) === false) {
            if (!destroyed && version === navigationVersion) { currentView = previousView; showView(); }
            return false;
          }
          entry.target = targetPrincipal;
        } catch (error) { notice(error.message || window.PortalI18n.t("无法切换目标主体。"), "error"); return false; }
        finally { entry.syncing = false; }
      }
      await refreshEntry(entry);
      return !destroyed && version === navigationVersion;
    };
    root.querySelectorAll("[data-management-tab]").forEach(tab => tab.addEventListener("click", () => openSection({view: tab.dataset.managementTab})));
    $("view-permissions").addEventListener("click", () => openSection({view: "entries", principalId: state.selectedUsername}));


    const notice = (message, tone = "") => {
      if (destroyed) return;
      const box = $("notice");
      box.hidden = !message;
      box.className = `settingsNotice${tone ? ` ${tone}` : ""}`;
      box.textContent = message || "";
    };
    const accessUser = (username = state.selectedUsername) => (
      state.access?.users?.find(user => user.username === username) || null
    );
    const renderUserList = () => {
      const host = $("user-list");
      host.replaceChildren();
      for (const user of state.access?.users || []) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "accessUserRow";
        button.setAttribute("aria-current", user.username === state.selectedUsername ? "true" : "false");
        const name = document.createElement("strong");
        name.textContent = user.display_name || user.username;
        const meta = document.createElement("small");
        meta.textContent = user.category && user.category !== "member"
          ? `${user.username} · ${userCategoryTitle(user.category)}`
          : `${user.username} · ${user.status}`;
        button.append(name, meta);
        button.addEventListener("click", () => openSection({view: "users", principalId: user.username}));
        host.append(button);
      }
    };
    const renderEditor = () => {
      const user = accessUser();
      $("editor").hidden = !user;
      $("empty").hidden = Boolean(user);
      if (!user) return;
      $("editor-name").textContent = user.display_name || user.username;
      $("editor-meta").textContent = user.username;
      $("display-name").value = user.display_name || user.username;
      $("email").value = user.email || "";
      $("status").value = user.status || "active";
      $("category").value = user.category || "member";
      // Saving runs UpdateUser on this username, category included; each user row carries the
      // server's answer for the caller (allows.UpdateUser), and the form is editable only when it allows it.
      const mayUpdate = user.allows?.UpdateUser === true;
      ["display-name", "email", "status", "category"].forEach(name => { $(name).disabled = !mayUpdate; });
      $("editor").querySelector('button[type="submit"]').disabled = !mayUpdate;
      // The administrator list is for Portal 管理员 alone and never their own row, in either mode
      // and under every scheme (b820 §3.1): the category and status of an administrator's row,
      // its deletion, and making anyone an administrator.
      const callerAdmin = me.access?.user_category === "portal_admin";
      const rowAdmin = user.category === "portal_admin";
      const own = user.username.toLowerCase() === String(me.username || "").toLowerCase();
      const listLocked = rowAdmin && (!callerAdmin || own);
      if (listLocked) { $("status").disabled = true; $("category").disabled = true; }
      $("category").querySelector('option[value="portal_admin"]').disabled = !callerAdmin && !rowAdmin;
      $("remove-user-button").hidden = listLocked;
      $("admin-note").hidden = !listLocked;
      $("admin-note").textContent = !listLocked ? ""
        : window.PortalI18n.t(own ? "不能取消、停用或删除自己的 Portal 管理员身份；需要时由另一位 Portal 管理员操作。"
          : "只有 Portal 管理员能修改管理员名单：这位 Portal 管理员的分类和状态不能在这里修改，也不能删除。");
      $("reset-password-button").hidden = user.username === me.username;
      const draft = userDrafts.get(user.username);
      if (draft) {
        const values = JSON.parse(draft.values);
        $("editor").querySelectorAll("input,select,textarea").forEach((input, index) => {
          if (input.type === "checkbox" || input.type === "radio") input.checked = values[index];
          else input.value = values[index];
        });
        baselines.set($("editor"), draft.baseline);
      } else remember($("editor"));
    };
    const renderAudit = () => {
      const host = $("audit-list");
      host.replaceChildren();
      for (const event of state.access?.audit || []) {
        const row = document.createElement("div");
        row.className = "accessAuditEvent";
        const at = document.createElement("span");
        at.textContent = window.PortalUI.formatDateTime(event.at);
        const actor = document.createElement("code");
        actor.textContent = event.actor || "system";
        const action = document.createElement("span");
        action.textContent = `${event.action || "update"}${event.target ? ` · ${event.target}` : ""}`;
        row.append(at, actor, action);
        host.append(row);
      }
    };
    const renderAccess = () => {
      renderUserList();
      renderEditor();
      renderAudit();
    };
    const loadAccess = async (preferredUsername = "", discardUser = "") => {
      saveUserDraft();
      if (discardUser) userDrafts.delete(discardUser);
      const access = await window.PortalAPI.get("/api/admin/access-control");
      if (destroyed) return;
      state.access = access;
      $("password-reset-status").textContent = state.access.password_reset?.email_configured
        ? window.PortalI18n.t("Email recovery is enabled.")
        : window.PortalI18n.t("Email recovery is waiting for SMTP configuration.");
      if (preferredUsername && accessUser(preferredUsername)) state.selectedUsername = preferredUsername;
      if (!accessUser()) state.selectedUsername = state.access.users?.[0]?.username || "";
      renderAccess();
    };
    // Profile updates and removal use HATG ISA; password reset and
    // session revocation use the account security route.
    const userInstruction = (name, args) => window.PortalInstructionClient.execute(name, args, {
      idempotencyKey: `user:${name}:${globalThis.PortalUIPrimitives.randomUUID()}`,
    });
    const saveAccess = async event => {
      event.preventDefault();
      const user = accessUser();
      if (!user) return;
      try {
        // Status and category go only when changed: on an administrator's row either one is a
        // change to the administrator list, which a profile edit must not ask for.
        const category = $("category").value;
        const status = $("status").value;
        await userInstruction("UpdateUser", {
          user_id: user.username,
          changes: {
            display_name: $("display-name").value.trim(),
            recovery_email: $("email").value.trim(),
            ...(status !== (user.status || "active") ? {status} : {}),
            ...(category !== (user.category || "member") ? {category} : {}),
          },
        });
        await loadAccess(user.username, user.username);
        invalidate({views: ["entries"], principalId: user.username, reason: "user.updated"});
        notice(window.PortalI18n.text`Access saved for ${user.username}.`, "success");
      } catch (error) {
        notice(error.message || window.PortalI18n.t("Unable to save access."), "error");
      }
    };
    const openResetPassword = () => {
      if (destroyed || confirming || isBusy()) return;
      const user = accessUser();
      if (!user || user.username === me.username) return;
      $("reset-password-form").reset();
      $("reset-password-user").textContent = window.PortalI18n.text`Set a new password for ${user.username}. Existing sessions will be revoked.`;
      remember($("reset-password-form"));
      openAccountDialog($("reset-password-dialog"));
      $("reset-password-input").focus();
    };
    const submitResetPassword = async event => {
      event.preventDefault();
      const user = accessUser();
      if (!user) return;
      const password = $("reset-password-input").value;
      const strengthError = window.PortalIdentity.passwordStrengthError(password);
      if (strengthError) {
        notice(strengthError, "error");
        $("reset-password-input").focus();
        return;
      }
      try {
        await userInstruction("ResetUserPassword", {
          user_id: user.username,
          new_password: password,
        });
        closeAccountDialog($("reset-password-dialog"));
        await loadAccess(user.username);
        notice(window.PortalI18n.text`Password reset and sessions revoked for ${user.username}.`, "success");
      } catch (error) {
        notice(error.message || window.PortalI18n.t("Unable to reset password."), "error");
      }
    };
    const revokeSessions = async () => {
      const user = accessUser();
      if (!user) return;
      const approved = await window.PortalUI.confirm({
        title: window.PortalI18n.t("Revoke sessions?"),
        message: `${user.username} will need to sign in again on every device.`,
        confirmLabel: window.PortalI18n.t("Revoke sessions"),
        tone: "danger",
      });
      if (!approved) return;
      try {
        await userInstruction("RevokeUserSessions", {
          user_id: user.username,
        });
        await loadAccess(user.username);
        notice(window.PortalI18n.text`Sessions revoked for ${user.username}.`, "success");
      } catch (error) {
        notice(error.message || window.PortalI18n.t("Unable to revoke sessions."), "error");
      }
    };
    const syncRemovalDialog = () => {
      const user = accessUser();
      $("remove-user-submit").disabled = !user || $("delete-confirm-input").value !== user.username;
    };
    const openRemoveUser = () => {
      if (destroyed || confirming || isBusy()) return;
      const user = accessUser();
      if (!user) return;
      $("remove-user-form").reset();
      $("remove-user-name").textContent = window.PortalI18n.text`Delete ${user.username}.`;
      $("delete-confirm-label").textContent = window.PortalI18n.text`Type ${user.username} to confirm permanent deletion`;
      syncRemovalDialog();
      remember($("remove-user-form"));
      openAccountDialog($("remove-user-dialog"));
    };
    const submitRemoveUser = async event => {
      event.preventDefault();
      const user = accessUser();
      if (!user) return;
      if ($("delete-confirm-input").value !== user.username) return;
      $("remove-user-submit").disabled = true;
      try {
        await userInstruction("DeleteUser", {user_id: user.username});
        closeAccountDialog($("remove-user-dialog"));
        await loadAccess();
        notice(window.PortalI18n.text`User ${user.username} and personal data deleted. Shared content and history were retained.`, "success");
      } catch (error) {
        notice(error.message || window.PortalI18n.t("Unable to remove user."), "error");
        syncRemovalDialog();
      }
    };
    let creationSubmitted = false;
    const creationMessage = message => { if (!destroyed) $("create-permission-status").textContent = message; };
    const accountInput = () => ({
      username: $("create-username").value.trim(),
      display_name: $("create-display-name").value.trim() || $("create-username").value.trim(),
      email: $("create-email").value.trim(), status: "active",
    });
    const openCreateUser = () => {
      if (destroyed || confirming || isBusy()) return;
      $("create-user-form").reset();
      creationSubmitted = false;
      $("create-submit").disabled = false;
      $("create-open-credentials").hidden = true;
      $("create-open-credentials").dataset.principalId = "";
      remember($("create-user-form"));
      openAccountDialog($("create-user-dialog"));
      creationMessage(permissionsOff ? "" : window.PortalI18n.t("新账号的权限由启用的权限方案决定：本人对自己的账号和个人空间有 admin，其余按方案规则。"));
    };
    const createUser = async event => {
      event.preventDefault();
      if (creationSubmitted || !$("create-username").reportValidity()) return;
      const password = $("create-password").value;
      const strengthError = window.PortalIdentity.passwordStrengthError(password);
      if (strengthError) { creationMessage(strengthError); $("create-password").focus(); return; }
      try {
        const account = accountInput();
        const payload = {user_id: account.username, display_name: account.display_name,
          recovery_email: account.email, initial_password: password};
        $("create-submit").disabled = true;
        const result = await userInstruction("CreateUser", payload);
        if (destroyed) return;
        if (!result.user) throw new Error("服务端没有确认账号已创建，请核对后再操作。");
        creationSubmitted = true; $("create-password").value = "";
        const username = payload.user_id;
        const message = permissionsOff ? window.PortalI18n.text`账号 ${username} 已创建。`
          : window.PortalI18n.text`账号 ${username} 已创建；需要方案规则以外的权限时，在它的权限表项中添加。`;
        creationMessage(message);
        $("create-open-credentials").dataset.principalId = username;
        $("create-open-credentials").hidden = permissionsOff;
        remember($("create-user-form"));
        try { await loadAccess(username); }
        catch (error) { notice(window.PortalI18n.text`${message} 账户目录刷新失败：${error.message}`, "error"); }
        invalidate({views: ["entries"], principalId: username, reason: "user.created"});
        notice(message, "success");
      } catch (error) {
        $("create-submit").disabled = false;
        creationMessage(window.PortalI18n.text`${error.message || window.PortalI18n.t("创建未完成")}。请核对账号状态；不会自动重试。`);
      }
    };
    $("create-open-credentials").addEventListener("click", async () => {
      const principalId = $("create-open-credentials").dataset.principalId;
      if (!creationSubmitted || !principalId || !(await allowLeave([$("create-user-form")]))) return;
      if (await openSection({view: "entries", principalId})) closeAccountDialog($("create-user-dialog"));
    });


    $("editor").addEventListener("submit", run(saveAccess));
    $("reset-password-button").addEventListener("click", openResetPassword);
    $("reset-password-form").addEventListener("submit", run(submitResetPassword));
    $("revoke-sessions-button").addEventListener("click", run(revokeSessions));
    $("remove-user-button").addEventListener("click", openRemoveUser);
    $("remove-user-form").addEventListener("submit", run(submitRemoveUser));
    $("remove-user-form").addEventListener("change", syncRemovalDialog);
    $("delete-confirm-input").addEventListener("input", syncRemovalDialog);
    $("create-user-button").addEventListener("click", openCreateUser);
    $("create-user-form").addEventListener("submit", run(createUser));
    for (const name of ["close-create-user", "cancel-create-user"]) {
      $(name).addEventListener("click", async () => {
        if (await allowLeave([$("create-user-form")])) {
          closeAccountDialog($("create-user-dialog")); $("create-user-form").reset();
        }
      });
    }
    for (const name of ["close-reset-password", "cancel-reset-password"]) {
      $(name).addEventListener("click", async () => {
        if (await allowLeave([$("reset-password-form")])) { closeAccountDialog($("reset-password-dialog")); $("reset-password-form").reset(); }
      });
    }
    for (const name of ["close-remove-user", "cancel-remove-user"]) {
      $(name).addEventListener("click", async () => {
        if (await allowLeave([$("remove-user-form")])) { closeAccountDialog($("remove-user-dialog")); $("remove-user-form").reset(); }
      });
    }

    if (allows.CreateUser === true) await loadAccess(options.principalId || me.username || "");
    currentPrincipalId = state.selectedUsername;
    showView();
    const initialView = allows.CreateUser === true ? "users" : allows.ListResourceTypes === true ? "resource-types" : (allows.ListProviders === true || allows.RegisterProviderAccount === true) ? "backend" : (allows.GetExecutionSettings === true || allows.UpdateExecutionSettings === true) ? "execution" : "network";
    await openSection({view: options.view || initialView, principalId: options.principalId, accountId: options.accountId, createAccount: options.createAccount});
    return { username: String(me.username || ""), openSection, invalidate, isBusy, hasUnsavedChanges,
      destroy() {
        destroyed = true; disposePermission();
        enrollment.destroy();
        for (const [dialog, controller] of accountDialogs) {
          closeAccountDialog(dialog); controller.destroy();
        }
        root.replaceChildren();
      },
    };
  }

  window.PortalManagementPanel = { mount };

  async function initStandalone() {
    const host = document.querySelector("[data-portal-management-host]");
    if (!host) return;
    window.PortalNav?.mount?.();
    try {
      const params = new URLSearchParams(location.search);
      const anchor = new URLSearchParams(location.hash.slice(1));
      const result = await mount(host, {view: params.get("view") || undefined, accountId: anchor.get("account_id") || ""});
      const label = document.querySelector("[data-management-user]");
      if (label) label.textContent = window.PortalI18n.text`Portal administration for ${result.username || "admin"}`;
    } catch (error) {
      host.replaceChildren();
      const notice = document.createElement("div");
      notice.className = "settingsNotice error";
      notice.setAttribute("role", "alert");
      notice.textContent = error.message || window.PortalI18n.t("Unable to load Portal Management.");
      host.append(notice);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initStandalone, { once: true });
  } else {
    initStandalone();
  }
})();
