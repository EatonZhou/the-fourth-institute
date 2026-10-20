---
kb_id: decision_4637ca4a69e2
type: decision
project: AI员工
created_at: '2026-09-28'
version:
  number: 42
  created_at: '2026-09-28T10:45:00Z'
  created_by:
    user: tfi-exported-user
    agent: resident_2a1fc6cd272d
  updated_at: '2026-10-19T19:43:58Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_865c3c24f251
importance: 8
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/tests/cases/test_isa_audit_g4.py
  - ${code_root}/management_portal/society_service/isa/definition.py
  - ${code_root}/management_portal/society_service/lineage_program.py
  - ${code_root}/management_portal/society_service/lineage_handoff_backend.py
  related:
  - decision_8838269736d3
  - decision_7ef392b5f60d
  - decision_898b8911a2ff
  - decision_4adcc1ff2f2c
  - decision_6fc653d55c79
  - decision_71aa039e3211
  - decision_d19b9909a674
  supersedes: []
  derived_from:
  - decision_898b8911a2ff
  - thinking_ca8f397a47de
---
# HATG ISA：Lineage 定义指令

Lineage 定义指令创建、读取、修改和删除 Lineage，以及 Lineage 中的组件、连线、候选 Fork、工作区交接和碎片；保存画布坐标；检查定义能否编译运行；一次提交多项修改。所有指令共同遵守的调用格式、执行流程和错误语义见 decision_898b8911a2ff。

## 操作的对象

- **Lineage**：一组 Resident 及其协作关系组成的可操作对象。一个 Lineage 保存元数据（名称、说明、所属项目与 Leader）、成员节点、组件、连线、工作区交接关系、Function Block 实例和碎片。每次修改后 Lineage 的变化计数（revision）加一。成员、组件和 Function Block 实例在画布上的坐标是所有协作者共享的布局，单独记录变化计数 `layout_revision`。Lineage 不分类型，没有类型字段；它在五层组织中的位置由 Principal 层级表达（decision_6591e7793116）。Lineage 聚合保存什么见 decision_6fc653d55c79。
- **成员节点**：Lineage 中每个 Resident 对应一个节点。节点随 Resident 的创建和删除自动出现和消失，由 Resident 指令（decision_d19b9909a674）管理。
- **组件**：Lineage 中负责事件、动作和候选处理的对象。组件类型如下，各类型的配置、端口和运行规则见 decision_4adcc1ff2f2c：

  | 类型 | 作用 |
  | --- | --- |
  | `event_source` | 观察手动触发、时间、Resident 状态、Effect 结果或共享状态等事实，产生 Event |
  | `effect` | 收到 Event 时运行一段脚本，按顺序调用一条或多条 ISA 指令 |
  | `filter` | 按条件保留或拒绝候选 |
  | `selector` | 在多个命名分支中选择路径 |
  | `reducer` | 冻结并合并一组候选 |
  | `loop` | 记录一段往复交互进行了几轮，达到上限或收到完成信号时停止 |
  | `seed` | 尚未成为 Resident 的候选，记录一个想法或方案；它从哪个 Resident 的工作出发由候选 Fork 表达 |

- **连线（wire）**：有向的激活路线或对象绑定。`route` 从输出端口连接接收者的输入端口，传输含 Message、Events 或两者的 Packet；`target` 从 Effect 的具名端点指向对象；`watch` 从观察事件源的 `watch` 端点指向被观察对象。对象绑定不设置目标端口。画布、指令参数和数据库的 source/target 含义一致。
- **候选 Fork**：从一个 Resident 指向一个 Seed 的关系，类型为 `candidate_fork`：Seed 的候选从这个 Resident 的工作出发，采纳 Seed 时 Fork 这个 Resident，这条关系随之变成到新 Resident 的继承关系。它是 Lineage 中唯一以 Seed 为终点的关系，一个 Seed 最多一条。
- **工作区交接（Workspace Handoff）**：Lineage 中两个 Resident 之间的一条交接关系，说明把来源工作区的哪些内容、用直接引用、文件复制或 Git worktree 中的哪种方式交给目标 Resident。
- **碎片（fragment）**：Lineage 上暂存的想法、参考文字、图片引用和分层待办（decision_4adcc1ff2f2c“碎片与分层待办”）。每个碎片有自己的 ID、正文和图片引用，属于定义的一部分，修改时推进 revision。

