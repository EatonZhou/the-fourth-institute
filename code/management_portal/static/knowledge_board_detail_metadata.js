// Knowledge board: a detail's front matter, its metadata and version views and the metadata editor.

// ── Modal ─────────────────────────────────────────────────────────────
const DETAIL_META_FIELDS = ["kb_id", "type", "project", "created_at", "importance", "ai_generated", "status"];
const DETAIL_LINK_FIELDS = ["source", "related", "supersedes", "derived_from"];
const DETAIL_STATUS_OPTIONS = ["", "open", "done", "dismiss", "obsolete"];

function stripYamlQuotes(value) {
  const text = String(value ?? "").trim();
  if (text.length >= 2 && ((text[0] === '"' && text[text.length - 1] === '"') || (text[0] === "'" && text[text.length - 1] === "'"))) {
    return text.slice(1, -1);
  }
  return text;
}

function parseYamlListValue(value) {
  const text = stripYamlQuotes(value);
  if (!text || text === "[]") return [];
  if (text.startsWith("[") && text.endsWith("]")) {
    const inner = text.slice(1, -1).trim();
    if (!inner) return [];
    return inner.split(",").map(item => stripYamlQuotes(item)).filter(Boolean);
  }
  return [text].filter(Boolean);
}

function parseDetailFrontmatter(raw) {
  const meta = {
    fields: {},
    links: Object.fromEntries(DETAIL_LINK_FIELDS.map(key => [key, []])),
    otherLines: [],
  };
  let activeLink = "";
  for (const line of String(raw || "").split(/\r?\n/)) {
    const top = line.match(/^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$/);
    if (top) {
      activeLink = "";
      const key = top[1];
      const value = top[2] || "";
      if (key === "links") continue;
      if (DETAIL_META_FIELDS.includes(key)) meta.fields[key] = stripYamlQuotes(value);
      else meta.otherLines.push(line);
      continue;
    }
    const linkHead = line.match(/^ {2}(source|related|supersedes|derived_from):\s*(.*)$/);
    if (linkHead) {
      activeLink = linkHead[1];
      meta.links[activeLink] = parseYamlListValue(linkHead[2] || "");
      continue;
    }
    const linkItem = line.match(/^ {2,}-\s*(.*)$/);
    if (linkItem && activeLink) {
      const value = stripYamlQuotes(linkItem[1] || "");
      if (value) meta.links[activeLink].push(value);
      continue;
    }
    meta.otherLines.push(line);
  }
  return meta;
}

