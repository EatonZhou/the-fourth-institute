---
kb_id: decision_b77fde81e7a0
type: decision
project: AI员工
created_at: '2026-10-18'
version:
  number: 14
  created_at: '2026-10-18'
  created_by:
    user: ''
    agent: resident_7765faecc45a
  updated_at: '2026-10-19T21:35:54Z'
  updated_by:
    user: ''
    agent: resident_6199a7a36480
importance: 5
ai_generated: false
links:
  source: []
  related:
  - decision_44cf587c1721
  supersedes: []
  derived_from: []
---

# HATG ISA：Family 内部机制指令

Family 内部机制指令是一个 Family 自己运转时使用的步骤：Router 转交门面请求，steward 撰写和发布进展简报、汇总门面摘要，Family 内部的 Effect 机械检查成员摘要和失败任务。调用者是 Family 自己的角色和组件，默认方案把它们放在免检或门面权限下，执行时再核对调用者属于本 Family。在 Family 之外创建、解除、删除和修改 Family 用 Family 类指令。Family 的整体设计、角色和内部工作流见 decision_44cf587c1721，工作摘要的存储与维护见 decision_d19b9909a674；所有指令共同遵守的调用格式、执行流程和错误语义见 decision_898b8911a2ff。

## 指令一览

| 指令 | 改变状态 | 作用 | 默认方案要求 |
| --- | --- | --- | --- |
| `DispatchFamilyTask` | 是 | 按 Family 当前场景模板派发精简任务 | 对 `resident_id` 有 @contributor |
| `ResolveFamilyRequest` | 是 | 将 Steward 已完成的路由复核成果归档到原请求，保留原失败回执且不重新执行 | 对 `resident_id` 有 @contributor |
| `RelayFamilyMessage` | 是 | 转交门面原请求，成员周额度低于 10% 时先等待 steward 复核 | 对 `resident_id` 有 @contributor |
| `CheckFamilyProgress` | 是 | 登记门面收到的工作轮次，达到配置间隔后请 Steward 撰写简报；同一输入只计一次 | 对 `lineage_id` 有 @contributor |
| `PublishFamilyProgress` | 是 | Steward 保存最多 500 字的简报并更新门面摘要，不向成员群发；按相关性另行定向通知 | 免检 |
| `CheckFamilyRoleAvailability` | 是 | 维护 Router／Steward 接单并返回已有迁移状态；不查额度、不发起迁移；门面关闭暂停 | 对 `lineage_id` 有 @contributor |
| `CheckFamilyRecovery` | 是 | 核验 Family 原任务的恢复结果，持续交给 steward 定位修复；无法恢复或 steward 失败时由门面通知实际创立者 | 对 `lineage_id` 有 @contributor |
| `CheckResidentWorkSummary` | 是 | Effect 机械检查成功轮次并挂下一轮提醒 | 免检 |
| `GetFamilySummaryInputs` | 否 | 读取按小时汇总的材料与来源版本 | 免检 |

`ResolveFamilyRequest` 是 Family 成果归档入口：对 `resident_id` 要求 @contributor，并由领域校验本 Family Steward 或门面管理权。结果为 `status=archived`、原 `message_id`、`steward_message_id` 和真实 `reply_message_ids`。它不投递新业务、不重开失败输入；前提与来源校验见 `decision_a64b3ea2c3c5`。

## 角色可用性

`CheckFamilyRoleAvailability(lineage_id, family_id)` 供独立 `role_availability` Effect 调用。本 Family 门面、Steward 或有 Lineage 管理权限的调用者可执行；自动 Effect 仅在属于该 Family 且参数指向同一 Family 时使用门面身份。

它先核验门面是否暂停，再恢复 Router／Steward 接单，返回已有迁移状态。返回 `family_id`、`paused`、`waiting_count`，未暂停时另有 `roles`（resident_id、status、reason、intake）。waiting_count 统计已有迁移记录；本指令不读取额度、不发起迁移，全程无需模型任务。

触发入口为两角色的 `resident.state_changed(intake)` 及每 60 秒 timer 接单补查。额度随待执行原输入进入平台准入检查；不另设 usage Event。所有 Resident 共用 ExecutionService 额度准入；Router／Steward 执行前另按既有策略决定有限迁移。具体职责、执行入口和脚本见角色工作流。

## 派发场景任务

