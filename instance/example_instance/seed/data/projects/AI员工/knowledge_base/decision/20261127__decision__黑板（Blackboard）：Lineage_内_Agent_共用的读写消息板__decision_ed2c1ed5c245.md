---
kb_id: decision_ed2c1ed5c245
type: decision
project: AI员工
created_at: '2026-11-27'
version:
  number: 1
  created_at: "2026-11-27"
  created_by:
    user: ""
    agent: "resident_9edb49ed68fb"
  updated_at: "2026-11-26T18:10:03Z"
  updated_by:
    user: ""
    agent: "resident_9edb49ed68fb"
importance: 7
ai_generated: false
links:
  source: []
  related:
  - decision_4adcc1ff2f2c
  - decision_71ba0494b93e
  - decision_898b8911a2ff
  - decision_3e063aac15b3
  - decision_15f7df737ed5
  - decision_600ae27b0538
  supersedes: []
  derived_from:
  - decision_4adcc1ff2f2c
metaness: 5
---

# 黑板（Blackboard）：Lineage 内 Agent 共用的读写消息板

Agent 之间已有两类直接通信：`DeliverMessage` 把一条消息交给某个 Resident 并触发它工作；Event、Route 和 Effect 把事实沿图传给指定对象。黑板提供第三类：间接通信。若干 Resident 连接到同一块黑板，有写权限的连接可以往上写条目，有读权限的连接可以读取已有条目。写什么、何时写、何时读由 Agent 自己决定，黑板不替任何人调度，也不要求谁必须读。它适合“公布结论、认领任务、留下进度、让后来者自己查看”这类不需要一对一送达的协作。

## 与相邻设计的边界

| 设计 | 对象与方式 | 与黑板的区别 |
| --- | --- | --- |
| `DeliverMessage` / Mailbox | 点对点；收件人被激活；留在收件人的对话里 | 黑板没有收件人，写入不激活任何人 |
| Graph State（decision_71ba0494b93e） | 少量键值，只保留当前值，支持条件写入 | 黑板保存带作者和先后顺序的条目历史，面向阅读，不做条件判断和计数 |
| 公共讨论与 Chatroom | 面向用户的帖子、回复与聊天室 | 黑板只在一个 Lineage 内供其 Resident 使用，条目有保留上限 |

## 黑板是图对象

黑板是 Lineage 图中的一种对象（种类 `blackboard`），由 `ApplyLineageChanges` 创建、配置和删除，随 Lineage 保存、迁移和删除（decision_4adcc1ff2f2c 的扩展约束：先定义端口、内容结构、编译产物和恢复边界）。它没有输入输出端口，不参与 Packet 传播。

对象配置：`title`、`description`（说明这块黑板用来放什么、什么时候该读；黑板的存在不自动进入 Resident 的上下文，Resident 通过 `GetLineage` 读取 Lineage 定义中的黑板对象与 `attach` 连线，或由任务说明指明 `board_id`，再用 `ListBoardEntries` 读取）、`retention`（见下）。

## 连接

Resident 通过一条 `attach` 连线连到黑板，连线配置 `access`：`read`、`write` 或 `read_write`。`attach` 与 `target`、`watch` 一样是被动绑定，不触发执行。同一个 Lineage 内的任何 Resident 都可连接，连接关系写在 Lineage 定义里，由 Lineage 编辑者维护；Resident 自己不能给自己增减连接。Resident 离开 Lineage 或被删除时，它的连接随之删除，已写条目保留，作者显示为已删除的 Resident。

## 条目

条目是追加后不可修改的记录：

- `entry_id`、`seq`（该黑板内自增的序号，唯一决定先后）、`author`（调用者的 Resident 或用户 ID，由服务按真实身份写入，不接受调用方自报）、`created_at`。
- `kind`：作者自定的短标签（最多 32 个字符，例如 `note`、`claim`、`result`），供读取时筛选；黑板不规定取值。
- `text`：Markdown 文本，最多 16 KiB；`data`：可选 JSON，最多 16 KiB；`refs`：可选的资源地址列表，附件和文件只以地址引用，黑板不保存文件内容。
- `supersedes`：可选，指向同一黑板上自己或他人较早条目的 `seq`，表示“本条更正或取代那条”；读取时较早条目带 `superseded_by`。更正只靠新增条目表达，不修改旧条目。
- `expires_at`：可选，不得晚于黑板配置的最长保留时间；到期后条目按过期清理。

同一条写入请求以幂等键去重：重试不会产生重复条目。

## 保留与删除

黑板自己决定条目何时消失，Agent 不能决定别人的条目去留，也不会无限增长。

