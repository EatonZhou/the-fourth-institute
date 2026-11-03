---
kb_id: decision_a64b3ea2c3c5
type: decision
project: AI员工
created_at: '2026-10-19'
version:
  number: 21
  created_at: "2026-10-19"
  created_by:
    user: ""
    agent: "resident_4b1da6140cf2"
  updated_at: "2026-11-03T11:50:44Z"
  updated_by:
    user: ""
    agent: "resident_2e9ea0072551"
importance: 8
ai_generated: true
links:
  source:
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/society_service/family_supervision.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/society_service/family_quota.py
  related:
  - decision_44cf587c1721
  - decision_89b9df038a95
  - decision_d19b9909a674
  - decision_c47a13e8b962
  - decision_15f7df737ed5
  - plan_2a5bca6bf011
  supersedes: []
  derived_from:
  - decision_44cf587c1721
metaness: 3
---

# Family 运行维护：记忆、队列与故障恢复

Family 的基础可用性由平台监督，Steward 处理需要判断的组织问题；Router 负责选人。巡检读取状态，只有确有待办才派发精简任务。成员的局部故障不等于整个 Family 不可用。

[Family 主文](/resource/knowledge?item=decision_44cf587c1721) 定义成员与生命周期；[角色工作流](/resource/knowledge?item=decision_89b9df038a95) 列出实际 Event、Route、Effect、阈值及 prompt。本文定义任务交给 Steward 后应如何处理、哪些状态由平台负责，以及当前保障的边界。

## 工作记忆

**业务成员写近期事实，Steward 做阶段和长期归纳。** 仅业务成员通过 UpdateResidentWorkSummary 维护自己的近期层；Router 和 Steward 均不维护自身工作摘要，不参与自身摘要覆盖检查、提醒和归纳。本 Family Steward 消费已读取的近期事实，替换阶段及必要时长期层。归纳保留有效职责、成果、未解决事项和资料入口，省略已经被结论覆盖的过程。项目级长期记录独立增量保存。

[Resident ISA](/resource/knowledge?item=decision_d19b9909a674) 唯一定义摘要字段、时间层、总字符预算、归纳阈值、revision、覆盖登记与提交冲突协议。各层合计受总预算约束；未决承诺、阻塞和重要约束不能因时间久而消失。信息取舍交给 Agent，不能通过删除未决事项来凑预算。

业务成员成功完成事件检查原输入是否已经登记覆盖。遗漏时以稳定消息 ID 合并一条 attach_to_next 提醒，挂到该成员下次工作；失败、取消、纯维护及提醒自身不形成循环。已有摘要且确无新增时可以只登记已检查的输入，不改内容版本。Steward 忙碌时推迟归纳，由小时检查补偿。

同一稳定任务 ID 的派发以首次保存的业务请求与消息快照为准。Family 派发入口每次核验当前调用者权限，首次投递前保存目标、任务类型、规范化事实、业务关联及当时的文本和发起人；再次派发同一请求复用原快照和已接收回执，不因调用者、模板或 JSON 对象键顺序变化改写消息，不重开已完成、失败或取消的输入。同 ID 改用其他目标、任务类型、事实或关联时拒绝。消息内容与输入状态继续由原受理领域维护，派发意图不构成第二个执行队列。

升级前已接收、没有请求快照的任务，只在业务关联、发送者与含事实模板生成的原消息能精确核对时登记快照；模板中明确的 guide_version 占位可匹配并保留原十二位十六进制指南摘要，任务事实、正文常量和指南路径仍须逐字相符，不能把任意摘要或正文变化当作版本变化；无法确定时保留原件并拒绝，不能从任意提示词猜测请求或放宽不可变消息检查。新的任务 ID 使用当前模板。

