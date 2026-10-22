---
kb_id: decision_4bc03b20c30f
type: decision
project: AI员工
created_at: '2026-09-10'
version:
  number: 16
  created_at: '2026-09-10'
  created_by:
    user: ''
    agent: resident_72f9ac2db8f0
  updated_at: '2026-10-17T08:40:00Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_f0be6136f8c8
importance: 5
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/static/workbench_studio.js
  - ${code_root}/management_portal/hatg/resource_actions.py
  - ${code_root}/management_portal/resources/providers/workbench.py
  - ${code_root}/management_portal/workbench_projections/native.py
  - ${code_root}/management_portal/workbench_projections/studio.py
  - ${code_root}/management_portal/workbench_projections/service.py
  related:
  - decision_ad3e21094f14
  - decision_f67ae5e70793
  - decision_4adcc1ff2f2c
  - decision_898b8911a2ff
  - decision_f560803947c6
  supersedes: []
  derived_from: []
---

# 投影工坊：页面实现与 Lineage 反向整理流程

## 工坊与设计师

投影工坊位于 Workbenches 页面（`/workbenches`）中，帮助用户把一个已有的 Lineage 做成可用的工作台页面、持续修改页面，并在需要时根据页面反过来整理 Lineage 的协作流程（下文称“反向整理”）。工坊使用 Portal 公共的工作台框架、面板、左侧图标栏、主题和基础控件，不另建页面框架或导航。

工坊绑定一个普通 Lineage，其中有一个固定的“前端投影设计师”Resident（角色 `designer`），按“工坊 Lineage ID + designer”找到并反复使用。用户可以在 Chat 中与设计师直接对话、纠正需求、继续修改同一页面。Chat 只用于沟通、解释和总结结果；页面源码和反向整理计划不通过聊天正文传递，而是由设计师调用资源动作保存（见下文）。

被做成页面或被整理的目标 Lineage 仍属于用户自己。读取和修改它时，按实际调用者对该 Lineage 的权限检查；能使用工坊不等于能修改目标 Lineage。缺少设计师或绑定时明确报错，不借用其他 Resident 或账号。

## 工作台目录的标题

目录卡片完整呈现工作台标题。长的连续英文、资源标识或混合语言标题按卡片可用宽度换行，不横向溢出或遮挡相邻卡片；打开与设置操作保持可见并可点击。

## 页面实现的权威状态

工作台资源（资源类型 `workbench`）是页面标题、说明、页面源码、绑定、修订号和发布状态的唯一来源。设计师读取当前资源和相关材料，按用户需求直接修改页面代码。服务不从聊天正文中截取 JSON，也不根据设计师的文字判断页面是否已经保存。

生成、优化和反投影的提交者先通过目标工作台的修改权限检查。可见工作流的 Effect 将本次 Event 中明确指定的临时凭证请求交给普通 DeliverMessage；由实际发起人向设计师签发该工作台的 contributor 凭证，绑定投递产生的输入，输入结束即失效。不得扩大到团队、其他工作台或全局，也不创建永久授权。设计师始终以自身 Resident 身份通过资源入口与保存时的权限检查；任务记录中的 created_by 只用于归属和追溯。人工改写且未配置凭证转交的流程必须显式修复绑定，不能默默覆盖人工脚本或重复投递已接纳的输入。

一次自动实现请求（生成任务）记录任务 ID、工作台 ID、设计师、投递给设计师的消息 ID，以及开始时的页面修订号 `base_revision`。设计师完成代码后，通过资源指令 `RunResourceAction` 调用工作台的 `generation.save` 动作，提交 `job_id`、`base_revision`（整数）和允许修改的 `title`、`description`、`html`。该动作：

- 核对调用者就是任务绑定的设计师；
- 核对任务与工作台一致，`base_revision` 与任务开始时的修订号一致，不一致时返回 409；
- 检查页面源码非空，只包含允许的字段；
- 在页面修订号仍为 `base_revision` 时保存草稿，修订号加一；
- 保存一份回执。同一任务重复提交相同结果时返回原回执，提交不同结果时返回 409。

页面是否已保存与设计师是否回复是两件事。设计师这一轮工作结束、并且存在匹配的保存回执时，生成任务才标记完成；设计师只回复文字而没有调用保存动作时，任务以失败结束，回复保留供排查。其他人在此期间修改了页面、修订号已经变化时，保存被拒绝，不会把别人的修改当作设计师的结果。

