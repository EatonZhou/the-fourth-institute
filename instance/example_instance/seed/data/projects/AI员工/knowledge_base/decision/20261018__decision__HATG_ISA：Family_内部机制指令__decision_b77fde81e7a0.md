---
kb_id: decision_b77fde81e7a0
type: decision
project: AI员工
created_at: '2026-10-18'
version:
  number: 23
  created_at: "2026-10-18"
  created_by:
    user: ""
    agent: "resident_7765faecc45a"
  updated_at: "2026-11-20T12:13:09Z"
  updated_by:
    user: ""
    agent: "resident_26a8281c84cc"
importance: 5
ai_generated: false
links:
  source: []
  related:
  - decision_89b9df038a95
  - decision_a64b3ea2c3c5
  - decision_44cf587c1721
  supersedes: []
  derived_from: []
---

# HATG ISA：门面与转交指令

门面是一个接收对外消息、再把消息转交给其他 Resident 的 Resident。门面登记它的转交对象集合：任何团队模板用 `UpdateFacadeMembers` 登记，前台、分诊、秘书都用同一种方式；Family 的成员关系在创建和修改时调用同一登记，不另存一份。本类指令提供转交的机制：以原作者的身份把门面收到的消息交给转交对象，记录转交关系，保证同一条原请求只有一个承接者，处理撤回与归档，并读取转交和汇总所需的材料，包括判定某个转交对象现在能否承接。

选谁承接、承接者额度不足怎么办、没有合适对象怎么办、失败怎么跟进、什么时候汇总摘要，是团队模板的策略，由模板用 Event、Effect 和共享状态组合出来，不属于指令（thinking_ca8f397a47de）。Family 的运行图见 decision_89b9df038a95，运行维护见 decision_a64b3ea2c3c5，消息路由见 decision_9ad68dd077d4，工作摘要的存储与维护见 decision_d19b9909a674；所有指令共同遵守的调用格式、执行流程和错误语义见 decision_898b8911a2ff。创建、解除、删除和修改 Family 用 Family 类指令。

## 指令一览

| 指令 | 改变状态 | 作用 | 默认方案要求 |
| --- | --- | --- | --- |
| `UpdateFacadeMembers` | 是 | 登记或替换门面的转交对象集合；空集合取消门面身份 | 对 `resident_id` 有 @admin（它决定谁能接收门面转交的请求，weihao 2026-11-26） |
| `CheckRelayTarget` | 否 | 判定一个转交对象现在能否承接：可承接、额度不足、不可用或未知 | 对 `resident_id` 有 @viewer，领域再次核验门面可读 |
| `GetRelayContext` | 否 | 按需读取转交所需的候选对象、原请求或冻结的上一轮 | 对 `resident_id` 有 @viewer，领域再次核验门面可读 |
| `RelayMessage` | 是 | 以原作者身份把门面原请求转交给一个转交对象，或由门面收下纯告知 | 转交原请求要求对门面 `resident_id` 有 @contributor；回复门面外发消息要求对门面 `resident_id` 有 @message |
| `ResolveRelayedRequest` | 是 | 将已经完成的复核成果归档到原失败请求，保留原失败回执且不重新执行 | 对 `resident_id` 有 @contributor |
| `GetFacadeSummaryInputs` | 否 | 读取按小时汇总的材料与来源版本 | 免检 |

失败输入的重开使用 `RequeueMessage`，它同样作用于门面的失败输入，见下文“重开门面的失败输入”。读取原请求经转交链后的实际状态与回复使用 `GetInputState`。

## 登记门面

### `UpdateFacadeMembers`

`UpdateFacadeMembers(resident_id, member_ids)` 把 `resident_id` 登记为门面，转交对象集合整体替换为 `member_ids`；空数组取消它的门面身份。`member_ids` 中的对象必须是调用者可见、未删除的 Resident，且不能包含门面自己。同一对象集合不变时重复调用返回当前登记。集合变化后，已经转交的请求保持原承接者，不重新转交；被移出集合的对象不再接收新的转交。登记不创建 Resident、不创建 Lineage 组件，也不连线：门面收到消息后的路由由团队模板自己的 Event 与 Effect 完成。结果返回 `resident_id`、`member_ids` 和 `status`（`registered` 或 `unchanged`、`removed`）。

Family 的成员关系是登记来源之一：Familize、UpdateFamily 改变成员时，同一事务调用这一登记，结果以 Family 的成员列表为准；其他模板直接调用，无须使用 Family 类指令。

