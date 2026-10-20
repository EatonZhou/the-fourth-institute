---
kb_id: decision_5d2bf4a72f09
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 6
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T08:28:58Z'
  updated_by:
    user: ''
    agent: resident_13db052ea5c9
importance: 8
metaness: 1
ai_generated: true
links:
  source:
  - ${code_root}/management_portal/serve_management_portal.py
  - ${code_root}/management_portal/static/home.js
  - ${code_root}/management_portal/static/portal_home_components.js
  - ${code_root}/management_portal/static/portal_workbench.js
  related:
  - decision_5ff0cb51ed84
  - decision_00546e28e1e0
  - decision_ef80c6d3b5ee
  supersedes: []
  derived_from: []
---
# Daily News 的每日信息摘要

Daily News 是 Home 的 Get Me Aligned 一级组件中的独立子 Panel，为 Portal 登录用户提供机器人、具身智能、AI 芯片和相关计算系统的每日行业报告。页面展示已经生成并校验通过的 Markdown 文档；新闻检索和撰写由 RobotCompany 调研 Lineage 中的研究 Resident 完成。本 Design 负责用户看到的报告、日期语义、读取接口，以及生成任务与 Portal 的责任边界；共享 Workbench 负责 Panel 的布局、折叠、关闭和恢复。

## 用户看到的报告区

Daily News 区域由三部分组成：

- Workbench 标签行：用 **Daily News** 标识子 Panel，并提供共享的布局和关闭操作；正文内部不重复显示同名标题。
- 元信息：显示文档日期和文件更新时间；当今天的文件尚未就绪而页面采用较早报告时，额外显示“最近有效报告”。
- 正文：用 Portal 统一的 Markdown 渲染器显示标题、段落、列表和链接。

**文档日期**是文件名所代表的 Portal 发布日。正文开头另行说明新闻实际覆盖的自然日；当前日报通常在次日生成，因此两者允许相差一天。读者应以正文的覆盖日期理解新闻范围，以工具栏日期识别 Portal 当前读取的是哪份文件。

该区域没有单独的刷新按钮。页面打开时读取一次；用户重新加载或再次进入 Home 才会发起新的 Daily News 请求。

## 设计点

- 拖拽、分组、折叠、关闭或恢复 Daily News Panel 不会重新请求报告，也不会清除已经渲染的正文。
- 请求进行中时，正文位置显示加载提示。
- 读取成功时，页面显示报告日期、文件更新时间和完整 Markdown 正文。
- 当今天文件不存在时，服务可选择日期不晚于今天的最新有效报告，并把它标为陈旧；页面显示“最近有效报告”。
- 今天文件已经存在但读取失败时，服务返回错误，不用较早报告掩盖故障。
- 没有任何可用文件时，页面显示“今天的新闻报告还没有生成。”。
- 接口、文件或 Markdown 内容异常时，错误只占据 Daily News 区域，Home 的其他内容继续工作。
- 空白文件按空内容处理，不显示一块看似成功但没有正文的报告。

## 参与组件与工作流

**研究 Resident**（「机器人调研 · 研究执行」resident_e9e3b30b9adf，属于 RobotCompany 调研 Lineage lineage_7e2464bbdb60）负责检索、筛选、整理和写出报告。Lineage 中的 `time.reached` 事件源每天触发它，连线任务指向其 Workspace 中的职责说明 `duties/daily_research.md`（decision_5ff0cb51ed84）；确定性的校验、发布和历史记录由同一 Workspace 的 `tools/robottool.py` 完成。**ExecutionService** 为这个 Resident 的工作分配模型执行。**报告目录**默认位于站点共享根下的 `Agents/lineage_7e2464bbdb60/memory/reports`（该 Lineage 的 Lineage Folder），保存通过校验的 Markdown 文件。**Daily News 接口**只选择并读取文件。**Home 前端**只负责状态文字和正文渲染。

一次完整工作按以下方式流动：

1. 定时事件源在计划时间触发，研究 Resident 按职责说明开始当天的调研。
2. Resident 亲自发起至少 16 个检索，其中至少 8 个为中文检索，覆盖指定行业主题并去重来源。
3. Resident 把核实过的新闻写入当天的清单；`robottool.py publish` 检查清单的结构、日期和必要字段，通过后才写出 Markdown 报告。
4. 新闻为 0 条或校验不通过时，Resident 专门再检索一轮；仍不合格则不发布当天报告，并在运行记录中说明。只有通过校验的报告才成为可读取文件。
5. 用户打开 Home 后，Portal 按服务器本地日期查找文件，返回今天的有效报告或符合回退条件的最近报告。
6. 浏览器用共享 Markdown 渲染器显示正文。

生成与展示分开，使报告失败不会拖慢 Home 请求，也让 Portal 避免持有研究 Resident 的模型会话。报告文件属于研究 Resident；Portal 只拥有“选择哪份有效文件并怎样反馈”的读取状态。生成失败由定时任务记录并在下一次运行恢复；页面读取失败由用户重载页面恢复。

## 读取接口

`GET /api/daily-news` 不接受页面日期选择。服务以服务器本地“今天”构造目标文件名 `<日期>__daily_news.md`，在报告目录（默认 `portal_paths.share_root/Agents/lineage_7e2464bbdb60/memory/reports`，可由 `PORTAL_DAILY_NEWS_ROOT` 改变）中查找，并返回以下字段：

| 字段 | 含义 |
| --- | --- |
| `status` | `ok`、`missing` 或 `error` |
| `requested_date` | 本次请求对应的服务器本地日期；只在 `ok` 时返回 |
| `date` | `ok` 时为实际返回报告的文档日期；`missing` 与 `error` 时为请求日期 |
| `stale` | 是否使用了早于 `requested_date` 的最近有效报告；只在 `ok` 时返回 |
| `path` | 服务实际读取或尝试读取的报告路径 |
| `updated_at` | 文件更新时间；只在 `ok` 时返回 |
| `content` | Markdown 正文；`missing` 与 `error` 时为空 |
| `error` | 读取失败的原因；只在 `error` 时返回，页面直接显示 |

服务先检查今天文件。文件存在且可读时返回 `status: ok` 和 `stale: false`。今天文件缺失时，它按文件名日期排序，从不晚于今天的候选中选最新一份，并返回 `stale: true`。今天文件存在但读取异常时返回 `status: error`；候选也不存在时返回 `status: missing`。

`path` 是服务诊断字段，页面不会把主机文件路径当作普通用户可浏览资源。用户对 Daily News 的页面访问权限来自 Portal 登录；研究 Resident 对网络、模型和报告目录的权限由其运行设置与 Workspace 绑定持有。
