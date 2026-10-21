// AI Backend registration and login, shared by Management and Home.
const t = (key, values) => window.PortalI18n.t(key, values);
const call = (name, args = {}, options = {}) => window.PortalInstructionClient.execute(name, args, options);
const kinds = {subscription_login: "订阅登录", api_profile: "模型 API 账号", service: "服务账号"};
const loginStates = {logged_in: "已登录", login_expired: "登录已过期", logged_out: "未登录", not_applicable: "无需登录",
  unavailable: "等待准备环境或检查授权",
  pending: "正在启动登录…", awaiting_user: "等待浏览器授权", succeeded: "登录成功", failed: "登录失败", expired: "登录已过期"};
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
function loadStyle() {
  if (!document.querySelector("[data-backend-style]")) document.head.append(node("link", undefined,
    {rel: "stylesheet", href: "/static/portal_management_backend.css", "data-backend-style": ""}));
}
function errorText(error) {
  if (error.details?.reason === "account.busy") return t("账号仍绑定 {p0} 个会话、{p1} 个执行；请先迁移会话。", {
    p0: error.details?.session_count ?? "—", p1: error.details?.execution_count ?? "—"});
  return error.message || t("操作失败，请重试。");
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

export function openLogin(accountId, {onChanged = () => {}} = {}) {
  if (logins.has(accountId)) return logins.get(accountId);
  let timer, closed = false, terminal = false;
  const abort = new AbortController();
  const view = dialog(t("账号登录：{p0}", {p0: accountId}), () => {
    closed = true; clearTimeout(timer); abort.abort(); logins.delete(accountId);
  });
  const status = node("p", t("正在启动登录…"), {role: "status", "data-backend-login-state": ""});
  const code = node("code", "", {"data-backend-login-code": ""});
  const link = node("a", t("打开授权页面"), {target: "_blank", rel: "noopener noreferrer", hidden: true});
  const authorization = node("form", undefined, {class: "backendForm", hidden: true});
  const response = field(authorization, "浏览器返回的授权码", "authorization_code", "", {type: "password", required: true, autocomplete: "off"});
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
    cancel.disabled = true; clearTimeout(timer);
    try {
      await call("CancelProviderAccountLogin", {account_id: accountId});
      if (!closed) { terminal = true; status.textContent = t("登录已取消"); code.textContent = ""; link.hidden = true; authorization.hidden = true; response.value = ""; }
    } catch (error) { if (!closed) { status.textContent = errorText(error); cancel.disabled = false; } }
  }, true);
  view.el.append(status, code, link, authorization, cancel); view.open(); logins.set(accountId, view);
  const render = result => {
    if (closed || terminal) return;
    const session = result.session || {};
    status.textContent = t(loginStates[session.state] || session.state || "正在启动登录…");
    code.textContent = session.verification_code || "";
    link.hidden = true; link.removeAttribute("href");
    try {
      const url = new URL(session.verification_url);
      if (["https:", "http:"].includes(url.protocol)) { link.href = url.href; link.hidden = false; }
    } catch (_) { /* No authorization URL yet. */ }
    terminal = ["succeeded", "failed", "expired"].includes(session.state);
    cancel.disabled = terminal;
    authorization.hidden = terminal || !session.requires_code_input;
    submit.disabled = session.state !== "awaiting_user";
    if (session.state === "succeeded") { code.textContent = ""; link.hidden = true; onChanged(); }
    if (!terminal) timer = setTimeout(poll, 2000);
  };
  const poll = async () => {
    try { render(await call("GetProviderAccountLogin", {account_id: accountId}, {signal: abort.signal})); }
    catch (error) { if (!closed && !terminal) { status.textContent = errorText(error); timer = setTimeout(poll, 4000); } }
  };
  void call("OpenProviderAccountLogin", {account_id: accountId}, {signal: abort.signal}).then(render).catch(error => {
    if (!closed) status.textContent = errorText(error);
  });
  return view;
}

