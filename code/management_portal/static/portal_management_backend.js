// AI Backend registration and login, shared by Management and Home.
const t = (key, values) => window.PortalI18n.t(key, values);
const call = (name, args = {}, options = {}) => window.PortalInstructionClient.execute(name, args, options);
async function accountDirectoryRequest(action, args = {}, options = {}) {
  const query = new URLSearchParams({action, ...args});
  const result = await window.PortalAPI.requestResult('/api/management/account-directories?' + query, {method: 'GET', credentials: 'same-origin', ...options});
  if (!result.response.ok) throw Object.assign(new Error(result.payload?.error || t("操作失败，请重试。")), {code: result.payload?.code});
  return result.payload;
}
const kinds = {subscription_login: "订阅登录", api_profile: "模型 API 账号", service: "服务账号"};
const loginStates = {logged_in: "已登录", login_expired: "登录已过期", logged_out: "未登录", not_applicable: "无需登录",
  unavailable: "等待准备环境或检查授权",
  pending: "正在启动登录…", awaiting_user: "等待浏览器授权", succeeded: "登录成功", failed: "登录失败", expired: "登录已过期"};
const probeStates = {supported: "支持", unsupported: "不支持", auth_failed: "认证失败", rejected: "接口存在但拒绝了请求",
  error: "接口返回错误", unreachable: "无法连接"};
const logins = new Map();

function node(tag, text, attrs = {}) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  for (const [name, value] of Object.entries(attrs)) {
    if (typeof value === "boolean") el[name] = value;
    else el.setAttribute(name, value);
  }
  return el;
}
function button(label, action, disabled = false) {
  const el = node("button", t(label), {type: "button", class: "portalButton is-compact", disabled});
  el.addEventListener("click", action); return el;
}
function field(host, label, name, value = "", attrs = {}) {
  const wrapper = node("label", undefined, {class: "portalField"});
  const input = node(attrs.tag || "input", undefined, {name, ...attrs});
  input.removeAttribute("tag"); input.value = value;
  const caption = node("span", t(label), {id: `backend-label-${name}`});
  input.setAttribute("aria-labelledby", caption.id);
  wrapper.append(caption, input); host.append(wrapper); return input;
}
function select(host, label, name, entries, value = "") {
  const input = field(host, label, name, "", {tag: "select"});
  for (const [key, title] of entries) input.append(node("option", title, {value: key}));
  input.value = value || entries[0]?.[0] || ""; return input;
}
function hint(input, text) {
  const help = node("small", t(text), {class: "backendHint", id: `backend-help-${input.name}`});
  input.setAttribute("aria-describedby", help.id); input.parentElement.append(help);
}
function advanced(host) {
  const details = node("details", undefined, {class: "backendAdvanced"});
  details.append(node("summary", t("高级设置（可选）")));
  const body = node("div", undefined, {class: "backendForm"}); details.append(body); host.append(details); return body;
}
function modelCatalog(host, api) {
  const el = node("fieldset", undefined, {class: "backendModelCatalog", "data-backend-model-catalog": ""});
  const list = node("div", undefined, {class: "backendModelList"});
  el.append(node("legend", t("模型列表")), node("p", t("添加服务商提供的模型标识，并选择一个默认模型。名称需与服务商文档完全一致。"), {class: "backendHint"}), list);
  host.append(el);
  let sequence = 0, selected;
  const rows = [];
  const syncDefault = () => {
    if (!rows.includes(selected) || !selected.enabled.checked) selected = rows.find(row => row.enabled.checked);
    for (const row of rows) {
      const name = row.id.value.trim() || t("未命名模型");
      row.defaultChoice.checked = row === selected;
      row.defaultChoice.disabled = !row.enabled.checked;
      row.defaultChoice.setAttribute("aria-label", t("将模型 {p0} 设为默认", {p0: name}));
      row.remove.setAttribute("aria-label", t("移除模型 {p0}", {p0: name}));
      row.remove.disabled = rows.length === 1;
      row.card.dataset.enabled = String(row.enabled.checked);
    }
  };
  const addRow = (entry = {}, focus = false) => {
    const key = ++sequence;
    const card = node("div", undefined, {class: "backendModelRow", "data-backend-model-row": ""});
    const heading = node("div", undefined, {class: "backendModelHeading"});
    const defaultChoice = node("input", undefined, {type: "radio", name: "model_default", value: String(key)});
    const defaultLabel = node("label", undefined, {class: "backendModelDefault"});
    defaultLabel.append(defaultChoice, node("span", t("默认模型")));
    const remove = button("移除模型", () => {
      const index = rows.indexOf(row);
      rows.splice(index, 1); card.remove(); syncDefault();
      (rows[Math.min(index, rows.length - 1)]?.id || add).focus();
    });
    heading.append(defaultLabel, remove); card.append(heading);
    const id = field(card, "模型标识", `model_id_${key}`, entry.id || "", {required: true, placeholder: "example-model", autocomplete: "off", spellcheck: "false", "data-backend-model-id": ""});
    const settings = node("details", undefined, {class: "backendModelSettings"});
    settings.append(node("summary", t("模型设置（可选）")));
    const options = node("div", undefined, {class: "backendForm"}); settings.append(options); card.append(settings);
    const enabled = field(options, "启用模型", `model_enabled_${key}`, "", {type: "checkbox"});
    enabled.checked = entry.enabled !== false;
    const efforts = node("fieldset", undefined, {class: "backendModelReasoning"});
    efforts.append(node("legend", t("支持的推理强度")), node("p", t("只勾选服务商支持的值；不支持时留空。取消勾选可移除，自定义值也可点 × 删除。"), {class: "backendHint"}));
    const choices = node("div", undefined, {class: "backendModelEfforts"});
    const controls = new Map();
    const presets = ["low", "medium", "high"];
    const addEffort = (value, checked) => {
      if (!controls.has(value)) {
        const chip = node("span", undefined, {class: "backendModelEffort"});
        const label = node("label");
        const input = node("input", undefined, {type: "checkbox", value}); input.checked = checked;
        label.append(input, node("span", value)); chip.append(label);
        if (!presets.includes(value)) {
          const title = t("移除推理强度 {p0}", {p0: value});
          const removeEffort = node("button", "×", {type: "button", class: "portalButton is-compact backendModelEffortRemove", "aria-label": title, title});
          removeEffort.addEventListener("click", () => { controls.delete(value); chip.remove(); effort.focus(); });
          chip.append(removeEffort);
        }
        choices.append(chip); controls.set(value, input);
      } else if (checked) controls.get(value).checked = true;
    };
    for (const value of new Set([...presets, ...(entry.reasoning_efforts || [])])) {
      addEffort(value, (entry.reasoning_efforts || []).includes(value));
    }
    const custom = node("div", undefined, {class: "backendModelCustomEffort"});
    const effort = field(custom, "其他推理强度", `model_effort_${key}`, "", {placeholder: "xhigh", autocomplete: "off"});
    const commitEffort = () => {
      const value = effort.value.trim();
      if (value) { addEffort(value, true); effort.value = ""; }
    };
    const addCustom = button("添加推理强度", () => { commitEffort(); effort.focus(); });
    effort.addEventListener("keydown", event => { if (event.key === "Enter") { event.preventDefault(); commitEffort(); } });
    custom.append(addCustom); efforts.append(choices, custom); options.append(efforts);
    const row = {entry, card, id, enabled, defaultChoice, remove, controls, commitEffort};
    rows.push(row); list.append(card);
    if (entry.id === api?.default_model && enabled.checked) selected = row;
    defaultChoice.addEventListener("change", () => { if (defaultChoice.checked) selected = row; syncDefault(); });
    id.addEventListener("input", syncDefault);
    enabled.addEventListener("change", syncDefault);
    syncDefault();
    if (focus) id.focus();
  };
  const add = button("添加模型", () => addRow({}, true)); el.append(add);
  const entries = [...(api?.models || [])];
  if (api?.default_model && !entries.some(entry => entry.id === api.default_model)) entries.push({id: api.default_model});
  for (const entry of entries.length ? entries : [{}]) addRow(entry);
  const read = (validate = true) => {
    const ids = new Set();
    const models = rows.map(row => {
      const id = row.id.value.trim();
      if (validate && !id) { row.id.focus(); throw new Error(t("请填写每个模型的标识。")); }
      if (validate && ids.has(id)) { row.id.focus(); throw new Error(t("模型标识不能重复：{p0}", {p0: id})); }
      ids.add(id); row.commitEffort();
      // Preserve metadata and the order of existing efforts when editing a catalog.
      const model = {...row.entry, id};
      const efforts = (row.entry.reasoning_efforts || []).filter(value => row.controls.get(value)?.checked);
      for (const [value, input] of row.controls) if (input.checked && !efforts.includes(value)) efforts.push(value);
      if (efforts.length || "reasoning_efforts" in row.entry) model.reasoning_efforts = efforts;
      if ("enabled" in row.entry || !row.enabled.checked) model.enabled = row.enabled.checked;
      return model;
    });
    if (validate && !selected) throw new Error(t("请至少启用一个模型，并选择默认模型。"));
    return {models, default_model: selected?.id.value.trim() || ""};
  };
  return {el, read};
}
function loadStyle() {
  if (!document.querySelector("[data-backend-style]")) document.head.append(node("link", undefined,
    {rel: "stylesheet", href: "/static/portal_management_backend.css", "data-backend-style": ""}));
}
function errorText(error) {
  if (error.details?.reason === "model_api.routes_incomplete") return t("接口已保存，运行器配置未完成。请重试保存；不会重复创建接口或账号。");
  if (error.details?.reason === "account.busy") return t("账号仍关联 {p0} 个会话、{p1} 个排队或运行中的执行。请先迁移会话并等待或取消执行；也可先停用账号。", {
    p0: error.details?.session_count ?? "—", p1: error.details?.execution_count ?? "—"});
  if (error.details?.reason === "account.role_in_use") return error.details.roles?.length
    ? t("无法删除：Backend 角色 {p0} 仍引用该账号。请先将这些角色改用其他已启用账号。", {p0: error.details.roles.join("、")})
    : t("请先修改引用该账号的 Backend 角色");
  if (error.details?.reason === "account.api_in_use") return t("无法删除：执行账号 {p0} 仍使用此模型 API。请先处理这些账号的引用，再删除接口。", {
    p0: (error.details.account_ids || []).join("、") || "—"});
  return error.message ? t(error.message) : t("操作失败，请重试。");
}
function secretSubmissionError(error) {
  // Never display an arbitrary exception from a request containing credentials.
  const code = String(error.code || "");
  if (error.details?.reason === "model_api.routes_incomplete") return errorText(error);
  if (error.details?.reason === "model_api.secret_write_failed") return t("Portal 服务器无法保存密钥。请管理员检查密钥存储目录的写入权限和磁盘空间，再重新提交；这不表示 API Key 无效。");
  if (code === "instruction.conflict") return t("连接名称已存在。请在高级设置中换一个名称，或编辑已有连接；这不表示 API Key 无效。");
  if (/permission|forbidden|unauthorized|access_denied/.test(code) || [401, 403].includes(error.status)) return t("当前登录身份没有添加或修改 AI 连接的权限。请重新登录或联系管理员。");
  if (code === "instruction.invalid_arguments") return t("连接信息不符合要求。请检查接口地址、模型列表和高级设置后重试。");
  if (["TypeError", "AbortError"].includes(error.name) || /network|timeout|unavailable/.test(code)) return t("未能确认保存结果。请检查网络与 Portal 服务，刷新连接列表确认是否已保存，再重试。");
  return t("AI 连接保存失败。请检查 Portal 服务日志中的失败原因，再重新提交 API Key；密钥尚未经过模型服务验证。");
}
function dialog(title, onClose = () => {}) {
  loadStyle();
  const el = node("dialog", undefined, {class: "accessDialog backendDialog", "aria-label": title});
  const heading = node("header"); heading.append(node("h2", title));
  el.append(heading); document.body.append(el);
  const controller = window.PortalUI.createDialogController({modal: el, backdropClose: false, escapeClose: true,
    onClose: () => { onClose(); controller.destroy(); el.remove(); }});
  heading.append(button("关闭", () => controller.close()));
  return {el, open: () => controller.open(), close: () => controller.close()};
}