新增转交对象时，调用者须对该对象拥有 `DeliverMessage` 的权限；门面成员关系在一次提交中整体替换。

## 判定转交对象

### `CheckRelayTarget`

`CheckRelayTarget(resident_id, member_id)` 只读判定门面的一个转交对象现在能否承接新请求，返回 `status`：

- `ready`：对象存在，当前接单开放，执行账号可用且额度判定充足（含已开启并生效的付费额度）。
- `quota_low`：对象所用账号额度低于保留阈值，且没有生效的付费额度。
- `unavailable`：已登记对象已删除、接单关闭、账号不可用，或正在进行账号迁移与生命周期操作。
- `unknown`：额度观察缺失或已过期，系统不推测。

同时返回 `reason`（原因码）和 `remaining_percent`（限制窗口的最低剩余比例，未知为 null）。判定的阈值、窗口取最小值和付费额度口径由系统按现有额度策略计算，调用者拿到的是结论；Effect 只比较 `status` 字符串，不比较数值。指令不改变任何状态，也不保证之后的转交成功，转交时对象状态变化由 `RelayMessage` 自己的错误返回。

不在门面当前登记集合中的 `member_id` 返回参数错误。

## 转交

### `RelayMessage`

- 参数：`resident_id`（门面）、`message_id`（原请求）、`member_id`（承接者，`notice=true` 时不填）；可选 `notice`、`continue_previous`、`reassign`、`reason`。
- 转交对象必须是门面当前登记的转交对象。指令不检查额度，不判断职责是否覆盖，不要求复核；额度由调用方先用 `CheckRelayTarget` 判定，职责和复核由调用它的 Effect 或 Resident 在转交前完成。
- 转交以原作者的身份投递：原消息内容、原发送者和附件访问权限保持不变，门面转交关联是对外投影的真值。成功返回 `status=relayed`、`member_id` 和承接者上的 `message_id`。
- 同一原请求同一时间只有一个承接者。转交时持有这条原请求的互斥所有权，慢转交期间重复调用返回 `instruction.in_progress`（可重试）。选择和投递持久保存，重复调用返回原回执；改为另一个对象必须显式 `reassign=true` 并写明 `reason`，原承接者正在执行时须先停止；改派记录保留来源。
- 取消优先：原请求已被撤回时返回 `withdrawn`，不再转交。转交期间的撤回仍可提交，最终关联提交核对当前取消状态。
- `notice=true` 由门面收下不要求承接者行动的消息，例如对方完成工作后的结果报告、已发布或已交付的通知、无问题无待办的进展说明。门面记下这条告知，在门面 Conversation 中显示并结束它，返回 `status=kept`；之后不能再转交。`notice` 不能与 `member_id` 或 `reassign` 同用。门面保留最近 20 条告知，`GetFacadeSummaryInputs` 把它们作为门面摘要的来源。
- `continue_previous`：调用者判定当前请求明确依赖同一发送者的上一轮时设为 true，默认 false。平台保存这个判断，并给承接者附上可信的上一轮背景；换承接者仍可使用。它不能与 `notice` 或 `reassign` 同用。
- 重复调用沿原转交身份核验；中断后的同键重试在原执行的活动锁内核验转交事实，只补齐原关联和回执。

### 回复门面发出的消息

要把回复交给门面外发消息的原写作者，以门面为 `resident_id`，传 `reply_to_message_id` 和 `message`。可用 `business_ref` 关联调用方业务记录。原外发消息决定承接者；服务按当前身份校验消息与门面的关系，正文和附件仍写入对应对话。这种调用不提供 `member_id` 或原转交请求的 `message_id`。

## 读取转交材料

### `GetRelayContext`

`GetRelayContext(resident_id, message_id, section?)` 是只读入口。`resident_id` 为门面，`message_id` 为已经存在的门面原请求；先按当前调用者核验可见性，再读取。

- `section=members`（默认）：返回门面当前登记的转交对象的 ID、角色、名称和可读状态，以及该请求原发送者的 Solo/Mute 约束，列表已经按这些约束过滤。已受理请求读取冻结约束；尚无快照时只读当前设置，不创建快照。嵌套团队以内层门面为候选；缺失或不可读的对象标为 `unavailable`。摘要、工作状态和能力不合并返回，使用各自指令按需读取。
- `section=request`：返回原请求的作者、正文、附件引用和被引用的消息，取自受理时冻结的材料。
- `section=previous_round`：返回受理原请求时已冻结的唯一上一轮问答，缺失为 null。读取不扫描会话、不替换候选、不建立续谈关系。
- 结果都包含 `resident_id` 与 `message_id`；`section` 不支持取全，读取入口不变成每轮自动整包预取。

