(() => {
  "use strict";
  window.PortalWatchboardApplications.register({
    id: "wireguard",
    matches: dashboard => dashboard.view_manifest?.payload?.layout === "wireguard-device-manager",
    create(context) {
      const state = {};
      const esc = context.escapeHtml;
      const portalUi = context.ui;
      const watchboardPayload = context.view.dashboard;
      const showWatchboardModal = context.modal.open;
      const closeWatchboardModal = context.modal.close;
      const runWatchboardCommand = context.commands.run;
      const submitChatPrompt = context.chat.submit;
      const watchboardStatusLabel = context.components.statusLabel;

      function watchboardWireGuardDashboardHtml(dashboard) {
        const collapsed = !!context.view.collapsed;
        const status = dashboard.status?.payload || {};
        const devices = Array.isArray(status.devices) ? status.devices : [];
        const users = Array.isArray(status.users) ? status.users : [];
        const canManage = !!dashboard.permissions?.can_run;
        const running = context.commands.running === "status";
        const userRows = users.map(user => window.PortalI18n.html`<span class="wireguardUserChip">${esc(user.name || "unknown")} · ${esc(String(user.device_count || 0))}</span>`).join("");
        const deviceRows = devices.map(device => {
          const label = String(device.label || "");
          const key = String(device.public_key || "");
          const shortKey = key.length > 18 ? `${key.slice(0, 9)}…${key.slice(-7)}` : key;
          return window.PortalI18n.html`<div class="watchboardCompactItem wireguardDeviceItem">
            <span>
              <strong>${esc(label || window.PortalI18n.t("未命名设备"))}</strong>
              <span>${esc(device.owner || "unknown")} · ${esc(device.os || "unknown")} · ${esc(device.device_type || "device")} · ${esc(device.address || window.PortalI18n.t("未分配 IP"))}</span>
              <span class="wireguardDeviceKey" title="${esc(key)}">${esc(shortKey || window.PortalI18n.t("尚无 public key"))}</span>
            </span>
            <span class="watchboardUserActions">
              <button class="portalButton is-icon is-quiet watchboardIconBtn" type="button" data-wireguard-device-edit="${esc(label)}" title="编辑" aria-label="编辑 ${esc(label)}" data-portal-icon="edit" ${canManage ? "" : "disabled"}></button>
              <button class="portalButton is-icon is-quiet watchboardIconBtn" type="button" data-wireguard-device-delete="${esc(label)}" title="删除" aria-label="删除 ${esc(label)}" data-portal-icon="trash" ${canManage ? "" : "disabled"}></button>
            </span>
          </div>`;
        }).join("");
        return window.PortalI18n.html`<div class="watchboardHead">
          <button class="portalButton is-icon is-quiet iconBtn" type="button" data-watchboard-dashboard-toggle aria-label="${collapsed ? window.PortalI18n.t("展开 Watchboard") : window.PortalI18n.t("收起 Watchboard")}" title="${collapsed ? window.PortalI18n.t("展开") : window.PortalI18n.t("收起")}" data-portal-icon="${collapsed ? "chevron-left" : "chevron-right"}"></button>
          <div class="watchboardTitle">Portal Private Access</div>
        </div>
        <div class="watchboardBody">
          <section class="watchboardSection">
            <div class="watchboardSectionHead"><span>如何加入</span><span>WireGuard</span></div>
            <div class="watchboardSectionBody wireguardGuide">
              <div class="paneNote">本页面用于用户自主注册设备到 WireGuard, 以便在各地都能安全地丝滑访问 Portal (作为公网代替方案). 在右侧的"Add Device"可以发起申请, chat 会引导你进行下一步，直到完成后同步设备列表。</div>
              <ol>
                <li>在电脑或手机安装 WireGuard 官方客户端 (https://www.wireguard.com/install/)。</li>
                <li>创建隧道并只把 <strong>Public Key</strong> 发给我们；Private Key 始终留在设备上。</li>
                <li>点击 Add Device 提交现有信息，Chat 会继续安装、证书和配置引导。</li>
                <li>隧道连接后，在任何网络访问 <strong>${esc(status.portal_url || window.PortalI18n.t("https://10.77.0.1/"))}</strong>。</li>
              </ol>
            </div>
          </section>
          <section class="watchboardSection">
            <div class="watchboardSectionHead"><span>用户</span><span>${esc(String(users.length))}</span></div>
            <div class="watchboardSectionBody"><div class="wireguardUserList">${userRows || window.PortalI18n.html`<span class="paneNote">暂无用户。</span>`}</div></div>
          </section>
          <section class="watchboardSection">
            <div class="watchboardSectionHead"><span>已注册设备</span><span>${esc(String(devices.length))}</span></div>
            <div class="watchboardSectionBody">
              ${status.error ? window.PortalI18n.html`<div class="paneNote consoleError">同步失败：${esc(status.error)}</div>` : ""}
              <div class="watchboardMiniActions">
                <button class="portalButton is-compact is-primary watchboardCommandBtn" type="button" data-wireguard-device-add ${canManage ? "" : "disabled"}>Add Device</button>
                <button class="portalButton is-compact watchboardCommandBtn" type="button" data-wireguard-device-refresh ${canManage && !running ? "" : "disabled"}>${running ? window.PortalI18n.t("Syncing") : window.PortalI18n.t("Refresh")}</button>
              </div>
              <div class="watchboardCompactList">${deviceRows || window.PortalI18n.html`<div class="paneNote">尚未登记设备。</div>`}</div>
              <div class="paneNote">状态：${esc(watchboardStatusLabel(status))} · ${esc(status.updated_at || window.PortalI18n.t("尚未同步"))}</div>
            </div>
          </section>
        </div>`;
      }

      function wireGuardDevices() {
        const devices = watchboardPayload()?.status?.payload?.devices;
        return Array.isArray(devices) ? devices : [];
      }

      function wireGuardDeviceByLabel(label) {
        const target = String(label || "");
        return wireGuardDevices().find(device => String(device.label || "") === target) || null;
      }

      function wireGuardField(overlay, selector) {
        return String(overlay?.querySelector(selector)?.value || "").trim();
      }

      function openWireGuardDeviceModal(label = "") {
        const device = label ? wireGuardDeviceByLabel(label) : null;
        const isEdit = !!device;
        const owner = String(device?.owner || context.view.username || "");
        const os = String(device?.os || "unknown");
        const deviceType = String(device?.device_type || "device");
        const body = window.PortalI18n.html`<div class="watchboardFormStack">
          <div class="watchboardControlRow"><span>用户</span><input type="text" maxlength="80" data-wireguard-owner value="${esc(owner)}" placeholder="例如 user"></div>
          <div class="watchboardControlRow"><span>设备名称</span><input type="text" maxlength="80" data-wireguard-label value="${esc(device?.label || "")}" placeholder="例如 laptop"></div>
          <div class="watchboardControlRow"><span>系统</span><select data-wireguard-os>
            ${["unknown", window.PortalI18n.t("macOS"), window.PortalI18n.t("Windows"), window.PortalI18n.t("Linux"), window.PortalI18n.t("iOS"), window.PortalI18n.t("Android")].map(value => window.PortalI18n.html`<option value="${esc(value)}" ${os === value ? "selected" : ""}>${esc(value)}</option>`).join("")}
          </select></div>
          <div class="watchboardControlRow"><span>设备类型</span><select data-wireguard-device-type>
            ${["device", "desktop", "laptop", "phone", "tablet", "server"].map(value => window.PortalI18n.html`<option value="${esc(value)}" ${deviceType === value ? "selected" : ""}>${esc(value)}</option>`).join("")}
          </select></div>
          <div class="watchboardControlRow"><span>使用方式</span><select data-wireguard-client-mode><option value="app">图形客户端</option><option value="cli">CLI</option><option value="unknown">尚不确定</option></select></div>
          <div class="watchboardControlRow"><span>安装状态</span><select data-wireguard-installed><option value="unknown">尚不确定</option><option value="yes">已安装 WireGuard</option><option value="no">尚未安装</option></select></div>
          <div class="watchboardControlRow"><span>Public Key</span><input type="text" data-wireguard-public-key value="${esc(device?.public_key || "")}" placeholder="可暂不提供" autocomplete="off"></div>
          <div class="watchboardControlRow"><span>备注</span><textarea rows="3" data-wireguard-notes placeholder="网络环境或其他需求">${esc(device?.notes || "")}</textarea></div>
          <div class="paneNote">不要粘贴 WireGuard Private Key。信息不完整也可以提交，Chat 会继续引导。</div>
          <div class="watchboardMiniActions">
            <button class="portalButton is-compact is-primary watchboardCommandBtn" type="button" data-wireguard-device-submit>${isEdit ? window.PortalI18n.t("发送编辑请求") : window.PortalI18n.t("发送到 Chat")}</button>
            ${isEdit ? window.PortalI18n.html`<button class="portalButton is-compact watchboardCommandBtn" type="button" data-wireguard-device-save-direct>直接保存</button>` : ""}
            <button class="portalButton is-compact watchboardCommandBtn" type="button" data-watchboard-modal-close>Close</button>
          </div>
        </div>`;
        showWatchboardModal(isEdit ? "Edit device" : "Add device", body, {
          onBind: overlay => {
            overlay.querySelector("[data-wireguard-device-submit]")?.addEventListener("click", () => submitWireGuardDeviceRequest(overlay, device));
            overlay.querySelector("[data-wireguard-device-save-direct]")?.addEventListener("click", () => saveWireGuardDeviceMetadata(overlay, device));
            overlay.querySelectorAll("[data-watchboard-modal-close]").forEach(btn => btn.addEventListener("click", closeWatchboardModal));
            window.setTimeout(() => overlay.querySelector("[data-wireguard-owner]")?.focus(), 0);
          },
        });
      }

      async function submitWireGuardChatPrompt(prompt, overlay = null) {
        if (typeof submitChatPrompt !== "function") {
          portalUi.toast("当前 Chat 提交接口不可用。", "error");
          return;
        }
        const button = overlay?.querySelector("[data-wireguard-device-submit]");
        if (button) button.disabled = true;
        try {
          await submitChatPrompt(prompt);
          closeWatchboardModal();
          portalUi.toast("请求已发送到当前 Chat。", "success");
        } catch (err) {
          if (button) button.disabled = false;
          portalUi.toast(portalUi.errorMessage("发送设备请求", err), "error");
        }
      }

      async function submitWireGuardDeviceRequest(overlay, existingDevice = null) {
        const owner = wireGuardField(overlay, "[data-wireguard-owner]");
        const label = wireGuardField(overlay, "[data-wireguard-label]");
        const os = wireGuardField(overlay, "[data-wireguard-os]");
        const deviceType = wireGuardField(overlay, "[data-wireguard-device-type]");
        const clientMode = wireGuardField(overlay, "[data-wireguard-client-mode]");
        const installed = wireGuardField(overlay, "[data-wireguard-installed]");
        const publicKey = wireGuardField(overlay, "[data-wireguard-public-key]");
        const notes = wireGuardField(overlay, "[data-wireguard-notes]");
        if (!owner) {
          portalUi.toast(window.PortalI18n.t("请填写设备所属用户。"), "warning");
          overlay?.querySelector("[data-wireguard-owner]")?.focus();
          return;
        }
        const missing = [];
        if (!label) missing.push("设备名称");
        if (!os || os === "unknown") missing.push("操作系统");
        if (installed === "unknown") missing.push("WireGuard 安装状态");
        if (!publicKey) missing.push("Public Key");
        const action = existingDevice
          ? `用户 ${owner} 希望更新已注册设备 ${existingDevice.label}（当前地址 ${existingDevice.address || "未知"}）`
          : `用户 ${owner} 希望把一台设备接入 Portal 的 WireGuard 私网`;
        const prompt = `${action}。\n\n当前提交的信息：\n- 设备名称：${label || "尚未提供"}\n- 设备系统：${os || "尚未提供"}\n- 设备类型：${deviceType || "尚未提供"}\n- 使用方式：${clientMode || "尚未确定"}\n- WireGuard 安装状态：${installed === "yes" ? "已安装" : installed === "no" ? "尚未安装" : "尚未确认"}\n- Public Key：${publicKey || "尚未提供"}\n- 备注：${notes || "无"}\n- 仍缺少：${missing.length ? missing.join("、") : "无"}\n\n请根据当前信息继续注册引导。信息不完整时先告诉用户下一步；信息齐全时完成 VPS 设备注册或更新。不要索取或保存 Private Key。完成实际变更后运行本 Resident Watchboard 的 status 命令，同步设备与用户清单。`;
        await submitWireGuardChatPrompt(prompt, overlay);
      }

      async function saveWireGuardDeviceMetadata(overlay, existingDevice) {
        if (!existingDevice || context.commands.running) return;
        const label = wireGuardField(overlay, "[data-wireguard-label]");
        const publicKey = wireGuardField(overlay, "[data-wireguard-public-key]");
        const owner = wireGuardField(overlay, "[data-wireguard-owner]");
        const os = wireGuardField(overlay, "[data-wireguard-os]");
        const deviceType = wireGuardField(overlay, "[data-wireguard-device-type]");
        const notes = wireGuardField(overlay, "[data-wireguard-notes]");
        if (label !== String(existingDevice.label || "") || publicKey !== String(existingDevice.public_key || "")) {
          portalUi.toast(window.PortalI18n.t("设备名称或 Public Key 会改变 WireGuard 身份，请使用“发送编辑请求”。"), "warning");
          return;
        }
        if (!owner || !os || !deviceType) {
          portalUi.toast(window.PortalI18n.t("直接保存需要填写用户、系统和设备类型。"), "warning");
          return;
        }
        const button = overlay?.querySelector("[data-wireguard-device-save-direct]");
        if (button) button.disabled = true;
        closeWatchboardModal();
        const started = await runWatchboardCommand("save-metadata", watchboardPayload(), {
          label,
          owner,
          os,
          device_type: deviceType,
          notes,
        });
        if (started) portalUi.toast(window.PortalI18n.t("设备资料保存中，完成后清单会自动刷新。"), "success");
      }

      async function requestWireGuardDeviceDelete(label) {
        const device = wireGuardDeviceByLabel(label);
        if (!device) return;
        if (!await portalUi.confirm({
          title: window.PortalI18n.t("撤销设备访问？"),
          message: window.PortalI18n.text`将通过当前 Chat 请求撤销 ${device.label}（${device.owner || "unknown"}，${device.address || window.PortalI18n.t("未分配 IP")}）的 WireGuard 访问。`,
          confirmLabel: window.PortalI18n.t("发送撤销请求"),
        })) return;
        const prompt = `请撤销 WireGuard 设备 ${device.label} 的 Portal 私网访问。当前登记信息：用户 ${device.owner || "unknown"}，地址 ${device.address || "未知"}，Public Key ${device.public_key || "未知"}。请核对精确设备后从 VPS 注册表和 wg0 配置中删除；不要影响其他设备。完成后运行本 Resident Watchboard 的 status 命令，同步设备与用户清单。`;
        await submitWireGuardChatPrompt(prompt);
      }

      function bind(box, dashboard) {
        box.querySelector("[data-wireguard-device-add]")?.addEventListener("click", () => openWireGuardDeviceModal());
        box.querySelector("[data-wireguard-device-refresh]")?.addEventListener("click", () => runWatchboardCommand("status", dashboard));
        box.querySelectorAll("[data-wireguard-device-edit]").forEach(btn => {
          btn.addEventListener("click", () => openWireGuardDeviceModal(btn.dataset.wireguardDeviceEdit || ""));
        });
        box.querySelectorAll("[data-wireguard-device-delete]").forEach(btn => {
          btn.addEventListener("click", () => requestWireGuardDeviceDelete(btn.dataset.wireguardDeviceDelete || ""));
        });
      }
      return { render: dashboard => watchboardWireGuardDashboardHtml(dashboard), bind, key: dashboard => JSON.stringify(state) };
    },
  });
})();
