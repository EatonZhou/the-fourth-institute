---
kb_id: decision_ae422b92c6f3
type: decision
project: AI员工
created_at: '2026-09-12'
version:
  number: 16
  created_at: '2026-09-12'
  created_by:
    user: ''
    agent: resident_8be5b285cc4b
  updated_at: '2026-10-19T19:43:58Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_865c3c24f251
importance: 7
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/society_service/lineage_program_terminal_reconciliation.py
  - ${code_root}/management_portal/static/portal_chat_workspace.js
  - ${code_root}/management_portal/society_service/lineage_subgraph.py
  - ${code_root}/management_portal/society_service/lineage_program_effect_execution.py
  - ${code_root}/management_common/operation_identity.py
  - ${code_root}/management_portal/hatg/kernel.py
  - ${code_root}/management_portal/execution/agent_data_store.py
  - ${code_root}/management_common/principal.py
  - ${code_root}/management_portal/society_service/lineage_domain.py
  - ${code_root}/management_portal/society_service/resident_domain.py
  - ${code_root}/management_portal/execution/contracts.py
  related:
  - decision_4adcc1ff2f2c
  - decision_fb910c43ab07
  - decision_b1ab45c4b3ec
  - decision_d19b9909a674
  - decision_3fe3cca8fd95
  - decision_898b8911a2ff
  - decision_edc470e09f5a
  - decision_b820faf42218
  supersedes: []
  derived_from: []
---

# Portal 根身份、消息署名与异步执行归属

Portal 中的每一次业务操作都记在一个稳定的身份名下：一位人类用户，或者一个 Resident。这个身份称为操作的**根身份**。权限检查、指令执行记录和消息署名都以根身份为准。Project、Lineage、Event、Effect、工作区和 Provider 会话只说明操作针对什么对象、属于哪个范围、由什么引起，它们本身不产生新的调用者身份。

本文说明根身份有哪几类、一次操作怎样记录身份、消息和模型执行怎样署名，以及 Lineage 中的异步工作（自动触发的运行、排队等待投递的调用、恢复后的重放）以谁的身份进行。认证怎样确认调用者见 decision_3fe3cca8fd95，权限怎样判断见 decision_3fe3cca8fd95 与 decision_b820faf42218。

## 三类身份

Portal 用 `Principal`（身份）表示一个参与者。一个身份由类型和 ID 组成，定义在 `management_common/principal.py`：

| 类型 | ID 的形式 | 从哪里来 |
| --- | --- | --- |
| `user`（人类用户） | 1–80 个字母、数字或 `_`、`.`、`@`、`+`、`-`；不能以 `@` 或 `resident_` 开头，也不能是服务名 | 浏览器登录会话（decision_3fe3cca8fd95） |
| `resident` | `resident_` 加 12 位小写十六进制 | Resident 的 Bearer 令牌（decision_fb910c43ab07），或执行层当前正在运行的 Resident |
| `service`（Portal 服务） | 只有两个登记的名字：`portal`、`ai-socket-relay` | Portal 自己的服务进程 |

- **类型和 ID 一起校验。** 用户名不能占用 Resident 和服务的命名空间，所以任何一个 ID 只对应一种身份类型。
- **服务身份标识内部执行责任方。** Portal 的调度、恢复和完成事件由服务执行；来自 User 或 Resident 的业务请求继续保留真实 initiated_by。外部调用方不能自报服务身份。

## 一次操作的身份

一次操作的身份记为 `OperationIdentity`（新写入格式 `tfi.operation-identity.v1`，读取仅接受该精确格式），包含三个身份：

| 字段 | 含义 |
| --- | --- |
| `principal` | 执行这次操作的身份，权限按它检查 |
| `initiated_by` | 发起这次操作的身份。直接调用时与 `principal` 相同；Resident 替别人完成工作时（例如处理一条用户消息），这里记录原来的请求者 |
| `producer` | 写入记录的服务，固定是服务身份，通常为 `portal` |

- **指令以调用者为根身份执行。** 调用入口确认调用者之后，指令内核（`management_portal/hatg/kernel.py`）在执行期间把操作身份设为调用者，`principal` 和 `initiated_by` 都是调用者。领域代码从 `management_common/operation_identity.py` 读取当前身份，参数或消息正文中自称的身份不被采用。
- **署名、输入内容和显示名称都不授予权限。** 权限只来自认证得到的身份和权限系统的判断。

## 消息署名

普通消息通过 `origin` 记录来历（新写入格式 `tfi.message-origin.v1`，读取仅接受该精确格式）：

| 字段 | 含义 |
| --- | --- |
| `sender` | 实际发出者：用户、Resident，或生成系统消息的服务 |
| `producer` | 保存这条消息的服务 |
| `initiated_by` | 可选。有可靠的因果信息时，记录促成这条消息的身份，例如 Lineage 中的 Effect 投递消息时记录本次运行的发起者 |

消息的 `author_id` 是发出者的 ID，用于查询和展示；它与 `origin.sender` 不一致时，这条消息被拒绝写入。