function browseHostDirectory(initialPath, onSelect) {
  // Browsing and selection are separate. Only server-confirmed paths can be
  // committed; the dialog owns request cancellation and navigation history.
  let closed = false, generation = 0, request, current = "", parent = "", attempted = initialPath;
  let entries = [], selected = "", busy = false, invalid = false, editing = false, truncated = false;
  const history = []; let historyIndex = -1;
  const view = dialog(t("选择文件夹"), () => { closed = true; generation++; request?.abort(); });
  view.el.classList.add("backendDirectoryPicker");
  view.el.querySelector("h2").append(node("small", t("Portal 主机")));
  const body = node("div", undefined, {class: "backendDirectoryBrowser"});
  const nav = node("div", undefined, {class: "backendDirectoryNav"});
  function iconButton(label, glyph, action) {
    const el = button(label, action); el.textContent = glyph; el.setAttribute("aria-label", t(label)); el.title = t(label); return el;
  }
  const back = iconButton("后退", "←", () => void load(history[historyIndex - (current ? 1 : 0)], {index: historyIndex - (current ? 1 : 0)}));
  const forward = iconButton("前进", "→", () => void load(history[historyIndex + 1], {index: historyIndex + 1}));
  const up = iconButton("上一级", "↑", () => void load(parent));
  const location = node("div", undefined, {class: "backendDirectoryLocation"});
  const crumbs = node("nav", undefined, {"aria-label": t("目录路径"), class: "backendDirectoryCrumbs"});
  const address = node("input", undefined, {name: "browse_path", "aria-label": t("目录路径"), autocomplete: "off", spellcheck: false, hidden: true});
  const editPath = iconButton("编辑路径", "⌨", () => editLocation());
  const go = iconButton("打开目录", "↵", () => void load(address.value.trim(), {focusList: true})); go.hidden = true;
  location.append(crumbs, address); nav.append(back, forward, up, location, editPath, go);
  const content = node("div", undefined, {class: "backendDirectoryContent"});
  const places = node("nav", undefined, {class: "backendDirectoryPlaces", "aria-label": t("位置")});
  const home = button("主目录", () => void load());
  const roots = node("div"); places.append(home, roots);
  const main = node("div", undefined, {class: "backendDirectoryMain"});
  const filter = node("input", undefined, {type: "search", "aria-label": t("筛选文件夹"), placeholder: t("筛选文件夹")});
  const listActions = node("div", undefined, {class: "backendDirectoryListActions"});
  const enter = button("打开目录", () => { if (selected && !enter.disabled) void load(selected, {focusList: true}); }, true);
  listActions.append(filter, enter);
  const list = node("div", undefined, {class: "backendDirectoryList", role: "listbox", tabindex: "0", "aria-label": t("文件夹")});
  const feedback = node("div", undefined, {class: "backendDirectoryFeedback"});
  const status = node("p", "", {role: "status", "aria-live": "polite"});
  const retry = button("重试", () => void load(attempted)); retry.hidden = true;
  feedback.append(status, retry); main.append(listActions, list, feedback); content.append(places, main);
  const footer = node("footer", undefined, {class: "backendDirectoryFooter"});
  const pending = node("output", "", {"aria-label": t("待选路径"), class: "backendDirectoryPending"});
  const useCurrent = button("选择当前目录", () => { selected = ""; invalid = false; renderList(); sync(); });
  const actions = node("div", undefined, {class: "backendDirectoryCommit"});
  const cancel = button("取消", () => view.close());
  const choose = button("选择文件夹", () => void commit(), true);
  actions.append(cancel, choose); footer.append(pending, useCurrent, actions);
  body.append(nav, content, footer); view.el.append(body);
  const visible = () => entries.filter(entry => entry.name.toLocaleLowerCase().includes(filter.value.trim().toLocaleLowerCase()));
  function sync() {
    pending.textContent = busy ? t("正在读取目录…") : selected || current || t("尚未选择目录");
    pending.title = selected || current || "";
    choose.disabled = busy || invalid || editing || !(selected || current);
    useCurrent.disabled = busy || !current || editing; useCurrent.hidden = !selected;
    up.disabled = !current || !parent || parent === current;
    back.disabled = current ? historyIndex < 1 : historyIndex < 0;
    forward.disabled = historyIndex + 1 >= history.length;
    filter.disabled = busy || !current;
    enter.disabled = busy || editing || !selected;
    list.setAttribute("aria-busy", String(busy));
  }
  function editLocation() {
    if (busy) { generation++; request?.abort(); busy = false; invalid = true; status.textContent = ""; retry.hidden = true; }
    editing = true; address.hidden = false; crumbs.hidden = true; editPath.hidden = true; go.hidden = false;
    address.value = current || attempted || ""; address.focus(); address.select(); sync();
  }
  function endEdit() { editing = false; address.hidden = true; crumbs.hidden = false; editPath.hidden = false; go.hidden = true; sync(); }
  function renderCrumbs() {
    crumbs.replaceChildren();
    // Canonical paths come from the host. Preserve POSIX, drive and UNC roots.
    const separator = current.includes("\\") ? "\\" : "/";
    const root = current.match(/^(?:[A-Za-z]:[\\/]|\\\\[^\\]+\\[^\\]+[\\/]?|\/)/)?.[0] || "";
    let path = root;
    if (root) crumbs.append(button(root, () => void load(root)));
    for (const part of current.slice(root.length).split(/[\\/]/).filter(Boolean)) {
      path += (path.endsWith(separator) ? "" : separator) + part;
      const target = path;
      crumbs.append(node("span", "›", {"aria-hidden": "true"}), button(part, () => void load(target)));
    }
    crumbs.lastElementChild?.setAttribute("aria-current", "location");
    crumbs.scrollLeft = crumbs.scrollWidth;
  }
  function selectEntry(path) {
    if (busy || editing) return;
    selected = path; invalid = false;
    const items = visible();
    [...list.children].forEach((row, index) => row.setAttribute("aria-selected", String(items[index].path === path)));
    const index = items.findIndex(entry => entry.path === path);
    if (index >= 0) list.setAttribute("aria-activedescendant", `backend-directory-option-${index}`);
    sync();
  }
  function renderList() {
    const items = visible();
    list.replaceChildren(...items.map((entry, index) => {
      const row = node("div", undefined, {role: "option", id: `backend-directory-option-${index}`, "aria-selected": String(entry.path === selected)});
      row.append(node("span", "", {class: "backendDirectoryIcon", "aria-hidden": "true"}), node("span", entry.name)); row.title = entry.name;
      row.addEventListener("click", () => { selectEntry(entry.path); list.focus(); });
      row.addEventListener("dblclick", () => { if (!busy && !editing) void load(entry.path, {focusList: true}); });
      return row;
    }));
    const index = items.findIndex(entry => entry.path === selected);
    if (index >= 0) list.setAttribute("aria-activedescendant", `backend-directory-option-${index}`);
    else list.removeAttribute("aria-activedescendant");
    if (!busy && !invalid) status.textContent = truncated ? t("目录较多，仅显示部分；可输入完整路径。")
      : !entries.length ? t("此目录没有子目录。") : !items.length ? t("没有匹配的文件夹。") : "";
  }
  filter.addEventListener("input", () => { selected = ""; invalid = false; renderList(); sync(); });
  address.addEventListener("input", () => { invalid = true; sync(); });
  list.addEventListener("keydown", event => {
    if (busy || editing) return;
    const items = visible(), index = items.findIndex(entry => entry.path === selected);
    if (event.key === "Enter" && selected) { event.preventDefault(); void load(selected, {focusList: true}); return; }
    const next = {ArrowDown: Math.min(items.length - 1, index + 1), ArrowUp: Math.max(0, index - 1), Home: 0, End: items.length - 1}[event.key];
    if (next === undefined || !items[next]) return;
    event.preventDefault(); selectEntry(items[next].path); list.children[next]?.scrollIntoView({block: "nearest"});
  });
  view.el.addEventListener("keydown", event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "l") { event.preventDefault(); event.stopPropagation(); editLocation(); }
    else if (event.target === address && event.key === "Enter") { event.preventDefault(); void load(address.value.trim(), {focusList: true}); }
    else if (event.altKey && event.key === "ArrowLeft" && !back.disabled) { event.preventDefault(); back.click(); }
    else if (event.altKey && event.key === "ArrowRight" && !forward.disabled) { event.preventDefault(); forward.click(); }
    else if (event.altKey && event.key === "ArrowUp" && !up.disabled) { event.preventDefault(); up.click(); }
  });
  async function read(path) {
    request?.abort(); request = new AbortController();
    return accountDirectoryRequest("browse", path ? {path} : {}, {signal: request.signal});
  }
  async function load(path, {index, focusList = false} = {}) {
    const token = ++generation; attempted = path; busy = true; invalid = true;
    current = ""; selected = ""; entries = []; filter.value = ""; retry.hidden = true;
    status.textContent = t("正在读取目录…"); list.replaceChildren(); list.removeAttribute("aria-activedescendant"); sync();
    try {
      const result = await read(path);
      if (closed || token !== generation) return;
      if (!result.path) throw new Error(t("操作失败，请重试。"));
      current = result.path; parent = result.parent || ""; address.value = current;
      entries = result.directories || []; truncated = !!result.truncated; invalid = false; busy = false;
      if (index !== undefined) { historyIndex = index; history[index] = current; }
      else if (history[historyIndex] !== current) { history.splice(historyIndex + 1); history.push(current); historyIndex = history.length - 1; }
      roots.replaceChildren(...(result.roots || []).map(root => button(root.name, () => void load(root.path))));
      endEdit(); renderCrumbs(); renderList(); sync();
      if (focusList) list.focus();
    } catch (error) {
      if (closed || token !== generation) return;
      busy = false; invalid = true; status.textContent = errorText(error); retry.hidden = false;
      crumbs.replaceChildren(node("span", path || t("主目录"))); sync();
    }
  }
  async function commit() {
    if (choose.disabled) return;
    const path = selected || current, token = ++generation;
    busy = true; retry.hidden = true; status.textContent = t("正在读取目录…"); sync();
    try {
      const result = await read(path);
      if (closed || token !== generation) return;
      if (!result.path) throw new Error(t("操作失败，请重试。"));
      onSelect(result.path); view.close();
    } catch (error) {
      if (closed || token !== generation) return;
      busy = false; invalid = true; status.textContent = errorText(error); sync();
    }
  }
  view.open(); void load(initialPath);
  return view;
}

