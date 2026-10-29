---
kb_id: decision_5b5653c3eaa5
type: decision
project: AI员工
created_at: '2026-09-28'
version:
  number: 64
  created_at: '2026-09-28T10:45:00Z'
  created_by:
    user: tfi-exported-user
    agent: resident_2a1fc6cd272d
  updated_at: '2026-10-27T12:50:35Z'
  updated_by:
    user: ''
    agent: resident_73cb83291316
importance: 8
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/society_service/function_block_domain.py
  - ${code_root}/management_portal/society_service/graph_storage.py
  - ${code_root}/management_portal/hatg/catalog/function_block.py
  related:
  - decision_898b8911a2ff
  - decision_4adcc1ff2f2c
  - decision_4637ca4a69e2
  - decision_68215b3a825b
  - decision_d19b9909a674
  - decision_44cf587c1721
  - decision_b04cac4188bb
  supersedes: []
  derived_from:
  - decision_898b8911a2ff
  - thinking_ca8f397a47de
---

# HATG ISA：Function Block、画布选区与指令

Function Block 是 Lineage 内的封装层次。它把一组选中的 Resident、组件、连线或内层 Function Block 组织在同一个边界中，供画布折叠展示和整体管理。封装后，各元素保留自己的 ID、配置、连接关系和执行行为；运行仍由普通 Resident、组件和连线完成。

模板负责描述可以施加的内容，施加后产生的持久结构可以封装为 Function Block。`template_id` 记录这个块的来源，可以为空；修改模板不改写已经生成的元素。模板的种类、施加过程和指令见[HATG ISA：模板与指令](/r/decision_b04cac4188bb)。Family 是带有成员名册和对外门面的特殊 Function Block，其路由、角色与指令见[Family](/r/decision_44cf587c1721)。

本篇四条指令属于 `function_block` 类别，owner 为 `lineage`。共同调用格式、权限检查、幂等与错误语义见 [HATG ISA 总则](/r/decision_898b8911a2ff)。

## 元素归属与嵌套

画布中双击 Function Block 图标打开其 Menu。修改标题成功后，画布名称、打开中的 Menu 标签和标题输入框同步显示已提交的名称，无需关闭或刷新。Menu 头部关闭按钮与 Escape 均关闭当前弹层。

一个块属于一条 Lineage，ID 使用 `fbi_<12位小写十六进制码>`。调用者用 `lineage_id` 和 `function_block_id` 定位它。

块的直接元素保存在 `elements` 中，每项为 `{id, kind, key}`：

| 字段 | 含义 |
| --- | --- |
| `id` | 当前 Lineage 中已有元素的 ID |
| `kind` | `resident`、`component`、`wire` 或 `function_block` |
| `key` | 可空的局部名称，供模板或 Family 识别自己生成的元素；手动封装通常为空 |

每个元素最多有一个直接所属块。块可以作为另一个块的元素，形成无环的树状层次；重复归属、自我包含和嵌套环均被拒绝。包含内层块时，外层只登记这个内层块的 ID，内层元素继续由内层块管理。

例如，块 A 包含 Resident R 和块 B，块 B 包含事件源 E。R 的直接所属块是 A，E 的直接所属块是 B；删除普通块 A 时，会递归处理 B 和 E。把 B 从 A 的元素中移出后，B 保留自己的元素，成为独立的块。

封装只包含明确提交的 `element_ids`。选中一个组件不会自动把相邻组件或连线加入块。跨越边界的连线继续指向真实的内部对象与端口；折叠节点上的连接位置由画布投影计算。修改内部对象使用各对象原有的图编辑接口，改动立即作用于同一组对象。

删除某个元素后，提交过程会移除它的归属记录。普通块变空时自动移除；内层空块移除后导致外层普通块也变空的，继续向外清理。Family 的结构维护遵守 Family 的成员与角色规则。

## 画布选区与封装确认

原生 HATG 画布的选区由当前可见的 Resident、组件、Function Block 和真实连线组成。展开的块以边界保留自身身份；折叠块内部的对象和聚合投影连线不进入选区。这里的可见性由折叠、视图和过滤状态决定，与对象是否暂时位于视口之外无关。

普通点击建立当前对象，按 Shift、Ctrl 或 Meta 点击开始多选时，只继承仍可见的单选锚点。选中连线时锚点是连线自身。打开的 Resident 对话可以继续指向折叠块内部的 Resident，画布不据此把隐藏 Resident 加入多选。

定义刷新、展开折叠和画布重绘按同一可见对象集合清理选区与锚点，保留仍可见的块与真实连线。工具栏数量和封装请求使用这组明确选区。选择折叠块与一个独立组件后，外层封装只提交块和组件的 ID，内层元素保持原来的直接归属。

