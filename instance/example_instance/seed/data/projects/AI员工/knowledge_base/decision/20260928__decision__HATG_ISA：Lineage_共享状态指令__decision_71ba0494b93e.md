---
kb_id: decision_71ba0494b93e
type: decision
project: AI员工
created_at: '2026-09-28'
version:
  number: 11
  created_at: '2026-09-28T10:45:00Z'
  created_by:
    user: tfi-exported-user
    agent: resident_2a1fc6cd272d
  updated_at: '2026-10-27T12:52:35Z'
  updated_by:
    user: ''
    agent: resident_73cb83291316
importance: 8
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/society_service/isa/shared_state.py
  - ${code_root}/management_portal/society_service/lineage_graph_state.py
  related:
  - decision_898b8911a2ff
  - decision_4adcc1ff2f2c
  - decision_68215b3a825b
  supersedes: []
  derived_from:
  - decision_898b8911a2ff
  - thinking_ca8f397a47de
---
# HATG ISA：Lineage 共享状态指令

Lineage 共享状态指令读写一个 Lineage 在运行中保存的键值数据，例如计数、进度或“某一步是否已经完成”的标记。Lineage 中的 Resident 和组件用它协调后续执行，事件源可以在某个值满足条件时产生 Event。所有指令共同遵守的调用格式、执行流程和错误语义见 decision_898b8911a2ff。

## 操作的对象

- **状态单元（cell）**：一个键和一个 JSON 值。每个 Lineage 最多 128 个状态单元，单个值最大 64 KiB，全部值合计最大 512 KiB。
- **变化计数**：共享状态有独立的变化计数（`revision`），每次实际改变值时加一，随 `state.changed` 事件记录。它与 Lineage 定义的变化计数分开，写状态不会改变 Lineage 定义。指令结果不返回它，也不接受它作为前提；需要“只在当前值符合预期时写入”时，给 `UpdateSharedState` 或 `DeleteSharedState` 带上 `expected_value`。

共享状态在运行中的使用方式（`state.changed` 与 `state.condition_met` 事件源、调用共享状态指令的 Effect）见 decision_4adcc1ff2f2c，实现中这组数据称为 Graph State。

## 指令一览

| 指令 | 改变状态 | 作用 | 默认方案要求 |
| --- | --- | --- | --- |
| `GetSharedState` | 否 | 读取全部状态或一个状态单元 | 对 `lineage_id` 有 @viewer |
| `UpdateSharedState` | 是 | 写入一个状态单元；带期望值时只在当前值相符时写入 | 对 `lineage_id` 有 @contributor |
| `DeleteSharedState` | 是 | 删除一个状态单元；带期望值时只在当前值相符时删除 | 对 `lineage_id` 有 @contributor |

## 指令说明

### `GetSharedState`

**读取一个 Lineage 的全部共享状态，或其中一个状态单元。**

- 参数：`lineage_id`、`key`（可选，省略时读取全部）、`fields`（可选，只读取这个状态单元中的这些顶层字段，需要同时指定 `key`）。
- 结果：`state` 为状态值，以 key 为键。指定 `key` 时另给出 `value`，即这个状态单元的值，不存在时为 null。Effect 脚本的变量路径只能写固定的段，按计算出的 key（例如 Family 的 `family_router_context_<门面>`，decision_44cf587c1721）读取时用 `value`。

### `UpdateSharedState`

**写入一个状态单元；附带期望值时，只在当前值等于期望值时写入。**

- 参数：`lineage_id`、`key`、`value`、`expected_value`（可选）。
- 结果：键、当前值、原值、`matched` 和 `changed`。写入时当前值即新值；期望值不符时当前值是未改变的原值。
- 写入与原值相同时不推进变化计数。
- 期望值用于多个参与者竞争同一个状态时只让一方成功：不存在的状态单元按 null 比较，所以 `expected_value: null` 只在状态单元尚未设置（或值为 null）时写入。当前值不等于期望值时返回 `matched=false`，这属于正常结果，指令不会失败。没有期望值时 `matched` 恒为 true。

### `DeleteSharedState`

**删除一个状态单元；可以附带期望值，只在当前值匹配时删除。**

- 参数：`lineage_id`、`key`、`expected_value`（可选）。
- 结果：`deleted`、`matched` 和 `changed`。
- 只有状态单元存在并被删除时才推进变化计数。

## 参数名契约

下表列出 arguments 内的顶层参数名；必填条件、类型和枚举以 GetInstructionSchema 为准。

| 指令名 | arguments 参数名 |
| --- | --- |
| `GetSharedState` | `lineage_id`、`key`、`fields` |
| `UpdateSharedState` | `lineage_id`、`key`、`value`、`expected_value` |
| `DeleteSharedState` | `lineage_id`、`key`、`expected_value` |
