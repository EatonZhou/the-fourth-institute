---
kb_id: decision_68215b3a825b
type: decision
project: AI员工
created_at: '2026-09-28'
version:
  number: 38
  created_at: '2026-09-28T10:45:00Z'
  created_by:
    user: tfi-exported-user
    agent: resident_2a1fc6cd272d
  updated_at: '2026-10-27T12:51:50Z'
  updated_by:
    user: ''
    agent: resident_73cb83291316
importance: 8
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/tests/cases/test_isa_audit_g4.py
  - ${code_root}/tests/cases/test_hatg_run_observation.py
  - ${code_root}/management_portal/society_service/lineage_program_runtime.py
  - ${code_root}/management_portal/society_service/trigger_path_observation.py
  related:
  - decision_898b8911a2ff
  - decision_4adcc1ff2f2c
  - decision_5b5653c3eaa5
  - decision_4637ca4a69e2
  supersedes: []
  derived_from:
  - decision_898b8911a2ff
  - thinking_ca8f397a47de
---
# HATG ISA：Lineage 运行指令

Lineage 运行指令推动 Lineage 运行并观察运行情况：手动触发一个事件、产生一个新事件、暂停、恢复或重置事件源与循环，以及查看一次运行调用了哪些 Resident、还有哪些调用在连线上等待投递。把模板施加到 Lineage 上（包括施加后不保留结构的一次性模板）使用 `ApplyTemplate`，见 decision_b04cac4188bb。所有指令共同遵守的调用格式、执行流程和错误语义见 decision_898b8911a2ff。

## 操作的对象

- **Event**：Lineage 中已经发生并被持久保存的事实，例如“用户手动触发了开始”或“某个 Resident 完成了一轮工作”。Event 由事件源产生，沿连线激活下游对象。
- **Trigger Path**：从一次 Event 发生或一次独立 Effect 激活开始的触发流程。可分叉、汇合，在 Resident 处终止；Resident 发出的消息、指令以及完成后产生的事件属于新的流程。
- **Execution**：Resident 接单后的一次独立执行。Agent 保存执行状态、内容和输入关联；Trigger Path 只保存对 Execution 的引用。多个流程可以关联同一个 Execution，Execution 中不保存流程信息。
- **待投递项（Delivery）**：目标忙碌时，在连线上等待的输入。尚未接单的输入没有 Execution ID；投递项有独立 delivery_id，可编辑、撤回或过期。
- **组件运行状态**：事件源的已触发次数、触发预算代次、是否暂停、最近一次触发与投递摘要和下一次计划触发时间；循环组件的已进行轮数、是否暂停和运行代次。组件定义属于 Lineage 定义，组件运行状态属于本类。

Event 目录、调度和恢复机制见 decision_4adcc1ff2f2c。触发根与原因保存在 trigger_paths，流程步骤和实际因果边保存在 trigger_path_steps、trigger_path_step_edges，Resident 步骤与执行之间的引用保存在 trigger_path_execution_links。步骤保存实际发生事实，读取不依赖当前图定义。

## 指令一览

| 指令 | 改变状态 | 作用 | 默认方案要求 |
| --- | --- | --- | --- |
| `TriggerEvent` | 是 | 手动触发一个事件源，开始一次运行 | 对 `lineage_id` 有 @contributor |
| `ResolveEffectActivation` | 是 | 由 Lineage admin 人工终结结果未知的 Effect 激活 | 对 `lineage_id` 有 @admin |
| `ResolveTriggerPath` | 是 | 由 Lineage admin 把无法自行结束的触发流程按失败关闭 | 对 `lineage_id` 有 @admin |
| `GetComponentRuntime` | 否 | 读取事件源、Effect 与循环组件的运行状态 | 对 `lineage_id` 有 @viewer |
| `PauseComponent` | 是 | 关闭或打开事件源与 Effect，暂停或恢复循环组件 | 对 `lineage_id` 有 @contributor |
| `ResetComponentRuntime` | 是 | 清零事件源的触发次数、循环的轮数，或 Route 的已发送消息次数 | 对 `lineage_id` 有 @contributor |
| `ListTriggerPaths` | 否 | 列出 Lineage 的 Trigger Path，最新的在前 | 对 `lineage_id` 有 @viewer |
| `WatchTriggerPath` | 否（流式） | 订阅一次 Trigger Path 的进展，直到它结束 | 对 `lineage_id` 有 @viewer |
| `GetTriggerPath` | 否 | 读取一次 Trigger Path 的详情 | 对 `lineage_id` 有 @viewer |
| `ListPendingDeliveries` | 否 | 读取一条连线上等待投递的调用 | 对 `lineage_id` 有 @viewer |
| `UpdatePendingDelivery` | 是 | 修改一条待投递调用的输入 | 对 `lineage_id` 有 @contributor |
| `DeletePendingDelivery` | 是 | 删除一条待投递调用 | 对 `lineage_id` 有 @contributor |

