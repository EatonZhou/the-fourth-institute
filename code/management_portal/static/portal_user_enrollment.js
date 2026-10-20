import { sha256 } from "./portal_sha256.js";

export function newSecret(prefix = "") {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  return prefix + btoa(String.fromCharCode(...bytes)).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

export async function secretDigest(secret) {
  return sha256(new TextEncoder().encode(secret));
}

// The fragment never reaches the server; `for=agent` opens the agent path on /join.
export function invitationLink(secret, audience = "human", origin = location.origin) {
  return `${origin}/join#invite=${secret}${audience === "agent" ? "&for=agent" : ""}`;
}

// A message the inviter can send as is: the link plus where a newcomer should start.
export function handoffText(username, link, audience = "human", days = 7, origin = location.origin) {
  const values = {p0: username, p1: link, p2: days, p3: origin};
  return audience === "agent"
    ? window.PortalI18n.t("你受邀以 User {p0} 加入 Portal（{p3}）。邀请只能使用一次，{p2} 天内有效：\n{p1}\n\n先读 agent 指引：{p3}/guide/agent.md\n在你运行的机器上领取邀请：\ncurl -fsSO {p3}/guide/portal_user.py\npython3 portal_user.py --url {p3} --credentials ~/.config/portal/credentials.json join\n（提示输入时粘贴上面的邀请链接）", values)
    : window.PortalI18n.t("你受邀以 {p0} 加入 Portal。打开下面的链接设置密码（只能使用一次，{p2} 天内有效）：\n{p1}\n\n第一次使用可以先看新用户指引：{p3}/guide", values);
}

export function mountEnrollment(root, {invitations = false} = {}) {
  if (!document.querySelector('[data-enrollment-style]')) {
    const link = document.createElement("link");
    link.rel = "stylesheet"; link.href = "/static/portal_user_enrollment.css";
    link.dataset.enrollmentStyle = ""; document.head.append(link);
  }
  root.classList.add("userEnrollment");
  root.innerHTML = window.PortalI18n.html`
    <details><summary>${invitations ? window.PortalI18n.t("User invitations") : window.PortalI18n.t("Personal access tokens")}</summary>
      <form>
        <label class="portalField"><span>${invitations ? window.PortalI18n.t("Username") : window.PortalI18n.t("Token name")}</span>
          <input name="name" required maxlength="80" autocomplete="off" /></label>
        ${invitations ? `<label class="portalField"><span>${window.PortalI18n.t("Display name")}</span><input name="display" maxlength="160" /></label>
        <label class="portalField enrollmentAudience"><span>${window.PortalI18n.t("邀请对象")}</span><select name="audience">
          <option value="human">${window.PortalI18n.t("人类")}</option><option value="agent">Agent</option></select></label>` : ""}
        <label class="portalField enrollmentExpiry"><span>${window.PortalI18n.t("Expires in days")}</span>
          <input name="days" type="number" required min="1" max="${invitations ? 30 : 365}" value="${invitations ? 7 : 90}" /></label>
        <button class="portalButton" type="submit">${invitations ? window.PortalI18n.t("Create invitation") : window.PortalI18n.t("Create token")}</button>
      </form>
      <div class="enrollmentSecret" hidden>
        <label class="portalField"><span>${invitations ? window.PortalI18n.t("Invitation link") : window.PortalI18n.t("New token (shown once)")}</span><textarea readonly rows="3" spellcheck="false"></textarea></label>
        ${invitations ? `<label class="portalField"><span>${window.PortalI18n.t("转交说明（含链接，可直接发送）")}</span><textarea readonly rows="7" spellcheck="false" data-handoff></textarea></label>` : ""}
        <button class="portalButton" type="button" data-copy>${window.PortalI18n.t("Copy")}</button>
        ${invitations ? `<button class="portalButton" type="button" data-copy-handoff>${window.PortalI18n.t("复制转交说明")}</button>` : ""}
        <button class="portalButton" type="button" data-dismiss>${window.PortalI18n.t("Done")}</button>
      </div>
      <p role="status"></p><ul></ul>
    </details>`;
  let busy = false, destroyed = false, loaded = false;
  const abort = new AbortController();
  const form = root.querySelector("form"), status = root.querySelector('[role="status"]');
  const box = root.querySelector(".enrollmentSecret"), output = box.querySelector("textarea");
  const handoff = box.querySelector("[data-handoff]");
  const execute = (name, args = {}) => window.PortalInstructionClient.execute(name, args, {signal: abort.signal});
  const kind = invitations ? "UserInvitation" : "UserAccessToken";
  const refresh = async () => {
    const result = invitations ? await execute("ListUserInvitations")
      : await execute("ListUserAccessTokens", {user_id: await window.PortalIdentity.userId()});
    if (destroyed) return;
    const rows = invitations ? result.invitations : result.credentials;
    root.querySelector("ul").replaceChildren(...rows.map(row => {
      const li = document.createElement("li"), text = document.createElement("span");
      text.textContent = `${invitations ? row.username : row.name} · ${row.status} · ${window.PortalUI.formatDateTime(new Date(row.expires_at * 1000))}`;
      li.append(text);
      if (row.status === (invitations ? "pending" : "active")) {
        const button = document.createElement("button");
        button.type = "button"; button.className = "portalButton is-compact"; button.textContent = "Revoke";
        button.addEventListener("click", () => run(async () => {
          await execute(`Revoke${kind}`, invitations ? {invitation_id: row.invitation_id} : {credential_id: row.credential_id});
          status.textContent = "Revoked."; await refresh();
        }));
        li.append(button);
      }
      return li;
    }));
    loaded = true;
  };
  const run = async work => {
    if (busy || destroyed) return;
    busy = true; root.querySelectorAll("button").forEach(button => button.disabled = true);
    try { await work(); } catch (error) { if (!destroyed) status.textContent = error.message; }
    finally { busy = false; if (!destroyed) root.querySelectorAll("button").forEach(button => button.disabled = false); }
  };
  root.querySelector("details").addEventListener("toggle", event => {
    if (event.target.open && !loaded) run(refresh);
  });
  form.addEventListener("submit", event => {
    event.preventDefault();
    run(async () => {
      const secret = newSecret(invitations ? "" : "dbu_");
      const fields = new FormData(form);
      const args = {secret_sha256: await secretDigest(secret), expires_in_days: Number(fields.get("days"))};
      if (invitations) { args.user_id = fields.get("name").trim(); args.display_name = fields.get("display").trim(); }
      else { args.user_id = await window.PortalIdentity.userId(); args.name = fields.get("name").trim(); }
      // Keep the locally generated secret visible even if the server response is lost.
      output.value = invitations ? invitationLink(secret, fields.get("audience")) : secret;
      if (handoff) handoff.value = handoffText(args.user_id, output.value, fields.get("audience"), args.expires_in_days);
      box.hidden = false;
      try {
        await execute(`Create${kind}`, args);
      } catch (error) {
        if (error.status >= 400 && error.status < 500) {
          output.value = "";
          if (handoff) handoff.value = "";
          box.hidden = true;
        }
        throw error;
      }
      status.textContent = invitations ? window.PortalI18n.t("Invitation created.") : window.PortalI18n.t("Token created.");
      form.reset(); await refresh();
    });
  });
  root.querySelector("[data-copy]").addEventListener("click", () => run(async () => {
    if (await window.PortalUI.copyText(output.value)) status.textContent = "Copied.";
  }));
  root.querySelector("[data-copy-handoff]")?.addEventListener("click", () => run(async () => {
    if (await window.PortalUI.copyText(handoff.value)) status.textContent = "Copied.";
  }));
  root.querySelector("[data-dismiss]").addEventListener("click", () => {
    output.value = ""; if (handoff) handoff.value = ""; box.hidden = true;
  });
  return {
    isBusy: () => busy,
    hasUnsavedChanges: () => !!output.value || !!form.elements.name.value,
    destroy() { destroyed = true; abort.abort(); output.value = ""; if (handoff) handoff.value = ""; root.replaceChildren(); },
  };
}
