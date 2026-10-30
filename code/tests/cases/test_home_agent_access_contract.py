from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AGENT_ACCESS_JS = ROOT / "management_portal" / "static" / "home_agent_access.js"


def test_agent_access_loads_shortcuts_and_renders_navigable_residents() -> None:
    subprocess.run(
        ["node", str(ROOT / "tests/cases/javascript/home_agent_access.cjs")],
        cwd=ROOT,
        check=True,
    )


def test_recently_used_preserves_distinct_activity_labels() -> None:
    """Home 最近使用列表分别显示排队中、启动中和运行中，已完成的只显示任务摘要"""
    source = AGENT_ACCESS_JS.read_text(encoding="utf-8")
    start = source.index("function agentAccessActivityLabel")
    end = source.index("\n    function renderAgentAccess", start)
    functions = source[start:end]

    script = f"""
global.window = {{}};
require("./management_portal/static/portal_text.js");
const esc = (value) => String(value ?? "");
const residentShortcutUrl = () => "/lineage";
{functions}

const expected = {{
  queued: "排队中",
  dispatching: "启动中",
  running: "运行中",
}};
for (const [status, label] of Object.entries(expected)) {{
  const html = agentAccessLinkHtml({{
    residentId: "resident_123456789abc",
    title: "Resident",
    summary: "任务摘要",
    activityStatus: status,
  }});
  if (!html.includes(`${{label}} · 任务摘要`)) throw new Error(`${{status}}: ${{html}}`);
}}

const completed = agentAccessLinkHtml({{
  residentId: "resident_123456789abc",
  title: "Resident",
  summary: "任务摘要",
  activityStatus: "completed",
}});
if (!completed.includes("任务摘要")) throw new Error(completed);
for (const label of Object.values(expected)) {{
  if (completed.includes(label)) throw new Error(completed);
}}
"""
    subprocess.run(["node", "-e", script], cwd=ROOT, check=True)

    assert 'activityStatus: String(item?.status || "").trim().toLowerCase()' in source
    assert 'running: ["queued", "dispatching", "running"]' not in source
