(function () {
  "use strict";

  let pending = null;

  function open() {
    if (pending) return pending;
    pending = new Promise(resolve => {
      const overlay = document.createElement("div");
      overlay.className = "portalDetailOverlay";
      overlay.hidden = true;
      overlay.dataset.portalDetailOverlay = "1";
      overlay.dataset.portalOverlayLayer = "dialog";
      overlay.innerHTML = window.PortalI18n.html`
        <form class="portalDetailSurface portalPromptSurface" data-portal-detail-surface role="dialog" aria-modal="true" aria-labelledby="projectCreationTitle">
          <header class="portalDetailHeader">
            <h2 id="projectCreationTitle">创建 Project</h2>
            <button class="portalDetailClose" type="button" data-project-cancel aria-label="关闭" data-portal-icon="close"></button>
          </header>
          <div class="portalDetailBody portalPromptBody">
            <label class="portalField"><span>Project 名称</span><input name="id" required autocomplete="off" /></label>
            <label class="portalField"><span id="projectCreationTypeLabel">类型</span><select name="project_type" aria-labelledby="projectCreationTypeLabel">
              <option value="company">公司项目</option><option value="academic">学术项目</option>
              <option value="personal">个人项目</option><option value="other">其他项目</option>
            </select></label>
            <label class="portalField"><span>说明（可选）</span><textarea name="description" rows="3"></textarea></label>
            <label class="portalField"><span id="projectCreationStatusLabel">状态</span><select name="status" aria-labelledby="projectCreationStatusLabel">
              <option value="active">进行中</option><option value="not_started">未开始</option>
            </select></label>
            <label class="portalField"><span>标签（可选）</span><input name="tags" placeholder="robotics, paper" autocomplete="off" /></label>
            <label class="portalField"><span>截止时间（可选）</span><input name="ddl" placeholder="2026-12-31 18:00" autocomplete="off" /></label>
            <p>创建后，你将拥有这个 Project 的管理权限。</p>
            <p role="alert" data-project-error hidden></p>
          </div>
          <footer class="portalDetailFooter">
            <button class="portalButton" type="button" data-project-cancel>取消</button>
            <button class="portalButton is-primary" type="submit">创建</button>
          </footer>
        </form>`;
      document.body.appendChild(overlay);
      const form = overlay.querySelector("form");
      const error = overlay.querySelector("[data-project-error]");
      let busy = false;
      const dialog = window.PortalUI.createDialogController({
        modal: overlay, backdropClose: true, escapeClose: true,
        beforeClose: () => !busy,
        onClose: () => {
          dialog.destroy();
          overlay.remove();
          pending = null;
          resolve(null);
        },
      });
      overlay.querySelectorAll("[data-project-cancel]").forEach(button => {
        button.addEventListener("click", () => dialog.close());
      });
      form.addEventListener("submit", event => {
        event.preventDefault();
        if (busy) return;
        const payload = Object.fromEntries(new FormData(form));
        payload.id = payload.id.trim();
        if (!payload.id) { form.elements.id.focus(); return; }
        busy = true;
        error.hidden = true;
        const tags = [...new Set(String(payload.tags || "").split(/[,，\s]+/).map(tag => tag.trim()).filter(Boolean))];
        const ddl = String(payload.ddl || "").trim();
        void window.PortalUI.runMutation({
          key: "project:create", button: form.querySelector('[type="submit"]'), busyLabel: "创建中…",
          notifyError: false, retry: false,
          run: () => window.PortalInstructionClient.execute("CreateProject", {
            project_id: payload.id,
            project_type: payload.project_type,
            status: payload.status,
            language: window.PortalI18n.locale(),
            ...(String(payload.description || "").trim() ? { description: String(payload.description).trim() } : {}),
            ...(tags.length ? { tags } : {}),
            ...(ddl ? { ddl } : {}),
          }, { idempotencyKey: `project-create:${payload.id}:${globalThis.PortalUIPrimitives.randomUUID()}` }),
          onSuccess: result => {
            resolve(result);
            busy = false;
            dialog.close();
          },
          onError: failure => {
            error.textContent = failure.message || window.PortalI18n.t("创建失败，请重试。");
            error.hidden = false;
          },
          onFinally: () => { busy = false; },
        });
      });
      dialog.open({ focusTarget: form.elements.id });
    });
    return pending;
  }

  window.PortalProjectCreation = Object.freeze({ open });
})();