小时 Effect 先检查 Steward 空闲且无排队，再读取 GetFamilySummaryInputs 的概览；有成员到期归纳或门面来源变化时才派发 summary。通知不附成员全文；消息 ID 只由材料（成员与门面的来源版本、到期清单）决定，同材料始终复用同一 ID。按统一派发幂等，已完成、失败或被人停止的同材料任务不会重开，材料变化后才派新任务，因此停掉一次摘要任务不会每小时重派。Steward 先处理到期层，再以 source_versions 和 expected_revision 更新门面。私人 Family 维护成员时间层，但不做小时门面汇总；嵌套 Family 只读取内层门面投影，外层不改内层成员时间层。

摘要维护是本 Family 的内部工作。系统生成的 `summary_check`（业务成员 `resident.work_finished`）以及 `summary_rollup`／`memory_rollup`（`time.reached`）调用 `DispatchFamilyTask(trigger="summary")` 时，以所属 Family 的门面为调用主体；不沿用完成事件中的外部发起者。绑定只接受本门面目标和材料生成的稳定消息 ID，不接受自带 facts、原业务消息关联或其他任务类型；所属门面必须仍在同一 Lineage、同一 Family 担任门面。普通工作流、其他组件和外部直接调用不获得这项能力，仍按原调用主体校验权限。派发继续经过正常 ISA 权限、任务快照、受理幂等和取消检查；Effect 回执记录本次实际调用主体，原事件的发起者审计不变。失败流程保留历史记录，后续成员完成或小时检查按当前材料补查。

工作查看、搜索与路由使用同一份时间层投影。Family 内摘要不由全局 Summary Resident 续写；后者只维护独立 Resident。加入 Family 撤销全局写作认领；移出保留内容与版本，由全局写作者从已有内容续写。归属和版本在认领、证据读取与提交时复核。

## 门面汇总与简报兼容

只保留每小时的按需 summary 检查；移除门面累计 20 个输入的自动 progress Effect 与 Route。小时任务先归纳到期业务成员，再在来源变化时更新门面，不另发重复简报。这样也能覆盖自主工作、低消息量 Family 的到期归纳。归纳只因新工作或容量到期，团队停工后小时检查不再派发。

门面摘要概述成果、进行中事项和阻塞，缺失资料明确标记，不把待办写成完成。它不向全体成员发送 info；确有成员需要行动时才定向通知。

`CheckFamilyProgress`／`PublishFamilyProgress` 保留显式调用和旧批次收尾兼容，默认运行图不再调用。旧账本保留；本 Family Steward 可提交对应批次，report 与随附门面九字段摘要各不超过 500 字符，带 source_versions 和 expected_revision；冲突后重读，同内容重试不重复写入，也不恢复广播。

## 队列与通信整理

队列事件请求一次评估，**不自动招募**。事件阈值、从未超限到超限的触发语义及传入指标见角色工作流；指标只是触发时观测，不能替代当前 Mailbox。

Steward 的处理顺序：

1. 读取 GetMailbox、ListResidentStates，区分重复请求、可一起处理的同一事项、依赖等待、短暂忙碌与职责过宽。
2. 在权限允许时用 MergeMailboxItems、ReorderMailbox 整理尚未执行的相关请求。保留原作者、原文、独立诉求和取消语义；已领取输入不变，info 不参与合并。合并减少轮次，不表示实际工作量消失，也不能为降低队列数字撤销有效请求。
3. 重读整理后的 Mailbox，按剩余工作量、可并行性、既有职责和交接成本决定串行、分流或增员。维持串行也是有效结果，摘要中保留依据，必要时安排复查。
4. 新成员通过 CreateResident/ForkResident 与 UpdateFamily 的归属契约加入；门面原请求用 RelayFamilyMessage 的改派协议转交，其他输入明确交接，避免重复执行。私人 Family 的组织变更须经所有者确认。

Mailbox 操作和授权由 [Mailbox Design](/resource/knowledge?item=decision_15f7df737ed5) 定义，整理不会扩大原发送者权限。

