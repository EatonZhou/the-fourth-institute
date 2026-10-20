import {REFERENCES} from '../src/progress/data.js';
export const base = REFERENCES.index;
export const stage = "workspace_f9ff5e8a76df/file/reports/stage-index.json";
export const content = "workspace_f9ff5e8a76df/file/reports/stage-content.json";
export const schema =
  "workspace_f9ff5e8a76df/file/reports/stage-report-schema.json";
export const map = "repo_5d287fed9c7e/file/architecture/modules.yaml";
export const report = "workspace_f9ff5e8a76df/file/reports/stages/test.json";
export const sha = async (text) =>
  Array.from(
    new Uint8Array(
      await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)),
    ),
    (x) => x.toString(16).padStart(2, "0"),
  ).join("");
export async function fixture() {
  const body = {
    schema: "tfi.portal-stage-report.v1",
    ref: report,
    title: "阶段成果",
    start_at: "2026-10-14T00:00:00Z",
    end_at: "2026-10-15T00:00:00Z",
    generated_at: "2026-10-15T00:00:00Z",
    headlines: [{ title: "消息外观更清楚", effect: "一眼看清是谁在说话" }],
    modules: [
      {
        id: "conversation",
        title: "对话",
        improvements: [
          {
            id: "bubble",
            title: "消息外观更清楚",
            effect: "一眼看清是谁在说话",
            entry: {
              label: "体验",
              url: "/workbenches/view?id=workbench_75b51c00005e",
            },
            steps: ["打开对话，观察气泡。"],
            details: { commits: ["abc"], designs: [] },
          },
        ],
      },
      {
        id: "canvas",
        title: "团队画布",
        improvements: [
          {
            id: "fold",
            title: "团队画布更清楚",
            effect: "收起团队后留下阅读空间",
            entry: {
              label: "体验画布",
              url: "/lineage?lineage=lineage_8794980b1bb7",
            },
            steps: ["展开团队，再收起。"],
          },
        ],
      },
    ],
  };
  const raw = JSON.stringify(body, null, 2),
    line = raw.slice(0, raw.indexOf('"id": "bubble"')).split("\n").length;
  const target = {
    report_ref: report,
    module_id: "conversation",
    improvement_id: "bubble",
    json_pointer: "/modules/0/improvements/0",
    ref: report + "#L" + line,
  };
  const db = {
    [stage]: JSON.stringify({
      schema: "tfi.portal-stage-index.v1",
      schema_ref: schema,
      content_ref: content,
      report_refs: [report],
      latest_report_ref: report,
    }),
    [schema]: JSON.stringify({ "x-contract-revision": 3 }),
    [report]: raw,
    [content]: JSON.stringify({
      report_sources: [{ report_ref: report, sha256: await sha(raw) }],
      item_links: [
        {
          source: { report_ref: report, json_pointer: "/headlines/0" },
          target,
        },
      ],
      improvement_details: [],
      experience_targets: [
        {
          target,
          entry: {
            label: "打开团队对话",
            url: "/workbenches/view?id=workbench_75b51c00005e&region=facade",
          },
        },
      ],
    }),
    [map]:
      "modules:\n  - id: conversation\n    title: 对话\n  - id: canvas\n    title: 团队画布\n",
  };
  const denied = new Set(),
    failed = new Set(),
    calls = [];
  const resources = {
    resolve: async (ref) => ({ readable: !denied.has(ref.split("#")[0]) }),
    read: async (ref, format) => {
      const address = ref.split("#")[0];
      calls.push({ ref, format });
      if (denied.has(address)) return { readable: false, ref, data: null };
      if (failed.has(address))
        throw Object.assign(Error("network unavailable"), {
          code: "network_error",
        });
      const text = db[address];
      if (text === undefined) return { readable: false, ref, data: null };
      return {
        readable: true,
        ref,
        data:
          format === "yaml"
            ? {
                modules: [
                  { id: "conversation", title: "对话" },
                  { id: "canvas", title: "团队画布" },
                ],
              }
            : format === "json"
              ? JSON.parse(text)
              : text,
        metadata: { content_sha256: await sha(text) },
      };
    },
  };
  return { db, body, target, resources, denied, failed, calls };
}