## 归档复核成果

### `ResolveRelayedRequest`

参数：`resident_id`（门面）、`message_id`（原失败请求）、`review_message_id`（复核输入）、`reason`。结果为 `status=archived`、原 `message_id`、`review_message_id` 和真实 `reply_message_ids`。它不投递新业务、不重开失败输入；前提与来源校验见 decision_a64b3ea2c3c5。

## 汇总材料

### `GetFacadeSummaryInputs`

`GetFacadeSummaryInputs(resident_id, overview_only?)` 读取门面摘要的来源，只有门面本身和对门面有管理权的调用者能读。默认返回 `title`、`goal`、`members`、`notices`（门面收下的最近告知，含正文，其指纹计入 `source_versions`）、`previous_summary`、`source_versions`、`needs_update`、`facade_needs_update`、`consolidation_due`。`consolidation_due` 只列到期业务成员的 `resident_id`、`revision`、`level`、`reason`，不带成员各层全文；承担团队运转角色的转交对象不进入归纳清单。内层门面不进入外层归纳待办。

`overview_only=true` 仅返回门面 ID、是否需要更新、归纳待办和 `notification`，不返回成员全文、门面旧摘要或来源列表。`notification` 是可交给 `DeliverMessage` 的短入口，同小时同材料复用消息 ID。`needs_update` 为门面来源变化或存在到期待办。成员来源版本考虑内容、缺失、标题、名单和目标。比较来源版本需要对整张映射求差，脚本没有这种能力，所以由这条读取指令完成。

## 重开门面的失败输入

`RequeueMessage(resident_id=门面, message_id=失败的原请求)` 把门面上状态为 `failed` 的原请求放回 `queued`，重新触发门面收到消息的 Event，转交流程再运行一次。原消息 ID、作者、时间、正文和附件保持不变，失败回执保留在 Conversation 事件和恢复审计中，不复制业务请求。已取消的请求返回取消状态，不重新入队；已经有承接者的请求保持现状并返回成功；已经排队、运行或完成的请求只观察结果。门面输入没有执行轮次，重开只改变输入状态并重新发出到达 Event。权限、幂等与显式恢复语义见 decision_c54e68a82d7a。

## 由团队模板组合的策略

下列工作由团队模板的 Effect 用通用指令和共享状态组合完成，指令集不为它们登记专用指令：

| 工作 | 组合用到的指令 |
| --- | --- |
| 周期汇总检查 | `ListResidentStates`、`GetFacadeSummaryInputs`、`DeliverMessage` |
| 摘要遗漏提醒 | `GetResidentSummaries`（`include_maintenance`）、`DeliverMessage`（`attach_to_next`） |
| 恢复原请求并升级 | `GetInputState`、`RequeueMessage`、`RelayMessage`、`CreateNotification`、`GetSharedState`、`UpdateSharedState` |
| 角色接单 | `SetIntakeOpen`、`ListResidentStates` |
| 业务活动水位与回顾门控 | `GetSharedState`、`UpdateSharedState`、`ListResidentStates`、`DeliverMessage` |
| 门面摘要 | `UpdateFacadeSummary` |
| 额度不足时改走招募或额度任务 | `CheckRelayTarget`、`DeliverMessage` |
| 派发任务给承接者 | `GetRelayContext`、`DeliverMessage` |

## 参数名契约

下表列出 arguments 内的顶层参数名；必填条件、类型和枚举以 GetInstructionSchema 为准。

| 指令名 | arguments 参数名 |
| --- | --- |
| `UpdateFacadeMembers` | `resident_id`、`member_ids` |
| `CheckRelayTarget` | `resident_id`、`member_id` |
| `GetRelayContext` | `resident_id`、`message_id`、`section` |
| `RelayMessage` | `resident_id`、`message_id`、`member_id`、`notice`、`continue_previous`、`reassign`、`reason`、`reply_to_message_id`、`message`、`business_ref` |
| `ResolveRelayedRequest` | `resident_id`、`message_id`、`review_message_id`、`reason` |
| `GetFacadeSummaryInputs` | `resident_id`、`overview_only` |