事件源、Effect 等组件的增加、删除和配置修改都使用本类的组件指令，例如让一个 Resident 每天定时开始工作，就是用 `CreateComponent` 创建一个 `time.reached` 事件源，再用 `CreateWire` 把它连到该 Resident 的 Invoke 端口并写明任务。

Function Block 实例内部的组件和连线也可以用这些指令直接修改。实例下一次重新展开时，按定义的内容覆盖这些修改（decision_5b5653c3eaa5）。

## 定义写入的所属范围

定义写指令显式传入 `lineage_id`。候选 Fork、Resident 和工作区交接的 ID 必须属于这个 Lineage；移动操作中它表示源 Lineage，目标由 `target_lineage_id` 指定。服务在外部工作开始前检查所属范围，并在实际修改定义的短事务内重新核对。对象已移走或被删除时拒绝提交；本次调用已提交的删除或迁移不再被后续步骤误判为外部变化。草案按 Lineage 与草案 ID 共同定位。

ISA 入口由权限系统检查调用者对目标 Lineage 的等级（decision_b820faf42218）：默认模板中读取定义要求 viewer，修改定义要求 contributor，删除 Lineage 和跨 Lineage 移动对象要求 admin（decision_600ae27b0538）。所属范围校验是对象一致性约束，模板对某条指令不设要求时也执行；权限判断统一在 ISA 入口完成。新 Lineage 的创建者按创建者规则对它有 admin，Lineage 内的 Resident 按 Lineage 内协作规则对它有 contributor。

## 放置参数

创建对象的指令都接受放置参数，用来决定新对象在画布上的位置：`near_resident_id`（在该 Resident 附近寻找空位）、`placement`（节点尺寸信息，单位为画布坐标）、`x` 与 `y`（首选坐标，被占用时就近避让）。省略时，Resident 发起的调用放在调用者附近，页面发起的调用放在 Leader 附近。放置参数只影响画布位置，不建立任何关系。对象创建之后，在画布上拖动、自动整理得到的新坐标用 `UpdateLineageLayout` 保存；Society 页面和 Resident 使用同一条指令，也经过同一套权限检查。视口位置、缩放和折叠状态属于个人显示偏好，不经过 ISA（decision_1b2bdf5c33d6）。

Lineage 定义的合并提交、显式 `expected_version`、冲突详情和多 Lineage 版本前提统一见 decision_d918ebebc062。本篇负责各指令修改哪些对象及其操作限制；布局的 `layout_revision` 与定义 revision 分开。

为 Resident 式算子配置执行者时，先创建普通 Resident，再提交算子定义，这是两次提交；第二步失败时，已经创建的 Resident 保留在成员列表中，可单独使用或删除。`ApplyLineageChanges` 的整批修改使用一次原子提交。

## 指令一览

