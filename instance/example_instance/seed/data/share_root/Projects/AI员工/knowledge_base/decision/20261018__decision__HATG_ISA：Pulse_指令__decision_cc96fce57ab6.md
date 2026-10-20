---
kb_id: decision_cc96fce57ab6
type: decision
project: AI员工
created_at: '2026-10-18'
version:
  number: 6
  created_at: '2026-10-18'
  created_by:
    user: ''
    agent: resident_7765faecc45a
  updated_at: '2026-10-19T08:40:26Z'
  updated_by:
    user: ''
    agent: resident_511e229bc8c3
importance: 5
ai_generated: false
links:
  source: []
  related: []
  supersedes: []
  derived_from: []
---

# HATG ISA：Pulse 指令

Pulse 指令读取 Portal 按日、周、月整理的知识动态，并让用户把自己的个人动态条目公开或撤回。Pulse 的数据来源、整理方式和页面见 decision_865ac8ebef25；所有指令共同遵守的调用格式、执行流程和错误语义见 decision_898b8911a2ff。本组指令都由 Portal Web 执行。

## 操作的对象

- **知识动态**：系统按日、周、月三个周期汇总的公共摘要，能调用 `GetPulse` 的人读到同一份。
- **个人动态条目**：系统为每位用户整理的个人工作条目，读取按目标 User 的权限判定（见 `GetPulse`），按 `source_period`（周期）、`source_key`（这一期的键）和 `item_id` 定位。
- **公开记录**：用户选择公开的一条个人动态条目，有自己的 `publication_id`，显示在所有人的 Pulse 中。

## 指令一览

| 指令 | 改变状态 | 作用 | 默认方案要求 |
| --- | --- | --- | --- |
| `GetPulse` | 否 | 读取知识动态与一位 User 的个人动态 | 对 `user_id` 有 @admin |
| `PublishPulseItem` | 是 | 公开或撤回一条个人动态条目 | 免检 |

### `GetPulse`

**读取按日、周、月整理的知识动态，以及一位 User 的个人动态条目。**

- 参数：`user_id`（必填，个人动态所属 User 的用户名；读自己时写自己的用户名）。
- 结果：三个周期的公共摘要、已公开的条目和这位 User 的个人条目。谁能读取由权限系统决定：默认方案要求调用者对这个 User 有 `@admin`；能调用的人都得到同一份公共摘要。

### `PublishPulseItem`

**公开或撤回调用者自己的一条个人动态条目。**

- 参数：`source_period`、`source_key`、`item_id`、`published`（必填；`true` 公开，`false` 撤回）。
- 结果：更新后的动态；公开时另返回公开记录。
- 公开记录的 ID 由所有者和条目三项确定，同一条目重复公开得到同一个公开记录。公开时服务按来源重新核对条目内容和所有者；撤回时只能撤回调用者自己公开的记录。

## 参数名契约

下表列出 arguments 内的顶层参数名；必填条件、类型和枚举以 GetInstructionSchema 为准。

| 指令名 | arguments 参数名 |
| --- | --- |
| `GetPulse` | `user_id` |
| `PublishPulseItem` | `source_period`、`source_key`、`item_id`、`published` |
