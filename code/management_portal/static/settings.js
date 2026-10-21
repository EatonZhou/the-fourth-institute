(function () {
  "use strict";

  if (window.PortalSettingsPanel) return;

  const COLOR_PRESETS = [
    ["", "默认"], ["#0f766e", "青绿"], ["#3563c6", "湖蓝"],
    ["#7952b3", "鸢紫"], ["#aa4f72", "玫瑰"], ["#a26c30", "琥珀"],
  ];

  function appearanceMarkup() {
    return window.PortalI18n.html`
      <div data-settings-appearance role="tabpanel" aria-label="Appearance" hidden>
        <section class="menuRow settingsAutoColors" aria-label="Auto 配色">
          <div class="menuRowCopy"><strong>Auto 配色</strong><small>为你的消息气泡、引用、链接和按钮选择颜色。随系统适配浅色与深色外观。</small></div>
          <div class="settingsColorLayout menuRowEditor">
            <div class="settingsColorControls">
              <fieldset class="settingsColorPresets"><legend>主题色</legend>
                <div class="settingsColorSwatches">${COLOR_PRESETS.map(([color, label]) => window.PortalI18n.html`
                  <label title="${window.PortalI18n.t(label)}"><input type="radio" name="portal-auto-color" data-settings-color-preset value="${color}" aria-label="${window.PortalI18n.t(label)}" />
                    <span style="--swatch:${color || 'linear-gradient(135deg, #0f766e 50%, #8ec7ff 50%)'}" aria-hidden="true"></span></label>`).join("")}</div>
              </fieldset>
              <label class="settingsCustomColor"><span>自定义颜色</span><input type="color" data-settings-color-custom value="#0f766e" /><output data-settings-color-hex>#0F766E</output></label>
              <label class="settingsBubbleStrength"><span>气泡不透明度</span><input type="range" min="0" max="100" step="1" value="18" data-settings-bubble-strength /><output data-settings-bubble-value>18%</output><small>0% 融入背景，100% 为纯色。</small></label>
              <div class="settingsQuoteColor">
                <label class="settingsQuoteColorMode"><span>引用底色</span><select data-settings-quote-mode><option value="auto">跟随气泡</option><option value="custom">自定义</option></select></label>
                <label class="settingsCustomColor" data-settings-quote-custom-row hidden><span>自定义引用底色</span><input type="color" data-settings-quote-custom value="#0c5c56" /><output data-settings-quote-hex></output></label>
              </div>
              <div class="settingsColorActions"><button class="portalButton is-compact is-quiet" type="button" data-settings-colors-reset>恢复默认</button><span class="settingsColorStatus" data-settings-colors-status role="status" aria-live="polite">更改会自动保存。</span></div>
              <div class="settingsAutoModeNote" data-settings-auto-note hidden><span>当前使用其他主题，配色将在 Auto 模式生效。</span><button class="portalButton is-compact" type="button" data-settings-use-auto>使用 Auto</button></div>
            </div>
            <div class="settingsColorPreview" data-portal-auto-preview aria-label="聊天配色预览">
              <div class="settingsColorPreviewTitle"><span>效果预览</span><span>Auto</span></div>
              <div class="settingsPreviewMessages">
                <div class="messageItem human messageIncoming"><div class="messageBubble">下午好，下一步的计划整理好了。</div></div>
                <div class="messageItem human"><div class="messageBubble"><div class="messageQuote"><span class="messageQuoteAuthor">项目进展</span><span class="messageQuoteText">下一步的计划整理好了。</span></div><span>收到，我们按这个计划继续。</span></div></div>
                <div class="settingsPreviewReply"><strong>Assistant</strong><span>好的，随时可以查看进展。</span><span class="settingsPreviewLink">查看详情 →</span></div>
              </div>
              <div class="settingsPreviewComposer"><span>发送消息…</span><span class="settingsPreviewSend" aria-hidden="true">↑</span></div>
            </div>
          </div>
        </section>
        <section class="menuRow settingsLanguageRow" aria-label="Language">${window.PortalI18n.picker()}</section>
        <section class="menuRow" aria-label="Motion preference">
          <div class="menuRowCopy"><strong>Motion</strong><small>Control animated status and interface effects.</small></div>
          <fieldset class="settingsMotionFieldset" aria-label="Motion preference"><div class="settingsMotionOptions">
            <label><input data-settings-motion type="radio" name="portal-motion" value="system" /><span>System</span></label>
            <label><input data-settings-motion type="radio" name="portal-motion" value="full" /><span>Full</span></label>
            <label><input data-settings-motion type="radio" name="portal-motion" value="reduced" /><span>Reduced</span></label>
          </div><span class="settingsColorStatus settingsMotionStatus" data-settings-motion-status role="status" aria-live="polite"></span></fieldset>
        </section>
      </div>`;
  }

  function panelMarkup() {
    return window.PortalI18n.html`
      <div class="residentMenuIdentity userMenuIdentity">
        <div class="residentMenuPortrait" data-menu-user-avatar></div>
        <div class="residentMenuIdentityCopy">
          <div class="residentMenuName"><strong data-menu-user-name></strong></div>
          <div class="residentMenuIdRow" data-menu-user-id-row><code data-menu-user-id></code></div>
        </div>
      </div>
      <div class="userMenuBody"><div class="nodeSettingsTabs" role="tablist" aria-label="User Menu" aria-orientation="vertical">
        <button class="nodeSettingsTab" type="button" role="tab" aria-selected="true" data-settings-tab="settings">Settings</button>
        <button class="nodeSettingsTab" type="button" role="tab" aria-selected="false" tabindex="-1" data-settings-tab="appearance">Appearance</button>
        <button class="nodeSettingsTab" type="button" role="tab" aria-selected="false" tabindex="-1" data-settings-tab="package">Package</button>
      </div>
      <div class="userMenuPanels">
      <div class="settingsNotice" data-settings-notice role="status" hidden></div>
      ${appearanceMarkup()}
      <div data-settings-package role="tabpanel" aria-label="Package" hidden></div>
      <div data-settings-content role="tabpanel" aria-label="Settings">
      <section aria-label="User avatar" data-settings-avatar></section>
      <section class="menuRow" aria-label="Password">
        <div class="menuRowCopy"><strong>Password</strong><small>Change the password used to sign in to Portal.</small></div>
        <button class="portalButton is-compact" data-settings-password-open type="button">Change password</button>
        <form class="settingsPasswordForm menuRowEditor" data-settings-password-form hidden>
          <p class="settingsPasswordRequirements" id="settings-password-requirements">${window.PortalI18n.t(window.PortalIdentity.passwordRequirements)}</p>
          <div class="settingsPasswordGrid">
            <label class="portalField">
              <span>Current password</span>
              <input data-settings-current-password type="password" required autocomplete="current-password" />
            </label>
            <label class="portalField">
              <span>New password</span>
              <input data-settings-new-password type="password" required maxlength="256" autocomplete="new-password" aria-label="New password" aria-describedby="settings-password-requirements" />
            </label>
            <label class="portalField">
              <span>Confirm new password</span>
              <input data-settings-confirm-password type="password" required maxlength="256" autocomplete="new-password" />
            </label>
          </div>
          <label class="portalField settingsPasswordVerification" data-settings-password-verification hidden>
            <span>Authenticator or recovery code</span>
            <input data-settings-password-verification-code type="text" autocomplete="one-time-code" />
          </label>
          <div class="settingsPasswordActions">
            <button class="portalButton" data-settings-password-cancel type="button">Cancel</button>
            <button class="portalButton is-primary" data-settings-password-submit type="submit">Change password</button>
          </div>
        </form>
      </section>
      <section class="menuRow settingsTwoFactor" data-settings-two-factor aria-label="Two-factor authentication">
          <div class="menuRowCopy">
            <strong>Two-factor authentication</strong>
            <small data-settings-two-factor-status>Loading security status…</small>
          </div>
          <div class="menuRowActions">
            <button class="portalButton is-compact" data-settings-two-factor-enable type="button" hidden>Set up authenticator</button>
            <button class="portalButton is-compact" data-settings-two-factor-manage type="button" hidden>Manage</button>
          </div>
          <form class="settingsTwoFactorSetup menuRowEditor" data-settings-two-factor-setup hidden>
            <label class="portalField">
              <span>Current password</span>
              <input data-settings-two-factor-setup-password type="password" autocomplete="current-password" required />
            </label>
            <button class="portalButton" data-settings-two-factor-setup-cancel type="button">Cancel</button>
            <button class="portalButton is-primary" type="submit">Continue</button>
          </form>
          <form class="settingsTwoFactorConfirm menuRowEditor" data-settings-two-factor-confirm hidden>
            <img data-settings-two-factor-qr alt="Authenticator setup QR code" />
            <div class="settingsTwoFactorConfirmCopy">
              <p>Scan this QR code with your authenticator. If scanning is unavailable, enter this key manually:</p>
              <code data-settings-two-factor-secret></code>
              <label class="portalField">
                <span>6-digit verification code</span>
                <input data-settings-two-factor-confirm-code type="text" inputmode="numeric" autocomplete="one-time-code" required />
              </label>
              <button class="portalButton is-primary" type="submit">Enable two-factor authentication</button>
            </div>
          </form>
          <div class="settingsTwoFactorEnabled menuRowEditor" data-settings-two-factor-enabled hidden>
            <div class="settingsPasswordGrid">
              <label class="portalField">
                <span>Current password</span>
                <input data-settings-two-factor-password type="password" autocomplete="current-password" />
              </label>
              <label class="portalField">
                <span>Authenticator or recovery code</span>
                <input data-settings-two-factor-code type="text" autocomplete="one-time-code" />
              </label>
            </div>
            <div class="settingsTwoFactorActions">
              <button class="portalButton" data-settings-two-factor-manage-cancel type="button">Cancel</button>
              <button class="portalButton" data-settings-two-factor-regenerate type="button">Generate new recovery codes</button>
              <button class="portalButton is-danger" data-settings-two-factor-disable type="button">Disable two-factor authentication</button>
            </div>
          </div>
          <div class="settingsRecoveryCodes menuRowEditor" data-settings-recovery-codes hidden>
            <strong>Save these recovery codes now</strong>
            <p>Each code works once. They will not be shown again.</p>
            <pre data-settings-recovery-code-list></pre>
            <button class="portalButton" data-settings-recovery-copy type="button">Copy recovery codes</button>
          </div>
      </section>
      <section class="menuRow settingsPasskeys" data-settings-passkeys aria-label="Passkeys">
          <div class="menuRowCopy">
            <strong>Passkeys</strong>
            <small data-settings-passkeys-status>Loading Passkeys…</small>
          </div>
          <button class="portalButton is-compact" data-settings-passkeys-add data-portal-icon="plus" type="button">Add Passkey</button>
          <form class="settingsPasskeySetup menuRowEditor" data-settings-passkey-setup hidden>
            <div class="settingsPasskeySetupFields">
              <label class="portalField">
                <span>Passkey name</span>
                <input data-settings-passkey-name type="text" maxlength="80" value="This device" required />
              </label>
              <label class="portalField">
                <span>Current password</span>
                <input data-settings-passkey-password type="password" autocomplete="current-password" required />
              </label>
              <label class="portalField" data-settings-passkey-verification hidden>
                <span>Authenticator or recovery code</span>
                <input data-settings-passkey-code type="text" autocomplete="one-time-code" />
              </label>
            </div>
            <div class="settingsPasskeySetupActions">
              <button class="portalButton" data-settings-passkey-cancel type="button">Cancel</button>
              <button class="portalButton is-primary" type="submit">Register Passkey</button>
            </div>
          </form>
          <div class="settingsPasskeyList menuRowEditor" data-settings-passkey-list></div>
      </section>
      <section class="menuRow userMenuTokens" data-user-tokens aria-label="Personal access tokens"></section>
      </div></div></div>`;
  }

  async function mount(root, options = {}) {
    if (!root) throw new Error("Settings panel host is unavailable.");
    // The identity header, the tab rail, the rows and the avatar editor are the
    // other Menus'.
    const { loadAsset } = window.PortalUIPrimitives;
    await loadAsset("/static/portal_resident_menu.css").catch(() => {});
    const avatarModule = loadAsset("/static/portal_avatar_editor.js", { ready: () => window.PortalAvatarEditor });
    root.classList.add("userMenu");
    root.innerHTML = panelMarkup();
    let destroyed = false;
    let pending = 0;
    let packageInstance = null;
    let enrollment = null;
    let packageEpoch = 0;
    let confirming = false;
    let avatarEditor = null;
    const track = callback => async (...args) => {
      if (destroyed) return;
      pending += 1;
      try { return await callback(...args); } finally { pending -= 1; }
    };
    const isBusy = () => pending > 0 || avatarEditor?.isBusy() || enrollment?.isBusy() || packageInstance?.isBusy?.() === true;
    const hasUnsavedChanges = () => avatarEditor?.hasUnsavedChanges() || enrollment?.hasUnsavedChanges() || settingsDirty() || packageInstance?.hasUnsavedChanges?.() === true;
    const disposePackage = () => {
      packageEpoch += 1;
      packageInstance?.destroy?.(); packageInstance = null;
      root.querySelector("[data-settings-package]").replaceChildren();
    };
    const noticeBox = root.querySelector("[data-settings-notice]");
    const motionInputs = [...root.querySelectorAll("[data-settings-motion]")];
    const passwordOpen = root.querySelector("[data-settings-password-open]");
    const passwordForm = root.querySelector("[data-settings-password-form]");
    const currentPasswordInput = root.querySelector("[data-settings-current-password]");
    const newPasswordInput = root.querySelector("[data-settings-new-password]");
    const confirmPasswordInput = root.querySelector("[data-settings-confirm-password]");
    const passwordVerification = root.querySelector("[data-settings-password-verification]");
    const passwordVerificationCode = root.querySelector("[data-settings-password-verification-code]");
    const passwordCancel = root.querySelector("[data-settings-password-cancel]");
    const passwordSubmit = root.querySelector("[data-settings-password-submit]");
    const twoFactorStatus = root.querySelector("[data-settings-two-factor-status]");
    const twoFactorEnable = root.querySelector("[data-settings-two-factor-enable]");
    const twoFactorManage = root.querySelector("[data-settings-two-factor-manage]");
    const twoFactorSetup = root.querySelector("[data-settings-two-factor-setup]");
    const twoFactorSetupPassword = root.querySelector("[data-settings-two-factor-setup-password]");
    const twoFactorConfirm = root.querySelector("[data-settings-two-factor-confirm]");
    const twoFactorQr = root.querySelector("[data-settings-two-factor-qr]");
    const twoFactorSecret = root.querySelector("[data-settings-two-factor-secret]");
    const twoFactorConfirmCode = root.querySelector("[data-settings-two-factor-confirm-code]");
    const twoFactorEnabled = root.querySelector("[data-settings-two-factor-enabled]");
    const twoFactorPassword = root.querySelector("[data-settings-two-factor-password]");
    const twoFactorCode = root.querySelector("[data-settings-two-factor-code]");
    const twoFactorRegenerate = root.querySelector("[data-settings-two-factor-regenerate]");
    const twoFactorDisable = root.querySelector("[data-settings-two-factor-disable]");
    const recoveryPanel = root.querySelector("[data-settings-recovery-codes]");
    const recoveryCodeList = root.querySelector("[data-settings-recovery-code-list]");
    const recoveryCopy = root.querySelector("[data-settings-recovery-copy]");
    const passkeyStatus = root.querySelector("[data-settings-passkeys-status]");
    const passkeyAdd = root.querySelector("[data-settings-passkeys-add]");
    const passkeySetup = root.querySelector("[data-settings-passkey-setup]");
    const passkeyName = root.querySelector("[data-settings-passkey-name]");
    const passkeyPassword = root.querySelector("[data-settings-passkey-password]");
    const passkeyVerification = root.querySelector("[data-settings-passkey-verification]");
    const passkeyCode = root.querySelector("[data-settings-passkey-code]");
    const passkeyCancel = root.querySelector("[data-settings-passkey-cancel]");
    const passkeyList = root.querySelector("[data-settings-passkey-list]");
    const state = { preferences: {}, twoFactor: {}, passkeys: {}, setupToken: "", recoveryCodes: [] };
    const settingsDirty = () => Boolean(state.setupToken) ||
      [passwordForm, twoFactorSetup, twoFactorConfirm, twoFactorEnabled, passkeySetup]
        .some(panel => !panel.hidden && [...panel.querySelectorAll("input")]
          .some(input => !input.closest("[hidden]") && input.value !== input.defaultValue));

    const notice = (message, tone = "") => {
      if (destroyed) return;
      noticeBox.hidden = !message;
      noticeBox.className = `settingsNotice${tone ? ` ${tone}` : ""}`;
      noticeBox.textContent = message || "";
    };
    const renderPreferences = () => {
      const languageSelect = root.querySelector("[data-portal-language]");
      if (languageSelect) languageSelect.value = window.PortalI18n.preference();
      const motion = ["system", "full", "reduced"].includes(state.preferences?.appearance?.motion)
        ? state.preferences.appearance.motion
        : "system";
      motionInputs.forEach(input => { input.checked = input.value === motion; });
    };
    const colorPreview = root.querySelector("[data-portal-auto-preview]");
    const colorCustom = root.querySelector("[data-settings-color-custom]");
    const colorPresets = [...root.querySelectorAll("[data-settings-color-preset]")];
    const bubbleStrength = root.querySelector("[data-settings-bubble-strength]");
    const colorReset = root.querySelector("[data-settings-colors-reset]");
    const colorStatus = root.querySelector("[data-settings-colors-status]");
    const quoteMode = root.querySelector("[data-settings-quote-mode]");
    const quoteCustom = root.querySelector("[data-settings-quote-custom]");
    const colorControls = [...colorPresets, colorCustom, bubbleStrength, quoteMode, quoteCustom, colorReset];
    colorControls.forEach(control => { control.disabled = true; });
    const savedColors = () => window.PortalTheme.normalizeAutoColors(state.preferences?.appearance?.auto_colors);
    const renderColors = value => {
      const colors = window.PortalTheme.paintAutoColors(colorPreview, value);
      colorPresets.forEach(input => { input.checked = input.value === colors.accent; });
      colorCustom.value = colors.accent || "#0f766e";
      root.querySelector("[data-settings-color-hex]").textContent = colors.accent ? colors.accent.toUpperCase() : window.PortalI18n.t("跟随系统");
      bubbleStrength.value = colors.bubble_intensity;
      root.querySelector("[data-settings-bubble-value]").textContent = `${colors.bubble_intensity}%`;
      bubbleStrength.setAttribute("aria-valuetext", `${colors.bubble_intensity}%`);
      quoteMode.value = colors.quote_color ? "custom" : "auto";
      root.querySelector("[data-settings-quote-custom-row]").hidden = !colors.quote_color;
      quoteCustom.value = colors.quote_color || "#0c5c56";
      root.querySelector("[data-settings-quote-hex]").textContent = colors.quote_color.toUpperCase();
    };
    const renderAutoMode = () => {
      root.querySelector("[data-settings-auto-note]").hidden = window.PortalTheme.mode() === "auto";
    };
    const saveColors = track(async value => {
      const previous = savedColors();
      const colors = window.PortalTheme.normalizeAutoColors(value);
      renderColors(colors);
      colorStatus.classList.remove("error");
      colorStatus.textContent = window.PortalI18n.t("正在保存…");
      colorControls.forEach(control => { control.disabled = true; });
      try {
        await window.PortalUserSettings.update({ appearance: { auto_colors: colors } });
        state.preferences = window.PortalUserSettings.read();
        if (destroyed) return;
        renderColors(savedColors());
        colorStatus.textContent = window.PortalI18n.t("已保存到你的账号。");
      } catch (error) {
        if (destroyed) return;
        renderColors(previous);
        colorStatus.classList.add("error");
        colorStatus.textContent = window.PortalI18n.t("保存失败，已恢复原配色。请重试。");
      } finally {
        if (!destroyed) colorControls.forEach(control => { control.disabled = false; });
      }
    });
    colorPresets.forEach(input => input.addEventListener("change", () => saveColors({ ...savedColors(), accent: input.value })));
    colorCustom.addEventListener("input", () => renderColors({ ...savedColors(), accent: colorCustom.value }));
    colorCustom.addEventListener("change", () => saveColors({ ...savedColors(), accent: colorCustom.value }));
    bubbleStrength.addEventListener("input", () => renderColors({ ...savedColors(), bubble_intensity: Number(bubbleStrength.value) }));
    bubbleStrength.addEventListener("change", () => saveColors({ ...savedColors(), bubble_intensity: Number(bubbleStrength.value) }));
    quoteMode.addEventListener("change", () => saveColors({ ...savedColors(), quote_color: quoteMode.value === "custom"
      ? getComputedStyle(colorPreview).getPropertyValue("--portal-message-quote-background").trim() : "" }));
    quoteCustom.addEventListener("input", () => renderColors({ ...savedColors(), quote_color: quoteCustom.value }));
    quoteCustom.addEventListener("change", () => saveColors({ ...savedColors(), quote_color: quoteCustom.value }));
    colorReset.addEventListener("click", () => saveColors({}));
    root.querySelector("[data-settings-use-auto]").addEventListener("click", () => window.PortalTheme.setMode("auto"));
    window.addEventListener("portal:theme-changed", renderAutoMode);
    const renderTwoFactor = () => {
      const enabled = state.twoFactor?.enabled === true;
      const remaining = Number(state.twoFactor?.recovery_codes_remaining || 0);
      twoFactorStatus.textContent = enabled
        ? (remaining === 1 ? window.PortalI18n.t("Enabled · 1 recovery code remaining") : window.PortalI18n.text`Enabled · ${remaining} recovery codes remaining`)
        : window.PortalI18n.t("Off. Add an authenticator code after your password at sign-in.");
      if (enabled) twoFactorSetup.hidden = true;
      else twoFactorEnabled.hidden = true;
      twoFactorEnable.hidden = enabled || !twoFactorSetup.hidden || !twoFactorConfirm.hidden;
      twoFactorManage.hidden = !enabled || !twoFactorEnabled.hidden;
      passwordVerification.hidden = !enabled;
    };
    const closeTwoFactorManage = () => {
      twoFactorPassword.value = "";
      twoFactorCode.value = "";
      twoFactorEnabled.hidden = true;
      renderTwoFactor();
    };
    const formatPasskeyTime = value => {
      const timestamp = Number(value || 0);
      return timestamp
        ? window.PortalUI.formatDateTime(timestamp, { unixSeconds: true })
        : "Never used";
    };
    const renderPasskeys = () => {
      const supported = Boolean(window.PublicKeyCredential && navigator.credentials);
      const credentials = Array.isArray(state.passkeys?.credentials)
        ? state.passkeys.credentials
        : [];
      passkeyAdd.disabled = !supported;
      passkeyStatus.textContent = supported
        ? (credentials.length
            ? window.PortalI18n.text`${credentials.length} registered`
            : window.PortalI18n.t("No Passkeys registered."))
        : window.PortalI18n.t("This browser does not support Passkeys.");
      passkeyVerification.hidden = state.twoFactor?.enabled !== true;
      passkeyList.replaceChildren();
      credentials.forEach(credential => {
        const row = document.createElement("div");
        row.className = "settingsPasskeyRow";
        const copy = document.createElement("div");
        const name = document.createElement("strong");
        name.textContent = credential.name || "Passkey";
        const metadata = document.createElement("small");
        metadata.textContent = credential.last_used_at
          ? window.PortalI18n.text`Last used ${formatPasskeyTime(credential.last_used_at)}`
          : window.PortalI18n.text`Added ${formatPasskeyTime(credential.created_at)}`;
        copy.append(name, metadata);
        const remove = document.createElement("button");
        remove.className = "portalButton is-danger";
        remove.type = "button";
        remove.dataset.settingsPasskeyDelete = credential.credential_id;
        remove.dataset.portalIcon = "trash";
        remove.setAttribute("aria-label", window.PortalI18n.text`Delete ${credential.name || "Passkey"}`);
        remove.title = window.PortalI18n.t("Delete Passkey");
        row.append(copy, remove);
        passkeyList.appendChild(row);
      });
    };
    const closePasswordEditor = () => {
      passwordForm.reset();
      passwordForm.hidden = true;
      passwordOpen.hidden = false;
    };
    const loadSecurity = async () => {
      const result = await window.PortalAPI.get("/api/account/security");
      state.twoFactor = result.two_factor || {};
      state.passkeys = result.passkeys || {};
      renderTwoFactor();
      renderPasskeys();
    };
    const showRecoveryCodes = codes => {
      state.recoveryCodes = Array.isArray(codes) ? codes : [];
      recoveryCodeList.textContent = state.recoveryCodes.join("\n");
      recoveryPanel.hidden = state.recoveryCodes.length === 0;
    };
    const motionStatus = root.querySelector("[data-settings-motion-status]");
    const saveMotion = async event => {
      const previous = window.PortalMotion?.mode?.() || "system";
      const motion = event.currentTarget.value;
      motionInputs.forEach(input => { input.disabled = true; });
      motionStatus.classList.remove("error");
      motionStatus.textContent = window.PortalI18n.t("正在保存…");
      window.PortalMotion?.apply?.(motion);
      try {
        await window.PortalUserSettings.update({appearance: {motion}});
        state.preferences = window.PortalUserSettings.read();
        window.PortalMotion?.apply?.(state.preferences?.appearance?.motion);
        renderPreferences();
        motionStatus.textContent = window.PortalI18n.t("Setting saved.");
      } catch (error) {
        window.PortalMotion?.apply?.(previous);
        renderPreferences();
        motionStatus.textContent = error.message || window.PortalI18n.t("Unable to save setting.");
        motionStatus.classList.add("error");
      } finally {
        motionInputs.forEach(input => { input.disabled = false; });
      }
    };
    const changePassword = async event => {
      event.preventDefault();
      const strengthError = window.PortalIdentity.passwordStrengthError(newPasswordInput.value);
      if (strengthError) {
        notice(strengthError, "error");
        newPasswordInput.focus();
        return;
      }
      if (newPasswordInput.value !== confirmPasswordInput.value) {
        notice(window.PortalI18n.t("The new passwords do not match."), "error");
        confirmPasswordInput.focus();
        return;
      }
      if (state.twoFactor?.enabled === true && !passwordVerificationCode.value.trim()) {
        notice(window.PortalI18n.t("Enter an authenticator or recovery code."), "error");
        passwordVerificationCode.focus();
        return;
      }
      const approved = await window.PortalUI.confirm({
        title: window.PortalI18n.t("Change password?"),
        message: window.PortalI18n.t("Your password will be replaced and your other Portal sessions will be signed out."),
        confirmLabel: window.PortalI18n.t("Change password"),
        tone: "primary",
      });
      if (!approved) return;
      const controls = [...passwordForm.querySelectorAll("input, button")];
      controls.forEach(control => { control.disabled = true; });
      try {
        await window.PortalAPI.post("/api/account/password", {
          current_password: currentPasswordInput.value,
          new_password: newPasswordInput.value,
          new_password_confirmation: confirmPasswordInput.value,
          verification_code: passwordVerificationCode.value.trim(),
        });
        closePasswordEditor();
        notice(window.PortalI18n.t("Password changed. Other sessions have been signed out."), "success");
      } catch (error) {
        notice(error.message || window.PortalI18n.t("Unable to change password."), "error");
      } finally {
        controls.forEach(control => { control.disabled = false; });
        passwordSubmit.disabled = false;
      }
    };
    const startTwoFactorSetup = async event => {
      event.preventDefault();
      const controls = [...twoFactorSetup.querySelectorAll("input, button")];
      controls.forEach(control => { control.disabled = true; });
      try {
        const result = await window.PortalAPI.post("/api/account/two-factor/setup", {
          current_password: twoFactorSetupPassword.value,
        });
        state.setupToken = result.setup_token || "";
        twoFactorQr.src = result.qr_code_data_url || "";
        twoFactorSecret.textContent = result.secret || "";
        twoFactorSetup.hidden = true;
        twoFactorConfirm.hidden = false;
        twoFactorConfirmCode.focus();
        notice(window.PortalI18n.t("Scan the code, then confirm with the current authenticator code."), "success");
      } catch (error) {
        notice(error.message || window.PortalI18n.t("Unable to start two-factor setup."), "error");
      } finally {
        controls.forEach(control => { control.disabled = false; });
      }
    };
    const confirmTwoFactorSetup = async event => {
      event.preventDefault();
      const controls = [...twoFactorConfirm.querySelectorAll("input, button")];
      controls.forEach(control => { control.disabled = true; });
      try {
        const result = await window.PortalAPI.post("/api/account/two-factor/confirm", {
          setup_token: state.setupToken,
          code: twoFactorConfirmCode.value,
        });
        twoFactorConfirm.hidden = true;
        twoFactorConfirm.reset();
        state.setupToken = "";
        showRecoveryCodes(result.recovery_codes);
        await loadSecurity();
        notice(window.PortalI18n.t("Two-factor authentication enabled. Other sessions have been signed out."), "success");
      } catch (error) {
        notice(error.message || window.PortalI18n.t("Unable to confirm two-factor setup."), "error");
      } finally {
        controls.forEach(control => { control.disabled = false; });
      }
    };

    const closePasskeySetup = () => {
      passkeySetup.reset();
      passkeyName.value = "This device";
      passkeySetup.hidden = true;
      passkeyAdd.hidden = false;
    };
    const registerPasskey = async event => {
      event.preventDefault();
      if (state.twoFactor?.enabled === true && !passkeyCode.value.trim()) {
        notice(window.PortalI18n.t("Enter an authenticator or recovery code."), "error");
        passkeyCode.focus();
        return;
      }
      const controls = [...passkeySetup.querySelectorAll("input, button")];
      controls.forEach(control => { control.disabled = true; });
      try {
        const helper = window.PortalPasskeys;
        if (!helper) throw new Error("Passkey support is unavailable.");
        await helper.register({
          name: passkeyName.value.trim(),
          current_password: passkeyPassword.value,
          verification_code: passkeyCode.value.trim(),
        });
        closePasskeySetup();
        await loadSecurity();
        notice(window.PortalI18n.t("Passkey registered. You can now sign in without a password."), "success");
      } catch (error) {
        const helper = window.PortalPasskeys;
        notice(helper?.userFacingError?.(error) || error.message || window.PortalI18n.t("Unable to register Passkey."), "error");
      } finally {
        controls.forEach(control => { control.disabled = false; });
      }
    };
    const deletePasskey = async button => {
      const credentialId = button.dataset.settingsPasskeyDelete || "";
      const name = button.closest(".settingsPasskeyRow")?.querySelector("strong")?.textContent || "this Passkey";
      const approved = await window.PortalUI.confirm({
        title: window.PortalI18n.t("Delete Passkey?"),
        message: `${name} will no longer be available for Portal sign-in.`,
        confirmLabel: window.PortalI18n.t("Delete Passkey"),
        tone: "danger",
      });
      if (!approved) return;
      button.disabled = true;
      try {
        await window.PortalAPI.post("/api/account/passkeys/delete", {
          credential_id: credentialId,
        });
        await loadSecurity();
        notice(window.PortalI18n.t("Passkey deleted."), "success");
      } catch (error) {
        button.disabled = false;
        notice(error.message || window.PortalI18n.t("Unable to delete Passkey."), "error");
      }
    };
    const twoFactorAction = async action => {
      if (!twoFactorPassword.value || !twoFactorCode.value) {
        notice(window.PortalI18n.t("Enter your current password and authenticator or recovery code."), "error");
        return;
      }
      const controls = [twoFactorPassword, twoFactorCode, twoFactorRegenerate, twoFactorDisable];
      controls.forEach(control => { control.disabled = true; });
      try {
        const result = await window.PortalAPI.post(`/api/account/two-factor/${action}`, {
          current_password: twoFactorPassword.value,
          verification_code: twoFactorCode.value,
        });
        if (action === "recovery-codes") {
          showRecoveryCodes(result.recovery_codes);
          notice(window.PortalI18n.t("New recovery codes generated. Previous recovery codes no longer work."), "success");
        } else {
          recoveryPanel.hidden = true;
          notice(window.PortalI18n.t("Two-factor authentication disabled. Other sessions have been signed out."), "success");
        }
        closeTwoFactorManage();
        await loadSecurity();
      } catch (error) {
        notice(error.message || window.PortalI18n.t("Unable to update two-factor authentication."), "error");
      } finally {
        controls.forEach(control => { control.disabled = false; });
      }
    };

    motionInputs.forEach(input => input.addEventListener("change", track(saveMotion)));
    passwordOpen.addEventListener("click", () => {
      passwordOpen.hidden = true;
      passwordForm.hidden = false;
      currentPasswordInput.focus();
    });
    passwordCancel.addEventListener("click", closePasswordEditor);
    passwordForm.addEventListener("submit", track(changePassword));
    twoFactorEnable.addEventListener("click", () => {
      twoFactorSetup.hidden = false;
      twoFactorEnable.hidden = true;
      twoFactorSetupPassword.focus();
    });
    root.querySelector("[data-settings-two-factor-setup-cancel]").addEventListener("click", () => {
      twoFactorSetup.reset();
      twoFactorSetup.hidden = true;
      renderTwoFactor();
    });
    twoFactorManage.addEventListener("click", () => {
      twoFactorEnabled.hidden = false;
      twoFactorManage.hidden = true;
      twoFactorPassword.focus();
    });
    root.querySelector("[data-settings-two-factor-manage-cancel]").addEventListener("click", closeTwoFactorManage);
    twoFactorSetup.addEventListener("submit", track(startTwoFactorSetup));
    twoFactorConfirm.addEventListener("submit", track(confirmTwoFactorSetup));
    twoFactorRegenerate.addEventListener("click", track(() => twoFactorAction("recovery-codes")));
    twoFactorDisable.addEventListener("click", track(() => twoFactorAction("disable")));
    recoveryCopy.addEventListener("click", async () => {
      if (await window.PortalUI.copyText(state.recoveryCodes.join("\n"), window.PortalI18n.t("Recovery codes"))) {
        notice(window.PortalI18n.t("Recovery codes copied."), "success");
      }
    });
    passkeyAdd.addEventListener("click", () => {
      passkeyAdd.hidden = true;
      passkeySetup.hidden = false;
      passkeyName.focus();
    });
    passkeyCancel.addEventListener("click", closePasskeySetup);
    passkeySetup.addEventListener("submit", track(registerPasskey));
    passkeyList.addEventListener("click", event => {
      const button = event.target.closest("[data-settings-passkey-delete]");
      if (button) track(deletePasskey)(button);
    });
    const me = options.me || await window.PortalIdentity.load();
    const {mountEnrollment} = await import("/static/portal_user_enrollment.js");
    enrollment = mountEnrollment(root.querySelector("[data-user-tokens]"));
    const displayName = String(me.display_name || me.username || window.PortalI18n.t("User"));
    root.querySelector("[data-menu-user-name]").textContent = displayName;
    root.querySelector("[data-menu-user-id]").textContent = String(me.username || "");
    root.querySelector("[data-menu-user-id-row]").hidden = !me.username || me.username === displayName;
    const portrait = root.querySelector("[data-menu-user-avatar]");
    portrait.textContent = String(me.username || "U").slice(0, 1);
    const tabs = [...root.querySelectorAll("[data-settings-tab]")];
    const content = root.querySelector("[data-settings-content]");
    const packagePanel = root.querySelector("[data-settings-package]");
    const appearancePanel = root.querySelector("[data-settings-appearance]");
    tabs.forEach((button, index) => button.addEventListener("keydown", event => {
      let next;
      if (["ArrowDown", "ArrowRight"].includes(event.key)) next = (index + 1) % tabs.length;
      else if (["ArrowUp", "ArrowLeft"].includes(event.key)) next = (index + tabs.length - 1) % tabs.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = tabs.length - 1;
      if (next === undefined) return;
      event.preventDefault();
      tabs[next].focus();
      tabs[next].click();
    }));
    tabs.forEach(button => button.addEventListener("click", async () => {
      const target = button.dataset.settingsTab;
      if (button.getAttribute("aria-selected") === "true" || confirming || destroyed) return;
      if (isBusy()) { notice(window.PortalI18n.t("操作正在处理，请稍后切换。"), "error"); return; }
      const epoch = packageEpoch;
      if (hasUnsavedChanges()) {
        confirming = true;
        let approved;
        try { approved = await window.PortalUI.confirm({ title: window.PortalI18n.t("放弃未保存的修改？"), message: window.PortalI18n.t("切换后将清除未保存的修改。"), confirmLabel: window.PortalI18n.t("放弃修改"), tone: "danger" }); }
        finally { confirming = false; }
        if (!approved || destroyed || isBusy() || epoch !== packageEpoch) return;
        root.querySelectorAll("[data-settings-content] form").forEach(form => form.reset());
        twoFactorPassword.value = "";
        twoFactorCode.value = "";
        state.setupToken = "";
        twoFactorConfirm.hidden = true;
        twoFactorQr.removeAttribute("src");
        twoFactorSecret.textContent = "";
        avatarEditor?.close();
      }
      disposePackage();
      notice("");
      content.hidden = target !== "settings";
      packagePanel.hidden = target !== "package";
      appearancePanel.hidden = target !== "appearance";
      tabs.forEach(tab => { const selected = tab === button; tab.setAttribute("aria-selected", String(selected)); tab.tabIndex = selected ? 0 : -1; });
      if (target !== "package") return;
      const current = packageEpoch;
      const container = document.createElement("div");
      container.textContent = window.PortalI18n.t("正在加载 Package…");
      packagePanel.replaceChildren(container);
      try {
        const instance = await window.PortalNav.mountPrincipalDetails({container, principalId: String(me.username || "")});
        if (destroyed || current !== packageEpoch) { instance?.destroy?.(); return; }
        packageInstance = instance;
      } catch (error) { if (!destroyed && current === packageEpoch) container.textContent = window.PortalI18n.text`Package 加载失败：${error.message}`; }
    }));
    // The header portrait previews the unsaved choice; Cancel puts the saved one back.
    void avatarModule.then(() => {
      if (destroyed) return;
      avatarEditor = window.PortalAvatarEditor.mount(root.querySelector("[data-settings-avatar]"), {
        portrait, label: me.username,
        load: async () => (await window.PortalAPI.get("/api/account/avatar")).avatar || me.avatar,
        save: async avatar => (await window.PortalInstructionClient.execute("UpdateUser", {user_id: me.username, changes: {avatar}}, {
          idempotencyKey: `avatar:${globalThis.PortalUIPrimitives.randomUUID()}`,
        })).user.avatar,
        notice,
        onSaved: avatar => window.dispatchEvent(new CustomEvent("portal:user-avatar-changed", {detail: {username: me.username, avatar}})),
      });
    }).catch(error => {
      if (!destroyed) notice(error.message || window.PortalI18n.t("头像加载失败"), "error");
    });
    state.preferences = await window.PortalIdentity.loadPreferences();
    renderPreferences();
    renderColors(savedColors());
    renderAutoMode();
    colorControls.forEach(control => { control.disabled = false; });
    await loadSecurity();
    return { username: String(me.username || ""), isBusy, hasUnsavedChanges,
      destroy() {
        destroyed = true; avatarEditor?.destroy(); disposePackage();
        window.removeEventListener("portal:theme-changed", renderAutoMode);
        enrollment?.destroy();
        root.querySelectorAll("form").forEach(form => form.reset());
        state.setupToken = ""; state.recoveryCodes = [];
        root.replaceChildren();
      },
    };
  }

  window.PortalSettingsPanel = { mount };

  async function initStandalone() {
    const host = document.querySelector("[data-portal-settings-host]");
    if (!host) return;
    window.PortalNav?.mount?.();
    try {
      const result = await mount(host);
      const label = document.querySelector("[data-settings-user]");
      if (label) label.textContent = result.username ? window.PortalI18n.text`Menu · ${result.username}` : window.PortalI18n.t("User Menu");
    } catch (error) {
      host.innerHTML = window.PortalI18n.html`<div class="settingsNotice error" role="alert"></div>`;
      host.firstElementChild.textContent = error.message || window.PortalI18n.t("Unable to load settings.");
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initStandalone, { once: true });
  } else {
    initStandalone();
  }
})();