function parseSkillDetailFields(raw) {
  const fields = {};
  let section = "";
  for (const line of String(raw || "").split(/\r?\n/)) {
    const top = line.match(/^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$/);
    if (top) {
      const key = top[1];
      const value = stripYamlQuotes(top[2] || "");
      section = value ? "" : key;
      if (["name", "description"].includes(key) && value) fields[key] = value;
      continue;
    }
    const nested = line.match(/^ {2}([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$/);
    if (section === "metadata" && nested?.[1] === "id") {
      const value = stripYamlQuotes(nested[2] || "");
      if (value) fields.id = value;
    }
  }
  return fields;
}

function splitDetailMarkdown(text) {
  const content = String(text || "");
  const match = content.match(/^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)([\s\S]*)$/);
  if (!match) {
    return {
      hasFrontmatter: false,
      rawFrontmatter: "",
      meta: parseDetailFrontmatter(""),
      body: content,
    };
  }
  return {
    hasFrontmatter: true,
    rawFrontmatter: match[1] || "",
    meta: parseDetailFrontmatter(match[1] || ""),
    body: match[2] || "",
  };
}

function yamlScalar(value, { numeric = false, boolean = false } = {}) {
  const text = String(value ?? "").trim();
  if (numeric) return String(Number.parseInt(text || "0", 10) || 0);
  if (boolean) return text === "true" ? "true" : "false";
  if (!text) return '""';
  if (/^[A-Za-z0-9_./@#:-]+$/.test(text)) return text;
  return JSON.stringify(text);
}

function yamlListLines(key, values) {
  const items = Array.isArray(values) ? values.map(v => String(v || "").trim()).filter(Boolean) : [];
  if (!items.length) return [`  ${key}: []`];
  return [`  ${key}:`, ...items.map(item => `    - ${yamlScalar(item)}`)];
}

function composeDetailMarkdown(meta, body) {
  const fields = meta?.fields || {};
  const links = meta?.links || {};
  const otherLines = Array.isArray(meta?.otherLines) ? meta.otherLines.filter(line => String(line || "").trim()) : [];
  const lines = ["---"];
  for (const key of ["kb_id", "type", "project", "created_at"]) {
    if (fields[key] !== undefined && fields[key] !== "") lines.push(`${key}: ${yamlScalar(fields[key])}`);
  }
  const importance = Math.min(10, Math.max(1, Number.parseInt(fields.importance || "5", 10) || 5));
  lines.push(`importance: ${importance}`);
  lines.push(`ai_generated: ${yamlScalar(fields.ai_generated === "true" ? "true" : "false", { boolean: true })}`);
  if (fields.status) lines.push(`status: ${yamlScalar(fields.status)}`);
  lines.push("links:");
  for (const key of DETAIL_LINK_FIELDS) lines.push(...yamlListLines(key, links[key] || []));
  if (otherLines.length) {
    lines.push("");
    lines.push(...otherLines);
  }
  lines.push("---");
  return `${lines.join("\n")}\n\n${String(body || "").replace(/^\n+/, "")}`;
}

function detailSelectOptions(values, current) {
  const seen = new Set();
  const items = [];
  for (const value of [current, ...(values || [])]) {
    const clean = String(value || "").trim();
    if (!clean || seen.has(clean)) continue;
    seen.add(clean);
    items.push(clean);
  }
  return items.map(value => window.PortalI18n.html`<option value="${escapeHtml(value)}"${value === current ? " selected" : ""}>${escapeHtml(value)}</option>`).join("");
}

function detailMetadataHtml(parsed, schema = "knowledge") {
  if (!parsed?.hasFrontmatter) return "";
  const skillFields = schema === "skill" ? parseSkillDetailFields(parsed.rawFrontmatter) : {};
  const fields = parsed.meta?.fields || {};
  const links = parsed.meta?.links || {};
  const fieldEntries = schema === "skill"
    ? [
        ["name", skillFields.name],
        ["description", skillFields.description],
        ["metadata.id", skillFields.id],
      ]
    : [
        ["kb_id", fields.kb_id],
        ["type", fields.type],
        ["project", fields.project],
        ["importance", fields.importance],
        ["ai generated", fields.ai_generated],
        ["status", fields.status],
      ];
  const rows = fieldEntries.filter(([, value]) => String(value || "").trim()).map(([key, value]) => window.PortalI18n.html`
          <div class="detailMetadataItem">
            <div class="detailMetadataKey">${escapeHtml(key)}</div>
            <div class="detailMetadataValue"${key === "kb_id" ? window.PortalI18n.text` data-copy-kb-id="${escapeHtml(value)}" title="双击复制条目 ID"` : ""}>${escapeHtml(value)}</div>
          </div>
        `).join("");
  const linkRows = DETAIL_LINK_FIELDS
    .map(key => [key, (links[key] || []).filter(Boolean)])
    .filter(([, values]) => values.length)
    .map(([key, values]) => window.PortalI18n.html`<div class="detailMetadataLinkRow"><strong>${escapeHtml(key)}</strong>: ${escapeHtml(values.join(", "))}</div>`)
    .join("");
  const versionHtml = schema === "knowledge"
    ? detailVersionHtml(parsed, state.selected || {})
    : "";
  if (!rows && !linkRows && !versionHtml) return "";
  const fieldCount = fieldEntries.filter(([, value]) => String(value || "").trim()).length;
  const relationCount = DETAIL_LINK_FIELDS.reduce((count, key) => count + (links[key] || []).filter(Boolean).length, 0);
  const summary = [fieldCount ? window.PortalI18n.text`${fieldCount} 项` : "", relationCount ? window.PortalI18n.text`${relationCount} 个关系` : "", versionHtml ? window.PortalI18n.t("含版本记录") : ""]
    .filter(Boolean)
    .join(" · ");
  return window.PortalI18n.html`
          <details class="detailMetadataCard">
            <summary class="detailMetadataSummary"><span>资源元数据</span><span class="detailMetadataSummaryMeta">${escapeHtml(summary)}</span></summary>
            <div class="detailMetadataBody">
              ${rows ? window.PortalI18n.html`<div class="detailMetadataGrid">${rows}</div>` : ""}
              ${linkRows ? window.PortalI18n.html`<div class="detailMetadataLinks">${linkRows}</div>` : ""}
              ${versionHtml}
            </div>
          </details>
        `;
}

function detailVersionTime(value) {
  const raw = String(value || "").trim();
  if (!raw) return "未记录";
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) return raw;
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return raw;
  return new Intl.DateTimeFormat(window.PortalI18n.locale(), {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
}

function detailVersionPrincipal(value) {
  const principal = value && typeof value === "object" ? value : {};
  return {
    user: String(principal.user || "").trim(),
    agent: String(principal.agent || "").trim(),
  };
}

function detailVersionEventHtml(label, timestamp, principal) {
  const identity = detailVersionPrincipal(principal);
  const identities = [
    identity.user ? window.PortalI18n.html`<span>User · ${escapeHtml(identity.user)}</span>` : "",
    identity.agent ? window.PortalI18n.html`<span>Agent · ${escapeHtml(identity.agent)}</span>` : "",
  ].filter(Boolean).join("");
  return window.PortalI18n.html`
          <div class="detailVersionEvent">
            <div class="detailVersionEventHead">
              <strong>${escapeHtml(label)}</strong>
              <time title="${escapeHtml(timestamp || "")}">${escapeHtml(detailVersionTime(timestamp))}</time>
            </div>
            ${identities
        ? window.PortalI18n.html`<div class="detailVersionIdentity">${identities}</div>`
        : window.PortalI18n.html`<span class="detailVersionUnknown">操作者未记录</span>`}
          </div>
        `;
}

function detailVersionHtml(parsed, item) {
  const raw = item?.version && typeof item.version === "object"
    ? item.version
    : {};
  const createdAt = String(raw.created_at || parsed?.meta?.fields?.created_at || "").trim();
  const updatedAt = String(
    raw.updated_at
    || (item?.mtime ? new Date(Number(item.mtime) * 1000).toISOString() : ""),
  ).trim();
  if (!createdAt && !updatedAt) return "";
  const number = Math.max(1, Number.parseInt(raw.number || "1", 10) || 1);
  return window.PortalI18n.html`
          <section class="detailVersion" aria-label="版本信息">
            <div class="detailVersionHeader">
              <span class="detailVersionTitle">版本信息</span>
              <span class="detailVersionBadge">v${number}</span>
              ${raw.recorded === false ? window.PortalI18n.html`<span class="detailVersionBadge">兼容旧条目</span>` : ""}
            </div>
            <div class="detailVersionEvents">
              ${detailVersionEventHtml(window.PortalI18n.t("创建"), createdAt, raw.created_by)}
              ${detailVersionEventHtml(window.PortalI18n.t("最近修改"), updatedAt, raw.updated_by)}
            </div>
          </section>
        `;
}

async function copyTextToClipboard(text) {
  const value = String(text || "");
  if (!value) return;
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return;
  }
  const input = document.createElement("textarea");
  input.value = value;
  input.setAttribute("readonly", "");
  input.style.position = "fixed";
  input.style.left = "-9999px";
  document.body.appendChild(input);
  input.select();
  document.execCommand("copy");
  input.remove();
}

async function copyKnowledgeIdFromDetail(target) {
  const id = target?.dataset?.copyKbId || "";
  if (!id) return;
  await copyTextToClipboard(id);
  target.classList.add("copied");
  setTimeout(() => target.classList.remove("copied"), 900);
}

function renderDetailMetaEditor(meta) {
  const fields = meta?.fields || {};
  const links = meta?.links || {};
  const projects = (state.raw?.projects || []).map(project => project.id).filter(Boolean);
  const types = state.raw?.allowed_types || [];
  const typeOptions = detailSelectOptions(types, fields.type || "");
  const projectOptions = detailSelectOptions(projects, fields.project || "");
  const statusOptions = DETAIL_STATUS_OPTIONS.map(value => window.PortalI18n.html`<option value="${escapeHtml(value)}"${value === (fields.status || "") ? " selected" : ""}>${escapeHtml(value || window.PortalI18n.t("无"))}</option>`).join("");
  const linkEditors = DETAIL_LINK_FIELDS.map(key => window.PortalI18n.html`
          <div>
            <label for="detail-link-${escapeHtml(key)}">${escapeHtml(key)}</label>
            <textarea id="detail-link-${escapeHtml(key)}" data-detail-link="${escapeHtml(key)}" spellcheck="false">${escapeHtml((links[key] || []).join("\n"))}</textarea>
          </div>
        `).join("");
  return window.PortalI18n.html`
          <section class="detailMetaEditor">
            <div class="detailMetaForm">
              <div class="detailMetaField">
                <label for="detail-meta-kb-id">kb_id</label>
                <input id="detail-meta-kb-id" data-detail-meta="kb_id" value="${escapeHtml(fields.kb_id || "")}" spellcheck="false" readonly />
              </div>
              <div class="detailMetaField">
                <label for="detail-meta-type">type</label>
                <select id="detail-meta-type" data-detail-meta="type">${typeOptions}</select>
              </div>
              <div class="detailMetaField">
                <label for="detail-meta-project">project</label>
                <select id="detail-meta-project" data-detail-meta="project">${projectOptions}</select>
              </div>
              <div class="detailMetaField">
                <label for="detail-meta-created">created_at</label>
                <input id="detail-meta-created" data-detail-meta="created_at" type="date" value="${escapeHtml(fields.created_at || "")}" />
              </div>
              <div class="detailMetaField">
                <label for="detail-meta-importance">importance</label>
                <input id="detail-meta-importance" data-detail-meta="importance" type="number" min="1" max="10" step="1" value="${escapeHtml(fields.importance || "5")}" />
              </div>
              <div class="detailMetaField">
                <label for="detail-meta-ai">ai_generated</label>
                <select id="detail-meta-ai" data-detail-meta="ai_generated">
                  <option value="false"${fields.ai_generated === "true" ? "" : " selected"}>false</option>
                  <option value="true"${fields.ai_generated === "true" ? " selected" : ""}>true</option>
                </select>
              </div>
              <div class="detailMetaField">
                <label for="detail-meta-status">status</label>
                <select id="detail-meta-status" data-detail-meta="status">${statusOptions}</select>
              </div>
            </div>
            <div class="detailMetaLinks">${linkEditors}</div>
            <details class="detailMetaOther">
              <summary><span>未识别字段</span><span data-portal-icon="chevron-down" data-portal-icon-class="detailDisclosureIcon" class="detailDisclosureIcon" aria-hidden="true"></span></summary>
              <label for="detail-meta-other">原样保留的行</label>
              <textarea id="detail-meta-other" data-detail-meta-other spellcheck="false">${escapeHtml((meta?.otherLines || []).join("\n"))}</textarea>
            </details>
          </section>
        `;
}

function updateDetailDraftFromDom() {
  if (!state.detailEditing) return;
  if (usesPackageStyleEditor()
    || state.detailSelectedFileId !== state.detailFileId
    || state.detailSelectedFileKind !== "markdown") {
    detailDocumentSession.updateDraft(el.mText.querySelector("[data-detail-body]")?.value || "");
    syncDetailButtons();
    return;
  }
  if (!state.detailMetaDraft) return;
  const meta = {
    fields: { ...(state.detailMetaDraft.fields || {}) },
    links: Object.fromEntries(DETAIL_LINK_FIELDS.map(key => [key, []])),
    otherLines: [],
  };
  el.mText.querySelectorAll("[data-detail-meta]").forEach(input => {
    const key = input.dataset.detailMeta || "";
    if (key) meta.fields[key] = String(input.value || "").trim();
  });
  el.mText.querySelectorAll("[data-detail-link]").forEach(input => {
    const key = input.dataset.detailLink || "";
    if (!key) return;
    meta.links[key] = String(input.value || "")
      .split(/\r?\n|,/)
      .map(item => item.trim())
      .filter(Boolean);
  });
  const other = el.mText.querySelector("[data-detail-meta-other]");
  meta.otherLines = other ? String(other.value || "").split(/\r?\n/) : [];
  const body = el.mText.querySelector("[data-detail-body]")?.value || "";
  state.detailMetaDraft = meta;
  state.detailBodyDraft = body;
  detailDocumentSession.updateDraft(composeDetailMarkdown(meta, body));
  syncDetailButtons();
}