## 触发与运行

### `TriggerEvent`

**手动触发一个 `manual.triggered` 事件源，并带上本次运行的输入。**

- 参数：`lineage_id`、`event_source_id` 或 `wire_id`（二选一）、`prompt`（可选，本次输入；与组合后的消息共享最多 1,000,000 字符的投递预算，decision_4adcc1ff2f2c）、`content_parts`（可选，图片、文件或上下文引用，最多 32 项）、`event_name`（可选）、`subject_resident_id`（可选）、`payload`（可选）。
- 结果：Event ID、Trigger Path ID 和本次投递摘要。
- 本次输入只属于这一次 Trigger Path，Effect 中配置的固定提示词保持不变。投递给 Resident 时，固定提示词在前、本次输入在后。
- `event_name` 写入 Event 的 `payload.name`，供接收方区分同一事件源的不同用途；`subject_resident_id` 写入 Event 涉及的 Resident；结构化的 `payload` 写入 `payload.data`。
- Event 类型固定为 `manual.triggered`，只沿 `manual.triggered` 事件源或它的某条连线分发。系统事件（例如 Resident 完成工作）由运行时根据真实事实产生，调用者不能用这条指令伪造。
- Society 页面的手动触发、“创建团队”后的开始事件和 Effect 脚本发出的手动 Event 都使用这条指令。定时开始的工作由 `time.reached` 事件源自动触发，不需要调用这条指令。
- 事件源已暂停时以 `instruction.conflict` 失败。

## 组件运行控制

### `GetComponentRuntime`

**读取 Lineage 中事件源、Effect 与循环组件的运行状态。**

- 参数：`lineage_id`、`component_id`（可选，省略时读取全部事件源、Effect 与循环组件）。
- 自动事件源的身份状态由 Lineage 领域统一判断。未暂停、未耗尽的源无法解析有效 `run_as_principal` 时返回 `state=blocked`、`error`（具体原因）；`GetLineage.program_event_source_runtime` 使用同一投影，`GetLineageDiagnostics` 提供对应错误诊断。暂停与耗尽状态优先，手动源不受此规则阻塞。读取不建立计划或替换执行身份；合法身份配置完成后仍由原协调器建立和推进计划。
- Effect 返回 `kind=effect`、`paused`、`state=paused|active`、`paused_at`，包括从未执行过的 Effect。`GetLineage.program_effect_runtime` 同步提供这些字段；最近执行结果与当前开关分别表达。
- 结果：每个组件的运行状态。事件源包括已触发次数、是否暂停、最近一次触发与投递摘要和下一次计划触发时间（已抽取随机偏移时包含偏移）；循环组件包括已进行轮数和是否暂停。Home 的“定时与自动触发”面板用它显示触发计划（decision_481dc4e6292c）。

### `PauseComponent`

**关闭或打开一个事件源或 Effect，暂停或恢复循环组件。**

