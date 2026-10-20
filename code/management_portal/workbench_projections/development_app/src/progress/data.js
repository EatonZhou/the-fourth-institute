import { itemKey } from "./model.js";
import { schemaMatches } from "../shared/format-tags.js";
import { contentDigest } from "../shared/content-digest.js";
export const REFERENCES = Object.freeze({
  index: "workspace_f9ff5e8a76df/file/reports/stage-index.json",
  modules: "repo_711b6ab6d5c8/file/code/architecture/modules.yaml",
});
const array = (x) => (Array.isArray(x) ? x : []);
export async function digest(text) {
  return contentDigest(text);
}
const fault = (ref, kind, message) => ({ ref, kind, message });
async function readModules(resources, ref) {
  try {
    const [yaml, text] = await Promise.all([
      resources.read(ref, "yaml"),
      readText(resources, ref),
    ]);
    if (
      !yaml.readable ||
      text.failure ||
      yaml.metadata?.content_sha256 !== text.hash
    )
      return { modules: [] };
    const lines = text.text.split("\n"),
      modules = [];
    array(yaml.data?.modules).forEach((m, i) => {
      if (array(yaml.data.modules).filter((x) => x.id === m.id).length !== 1)
        return;
      const n = lines.findIndex(
        (l) =>
          /^\s*-?\s*id:\s*/.test(l) &&
          l
            .replace(/^\s*-?\s*id:\s*/, "")
            .replace(/["']/g, "")
            .trim() === m.id,
      );
      if (n >= 0)
        modules.push({
          ...m,
          ref: ref + "#L" + (n + 1),
          source: ref,
          json_pointer: `/modules/${i}`,
          observed_at: new Date().toISOString(),
        });
    });
    return { modules, hash: text.hash };
  } catch {
    return { modules: [] };
  }
}
export async function readText(resources, ref, expected) {
  try {
    const r = await resources.read(ref, "text");
    if (!r.readable)
      return { failure: fault(ref, "unreadable", "这份内容目前不可读取。") };
    const hash = await digest(r.data);
    if (
      (expected && hash !== expected) ||
      (r.metadata?.content_sha256 && r.metadata.content_sha256 !== hash)
    )
      return { failure: fault(ref, "version", "内容版本已变化，请重新读取。") };
    return { ref, text: r.data, hash, metadata: r.metadata };
  } catch (error) {
    return {
      failure: fault(ref, "error", `读取未完成：${error.message || "请重试"}`),
    };
  }
}
// Record token offsets while parsing the SAME text used for the report objects.
// No title search, object reserialization, or independently read JSON/text pair.
export function jsonDocument(text) {
  const data = JSON.parse(text),
    lines = new Map();
  let pos = 0,
    line = 1;
  const advance = () => {
    if (text[pos++] === "\n") line++;
  };
  const space = () => {
    while (/\s/.test(text[pos] || "") && pos < text.length) advance();
  };
  const string = () => {
    const start = pos++;
    while (pos < text.length) {
      if (text[pos] === "\\") pos += 2;
      else if (text[pos++] === '"') break;
    }
    return JSON.parse(text.slice(start, pos));
  };
  const walk = (path) => {
    space();
    lines.set(path, line);
    const ch = text[pos];
    if (ch === "{") {
      pos++;
      space();
      if (text[pos] === "}") {
        pos++;
        return;
      }
      while (pos < text.length) {
        const k = string();
        space();
        pos++;
        walk(path + "/" + k.replace(/~/g, "~0").replace(/\//g, "~1"));
        space();
        if (text[pos++] === "}") break;
        space();
      }
    } else if (ch === "[") {
      pos++;
      space();
      let i = 0;
      if (text[pos] === "]") {
        pos++;
        return;
      }
      while (pos < text.length) {
        walk(path + "/" + i++);
        space();
        if (text[pos++] === "]") break;
      }
    } else if (ch === '"') string();
    else while (pos < text.length && !/[\s,}\]]/.test(text[pos])) advance();
  };
  walk("");
  return { data, lines };
}
export function experienceEntry(entry) {
  if (
    !entry ||
    typeof entry.label !== "string" ||
    !entry.label.trim() ||
    typeof entry.url !== "string" ||
    !/^\/(?!\/)[^\s\\]*$/.test(entry.url) ||
    entry.url.length > 4096
  )
    return null;
  if (
    entry.url.startsWith("/workbenches/view") &&
    !/^\/workbenches\/view\?id=workbench_[a-f0-9]{12}&region=[A-Za-z0-9_-]+$/.test(
      entry.url,
    )
  )
    return null;
  return { label: entry.label, url: entry.url };
}
export async function loadProgress(resources, refs = REFERENCES) {
  const failures = [],
    watched = [refs.index, refs.modules],
    documents = new Map();
  async function json(ref) {
    watched.push(ref);
    const doc = await readText(resources, ref);
    if (doc.failure) {
      failures.push(doc.failure);
      return null;
    }
    try {
      const parsed = jsonDocument(doc.text);
      const result = { ...doc, ...parsed };
      documents.set(ref, result);
      return result;
    } catch {
      failures.push(fault(ref, "format", "这份报告格式暂不可读取。"));
      return null;
    }
  }
  const index = await json(refs.index);
  if (!index || !schemaMatches(index.data.schema, "tfi.portal-stage-index.v1")) {
    if (index)
      failures.push(fault(refs.index, "format", "阶段成果索引格式不匹配。"));
    return { items: [], reports: [], modules: [], failures, watched };
  }
  const reportRefs = [...new Set(array(index.data.report_refs))];
  const [schema, supplement, ...reads] = await Promise.all([
    json(index.data.schema_ref),
    index.data.content_ref ? json(index.data.content_ref) : null,
    ...reportRefs.map(json),
  ]);
  const content =
    schema?.data["x-contract-revision"] >= 2 ? supplement?.data : null;
  const reports = [],
    rows = new Map(),
    invalidKeys = new Set();
  for (let i = 0; i < reads.length; i++) {
    const doc = reads[i];
    if (!doc) continue;
    const d = doc.data,
      ref = reportRefs[i];
    if (
      !schemaMatches(d.schema, "tfi.portal-stage-report.v1") ||
      d.ref !== ref ||
      !Number.isFinite(Date.parse(d.end_at)) ||
      !Array.isArray(d.modules)
    ) {
      failures.push(fault(ref, "format", "阶段成果格式不匹配。"));
      continue;
    }
    const r = {
      ...d,
      hash: doc.hash,
      observed_at: new Date().toISOString(),
      items: [],
      recent: [],
      complete: true,
    };
    reports.push(r);
    d.modules.forEach((m, mi) =>
      array(m.improvements).forEach((c, ci) => {
        if (!m.id || !c.id || !c.title) {
          r.complete = false;
          return;
        }
        const pointer = `/modules/${mi}/improvements/${ci}`,
          key = itemKey(ref, m.id, c.id);
        if (rows.has(key) || invalidKeys.has(key)) {
          r.complete = false;
          invalidKeys.add(key);
          rows.delete(key);
          r.items = r.items.filter((x) => x.key !== key);
          return;
        }
        const row = {
          ...c,
          key,
          ref: ref + "#L" + doc.lines.get(pointer + "/id"),
          report_ref: ref,
          json_pointer: pointer,
          module_id: m.id,
          module_title: m.title || m.id,
          report: r,
          hash: doc.hash,
          entry: experienceEntry(c.entry),
          details: c.details || {},
          media: array(c.media),
          headline: false,
        };
        rows.set(key, row);
        r.items.push(row);
      }),
    );
  }
  const sourceGood = (ref) =>
    array(content?.report_sources).filter((x) => x.report_ref === ref)
      .length === 1 &&
    array(content?.report_sources).find((x) => x.report_ref === ref)?.sha256 ===
      documents.get(ref)?.hash;
  const targetRow = (t, inlineReportRef = null) => {
    // Same-report inline links and their targets were parsed from identical,
    // hash-checked bytes. Only external supplemental links need report_sources.
    if (!t || (t.report_ref !== inlineReportRef && !sourceGood(t.report_ref)))
      return null;
    const row = rows.get(itemKey(t.report_ref, t.module_id, t.improvement_id));
    return row && row.json_pointer === t.json_pointer && row.ref === t.ref
      ? row
      : null;
  };
  for (const row of rows.values()) {
    const candidates = array(content?.experience_targets).filter(
      (x) =>
        x.target?.report_ref === row.report_ref &&
        x.target?.module_id === row.module_id &&
        x.target?.improvement_id === row.id,
    );
    if (candidates.length)
      row.entry =
        candidates.length === 1 && targetRow(candidates[0].target)
          ? experienceEntry(candidates[0].entry)
          : null;
    const details = array(content?.improvement_details).filter(
      (x) =>
        x.target?.report_ref === row.report_ref &&
        x.target?.module_id === row.module_id &&
        x.target?.improvement_id === row.id,
    );
    if (details.length === 1 && targetRow(details[0].target)) {
      row.details = { ...row.details, ...details[0] };
      row.media = array(details[0].media);
    }
    row.improvement_id = row.id;
  }
  // Attribution changes browsing only. The original report identity remains
  // authoritative for feedback, deduplication and drafts.
  const currentMap = await readModules(resources, refs.modules);
  await Promise.all(
    [...rows.values()].map(async (row) => {
      const matches = array(content?.module_attributions).filter(
        (x) =>
          x.target?.report_ref === row.report_ref &&
          x.target?.module_id === row.module_id &&
          x.target?.improvement_id === row.id,
      );
      if (matches.length !== 1 || !targetRow(matches[0].target)) return;
      const declared = matches[0].module;
      if (
        !declared ||
        declared.map_ref !== refs.modules ||
        !/^[a-f0-9]{64}$/.test(declared.map_sha256 || "")
      )
        return;
      // Old map attribution is optional browsing metadata, not a reason to
      // fetch or watch historical architecture. Original report/Git evidence
      // remains untouched when this current-map declaration cannot be used.
      if (currentMap.hash !== declared.map_sha256) return;
      const module = currentMap.modules.find(
        (m) =>
          m.id === declared.id &&
          m.title === declared.title &&
          m.ref === declared.ref &&
          m.json_pointer === declared.json_pointer,
      );
      if (module) row.module_attribution = module;
    }),
  );
  for (const r of reports) {
    for (const collection of ["headlines", "recent_experiences"])
      array(r[collection]).forEach((item, i) => {
        const pointer = `/${collection}/${i}`,
          links = array(content?.item_links).filter(
            (x) =>
              x.source?.report_ref === r.ref &&
              x.source?.json_pointer === pointer,
          );
        const explicit = item.improvement_ref;
        const same = (a, b) =>
          [
            "report_ref",
            "module_id",
            "improvement_id",
            "json_pointer",
            "ref",
          ].every((k) => a?.[k] === b?.[k]);
        const conflict =
          links.length > 1 ||
          (explicit && links.length === 1 && !same(explicit, links[0].target));
        const t = conflict
          ? null
          : explicit ||
            (links.length === 1 && sourceGood(r.ref) ? links[0].target : null);
        const row = targetRow(t, explicit && !conflict ? r.ref : null);
        if (row) {
          if (collection === "headlines" && row.report_ref === r.ref) {
            row.headline = true;
            row.headline_order = i;
          } else if (collection === "recent_experiences")
            r.recent.push({ ...row, scene_report_ref: r.ref, recent: true });
          return;
        }
        // A standalone summary remains its own source object, never a guessed improvement.
        const doc = documents.get(r.ref),
          key = itemKey(r.ref, "summary", `${r.hash}:${pointer}`);
        const summary = {
          ...item,
          key,
          ref: r.ref + "#L" + doc.lines.get(pointer),
          report_ref: r.ref,
          json_pointer: pointer,
          hash: r.hash,
          report: r,
          module_id: "",
          module_title: "",
          details: {},
          media: [],
          entry: experienceEntry(item.entry),
          headline: true,
          standalone: true,
        };
        if (collection === "recent_experiences")
          r.recent.push({ ...summary, scene_report_ref: r.ref, recent: true });
        else r.items.push(summary);
      });
    r.items.sort(
      (a, b) =>
        Number(b.headline) - Number(a.headline) ||
        (a.headline_order ?? 99) - (b.headline_order ?? 99),
    );
  }
  reports.sort((a, b) => Date.parse(b.end_at) - Date.parse(a.end_at));
  const { modules } = currentMap;
  return {
    reports,
    items: reports.flatMap((r) => r.items),
    modules,
    failures,
    watched: [...new Set(watched)],
    index_ref: refs.index,
  };
}
export async function readMedia(sdk, resources, media) {
  if (!(await resources.resolve(media.read_ref)).readable)
    throw Error("图片目前不可读取。");
  const r = await sdk.resource("read", {
    address: media.read_ref.replace(/#L\d+$/, ""),
  });
  if (r.encoding !== "base64" && r.base64 === undefined)
    throw Error("图片格式不可读取。");
  const encoded = r.base64 ?? r.content,
    bytes = Uint8Array.from(atob(encoded), (c) => c.charCodeAt(0));
  if (media.sha256 && (await digest(bytes)) !== media.sha256)
    throw Error("图片版本已变化。");
  const type =
    r.content_type ||
    r.mime_type ||
    r.metadata?.content_type ||
    media.mime_type;
  if (!["image/png", "image/jpeg", "image/webp", "image/gif"].includes(type))
    throw Error("此图片格式暂不支持。");
  return `data:${type};base64,${encoded}`;
}