| 指令 | 改变状态 | 作用 | 默认方案要求 |
| --- | --- | --- | --- |
| `CreateLineage` | 是 | 创建一个 Lineage | 对 `project_id` 有 @contributor |
| `GetLineage` | 否 | 读取完整的 Lineage 定义 | 对 `lineage_id` 有 @viewer |
| `UpdateLineage` | 是 | 修改 Lineage 的元数据、头像或碎片 | 对 `lineage_id` 有 @contributor |
| `UpdateLineageLayout` | 是 | 保存画布坐标 | 对 `lineage_id` 有 @contributor |
| `DeleteLineage` | 是 | 删除一个 Lineage | 对 `lineage_id` 有 @admin |
| `ListComponentTypes` | 否 | 读取可用的组件类型与事件类型 | 对“共享”有 @viewer |
| `CreateComponent` | 是 | 创建一个组件 | 对 `lineage_id` 有 @contributor |
| `UpdateComponent` | 是 | 修改一个组件 | 对 `lineage_id` 有 @contributor |
| `DeleteComponent` | 是 | 删除一个组件及其连线 | 对 `lineage_id` 有 @contributor |
| `CreateWire` | 是 | 创建一条连线 | 对 `lineage_id` 有 @contributor |
| `UpdateWire` | 是 | 修改一条连线 | 对 `lineage_id` 有 @contributor |
| `DeleteWire` | 是 | 删除一条连线 | 对 `lineage_id` 有 @contributor |
| `CreateCandidateFork` | 是 | 建立或替换 Seed 的候选 Fork | 对 `lineage_id` 有 @contributor |
| `DeleteCandidateFork` | 是 | 删除 Seed 的候选 Fork | 对 `lineage_id` 有 @contributor |
| `CreateWorkspaceHandoff` | 是 | 创建两个 Resident 之间的交接关系 | 对 `lineage_id` 有 @contributor |
| `UpdateWorkspaceHandoff` | 是 | 修改交接内容或说明 | 对 `lineage_id` 有 @contributor |
| `DeleteWorkspaceHandoff` | 是 | 删除交接关系 | 对 `lineage_id` 有 @contributor |
| `PrepareWorkspaceHandoff` | 是 | 准备交接材料并为目标建立工作区绑定 | 对 `lineage_id` 有 @contributor |
| `GetWorkspaceHandoff` | 否 | 读取交接定义和最近一次准备结果 | 对“共享”有 @viewer |
| `GetLineageDiagnostics` | 否 | 读取当前定义的诊断，包括 Effect 脚本的字面量参数校验 | 对 `lineage_id` 有 @viewer |
| `ApplyLineageChanges` | 是 | 一次原子提交多项修改 | 对 `lineage_id` 有 @contributor |
| `WatchLineage` | 否（流式） | 订阅 Lineage 定义的变化 | 对 `lineage_id` 有 @viewer |

Lineage 不保存待确认的修改方案。需要先让人确认的团队结构，由 Resident 在回复里给出方案，确认后再用 `ApplyLineageChanges` 等指令提交（`${share_root}/Skills/tfi-agent-rule/portal-resident.md` 的“创建团队”协议）。

列出 Lineage 使用资源指令 `SearchResources`（decision_ad3e21094f14），Lineage 是登记资源。

## Lineage

### `CreateLineage`

**创建一个新的 Lineage，并分配独立的 Lineage ID。**

- 参数：`title`、`description`（可选）、`project_id`（必填，新 Lineage 所属的 Project；调用者需要对它有 `@contributor`）、`leader_resident_id`（可选）、`definition`（可选，初始成员、组件和连线）。
- 结果：新 Lineage ID。
- 创建新 Lineage 不修改调用者所在的 Lineage，也不改变调用者的归属。

### `WatchLineage`

**订阅当前定义的失效通知。**

- 参数：`lineage_id`、`after_cursor`（可选，从这个游标之后开始）。
- 结果：每项仅包含 `lineage_id` 和当前 `cursor`（revision）。多次变化可合并为一项；当前 revision 大于 after_cursor 时通知，客户端重新调用 `GetLineage`。
- 不记录或回放逐次编辑；断线重连重新读取当前定义，Lineage 删除后结束订阅。

### `GetLineage`

**读取一个 Lineage 规范化后的完整定义。**

- 参数：`lineage_id`。
- 结果：元数据、成员节点、组件、连线、工作区交接关系和 Function Block 实例。
- 修改 Lineage 之前，通常先用这条指令了解当前结构。

### `UpdateLineage`

**修改 Lineage 的名称、说明、所属项目、Leader、自动运行身份、头像或碎片。**