export async function mount(root, {me = {}, accountId = ""} = {}) {
  loadStyle(); root.classList.add("backendManagement"); root.replaceChildren();
  let destroyed = false, busy = false, editor = null, login = null;
  const abort = new AbortController();
  const canWrite = me.access?.permission_system === "open" || me.access?.allows?.UpdateProviderAccount === true;
  const canRegister = me.access?.permission_system === "open" || me.access?.allows?.RegisterProviderAccount === true;
  const execute = (name, args = {}, options = {}) => call(name, args, {...options, signal: abort.signal});
  const state = {providers: [], accounts: [], apis: [], roles: []};
  let providersReady = false;
  const addAccount = button("新增执行账号", () => accountEditor(), true);
  const addSubscription = button("接入订阅账号", () => accountEditor("subscription_login"), true);
  const addApi = button("新增模型 API", () => apiEditor(), !canWrite);
  function syncAccountButton() {
    addAccount.disabled = !canRegister || busy || !providersReady || !state.providers.length;
    addSubscription.disabled = !canRegister || busy || !providersReady || !state.providers.some(p => p.kinds.includes("subscription_login"));
  }
  const notice = node("p", "", {role: "status", "data-backend-notice": ""}); root.append(notice);
  const introduction = node("section", undefined, {class: "backendIntroduction"});
  introduction.append(node("h2", t("接入你的 AI")), node("p", t("先选择接入方式。这里连接你已有的账号或接口；订阅开通和 API Key 申请请在服务商处完成。")));
  const choices = node("div", undefined, {class: "backendChoices"});
  for (const [title, description, action] of [
    ["订阅账号登录", "已有支持的 AI 编程订阅？给账号起名，选择后端，再通过浏览器登录授权，无需填写 API Key。", addSubscription],
    ["使用模型 API", "已有 API Key？填写接口地址、模型名称和密钥，保存后继续测试；也可只保存接口。", addApi],
  ]) {
    const card = node("div", undefined, {class: "backendChoice"});
    card.append(node("h3", t(title)), node("p", t(description)), action); choices.append(card);
  }
  introduction.append(choices); root.append(introduction);
  const sections = {};
  for (const [key, title] of [["providers", "Provider"], ["accounts", "执行账号"], ["apis", "模型 API"], ["roles", "Backend 角色"]]) {
    const section = node("section", undefined, {class: "settingsSection", "data-backend-section": key, "aria-label": t(title)});
    const header = node("header"), actions = node("div", undefined, {class: "backendActions"});
    header.append(node("h2", t(title)), actions);
    const output = node("div", undefined, {class: "backendTableScroll"});
    section.append(header, output); root.append(section); sections[key] = {output, actions};
  }
  const run = async (work, secret = false) => {
    if (busy || destroyed) return;
    busy = true; notice.textContent = t("正在处理…");
    const buttons = [...root.querySelectorAll("button"), ...(editor ? editor.el.querySelectorAll("button") : [])];
    const disabled = buttons.map(el => el.disabled); buttons.forEach(el => el.disabled = true);
    try { const completed = await work(); if (!destroyed && completed !== false) notice.textContent = t("已保存"); }
    catch (error) { if (!destroyed) {
      const message = secret ? t("密钥提交失败，请检查配置后重新输入。") : errorText(error);
      notice.textContent = message;
      const alert = editor?.el.querySelector('[role="alert"]'); if (alert) alert.textContent = message;
    } }
    finally { busy = false; buttons.forEach((el, i) => { if (el.isConnected) el.disabled = disabled[i]; }); syncAccountButton(); }
  };
  const table = (key, headers, rows) => {
    const output = sections[key].output; output.replaceChildren();
    if (!rows.length) { output.append(node("p", t("暂无登记"))); return; }
    const grid = node("table"), head = node("tr"), body = node("tbody");
    for (const title of headers) head.append(node("th", t(title), {scope: "col"}));
    const thead = node("thead"); thead.append(head); grid.append(thead, body);
    for (const {cells, id} of rows) {
      const row = node("tr", undefined, id ? {"data-backend-account": id, tabindex: "-1"} : {});
      for (const value of cells) { const cell = node("td"); cell.append(value instanceof Node ? value : document.createTextNode(String(value ?? "—"))); row.append(cell); }
      body.append(row);
    }
    output.append(grid);
  };
  const actions = (...buttons) => { const el = node("div", undefined, {class: "backendActions"}); el.append(...buttons); return el; };
  const confirm = (title, id) => window.PortalUI.confirm({title: t(title), message: id, confirmLabel: t("删除"), tone: "danger"});
  function newEditor(title, onClose = () => {}) {
    editor?.close();
    const view = dialog(t(title), () => { onClose(); view.el.querySelectorAll('[type="password"]').forEach(el => el.value = ""); if (editor === view) editor = null; });
    editor = view;
    const form = node("form", undefined, {class: "backendForm"});
    form.append(node("p", "", {role: "alert"})); view.el.append(form); view.open(); return {view, form};
  }
  const saveButton = form => form.append(node("button", t("保存"), {type: "submit", class: "portalButton is-primary"}));
  function setupEditor(accountId) {
    let timer, closed = false, checking = false, working = false, setup = null;
    const {view, form} = newEditor("接入并启用 AI", () => { closed = true; clearTimeout(timer); });
    form.append(node("p", accountId));
    const status = node("p", "", {role: "status", "data-backend-setup-status": ""});
    const capabilities = node("p", "");
    const model = field(form, "模型", "model", "", {required: true, autocomplete: "off"});
    const choices = node("datalist", undefined, {id: "backend-setup-models"});
    model.setAttribute("list", choices.id); form.append(choices);
    const useDefault = field(form, "用作默认 AI", "use_default", "", {type: "checkbox"});
    const defaultRole = state.roles.find(role => role.role === "default");
    useDefault.checked = !defaultRole?.account_id;
    const prepare = button("准备运行环境", () => void perform(async () => {
      const result = await execute("PrepareProviderRuntime", {provider: setup.provider});
      status.textContent = t(result.preparation.message || (result.preparation.status === "ready" ? "运行环境已就绪" : "正在准备运行环境…"));
      await reload();
    }), true);
    const authorize = button("登录", () => {
      login = openLogin(accountId, {onChanged: () => { login?.close(); void reload(); }});
    }, true);
    const retry = button("刷新", () => void perform(reload));
    const test = button("测试并完成", () => void perform(async () => {
      if (!model.value.trim()) { model.reportValidity(); return; }
      checking = true; model.disabled = true; useDefault.disabled = true; setButtons();
      try {
        const receipt = await execute("CheckProviderAccount", {account_id: accountId, model: model.value.trim()});
        await pollCheck(receipt.execution_id);
      } catch (error) { checking = false; model.disabled = false; useDefault.disabled = false; throw error; }
    }), true);
    form.append(capabilities, status, actions(prepare, authorize, retry, test));
    form.addEventListener("submit", event => event.preventDefault());
    function setButtons() {
      prepare.disabled = !canWrite || !setup || working || checking;
      authorize.hidden = !setup || setup.login_state === "not_applicable";
      authorize.disabled = !canWrite || !setup?.runtime_ready || working || checking;
      test.disabled = !canWrite || !setup?.enabled || !setup?.runtime_ready || working || checking;
      retry.disabled = working || checking;
    }
    async function perform(work) {
      if (closed || working) return;
      working = true; setButtons();
      try { await work(); }
      catch (error) { if (!closed) status.textContent = errorText(error); }
      finally { working = false; if (!closed) setButtons(); }
    }
    async function reload() {
      const result = await execute("GetProviderAccountSetup", {account_id: accountId});
      if (closed) return;
      setup = result.setup;
      choices.replaceChildren(...(setup.models || []).map(item => node("option", undefined,
        {value: typeof item === "string" ? item : item.id || item.model || ""})));
      if (!model.value) model.value = setup.default_model || "";
      capabilities.textContent = t(setup.supports_tools ? "支持对话与工具任务；本次测试验证模型回复。" : "支持对话；此接入方式不提供工具执行。");
      status.textContent = [t(setup.runtime_ready ? "运行环境已就绪" : "需要准备运行环境"),
        t(loginStates[setup.login_state] || setup.login_state), t(setup.preparation?.message || ""), t(setup.catalog_error || "")].filter(Boolean).join(" · ");
      clearTimeout(timer);
      if (!setup.runtime_ready && ["preparing", "pending"].includes(setup.preparation?.status)) timer = setTimeout(() => void perform(reload), 2000);
      setButtons();
    }
    async function pollCheck(executionId) {
      try {
        const result = await execute("GetProviderAccountCheck", {account_id: accountId, execution_id: executionId});
        if (closed) return;
        status.textContent = t(result.check.message);
        if (!["completed", "failed", "cancelled"].includes(result.check.status)) {
          timer = setTimeout(() => void pollCheck(executionId), 2000); return;
        }
        if (result.check.passed && useDefault.checked) {
          const currentDefault = state.roles.find(role => role.role === "default");
          await execute("UpdateBackendRole", {backend_role: "default", account_id: accountId,
            model: result.check.model, verification_execution_id: executionId}, {expectedVersion: currentDefault?.version || 0});
          if (!closed) status.textContent = t("测试通过，已设为默认 AI；新对话和未配置的 Resident 将使用此账号。");
          await refresh("roles");
        }
        checking = false; model.disabled = false; useDefault.disabled = false; setButtons();
      } catch (error) {
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
    const {view, form} = newEditor("新增执行账号");
    form.append(node("p", t("1. 给账号起名并选择后端 → 2. 准备环境、登录授权 → 3. 选择模型并测试。"), {class: "backendHint"}));
    const id = field(form, "账号名称", "account_id", "", {required: true, pattern: "[a-z][a-z0-9_\\-]{0,127}", autocomplete: "off", placeholder: "my-ai"});
    hint(id, "这是你在 Portal 中使用的唯一名称，不是服务商的登录邮箱。以小写字母开头，只用小写字母、数字、下划线或短横线，最多 128 字符。");
    const providers = preferredKind ? state.providers.filter(p => p.kinds.includes(preferredKind)) : state.providers;
    const provider = select(form, "AI 后端", "provider", providers.map(p => [p.provider, p.title || p.provider]));
    const kind = select(form, "接入方式", "kind", []);
    const extra = node("div", undefined, {class: "backendForm"}); form.append(extra);
    const renderFields = () => {
      extra.replaceChildren();
      if (kind.value === "subscription_login") extra.append(node("p", t("保存后按页面提示打开浏览器完成授权。不要在这里填写订阅密码或 API Key。"), {class: "backendHint"}));
      if (kind.value === "service") extra.append(node("p", t("复用 Provider 已有的服务配置和授权。")));
      form.querySelector('[type="submit"]').disabled = false;
      const options = advanced(extra);
      if (kind.value === "subscription_login" && provider.value === "codex") {
        field(options, "备用账号（逗号分隔）", "fallback_account_ids"); field(options, "默认路线", "is_default", "", {type: "checkbox"});
      } else if (kind.value === "subscription_login" && provider.value === "claude") {
        field(options, "模型目录（JSON 数组）", "models", "[]", {tag: "textarea", rows: "3"});
      } else if (kind.value === "api_profile") {
        const api = select(extra, "模型 API", "api_id", [["", t("选择已登记的模型 API")], ...state.apis.map(api => [api.api_id, api.api_id])]);
        api.required = true;
        hint(api, "选择已保存的接口。还没有接口？关闭此窗口，使用页面顶部的“新增模型 API”。");
        field(options, "路线（可选）", "route_id");
        const secret = field(options, "更换 API Key（可选）", "secret", "", {type: "password", autocomplete: "new-password"});
        hint(secret, "留空沿用已保存的密钥。填写后替换所选接口的 API Key，提交后不回显。");
      }
      field(options, "初始并发上限", "max_concurrency", "1", {type: "number", min: "1", max: "256", step: "1", required: true});
      extra.append(options.parentElement);
    };
    const renderKinds = () => {
      kind.replaceChildren(...(state.providers.find(p => p.provider === provider.value)?.kinds || []).map(value => node("option", t(kinds[value] || value), {value})));
      if (preferredKind) kind.value = preferredKind;
      renderFields();
    };
    saveButton(form); provider.addEventListener("change", renderKinds); kind.addEventListener("change", renderFields); renderKinds();
    form.addEventListener("submit", event => {
      event.preventDefault(); if (busy) return;
      const value = name => form.elements.namedItem(name)?.value || "";
      const config = {};
      if (kind.value === "subscription_login" && provider.value === "codex") {
        config.fallback_account_ids = value("fallback_account_ids").split(",").map(v => v.trim()).filter(Boolean);
        config.is_default = form.elements.is_default.checked;
      }
      if (kind.value === "subscription_login" && provider.value === "claude") {
        try { config.models = JSON.parse(value("models")); if (!Array.isArray(config.models)) throw new Error(); }
        catch (_) { form.querySelector('[role="alert"]').textContent = t("模型目录必须是 JSON 数组。"); return; }
      }
      if (kind.value === "api_profile") config.api_id = value("api_id");
      const args = {account_id: id.value.trim(), provider: provider.value, kind: kind.value, config, max_concurrency: Number(value("max_concurrency"))};
      if (value("route_id")) args.route_id = value("route_id").trim();
      const secret = form.elements.namedItem("secret");
      if (secret?.value) args.secret = secret.value;
      if (secret) secret.value = "";
      void run(async () => {
        try { await execute("RegisterProviderAccount", args); } finally { delete args.secret; }
        view.close(); await refresh("accounts"); setupEditor(args.account_id);
      }, Boolean(args.secret));
    });
  }
  function concurrencyEditor(account) {
    const {view, form} = newEditor("编辑初始并发");
    const value = field(form, "初始并发上限", "max_concurrency", account.initial_max_concurrency ?? account.max_concurrency, {type: "number", min: "1", max: "256", step: "1", required: true});
    saveButton(form); form.addEventListener("submit", event => { event.preventDefault(); void run(async () => {
      await execute("UpdateProviderAccountRegistration", {account_id: account.account_id, changes: {max_concurrency: Number(value.value)}}, {expectedVersion: account.registration_version});
      view.close(); await refresh("accounts");
    }); });
  }
  function apiEditor(api = null) {
    const {view, form} = newEditor(api ? "编辑模型 API" : "新增模型 API");
    form.append(node("p", t(api ? "修改连接信息。API Key 留空时保留当前密钥。" : "准备好服务商提供的 API Key、Base URL 和模型名称。新增接口使用 OpenAI 兼容的 Chat Completions 协议。"), {class: "backendHint"}));
    const id = field(form, "连接名称", "api_id", api?.api_id || "", {required: true, readOnly: Boolean(api), placeholder: "my-model-api", ...(!api ? {pattern: "[a-z][a-z0-9_\\-]{0,127}"} : {})});
    hint(id, "自己起一个唯一名称，保存后不可改名。以小写字母开头，只用小写字母、数字、下划线或短横线，最多 128 字符。");
    const url = field(form, "接口基础地址（Base URL）", "base_url", api?.base_url || "", {type: "url", required: true, placeholder: "https://api.example.com/v1"});
    hint(url, "复制服务商提供的 API 基础地址，不是官网或聊天网页；不要附加 /chat/completions。");
    const model = field(form, "模型名称", "default_model", api?.default_model || "", {required: true, placeholder: "example-model"});
    hint(model, "填写服务商文档中的模型标识，必须与接口支持的名称完全一致。");
    const secret = field(form, "API Key（密钥）", "secret", "", {type: "password", autocomplete: "new-password"});
    hint(secret, api ? "留空保留当前密钥；填写则替换。密钥不会回显，也不是一次性验证码。" : "粘贴服务商生成的 API Key。Portal 会保存供后续调用使用，提交后不回显；这不是一次性验证码。无需密钥的接口可留空。");
    const options = advanced(form);
    const provider = field(options, "服务商名称（可选）", "provider", api?.provider || "", {placeholder: "example-provider"});
    hint(provider, "仅用于标记接口来源，不是登录账号；填写服务商名称即可。");
    const path = field(options, "服务器上的密钥文件路径（可选）", "secret_file", api?.secret_file || "");
    hint(path, "仅在密钥已由管理员放在 Portal 服务器上时填写绝对路径；不是你电脑上的文件。已填写 API Key 时无需填写。");
    const enabled = field(options, "启用此接口", "enabled", "", {type: "checkbox"}); enabled.checked = api?.enabled !== false;
    const canCreateAccount = !api && canRegister && state.providers.some(p => p.provider === "provider_api" && p.kinds.includes("api_profile"));
    const account = canCreateAccount ? field(options, "AI 账号名称（可选）", "account_id", "", {pattern: "[a-z][a-z0-9_\\-]{0,127}", placeholder: t("留空使用连接名称")}) : null;
    const register = canCreateAccount ? field(form, "同时添加为 AI 账号，继续接入与测试", "register_account", "", {type: "checkbox"}) : null;
    if (register) { register.checked = true; hint(register, "用于 Resident 对话。保存后选择模型并测试，可设为默认 AI；直连模型 API 不提供工具执行。测试会发起模型调用，可能产生费用。"); }
    let createdId = "";
    saveButton(form); form.addEventListener("submit", event => {
      event.preventDefault(); if (busy) return;
      const changes = {provider: provider.value.trim(), base_url: url.value.trim(), default_model: model.value.trim(), enabled: enabled.checked};
      if (path.value.trim()) changes.secret_file = path.value.trim();
      if (secret.value) changes.secret = secret.value; secret.value = "";
      void run(async () => {
        try {
          if (!createdId) {
            await execute(api ? "UpdateModelApi" : "CreateModelApi", api ? {api_id: api.api_id, changes} : {api_id: id.value.trim(), ...changes});
            if (!api) createdId = id.value.trim();
          }
        }
        finally { delete changes.secret; }
        if (register?.checked) {
          const accountId = account.value.trim() || createdId;
          try {
            await execute("RegisterProviderAccount", {account_id: accountId, provider: "provider_api", kind: "api_profile", config: {api_id: createdId}, max_concurrency: 1});
          } catch (_) {
            for (const input of [id, provider, url, model, path, secret, enabled]) input.disabled = true;
            const message = t("API 已保存，但 AI 账号尚未添加。可修改账号名称后重试保存，不会重复创建 API；也可取消勾选，仅保留接口。");
            form.querySelector('[role="alert"]').textContent = message; notice.textContent = message;
            await refresh("apis"); return false;
          }
          view.close(); await Promise.all([refresh("apis"), refresh("accounts")]); setupEditor(accountId); return;
        }
        view.close(); await refresh("apis");
      }, Boolean(changes.secret));
    });
  }
  function roleEditor(role = null) {
    const {view, form} = newEditor(role ? "编辑 Backend 角色" : "新增 Backend 角色");
    const name = field(form, "角色", "role", role?.role || "", {required: true, readOnly: Boolean(role)});
    const account = select(form, "执行账号", "account_id", [["", t("选择账号")], ...state.accounts.filter(a => a.enabled !== false || a.account_id === role?.account_id).map(a => [a.account_id, a.account_id])], role?.account_id);
    account.required = true;
    const model = field(form, "模型", "model", role?.model || "");
    const effort = field(form, "推理强度", "reasoning_effort", role?.reasoning_effort || "");
    saveButton(form); form.addEventListener("submit", event => { event.preventDefault(); void run(async () => {
      await execute("UpdateBackendRole", {backend_role: name.value.trim(), account_id: account.value, model: model.value.trim(), reasoning_effort: effort.value.trim()}, {expectedVersion: role?.version || 0});
      view.close(); await refresh("roles");
    }); });
  }
  const renderers = {
    providers: () => table("providers", ["Provider", "账号种类", "已安装版本", "可用版本", "待切换"], state.providers.map(p => ({cells: [p.title || p.provider,
      p.kinds.map(k => t(kinds[k] || k)).join(" / "), p.runtime?.installed_version || "—", p.runtime?.available_version || "—", t(p.runtime?.pending_switch ? "是" : "否")]}))),
    accounts: () => table("accounts", ["执行账号", "Provider", "账号种类", "路线", "状态", "登录状态", "初始并发上限", "版本", "操作"], state.accounts.map(a => {
      const identity = node("div", a.account_id);
      for (const path of [a.home_path, a.socket_path].filter(Boolean)) identity.append(node("small", path));
      const readonly = !canWrite || a.kind === "service" || !Number.isInteger(a.registration_version);
      return {id: a.account_id, cells: [identity, a.provider, t(kinds[a.kind] || a.kind || "—"), a.route_id || a.backend_route_id || "—", t(a.enabled === false ? "已停用" : "已启用"),
        t(loginStates[a.login_state] || a.login_state || "—"), a.initial_max_concurrency ?? a.max_concurrency, a.registration_version,
        actions(button("接入与测试", () => setupEditor(a.account_id), !canWrite), ...(a.kind === "subscription_login" ? [button(a.login_state === "login_expired" ? "重新登录" : "登录", () => { login = openLogin(a.account_id, {onChanged: () => void refresh("accounts")}); }, !canWrite)] : []),
          button(a.enabled === false ? "启用" : "停用", () => void run(async () => { await execute("UpdateProviderAccountRegistration", {account_id: a.account_id, changes: {enabled: a.enabled === false}}, {expectedVersion: a.registration_version}); await refresh("accounts"); }), readonly),
          button("编辑初始并发", () => concurrencyEditor(a), readonly),
          button("删除", () => void run(async () => { if (await confirm("删除执行账号？", a.account_id)) { await execute("DeleteProviderAccount", {account_id: a.account_id}); await refresh("accounts"); } }), readonly))]};
    })),
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
        button("删除", () => void run(async () => { if (await confirm("删除模型 API？", api.api_id)) { await execute("DeleteModelApi", {api_id: api.api_id}); await refresh("apis"); } }), !canWrite))]};
    })),
    roles: () => table("roles", ["角色", "执行账号", "模型", "推理强度", "版本", "操作"],
      (state.roles.some(r => r.role === "default") ? state.roles : [{role: "default", version: 0}, ...state.roles]).map(role => ({cells: [role.role, role.account_id || t("必须设置默认账号"), role.model || "—", role.reasoning_effort || "—", role.version,
        button("编辑", () => roleEditor(role), !canWrite)]}))),
  };
  const instructions = {providers: "ListProviders", accounts: "ListProviderAccounts", apis: "ListModelApis", roles: "ListBackendRoles"};
  async function refresh(key, args = {}) {
    if (key === "providers") { providersReady = false; syncAccountButton(); }
    try {
      const result = await execute(instructions[key], args); if (destroyed) return;
      state[key] = key === "accounts" ? (result.accounts || []).map(account => account.registration ? {
        ...account, ...account.registration, registration_version: account.registration.version,
        initial_max_concurrency: account.registration.max_concurrency,
      } : account) : result[key] || [];
      if (key === "providers") providersReady = true;
      renderers[key]();
    } catch (error) { if (!destroyed) sections[key].output.replaceChildren(node("p", errorText(error), {role: "alert"})); }
    finally { if (!destroyed) syncAccountButton(); }
  }
  sections.accounts.actions.append(addAccount);
  const checkUpdates = button("检查更新", async () => {
    checkUpdates.disabled = true;
    try { await refresh("providers", {refresh: true}); }
    finally { if (!destroyed) checkUpdates.disabled = !canWrite; }
  }, !canWrite);
  sections.providers.actions.append(checkUpdates);
  sections.apis.actions.append(button("新增模型 API", () => apiEditor(), !canWrite));
  sections.roles.actions.append(button("新增 Backend 角色", () => roleEditor(), !canWrite));
  for (const key of Object.keys(sections)) sections[key].actions.append(button("刷新", () => void refresh(key)));
  await Promise.all(Object.keys(sections).map(refresh));
  function focusAccount(id) {
    if (!id) return;
    const row = [...root.querySelectorAll("[data-backend-account]")].find(el => el.dataset.backendAccount === id);
    if (row) { row.scrollIntoView({block: "center"}); row.focus({preventScroll: true}); }
  }
  focusAccount(accountId);
  return {focusAccount, openAccountEditor: accountEditor, isBusy: () => busy, hasUnsavedChanges: () => Boolean(editor),
    destroy() { destroyed = true; abort.abort(); editor?.close(); login?.close(); root.replaceChildren(); }};
}