Functionalize 命名弹窗保存打开时的选区。确认时，若其中对象已经删除或隐藏，页面报告选区已变化并要求重新选择，整次操作不提交。请求失败继续显示领域错误并保留选区供调整；元素直接归属唯一和无环嵌套仍由领域服务检查。

## 画布位置与解除封装

`x`、`y` 是 Function Block 的画布坐标，接受有限数值。创建时可提供期望位置，统一布局规则负责落点和避让；未提供时由布局规则安排。更新位置只改变块的布局。

折叠时，画布显示块及穿越边界的连线；内部对象的身份和配置继续保留。折叠的块只画一只鸽子：Family 使用 Family 的鸽子，来自内置模板的块使用该模板在货架上的鸽子，来自其他模板的块按 template_id 取鸽子，同一模板施加出的块因此外观一致；没有来源模板的块按块 ID 取。块标题、来源模板和元素数写在悬停提示中，无障碍名称是块标题。解除最外层封装后，直接元素回到外层画布；解除一个嵌套块时，它的直接元素归入原来的父块。仍然存在的内层块保持封装。新显露的组件与块按统一布局规则安排可用位置。

解除封装与移出元素都会保留相应对象。删除块则会删除其内部对象。已存在的 Resident 一旦被选入块，也受块的删除规则约束；需要保留时，先解除封装或将该 Resident 移出。

## Family 的边界

Family 使用同一个 Function Block ID，并由 `families`、`family_residents` 保存 Family 的标题、目标、角色和成员事实。读取 Lineage 时，`family_storage` 给对应块附加 `family` 投影。

Family 的门面、steward、Router Resident、业务成员、组件和连线都通过元素归属纳入结构。嵌套 Family 在外层以一个内层块出现，外层成员名册使用内层的门面 Resident ID；相应元素的 `key` 为 `member:<门面Resident ID>`，`id` 为内层块 ID。

通用读取指令可以读取 Family，通用更新指令可以调整它的坐标。Family 改名、成员调整、参数修改与解除必须走 [Family 文档](/r/decision_44cf587c1721)规定的接口，以同步名册、门面和内部结构。通用删除遇到 Family 或包含 Family 的子树时拒绝执行；通用解除封装遇到目标为 Family 时提示使用 `Unfamilize`。

## Lineage 定义形状

`GetLineage` 的定义在顶层返回 `function_blocks`，读取块就读这个数组：它包含该 Lineage 的全部块，包括嵌套块和 Family，每项包含标识、标题、来源模板、坐标、创建与修改信息和直接元素列表，Family 块还包含 `family` 投影；嵌套关系由各块的 `elements` 表达，数组顺序不定义归属，读取单个块时按 `function_block_id` 选取。Resident 位于 `nodes`，执行组件和连线位于 `program.components`、`program.wires`。元素归属统一由块的 `elements` 表达。下面给出一个手动封装的示例：

```json
{
  "nodes": [
    {"id": "resident_0123456789ab", "title": "工作者"}
  ],
  "program": {
    "schema": "tfi.hatg-program.v2",
    "components": [],
    "wires": []
  },
  "function_blocks": [
    {
      "id": "fbi_0123456789ab",
      "title": "工作组",
      "template_id": null,
      "x": 40.5,
      "y": 22.0,
      "created_at": "2026-10-13T10:00:00Z",
      "created_by": "TFI portal",
      "updated_at": "2026-10-13T10:00:00Z",
      "updated_by": "TFI portal",
      "elements": [
        {"id": "resident_0123456789ab", "kind": "resident", "key": null}
      ]
    }
  ]
}
```

Family 块还包含如下 `family` 字段。它是 Family 领域事实的读取投影：

```json
{
  "title": "研究组",
  "goal": "完成研究任务",
  "route_threshold": 60,
  "review_interval_seconds": 86400,
  "facade": "resident_000000000001",
  "steward": "resident_000000000002",
  "router": "resident_000000000003",
  "members": ["resident_000000000004"],
  "previous_leader_id": "",
  "human_familizer_id": "TFI portal"
}
```

`commit_lineage_edit` 的 `body` 使用相同的顶层结构。模板模块可在同一次提交中创建图元素并封装，传入 `template_id` 和元素局部名；公开的 Functionalize 只接收已有元素。Lineage 草稿、定义合并和子图操作必须保留块及其归属关系；复制时同步重映射块 ID、元素 ID 和嵌套引用。

## 指令一览

| 指令 | 改变状态 | 说明 | 默认方案要求 |
| --- | --- | --- | --- |
| `Functionalize` | 是 | 将当前 Lineage 的选区封装为一个 Function Block | 对 `lineage_id` 有 @contributor |
| `Unfunctionalize` | 是 | 解除普通 Function Block，保留内部元素 | 对 `lineage_id` 有 @contributor |
| `UpdateFunctionBlock` | 是 | 修改块的名称、位置或直接元素归属 | 对 `lineage_id` 有 @contributor |
| `DeleteFunctionBlock` | 是 | 删除普通块及其递归包含的元素 | 对 `lineage_id` 有 @contributor |