用户在设计师 Chat 中提出后续修改时，消息按普通对话发送。上一条输入的临时凭证不会延续到新的消息；缺少修改权时应由持有者随新消息或按时长签发，不能借用历史任务权限。设计师读取工作台当前的修订号，通过 `UpdateResource` 或当前任务指定的资源动作保存代码，并说明修改内容和验证结果。

## 实现范围与发布边界

页面 `html` 只包含业务内容片段、局部样式和内联业务脚本。Portal 宿主拥有全局导航、页面框架、主题和基础控件。页面不得创建独立 `html/head/body/nav` 框架、覆盖基础主题或访问 sandbox 禁止的同源能力；交互使用 Projection SDK、正式资源地址和已绑定 HATG 入口。

生成或优化只保存草稿，不自动发布，不启动目标团队实验，不创建业务任务，不修改成员、权限、导航或绑定。用户预览后显式发布当前已保存版本。页面实现缺少必要信息时，设计师通过正常对话提出问题或报告阻断，不提交空页面或假成功。

## 浏览器宿主与请求身份

Portal 工作台支持 HTTPS 与 HTTP 内网地址。宿主的通道身份和操作幂等键统一调用 `PortalUIPrimitives.randomUUID()`，由 `crypto.getRandomValues` 生成 UUID v4；不以浏览器是否提供安全上下文专属的 `crypto.randomUUID` 决定页面能否打开或操作能否提交。

每次创建投影 iframe 都分配新的随机 channel。Projection SDK 以该 channel 内递增且不复用的序号关联请求和响应，允许多个请求同时等待；宿主和 SDK 继续核对消息来源窗口、channel 和请求 ID。计数器只关联一次挂载内的消息，不替代服务端权限校验，也不承担持久操作的幂等身份。

兼容验证覆盖 HTTP 非安全 origin 上的真实页面、沙箱内容和 SDK 往返，以及 WebKit 页面挂载与布局恢复；仅使用 localhost 的浏览器测试不能代表内网 HTTP 环境。

## 工坊操作

| 工坊操作 | 执行方式 | 结果 |
| --- | --- | --- |
| 读取可投影团队 | 授权 HATG 查询与 ResourceService 读取 | 展示真实成员、能力、绑定和版本，不触发模型或业务任务 |
| 生成新工作台 | 稳定 Event／Effect 向同一 designer 投递需求 | designer 直接保存 Workbench 草稿并自然语言总结 |
| 优化已有页面 | 同一 designer 读取当前页面后继续实现 | 保留身份与绑定，按 revision 保存新草稿 |
| 打开设计师对话 | Chat Workspace 打开绑定 designer | 用户持续协作；普通消息不包装为生成协议 |
| 反向整理 Lineage | designer 分析页面、业务规范和当前图 | 结构化计划通过资源动作保存，聊天只解释计划 |
| 审查与应用计划 | 能修改该 Workbench 的用户确认计划哈希后调用公共接口 | 分步保存真实回执；不宣称跨服务原子回滚 |
| 预览与发布 | 页面校验、预览和显式版本发布 | 发布用户选择的已保存版本，不隐含图修改 |

固定的任务说明写在 Lineage 中调用 `DeliverMessage` 的 Effect 里，本次需求和资源引用作为本次输入附在后面。Lineage 中不保存页面源码或页面专用的业务字段。

## 反向整理计划

反向整理计划由设计师通过 `RunResourceAction` 调用工作台的 `reverse.plan.save` 动作（参数 `job_id` 和 `plan`）提交，服务不从设计师的回复中解析计划。动作核对 job、designer 和 Workbench 后，保存计划及哈希；Resident 在 Chat 中用自然语言概述复用关系、风险和待确认步骤。

计划使用 `summary`、`operations`、`reuse`、`risks`、`verification`、`domain_spec_changes` 和有序 `steps`。步骤只包含 `id`、`instruction`、`arguments`，后续步骤可以用 `{"$ref": "<先前步骤 id>.<字段>"}` 引用先前正式回执。

- **计划只做最小的增量添加。** 一份计划最多 30 步，`instruction` 只能是 `CreateResident`、`CreateComponent` 或 `CreateWire`；不能执行任务、授权、删除或替换现有对象。
- **新组件只能是手动入口和消息 Effect。** `CreateComponent` 只创建 `manual.triggered` 事件源，或脚本中每条调用都是 `DeliverMessage` 的 Effect；步骤参数不得包含 `actor` 或 `lineage_id`。可选 `page_update` 包含完整业务片段和 Event 别名映射。模型不得直接应用计划、触发任务、授权、删除成员或发布页面。