对反复发送无行动价值消息的成员，Steward 可依据具体事例定向提醒：日常进展写摘要或共享产物；获取必要信息、重要运行影响、交接和约定完成流程才需要通知。没有新情况不重复催告，不广播批评，不要求逐条确认。

## 原输入恢复

### 门面路由失败的定向恢复

门面尚未转交、原请求处于 failed 时，修复失败原因后，通过 `DeliverMessage` 向同一门面发送正文仅为 `continue`、`message.quote.message_id` 指向原请求的恢复命令。本 Family Steward 应明确引用目标；原发送者或能管理门面的主体也可使用该入口。无引用的 continue 只选择发送者自己的请求，存在多个候选时要求明确引用，不批量恢复历史失败业务。

恢复命令是控制输入，不替换业务输入：原消息 ID、作者、正文和附件保持不变，平台保存恢复意图，以控制输入 ID 形成一次独立的路由 occurrence，再重新开放原请求。重放同一控制输入只补完已保存意图；原请求已排队、运行或完成时只观察结果，不另起执行。已经转交且失败的请求沿当前承接关系恢复成员原输入，仍受检查点与权限约束。

取消优先于恢复：原请求或当前成员副本已取消时，continue 只展示取消状态，不重新入队。保存恢复意图后发生撤回，中断续跑也不得重开或再派发路由。恢复命令自身被取消后不得继续处理。取消后的新业务意图须另行明确提交。

`RelayFamilyMessage` 负责当前有效请求的转交，不隐式重开 failed；`RequeueMessage` 负责实际执行者的轮次，门面没有可放回的执行轮次。恢复进入 routing／queued／running 只表示工作已继续，只有原请求关联的实际执行完成及真实回复才能证明成功；修复回执本身不构成业务完成。

### 成员执行与 Session 恢复

恢复使用原消息 ID、原文、发送者与已保存的输出，不发送替代任务。RestartSession 默认在成功后恢复最近失败输入；取消迁移、初始化失败、明确停止、已经完成或已排队的输入不得重复放回。用户取消本次失败恢复的选择持久保存。详细迁移、接单持有与取消协议由 [Session 重启 Design](/resource/knowledge?item=decision_c47a13e8b962) 定义。

Steward 先查当前输入、账号与 Family.auto_migrate_priority，再按账号种类策略选择已登记 Agent 账号，用 compact 交接迁移，并检查 GetSessionRestart 与原输入状态。环境已经修复但原输入仍失败时，显式 RequeueMessage 继续原输入。

平台每十分钟复查环境原因导致的失败输入及准入前因额度策略挂起的输入。网络未恢复时不消耗恢复次数；原账号额度恢复后继续原输入。后台对同输入的自动恢复及备用迁移有持久的次数限制，达到上限后不再盲试。显式 RequeueMessage 或显式 RestartSession 按正常权限执行，不受后台计数限制，也不重置后台计数。

### 既有组织成果归档

对于未转交、路由失败的原请求，若本 Family Steward 的 route_review 输入已经完成原组织目标，授权恢复者调用 `ResolveFamilyRequest(resident_id, message_id, steward_message_id, reason)`。允许本 Family Steward 或具门面管理权的主体；原作者权限不自动扩展为归档权。

领域读取持久消息和输入回执，校验复核来源为本门面、本 Family 当前 Steward、原请求之后的输入，且不可变 business_ref 绑定同一原请求。旧版 `duty/family-route_review` 仅接受本门面签发的存储输入中唯一 JSON facts 对象，精确匹配 facade、lineage_id、family_id、message_id；不能用调用者提交的文本或任意完成消息代替来源。新派发使用 `family-review/<facade>/message/<原ID>`，不解析 prompt 取关联。