- 参数：`lineage_id`、`component_id`、`paused`（必填；`true` 关闭或暂停，`false` 打开或恢复）。
- 结果：组件的运行状态。
- 事件源暂停期间不产生新的 Event：到期的定时触发被跳过，观察型事件源不记录暂停期间的变化。循环组件暂停期间不开始新的轮次，正在进行的一轮继续完成。
- 暂停不修改 Lineage 定义，不关闭 Resident 接单，也不取消正在进行的工作。
- Event 与 Effect 的开关唯一真值为 PostgreSQL 中的运行状态 `paused`，默认打开。事件源保存在 `event_source_progress`，Effect 保存在已有组件状态表 `operator_work`；重启、刷新和修改组件配置不会自动打开。界面关闭和打开都调用 `PauseComponent`，分别传 `paused=true` 与 `paused=false`，不另存定义开关。
- 关闭 Event 后，手动指令、定时观察、系统回调和积压重放均不能产生新的触发或增加计数；手动触发返回已关闭错误。关闭检查与触发记录的建立在同一短事务中串行。
- 关闭 Effect 后，所有 Event 和 Packet 激活以及恢复重试都经过执行器的开关检查。脚本在每条指令准入时检查持久状态，准入与关闭使用同一事务锁。关闭期间返回 `status=paused`，不产生成功或失败事件；已有待投递项保持等待，不被当作投递完成。
- 已经准入的外部调用可以返回其结果；关闭不能追溯撤销外部副作用。脚本的后续指令等待打开，已完成调用保留在恢复日志中。
- System 请求计时与系统演化 Harness 记录的具名 Event 开关同步投影到 Execution 本地设置。`PauseComponent` 在原执行准入锁内提交运行状态，释放状态锁后重读最新图与运行状态、完成投影才返回；定义修改、删除和服务启动也重建投影。旧回调不能覆盖较新的开关状态。投影失败保留已提交回执，同幂等恢复只补投影，不重放旧暂停操作。删除已安装开关会关闭投影；从未安装时保留原设置值（decision_4527448ad07f）。

打开（`paused=false`）时：

- 定时事件源恢复后从下一次计划时间继续，不补发暂停期间的触发；观察型事件源重新建立观察基线。固定间隔的事件源从恢复时刻重新计时；日历规则的事件源取恢复后的下一个日历时间。
- Effect 打开后允许新激活和已有待投递项继续；恢复日志中的已完成指令复用原结果。重复打开或关闭保持幂等。

### `ResetComponentRuntime`

**清零运行计数：事件源的已触发次数、循环组件的已进行轮数，或指向 Resident 的 Route 已发送消息次数。**

- 参数：`lineage_id`，以及 `component_id`（事件源或循环组件）与 `wire_id`（Route）中的一个。
- 结果：清零后的运行状态 `runtime` 和新的运行代次 `generation`。
- 重置事件源会同时清除它最近一次的投递摘要，并取消“触发次数耗尽后自动删除”的待删除资格。
- 重置保持关闭状态。Effect 没有可清零的运行计数，使用本指令时报告参数错误。

给出 `wire_id` 时只作用于指向 Resident Invoke 的 Route，`runtime` 包含 `sent_count`、`reserved_count`、`max_messages`、`remaining_messages`、`message_limit_reached`、`generation`。

发送上限由 `CreateWire` / `UpdateWire` 的 `config.max_messages` 设置：缺省或 null 为无限次，0 表示停止发送，正整数表示最多成功投递的消息数。重置递增运行代次，使已发送计数归零；保持连线配置、图版本和历史投递去重记录，不补发已丢弃的消息。尚未确认结果的发送保留占位，避免重置导致超额投递。

## 人工处置结果未知的 Effect

### `ResolveEffectActivation`

**由 Lineage 的 admin 确认一次结果未知的激活应按成功或失败结束。** 服务重启或外部调用中断可能留下无法自动确认的调用；运行面板对可处置的激活显示“标记成功 / 标记失败”，并允许填写说明。

