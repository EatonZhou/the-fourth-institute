---
kb_id: decision_865ac8ebef25
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 14
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T12:15:15Z'
  updated_by:
    user: ''
    agent: resident_6199a7a36480
importance: 8
metaness: 2
ai_generated: true
links:
  source:
  - ${code_root}/management_portal/portal_feature_switches.py
  - ${share_root}/Agents/lineage_ed619225d417/residents/resident_e267c94e7252/workspace/duties/pulse.md
  - ${share_root}/Agents/lineage_ed619225d417/residents/resident_e267c94e7252/workspace/tools/pulsetool.py
  - ${code_root}/management_portal/portal_pulse_backend.py
  - ${code_root}/management_portal/pulse_publication_store.py
  - ${code_root}/management_portal/web_isa.py
  - ${code_root}/management_portal/static/home.js
  - ${code_root}/management_portal/static/portal_home_components.js
  - ${code_root}/management_portal/static/portal_workbench.js
  related:
  - decision_3e063aac15b3
  - decision_00546e28e1e0
  supersedes: []
  derived_from: []
---
# Portal Pulse 与个人动态发布

Portal Pulse 把项目知识库和 Agent 工作在一天、一周或一个月内发生的变化整理成可读摘要，让团队不用逐个翻看项目也能知道最近发生了什么。Home 首页的 **My Pulse / Publish** 区域有两个子面板：**Knowledge Pulse** 展示团队都能看到的内容，**Personal Pulse** 展示当前用户自己的变化条目，用户可以把其中的条目公开给团队。

本 Design 说明这两个面板、摘要从哪里来、公开和撤回怎样进行，以及谁可以做什么。面板在 Home 中的位置、折叠和关闭由共享 Workbench 负责（decision_00546e28e1e0）。

## 页面上的两个视图

**时间范围标签页**是 Day、Week、Month 三个按钮，分别查看日、周、月摘要；首次打开选择 Day。两个子面板各自保存当前范围，切换一方不改变另一方，也不刷新 Home 其他区域。

Knowledge Pulse 包含：

- 工具栏：时间范围按钮、摘要周期和生成时间。
- Overview：一段面向所有项目变化的总览文字。
- Project changes：按项目排列的变化列表，页面最多显示 5 项。每项给出变化摘要、状态计数，以及最多 2 个可点击的证据标签。
- Published：用户已公开的个人条目，页面最多显示 4 项。

**证据标签**是通往知识条目、文件或 Portal 资源的短链接。它帮助读者核对摘要来源；目标资源是否可读仍由目标资源自己的权限决定。

Personal Pulse 包含当前用户在所选周期内的摘要和最多 10 条个人条目。每条未公开内容有 **Publish**（发布）按钮；已经公开的内容有 **Retract**（撤回）按钮。发布把该条目加入团队可见列表，撤回则从公开列表移除，个人来源仍保留。

## 设计点

- Pulse 功能在前端配置中关闭时，两个子面板在原位置显示不可用状态，Workbench 布局保持不变。
- Home 用当前用户的 `user_id` 读取 Pulse，展示公共 Knowledge Pulse 与该用户的 Personal Pulse。指令授权、个人来源所有权和发布身份的唯一合同见 decision_cc96fce57ab6；Pulse 后端不另存用户名单或公开开关。
- 日、周、月各自保持对应摘要。Knowledge Pulse 和 Personal Pulse 的范围独立切换。
- 两个子面板可以分别拖拽、分组、折叠、关闭和恢复；这些布局操作只改变布局，时间范围和已加载内容保持不变。
- 页面直接显示摘要和有限列表，需要进一步阅读时点击证据链接。
- 发布只作用于当前用户自己的个人条目。服务按提交的周期、周期键和条目 ID 从个人摘要中重新找到条目，并确认所有者是当前用户后才公开。
- 对同一来源条目重复发布得到同一个公开记录，不制造重复项。
- 只有公开记录的所有者可以撤回自己的条目。
- 发布或撤回成功后，当前范围重新渲染并显示短暂成功提示；失败时保留原页面内容并显示错误提示。

## 参与的部分