能修改该 Workbench 的用户（权限系统判定，见 decision_f67ae5e70793）确认当前计划哈希后才进入应用。应用前核对页面版本、目标图版本和权限；每一步持久化命令、参数、基线与幂等键，随后保存真实回执。部分完成保留已经发生的修改，不伪造回滚。人工修订保留原计划、失败原因、作者和版本历史，不冒充 AI 结果。

## 编辑表单与后台刷新

生成任务完成后，Studio 重新读取该工作台的已保存详情，使编辑器修订号、未编辑的表单字段和预览使用同一份页面数据；不能只凭本地 revision 大于任务 base_revision 就跳过同步。保存回执证明生成已保存，生成前的基线不表示浏览器已经载入该结果。此同步只读取详情，不重新投递生成任务，并继续保留用户尚未提交的字段。

- **用户输入过的字段不被后台刷新覆盖。** 工作台加载完成或观察到生成任务结束时，Studio 用已保存的标题、说明和页面源码重新填写表单；用户输入过、尚未提交的字段保持原值。
- **提交即结束编辑状态。** 交给设计师实现、保存并预览和发布都会提交当前字段；提交后这些字段重新跟随已保存的值，生成完成后显示设计师保存的结果。

字段只有收到用户的输入事件才算编辑中。与上次填写值不同但用户没有输入过的字段（例如初始为空、随生成请求提交的标题）仍跟随刷新，因此生成结束后反向整理读取的是已保存页面。

## 恢复与可观察性

`workbenches.generations` 每条任务一行。任务身份、发起者、工作台/Resident/Lineage/消息关联、状态、时间、版本与计划指纹分列保存；计划、回执、待保存/待应用/待页面操作及必要失败详情使用限定用途的 JSONB。表不保存整份 `data` 文档；写入拒绝未知字段和不一致身份，按工作台、发起者和创建时间索引查询。未完成操作的数据必须随迁移保留，进程重启后接续原操作。

生成任务只记录原始输入、任务状态和资源动作回执。刷新页面后继续观察原来的消息 ID 和同一个任务，不重复投递。设计师给出回复只说明这一轮工作结束；页面是否已经做好，以保存回执为准。

失败、取消、额度不足和版本冲突展示真实状态。若页面草稿已经保存而后续回复失败，界面分别呈现已保存 revision 与会话失败，不能丢弃真实页面变化。生成回复可在技术详情中查看，但不会被当作源码再次载入。展开后的回复使用有明确标签的只读文本框，默认至少八行并允许纵向调整，便于阅读完整阻断原因和复制原文。

页面生成、反向计划、应用回执和验证报告按工坊领域协议关联。直接从设计师 Chat 完成正式资源修改后，Workbenches 通过 ResourceService 与资源变化通知读取最新状态，不依赖某个浏览器按钮回调。

## 页面交互与结果恢复

生成、反投影、连线 Inspector 和回复恢复使用同一条页面流程。生成、分析、确认应用和发布保留实际版本及命令回执；工坊复用固定执行团队的绑定。

连线 Inspector 可用普通点击打开，显示完整 wire ID 与正确的目标 Effect；查看连线不改变图成员或布局。

发布后的输入与回复保留实际 Trigger Path、message、reply ID 及完成状态。重新加载页面恢复同一结果与正文；仅预览输入不发送消息或触发任务。成功状态须来自实际完成的业务结果。验收生成需求描述页面行为，并交由设计师使用当前公开 Projection SDK；不强制未经确认的接口别名或已取消的 Resident invocation_id。

只有关联的生成、反投影和业务输入全部终态后，才清理临时对象；历史结果和回执继续保留。

## 关联设计

- 通用投影与双向演化：`decision_f67ae5e70793`。
- HATG 对象、逐次 prompt 与公共指令：`decision_4adcc1ff2f2c`、`decision_898b8911a2ff`。
- 公共 Workbench 与 Panel Package：`decision_4348ed990621`、`decision_538e155e1102`。
- Chat Workspace：`decision_f560803947c6`。

## 分析失败与结果保留

Workbench 保留原始 `input_error` 供诊断，只向用户显示可读恢复说明，不得渲染 Python 字典。反投影只接受已经保存的非空页面；服务端必须拒绝空页面，Studio 按同一条件禁用分析入口。
