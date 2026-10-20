---
kb_id: decision_91eb3626d323
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 14
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-18T16:15:48Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_73cb83291316
importance: 9
metaness: 2
ai_generated: true
links:
  source:
  - ${code_root}/management_portal/execution/session_manager.py
  - ${code_root}/management_portal/execution/runtime.py
  - ${code_root}/management_portal/execution/agent_data_projection.py
  - ${code_root}/management_portal/execution/agent_data_store.py
  - ${code_root}/tests/cases/test_execution_layer.py
  - ${code_root}/tests/cases/test_execution_context.py
  related:
  - decision_8ac0b5a6c0e5
  - decision_d40f59f5508f
  - decision_6ad0384e020d
  - decision_c47a13e8b962
  - decision_b1ab45c4b3ec
  - decision_3e8c2a7f91d4
  - decision_6b9e42f1c8a7
  supersedes: []
  derived_from: []
---
# SessionManager 与 Provider Binding

一个 Resident 可以和模型连续对话很多轮，靠的是模型服务供应方（Provider）那边保存着的会话：Codex 叫 thread，Claude Code、Kimi Code 叫 session。Portal 不直接把业务数据挂在 Provider 的会话 ID 上，而是给每段会话一个自己的稳定地址 `binding_id`，再记下这个地址当前指向哪个 Provider 会话。这条对应关系称为 **Provider Binding**。

**SessionManager**（会话管理器）是 ExecutionService 中维护这条对应关系的组件。每次模型执行开始前，它把请求中的会话地址解析成 Provider 会话；Provider 接受轮次、报告原生会话 ID 时建立对应关系；执行完成后再更新上下文附加记录。重启会话、Fork、清空上下文等业务流程需要替换或移动会话时，也通过它完成。本文说明会话怎样创建、续接、分叉、压缩和释放，业务流程怎样替换会话，以及“这个会话是否已经接收过输入”的回执怎样保存。

## 先认识几个身份

- **会话地址（`binding_id`）**：Portal 自己的稳定会话地址。Resident 的会话地址是 `resident:<resident_id>`。
- **Provider 会话**：Provider 维护的原生会话，由 Provider、**路由**（这个会话属于哪个执行账号，见 decision_d40f59f5508f）和原生会话 ID 三者共同确定，Portal 给它一个内部引用 `provider_session_ref`。
- **RuntimeSession**：一次执行开始前生成的只读会话快照，交给 Provider Adapter 使用。
- **上下文附加记录**：Portal 为某一轮附加给模型的系统上下文的摘要，包括内容指纹、最近一次附加时间和累计次数。

这些记录都保存在 AgentData（规范内容存储，decision_b1ab45c4b3ec）中。SessionManager 负责解释和提交会话对应关系的变化；Provider Adapter 负责原生调用；执行编排器 Runtime 负责执行顺序；SocietyService 决定业务上何时 Fork、重启、清空或删除会话。

```text
Portal 调用方
  │ 使用稳定的 binding_id
  ▼
SessionManager ──读取──> Provider Binding（AgentData）
  │                         │ 指向
  │ 生成 RuntimeSession     ▼
  └──────────────────> Provider 会话（Provider + 路由 + 原生会话 ID）
                              │
                              ▼
                       Provider Adapter 调用
```

一个会话地址同一时刻最多指向一个 Provider 会话；同一个 Provider 会话也只能被一个会话地址占用。已经存在的会话不能换路由，避免同一个原生会话被另一个账号接管。

## 设计点

- **无状态请求不读写任何会话对应关系。**
- **持续会话在执行前解析，在 Provider 接受轮次时建立对应关系。** 准备阶段只读取和校验。收到轮次开始事件和原生会话 ID 后即可绑定，随后失败或取消也保留已经接受输入的会话，以便恢复和判断输入是否送达。
- **`create` 和 `fork` 要求目标地址还不存在。** 恢复同一次执行时例外：目标已经在上一次尝试中建立，就以它为准。
- **`compact` 和 `release` 要求目标地址已经存在。**
- **`turn` 可以继续已有会话，也可以在首次接受输入时建立新会话。**
- **`fork` 从来源地址复制上下文。** 来源必须存在，并且与请求的 Provider 和路由一致；目标建立后独立续接。
- **`release` 默认只让 Provider 执行释放动作，Portal 的对应关系保留。** 请求的 `runtime_options.delete_binding_on_release` 为真时，才删除 Portal 的对应关系；删除对应关系不保证 Provider 立即清除它那边的数据。
- **已有会话沿用它保存的路由。** Provider 返回的路由与已有会话的路由不同时，执行失败。
- **上下文附加次数只在确实附加后增加。** Runtime 确认这一轮真的把上下文发给了模型，才增加次数并更新时间和指纹；恢复执行时不会仅凭准备阶段的记录重复计数。