| 部分 | 负责 |
| --- | --- |
| Pulse Resident | Portal System Lineage（`lineage_ed619225d417`）的「Portal Pulse · 日周月摘要」Function Block 中的 Resident（`resident_e267c94e7252`），按职责说明 `duties/pulse.md` 阅读变化、亲自写出日、周、月摘要；Workspace 中的 `tools/pulsetool.py` 负责列出变化和发布 |
| Pulse 数据目录（Portal System 的 `memory/pulse`） | 保存摘要索引 `index.json` 和各周期摘要 |
| 公开记录表（PostgreSQL `portal_pulse.publications`） | 每条公开记录一行，保存所有者、来源周期与条目、正文、Project、证据路径和公开时间 |
| Portal Pulse 后端（`portal_pulse_backend.py`） | 读取数据目录，合并出页面需要的内容；执行公开和撤回 |
| Home 前端（`home.js`） | 切换时间范围、裁剪列表长度、显示操作结果 |

生成器写出的摘要回答“这一周期发生了什么”；公开记录回答“谁把哪一条个人条目公开了”。浏览器只保存两个子面板各自选中的时间范围和已加载的内容。

## 摘要怎样产生和到达页面

1. Portal System 的 Pulse Function Block 内保留三个定时事件源（`time.reached`），按香港时间触发：每天 00:20 写前一天的日摘要（最多延迟 15 分钟），每周一 00:45 写周摘要（最多延迟 20 分钟），每月 1 日 01:10 写月摘要（最多延迟 30 分钟）。事件以 Lineage 的自动运行身份投递给 Pulse Resident，连线任务指向它的职责说明；手动事件源“手动 · 立即汇总”用于补写某一期，期别和日期从触发说明中读取。
2. 日任务用 `pulsetool.py changes` 列出 `${share_root}/Projects` 下各项目知识库的变化，Resident 阅读有实质内容的原文后写出全局进展、最急的事、各项目与各人的进展；周、月任务阅读期间的日摘要，写趋势和方向变化。
3. 摘要中的证据路径只能取自变化清单；个人进展只写人（不含 `resident_*`），只给本人看。
4. `pulsetool.py publish` 先写摘要文件再更新 `index.json`，所以 Portal 读到的总是完整的一份摘要。
5. Home 读取 Pulse 时，后端从 `index.json` 找到每个周期最新的摘要，合并公开记录和当前用户的个人条目，一次返回。
6. 前端把返回内容裁剪成总览、项目列表和个人列表。

Home 读取的是预先生成的文件，所以打开页面很快，浏览器也不需要访问项目目录。某次生成失败时，`index.json` 仍指向上一份完整摘要，页面继续显示它。

## 流程开关与数据所有权

Pulse 的三个定时 Event、一个手动 Event、原有 Pulse Resident 及四条投递连线共同属于具名 Function Block。开关直接使用 `PauseComponent`（`paused: true|false`）的持久运行状态。关闭一个定时 Event 后，该周期不再产生新触发；关闭三个定时 Event 停止自动生成，保留手动 Event 可补写一期。手动 Event 关闭后也不能手动触发。已被 Resident 接收的任务可以完成；关闭 Event 不撤销已接收的模型执行。Function Block 的封装保持上述 Event 和连线语义。

暂停生成不删除已有摘要，也不隐藏 Home Pulse。读取、个人发布和撤回继续使用已有文件及 PostgreSQL `portal_pulse.publications`；公开权限仍由权限系统决定（decision_b820faf42218），个人所有权仍由来源条目决定。

默认摘要根目录为 Portal System canonical Lineage Folder 的 `memory/pulse`，可用 `PORTAL_PULSE_ROOT` 为隔离环境指定根目录。后端和 Resident 的发布工具必须指向同一根目录。迁移已有流程时保留 Resident 身份、四个 Event 身份、运行游标及暂停状态；Lineage 子图迁移负责身份和工作区，摘要历史需要另行完整复制、校验并最后提交索引。索引中的历史路径与新根目录同步，摘要正文、条目 ID、公开记录和所有者保持不变。迁移工具在所有摘要和索引完整落盘后，原子写入目标目录的 `.portal-system-migration.json` 完成凭据。凭据出现前，有既有历史的部署继续从原根目录读取，提前重新加载共同源码不会造成空白；凭据出现后只读取 System 数据，目标数据损坏时不会恢复已经过时的旧摘要。全新部署没有原摘要时直接使用 System 数据目录，显式 `PORTAL_PULSE_ROOT` 始终优先。