- 参数：`lineage_id`、`changes`。`changes.fragments` 是碎片的完整新列表：每项 `{id?, content, attachments?}`，省略 `id` 的是新碎片，列表中不再出现的碎片被删除。`changes.avatar` 为 `{"sprite_index": 0–41}`、`{"upload_id": …}`（`CreateUpload` 用途 `avatar` 的已完成上传）或 `null`（回到名称首字）；头像保存在定义之外，只改头像不产生新的定义 revision（decision_5bc3fdfc7eec）。DeleteLineage 同时删除头像。
- 结果：修改后的元数据与碎片。
- 只修改名称时，成员、组件、连线和权限配置保持不变。
- Society 页面保存碎片时携带所读定义的 revision 作为 `expected_version`。另一处先改了定义时，这次保存以 `instruction.conflict` 拒绝，页面重新读取后由用户再保存，不会用旧列表覆盖别人刚写的碎片。

### `UpdateLineageLayout`

**保存成员、组件和 Function Block 实例在画布上的坐标。**

- 参数：`lineage_id`、`positions`（每项 `{object_id, x, y}`，最多 512 项）。
- 结果：`applied`（实际保存的对象及其类型和坐标）、`missing_object_ids`（已不存在的对象）、`layout_revision`、`layout_updated_at`。
- 坐标按对象逐个以后写为准：移动一个对象不会让另一个对象的移动或正在编辑的表单失效，所以这条指令不使用 `expected_version`。对象已被删除时只列入 `missing_object_ids`，不算冲突。
- 只推进 `layout_revision`。定义 revision 不变，Program 不重新编译，`WatchLineage` 不发出定义变化通知。

### `DeleteLineage`

**删除整个 Lineage，不能恢复。**

- 参数：`lineage_id`。
- 结果：被删除的 Lineage ID 和一并删除的 Resident ID。
- Lineage 中的全部 Resident 按 `DeleteResident` 的规则一并删除，Lineage 目录和其中的文件随之删除。Project 层 Lineage 仍在管理子 Lineage 时拒绝删除。

## 组件与连线

### `ListComponentTypes`

**读取当前可用的组件类型，以及每种事件的配置定义。**

- 参数：无。
- 结果：每种组件类型的配置字段和端口，以及事件类型目录。Effect 不分类型，它可以调用的指令与参数定义来自指令目录（decision_aa4fdbac8364）。

### `CreateComponent`

**在 Lineage 中创建一个组件。**

- 参数：`lineage_id`、`kind`（组件类型）、`title`（可选）、`config`（可选，按类型校验）、`client_key`（可选，调用者的审计标记，不代替组件 ID）、放置参数。
- 结果：新组件 ID 与组件。
- 创建或修改 Seed 时可以在 `config.source_resident_id` 写来源 Resident：提交时记录为这个 Seed 的候选 Fork（替换原有的一条），组件本身不保存这个字段。Function Block 定义中的 Seed 就这样声明来源。

### `UpdateComponent`

**修改一个组件的名称或配置。**

- 参数：`lineage_id`、`component_id`、`changes`。
- `changes` 可以包含 `title` 和 `config`；Effect 还可以包含 `script`（组合指令的脚本）、`endpoints`（具名目标端点）与 `failure_recipients`（失败通知 User 用户名数组，最多 20 位，空数组关闭通知；省略时保留已有清单），写法与执行规则见 decision_4adcc1ff2f2c。事件源还可以包含 `watch_subject`（观察对象，系统据此维护对应的 watch 连线）：`{kind, id}` 设定一个对象；观察 Resident 的事件类型可以用 `{kind: "resident", ids: [...]}` 一次设定多个 Resident，任一 Resident 发生该事实都会触发（decision_4adcc1ff2f2c）。
- 放弃一个 Seed 时，把它的状态改为 `dismissed` 并填写原因；Seed 保留在 Lineage 中作为记录。
- 结果：修改后的组件。
- 修改已有连接的端口后，服务校验整张图；连接不兼容时拒绝本次修改，并保留提交前的定义。删除 Effect 的具名目标端点时，相应的 target 连线随端点一起移除。