复核输入必须 completed，并有属于该输入、由 Steward 实际写出的 agent 回复。调用者在 reason 中审计说明成果如何满足组织请求；平台校验来源和终态，不替代对成果含义的判断。取消、仍排队／运行、其他承接关系、未完成或无回复的来源、跨 Family／不相关来源均拒绝。同一来源重复归档幂等，不创建或执行新任务。

归档只在 Family 的原请求关联账本保存真实来源、操作者、理由、时间、回复 ID 和原失败回执。原消息 ID、作者、时间、正文及原失败回执保持不变；GetInputState/GetMessage 沿已有回复投影展示既有完成成果。CheckFamilyRecovery 复查原 incident 为 resolved，包括先前的 missing_creator；不得从 Resident 创建链推造人类创立者。归档不自动填写 human_familizer_id，不把修复轮次“受理”当业务完成。

## 持续监督与关闭接单

成员、Router、Steward 的失败信号和路由链失败信号进入 CheckFamilyRecovery；每分钟机械复查及 Steward 完成事件核对当前状态。登记按原门面请求定位，没有门面请求时按 Resident 与消息 ID 定位。延迟事件对应的输入已经完成就结案；改派后跟随现接手者，历史 failed/continue 副本不再次执行。

成员消息到达时检查接单阻塞；周期检查当前队列，覆盖消息到达后才关闭或事件漏送。每个关闭成员取一个有效阻塞输入，同一 Family 合并为一条未完成修复请求。已准入且未因关闭而挂起的活动工作不误报。

Steward 可用 OpenIntake 恢复成员，必要时修复 Session、代码缺陷或对外求助。原队列位置和身份保留，不额外投递。排队、运行和重新路由只是等待结果，**原输入完成才算恢复成功**。

同一 Family 同时只保留一条未完成恢复请求，通知意图与幂等 ID 先持久化，中断后续投同一条。Steward 连续三轮修复结束而原任务仍未解决时升级；第三轮还在排队或运行不提前算用尽。取消停止跟进，已删除成员不自动重建。

门面开放时，平台恢复 Router、Steward 的接单能力，不要求它们先能运行模型。生命周期迁移持有的临时关闭须等待该操作，不能抢先开放或重复迁移。**关闭门面是暂停整个 Family 自维护的边界**；重新开放后继续检查当前阻塞。开放接单本身不授予额度 override（门面人工放行的同步见“额度与自动转组”），不打断已准入执行，也不复活取消或明确停止的输入。

Steward 自己失败、收不到恢复任务、恢复后仍不能接单，或显式报告 escalate_reason 时，平台直接升级。故障与当前状态保留，后续完成仍可结案。

## 额度与自动转组

| 对象 | 检查与行动 |
| --- | --- |
| 业务成员 | RelayFamilyMessage 在直达、引用直交、模型选人后统一检查五小时、周等所有新鲜窗口的最低剩余额度；低于 5% 时保存复核记录，原请求留在门面，派发 quota 给 Steward。恰好 5% 不触发该复核；未知不当作零 |
| 业务成员、Router、Steward | auto_migrate 开启且有待执行原输入时，在实际执行入口检查真实绑定账号；任一新鲜额度窗口不高于 5%，或按量余额耗尽时，平台寻找安全备用账号。空闲时不查询额度或迁移。resident.state_changed(intake) 与每分钟 timer 只经 CheckFamilyRoleAvailability 维护接单，不再设置额度 Watch |
| 备用账号 | 已登记、观测新鲜、可用，所有已知额度窗口严格高于 5%；按量后端有确认的正余额。自动切换只在订阅账号之间进行：服务账号和 API 只有在用户把该种类列入 Family.auto_migrate_priority，或与原账号同种类时才可选。优先同账号种类的安全备用账号；同种类不可用时按 auto_migrate_priority 选择，未列出的订阅种类补在末尾，默认按 Claude Code、Codex、Kimi Code 排序 |