### 本站开关与可安装映射

Portal 管理中的 Pulse 开关控制本站明确配置的事件源组。配置位于 `deployment.pulse.lineage_id` 与 `event_source_ids`；部署可以指向自己的普通 Lineage，代码不据标题或实验室 ID 猜测目标。组内只要仍有开放的事件源，状态就是开启；混合状态同时标记部分暂停。

发行工程带出 Pulse 代码，只保证读取和发布能力可用。只有本站配置指向所选 Lineage 中实际存在、归属和类型正确且唯一的事件源，才有可初始化的流程开关。缺配置、未选择该 Lineage 或缺组件返回 `needs_configuration`；重复、归属错误或类型错误返回 `conflict`。这些诊断不产生“默认关闭已生效”的结论。

开关映射由 `portal_feature_switches.installation_mapping` 提供，合同版本为 `tfi.portal-feature-binding.v1`。它显式接收站点配置、所选普通 Lineage ID 与发行定义行，返回可序列化绑定、最小站点配置及诊断；不读取源站运行状态，不设置启用默认值。`ready` 仅表示映射可用，流程运行还需自己的身份、依赖与准入条件。发行 catalog、快照和 manifest 继续由发行模块唯一维护（decision_15aab18c0ab3）。

目标安装器核验目标定义后使用原生组件运行状态初始化；升级保留目标站已有映射与暂停值，不能用包内默认值覆盖本站选择。Event Source 的暂停值由 `event_source_progress.checkpoint_json` 保存，管理端通过 `component_runtime.views/set_paused` 读写；恢复沿原生重建观察基线与时间计划规则执行，不另存启用布尔值。初始化适配与升级事务由安装器负责，映射接口本身不执行安装。

管理写入在一次请求中固定同一映射，写前验证全组。部分调用失败时尝试恢复已调用组件的原值；恢复失败明确要求重读实际状态，不能宣称整组成功。权限错误仍交给权限入口，关闭不撤销既有任务或删除历史摘要。

## 接口与限制

### 读取

Home 通过 HATG 指令 `GetPulse`（decision_cc96fce57ab6）读取 Pulse，传入当前用户的 `user_id`。一次读取返回 `daily`、`weekly`、`monthly` 三个周期的公共摘要和当前用户个人条目，以及公开记录。两个子面板在这份结果内切换 Day、Week、Month，切换时不再请求服务端。

长度和数量限制：

| 内容 | 后端最多返回 | 页面最多显示 |
| --- | --- | --- |
| 项目变化 | 12 个项目 | 5 个 |
| 公开条目 | 30 条 | 4 条 |
| 个人条目 | — | 10 条 |
| 每条的证据路径 | 8 个 | 2 个 |

总览最多 900 个字符，每个项目摘要最多 420 个字符，每条个人或公开条目最多 260 个字符。证据路径只接受站点 `portal_paths.share_root/Projects` 和 `portal_paths.staging_root` 下的路径；用户点开证据时，目标资源按自己的权限决定是否可读。

### 公开与撤回

Home 把当前条目的 `source_period`、`source_key`、`item_id` 和目标 `published` 状态交给 `PublishPulseItem`。参数、来源重新核对、稳定公开记录身份及撤回所有权以 decision_cc96fce57ab6 为唯一指令合同。成功时页面使用返回的完整 `pulse` 重绘当前范围并提示结果；失败时保留原内容并显示错误。

### 写入与并发

公开和撤回各在一个数据库事务中完成，事务持有同一把 `publications` 锁，先读出现有记录再写入，并发操作按提交顺序生效。摘要文件读到损坏或缺失时，后端返回空结果或错误，页面保留上一次成功的内容并提示错误。