### `DeleteComponent`

**删除一个组件、关联连线，以及观察这些被删除对象的事件源。**

- 参数：`lineage_id`、`component_id`。
- 结果：被删除的组件和连线 ID。
- 删除算子只解除它与执行者 Resident 的关联，Resident 本身保留。
- 观察被删除组件或连线的事件源会一起删除；该规则递归应用于依赖这些观察者的事件源。结果中的组件 ID 指本次指定的组件，连线 ID 列表包含清理的连线。

### `CreateWire`

**在两个兼容端口之间创建一条连线。**

- 参数：`lineage_id`、`source`、`source_port`（可推断时可省略）、`target`、`kind`（`route`、`target` 或 `watch`）、`config`（可选）、`client_key`（可选）。`target_port` 仅用于激活边：`invoke` 或 Loop 的 `done`；`watch`/`target` 直接绑定对象，省略此字段。
- `watch` 的 `source` 必须是观察事件源，`source_port` 为 `watch`；`target` 是被观察的 Resident、组件、投递连线或交接关系，由事件类型约束可选对象。`target` 的 `source` 必须是 Effect，`source_port` 指定它声明的具名端点。输入的旧方向或 Reference 端口会被拒绝。
- `label`（可选）是连线在画布上显示的名称。上下文与资源读取由 HATG ISA 指令承担，图连接不提供自动读取配置。
- `config` 保存触发目标时的参数映射（`invoke_profiles`，按目标类型各存一份参数）、是否附带事件信封（`include_envelope`，默认 `true`），以及目标暂时不能接收时的处理策略（`unavailable_policy`：`drop` 丢弃、`latest` 只保留最新、`queue` 排队，排队上限 `queue_limit`；`expires_after_seconds` 为待投递调用的过期时间）。指向 Resident Invoke 端口的连线在这里写明交给该 Resident 的具体任务。
- `event` 连线还可以设置 `context_policy`（`session`），说明目标 Resident 怎样执行这次投递（decision_481dc4e6292c）；指向 Effect 的 `event` 连线可以设置 `parallel: true`，每个 Event 到达即并发执行，不按顺序排队（例如 Family Router）。指向 Resident 的连线不能并行，Resident 始终按顺序处理。
- 结果：新连线 ID 与连线。
- 起点和终点的端口方向、用途或数据种类不兼容时拒绝创建。

### `UpdateWire`

**修改一条连线的端点或配置。**

- 参数：`lineage_id`、`wire_id`、`changes`。
- 结果：修改后的连线。

### `DeleteWire`

**删除一条连线，并清理观察该连线的事件源及其关联连线。**

- 参数：`lineage_id`、`wire_id`。
- 结果：被删除的连线 ID。

## 候选 Fork

候选 Fork 属于所在 Lineage 定义，建立、替换和删除都是一次 Lineage 定义提交。Seed 被删除时它的候选 Fork 一起删除；来源 Resident 被删除时只删除这条关系，Seed 保留，等待重新建立；放弃 Seed 不删除它。画布上候选 Fork 是从 Resident 指向 Seed 的虚线箭头，规则见 decision_4adcc1ff2f2c。

### `CreateCandidateFork`

**建立 Seed 的候选 Fork。**

- 参数：`lineage_id`、`seed_id`、`source_resident_id`（同一 Lineage 中的 Resident）。
- 结果：候选 Fork 的关系 ID `edge_id`，以及被替换的关系 ID `replaced_edge_id`（原来没有或来源相同时为空）。
- 一个 Seed 只有一个候选 Fork：Seed 已有指向另一个 Resident 的候选 Fork 时，用一条新关系替换它；来源相同时返回原有关系，不提交修改。
- 只有状态为 `active` 的 Seed 可以建立；来源不在这个 Lineage 中时拒绝。

