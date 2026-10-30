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

  // mount builds Portal Management in root. Its parts live in module scripts
  // that load before this one; managementPart(name) returns what a script
  // registers:
  //   portal_management_layout.js  the tabs, the stage with one pane per
  //     view and the sections in the panes
  //   portal_management_paths.js  the data directories form and path
  //     migrations
  //   portal_management_execution.js  the execution settings: reading them
  //     when the tab opens and saving an input when it changes
  //   portal_management_services.js  Telepus, WALL scheduling, the feature
  //     switches and Resident semantic search
  //   portal_management_permission_system.js  the scheme in use and turning
  //     the permission system on or off
  //   portal_management_guards.js  form baselines, the account dialogs, user
  //     drafts, leaving unsaved changes and running account actions
  //   portal_management_sections.js  mounting the views and opening a section
  //   portal_management_users.js  the notice, the user list, the editor, the
  //     audit, reading the directory and saving a user's profile
  //   portal_management_accounts.js  password resets, session revocation,
  //     deleting and creating users
  // mount checks the caller's access, writes the panel's markup, mounts the
  // invitations, holds the state the parts share, creates the parts in that
  // order, opens the first view and owns teardown; a part that calls a
  // function of a part created after it gets an arrow forwarding the call.
  function managementPart(name) {
    const part = window[name];
    if (!part) throw new Error(`${name} is not loaded.`);
    return part;
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
    const state = { access: null, selectedUsername: "" };
    const userDrafts = new Map();
    // The state the parts share: whether the panel is destroyed, how many
    // account and settings actions are in flight, whether a discard is being
    // confirmed, the principal of the current view, whether the permission
    // system is off, the execution settings last read or saved, and whether
    // and how often the permission switch changed. The part that computes a
    // field's first value sets it; each part keeps the rest of its state to
    // itself.
    const panelState = {
      destroyed: false, pending: 0, confirming: false, currentPrincipalId: "", permissionsOff: null,
      savedExecution: {}, permissionSaving: false, permissionRevision: 0,
    };

    const {
      entries, executionSection, permissionStatus, permissionViews, tabs,
    } = managementPart("PortalManagementLayout").createLayout({ $, allows, me, panelState, portalAdmin, root });
    const { readPaths } = managementPart("PortalManagementPaths").createPaths({ panelState, root });
    const {
      acceptExecution, executionStatus, included, maySwitch, permissionSwitches, readExecutionSettings, showSetting,
      startExecutionInputs, switchMessages,
    } = managementPart("PortalManagementExecution").createExecution({
      allows, executionSection, me, panelState, readEmbeddings: (...args) => readEmbeddings(...args),
      readFeatureSwitches: (...args) => readFeatureSwitches(...args), readSchemes: (...args) => readSchemes(...args),
      readTelepus: (...args) => readTelepus(...args), readWallScheduling: (...args) => readWallScheduling(...args),
      root, savePermissionSystem: (...args) => savePermissionSystem(...args),
      showReadNote: (...args) => showReadNote(...args),
      syncPermissionSystem: (...args) => syncPermissionSystem(...args),
    });
    const {
      readEmbeddings, readFeatureSwitches, readTelepus, readWallScheduling,
    } = managementPart("PortalManagementServices").createServices({
      allows, executionSection, included, me, panelState, portalAdmin,
    });
    const {
      readSchemes, savePermissionSystem, showReadNote, syncPermissionSystem,
    } = managementPart("PortalManagementPermissionSystem").createPermissionSystem({
      $, acceptExecution, entries, executionSection, executionStatus, maySwitch, me, panelState, permissionStatus,
      permissionSwitches, root, showSetting, switchMessages, tabs,
    });
    startExecutionInputs();
    const noticeElement = $("notice"); root.prepend(noticeElement);
    const {
      accountDialogs, allowLeave, baselines, closeAccountDialog, hasUnsavedChanges, isBusy, openAccountDialog,
      remember, run, saveUserDraft,
    } = managementPart("PortalManagementGuards").createGuards({
      $, enrollment, entries, notice: (...args) => notice(...args), panelState, root, state, userDrafts,
    });
    const {
      disposePermission, invalidate, openSection, showView,
    } = managementPart("PortalManagementSections").createSections({
      $, accessUser: (...args) => accessUser(...args), entries, me, mountNetwork,
      notice: (...args) => notice(...args), options, panelState, permissionViews, portalAdmin, readExecutionSettings,
      readPaths, renderEditor: (...args) => renderEditor(...args),
      renderUserList: (...args) => renderUserList(...args), root, saveUserDraft, state, syncPermissionSystem,
    });
    const {
      accessUser, loadAccess, notice, renderEditor, renderUserList, saveAccess, userInstruction,
    } = managementPart("PortalManagementUsers").createUsers({
      $, baselines, invalidate, me, openSection, panelState, remember, saveUserDraft, state, userDrafts,
    });
    managementPart("PortalManagementAccounts").createAccounts({
      $, accessUser, allowLeave, closeAccountDialog, invalidate, isBusy, loadAccess, me, notice, openAccountDialog,
      openSection, panelState, remember, run, saveAccess, userInstruction,
    });

    if (allows.CreateUser === true) await loadAccess(options.principalId || me.username || "");
    panelState.currentPrincipalId = state.selectedUsername;
    showView();
    const initialView = allows.CreateUser === true ? "users" : allows.ListResourceTypes === true ? "resource-types" : (allows.ListProviders === true || allows.RegisterProviderAccount === true) ? "backend" : (allows.GetExecutionSettings === true || allows.UpdateExecutionSettings === true) ? "execution" : "network";
    await openSection({view: options.view || initialView, principalId: options.principalId, accountId: options.accountId, createAccount: options.createAccount});
    return { username: String(me.username || ""), openSection, invalidate, isBusy, hasUnsavedChanges,
      destroy() {
        panelState.destroyed = true; disposePermission();
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
