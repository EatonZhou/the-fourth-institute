import "./styles.css";
import { loadProgress, REFERENCES, readText, readMedia } from "./data.js";
import {
  filterItems,
  dateRangeError,
  moduleScope,
  browsingModule,
} from "./model.js";
import { observeView } from "./view-state.js";
import { renderMarkdown } from "../shared/markdown.js";
const stamp = (value) =>
  new Date(value).toLocaleString("zh-HK", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Asia/Hong_Kong",
  });
const array = (x) => (Array.isArray(x) ? x : []);
const el = (tag, text, cls) => {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = text;
  if (cls) n.className = cls;
  return n;
};
const btn = (text, fn, disabled = false) => {
  const b = el("button", text, "portalButton");
  b.type = "button";
  b.disabled = disabled;
  b.onclick = fn;
  return b;
};
export async function mount(root, context) {
  const { sdk, resources, signal } = context;
  let snapshot = { items: [], reports: [], modules: [], failures: [] },
    filters = { start_date: "", end_date: "", module_filter: "" };
  let visible = true,
    destroyed = false,
    loading = false,
    sending = false,
    generation = 0,
    focusRevision = -1,
    watchEpoch = 0;
  let selected = "",
    feedbackKey = "",
    query = "",
    notice = "",
    watchKey = "",
    stopWatch,
    hiddenView,
    navigationFocus;
  const drafts = new Map(),
    sources = new Map(),
    media = new Map(),
    messages = new Map(),
    steps = new Map(),
    simulations = new Map();
  const alive = () => !destroyed && !signal?.aborted;
  const view = observeView(
    root,
    () => alive() && visible,
    (s) => {
      if (hiddenView) hiddenView = s;
    },
  );
  const guard = (fn) => async () => {
    try {
      await fn();
    } catch (e) {
      if (alive()) {
        notice = e.message || "操作未完成，请重试。";
        render();
      }
    }
  };
  root.classList.add("progressRegion");
  root.textContent = "正在读取阶段成果…";
  function currentItems() {
    return filterItems(snapshot.items, {
      ...filters, query,
      module_ids: moduleScope(snapshot.modules, filters.module_filter),
    });
  }
  const object = (row) => ({
    ref: row.ref,
    kind: "change",
    status: "current",
    source: row.report_ref,
    observed_at: row.report.observed_at,
  });
  function cancelConfirmation() {
    if (sending) sdk.cancelFeedback?.().catch(() => {});
  }
  function selectLocally(row) {
    if (selected !== row.key) cancelConfirmation();
    selected = row.key;
  }
  async function choose(row) {
    selectLocally(row);
    await sdk.focus(object(row));
  }
  async function refresh() {
    if (!alive() || !visible) return;
    const token = ++generation;
    loading = true;
    const next = await loadProgress(resources);
    if (!alive() || !visible || token !== generation) return;
    const previous = snapshot;
    snapshot = next;
    for (const [key] of drafts) {
      const old = previous.items.find((x) => x.key === key),
        now = next.items.find((x) => x.key === key);
      if (old && (!now || old.hash !== now.hash)) {
        cancelConfirmation();
        if (
          !now &&
          next.failures.some(
            (f) => f.ref === old.report_ref && f.kind === "unreadable",
          )
        )
          drafts.delete(key);
      }
    }
    if (!next.items.some((x) => x.key === selected)) {
      selected = "";
      feedbackKey = "";
    }
    // Reauthorize and reread only optional bodies the reader actually opened.
    const results = await Promise.all(
      [...sources].map(async ([key, source]) => [
        key,
        {
          ...source,
          result: await readText(resources, source.ref, source.hash),
        },
      ]),
    );
    if (!alive() || !visible || token !== generation) return;
    sources.clear();
    for (const [k, v] of results) sources.set(k, v);
    const images = await Promise.all(
      [...media].map(async ([key, m]) => {
        try {
          return [
            key,
            {
              ...m,
              url: await readMedia(sdk, resources, m.source),
              error: null,
            },
          ];
        } catch (e) {
          return [key, { ...m, url: null, error: e.message }];
        }
      }),
    );
    if (!alive() || !visible || token !== generation) return;
    media.clear();
    for (const [k, v] of images) media.set(k, v);
    loading = false;
    render();
    await watch().catch(() => {});
  }
  async function watch() {
    if (!alive() || !visible || !context.watch) return;
    const refs = [...new Set(snapshot.watched || [REFERENCES.index])].sort();
    const key = JSON.stringify(refs);
    if (key === watchKey) return;
    const epoch = ++watchEpoch;
    const previousKey = watchKey;
    watchKey = key;
    try {
      // The SDK replaces the group only after every reference is observed.
      // Keep the existing group if registration of the new group fails.
      const stop = await context.watch(refs, () => refresh().catch(() => {}));
      if (!alive() || !visible || watchEpoch !== epoch) await stop?.();
      else stopWatch = stop;
    } catch (error) {
      if (watchEpoch === epoch) watchKey = previousKey;
      throw error;
    }
  }
  function stopWatching() {
    watchEpoch++;
    watchKey = "";
    const stop = stopWatch;
    stopWatch = null;
    Promise.resolve(stop?.()).catch(() => {});
  }
  async function navigate(row) {
    const checked = await readText(resources, row.report_ref, row.hash);
    if (checked.failure) {
      messages.set(row.key, checked.failure.message);
      await refresh();
      return;
    }
    selectLocally(row);
    try {
      // The registered entry names the destination; the formal achievement
      // association supplies its module. Never infer a target from prose.
      const entry = new URL(row.entry.url, "https://portal.invalid");
      if (
        entry.pathname === "/workbenches/view" &&
        entry.searchParams.get("region") === "architecture"
      ) {
        const m = snapshot.modules.find(
          (m) => m.id === browsingModule(row).id,
        );
        if (!m || !(await resources.resolve(m.ref)).readable) {
          messages.set(row.key, "关联模块目前不可读取，暂未打开架构入口。");
          render();
          return;
        }
        const outgoing = {
          object: {
            ref: m.ref,
            kind: "module:" + m.id,
            status: "current",
            source: m.source,
            observed_at: m.observed_at,
          },
        };
        navigationFocus = outgoing;
        try {
          const result = await sdk.focus(outgoing.object);
          if (navigationFocus === outgoing)
            outgoing.revision = result?.revision;
        } catch (e) {
          if (navigationFocus === outgoing) navigationFocus = null;
          throw e;
        }
      }
      const r = await sdk.openPage(row.entry.url);
      if (!alive()) return;
      messages.set(
        row.key,
        r.opened
          ? r.mode === "local"
            ? "已打开，可从工作台返回续读。"
            : "已发起页面导航。"
          : "当前布局未能打开入口；阅读已保留。",
      );
    } catch (e) {
      messages.set(row.key, `入口未打开：${e.message || e.code || "请重试"}`);
    }
    if (alive()) render();
  }
  async function navigateModule(row) {
    const m =
      row.module_attribution ||
      snapshot.modules.find((x) => x.id === row.module_id);
    if (!m) return;
    if (!(await resources.resolve(m.ref)).readable) {
      messages.set(row.key, "模块目前不可读取。");
      render();
      return;
    }
    await sdk.focus({
      ref: m.ref,
      kind: "module:" + m.id,
      status: "current",
      source: m.source,
      observed_at: m.observed_at,
    });
    if ((await sdk.openPanel("architecture")) === false) {
      messages.set(row.key, "当前布局未能打开架构区。");
      render();
    }
  }
  async function openSource(row, source, label) {
    await choose(row);
    const token = generation,
      key = row.key + "|" + source.read_ref;
    sources.set(key, {
      ref: source.read_ref,
      hash: source.sha256,
      label,
      result: null,
    });
    render();
    const result = await readText(resources, source.read_ref, source.sha256);
    if (
      alive() &&
      token === generation &&
      snapshot.items.some((x) => x.key === row.key && x.hash === row.hash)
    ) {
      sources.set(key, {
        ref: source.read_ref,
        hash: source.sha256,
        label,
        result,
      });
      render();
    }
  }
  async function send(row) {
    if (sending || !drafts.get(row.key)?.trim()) return;
    const checked = await readText(resources, row.report_ref, row.hash);
    if (checked.failure) {
      messages.set(row.key, checked.failure.message);
      await refresh();
      return;
    }
    if (
      selected !== row.key ||
      !snapshot.items.some((x) => x.key === row.key && x.hash === row.hash)
    )
      return;
    const token = generation;
    sending = true;
    try {
      await sdk.focus(object(row));
      if (!alive() || token !== generation || selected !== row.key) return;
      await sdk.feedback({
        subject:
          row.title.length <= 240
            ? row.title
            : row.title.slice(0, 239).replace(/[\uD800-\uDBFF]$/, "") + "…",
        ref: row.ref,
        anchor: {
          source_ref: row.ref,
          ...(steps.has(row.key) ? { step_index: steps.get(row.key) } : {}),
        },
        scene: {
          panel: "progress",
          report_ref: row.report_ref,
          improvement_id: row.id || null,
          module_id: row.module_id,
          json_pointer: row.json_pointer,
          content_sha256: row.hash,
          presentation_ref: row.scene_report_ref || row.report_ref,
          ...(steps.has(row.key) ? { step_index: steps.get(row.key) } : {}),
        },
        text: drafts.get(row.key),
      });
      messages.set(row.key, "反馈已交给团队。");
      drafts.delete(row.key);
      feedbackKey = "";
    } catch (e) {
      messages.set(
        row.key,
        e.code === "projection.cancelled"
          ? "已取消，草稿仍保留。"
          : "反馈未完成，草稿仍保留。",
      );
    } finally {
      sending = false;
      if (alive()) render();
    }
  }
  async function acceptFocus(value, meta = {}) {
    const rev = Number(meta.revision ?? focusRevision + 1);
    if (!alive() || rev <= focusRevision) return;
    focusRevision = rev;
    // Our outbound navigation must not refilter/scroll the reader on return.
    // Only consume its matching echo; later independent selections still apply.
    if (
      navigationFocus &&
      value &&
      (navigationFocus.revision == null || navigationFocus.revision === rev) &&
      ["ref", "kind", "status", "source", "observed_at"].every(
        (key) => navigationFocus.object[key] === value[key],
      )
    ) {
      navigationFocus = null;
      return;
    }
    if (!value) {
      filters.module_filter = "";
      render();
      return;
    }
    if (value.kind?.startsWith("module:")) {
      const m = snapshot.modules.find(
        (m) => m.id === value.kind.slice(7) && m.ref === value.ref,
      );
      if (
        m &&
        (await resources.resolve(m.ref)).readable &&
        rev === focusRevision
      ) {
        filters.module_filter = m.id;
        render();
      }
    } else if (value.kind === "change") {
      const row = snapshot.items.find((x) => x.ref === value.ref);
      if (row && rev === focusRevision) {
        selected = row.key;
        filters.start_date = "";
        filters.end_date = "";
        filters.module_filter = "";
        query = "";
        render();
        [...root.querySelectorAll("[data-item-key]")]
          .find((n) => n.dataset.itemKey === row.key)
          ?.scrollIntoView({ block: "nearest" });
      }
    }
  }
  function selectControl(label, value, options, changed) {
    const box = el("label", label, "pSelect"),
      s = el("select");
    s.setAttribute("aria-label", label);
    for (const [v, t] of options) {
      const o = el("option", t);
      o.value = v;
      s.append(o);
    }
    s.value = value;
    s.onchange = () => {
      changed(s.value);
      render();
    };
    box.append(s);
    return box;
  }
  function render() {
    if (!alive()) return;
    const before = hiddenView || view.capture();
    root.replaceChildren();
    const toolbar = el("header", undefined, "pToolbar");
    toolbar.append(el("h1", "阶段进展", "pToolbarTitle"));
    const dates = el("div", undefined, "pDates");
    dates.setAttribute("role", "group");
    dates.setAttribute("aria-label", "成果日期（香港时间，按所属阶段结束日）");
    for (const [key, label, caption] of [
      ["start_date", "起始日期", "从"], ["end_date", "结束日期", "至"],
    ]) {
      const box = el("label", caption, "pSelect"), input = el("input");
      input.type = "date";
      input.setAttribute("aria-label", label);
      input.title = "按成果所属阶段结束日筛选（香港时间），包含当天";
      input.value = filters[key];
      if (dateRangeError(filters)) input.setAttribute("aria-invalid", "true");
      input.onchange = () => { filters[key] = input.value; render(); };
      box.append(input);
      dates.append(box);
    }
    const allDates = btn("全部日期", () => {
      filters.start_date = filters.end_date = "";
      render();
    });
    allDates.title = "清除起止日期";
    allDates.setAttribute("aria-pressed", String(!filters.start_date && !filters.end_date));
    dates.append(allDates);
    const moduleControl = selectControl("模块", filters.module_filter, [
      ["", "全部模块"],
      ...[...new Map([
        ...snapshot.items.map(browsingModule),
        ...snapshot.modules.filter(m => m.id === filters.module_filter),
      ].filter(m => m.id).map(m => [m.id, m.title])).entries()],
    ], v => filters.module_filter = v);
    moduleControl.classList.add("pModuleFilter");
    const search = el("input");
    search.type = "search";
    search.className = "pSearch";
    search.placeholder = "搜索成果";
    search.setAttribute("aria-label", "查找成果");
    search.value = query;
    search.oninput = () => { query = search.value; render(); };
    toolbar.append(dates, moduleControl, search);
    root.append(toolbar);
    const rangeError = dateRangeError(filters);
    if (rangeError) {
      const message = el("p", rangeError, "pMessage");
      message.setAttribute("role", "alert");
      root.append(message);
    }
    if (notice) root.append(el("p", notice, "pMessage"));
    if (filters.module_filter) {
      const range = el(
        "p",
        `范围：${snapshot.modules.find((m) => m.id === filters.module_filter)?.title || filters.module_filter}及其子模块 `,
        "pQuiet",
      );
      range.append(
        btn("看全部模块", () => {
          filters.module_filter = "";
          render();
        }),
      );
      root.append(range);
    }
    const main = el("div", undefined, "pStories");
    root.append(main);
    const rows = currentItems();
    for (const r of snapshot.reports) {
      const group = rows.filter(x => x.report_ref === r.ref);
      if (!group.length) continue;
      const section = el("section", undefined, "pPeriod");
      section.append(el("p", `${stamp(r.start_at)} — ${stamp(r.end_at)}${r !== snapshot.reports[0] ? " · 历史回顾" : ""}`, "pDate"));
      for (const [i, row] of group.entries()) renderStory(section, row, i === 0);
      main.append(section);
    }
    if (!rows.length && !rangeError && snapshot.reports.length) {
      const none = el("p", snapshot.failures.length
        ? "已读取的成果中没有匹配项；部分汇报尚未读取。"
        : "这个范围还没有匹配的成果。", "pEmpty");
      none.setAttribute("role", "status");
      if (query || filters.module_filter || filters.start_date || filters.end_date)
        none.append(btn("清除筛选", () => {
          filters = { start_date: "", end_date: "", module_filter: "" };
          query = "";
          render();
        }));
      main.append(none);
    }
    if (!snapshot.reports.length && !snapshot.failures.length)
      main.append(el("p", loading ? "正在读取成果…" : "还没有阶段汇报。"));
    for (const failure of snapshot.failures) {
      const p = el("p", failure.message, "pMessage");
      p.append(btn("重新读取这份内容", guard(refresh)));
      main.append(p);
    }
    if (before) {
      const restored = view.restore(before);
      requestAnimationFrame(() => {
        if (alive() && visible) view.restore(before);
      });
      // openPage can finish after the host already hid this iframe. Retain
      // the saved reading scene until it is measurable on the next show.
      hiddenView = restored && visible ? null : before;
    }
  }
  function renderStory(parent, row, first) {
    const article = el(
      "article",
      undefined,
      "pStory" + (first ? " pLead" : ""),
    );
    article.dataset.itemKey = row.key;
    article.dataset.viewOwner = row.key;
    if (row.recent)
      article.append(
        el("p", "最近可体验 · 来自 " + stamp(row.report.end_at), "pQuiet"),
      );
    const heading = el(first ? "h1" : "h2"),
      select = btn(
        row.title,
        guard(async () => {
          await choose(row);
          render();
        }),
      );
    select.className = "pTitle";
    select.setAttribute("data-change-key", row.key);
    select.setAttribute("aria-pressed", String(selected === row.key));
    heading.append(select);
    article.append(
      heading,
      el("p", row.effect || row.summary || "", "pEffect"),
    );
    if (row.id === "pending-human-message-style") {
      const demo = el("div", undefined, "pDemo");
      demo.append(el("span", "交互示意 · 本地示例，不发送消息", "pQuiet"));
      const bubble = el("div", "我想了解这个改善。", "pBubble");
      demo.append(
        bubble,
        el("small", simulations.get(row.key) ? "已受理" : "发送中"),
        btn(simulations.get(row.key) ? "重置示意" : "看受理后", () => {
          simulations.set(row.key, !simulations.get(row.key));
          render();
        }),
      );
      article.append(demo);
    }
    const actions = el("div", undefined, "pActions");
    if (row.entry)
      actions.append(
        btn(
          row.entry.label,
          guard(() => navigate(row)),
        ),
      );
    const displayModule = browsingModule(row);
    const module =
      row.module_attribution ||
      snapshot.modules.find((m) => m.id === row.module_id);
    if (module)
      actions.append(
        btn(
          displayModule.title,
          guard(() => navigateModule(row)),
        ),
      );
    else if (displayModule.title)
      actions.append(el("span", displayModule.title, "pQuiet"));
    actions.append(
      btn(
        "讨论这项成果",
        guard(async () => {
          await choose(row);
          feedbackKey = feedbackKey === row.key ? "" : row.key;
          render();
          root.querySelector("textarea")?.focus();
        }),
      ),
    );
    article.append(actions);
    if (messages.has(row.key))
      article.append(el("p", messages.get(row.key), "pMessage"));
    const detail = el("details", undefined, "pDetails");
    detail.dataset.viewKey = row.key;
    detail.append(el("summary", "实现与体验步骤"));
    const body = el("div", undefined, "pDetailBody");
    detail.append(body);
    if (row.report !== snapshot.reports[0])
      body.append(
        el("p", "以下为该期版本记载；体验入口打开当前页面。", "pQuiet"),
      );
    const list = el("ol");
    array(row.steps).forEach((step, i) => {
      const li = el("li");
      li.append(
        btn(
          typeof step === "string" ? step : step.text || step.title || "",
          guard(async () => {
            await choose(row);
            steps.set(row.key, i);
            feedbackKey = row.key;
            render();
          }),
        ),
      );
      list.append(li);
    });
    if (list.children.length) body.append(list);
    const d = row.details || {};
    if (array(d.commits).length || array(d.commit_sources).length) {
      body.append(el("h3", "实现"));
      const declared = new Map(
        array(d.commit_sources).map((c) => [c.commit, c]),
      );
      for (const commit of new Set([...array(d.commits), ...declared.keys()])) {
        const source = declared.get(commit),
          p = el("p");
        p.append(
          source
            ? btn(
                commit.slice(0, 12) + " · 提交说明",
                guard(() => openSource(row, source, "提交说明")),
              )
            : el("code", commit),
        );
        body.append(p);
      }
    }
    if (array(d.designs).length || array(d.design_sources).length) {
      body.append(el("h3", "Design"));
      const provided = array(d.design_sources);
      for (const design of provided) {
        const p = el(
          "p",
          `${design.ref} · 报告版本 ${design.recorded_version}`,
        );
        if (design.recorded_body)
          p.append(
            btn(
              "读取当期正文",
              guard(() => openSource(row, design.recorded_body, "当期 Design")),
            ),
          );
        else if (design.recorded_excerpt_ref)
          p.append(
            btn(
              "读取当期摘录",
              guard(() =>
                openSource(
                  row,
                  { read_ref: design.recorded_excerpt_ref },
                  "当期摘录",
                ),
              ),
            ),
          );
        if (
          design.live_version_observed &&
          design.live_version_observed !== design.recorded_version
        )
          p.append(
            el("span", ` · 现行观察版本 ${design.live_version_observed}`),
          );
        body.append(p);
      }
      for (const design of array(d.designs).filter(
        (d) => !provided.some((p) => p.ref === d.ref),
      ))
        body.append(
          el(
            "p",
            `${design.ref} · ${design.version || ""} ${design.summary || ""}`,
          ),
        );
    }
    for (const [i, m] of row.media.entries()) {
      if (m.kind !== "screenshot") continue;
      const key = row.key + "|media|" + i,
        loaded = media.get(key),
        fig = el("figure");
      fig.append(el("figcaption", m.caption || "当期截图"));
      if (loaded?.url) {
        const img = el("img");
        img.src = loaded.url;
        img.alt = m.alt || m.caption || "当期截图";
        img.width = m.width || 720;
        img.height = m.height || 450;
        fig.append(img);
      } else
        fig.append(
          btn(
            "查看当期截图",
            guard(async () => {
              try {
                const url = await readMedia(sdk, resources, m);
                if (alive()) media.set(key, { source: m, url });
              } catch (e) {
                media.set(key, { source: m, error: e.message });
              }
              render();
            }),
          ),
        );
      if (loaded?.error) fig.append(el("p", loaded.error));
      body.append(fig);
    }
    for (const [key, source] of sources) {
      if (!key.startsWith(row.key + "|")) continue;
      const section = el("section", undefined, "pDocument");
      section.dataset.scrollKey = key;
      section.append(el("h3", source.label));
      if (!source.result) section.append(el("p", "正在读取…"));
      else if (source.result.failure)
        section.append(
          el("p", source.result.failure.message),
          btn(
            "重试读取",
            guard(() =>
              openSource(
                row,
                { read_ref: source.ref, sha256: source.hash },
                source.label,
              ),
            ),
          ),
        );
      else {
        const content = el("div");
        renderMarkdown(content, source.result.text);
        section.append(content);
      }
      body.append(section);
    }
    article.append(detail);
    if (feedbackKey === row.key) {
      const box = el("div", undefined, "pFeedback"),
        input = el("textarea");
      input.setAttribute("aria-label", "反馈内容");
      input.value = drafts.get(row.key) || "";
      input.placeholder = "你对这项成果有什么想法？";
      input.oninput = () => drafts.set(row.key, input.value);
      box.append(
        input,
        btn(
          "提交反馈",
          guard(() => send(row)),
          sending,
        ),
        btn("返回阅读", () => {
          closeFeedback();
        }),
      );
      article.append(box);
    }
    parent.append(article);
  }
  function closeFeedback() {
    const key = feedbackKey;
    cancelConfirmation();
    feedbackKey = "";
    render();
    const article = [...root.querySelectorAll("[data-item-key]")].find(
      (n) => n.dataset.itemKey === key,
    );
    [...(article?.querySelectorAll("button") || [])]
      .find((n) => n.textContent === "讨论这项成果")
      ?.focus({ preventScroll: true });
  }
  const escape = (e) => {
    if (e.key !== "Escape") return;
    cancelConfirmation();
    if (feedbackKey) {
      closeFeedback();
    }
  };
  root.addEventListener("keydown", escape);
  await refresh();
  context.onFocus?.(acceptFocus);
  try {
    const c = await sdk.context();
    if (c.focus?.object)
      await acceptFocus(c.focus.object, { revision: c.focus.revision });
  } catch {
    /* no shared selection */
  }
  const runtime = {
    async show() {
      if (!alive()) return;
      visible = true;
      await refresh();
    },
    hide() {
      if (visible) hiddenView = view.capture();
      visible = false;
      generation++;
      stopWatching();
    },
    destroy() {
      if (destroyed) return;
      destroyed = true;
      generation++;
      stopWatching();
      cancelConfirmation();
      view.destroy();
      sources.clear();
      media.clear();
      drafts.clear();
      root.removeEventListener("keydown", escape);
      root.replaceChildren();
      root.classList.remove("progressRegion");
    },
  };
  context.onDispose?.(() => runtime.destroy());
  return runtime;
}