Mailbox 群体消息使用来源 v2：新写入 `tfi.message-origin.v2`，读取仅接受该精确格式。`sender` 固定为 `{"kind":"mailbox","id":"mailbox"}`，`initiated_by` 必须记录真实整理者。Mailbox 只是署名类型，不是 Principal，不能登录、持有权限或成为指令执行身份。群体消息与原成员的关系及整理流程由 decision_15f7df737ed5 规定。读取不自动改写历史记录，认证根身份、发起者和 producer 的职责不因前缀变化而改变。

- **发给 Resident 的消息都以用户输入交给模型。** 不论发出者是人还是另一个 Resident，传给 Provider 时都使用 `role=user`；正文保持原样，发出者记录在 `origin` 中。
- **回复和通知由实际产生它的一方署名。** 模型回复署名为实际执行的 Resident，系统通知署名为服务身份。
- **Family 对外发出的消息由门面署名。** Family 的成员或 steward 发给 Family 外的用户或 Resident 时，`sender` 是门面，实际发出的成员或 steward 记在 `initiated_by`（decision_44cf587c1721）。Resident 发给用户的消息由发出它的 Resident 或门面署名，`recipients` 写明收件人（decision_c524c775e0be）。
- **页面显示 Resident 的当前名称。** Chat 页面把 Resident 发出的消息作者显示为该 Resident 的资源标签，悬停时显示稳定 ID；名称读取失败时显示 ID，同名的 Resident 也能区分。名称只影响显示，不改写消息的作者和历史。
- **写入后的内容和署名不再改变。** 同一个消息 ID 的正文和署名写入后保持不变。执行层把模型输入、输出和工具记录保存为不可变的 Segment（AgentData 中的一段执行记录，见 decision_b1ab45c4b3ec）；同一个 Segment ID 再次写入时，类型、内容和元数据必须完全相同，否则写入失败。历史记录只显示保存时能够确认的署名。

## 模型执行的归属

执行层（ExecutionService 和 Execution Worker，负责调度并调用模型）为每一次模型执行分别记录：

- **请求者**：提交这次执行的身份，记在操作身份的 `initiated_by` 中；
- **执行者**：实际运行的 Resident，记在 `principal` 中，模型输出和工具记录都署名为它；
- **Provider**：这次执行使用的模型提供方。

取消一次执行或调整排队顺序时，执行层同时记录实际操作者和原来的请求者；实际操作者先经过 Resident 领域的权限检查。服务之间的内部调用只传递已经验证过的这些事实，署名本身不作为额外的授权凭据。

## Lineage 中的异步工作

Lineage 中很多工作不是由人当场发起的：定时触发的事件源、观察型事件源、排队等待投递的调用、服务重启后恢复的工作。它们使用下面的规则确定身份。

- **Event 分开保存观察对象和身份。** `subject_resident_id` 只说明 Event 观察的是哪个 Resident，例如“这个 Resident 完成了一轮工作”，它不是执行身份。手动触发的 Event 在 `identity` 中保存发起者的操作身份。
- **Effect 使用经过验证的事件身份调用指令。** 有 User 发起者时以该 User 为调用者，按该 User 自己的权限检查；没有 User 的 Resident 后续事件以实际执行的 Resident 为调用者，按它当前的权限检查。Portal 服务转发完成事件时保留已验证的来源；自主观察使用 Portal 的内部服务身份记录事实，该身份不成为接收工作的 Resident 的权限来源（decision_4adcc1ff2f2c）。
- **Resident 完成一轮工作引起的 Event 由 Portal 服务分发。** `principal` 为 Portal 服务，`initiated_by` 保留请求该轮工作的 User 或 Resident。Family 的对外投递继续通过门面；Effect 根据 `initiated_by` 检查业务权限。
- **User 是所有运行入口的可选身份。** Lineage 可设置 `run_as_principal: {kind: user, id: TFI portal}`；未配置时自动事件正常激活，Resident 使用自身身份与自身权限执行。Resident 接收的工作一律按它自己的权限判定，不借用发送方、发起者、创建者、Leader、owner 或服务的权限；需要额外权限时由持有者签发临时凭证（decision_b820faf42218）。通过 `UpdateLineage` 修改可选 User 时沿用管理权限：调用者可以指定自身 User，由管理员按自身权限指定其他有效 User，并保存审计。显式配置的账号失效、配置格式错误或权限不足仍然失败；清除配置后恢复自身身份执行。
- **算子和循环的后续操作由完成判断的 Resident 发出。** Proposal 算子或循环组件请某个 Resident 给出判断后产生的后续操作，发出者是这个 Resident，原来的 Event 继续作为因果来源。
- **排队的调用在重放前重新校验身份。** 等待投递的调用保存了原来的操作身份。重放时，系统重新确认原操作身份仍有权管理这个 Lineage，并确认实际执行的 Resident 仍是它的成员。缺少可信身份的旧调用不会被重放，需要调用者重新发起；恢复程序不补造身份。

## 开销与边界

每条消息和每次操作只保存固定数量的身份引用（最多三个），存储开销随记录数量线性增长。署名只记录事实：它不产生额外的资源权限，也不改变 Resident 在宿主机上的文件系统访问能力。