Event 类型、Watch 对象、触发参数、Route 到 Effect 的连线及各场景的实际 prompt 字段，统一见 Family 角色工作流。本篇维护指令参数和执行语义，不另存一份场景表。

### `DispatchFamilyTask`

以门面 resident_id 和 trigger 指向 Family 的当前工作场景。仅本 Family 门面、Router、Steward 或 Lineage 管理者可以调用；目标内部角色由执行契约决定。

- trigger 为 route、queue、review、summary、progress、recruit、route_review、recovery、quota、organization。
- 可选 facts 为本轮结构化事实；source_message_id 绑定原门面消息，route 场景必需。平台读取该消息的作者、原文、附件引用及引用信息，覆盖对应事实，不让调用者伪造原请求。
- 可选 event 只提取事件标识、类型、主体、队列指标等本轮必要信息，不透传完整运行快照。
- 可选 message_id 为此次派发的稳定消息 ID，用于去重；与原请求 source_message_id 不同。
- 从对应 Route 的 family_tasks 参数读取 guide/prompt；物化工作区 family-work-guide.md，渲染当前事实、指南路径和版本。完整指南仅由统一上下文构造器在会话首轮注入。
- 任务以门面为发送者、调用者为发起者投给对应内部角色，返回投递回执。内部角色不因此进入发起者的直接会话列表；不自动加载成员摘要、状态和往来。

`trigger` 是任务场景，不是 Event 类型。`DispatchFamilyTask` 自动为 facts 加入 facade、lineage_id、family_id；source_message_id 另映射为 facts.message_id。event 只保留顶层 id、event_id、type、subject_resident_id，以及 payload 中的 resident_id、message_id、queue_count、pressure_metric、pressure_threshold、pressure_value、oldest_wait_seconds，并把后者平铺到 facts.event；不存在的字段不补造。

当前 Effect 也可通过服务分支派发：`RelayFamilyMessage` 引出 recruit/quota，`CheckFamilyRecovery` 引出 recovery，`CheckFamilyProgress` 引出 progress，组织变更与门面 info 引出 organization。这些路径用 `tell_steward` 读取同一场景模板，但只渲染各调用方传入的 task_facts，不自动补充 `DispatchFamilyTask` 的三个公共字段；不能把配置所在连线误认为独立触发 Event。

## 转交门面请求

### `RelayFamilyMessage`

按 Family Router 的选择转交门面原请求；当前成员和本 Family Steward 都是合法承接者，组织工作归 Steward，并在成员周额度低于 10% 时进行执行前复核（decision_a64b3ea2c3c5）。

- 参数：`resident_id`（门面）、`message_id`（原请求）、`member_id`（所选成员）；可选 `quota_reviewed`、`scope_gap`、`scope_reviewed`、`reassign`、`reason`。
- `scope_gap` 使请求等待 steward 安排职责；只有本 Family steward 可以用 `scope_reviewed=true` 或 `quota_reviewed=true` 并填写理由放行。`reassign=true` 显式改派，原成员正在执行时须先停止；改派记录保留来源。
- 默认先检查成员实际绑定账号的周额度；低于 10% 时保留门面请求，并以门面身份向 steward 发出一次判断请求。返回 status=waiting_for_quota_review 和 steward_message_id，成员尚未收到原请求。
- 只有本 Family steward 能以 quota_reviewed=true 并填写判断理由放行已经登记的复核。它先评估工作强度，必要时等待成员迁移完成；放行只转交原请求，不新造任务。
- 正常或批准的转交返回 status=relayed、member_id 和成员上的 message_id；原消息内容、原发送者和附件访问权限保持，门面转交关联仍是对外投影的真值。
- 同一门面请求的选择和投递持久保存；重复调用返回原回执；改为另一成员必须显式改派，已取消请求返回 withdrawn，不再放行。该指令只处理已到达门面的请求，不替代普通消息投递。

## 进展简报与故障监督

`CheckFamilyProgress`、`PublishFamilyProgress` 与 `CheckFamilyRecovery` 的调用时机、批次和升级规则见 Family 运行维护 的进展简报与持续监督。三条指令都以 `lineage_id` 和 `family_id` 指向一个 Family：