## 参数名契约

以下各项列出指令参数和结果字段。所有指令的 `lineage_id` 必填；定位已有块时，`function_block_id` 也必填。定义变更使用所在 Lineage 的版本前提，公共调用封套中的 `expected_version`、幂等键与冲突处理遵守 ISA 总则。

| 指令名 | arguments 参数名 |
| --- | --- |
| `Functionalize` | `lineage_id`、`element_ids`、`title`、`x`、`y` |
| `Unfunctionalize` | `lineage_id`、`function_block_id` |
| `UpdateFunctionBlock` | `lineage_id`、`function_block_id`、`title`、`x`、`y`、`add_element_ids`、`remove_element_ids` |
| `DeleteFunctionBlock` | `lineage_id`、`function_block_id` |

### Functionalize

参数：`lineage_id`、`element_ids` 必填；`title`、`x`、`y` 可选。`element_ids` 是非空、无重复的 ID 数组，每个元素必须在当前 Lineage 中，并满足唯一归属和无环约束。省略名称时使用“Function Block”。

结果：`function_block_id`，即新建块的 ID。封装保留各元素原有的执行行为和连线端点。

### Unfunctionalize

参数：`lineage_id`、`function_block_id`。

结果：`element_ids`，列出解除该边界后保留的直接元素 ID。最外层块解除后，元素释放归属；嵌套块解除后，元素归入父块。Resident、组件、连线及内层块继续存在。目标为 Family 时拒绝，并提示使用 Family 的解除指令。

### Family 的工作流归属

Family 的成员、生成结构与普通工作流共用 `elements` 唯一直接归属。普通工作流使用 `workflow:` 局部 key，与自动生成元素分开；`UpdateFamily(add_element_ids, remove_element_ids)` 维护组件及内部连线的归属，负责祖先 Family 间转移、移出回父容器和生成结构保护。`UpdateFunctionBlock` 对 Family 继续只允许坐标更新；业务对象的执行与运行记录不随收纳改变。自动专属工作流收纳、外部入口及 Unfamilize 保留规则见 decision_44cf587c1721。

### UpdateFunctionBlock

参数：`lineage_id`、`function_block_id` 必填；`title`、`x`、`y`、`add_element_ids`、`remove_element_ids` 可选。省略的修改项保持原值。加入元素必须属于当前 Lineage，并满足唯一归属和无环约束；移出的 ID 必须是这个块的直接元素。

结果：`function_block`，为更新后的完整块对象。普通块移出全部元素后自动删除，此时结果中省略 `function_block`。移出只解除归属，不删除元素。Family 只允许通过本指令更新坐标，改名和成员调整使用 Family 指令。

### DeleteFunctionBlock

参数：`lineage_id`、`function_block_id`。

结果：`element_ids` 为递归删除的内部元素 ID，包含内层块和 Resident，不包含请求删除的最外层块 ID；`resident_ids` 为其中被删除的 Resident ID。

删除内部 Resident 使用标准 Resident 删除流程。删除组件和连线同时清理相连引用及依赖它们的观察器，释放相应算子执行器的归属。元素来源不影响删除语义。目标或其包含的子树存在 Family 时拒绝，须先通过 Family 接口处理其解除。

## 关系存储与提交

| 表 | 保存的事实 |
| --- | --- |
| `lineage_nodes` | 图对象身份；块的 `node_type` 为 `function_block` |
| `function_blocks` | `function_block_id`、`lineage_id`、`title`、`template_id`、`x`、`y`、创建与修改时间及操作者 |
| `function_block_elements` | `function_block_id`、`element_id`、`element_kind`、`element_key` |
| `families`、`family_residents` | Family 的参数、角色和成员事实 |

`function_block_elements` 以块和元素 ID 为联合主键，并对 `element_id` 建唯一索引，保证一个元素只有一个直接所属块。`function_blocks.x/y` 保存块坐标。组件配置、连线配置和 Resident 身份由各自的领域表保存。

`graph_storage.put` 将顶层块与元素归属写入关系表，批量读取时组装定义，随后附加 Family 投影。定义合并按块 ID 处理对象，保留独立字段的并发修改；当一次编辑要封装的元素已经被另一笔编辑删除时，返回冲突。正常删除会同步清理归属及空块。

## v46 迁移

schema v46 从 v45 执行一次迁移，顺序覆盖图身份、归属、Family 参数和端口连线：