- 参数：`lineage_id`、`activation_key`（来自 `GetTriggerPath.steps` 的 Effect 步骤）、`status`（`succeeded` 或 `failed`）、`note`（可选，最多 4000 字符）。
- 权限：按当前调用主体及其委托上下文检查该 Lineage 的 `admin`；不具备权限返回 `instruction.forbidden`。
- 结果：`activation` 终态记录，其中 `disposition` 保存 `actor`、`status`、`note` 与 `occurred_at`。原指令的调用摘要仍保留未知状态，人工判断与实际调用事实分别记录。
- 仅可处置含未确认调用、且已经停止执行的激活。激活不存在返回 `instruction.not_found`；仍在执行、没有未知调用或已结束且结果不同，返回 `instruction.conflict`。已人工处置的激活再次收到相同结果时返回首次记录，处置人、时间和说明保持不变。
- 激活终态、恢复日志移除与对应 Event/Wire 待投递项的释放在同一短事务提交。该连线上的其他待投递项继续保留；清理恢复依赖在提交后进行，可按既有清理机制重试。人工处置解除本次激活对删除的阻塞。
- 重试只会读回执行方保存的同一张结果未知的回执时，平台停止重试：这次待投递项等待处置，不再挡住连线上后面的 Event，并以 Leader 署名请 Effect 的失败收件人（没有时为 Lineage 管理员）处置。ListPendingDeliveries 把它列为 `blocked`，`recovery_blocked.requires_resolution` 为 true，并带 `activation_key`（decision_4adcc1ff2f2c）。
- 处置结束本次脚本，不执行剩余调用，也不产生 `effect.succeeded` 或 `effect.failed` 下游 Event。外部业务的实际结果需要处置人自行核实；已有调用结果与已产生的业务事实保持不变。

## 人工关闭停住的触发流程

### `ResolveTriggerPath`

**由 Lineage 的 admin 把一条无法自行结束的触发流程按失败关闭。** 例如 Event 已登记却从未派发、Resident 步骤因无法恢复被挡住、Effect 激活停在执行中但已没有执行者继续推进。这类流程一直保持进行中，恢复轮询也不会再推进它。

- 参数：`lineage_id`、`trigger_path_id`、`note`（必填，写明关闭原因，最多 4000 字符）。
- 权限：该 Lineage 的 `admin`，与 `ResolveEffectActivation` 相同。
- 效果：流程中所有仍未结束的步骤记为失败，`result.disposition` 保存 `actor`、`status`（`failed`）、`note`、`scope`（`trigger_path`）与 `occurred_at`；仍有执行日志的 Effect 激活按 `ResolveEffectActivation` 的同一事务终结，释放本次排队项和恢复依赖。随后流程按常规规则结束为失败。不重试任何工作，也不产生下游 Event。
- 带 `scope=trigger_path` 处置的流程不发 Lineage 自动执行失败通知，流程中更早失败的步骤也包括在这次人工决定里。
- 拒绝条件，全部在改动前检查：流程中有结果未知的 Effect 调用时先用 `ResolveEffectActivation` 逐个处置；Effect 激活仍在执行、Resident 仍在处理流程中的输入、关联的 Execution 尚未结束，或流程仍有排队中的投递（先用 `DeletePendingDelivery`）时返回 `instruction.conflict`。流程不存在返回 `instruction.not_found`；已结束的流程原样返回。

## 运行观察

读取以一次数据库快照组织根记录、步骤、边和 Execution 引用。状态来自各自的唯一责任表，流程中不复制聊天正文或执行结果。分页 limit 默认 50，范围 1–200；Resident 过滤先于分页。

### `ListTriggerPaths`

参数为 lineage_id、可选 resident_id、cursor、limit；cursor 是上一页返回的 next_cursor，原样交回，不填时读第一页。返回 paths、total，还有下一页时另返回 next_cursor，按发生时间从新到旧列出。空事件、被过滤的分支、只执行 Effect 的流程也能被观察。

### `GetTriggerPath`

参数为 lineage_id、trigger_path_id、可选 offset、limit、artifact_activation_key。返回 path、steps、edges、execution_links、executions；四个集合用同一个 offset（非负整数）按位置各取一段，因此这条指令保留 offset，不用 cursor。path 包含 root_kind、occurrence_id、root_node_id、cause_kind、cause_id、occurred_at 和状态。