## RuntimeSession 包含什么

`prepare` 返回的快照包含：

| 内容 | 说明 |
|---|---|
| 会话模式 | `stateless` 或 `persistent` |
| 会话地址 | `binding_id`，无状态请求为空 |
| 原生会话 ID | 已有会话的 ID；首次调用或无状态请求为空 |
| 路由 | 已解析的路由 ID |
| 是否新会话 | 本次是否预期建立新的 Provider 会话 |
| Provider 会话引用 | `provider_session_ref`，供后续按条件更新使用 |
| 上下文附加记录 | 最近的指纹、时间和累计附加次数 |
| Harness 工具名 | 这个会话已知可用的 Harness 工具 |

## Runtime 使用的两个步骤

### `prepare`：执行前解析会话

- 先确认会话地址属于请求的执行者。`resident:<id>` 形式的地址直接从地址取得执行者；其他地址从这个 Provider 会话最早的 Execution 推导执行者，binding 本身不存执行者字段。执行者不同、或已有会话属于另一个 Provider 时失败。
- 已有会话时，使用它保存的原生会话 ID 和路由。
- `fork` 且目标还不存在时，读取来源会话：来源必须存在、Provider 相同；请求和来源都有路由时两者必须一致。
- 新会话使用提交阶段已经解析好的路由。SessionManager 不自己选择账号。

### `complete`：执行成功后更新对应关系

Provider 调用和 AgentData 内容写入都成功后，Runtime 把原生会话 ID、路由、上下文指纹和本轮观察到的 Harness 工具交给 `complete`：

- 无状态请求或没有会话地址时不写。
- `release` 且 `delete_binding_on_release` 为真时，以准备阶段看到的 `provider_session_ref` 为条件删除对应关系；迟到的释放不会删除后来替换上的新会话。
- Provider 没有返回原生会话 ID 时，不创建对应关系。
- 先按 Provider、路由和原生会话 ID 建立或复用唯一的 Provider 会话记录，再把会话地址绑定到它。
- 本轮观察到的 Harness 工具名与已有集合合并。
- 确实附加了上下文时，更新最后附加时间和指纹，并把次数加一。

会话重启在提交前重新读取对应关系，只比较 Provider、路由和原生会话身份。上下文附加计数、更新时间或派生字段变化不会使同一会话被误判为替换。

## 一轮对话的工作顺序

1. Runtime 调用 `prepare` 得到会话快照，再让 AgentData 投影器记录本次执行。
2. Provider Adapter 使用快照中的原生会话和路由执行这一轮。收到开始事件时登记会话绑定与不可变的输入接受回执；后续失败或取消不会撤销这份事实。
3. Provider 完成后，Runtime 在 Scheduler 中保存“Provider 已完成”恢复点。
4. Runtime 写完 AgentData 中的内容，最后调用 `complete` 更新对应关系。

这个顺序把 Provider 的外部动作和 Portal 的记录分开提交。Provider 已经完成而后续写入中断时，恢复可以从恢复点取得已保存的结果，跳过第二次 Provider 调用，再补写 AgentData 和对应关系。`complete` 已经成功但响应丢失时，唯一约束保证同一对应关系可以重复提交。恢复同一次执行时，保留原执行的接受时间、提交者、执行者和 Provider 等不可变来源字段；“Provider 已完成”的恢复点可以在重建 Runtime、重新连接数据库后完成提交，不会因为 Portal 这一侧提交失败而重新调用模型。

## 业务流程使用的会话事务

除了 Runtime 的两个步骤，SessionManager 还为业务流程提供一组会话事务。它们共用同一套约束；业务调用方只提交会话地址、预期状态和已经选好的操作，不直接组合 AgentData 的底层写入。

| 接口 | 作用与失败规则 |
|---|---|
| `provider_binding` | 按地址读取当前对应关系，不存在时返回空对象。可选 `expected_binding` 核对 Provider 会话引用、Provider、原生会话 ID 和路由，不一致时报告“对应关系已变化”。 |
| `provider_binding_routes` | 批量只读取路由，省略没有命中的地址。 |
| `bind_session` | 在同一个 AgentData 事务中建立或复用 Provider 会话记录并绑定地址。已有对应关系的身份不可改变；相同绑定可以重放；冲突时本次新建的 Provider 会话记录一并回滚。上下文摘要和工具集合只在显式提供时更新。调用方只提供会话与上下文参数，不传无作用的 actor 或 agent_id。 |
| `replace_unaccepted_session` | 恢复 Provider 已丢失且尚未接收输入的空会话。在事务中锁定原对应关系，核对其身份和接收回执，且新会话必须位于原路由；并发替换或已有输入时拒绝恢复。 |
| `replace_session` | 把事先准备好的临时地址原子地替换到正式地址上，消费临时地址，返回替换前后的对应关系回执（`previous_binding`、`current_binding`）。可选 `expected_binding` 在写事务内核对 Provider、原生会话 ID 和 Provider 会话引用；显式传入空对象 `{}` 表示预期正式地址还没有会话，这时正式地址已被并发建立就拒绝替换，双方都保持原样。不传预期时，要求正式地址已经存在。 |
| `restore_session` | 用替换回执回滚一次替换。回执中的两个对应关系都必须属于这个地址，并且当前对应关系仍与回执一致；之后又被替换过时拒绝回滚。回执的替换前为空时，回滚就是删除这次替换上的对应关系，恢复到没有会话。 |
| `release_session` | 只删除 Portal 的对应关系，保留原生会话记录和完整轨迹。可选 `expected_provider_session_ref` 在删除事务内比较，防止迟到的清理删掉后来替换上的会话；地址已经不存在时同样成功。 |

