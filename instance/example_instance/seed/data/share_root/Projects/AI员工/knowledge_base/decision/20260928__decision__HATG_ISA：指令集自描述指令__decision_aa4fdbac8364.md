---
kb_id: decision_aa4fdbac8364
type: decision
project: AI员工
created_at: '2026-09-28'
version:
  number: 16
  created_at: '2026-09-28T10:45:00Z'
  created_by:
    user: tfi-exported-user
    agent: resident_2a1fc6cd272d
  updated_at: '2026-10-19T08:28:22Z'
  updated_by:
    user: ''
    agent: resident_60ae6d0cdf74
importance: 8
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/hatg/routing.py
  related:
  - decision_898b8911a2ff
  - decision_71aa039e3211
  supersedes: []
  derived_from:
  - decision_898b8911a2ff
  - thinking_ca8f397a47de
---
# HATG ISA：指令集自描述指令

指令集自描述指令让调用者在运行时了解 ISA 本身：有哪些指令、每条指令接受什么参数，以及已经提交的某次执行现在处于什么状态。Agent 在不确定参数时先读取定义，在响应丢失时查询执行记录，都使用这一类指令。所有指令共同遵守的调用格式、执行流程和错误语义见 decision_898b8911a2ff。

## 操作的对象

- **指令目录**：所有已登记指令的声明，包括指令名、所属类别、说明、参数定义、结果定义、是否改变状态和目标对象参数。声明方式见 decision_898b8911a2ff 的“指令声明与目录”。
- **执行记录**：改变状态的指令由拥有服务保存的恢复记录，只读调用不保存。记录内容、保留期限、受保护状态、恢复依赖和到期防重栅栏统一由 decision_898b8911a2ff“重试、幂等与并发”规定；本篇只规定怎样定位和读取，执行记录不承担永久审计。

## 指令一览

| 指令 | 改变状态 | 作用 | 默认方案要求 |
| --- | --- | --- | --- |
| `ListInstructions` | 否 | 列出指令目录 | 免检 |
| `GetInstructionSchema` | 否 | 读取一条指令的完整定义 | 免检 |
| `GetInstructionExecution` | 否 | 读取一次指令执行的当前状态 | 对 `instruction_id` 所指指令执行回执的 lineage 有 @viewer；无所属对象时免检 |

## 指令说明

### `ListInstructions`

**列出已登记的指令。**

- 参数：`category`（可选，只列出某一类）。
- 结果：指令列表，每项包含指令名、类别、拥有服务、一句话说明、是否改变状态和是否流式。`RunResourceAction` 下按资源类型列出各类型声明的动作。
- 结果另带 `guide`：Agent 指引的 Portal 内路径 `/guide/agent.md`。已经持有凭据、不经过网页的 agent 在发现指令时就能找到按任务组织的说明，不必一次读完整个目录（decision_3286d4167180）。

### `GetInstructionSchema`

**读取一条指令的完整定义。**

- 参数：`instruction`（指令名）。
- 结果：说明、参数定义（名称、类型、是否必填、说明、取值范围）、结果定义、是否改变状态、是否流式和目标对象参数。有参数组时另给出 `one_of`（恰好给其中一组）或 `any_of`（这些参数至少给一个，给出的每个都按映射判定）。

### `GetInstructionExecution`

**读取一次指令执行的当前状态和它产生的业务对象引用。**

- 参数使用两种互斥定位：`instruction_id`，或原提交的 `instruction` 与 `idempotency_key` 二者齐全。按原请求查询只匹配当前已认证调用者的指令与键，不允许指定他人身份。
- 结果：执行状态、业务对象引用和失败信息。
- 执行记录保存在执行该指令的拥有服务中。按执行 ID 查询时，入口根据其中的服务标记路由；按原请求查询时，入口使用原指令目录声明的 owner，读取当前调用者原幂等记录，再走相同的执行记录授权。查询不创建执行、不重放业务，也不以缺失记录证明从未受理。
- 执行记录按它的范围判定。默认方案把本指令映射到固定节点，所有登录的调用者都能发起查询；属于某个 Lineage 的记录按读取这个 Lineage 判定，即询问记录中保存的 `lineage_id` 上的 GetLineage，原调用者也遵守此要求；调用者私有范围的记录只返回给原调用者。这是“判定对象可以来自已保存的记录”的做法（decision_898b8911a2ff“声明格式”）：判定对象取自记录，不取自调用参数，本指令的映射也不引用任何未声明的参数。
- 结果不包含原始完整响应。业务对象的最新内容通过相应类别的读取指令获得。记录已到期回收时返回 `instruction.not_found`，不会把到期摘要作为旧执行结果返回；只读调用的执行 ID 也没有对应持久记录。
- 查询返回 `instruction.not_found` 不能证明原操作未受理。到期旧键仍由拥有服务的防重栅栏拒绝，不会因查不到记录就自动重放；拒绝码与结果核对义务见 decision_898b8911a2ff“重试、幂等与并发”。调用者应先检查业务对象、长期 Run 事实或执行轨迹，再决定是否提交新操作。

## 参数名契约

下表列出 arguments 内的顶层参数名；必填条件、类型和枚举以 GetInstructionSchema 为准。

| 指令名 | arguments 参数名 |
| --- | --- |
| `ListInstructions` | `category` |
| `GetInstructionSchema` | `instruction` |
| `GetInstructionExecution` | `instruction_id`、`instruction`、`idempotency_key` |