- **保留策略写在黑板配置里**，由 Lineage 编辑者设置：`max_entries`（默认 200，站点上限 1000）、`max_total_bytes`（默认 1 MiB）、`max_age`（可选，条目超过这个年龄就清理）、`max_entry_ttl`（条目 `expires_at` 的上限）。
- **超限时从最旧的条目开始清理**，每次写入后检查；到期条目同样清理。清理不通知作者，读取结果始终返回当前最早保留的 `earliest_seq`，调用方据此知道自己的游标之前的条目已经不在。
- **要长期保留的内容不放黑板。** 需要“当前值”的用 Graph State，需要“成果”的写成文件或知识后在条目里引用。
- **撤回：** 黑板所属 Lineage 内的 Resident（有写连线）可以撤回该黑板上的任何条目，不需要管理员权限；不属于该 Lineage 的调用者只能撤回自己写的条目。撤回后正文、`data` 与 `refs` 被清空，条目以 `retracted` 标记保留序号，读取游标不出现断档；它同样受保留策略约束。

## 读取与已读位置

- 读取是分页、按 `seq` 递增，可按 `author_id`、`kind` 筛选，结果带 `latest_seq`、`earliest_seq` 与 `next_cursor`。
- 服务为每个读者在每块黑板上保存一个**已读位置**，由读者显式推进；未给游标的读取从已读位置之后开始，所以 Resident 重启或换会话后能接着读，也不会因为读过而丢失条目。
- 想被唤醒的场景用 Event Source `blackboard.entry_created`（`watch` 连线观察黑板，可按 `kind` 过滤），再接 Route 激活某个 Resident；不接就没有人被打扰。
- 黑板内容来自其他 Agent，读取结果按对等来源的数据交给 Resident，带作者标识；Resident 不把条目里的文字当作系统或用户指令执行。

## 指令

黑板的创建、配置、连接和删除走 Lineage 定义指令（`ApplyLineageChanges`），不另设指令。数据面指令：

| 指令 | 改变状态 | 作用 | 默认方案要求 |
| --- | --- | --- | --- |
| `CreateBoardEntry` | 是 | 写入一个条目 | 对黑板所属 Lineage 有 @contributor；Resident 调用者须有 `write` 或 `read_write` 连接 |
| `ListBoardEntries` | 否 | 按序读取条目，可筛选；默认从读者已读位置之后开始 | 对黑板所属 Lineage 有 @viewer；Resident 调用者须有 `read` 或 `read_write` 连接 |
| `WatchBoard` | 否（流式） | 流式等待新条目 | 对黑板所属 Lineage 有 @viewer；Resident 调用者须有 `read` 或 `read_write` 连线 |
| `SetBoardReadPosition` | 是 | 把调用者的已读位置推进到某个 `seq`，不后退 | 对黑板所属 Lineage 有 @viewer；Resident 调用者须有 `read` 或 `read_write` 连线 |
| `DeleteBoardEntry` | 是 | 撤回一个条目：所属 Lineage 内的 Resident 可撤回任何条目，其他调用者只能撤回自己写的 | 对黑板所属 Lineage 有 @contributor；Lineage 内 Resident 须有 `write` 或 `read_write` 连线；处理器对不属于该 Lineage 的调用者核对作者身份 |

## 参数名契约

下表列出 arguments 内的顶层参数名；必填条件、类型和枚举以 GetInstructionSchema 为准。黑板指令的权限都对 `lineage_id` 判定，`board_id` 指定该 Lineage 内的黑板。

| 指令名 | arguments 参数名 |
| --- | --- |
| `CreateBoardEntry` | `lineage_id`、`board_id`、`text`、`kind`、`data`、`refs`、`supersedes`、`expires_at` |
| `ListBoardEntries` | `lineage_id`、`board_id`、`cursor`、`limit`、`author_id`、`kind` |
| `WatchBoard` | `lineage_id`、`board_id`、`after_cursor`、`kind` |
| `SetBoardReadPosition` | `lineage_id`、`board_id`、`seq` |
| `DeleteBoardEntry` | `lineage_id`、`board_id`、`entry_id` |

用户（人）读写不需要连线，凭对 Lineage 的权限即可，条目中的作者是该用户。条目写入、读取与撤回都是指令执行，在回执中留痕；写入本身不产生 Trigger Path，除非有 `blackboard.entry_created` 事件源观察它。

## 不变量

- 条目一经写入，`seq`、作者、正文不变，只会被撤回或清理。
- `seq` 在一块黑板内严格递增，被清理或撤回后不重用。
- 写入、清理与已读位置的修改在同一个事务边界内完成；并发写入不会产生重复或乱序的 `seq`。
- 黑板不改变 Lineage 的定义版本，写条目不使 Lineage 定义失效。
- 黑板只属于一个 Lineage，不跨 Lineage 共享；迁移 Lineage 对象时，黑板与连线随所属 Lineage 的规则一起处理。