**门面的人工额度放行覆盖 Router 与 Steward。** 有人在门面上人工打开接单时记录的额度放行（`quota_reserve_override`），由平台同步给本 Family 的 Router 和 Steward：每个角色以自己的 binding（`resident:<角色 ID>`）和递增的 revision 写入同一份三字段放行，并交给 Execution Scheduler；门面停用放行时同样同步为停用。门面没有放行记录时不改动角色自己的设置。同步发生在平台每次维护角色接单时，Family 暂停期间也照常同步。

执行前检查遇到额度不足时，仍先按上表寻找安全备用账号；找不到安全账号，或同一额度窗口的迁移次数已经用完时，已放行的执行者（业务成员看自己的放行，Router、Steward 看从门面同步来的放行）不再等待：平台结束它的 waiting 状态，保留该窗口的尝试次数，让原输入在当前账号上继续运行，额度用完的 Codex 账号因此改用 credits 计费。额度读数过期时，已放行的执行者同样继续。未放行的执行者保持等待，并按下段通知。

**Router、Steward 找不到账号时由门面管理者选择。** 角色的额度经新鲜读数确认不足，且找不到安全账号或迁移次数已用完时，平台除了向 Family 创建人发送 Lineage 通知，还以门面名义在门面对话里发一组问题卡片（`family_role_quota_questions`），写明角色、账号、剩余额度、预计重置时间和等待中的工作数，选项为“用当前账号的 credits 继续”“等额度重置”“我去补账号”。每个额度窗口只发一次；额度读数未知或过期时不发。门面管理者都能回答：回答留在门面对话里，标为已完成，不交给 Router；DeliverMessage 随后执行选择——选择 credits 继续时，以回答者自己的门面管理权对门面做人工放行，Router、Steward 按上段同步后继续运行；选择等待只回复确认；选择补账号回复首页 AI 账号入口。执行结果作为门面消息写回对话。角色恢复安全账号或进入新的额度窗口时，未回答的卡片关闭。业务成员仍按原方式通知创建人。

所有 Resident 都受 ExecutionService 的通用额度准入保护；该调度门槛取自账号配置，区别于 Family 的 5% 预防线。底层账号额度观测仍维护新鲜快照；取消的是额外的运行图 usage 轮询。自动转组以本 Family 的 auto_migrate 为准，默认开启；成员的直接工作流输入也经过执行前检查。业务额度复核由本 Family Steward 在该策略内决定是否迁移；核对迁移完成后用 quota_reviewed=true 和理由放行原请求，仍检查取消及附件权限。

首次安装尚未配置默认 AI，或持续角色绑定的账号已不存在时，Router、Steward 的已受理输入保持待执行。后台把账号缺失保存为 `family_quota_migrations.account_wait`，角色可用性显示账号配置提示；不会将它记成 coordinator 故障、消耗额度迁移次数或选择其他账号。其他 Resident 和 Lineage 仍按各自状态恢复。后续扫描观察到账号后，仅移除这项等待标记，再执行原有额度保护；既有额度事故与迁移记录继续保留，取消、停止及已准入工作维持原语义。尚未绑定的角色按当前默认 Backend 的实际 Provider 和账号查询额度，支持默认 AI 从 Codex 占位配置切换到其他 Provider。

Family 执行成员迁移沿用账号策略和 Restart 领域，跨 Provider 或跨账号种类显式选择目标账号当前默认 model，使用 compact 交接上下文；已准入工作先完成，未准入 job、消息与发送者跨迁移保留。family_quota_migrations 持久保存账号窗口、尝试次数、已试账号、幂等迁移意图和通知去重；同一账号窗口最多尝试两个备用账号。角色已迁离原 Lineage 的旧记录不能再启动迁移；未知或过期观测不能清除已登记的不可用状态。