- `CheckFamilyProgress`：仅保留显式调用及旧批次兼容；当前 Family 自动运行图不调用。显式调用时记录轮次，达到 `every_turns`（默认 20）时登记待写批次并请 Steward 撰写；结果是计数和本次请求的批次。
- `PublishFamilyProgress`：只允许本 Family Steward 提交对应批次。简报正文 1–500 字，并带九字段门面工作摘要、`expected_revision` 和 `GetFamilySummaryInputs` 的 `source_versions`；只保存简报和门面摘要，不投递成员 info。由 Steward 按相关性另用 DeliverMessage 定向通知；同批次同内容重试返回同一结果，旧部分投递批次也不继续群发。
- `CheckFamilyRecovery`：监督 Effect 把成员、Router 或 steward 的失败以及消息到达后的接单阻塞交给它；定时复查同时检查关闭成员的当前队列，不扫描历史失败。Steward 按原请求恢复接单或修复故障，排队中的原输入不重复投递。Router/Steward 自身接单由 CheckFamilyRoleAvailability 维护，额度迁移由待执行输入的准入检查触发；关闭门面返回 paused 并暂停自维护。未解决任务继续跟进；Steward 用 `escalate_reason` 报告无法继续时，由门面通知实际的真人创立者。取消、删除和已准入执行仍受保留。

## 工作摘要的机械检查与汇总材料

`CheckResidentWorkSummary` 接收 resident_id、message_id，仅允许本 Family 门面、Steward 或本人执行。Effect 在成功完成后比较覆盖记录；失败、取消、纯维护轮次及 Router／Steward 全部跳过。漏写时合并一条 attach_to_next 提醒。已覆盖时另返回 consolidation_due，完成事件 Effect 仅在其为真时尝试短通知 Steward。status 为 covered、skipped、facade、reminder_pending 或 reminder_attached。

`GetFamilySummaryInputs` 接收门面 resident_id 和可选 overview_only，仅本 Family 门面和 Steward 可读。默认返回 title、goal、members、previous_summary、source_versions、expected_revision、needs_update、facade_needs_update、consolidation_due。后者仅列到期业务成员的 resident_id、revision、level、reason；排除 Router／Steward，不带成员各层全文；Steward 按需读 GetResidentSummaries include_maintenance=true。内层门面不进入外层归纳待办。

overview_only=true 仅返回门面 ID、是否需要更新、归纳待办和 notification，不返回成员全文、门面旧摘要或来源列表。notification 是可交给 DeliverMessage 的短入口，同小时同材料复用消息 ID；忙碌/排队时不投递，下一小时继续检查。needs_update 为门面来源变化或存在到期待办；私人 Family 不因门面变化触发小时汇总，成员归纳仍有效。成员来源版本考虑内容、缺失、标题、名单和目标。

Steward 先归纳到期成员，再读取所需 Family 材料汇总门面；材料缺失标为未覆盖，不读取成员历史。归纳及门面写入均使用 UpdateResidentWorkSummary，权限、总量、未决事项保护及版本协议见 decision_d19b9909a674。

## 参数名契约

下表列出 arguments 内的顶层参数名；必填条件、类型和枚举以 GetInstructionSchema 为准。

| 指令名 | arguments 参数名 |
| --- | --- |
| `DispatchFamilyTask` | `resident_id`、`trigger`、`facts`、`source_message_id`、`message_id`、`event` |
| `ResolveFamilyRequest` | `resident_id`、`message_id`、`steward_message_id`、`reason` |
| `RelayFamilyMessage` | `resident_id`、`message_id`、`member_id`、`quota_reviewed`、`scope_gap`、`scope_reviewed`、`reassign`、`reason` |
| `CheckFamilyProgress` | `lineage_id`、`family_id`、`event`、`every_turns` |
| `PublishFamilyProgress` | `lineage_id`、`family_id`、`batch_id`、`report`、`summary`、`expected_revision`、`source_versions` |
| `CheckFamilyRoleAvailability` | `lineage_id`、`family_id` |
| `CheckFamilyRecovery` | `lineage_id`、`family_id`、`event`、`escalate_reason` |
| `CheckResidentWorkSummary` | `resident_id`、`message_id` |
| `GetFamilySummaryInputs` | `resident_id`、`overview_only` |

当前 Family 只保留每小时的按需摘要检查，成员完成事件补查自身覆盖及到期归纳。Router／Steward 均不写自身摘要；GetFamilySummaryInputs 的归纳清单仅包含业务成员。门面来源变化仍由同一 summary 任务汇总，无自动输入批次简报。