export function openLogin(accountId, {onChanged = () => {}} = {}) {
  if (logins.has(accountId)) return logins.get(accountId);
  let timer, closed = false, terminal = false, pending = true, generation = 0;
  let regionSelect, startButton, startedAt = Date.now();
  const waiting = () => t("正在获取登录授权信息…已等待 {p0} 秒。显示授权链接或设备码后，请在浏览器完成授权。", {
    p0: Math.floor((Date.now() - startedAt) / 1000)});
  const showProgress = () => { if (!closed && pending) status.textContent = waiting(); };
  let progressTimer = setInterval(showProgress, 1000);
  const abort = new AbortController();
  const view = dialog(t("账号登录：{p0}", {p0: accountId}), () => {
    closed = true; clearTimeout(timer); clearInterval(progressTimer); abort.abort(); logins.delete(accountId);
  });
  const status = node("p", waiting(), {role: "status", "data-backend-login-state": "", "aria-busy": "true"});
  const code = node("code", "", {"data-backend-login-code": ""});
  const link = node("a", t("打开授权页面"), {target: "_blank", rel: "noopener noreferrer", hidden: true});
  const regionForm = node("div", undefined, {class: "backendForm", hidden: true});
  const authorization = node("form", undefined, {class: "backendForm", hidden: true});
  const response = field(authorization, "浏览器返回的授权码", "authorization_code", "", {type: "password", required: true, autocomplete: "off"});
  const inputHelp = node("small", "", {class: "backendHint", id: "backend-login-help", hidden: true});
  response.setAttribute("aria-describedby", inputHelp.id); response.parentElement.append(inputHelp);
  const submit = node("button", t("提交授权码"), {type: "submit", class: "portalButton"}); authorization.append(submit);
  authorization.addEventListener("submit", async event => {
    event.preventDefault();
    if (submit.disabled || terminal) return;
    const args = {account_id: accountId, authorization_code: response.value}; response.value = ""; submit.disabled = true;
    clearTimeout(timer);
    try { render(await call("UpdateProviderAccountLogin", args, {signal: abort.signal})); }
    catch (_) { if (!closed) { status.textContent = t("授权码提交失败，请重试。"); submit.disabled = false; timer = setTimeout(poll, 2000); } }
    finally { delete args.authorization_code; }
  });
  const cancel = button("取消登录", async () => {
    cancel.disabled = true; clearTimeout(timer); generation++;
    try {
      await call("CancelProviderAccountLogin", {account_id: accountId});
      if (!closed) { terminal = true; pending = false; clearInterval(progressTimer); status.setAttribute("aria-busy", "false"); status.textContent = t("登录已取消"); code.textContent = ""; link.hidden = true; authorization.hidden = true; response.value = ""; }
    } catch (error) { if (!closed) { status.textContent = errorText(error); cancel.disabled = false; } }
  }, true);
  view.el.append(regionForm, status, code, link, authorization, cancel); view.open(); logins.set(accountId, view);
  const render = result => {
    if (closed || terminal) return;
    const session = result.session || {};
    pending = !session.state || session.state === "pending";
    status.setAttribute("aria-busy", String(pending));
    const statusText = pending ? waiting() : t(session.error || loginStates[session.state] || session.state);
    if (status.textContent !== statusText) status.textContent = statusText;
    const codeText = session.verification_code || "";
    if (code.textContent !== codeText) code.textContent = codeText;
    let verificationUrl = "";
    try {
      const url = new URL(session.verification_url);
      if (["https:", "http:"].includes(url.protocol)) verificationUrl = url.href;
    } catch (_) { /* No authorization URL yet. */ }
    if (verificationUrl) { if (link.href !== verificationUrl) link.href = verificationUrl; }
    else if (link.hasAttribute("href")) link.removeAttribute("href");
    if (link.hidden !== !verificationUrl) link.hidden = !verificationUrl;
    terminal = ["succeeded", "failed", "expired", "cancelled"].includes(session.state);
    if (terminal) clearInterval(progressTimer);
    cancel.disabled = terminal;
    authorization.hidden = terminal || !session.requires_code_input;
    const callback = session.input_kind === "callback_url";
    response.parentElement.querySelector("span").textContent = t(callback ? "浏览器返回的完整地址" : "浏览器返回的授权码");
    submit.textContent = t(callback ? "提交回调地址" : "提交授权码");
    inputHelp.textContent = session.input_hint || ""; inputHelp.hidden = !session.input_hint;
    response.placeholder = session.input_hint || "";
    response.title = session.input_hint || "";
    submit.disabled = session.state !== "awaiting_user";
    if (session.state === "succeeded") { code.textContent = ""; link.hidden = true; onChanged(); }
    if (regionSelect) { regionSelect.disabled = session.state === "succeeded"; startButton.disabled = session.state === "succeeded"; }
    if (!terminal) timer = setTimeout(poll, 2000);
  };
  const poll = async () => {
    const current = generation;
    try {
      const result = await call("GetProviderAccountLogin", {account_id: accountId}, {signal: abort.signal});
      if (current === generation) render(result);
    } catch (error) { if (current === generation && !closed && !terminal) { pending = false; status.setAttribute("aria-busy", "false"); status.textContent = errorText(error); timer = setTimeout(poll, 4000); } }
  };
  const failed = error => {
    pending = false; clearInterval(progressTimer); status.setAttribute("aria-busy", "false");
    if (!closed) {
      status.textContent = errorText(error);
      if (regionSelect) { regionSelect.disabled = false; startButton.disabled = false; }
    }
  };
  const start = async () => {
    const current = ++generation;
    clearTimeout(timer); clearInterval(progressTimer); terminal = false; pending = true; startedAt = Date.now();
    code.textContent = ""; link.hidden = true; link.removeAttribute("href"); cancel.disabled = true;
    status.textContent = waiting(); status.setAttribute("aria-busy", "true");
    if (regionSelect) { regionSelect.disabled = true; startButton.disabled = true; }
    progressTimer = setInterval(showProgress, 1000);
    try {
      const args = {account_id: accountId, ...(regionSelect ? {region: regionSelect.value} : {})};
      const result = await call("OpenProviderAccountLogin", args, {signal: abort.signal});
      if (current === generation) render(result);
    } catch (error) { if (current === generation) failed(error); }
  };
  // Read the provider's login choices before creating an authorization flow.
  void call("GetProviderAccountLogin", {account_id: accountId}, {signal: abort.signal}).then(result => {
    if (closed) return;
    const options = result.login_options;
    if (!options?.regions?.length) { void start(); return; }
    regionForm.hidden = false;
    regionSelect = select(regionForm, "账号所属区域", "login_region", options.regions.map(r => [r.id, t(r.label)]), options.region);
    hint(regionSelect, "中国区账号使用 kimi.com，国际区账号使用 kimi.ai。切换区域后请重新获取授权。");
    startButton = button("获取登录授权", () => void start()); regionForm.append(startButton);
    regionSelect.addEventListener("change", () => {
      generation++; clearTimeout(timer); clearInterval(progressTimer); pending = false;
      code.textContent = ""; link.hidden = true; link.removeAttribute("href");
      status.setAttribute("aria-busy", "false"); status.textContent = t("请获取所选区域的登录授权。");
    });
    if (result.session?.state) render(result);
    else {
      pending = false; clearInterval(progressTimer); status.setAttribute("aria-busy", "false");
      status.textContent = t("请选择账号所属区域，然后获取登录授权。");
    }
  }).catch(failed);
  return view;
}