cause_kind 区分 input、timer、execution、instruction、step、external。Event 源节点 ID 表示配置身份；occurrence_id 标识本次发生，同一事件源再次触发产生另一个 Trigger Path。根记录同时承担“这次触发发生了什么、为何发生”的记录职责。

流程在全部步骤结束且已关联 Execution 均达到终态后结算。没有待处理步骤的空事件也可完成。失败或过期按失败结算，取消按取消结算。执行中的 Resident 后续产生新流程时，只保留明确的触发原因，原流程不等待新流程。

已结束的流程保留 30 天。SocietyService 的例行维护分批删除结束超过 30 天的流程及其步骤、因果边和 Execution 引用；每个 Effect 最近一次结束的激活所在的流程保留，GetLineage 用它显示该 Effect 的最近结果。进行中的流程不受保留期影响。超过保留期的 trigger_path_id 再读取时返回 `instruction.not_found`。

Effect 的激活身份、状态、错误和有限输出保存在步骤中，指令调用日志仍由 ISA 负责。artifact_activation_key 只允许选择本流程中实际发生的 Effect 激活，经权限与归属校验后返回 artifact_output；普通查询只返回引用，不读取大正文。

### `WatchTriggerPath`

参数为 lineage_id、trigger_path_id、可选 after_cursor。返回 change 与 cursor。游标覆盖该流程的步骤和全部关联 Execution 的状态变化，包括当前返回页之外的执行。终态送出最终状态后结束订阅；不追踪后续新流程，也不以模型输出代替业务验收。

### `ListPendingDeliveries`

参数为 lineage_id、wire_id。返回 deliveries，每项包含 delivery_id、Event 身份、输入、排队与过期时间及投递状态。

### `UpdatePendingDelivery`

参数为 lineage_id、wire_id、delivery_id、可选 prompt。返回修改后的 deliveries。空文本恢复连线定义中的输入；已被执行领取的项拒绝编辑。

### `DeletePendingDelivery`

参数为 lineage_id、wire_id、delivery_id。返回剩余 deliveries。撤回与流程步骤终结在同一事务提交，原事件重放不重新生成被撤回的项。

## 接口与存储边界

对外只提供 Trigger Path 观察接口，不保留 Run / Invocation 的旧接口别名。输入排队、执行提交、Provider transport retry 属于内部调度；它们不能创建另一套 Resident 运行记录。Provider turn ID 只在 Provider 适配层使用。

事件、步骤与后续工作状态在同一事务提交，提交后才发布通知；消息重复消费使用已登记的发生身份去重。服务重启从未完成步骤、输入和 Execution 继续处理，不重新产生已经完成的流程。

## 参数名契约

下表列出 arguments 内的顶层参数名；必填条件、类型和枚举以 GetInstructionSchema 为准。

| 指令名 | arguments 参数名 |
| --- | --- |
| `ResolveEffectActivation` | `lineage_id`、`activation_key`、`status`、`note` |
| `ResolveTriggerPath` | `lineage_id`、`trigger_path_id`、`note` |
| `TriggerEvent` | `lineage_id`、`event_source_id`、`wire_id`、`prompt`、`content_parts`、`event_name`、`subject_resident_id`、`payload` |
| `GetComponentRuntime` | `lineage_id`、`component_id` |
| `PauseComponent` | `lineage_id`、`component_id`、`paused` |
| `ResetComponentRuntime` | `lineage_id`、`component_id`、`wire_id` |
| `ListTriggerPaths` | `lineage_id`、`resident_id`、`cursor`、`limit` |
| `GetTriggerPath` | `lineage_id`、`trigger_path_id`、`artifact_activation_key`、`offset`、`limit` |
| `WatchTriggerPath` | `lineage_id`、`trigger_path_id`、`after_cursor` |
| `ListPendingDeliveries` | `lineage_id`、`wire_id` |
| `UpdatePendingDelivery` | `lineage_id`、`wire_id`、`delivery_id`、`prompt` |
| `DeletePendingDelivery` | `lineage_id`、`wire_id`、`delivery_id` |