业务身份和 AgentData 分属两个数据库。以 Resident 重启会话为例（decision_c47a13e8b962）：SocietyService 先在状态锁外准备好新的 Provider 会话，再在一次短提交中检查业务状态、按预期替换对应关系并保存 Resident 身份；身份保存失败时，用替换回执有条件地回滚。慢的 Provider 操作、等待终态和资源同步都在状态锁之外完成。

SocietyService 通过执行层适配器（`execution_client.sessions()`）在自己的进程内直接调用这些接口，Resident 的短状态锁内不增加网络等待；ExecutionService 不为它们提供 RPC。它们不发起 Provider 工作，也不决定业务的重启策略。

读取接口不接收 `actor`：它们是可信执行层内部的能力。页面上的用户仍由上层按真实对象授权。

## 会话接收回执

一个 Resident 的消息是否已经被模型接收，决定了失败后能不能安全续跑。SessionManager 的 `has_accepted_input(binding_id, agent_id)` 回答“这个会话是否已经接收过这个执行者的输入”。

- **AgentData 为每个“Provider 会话 + 执行者”保存一条不可变的接收回执。** Provider 会话的身份包含 Provider、路由和原生会话 ID。查询在同一个数据库读快照中先解析当前对应关系、再找回执，不依赖旧的执行记录是否还在。
- **回执在 Provider 确认接收时生成。** 执行投影确认到非空的 Provider 轮次时，在同一个本地事务中保存这次执行的接收事实和会话回执。回执记下首次接收的轮次、执行身份和时间，之后的消息复用它。执行开始时还没有会话对应关系的，在绑定完成的事务中补生成回执。执行失败或取消不会抹掉已经确认的接收；只创建空会话不会产生回执。
- **回执跟着 Provider 会话走。** 替换成新会话后，只查询新会话的回执。其他执行者的回执、派生复制出来的对话或轨迹，都不能证明当前执行者的输入已被接收。
- **回执独立且唯一。** 每个已接收输入的会话与执行者组合恰有一条回执；正常读取路径只读这项事实，不扫描或推测历史。

## 状态与失败处理

SessionManager 不管理排队、领取资格、取消或 Worker 租约，这些属于 Scheduler（decision_8ac0b5a6c0e5）；它也不负责把失败的执行写成 AgentData 终态，那是 Runtime 通过 AgentData 投影器完成的。

AgentData 中底层的会话维护方法只由执行层内部组合使用。SocietyService 负责把 Resident 身份转换成会话地址、决定业务操作的时机，不解释底层绑定规则。对话、轨迹和上下文的读取仍使用各自的内容接口。

每个会话事务在一个 AgentData 数据库事务中提交。同值重放、按条件替换与回滚、对已不存在的地址释放，各自保持上面的语义。会话事务不处理用户的停止请求，取消意图及其优先级由 Resident 状态入口和 Scheduler 保证。

会话恢复时，压缩后的启动指令附加判断直接读取当前上下文首个 compaction Segment 的 `finished_at`。这个时间表示压缩完成；读取过程不依赖 Segment 的 `created_at` 或 Execution 回执。上下文没有压缩，或历史压缩记录没有完成时间时返回空值，不制造当前时间，也不阻止正常会话启动。

## Claude Code 的会话与路由

Claude Code 的 profile ID 就是它的路由。原生订阅和 DeepSeek 兼容 profile 都使用同一套 Provider 会话和对应关系，没有 Claude 专用的历史库或业务绑定。第一轮开始后提交原生会话 ID 和实际路由；同一路由上的 Fork 新建独立 Provider 会话，使用父会话的账号路由；子 Resident 仍拥有自己的稳定会话地址与输入回执。跨账号重启由上游创建新会话并迁移上下文。生产环境的会话记录保存在共享 PostgreSQL 中，账号目录只保存 Provider 的登录与恢复材料。