### `DeleteCandidateFork`

**删除 Seed 的候选 Fork。**

- 参数：`lineage_id`、`seed_id`。
- 结果：被删除的关系 ID `edge_id`。
- Seed 和它的候选文件保留；重新建立候选 Fork 之前不能采纳这个 Seed。Seed 没有候选 Fork 时返回 `instruction.not_found`。

## 工作区交接

一条交接关系由“来源 Resident、目标 Resident”这一对端点唯一确定，属于所在 Lineage 定义的一部分；创建、修改和删除交接关系都是一次 Lineage 定义提交。交接的准备方式、安全限制和清理规则见 decision_7ef392b5f60d。

### `CreateWorkspaceHandoff`

**在同一 Lineage 的两个不同 Resident 之间创建交接关系。**

- 参数：`lineage_id`、`source_resident_id`、`target_resident_id`、`items`（交接项，最多 24 项；每项说明来源工作区、相对范围、准备方式和目标位置）、`explanation`（可选，最多 8000 字符）、`label`（可选）。
- 结果：交接关系 ID。
- 同一对端点已有交接关系时，返回已有关系。

### `UpdateWorkspaceHandoff`

**修改交接项、说明或标签。**

- 参数：`lineage_id`、`handoff_id`、`changes`。
- 结果：修改后的交接定义。
- 提交空字符串会清除说明；省略的字段保持原值。修改定义不会改变已经完成的准备结果。

### `DeleteWorkspaceHandoff`

**删除交接关系及其运行记录关联。**

- 参数：`lineage_id`、`handoff_id`。
- 结果：被删除的交接关系 ID。
- 已经准备好的目标文件和工作区绑定按各自所有者的规则处理，不随交接关系一起删除。

### `PrepareWorkspaceHandoff`

**准备交接材料，并为目标 Resident 建立工作区绑定。**

- 参数：`lineage_id`、`handoff_id`；或 `source_resident_id` 与 `target_resident_id`（这对端点还没有交接关系时先创建一条，交接来源 Resident 当前工作目录所在的 Workspace，直接共享）。
- 结果：交接关系 ID、准备状态（`preparing`、`ready` 或 `failed`）以及逐项结果。
- 全部交接项准备成功并完成绑定后，状态变为 `ready`；准备或绑定的任一步失败时，整次准备状态为 `failed`，交接关系和各项的本次尝试保存同一失败原因。逐项尝试状态表示这次整体准备的结果。
- 准备完成不会投递消息，也不会启动目标 Resident。需要在准备完成后开始工作时，由 Lineage 中的 `workspace_handoff.ready` 事件源连接目标的 Invoke 端口表达。
- 从来源 Resident 派生隔离工作区（例如为多个 Fork 出的 Resident 各准备一份代码副本）使用文件复制或 Git worktree 方式的交接表达。

### `GetWorkspaceHandoff`

**读取交接定义和最近一次准备的结果。**

- 参数：`handoff_id`。
- 结果：交接定义、准备状态、逐项结果和错误。

## 诊断

### `GetLineageDiagnostics`

**检查当前 Lineage 定义，列出会阻止运行的错误和需要注意的警告。**

- 参数：`lineage_id`。
- 结果：诊断列表，每项包含代码、对象 ID、级别和说明；`runnable` 表示本次检查是否没有 error 级诊断。运行时按每条实际激活路径继续校验，具体动作失败时报告对应的运行结果。
- 检查内容包括缺少观察对象、缺少目标、参数映射错误、算子缺少输出和不允许的循环。Society 页面的“程序诊断”入口直接展示这条指令的结果。
- Effect 脚本按调用指令的声明校验字面量参数，复用保存时的 `Param.check`，包括类型、枚举、范围和对象字段约束。脚本中 `limit="5"` 是字符串；整数应写作 `limit:=5`。含变量展开的参数在执行时解析后校验。脚本诊断的对象 ID 是 Effect ID，说明以“第 N 行”开头并写明参数名与错误原因；字面量参数错误的代码为 `effect.invalid_argument`。语法、未知指令、必填参数和引用错误沿用同一脚本分析器。