1. 未删除且非 `ephemeral` 的实例迁入 `function_blocks`，保留 ID 与坐标，标题取实例标题或 Family 标题，`template_id` 记录可确定的来源内置模板。
2. 内部组件、连线和 residents 槽中的 Resident 迁入 `function_block_elements`；Family 的成员绑定也迁入。能确定局部名称时填写 `element_key`，角色使用 `facade_resident`、`steward_resident`、`router_resident`，成员使用 `member:<Resident ID>`。
3. 嵌套 Family 的元素 ID 指向内层块，成员键保留内层门面 ID；既有父子关系迁为块元素关系，并保留已经确定的成员键。
4. `families.route_threshold` 与 `families.review_interval_seconds` 从实例参数回填。指向实例端口的连线改接到端口对应的内部对象，嵌套端口逐层解析。
5. 已删除或临时实例只保留 `lineage_nodes` 墓碑，并将节点类型改为 `function_block`。删除实例、槽位、对象、端口和定义引用的旧表，以及组件、连线上的旧归属字段。
6. 全局 Function Block 定义不迁入模板库；模板域按自身规则安装内置模板。需要保留的人工定义按模板文档重新建立。

迁移完成后，程序形状仅含 `schema`、`components`、`wires`，所有持久封装通过顶层 `function_blocks` 及其元素关系读取。


### 折叠模块的内部对象管理与问题确认（TFI portal 10-15）

Family 的概览采用场景化“工作流程”，将关联的 Event、Effect、失败通知及角色指南放在同一卡片，详见 decision_44cf587c1721。首页不重复完整元素清单；全部内部组件与批量操作仍由“内部对象”页管理，完整元素列表可在该页展开。共用组件保留同一身份并提示关联场景数量；普通 Function Block 的概览保留现有触发、通知汇总和元素入口。

Function Block 与 Family 实例菜单提供“内部对象”和“问题”页，不需要展开模块。内部对象页按成员关系递归列出内部组件，按 Effect、Event、其他组件分组；每行显示名称、种类、稳定 ID 和状态标签。Effect 行直接显示失败通知收件人，点击即打开失败通知编辑器；Event 行带暂停或恢复按钮。“设置”在列表下方打开现有原语检查器，保存与普通检查器使用相同的权限、定义版本和草稿保护；不改变折叠状态或全局画布选择。概览页的“失败通知”汇总模块内 Effect 的收件人，状态与清单都相同的 Effect 合为一行并注明数量，尚未配置的 Effect 与明确关闭的 Effect 分属两行；“修改 N 个 Effect 的收件人…”对模块内全部 Effect 打开编辑器。问题页的 Effect 失败同样列出收件人，并可从那里修改。

勾选对象后，顶部工具栏显示已选数量和“设置失败通知（N）”“暂停事件（N）”“恢复事件（N）”，全选框在部分选择时显示半选。批量设置失败通知打开与单个 Effect 相同的编辑器：所选 Effect 都有的收件人显示为普通标签；只在部分 Effect 中的收件人用虚线标签和覆盖数（例如 1/3）标出，可以“补到全部”或移除，不动则保持原有覆盖。新加的收件人加到全部所选 Effect。各 Effect 保存后的清单不同时，预览逐个列出，尚未配置的 Effect 写“尚未配置 · 当前不通知”，与“关闭失败通知”分开。保存以一次 ApplyLineageChanges 只更新清单有变化的 Effect 的 config.failure_recipients，保留各自脚本与端点。移除收件人会关闭原本有清单的 Effect；尚未配置的 Effect 只有在用户为它添加收件人或选择“关闭通知”时才写入清单，否则保持未配置。所选 Event 可以批量暂停、恢复，逐个调用 PauseComponent（暂停传 `paused: true`，恢复传 `paused: false`）；部分失败显示对象名称与原因，不把已成功操作回滚成未执行。

鸽子的红色数字表示当前查看者尚未查看的问题数量，点击直接打开问题页。清单列出内部 Effect 最新执行失败、组件配置错误和内部 Resident 最近错误（包括 Router、steward），显示具体来源、原因和时间。组件提供设置与运行记录入口，Effect 最近运行关联具体 Trigger Path；Resident 提供打开对话入口。取消的 Effect 不算失败。

打开“问题”页即自动将本次显示的问题记录为当前用户已查看，存入 Workbench PageState 的 functionBlockFailureAcknowledgements，不再要求点击“已知悉”；不修改错误、运行事实或其他用户的提示。查看按 Lineage、对象及失败发生标识匹配，新的失败重新提示。只打开概览或内部对象页不消除提醒；问题页打开后的后台刷新不自动将新失败标为已查看，再次打开问题页才消除新提醒。成功执行、修正配置或 Resident 错误清除后，该问题移出当前清单；已查看但尚未修好的错误仍可在问题页查看。

格式名与版本须精确匹配当前规范值，校验规则见 decision_86aed2f0e8c7；本篇同时校验各字段和结构版本，不接受历史前缀或其他版本。