export async function mount(root, {me = {}, accountId = ""} = {}) {
  loadStyle(); root.classList.add("backendManagement"); root.replaceChildren();
  let destroyed = false, busy = false, editor = null, login = null;
  let pendingAccountId = accountId;
  const abort = new AbortController();
  const canWrite = me.access?.permission_system === "open" || me.access?.allows?.UpdateProviderAccount === true;
  const canRegister = me.access?.permission_system === "open" || me.access?.allows?.RegisterProviderAccount === true;
  const canDeleteAccount = me.access?.permission_system === "open" || me.access?.allows?.DeleteProviderAccount === true;
  const canDeleteApi = me.access?.permission_system === "open" || me.access?.allows?.DeleteModelApi === true;
  const execute = (name, args = {}, options = {}) => call(name, args, {...options, signal: abort.signal});
  const state = {providers: [], accounts: [], apis: [], roles: []};
  let providersReady = false, rolesReady = false;
  const addAccount = button("新增执行账号", () => accountEditor(), true);
  const addSubscription = button("接入订阅帐号", () => accountEditor("subscription_login"), true);
  const addApi = button("新增 API Key", () => apiEditor(), !canWrite);
  function syncAccountButton() {
    addAccount.disabled = !canRegister || busy || !providersReady || !state.providers.length;
    addSubscription.disabled = !canRegister || busy || !providersReady || !state.providers.some(p => p.kinds.includes("subscription_login"));
  }
  const notice = node("p", "", {role: "status", "data-backend-notice": ""}); root.append(notice);
  const introduction = node("section", undefined, {class: "backendIntroduction"});
  introduction.append(node("h2", t("添加 AI 后端")), addSubscription, addApi); root.append(introduction);
  const management = advanced(root);
  const sections = {};
  for (const [key, title] of [["accounts", "已接入的 AI"], ["providers", "Provider"], ["apis", "模型 API"], ["roles", "Backend 角色"]]) {
    const section = node("section", undefined, {class: "settingsSection", "data-backend-section": key, "aria-label": t(title)});
    const header = node("header"), actions = node("div", undefined, {class: "backendActions"});
    header.append(node("h2", t(title)), actions);
    const output = node("div", undefined, {class: "backendTableScroll"});
    section.append(header, output);
    if (key === "accounts") root.insertBefore(section, management.parentElement); else management.append(section);
    sections[key] = {output, actions};
  }
  const run = async (work, secret = false, {success = "已保存", errorTarget = null, onError = null} = {}) => {
    if (busy || destroyed) return;
    busy = true; notice.textContent = t("正在处理…");
    const buttons = [...root.querySelectorAll("button"), ...(editor ? editor.el.querySelectorAll("button") : [])];
    const disabled = buttons.map(el => el.disabled); buttons.forEach(el => el.disabled = true);
    try { const completed = await work(); if (!destroyed && completed !== false) notice.textContent = t(success); }
    catch (error) { if (!destroyed) {
      const message = secret ? secretSubmissionError(error) : errorText(error);
      notice.textContent = message;
      const alert = errorTarget || editor?.el.querySelector('[role="alert"]');
      if (alert) { alert.textContent = message; alert.hidden = false; }
      onError?.(error);
      if (errorTarget?.isConnected) { errorTarget.scrollIntoView?.({block: "nearest"}); errorTarget.focus(); }
    } }
    finally { busy = false; buttons.forEach((el, i) => { if (el.isConnected) el.disabled = disabled[i]; }); syncAccountButton(); }
  };
  const buildTable = (headers, rows) => {
    const grid = node("table"), head = node("tr"), body = node("tbody");
    for (const title of headers) head.append(node("th", t(title), {scope: "col"}));
    const thead = node("thead"); thead.append(head); grid.append(thead, body);
    for (const {cells, id} of rows) {
      const row = node("tr", undefined, id ? {"data-backend-account": id, tabindex: "-1"} : {});
      for (const value of cells) { const cell = node("td"); cell.append(value instanceof Node ? value : document.createTextNode(String(value ?? "—"))); row.append(cell); }
      body.append(row);
    }
    return grid;
  };
  const table = (key, headers, rows) => {
    sections[key].output.replaceChildren(rows.length ? buildTable(headers, rows) : node("p", t("暂无登记")));
  };
  const actions = (...buttons) => { const el = node("div", undefined, {class: "backendActions"}); el.append(...buttons); return el; };
  const confirm = (title, id) => window.PortalUI.confirm({title: t(title), message: id, confirmLabel: t("删除"), tone: "danger"});
  function deleteControl(title, id, instruction, argument, reload, disabled, disabledReason = "当前身份没有删除权限。") {
    const control = node("div", undefined, {class: "backendDelete"});
    const feedback = node("p", "", {role: "alert", tabindex: "-1", hidden: true});
    const recovery = button("查看 Backend 角色", () => {
      const target = sections[recovery.dataset.section].output.parentElement;
      const details = target.closest("details"); if (details) details.open = true;
      target.tabIndex = -1; target.scrollIntoView?.({block: "nearest"}); target.focus();
    });
    recovery.hidden = true;
    const remove = button("删除", () => void run(async () => {
      feedback.hidden = true; feedback.textContent = ""; recovery.hidden = true;
      if (!await confirm(title, id)) { notice.textContent = t("已取消删除"); return false; }
      await execute(instruction, {[argument]: id}); await reload();
    }, false, {success: "已删除", errorTarget: feedback, onError: error => {
      const section = error.details?.reason === "account.role_in_use" ? "roles"
        : error.details?.reason === "account.api_in_use" ? "accounts" : "";
      if (section) {
        recovery.dataset.section = section;
        recovery.textContent = t(section === "roles" ? "查看 Backend 角色" : "查看已接入的 AI");
        recovery.hidden = false;
        void refresh(section);
      }
    }}), disabled);
    if (disabled) remove.title = t(disabledReason);
    control.append(remove, feedback, recovery); return control;
  }
  function newEditor(title, onClose = () => {}) {
    editor?.close();
    const view = dialog(t(title), () => { onClose(); view.el.querySelectorAll('[type="password"]').forEach(el => el.value = ""); if (editor === view) editor = null; });
    editor = view;
    const form = node("form", undefined, {class: "backendForm"});
    form.append(node("p", "", {role: "alert"})); view.el.append(form); view.open(); return {view, form};
  }
  const saveButton = form => form.append(node("button", t("保存"), {type: "submit", class: "portalButton is-primary"}));
  function claudeCatalogFields(host, config = {}, options = null) {
    const model = field(host, "默认模型", "default_model", config.default_model || "", {required: true, autocomplete: "off"});
    hint(model, "填写账号实际可用的模型名称；Portal 按原样发送，不自动改写模型名。");
    const models = field(options || advanced(host), "模型目录（JSON 数组）", "models", JSON.stringify(config.models || [], null, 2), {tag: "textarea", rows: "3"});
    hint(models, "留空数组时将默认模型登记为唯一模型；填写目录时，默认模型必须在目录中且已启用。");
  }
  function readClaudeCatalog(form) {
    const defaultModel = form.elements.default_model.value.trim();
    if (!defaultModel) throw new Error(t("请填写 Claude 默认模型。"));
    let models;
    try { models = JSON.parse(form.elements.models.value); }
    catch (_) { throw new Error(t("模型目录必须是 JSON 数组。")); }
    if (!Array.isArray(models)) throw new Error(t("模型目录必须是 JSON 数组。"));
    if (!models.length) models = [{id: defaultModel}];
    const ids = new Set();
    models = models.map(row => {
      if (typeof row === "string") row = {id: row};
      if (!row || typeof row !== "object" || Array.isArray(row) || typeof row.id !== "string" ||
          !row.id.trim() || ids.has(row.id.trim()) || ("enabled" in row && typeof row.enabled !== "boolean")) {
        throw new Error(t("模型目录的模型 ID 必须非空且不重复，enabled 必须是 boolean。"));
      }
      ids.add(row.id.trim()); return {...row, id: row.id.trim()};
    });
    if (!models.some(row => row.id === defaultModel && row.enabled !== false)) {
      throw new Error(t("Claude 路由的 default_model 必须是已启用的模型"));
    }
    return {default_model: defaultModel, models};
  }
  function claudeCatalogEditor(account, requestedModel = "") {
    if (!canWrite || account?.provider !== "claude" || account.kind !== "subscription_login" ||
        !Number.isInteger(account.registration_version)) return;
    const {view, form} = newEditor("编辑 Claude 模型目录");
    claudeCatalogFields(form, {...(account.config || {}), default_model: account.config?.default_model || requestedModel});
    saveButton(form);
    form.addEventListener("submit", event => {
      event.preventDefault();
      let config;
      try { config = {...(account.config || {}), ...readClaudeCatalog(form)}; }
      catch (error) { form.querySelector('[role="alert"]').textContent = errorText(error); return; }
      void run(async () => {
        await execute("UpdateProviderAccountRegistration", {account_id: account.account_id, changes: {config}},
          {expectedVersion: account.registration_version});
        view.close(); await refresh("accounts"); setupEditor(account.account_id);
      });
    });
  }
  function setupEditor(accountId, {useDefault: requestedDefault = false} = {}) {
    let timer, progressTimer, closed = false, checking = false, working = false, setup = null;
    let preparationRequested = false, setupFailed = false;
    let progressMessage = "", startedAt = 0;
    const {view, form} = newEditor("接入并启用 AI", () => { closed = true; clearTimeout(timer); clearInterval(progressTimer); clearInterval(preparationTimer); });
    form.append(node("p", accountId));
    const preparationPhases = {prerequisites: "检查系统依赖", download: "获取运行环境版本", dependency_install: "下载并安装运行依赖",
      executable_validation: "校验运行程序", service_activation: "启动并检查后台服务"};
    const stages = node("ol", undefined, {class: "backendSetupStages", "data-backend-setup-stages": ""});
    const stageRows = ["准备运行环境", "登录授权", "测试连接"].map(label => {const row = node("li", t(label)); stages.append(row); return row;});
    const preparationDetail = node("p", "", {class: "backendHint", "data-backend-preparation-detail": ""});
    const elapsed = node("p", "", {class: "backendHint", "data-backend-preparation-elapsed": ""});
    const openedAt = Date.now();
    const preparationTimer = setInterval(() => {
      if (closed || !setup || setup.runtime_ready || !["preparing", "pending", "busy"].includes(setup.preparation?.status)) return;
      const start = Date.parse(setup.preparation?.started_at) || openedAt;
      elapsed.textContent = t("已等待 {p0} 秒", {p0: Math.max(0, Math.floor((Date.now() - start) / 1000))});
    }, 1000);
    form.append(stages, preparationDetail, elapsed);
    const status = node("p", "", {role: "status", "data-backend-setup-status": ""});
    const nextStep = node("p", "", {"data-backend-check-next-step": "", hidden: true});
    const checkDetail = node("p", "", {class: "backendHint", "data-backend-check-detail": "", hidden: true});
    const checkLog = node("textarea", "", {id: "backend-check-log", "data-backend-check-log": "", rows: "12", readOnly: true,
      "aria-label": t("接入测试日志（已脱敏，可复制）"), hidden: true});
    const showLog = button("显示详细日志", () => {
      checkLog.hidden = !checkLog.hidden;
      showLog.textContent = t(checkLog.hidden ? "显示详细日志" : "收起详细日志");
      showLog.setAttribute("aria-expanded", String(!checkLog.hidden));
    });
    showLog.hidden = true;
    showLog.setAttribute("aria-controls", checkLog.id);
    showLog.setAttribute("aria-expanded", "false");
    const capabilities = node("p", "");
    const loginHint = node("p", "", {class: "backendHint", "data-backend-login-hint": "", hidden: true});
    form.append(loginHint);
    const model = field(form, "模型（可选）", "model", "", {autocomplete: "off", placeholder: t("自动使用账号默认模型")});
    hint(model, "无需手动填写；授权完成后自动读取默认模型，也可选择其他模型。");
    const choices = node("datalist", undefined, {id: "backend-setup-models"});
    model.setAttribute("list", choices.id); form.append(choices);
    const useDefault = field(form, "用作默认模型偏好", "use_default", "", {type: "checkbox"});
    const defaultRole = state.roles.find(role => role.role === "default");
    useDefault.checked = requestedDefault || !defaultRole?.account_id || defaultRole.account_id === accountId;
    hint(useDefault, "测试通过后保存后端与模型偏好；新会话会在同类兼容账号中按可用额度自动选择，不自动跨后端或转用其他付费 API。");
    const authorize = button("登录", () => {
      login = openLogin(accountId, {onChanged: () => { login?.close(); void perform(reload); }});
    }, true);
    const retry = button("重试", () => { preparationRequested = false; void perform(reload); });
    const linkedApi = () => state.apis.find(api => api.api_id === state.accounts.find(a => a.account_id === accountId)?.config?.api_id);
    const editConnection = button("编辑连接", () => { const api = linkedApi(); if (api) apiEditor(api, {accountId}); });
    const nativeClaude = () => state.accounts.find(a => a.account_id === accountId && a.provider === "claude" &&
      a.kind === "subscription_login" && Number.isInteger(a.registration_version));
    const editModels = button("编辑模型目录", () => claudeCatalogEditor(nativeClaude(), model.value.trim()));
    const test = button("测试并完成", () => void perform(async () => {
      nextStep.hidden = checkDetail.hidden = true;
      nextStep.textContent = checkDetail.textContent = "";
      checkLog.hidden = showLog.hidden = true; checkLog.value = "";
      showLog.textContent = t("显示详细日志"); showLog.setAttribute("aria-expanded", "false");
      checking = true; model.disabled = true; useDefault.disabled = true; setButtons();
      stageRows[2].textContent = t("测试连接") + " · " + t("进行中");
      try {
        const receipt = await execute("CheckProviderAccount", {account_id: accountId, model: model.value.trim()});
        if (closed) return;
        await pollCheck(receipt.execution_id);
      } catch (error) { checking = false; model.disabled = false; useDefault.disabled = false; throw error; }
    }, "正在启动本地后台并检查连接，请稍候…"), true);
    form.append(capabilities, status, nextStep, checkDetail, actions(authorize, retry, editConnection, editModels, test, showLog), checkLog);
    form.addEventListener("submit", event => event.preventDefault());
    function setButtons() {
      authorize.hidden = !setup || ["logged_in", "succeeded", "not_applicable"].includes(setup.login_state);
      authorize.disabled = !canWrite || !setup?.runtime_ready || working || checking;
      test.disabled = !canWrite || !setup?.enabled || !setup?.runtime_ready || setupFailed ||
        ["logged_out", "login_expired", "pending", "awaiting_user"].includes(setup?.login_state) || working || checking;
      retry.hidden = !setupFailed;
      retry.disabled = working || checking;
      editConnection.hidden = !linkedApi();
      editConnection.disabled = !canWrite || working || checking;
      editModels.hidden = !nativeClaude() || setup?.provider !== "claude" ||
        Boolean(!setup.catalog_error && setup.models?.length && setup.default_model);
      editModels.disabled = !canWrite || working || checking;
    }
    async function perform(work, message = "正在检查运行环境与登录状态…", quiet = false) {
      if (closed || working) return;
      working = true; setupFailed = false;
      clearTimeout(timer); clearInterval(progressTimer);
      if (!quiet) {
        setButtons(); startedAt = Date.now(); progressMessage = message;
        status.setAttribute("aria-busy", "true");
        status.textContent = t(message);
        progressTimer = setInterval(() => { if (!closed) status.textContent = t(progressMessage) + " " +
          t("已等待 {p0} 秒", {p0: Math.floor((Date.now() - startedAt) / 1000)}); }, 1000);
      }
      try { await work(); }
      catch (error) { if (!closed) { setupFailed = true; status.textContent = errorText(error); } }
      finally { working = false; if (!checking && !quiet) stopProgress(); if (!closed) setButtons(); }
    }
    function stopProgress() {
      clearInterval(progressTimer);
      if (!closed) status.setAttribute("aria-busy", "false");
    }
    let modelChoices = "";
    async function reload() {
      const result = await execute("GetProviderAccountSetup", {account_id: accountId});
      if (closed) return;
      setup = result.setup;
      const preparation = setup.preparation || {};
      const failed = ["failed", "interrupted"].includes(preparation.status);
      const authenticated = ["logged_in", "succeeded", "not_applicable"].includes(setup.login_state);
      stageRows[0].textContent = t("准备运行环境") + " · " + t(setup.runtime_ready ? "已完成" : failed ? "失败，可重试" : "进行中");
      stageRows[1].textContent = t("登录授权") + " · " + t(authenticated ? "已完成" : setup.runtime_ready ? "等待授权" : "等待环境就绪");
      stageRows[2].textContent = t("测试连接") + " · " + t(authenticated && setup.runtime_ready ? "等待你开始测试" : "等待前置步骤");
      const diagnostic = preparation.diagnostic || {};
      preparationDetail.textContent = [t(preparationPhases[preparation.phase] || ""), t(preparation.message || ""),
        failed && diagnostic.kind ? t("错误类别：{p0}", {p0: diagnostic.kind}) : "",
        diagnostic.rollback_failed ? t("自动恢复未完成，请联系管理员。") : ""].filter(Boolean).join(" · ");
      elapsed.hidden = setup.runtime_ready || failed;
      const nextChoices = JSON.stringify(setup.models || []);
      if (nextChoices !== modelChoices) {
        modelChoices = nextChoices;
        choices.replaceChildren(...(setup.models || []).map(item => node("option", undefined,
          {value: typeof item === "string" ? item : item.id || item.model || ""})));
      }
      if (!model.value) model.value = setup.default_model || "";
      const needsLogin = !["logged_in", "succeeded", "not_applicable"].includes(setup.login_state);
      loginHint.hidden = !needsLogin;
      const loginText = t(setup.provider === "codex" ? "先点击登录，获取设备码并在浏览器授权；无需填写模型名称。" : "先点击登录并在浏览器完成授权；无需填写模型名称。");
      if (loginHint.textContent !== loginText) loginHint.textContent = loginText;
      model.parentElement.hidden = useDefault.parentElement.hidden = capabilities.hidden = needsLogin;
      const capabilityText = t(setup.supports_tools ? "支持对话与工具任务；本次测试验证模型回复。" : "支持对话；此接入方式不提供工具执行。");
      if (capabilities.textContent !== capabilityText) capabilities.textContent = capabilityText;
      const statusText = [t(setup.runtime_ready ? "已就绪" : "正在自动准备运行环境…"),
        t(loginStates[setup.login_state] || setup.login_state), t(setup.preparation?.message || ""), t(setup.catalog_error || "")].filter(Boolean).join(" · ");
      if (status.textContent !== statusText) status.textContent = statusText;
      clearTimeout(timer);
      if (!setup.runtime_ready && canWrite) {
        if (["preparing", "pending"].includes(setup.preparation?.status)) {
          preparationRequested = true;
          timer = setTimeout(() => void perform(reload, undefined, true), 2000);
        } else if (!preparationRequested) {
          preparationRequested = true;
          progressMessage = "正在自动准备运行环境…";
          const result = await execute("PrepareProviderRuntime", {provider: setup.provider});
          if (closed) return;
          if (result.preparation.status === "busy") {
            // Another runtime update owns the lock; observe and retry without user action.
            preparationRequested = false;
            status.textContent = t("运行环境正在更新，完成后将自动继续。");
            timer = setTimeout(() => void perform(reload, undefined, true), 2000);
          } else {
            await reload();
          }
        } else {
          throw new Error(setup.preparation?.message || t("运行环境准备未完成，请重试。"));
        }
      }
    }
    async function pollCheck(executionId) {
      if (closed) return;
      try {
        const result = await execute("GetProviderAccountCheck", {account_id: accountId, execution_id: executionId});
        if (closed) return;
        stageRows[2].textContent = t("测试连接") + " · " + t(result.check.passed ? "已完成" :
          ["failed", "cancelled", "completed"].includes(result.check.status) ? "失败，可重试" : "进行中");
        status.textContent = t(result.check.message);
        if (!["completed", "failed", "cancelled"].includes(result.check.status)) {
          nextStep.textContent = t(result.check.next_step || "");
          nextStep.hidden = !result.check.next_step;
          progressMessage = result.check.message;
          timer = setTimeout(() => void pollCheck(executionId), 2000); return;
        }
        if (!result.check.passed) {
          nextStep.textContent = t(result.check.next_step || "请复制下方测试编号交给管理员排查；无需重新注册或再次发送 API Key。");
          nextStep.hidden = false;
          checkDetail.replaceChildren(node("span", t("测试编号") + ": "), node("code", executionId));
          if (result.check.diagnostic?.reason) checkDetail.append(node("span", " · " + t("失败类别") + ": " + result.check.diagnostic.reason));
          if (Number.isInteger(result.check.diagnostic?.http_status)) checkDetail.append(node("span", " · HTTP " + result.check.diagnostic.http_status));
          checkDetail.hidden = false;
          checkLog.value = result.check.log || t("暂无详细日志，请提供测试编号以便排查。");
          showLog.hidden = false;
        }
        if (result.check.passed && useDefault.checked) {
          progressMessage = "测试通过，正在设为默认 AI…";
          status.textContent = t(progressMessage);
          const currentDefault = state.roles.find(role => role.role === "default");
          await execute("UpdateBackendRole", {backend_role: "default", account_id: accountId, account_selection: "auto",
            model: result.check.model, verification_execution_id: executionId,
            ...(currentDefault?.account_id === accountId && currentDefault.model === result.check.model
              ? {reasoning_effort: currentDefault.reasoning_effort || ""} : {})}, {expectedVersion: currentDefault?.version || 0});
          if (!closed) status.textContent = t("测试通过，已设为默认 AI；新对话和未配置的 Resident 将使用此账号。");
          await refresh("roles");
        }
        stopProgress();
        checking = false; model.disabled = false; useDefault.disabled = false; setButtons();
        if (result.check.passed && !closed) {
          notice.textContent = t(useDefault.checked ? "测试通过，已保存模型偏好并启用自动选择账号。" : "测试通过，AI 接入已完成。");
          view.close();
        }
      } catch (error) {
        stopProgress();
        if (!closed) { checking = false; model.disabled = false; useDefault.disabled = false; status.textContent = errorText(error); setButtons(); }
      }
    }
    void perform(reload);
    return view;
  }
  function accountEditor(preferredKind = "") {
    if (destroyed || busy || editor) return;
    if (!canRegister) throw new Error(t("你没有新增 AI 账号的权限。"));
    if (!providersReady || !state.providers.length) throw new Error(t("Provider 列表不可用，请刷新后重试。"));
    let directoryBrowser = null, closed = false, scanGeneration = 0;
    const {view, form} = newEditor("新增执行账号", () => { closed = true; scanGeneration++; directoryBrowser?.close(); });
    form.append(node("p", t("在这里添加可用的 Agent 后端，作为 Portal Resident 的来源。"), {class: "backendHint"}));
    const id = field(form, "账号名称", "account_id", "", {required: true, pattern: "[a-z][a-z0-9_\\-]{0,127}", autocomplete: "off", placeholder: "my-ai"});
    hint(id, "为这个 Agent 后端添加指代名称。");
    const providers = preferredKind ? state.providers.filter(p => p.kinds.includes(preferredKind)) : state.providers;
    const provider = select(form, "AI 后端", "provider", providers.map(p => [p.provider, p.title || p.provider]));
    const kind = select(form, "接入方式", "kind", []);
    const extra = node("div", undefined, {class: "backendForm"}); form.append(extra);
    const directories = new Map();
    const reuseProviders = new Set();
    const provisionalName = "new-account";
    const directoryKey = () => provider.value + (reuseProviders.has(provider.value) ? ":existing" : ":new");
    const defaultDirectory = () => {
      const prefix = state.providers.find(p => p.provider === provider.value)?.account_directory_prefix || "";
      return prefix ? prefix + (/^[a-z][a-z0-9_\-]{0,127}$/.test(id.value.trim()) ? id.value.trim() : provisionalName) : "";
    };
    const syncDirectory = () => {
      const directory = form.elements.namedItem("account_dir");
      if (directory && !reuseProviders.has(provider.value) && !directories.has(directoryKey())) directory.value = defaultDirectory();
    };
    const renderFields = () => {
      scanGeneration++; directoryBrowser?.close(); directoryBrowser = null;
      extra.replaceChildren();
      if (kind.value === "subscription_login") {
        const reuse = field(extra, "使用本机已有登录", "reuse_existing", "", {type: "checkbox"});
        reuse.parentElement.classList.add("backendReuse");
        reuse.checked = reuseProviders.has(provider.value);
        reuse.addEventListener("change", () => {
          if (reuse.checked) reuseProviders.add(provider.value); else reuseProviders.delete(provider.value);
          renderFields();
        });
        const directory = field(extra, "账号目录", "account_dir", directories.get(directoryKey()) ?? (reuse.checked ? "" : defaultDirectory()), {autocomplete: "off", required: reuse.checked});
        const choosePath = path => {
          directory.value = path; directories.set(directoryKey(), path);
        };
        directory.addEventListener("input", () => {
          if (directory.value.trim()) directories.set(directoryKey(), directory.value);
          else { directories.delete(directoryKey()); syncDirectory(); }
        });
        hint(directory, reuse.checked ? "选择 Portal 主机上已有登录的目录，也可以手动输入。候选目录的登录状态将在接入后验证。" : "已自动填写待创建目录，可直接继续；保存后创建目录并引导登录。目录可修改。");
        const row = node("div", undefined, {class: "backendDirectoryRow"});
        directory.parentElement.append(row); row.append(directory, button("浏览", () => {
          directoryBrowser?.close(); directoryBrowser = browseHostDirectory(directory.value.trim(), choosePath);
        }));
        if (reuse.checked) {
          const candidates = node("div", undefined, {class: "backendDirectoryList"});
          const status = node("p", "", {role: "status", "aria-live": "polite"});
          const scan = async () => {
            const token = ++scanGeneration, selectedProvider = provider.value;
            status.textContent = t("正在查找本机账号目录…"); scanButton.disabled = true;
            try {
              const result = await accountDirectoryRequest("discover", {provider: selectedProvider});
              if (closed || token !== scanGeneration || !extra.contains(candidates)) return;
              candidates.replaceChildren(...(result.candidates || []).map(candidate => {
                const choice = button(candidate.path, () => choosePath(candidate.path));
                const item = node("div"); item.append(choice, node("small", candidate.evidence || "")); return item;
              }));
              status.textContent = (result.candidates?.length ? t("选择一个候选目录，或手动输入。登录状态尚未验证。") : t("未找到候选目录，可手动输入或浏览主机目录。"))
                + (result.truncated ? " " + t("仅显示部分扫描结果。") : "")
                + (result.warnings?.length ? " " + result.warnings.join("；") : "");
            } catch (error) {
              if (closed || token !== scanGeneration) return;
              status.textContent = t("扫描失败，可手动输入或浏览主机目录。") + " " + errorText(error);
            } finally { if (!closed && token === scanGeneration) scanButton.disabled = false; }
          };
          const scanButton = button("重新扫描", () => void scan());
          extra.append(scanButton, status, candidates); void scan();
        }
      }
      if (kind.value === "service") extra.append(node("p", t("复用 Provider 已有的服务配置和授权。")));
      form.querySelector('[type="submit"]').disabled = false;
      if (kind.value === "subscription_login" && provider.value === "claude") {
        const model = field(extra, "默认模型", "default_model", "sonnet", {readOnly: true});
        hint(model, "已自动选择默认模型，接入后可在模型目录中修改。");
      } else if (kind.value === "api_profile") {
        const backend = state.providers.find(p => p.provider === provider.value) || {};
        const compatible = state.apis.filter(item => !backend.api_protocol || (backend.api_upstream_protocols || [backend.api_protocol]).some(protocol => (item.harness_protocols || []).includes(protocol)));
        extra.append(button("新建模型 API 并接入", () => {
          if (id.value && !id.reportValidity()) return;
          apiEditor(null, {newProvider: provider.value});
        }, !canWrite));
        if (compatible.length) {
          const api = select(extra, "使用已有模型 API", "api_id", compatible.map(api => [api.api_id, `${api.api_id} · ${api.default_model}`]));
          api.required = true;
        } else {
          extra.append(node("p", t("还没有可用的模型 API。点击上方按钮，填写接口地址、API Key 和模型即可继续。"), {class: "backendHint"}));
          form.querySelector('[type="submit"]').disabled = true;
        }
        if (backend.api_protocol && backend.api_protocol !== "openai_chat") {
          extra.append(node("small", t("Claude Code 和 Codex 可通过 Portal 转换 OpenAI 与 Anthropic 协议，工具仍由所选运行器执行。"), {class: "backendHint"}));
        }
      }
    };
    const renderKinds = () => {
      kind.replaceChildren(...(state.providers.find(p => p.provider === provider.value)?.kinds || []).map(value => node("option", t(kinds[value] || value), {value})));
      if (preferredKind) kind.value = preferredKind;
      kind.parentElement.hidden = kind.options.length < 2;
      renderFields();
    };
    saveButton(form); id.addEventListener("input", syncDirectory); provider.addEventListener("change", renderKinds); kind.addEventListener("change", renderFields); renderKinds();
    form.addEventListener("submit", event => {
      event.preventDefault(); if (busy) return;
      const value = name => form.elements.namedItem(name)?.value || "";
      const config = {};
      if (kind.value === "subscription_login") {
        config.account_dir = value("account_dir").trim();
        if (reuseProviders.has(provider.value) && !config.account_dir) { form.elements.namedItem("account_dir").reportValidity(); return; }
      }
      if (kind.value === "subscription_login" && provider.value === "claude") {
        config.default_model = value("default_model");
        config.models = [{id: config.default_model}];
      }
      if (kind.value === "api_profile") {
        config.api_id = value("api_id");
        if (!config.api_id) return;
      }
      const args = {account_id: id.value.trim(), provider: provider.value, kind: kind.value, config, max_concurrency: 1};
      if (kind.value === "subscription_login" && reuseProviders.has(provider.value)) args.reuse_existing = true;
      void run(async () => {
        await execute("RegisterProviderAccount", args);
        view.close(); await refresh("accounts"); setupEditor(args.account_id);
      });
    });
  }
  function apiEditor(api = null, {accountId: setupAccountId = "", newProvider = "provider_api"} = {}) {
    const {view, form} = newEditor(api ? "编辑模型 API" : "新增模型 API");
    form.append(node("p", t(api ? "修改连接信息。API Key 留空时保留当前密钥。" : "填写接口地址、API Key 和模型列表。连接名称和账号关联会自动完成。"), {class: "backendHint"}));
    const url = field(form, "接口基础地址（Base URL）", "base_url", api?.base_url || "", {type: "url", required: true, placeholder: "https://api.example.com/v1"});
    hint(url, "复制服务商提供的 API 基础地址，不是官网或聊天网页；不要附加 /chat/completions。");
    const secret = field(form, "API Key（密钥）", "secret", "", {type: "password", autocomplete: "new-password"});
    hint(secret, api ? "留空保留当前密钥；填写则替换。密钥不会回显，也不是一次性验证码。" : "粘贴服务商生成的 API Key。Portal 会保存供后续调用使用，提交后不回显；这不是一次性验证码。无需密钥的接口可留空。");
    const primary = select(form, "接口协议", "api_kind", [["openai_compatible_chat", "OpenAI Chat Completions"],
      ["openai_responses", "OpenAI Responses"], ["anthropic_messages", "Anthropic Messages"],
      ...(api?.kind === "typesafe_system_one" ? [["typesafe_system_one", "Jev"]] : [])], api?.kind || "openai_compatible_chat");
    hint(primary, "选择服务商实际支持的协议。Claude Code 和 Codex 可自动转换使用，无需接口同时支持两家的协议。");
    const models = modelCatalog(form, api);
    const declaredModels = JSON.stringify(models.read(false).models);
    const options = advanced(form);
    const generatedId = "api-" + [...window.crypto.getRandomValues(new Uint8Array(12))].map(n => n.toString(16).padStart(2, "0")).join("");
    const id = field(options, "连接名称（可选）", "api_id", api?.api_id || "", {readOnly: Boolean(api), placeholder: t("留空自动生成，无需从服务商获取"), ...(!api ? {pattern: "[a-z][a-z0-9_\\-]{0,127}"} : {})});
    hint(id, "仅用于在 Portal 中区分连接，留空即可。自定义时以小写字母开头，只用小写字母、数字、下划线或短横线，最多 128 字符。");
    const provider = field(options, "服务商名称（可选）", "provider", api?.provider || "", {placeholder: "example-provider"});
    hint(provider, "仅用于标记接口来源，不是登录账号；填写服务商名称即可。");
    const path = field(options, "服务器上的密钥文件路径（可选）", "secret_file", api?.secret_file || "");
    hint(path, "仅在密钥已由管理员放在 Portal 服务器上时填写绝对路径；不是你电脑上的文件。已填写 API Key 时无需填写。");
    // Agent harnesses speak their own protocol to the API; Chat Completions follows the kind.
    const offered = new Set(api?.harness_protocols || ["openai_chat"]);
    const anthropicOptions = api?.protocols?.anthropic_messages || {};
    const chat = field(options, "也支持 OpenAI Chat Completions", "protocol_openai_chat", "", {type: "checkbox"});
    chat.checked = offered.has("openai_chat");
    const responses = field(options, "也支持 OpenAI Responses（/responses，Codex 使用）", "protocol_openai_responses", "", {type: "checkbox"});
    responses.checked = offered.has("openai_responses");
    const anthropic = field(options, "也支持 Anthropic Messages（Claude Code 使用）", "protocol_anthropic_messages", "", {type: "checkbox"});
    anthropic.checked = offered.has("anthropic_messages");
    hint(anthropic, "只勾选接口实际提供的附加协议；Portal 会优先直连，并在需要时为 Claude Code 和 Codex 转换协议。");
    const anthropicUrl = field(options, "Anthropic 接口地址（可选）", "anthropic_base_url", anthropicOptions.base_url || "",
      {type: "url", placeholder: t("留空使用去掉 /v1 的接口基础地址")});
    const anthropicAuth = select(options, "Anthropic 认证方式", "anthropic_auth",
      [["bearer", "Authorization: Bearer"], ["x-api-key", "x-api-key"]], anthropicOptions.auth || "bearer");
    const protocolState = () => JSON.stringify([primary.value, chat.checked, responses.checked, anthropic.checked, anthropicUrl.value.trim(), anthropicAuth.value]);
    const declaredProtocols = protocolState();
    const primaryWire = () => ({openai_compatible_chat: "openai_chat", openai_responses: "openai_responses", anthropic_messages: "anthropic_messages"})[primary.value];
    const showAnthropic = () => {
      for (const [box, protocol] of [[chat, "openai_chat"], [responses, "openai_responses"], [anthropic, "anthropic_messages"]]) box.parentElement.hidden = primaryWire() === protocol;
      anthropicUrl.parentElement.hidden = anthropicAuth.parentElement.hidden = !(anthropic.checked || primary.value === "anthropic_messages");
    };
    showAnthropic();
    // The endpoint answers which protocols it serves; the result fills the boxes above, which stay editable.
    const probeResults = node("ul", undefined, {class: "backendProbe", "aria-live": "polite"});
    const probe = button("检测接口协议", () => void (async () => {
      probe.disabled = true; probeResults.replaceChildren(node("li", t("正在检测…")));
      try {
        const args = {...(api ? {api_id: api.api_id} : {}), base_url: url.value.trim(), default_model: models.read().default_model,
          ...(secret.value ? {secret: secret.value} : {}), ...(path.value.trim() ? {secret_file: path.value.trim()} : {}),
          ...(anthropicUrl.value.trim() ? {anthropic_base_url: anthropicUrl.value.trim()} : {})};
        const {results} = await execute("ProbeModelApi", args);
        probeResults.replaceChildren(...[["openai_chat", "Chat Completions（OpenCode、Kimi Code、直连）"], ["openai_responses", "OpenAI Responses（Codex）"],
          ["anthropic_messages", "Anthropic Messages（Claude Code、DeepSeek Harness）"]].map(([name, label]) => {
          const result = results[name] || {};
          const detail = [result.http_status ? `HTTP ${result.http_status}` : "", result.message ? t(result.message) : ""].filter(Boolean).join(" · ");
          return node("li", `${t(label)}：${t(probeStates[result.status] || result.status || "—")}${detail ? `（${detail}）` : ""}`,
            {"data-protocol": name, "data-status": result.status || ""});
        }));
        for (const [box, name] of [[chat, "openai_chat"], [responses, "openai_responses"], [anthropic, "anthropic_messages"]]) {
          if (results[name]?.status === "supported") box.checked = true;
          else if (results[name]?.status === "unsupported") box.checked = false;
        }
        if (results[primaryWire()]?.status === "unsupported") {
          const supported = ["openai_responses", "openai_chat", "anthropic_messages"].find(name => results[name]?.status === "supported");
          if (supported) primary.value = supported === "openai_chat" ? "openai_compatible_chat" : supported;
        }
        if (results.anthropic_messages?.auth) anthropicAuth.value = results.anthropic_messages.auth;
        showAnthropic(); renderHarnesses();
      } catch (error) { probeResults.replaceChildren(node("li", errorText(error), {role: "alert"})); }
      finally { probe.disabled = !canWrite; }
    })(), !canWrite);
    probe.name = "probe_protocols"; options.append(probe);
    hint(probe, "向三种协议各发一次很短的请求并按结果勾选上面的协议，可再修改；会调用模型，可能产生少量费用。");
    options.append(probeResults);
    const enabled = field(options, "启用此接口", "enabled", "", {type: "checkbox"}); enabled.checked = api?.enabled !== false;
    const register = !api && canWrite ? field(options, "保存后继续接入与测试", "register_account", "", {type: "checkbox"}) : null;
    if (register) { register.checked = true; hint(register, "保存后自动提供所有协议兼容的运行器，创建 Resident 时均可选择。这里选择一个继续测试；测试会发起模型调用，可能产生费用。"); }
    const harness = register ? select(form, "Agent 运行器", "harness", []) : null;
    if (harness) hint(harness, "Claude Code 和 Codex 可通过 Portal 转换 OpenAI 与 Anthropic 协议，工具仍由所选运行器执行。");
    const renderHarnesses = () => {
      if (!harness) return;
      const protocols = new Set([primaryWire(), ...(chat.checked && !chat.parentElement.hidden ? ["openai_chat"] : []), ...(responses.checked && !responses.parentElement.hidden ? ["openai_responses"] : []),
        ...(anthropic.checked && !anthropic.parentElement.hidden ? ["anthropic_messages"] : [])]);
      const previous = harness.value || newProvider;
      harness.replaceChildren(node("option", t("直连模型（只对话，不执行工具）"), {value: "provider_api"}),
        ...state.providers.filter(p => p.api_protocol && p.kinds.includes("api_profile"))
          .map(p => {
            const supported = (p.api_upstream_protocols || [p.api_protocol]).some(protocol => protocols.has(protocol));
            const protocol = {openai_chat: "Chat Completions", openai_responses: "OpenAI Responses", anthropic_messages: "Anthropic Messages"}[p.api_protocol] || p.api_protocol;
            return node("option", (p.title || p.provider) + (supported ? "" : ` — ${t("需要协议")} ${protocol}`),
              {value: p.provider, disabled: !supported});
          }));
      const available = [...harness.options].some(option => option.value === previous && !option.disabled);
      if (!available && newProvider !== "provider_api") {
        harness.prepend(node("option", t("请确认接口协议，再选择运行器"), {value: ""}));
        harness.value = ""; harness.required = true;
      } else harness.value = available ? previous : "provider_api";
    };
    renderHarnesses();
    if (harness && [...harness.options].some(option => option.value === newProvider && !option.disabled)) harness.value = newProvider;
    responses.addEventListener("change", renderHarnesses);
    chat.addEventListener("change", renderHarnesses);
    let previousPrimary = primaryWire();
    primary.addEventListener("change", () => {
      const previous = {openai_chat: chat, openai_responses: responses, anthropic_messages: anthropic}[previousPrimary];
      if (previous) previous.checked = false;
      previousPrimary = primaryWire();
      if (!api && primary.value === "anthropic_messages") anthropicAuth.value = "x-api-key";
      showAnthropic(); renderHarnesses();
    });
    anthropic.addEventListener("change", () => { showAnthropic(); renderHarnesses(); });
    let createdId = "", provisionedAccounts = [], retryApiRoutes = false;
    saveButton(form); form.addEventListener("submit", event => {
      event.preventDefault(); if (busy) return;
      const apiId = id.value.trim() || generatedId;
      let catalog;
      try { catalog = models.read(); }
      catch (error) { form.querySelector('[role="alert"]').textContent = errorText(error); return; }
      const changes = {provider: provider.value.trim(), base_url: url.value.trim(), default_model: catalog.default_model, enabled: enabled.checked};
      if (!api || primary.value !== api.kind) changes.kind = primary.value;
      if (!api || JSON.stringify(catalog.models) !== declaredModels) changes.models = catalog.models;
      if (protocolState() !== declaredProtocols) {
        changes.protocols = {};
        if (chat.checked && !chat.parentElement.hidden) changes.protocols.openai_chat = {};
        if (responses.checked && !responses.parentElement.hidden) changes.protocols.openai_responses = {};
        if (anthropic.checked || primary.value === "anthropic_messages") changes.protocols.anthropic_messages = {auth: anthropicAuth.value,
          ...(anthropicUrl.value.trim() ? {base_url: anthropicUrl.value.trim()} : {})};
      }
      if (path.value.trim()) changes.secret_file = path.value.trim();
      if (secret.value) changes.secret = secret.value; secret.value = "";
      void run(async () => {
        try {
          if (!createdId || retryApiRoutes) {
            const saved = await execute(api || retryApiRoutes ? "UpdateModelApi" : "CreateModelApi", api || retryApiRoutes ? {api_id: api?.api_id || createdId, changes} : {api_id: apiId, ...changes});
            retryApiRoutes = false;
            provisionedAccounts = saved.accounts || [];
            if (!api) { createdId = apiId; id.value = apiId; }
          }
        }
        catch (error) {
          if (!api && error.details?.reason === "model_api.routes_incomplete") {
            createdId = error.details.api_id; id.value = createdId; id.disabled = true; retryApiRoutes = true;
          }
          throw error;
        }
        finally { delete changes.secret; }
        if (register?.checked) {
          const backend = harness?.value || "provider_api";
          const existing = provisionedAccounts.find(row => row.provider === backend || (backend === "provider_api" && row.account_id === createdId));
          if (!existing) {
            retryApiRoutes = true;
            throw Object.assign(new Error(), {details: {reason: "model_api.routes_incomplete"}});
          }
          view.close(); await Promise.all([refresh("apis"), refresh("accounts")]); setupEditor(existing.account_id); return;
        }
        view.close(); await Promise.all([refresh("apis"), refresh("accounts")]);
        if (setupAccountId) setupEditor(setupAccountId);
      }, Boolean(changes.secret), {success: register?.checked ? "接口已保存，请点击“测试并完成”启用 AI。" : "已保存"});
    });
  }
  function roleEditor(role = null) {
    const {view, form} = newEditor(role?.role === "default" ? "账号选择设置" : role ? "编辑 Backend 角色" : "新增 Backend 角色");
    form.classList.add("backendSelectionForm");
    const name = field(form, "角色", "role", role?.role || "", {required: true, readOnly: Boolean(role)});
    if (role?.role === "default") name.parentElement.hidden = true;
    const selection = select(form, "选择方式", "account_selection", [["auto", t("自动（按顺序和可用额度）")], ["fixed", t("手动固定账号")]], role?.account_selection || "auto");
    const types = new Map(state.accounts.filter(a => a.migration_type && a.provider !== "provider_api")
      .map(a => [a.migration_type.id, a.migration_type]));
    const defaults = [...types.values()].filter(a => a.id.split(":")[1] === "subscription_login")
      .sort((a, b) => a.default_priority - b.default_priority || a.id.localeCompare(b.id)).map(a => a.id);
    let order = role?.backend_priority?.length ? [...role.backend_priority] : defaults;
    const priority = node("fieldset", undefined, {class: "backendPriority", "data-backend-priority": ""});
    priority.append(node("legend", t("AI 后端优先级")), node("p", t("默认按从上到下的顺序选择。额度不足时，先尝试同类账号，再按此顺序切换；跨后端使用目标默认模型并保留上下文。"), {class: "backendHint"}));
    const list = node("ol", undefined, {class: "backendPriorityList"});
    const other = node("div", undefined, {class: "backendPriorityOther"});
    const reset = button("恢复推荐顺序", () => { order = [...defaults]; draw(); });
    priority.append(list, other, reset); form.append(priority);
    const label = key => types.get(key)?.label || key + t("（当前不可用）");
    const draw = () => {
      list.replaceChildren(); other.replaceChildren();
      for (const [i, key] of order.entries()) {
        const row = node("li", undefined, {"data-backend-priority-kind": key});
        const title = node("span", label(key), {class: "backendPriorityName"});
        const controls = node("div", undefined, {class: "backendPriorityControls"});
        for (const [direction, text] of [[-1, "上移"], [1, "下移"]]) {
          const move = button(direction < 0 ? "↑" : "↓", () => {
            const j = i + direction; [order[i], order[j]] = [order[j], order[i]]; draw();
            list.children[j]?.querySelector(`[data-direction="${direction}"]`)?.focus();
          }, i + direction < 0 || i + direction >= order.length);
          move.dataset.direction = String(direction); move.setAttribute("aria-label", t(text) + " " + label(key)); controls.append(move);
        }
        if (key.split(":")[1] !== "subscription_login") controls.append(button("移除", () => { order = order.filter(k => k !== key); draw(); }));
        row.append(node("span", String(i + 1), {class: "backendPriorityNumber", "aria-hidden": "true"}), title, controls); list.append(row);
      }
      if (!order.length) list.append(node("p", t("暂无可排序的订阅后端，可在下方加入其他后端。"), {class: "backendHint"}));
      const others = [...types.values()].filter(a => !order.includes(a.id));
      if (others.length) {
        other.append(node("p", t("其他后端仅在加入后参与选择和切换；API 用量将按对应账户计费。"), {class: "backendHint"}));
        for (const item of others) {
          const row = node("div", undefined, {class: "backendPriorityOptional"});
          const add = button("加入排序", () => { order.push(item.id); draw(); });
          add.setAttribute("aria-label", t("加入排序") + " " + item.label);
          row.append(node("span", item.label), add); other.append(row);
        }
      }
    };
    draw();
    const options = advanced(form);
    options.parentElement.open = true;
    const account = select(options, "模型偏好参考账号", "account_id", [["", t("选择账号")], ...state.accounts.filter(a => a.enabled !== false || a.account_id === role?.account_id).map(a => [a.account_id, a.account_id])], role?.account_id);
    account.required = true;
    const syncSelection = () => {
      priority.disabled = selection.value === "fixed";
      priority.dataset.disabled = String(priority.disabled);
      account.parentElement.querySelector("span").textContent = t(selection.value === "fixed" ? "固定账号" : "模型偏好参考账号");
    };
    selection.addEventListener("change", syncSelection); syncSelection();
    hint(account, "模型和推理强度仅用于此账号同类的后端；其他种类使用各自默认模型。固定模式不会自动切换。");
    const model = field(options, "模型偏好（可选）", "model", role?.model || "");
    const effort = field(options, "推理强度（可选）", "reasoning_effort", role?.reasoning_effort || "");
    saveButton(form); form.addEventListener("submit", event => { event.preventDefault(); void run(async () => {
      await execute("UpdateBackendRole", {backend_role: name.value.trim(), account_id: account.value, account_selection: selection.value,
        backend_priority: order, model: model.value.trim(), reasoning_effort: effort.value.trim()}, {expectedVersion: role?.version || 0});
      view.close(); await refresh("roles");
    }); });
  }
  const runnerTitle = provider => provider === "provider_api" ? t("直连模型（只对话，不执行工具）")
    : state.providers.find(item => item.provider === provider)?.title || provider;
  function accountRows(accounts, linked = false) {
    return accounts.map(a => {
      const identity = node("div", a.account_id);
      const readonly = !canWrite || a.kind === "service" || !Number.isInteger(a.registration_version);
      const reload = async () => { await refresh("providers"); await refresh("accounts"); };
      return {id: a.account_id, cells: [linked ? runnerTitle(a.provider) : identity,
        linked ? identity : [t(kinds[a.kind] || a.kind || "—"), runnerTitle(a.provider)].filter(Boolean).join(" · "),
        [t(a.enabled === false ? "已停用" : "已启用"), a.kind === "subscription_login" ? t(loginStates[a.login_state] || a.login_state || "—") : "",
          ""].filter(Boolean).join(" · "),
        actions(...(a.kind === "subscription_login" ? [button(a.login_state === "login_expired" ? "重新登录" : "登录", () => { login = openLogin(a.account_id, {onChanged: () => void refresh("accounts")}); }, !canWrite)] : []),
          button("设置与测试", () => setupEditor(a.account_id), !canWrite),
          ...(a.provider === "claude" && a.kind === "subscription_login" ? [button("编辑模型目录", () => claudeCatalogEditor(a), readonly)] : []),
          deleteControl("删除执行账号？", a.account_id, "DeleteProviderAccount", "account_id", reload, !canDeleteAccount || !Number.isInteger(a.registration_version),
            !Number.isInteger(a.registration_version) ? "Provider 内置身份没有可删除的账号登记。" : "当前身份没有删除权限。"))]};
    });
  }
  function renderAccounts() {
    const output = sections.accounts.output;
    const focusedAccount = output.contains(document.activeElement)
      ? document.activeElement.closest("[data-backend-account]")?.dataset.backendAccount : "";
    const expanded = new Set([...output.querySelectorAll("details[data-backend-api-accounts]")]
      .filter(details => details.open).map(details => details.dataset.backendApiAccounts));
    output.replaceChildren();
    if (rolesReady) {
      const role = state.roles.find(row => row.role === "default") || {role: "default", version: 0};
      const summary = node("div", undefined, {class: "backendSelectionSummary", "data-backend-account-selection": ""});
      summary.append(node("p", t("不同种类 AI 后端默认选择/自动切换的排序可手动调整，同种 AI 后端的选择由当前额度自动选择"), {class: "backendHint"}),
        button("账号选择设置", () => roleEditor(role), !canWrite));
      output.append(summary);
    }
    if (rolesReady && !state.roles.some(role => role.role === "default" && role.account_id)) {
      output.append(node("p", t("尚未配置默认模型偏好。请在账号的“设置与测试”中完成接入，或打开“账号选择设置”。"), {class: "backendHint", "data-backend-default-missing": "", role: "status"}));
    }
    if (!state.accounts.length) { output.append(node("p", t("暂无登记"))); return; }
    const standalone = [], groups = new Map();
    for (const account of state.accounts) {
      const apiId = account.kind === "api_profile" && account.config?.api_id;
      if (!apiId) { standalone.push(account); continue; }
      if (!groups.has(apiId)) groups.set(apiId, []);
      groups.get(apiId).push(account);
    }
    if (standalone.length) output.append(buildTable(["名称", "接入方式", "状态", "操作"], accountRows(standalone)));
    for (const [apiId, accounts] of groups) {
      const api = state.apis.find(item => item.api_id === apiId);
      const connection = node("article", undefined, {class: "backendApiConnection", "data-backend-api-connection": apiId});
      const header = node("header"), identity = node("div", undefined, {class: "backendApiIdentity"});
      identity.append(node("h3", api?.default_model || apiId));
      if (api?.base_url) identity.append(node("p", api.base_url, {class: "backendHint"}));
      identity.append(node("small", t("连接名称：{p0}", {p0: apiId})));
      const otherModels = (api?.models || []).filter(model => model.enabled !== false && model.id !== api.default_model).map(model => model.id);
      if (otherModels.length) identity.append(node("small", t("其他模型：{p0}", {p0: otherModels.join("、")})));
      const controls = actions();
      if (api) controls.append(node("span", "API Key · " + t(api.enabled === false ? "已停用" : "已启用"), {class: "backendHint"}),
        button("编辑连接", () => apiEditor(api), !canWrite));
      header.append(identity, controls); connection.append(header);
      connection.append(node("p", t("运行器：{p0}", {p0: [...new Set(accounts.map(account => runnerTitle(account.provider)))].join("、")}), {class: "backendHint"}));
      const details = node("details", undefined, {"data-backend-api-accounts": apiId, open: expanded.has(apiId)});
      details.append(node("summary", t("查看执行账号（{p0}）", {p0: accounts.length})),
        node("p", t("同一 API 可通过不同运行器使用；每个执行账号的状态可分别管理，账号选择由统一策略决定。"), {class: "backendHint"}));
      const scroll = node("div", undefined, {class: "backendTableScroll"});
      scroll.append(buildTable(["Agent 运行器", "执行账号", "状态", "操作"], accountRows(accounts, true)));
      details.append(scroll); connection.append(details); output.append(connection);
    }
    if (focusedAccount && state.accounts.some(account => account.account_id === focusedAccount)) focusAccount(focusedAccount);
  }
  const renderers = {
    providers: () => table("providers", ["Provider", "账号种类", "已安装版本", "可用版本", "待切换"], state.providers.map(p => ({cells: [p.title || p.provider,
      p.kinds.map(k => t(kinds[k] || k)).join(" / "), p.runtime?.installed_version || "—", p.runtime?.available_version || "—", t(p.runtime?.pending_switch ? "是" : "否")]}))),
    accounts: renderAccounts,
    apis: () => table("apis", ["连接名称", "服务商", "接口地址", "默认模型", "状态", "操作"], state.apis.map(api => {
      const result = node("span", "", {role: "status"});
      const test = button("测试连接", async () => {
        test.disabled = true; result.textContent = t("正在测试…");
        try { await execute("CompleteChat", {api_id: api.api_id, model: api.default_model, messages: [{role: "user", content: "Reply OK."}], options: {max_tokens: 8}}); result.textContent = t("连接成功"); }
        catch (_) { result.textContent = t("连接失败，请检查模型与凭据配置。"); }
        finally { test.disabled = false; }
      }, !canWrite || api.enabled === false);
      return {cells: [api.api_id, api.provider, api.base_url, api.default_model, t(api.enabled === false ? "已停用" : "已启用"), actions(
        button("编辑", () => apiEditor(api), !canWrite), test, result,
        deleteControl("删除模型 API？", api.api_id, "DeleteModelApi", "api_id", () => refresh("apis"), !canDeleteApi))]};
    })),
    roles: () => table("roles", ["角色", "账号选择", "参考账号", "模型", "推理强度", "版本", "操作"],
      (state.roles.some(r => r.role === "default") ? state.roles : [{role: "default", version: 0}, ...state.roles]).map(role => ({cells: [role.role, t(role.account_selection === "fixed" ? "手动固定" : "自动（按可用额度）"), role.account_id || t("尚未配置"), role.model || "—", role.reasoning_effort || "—", role.version,
        button("编辑", () => roleEditor(role), !canWrite)]}))),
  };
  const instructions = {providers: "ListProviders", accounts: "ListProviderAccounts", apis: "ListModelApis", roles: "ListBackendRoles"};
  async function refresh(key, args = {}) {
    try {
      const result = await execute(instructions[key], args); if (destroyed) return;
      state[key] = key === "accounts" ? (result.accounts || []).map(account => account.registration ? {
        ...account, ...account.registration, registration_version: account.registration.version,
      } : account) : result[key] || [];
      if (key === "providers") providersReady = true;
      if (key === "roles") rolesReady = true;
      renderers[key]();
      if (key === "apis" || key === "providers" || key === "roles") renderers.accounts();
      if (key === "accounts" && pendingAccountId) focusAccount(pendingAccountId);
    } catch (error) { if (!destroyed) sections[key].output.replaceChildren(node("p", errorText(error), {role: "alert"})); }
    finally { if (!destroyed) syncAccountButton(); }
  }
  management.prepend(addAccount);
  const checkUpdates = button("检查更新", async () => {
    checkUpdates.disabled = true;
    try { await refresh("providers", {refresh: true}); }
    finally { if (!destroyed) checkUpdates.disabled = !canWrite; }
  }, !canWrite);
  sections.providers.actions.append(checkUpdates);
  sections.apis.actions.append(button("新增模型 API", () => apiEditor(), !canWrite));
  sections.roles.actions.append(button("新增 Backend 角色", () => roleEditor(), !canWrite));
  for (const key of Object.keys(sections)) sections[key].actions.append(button("刷新", () => void refresh(key)));
  // Opening registration needs the catalog, not runtime probes or account health.
  const catalogReady = refresh("providers", {include_runtime: false});
  for (const key of ["accounts", "apis", "roles"]) void refresh(key);
  await catalogReady;
  if (!destroyed) void refresh("providers");
  function focusAccount(id) {
    if (!id) return;
    pendingAccountId = id;
    const row = [...root.querySelectorAll("[data-backend-account]")].find(el => el.dataset.backendAccount === id);
    if (row) {
      pendingAccountId = "";
      const details = row.closest("details"); if (details) details.open = true;
      row.scrollIntoView({block: "center"}); row.focus({preventScroll: true});
    }
  }
  focusAccount(accountId);
  return {focusAccount, openAccountEditor: accountEditor, isBusy: () => busy, hasUnsavedChanges: () => Boolean(editor),
    destroy() { destroyed = true; abort.abort(); editor?.close(); login?.close(); root.replaceChildren(); }};
}