## 批量修改

### `ApplyLineageChanges`

**把多项修改作为一次原子提交：全部成功时只提交一次，任一项失败时全部不生效。**

- 参数：`lineage_id`、`operations`、放置参数（可选）。
- `operations` 中每项是对组件或连线的 `create`、`update`、`delete` 或 `clone`，或对 Resident 的 `delete`。删除 Resident 采用 `DeleteResident` 的完整语义（decision_d19b9909a674）。后面的操作通过 `$ref:<名称>` 引用前面 create 或 clone 的 `ref`。clone 的 `value` 保存覆盖字段，省略新对象 ID 时由服务生成。批量删除组件或连线只清理指定对象和悬空连线，留下的观察者由诊断提示补充绑定。
- 结果：新建、修改和删除的对象 ID。删除 Leader 后，调用者通过 `GetLineage` 读取继任者。
- Lineage 中的 Effect 可以在脚本中调用这条指令，在 `operations` 中用 `${event.outputs.<字段名>}` 引用上游 Effect 输出的对象 ID，或用 `${变量.字段}` 引用本脚本中前面调用返回的对象 ID；引用在调用时解析。

### Route 端口与数据

Resident、Event Source、Filter、Reducer 的普通出端口为 output；Selector 使用 branch:<id>，Loop 使用 continue／stop。Route 接收端通常为 invoke，Loop 也接受 done。参数映射可以读取 packet.message.text、packet.message.data.* 与 packet.events.0.type；Watch 和 Target 绑定不传输 Packet。包格式与算子合同见 decision_4adcc1ff2f2c。

## 参数名契约

下表列出 arguments 内的顶层参数名；必填条件、类型和枚举以 GetInstructionSchema 为准。

| 指令名 | arguments 参数名 |
| --- | --- |
| `CreateLineage` | `title`、`description`、`project_id`、`leader_resident_id`、`definition`、`familize` |
| `GetLineage` | `lineage_id` |
| `UpdateLineage` | `lineage_id`、`changes` |
| `UpdateLineageLayout` | `lineage_id`、`positions` |
| `DeleteLineage` | `lineage_id` |
| `ListComponentTypes` | 无 |
| `CreateComponent` | `lineage_id`、`kind`、`title`、`config`、`client_key`、`near_resident_id`、`placement`、`x`、`y` |
| `UpdateComponent` | `lineage_id`、`component_id`、`changes` |
| `DeleteComponent` | `lineage_id`、`component_id` |
| `CreateWire` | `lineage_id`、`source`、`source_port`、`target`、`target_port`、`kind`、`config`、`label`、`client_key` |
| `UpdateWire` | `lineage_id`、`wire_id`、`changes` |
| `DeleteWire` | `lineage_id`、`wire_id` |
| `CreateCandidateFork` | `lineage_id`、`seed_id`、`source_resident_id` |
| `DeleteCandidateFork` | `lineage_id`、`seed_id` |
| `CreateWorkspaceHandoff` | `lineage_id`、`source_resident_id`、`target_resident_id`、`items`、`explanation`、`label` |
| `UpdateWorkspaceHandoff` | `lineage_id`、`handoff_id`、`changes` |
| `DeleteWorkspaceHandoff` | `lineage_id`、`handoff_id` |
| `PrepareWorkspaceHandoff` | `lineage_id`、`handoff_id`、`source_resident_id`、`target_resident_id` |
| `GetWorkspaceHandoff` | `handoff_id` |
| `GetLineageDiagnostics` | `lineage_id` |
| `ApplyLineageChanges` | `lineage_id`、`operations`、`near_resident_id`、`placement`、`x`、`y` |
| `WatchLineage` | `lineage_id`、`after_cursor` |