门面菜单的 Family 页签提供“额度不足时自动转组”，通过 `UpdateFamily(auto_migrate=bool)` 保存。所有现有和新建 Family 默认开启。关闭后停止新的自动迁移，包括 quota failure 恢复中的换账号；原任务和队列仍等待原账号恢复，额度保护继续有效。已开始的生命周期迁移可完成；不撤销人工操作。Steward 每轮任务带开关事实，行动前按 GetLineage 重读；人工暂停、取消、已完成输入和迁移幂等边界保持不变。没有确定证据区分取消原因时，不将历史 cancelled 输入自动恢复。

### 账号种类优先级

门面 Menu → Settings 在自动转组开关下提供可展开的“账号种类优先级”；Family Function Block 使用同一编辑器。列表只含订阅种类；服务账号和 API 种类列在下方，用户点“允许自动切换”后才加入列表参与排序，“不再允许”将其移出。上下移动后保存，取消不写入；恢复默认需要保存确认。通过 UpdateFamily(auto_migrate_priority=[...]) 保存到本 Family，空数组表示只用订阅、按默认顺序；列入数组的非订阅种类就是用户的明确允许。此策略同时适用于业务成员、Router 和 Steward 的执行前检查、额度失败恢复与 Steward 迁移判断；关闭 auto_migrate 仍阻止新的自动迁移。

Execution 的 ListProviderAccounts 为可执行 Agent 账号提供 migration_type（id、label、default_priority）。订阅按 Provider 分组，API 接入按 Provider＋Model API 身份分组，服务账号单独分组。Claude Code 订阅和 Claude Code＋DeepSeek API 因而属于不同种类；不能用 harness 相同推导为同类。相同 Provider 绑定同一个 Model API 的多个账号属于同类。未接入的已保存种类保留，不自动删除配置。

选择先过滤不可用、额度观测过期、已尝试或低于安全线的候选，保留“同种类有安全账号先换同类”的行为；同类耗尽后按用户排序选择。同类内部取额度余量较高者，再以账号 ID 稳定排序。排序不绕过五小时、周窗口或余额保护。新增但未排序的种类按默认顺序追加；只在下一次尚未保存迁移意图的选择时应用新排序，已开始的迁移仍沿原幂等意图完成。

保存使用当前 Lineage revision，并核对编辑时的原排序；冲突保留草稿，不覆盖其他人的顺序。浏览器、Steward 与确定性恢复路径共同读取这一配置，不分别维护另一个优先级来源。

## 升级、显示与保障边界

无法找到安全后端或恢复用尽时，平台记录诊断并通知 Family 的实际真人创建人 human_familizer_id。持续角色故障不把最近发言者、owner 或 Project 管理员当作创建人。缺少创建人时保留待升级状态并显示缺失，不能声称已经通知；普通业务需要人类决定时，先追溯本次工作的真人发起人，缺少时再用创建人。

门面没有模型，旧 backend/account 不作为门面的额度门控；外层 Relay 交给内层门面时也不检查该门面的额度，由内层向实际执行成员转交时执行额度准入。旧错误门控留下的 waiting 复核在同原请求重试时标记额度检查不适用并继续投递，保留原额度观测，不伪造 Steward 批准，不改变取消、目标名册、附件授权和幂等检查。角色健康合并真实 Session binding、接单状态及迁移/恢复记录；缺失角色显示不可用，执行服务不可达显示未知，没有迁移记录不等于健康。缺联系人独立显示，不能被角色健康掩盖。

平台保障包括接单监督、待执行输入的额度迁移检查和恢复通知。自动恢复以有效账号、可迁移工作和可用恢复入口为条件；条件不满足时保留故障与待处理事项。后续扩展由 [自维护补强计划](/resource/knowledge?item=plan_2a5bca6bf011) 定义。

Execution 拥有用量与准入事实；Restart/lifecycle 拥有迁移和临时接单关闭；Family 监督负责串联事实、保留原请求及升级。Steward 保存原因判断与组织决定，不另建轮询器或迁移执行器。平台运行保护不因用户删除某个角色任务模板而关闭。
