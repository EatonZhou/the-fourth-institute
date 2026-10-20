---
kb_id: decision_b04cac4188bb
type: decision
project: AI员工
created_at: '2026-10-13'
version:
  number: 16
  created_at: '2026-10-13'
  created_by:
    user: ''
    agent: resident_e8fc8c899f00
  updated_at: '2026-10-19T08:47:56Z'
  updated_by:
    user: ''
    agent: resident_2e9ea0072551
importance: 8
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/hatg/catalog/template.py
  - ${code_root}/management_portal/society_service/templates
  - ${code_root}/management_portal/static/lineage_templates.js
  - ${code_root}/management_portal/static/lineage_template_graph.js
  related:
  - decision_898b8911a2ff
  - decision_5b5653c3eaa5
  - decision_4637ca4a69e2
  - decision_d19b9909a674
  - decision_22227d0ad075
  supersedes: []
  derived_from: []
---

# HATG ISA：模板与指令

模板是跨 Lineage 共享的可编辑配方：把可复用的提示词、结构、脚本或组队行为保存为一份资料，施加时将参数与当前目标交给对应模块执行。模板独立存储，不属于某个 Lineage，也不依赖创建者继续存在。模板指令由 SocietyService 执行，指令类别为 `template`；共同调用格式与错误语义见 decision_898b8911a2ff。

## 模板与 Function Block

模板描述如何产生结果；Function Block 保存一个 Lineage 中真实元素的归属与呈现。结构模板施加后，Resident、组件和连线都是普通 Lineage 元素，新建元素可以封装为 Function Block。Function Block 通过 `elements` 引用这些元素，`template_id` 只记录来源。它不从模板动态读取运行定义，也不以模板正文充当自身内容。

模板更新、删除或恢复内置内容，都不会自动修改已经施加的结果。画布中的修改也不会自动回写模板。需要复用调整后的结构时，显式从选区或 Function Block 创建新模板。需要对一类现有结构重复做同一种改动时，使用带“已有元素”的结构模板（见“改写已有结构”）；施加后的 Function Block 是独立的图元素，与来源模板不再有关联，改写模板也不会查找或修改任何 Function Block 内的元素。其他对现有图的改动使用普通 Lineage、Function Block 或 Family 指令。Function Block 的归属、布局与边界见 decision_5b5653c3eaa5。

一次施加可以产生两类结果：新建了元素的结构模板和 Family 留下持续存在的协作结构；Prompt、脚本，以及只修改或删除已有元素的结构模板执行一次操作。探索模板执行一次探索启动，留下探索者、候选 Seed 及文件，而不建立持续运行的 Function Block。`behavior.persistent` 表示是否留下持续协作结构，不能据此判断操作没有副作用，或没有持久资源。脚本调用的指令也可以自行创建资源。

## 数据与目标

每份模板的内容字段为 `kind`、`category`、`title`、`description`、`targets`、`parameters`、`body`。`template_id` 是稳定身份；`version`、`created_at`、`created_by`、`updated_at`、`updated_by` 记录版本和真实调用者。内置条目还带 `builtin_key` 与 `builtin_source`。返回给界面时附加 `behavior` 与 `editor`，它们由模块按当前内容计算，不是第二份存储事实。

`kind` 决定执行模块，现有值为 `prompt`、`structure`、`script`、`explore`、`familize`。`category` 只决定货架分组，可取 `apply`、`structure`、`team`，不参与执行派发。单份模板的 `targets` 必须是模块支持目标的非空子集。

| 目标值 | 含义与约束 |
| --- | --- |
| resident | 对一个 Resident 施加；解析后必须恰好有一个目标 Resident。 |
| selection | 对非空选区施加；可以提交 Resident ID 或元素 ID，具体可接受对象由模块校验。 |
| canvas | 对画布空白处施加；不携带 Resident 或元素 ID，可提供放置坐标。 |

`target` 使用 `{kind, resident_ids?, element_ids?, x?, y?}`。目标必须属于 `lineage_id`；坐标必须为有限数值。选区中的普通 Function Block 可以解析到所含 Resident，Family 作为目标时使用其门面。模块继续检查自己的语义，例如 Prompt 至少需要一个 Resident，探索只接受单个 Resident，Familize 只接受 Resident 或已有 Family。

| 模块 | 支持的目标 | 施加结果 |
| --- | --- | --- |
| prompt | resident、selection | 向每个目标投递一次消息。 |
| structure | resident、selection、canvas | 解析角色，查找并改动已有元素，创建普通元素，按设置封装新建元素。 |
| script | resident、selection、canvas | 以施加者身份执行一次 Effect DSL；声明 target endpoint 时按目标分别执行。 |
| explore | resident | Fork 探索者，创建并同步候选 Seed 与文件。 |
| familize | resident、selection | 保留成员，创建门面、steward、Router 与 Family 协作结构。 |

## 模块架构

`management_portal/society_service/templates/` 集中模板职责。`store` 保存模板与内置安装记录；`values` 校验参数并替换引用；`builtins` 提供内置内容；`registry` 管理模块登记。每种执行行为由独立模块实现，ISA 适配器只处理指令参数、结果与错误映射。

模块实现 `TemplateKind` 接口并导出 `KIND`，通过 `registry.register(KIND)` 登记。接口包含以下成员：

- `key`、`title`、`targets`：执行类型的稳定键、名称及支持的目标类型。
- `normalize(template) -> dict`：校验和补齐该模块的 `body`。
- `describe(template) -> dict`：返回施加行为，包含 `steps`、`persistent`，需要时附加角色说明。
- `editor() -> dict`：返回正文编辑字段和新建角色说明。
- `apply(ctx: ApplyContext) -> dict`：执行施加并返回统一结果。

`ApplyContext` 包含 `lineage_id`、完整 `template`、已校验的 `parameters`、规范化 `target`、真实 `actor`、结果 `title` 与 `resident_settings`。新增一种执行类型只需实现一个行为模块并登记其 `KIND`；类型特有的校验、说明、编辑字段与施加逻辑都留在该模块。通用存储与七条模板指令不复制这种行为，也不要求建立新的模板专属运行引擎。

## 各模块的 body 与行为

### prompt

`body` 为 `{message, activation_policy?}`。`message` 是非空文本，可引用模板参数与当前目标。`activation_policy` 默认 `queue`，表示排队并触发处理；`attach_to_next` 表示随下一条请求附加送达。模块逐个目标渲染消息，以施加者身份投递，返回 Resident 与消息 ID，不创建组件或 Function Block。

需要每次施加时修改提示词，可把正文写成 `{{param.prompt}}` 或 `{{param.message}}`，并定义相应文本参数。没有参数且不需要新建角色的模板可以直接施加；其固定正文在模板编辑器中修改。

### structure

`body` 为 `{roles: [], components: [], wires: [], match: [], function_block: true}`。各列表中的每个元素都有模板内部唯一的 `key`。组件声明 `kind`、`title`、`config`；连线声明 `kind`、`source`、`target`，并可带 `source_port`、`target_port`、`config`。连线端点引用局部 key，施加时转换为真实 ID。组件和连线配置遵守普通 Lineage 编译与校验规则。`match` 列出施加时要查找的已有元素，规则见下文“改写已有结构”；没有 match 的模板只新建元素。

可选的 `layout` 记录编辑器图视图中每个角色和组件的位置，形如 `{"<key>": {"x": 40, "y": 50}}`，坐标与 HATG 画布相同。校验时坐标必须是有限数值，保存前保留两位小数，并去掉已不存在的 key。施加不读取 layout，新元素仍按画布布局规则落点。`from_selection` 把选区在画布上的位置写入 layout，编辑器打开时保持原来的排布。

角色使用 `key`、`from` 及相应起始设置：

- `from: target` 绑定本次目标，不新建 Resident。
- `from: lineage_leader` 绑定当前 Lineage Leader；没有 Leader 时拒绝施加。
- `from: new` 创建 Resident，可声明 `title`、`provider`、`account`、`settings`。
- `from: fork` 从前面已声明的角色派生；`fork_of` 是来源角色 key，`fork_mode` 可为 `full`、`compacted`、`recent-5`、`recent-10`。

包含 target 角色时，选择多个 Resident 会分别施加一份结构；所有 target 角色在每份结构中绑定该次目标。没有 target 角色时只生成一份结构。参数、图配置和所有目标的 Fork Provider 继承链在创建之前检查。新建 Resident、组件、连线取得新 ID，并放到目标附近或给定坐标；`function_block` 为 true 时把本次新建元素封装，目标和既有 Leader 保留在块外。块内元素的 `key` 保留模板局部标识，块的 `template_id` 记录来源。

`CreateTemplate.from_selection` 从真实图生成结构模板。`element_ids` 可直接传 Function Block ID，也可传 Resident、组件和连线 ID；块会递归展开，已选择端点之间的连线一并纳入。选中 Resident 转为 `from: new` 角色，复制 Provider、账号和可编辑运行设置；它不是对话快照。选区外的 Resident 端点转为 target 角色，选区外组件端点必须补入选区。正文中指向已抽取 Resident 的引用转换为 `{{role.key.id}}`。新建模板的标题可由同级 `template.title` 覆盖。

### 改写已有结构

有些结构模板要在目标已有的结构上做改动，例如给目标的每个输入 Event 再接两个新 Resident，或者删掉目标的“次数耗尽”事件并取消它监视的次数上限。目标周围已有什么元素、有几个，在写模板时并不确定。`match` 用一组按形状描述的“已有元素”表达这部分结构，施加时从目标出发在真实图中查找，再对找到的元素保留、删除或修改，同时新建模板中的其他元素。

match 的每一项有局部 `key` 与 `kind`，以及：

- `op`：`keep` 保留，`delete` 删除，`update` 修改。已有 Resident 只能保留；Inspect 与 Watch 连线只能保留或删除。
- `where`：配置字段的等值条件，例如 `{"event_type": "budget.exhausted"}`。条件值可以引用模板参数，不能引用其他已有元素。
- `update`：`op` 为 update 时必填，形如 `{"config": {...}}`，按字段覆盖找到元素的配置；值为 `null` 表示清除该字段。
- `config`、`title`：记录示例图中的原配置与标题，供编辑器显示和比较修改，施加时不参与查找。
- 连线另有 `source`、`target`、可选 `source_port`、`target_port` 与 `count`。端点只能是目标或 Leader 角色、或其他已有元素的 key。`count: one` 要求恰好找到一条，`all` 表示找到几条就各施加一次。

查找的起点是 `from: target` 与 `from: lineage_leader` 角色，没有这两种角色的模板不能声明已有元素。每个已有节点都必须经已有连线连到起点：施加时先找连在已绑定元素上、类型、端口与条件都相符的连线，再把连线另一端绑定到对应的已有节点并检查它的类型与条件。一个真实元素只绑定一个 key，目标与 Leader 不会被当作已有元素找到。Function Block 内的元素（含嵌套块）一律不参与查找：模板施加后形成的 Function Block 是独立的图元素，与来源模板不再有关系，改写模板不会修改它的内部。任何一项找不到时拒绝施加；某个 `count: one` 的连线在同一位置找到多条时，施加要求先选定一条。组合数超过 200 时要求收紧条件。

新建的组件与连线可以连到未删除的已有元素。依赖已有元素的新元素按找到的组合各新建一份：上例中目标有两条输入时，两个新 Resident 各得到两条新的输入连线。依赖关系包括连线端点、`config_from` 和正文中的 `{{match.key.id}}`、`{{match.key.title}}` 引用；新连线还继承它所连新组件的依赖。`config_from: <key>` 让新组件或新连线先复制对应已有元素的配置，再覆盖自己填写的字段，它只能指向同类型且不删除的已有元素。角色不能引用已有元素。

施加分两步完成。第一步对全部目标计算改动；存在未选定的候选、或两个目标的改动涉及同一个已有元素时，在创建任何元素之前报错。第二步创建新角色，然后在同一次图提交中重新查找已有元素并与第一步的结果比较；图已经变化时放弃整次施加，删除刚创建的 Resident，并返回“已有结构在施加时发生了变化”。删除已有元素时，连在它上面的连线与观察这些连线的 Watch 连线一并删除；这种连带删除如果会触及 Function Block 内的元素，施加被拒绝。只有新建元素会被封装进 Function Block；已有元素留在原处。只修改或删除已有元素、不新建任何元素的模板是一次性操作，`behavior.persistent` 为 false，施加后不留下 Function Block。

`describe` 为改写模板依次说明“从目标出发查找已有元素”“删除已有的 …”“修改已有的 …（字段）”，再说明新建内容。

`CreateTemplate.from_selection` 带 `rewrite: true` 时，把选区作为已有结构生成改写模板。选区中第一个 Resident 成为 target 角色；选区中的 Lineage Leader 不是这个 Resident 时成为 lineage_leader 角色。其余选中元素、两端都在选区内的连线以及选中连线的端点成为 match 项，`op` 均为 keep，连线的 `count` 为 one，并记录原配置；Event 组件附带 `event_type` 条件。配置中指向这些元素的 ID 改写为 `{{role.key.id}}` 或 `{{match.key.id}}`。选区不能包含 Function Block 或其中的元素。

### script

`body` 为 `{script, endpoints}`。`script` 是非空 Effect DSL，使用现有解析器与 ISA 执行入口；`endpoints` 默认为 `["target"]`，只支持 target endpoint。选定目标作为 `${endpoint.target}` 传入，并对每个目标执行一次；`endpoints: []` 时执行一次无目标脚本，可用于 canvas。

脚本自身不建立 Effect 组件或 Function Block。每条内嵌指令仍按施加者身份调用并检查权限。成功结果汇总消息回执与返回的运行 ID，模块还可返回 `output`；脚本失败按执行错误返回。一次性执行不表示脚本内多条指令具有整体回滚能力，已经提交的指令按各自语义保留结果。

### explore

`body` 为 `{seed_type, label, count, task}`。`seed_type` 可为 `idea`、`plan`、`other`；`count` 默认为 3，参数替换后必须是 1–12 的整数；`task` 为非空探索任务。`count` 和 `task` 可以引用模板参数。

模块从目标 Fork 一个探索者，在探索者工作区建立候选文件，并在 Lineage 中留下普通 Seed 组件及来源到 Seed 的候选连线。消息告诉探索者任务、文件位置与候选管理规则；每轮结束后同步新增、修改与放弃状态。Lineage 保有候选文件副本，删除探索者不删除已有 Seed 和副本。采纳候选沿普通 Seed 流程从来源 Fork。模板不封装 Function Block。

探索者的起始设置通过 `resident_settings.explorer` 提交，Provider 继承目标。`editor.resident_roles` 明确声明 explorer 为 fork 角色，界面据此提供账号与模型设置。

### familize

`body` 为 `{goal, route_threshold, review_interval_seconds}`，默认分别为空文本、60、86400。路由阈值为 0–100 的整数，回顾间隔为正整数秒。该模块使用这些明确字段，Family 参数不承载任意结构或脚本。

施加保留选中的成员，新建门面、steward 与 Router，生成普通组件与连线，并将成员、内部协作元素及仅服务这些成员的普通工作流纳入 Function Block；块带来源 `template_id` 与 Family 事实投影。已有 Family 可作为嵌套成员，使用其门面参与上层协作。先按 decision_44cf587c1721 的专属组件边界完成收纳，保留内部执行目标；只有外部来源指向成员的 route/target 连线改接门面，门面负责对外身份与消息入口，Router 选择成员，steward 承担内部协调和回顾。Family 生命周期及成员维护由 Family 模块负责。

`resident_settings` 只为 steward、router 提供 Provider、账号和运行设置。门面不运行模型，不要求用户选择 Provider 或模型。施加结果除统一字段外，还包含 `family_id`、三个角色的 Resident ID、成员 ID、改接的连线 ID 及当前 Leader ID。

### Fork 与起始设置

`resident_settings` 形如 `{role_key: {provider?, account?, settings?}}`，用于本次新建角色，不能改变既有目标 Resident。新建角色可选择 Provider；切换 Provider 后，模板中针对原 Provider 的运行默认值不被机械套用。

Fork 的 Provider 一律继承来源。模板或施加参数显式填写相同 Provider 视为无变化；填写不同 Provider，在创建任何角色或图元素之前报错。来源可以是目标、Leader、本次 new 角色或前一个 fork 角色，继承关系沿角色链解析。账号通过同一 Provider 的 `backend_route_id` 指定，`settings` 按常规运行设置更新。界面只显示“Provider：继承来源”，不提供跨 Provider Fork 选项。

## 参数与替换语法

`parameters` 是参数说明列表，每项包含 `name`、`type`，并可带 `default`、`required`、`description`、`options`。名称使用字母或下划线开头的标识符，同一模板内不能重复。支持 `text`、`integer`、`number`、`boolean`、`enum`、`duration`；enum 需要非空选项列表，duration 是正数秒。数字必须有限，布尔值不能冒充整数。施加时提交对象形式的参数值；未知参数、类型不符、缺少必填值均被拒绝。

引用递归作用于支持渲染的正文值：

- `{{param.name}}` 读取施加参数。
- `{{role.key.id}}`、`{{role.key.title}}` 读取角色身份与标题；`{{role.key.ids}}` 返回该角色所有 ID 的逗号分隔文本。
- `{{match.key.id}}`、`{{match.key.title}}` 读取结构模板本次找到的已有元素，可用于新建组件、新建连线和已有元素的 update；被引用的已有元素不能是将删除的元素，角色设置与 where 条件不能使用这种引用。
- `{{lineage.id}}` 读取目标 Lineage ID，`{{block.title}}` 读取本次结果标题。
- `{{param.interval|duration}}` 把数值秒渲染为可读时间，例如 3600 显示为“1 小时”。

值完全由一个引用组成时保留其原始类型，例如 `"max_rounds": "{{param.max_rounds}}"` 渲染为整数；引用嵌在文本中时转为文本。未知参数名、角色名和修饰符在规范化时拒绝。角色引用必须由模块提供；结构角色来自 roles，Prompt 与探索等模块使用 target。

Effect DSL 内的替换使用脚本专用转义器。在带引号的脚本文本中，参数包含引号、换行或类似 DSL 变量的文本时仍按参数内容处理，不把内容拼成新的指令。创建或编辑的 `dry_run` 负责正文校验与行为预览，不执行脚本、不投递消息、不创建 Resident，也不验证只有实际施加目标才能确定的所有运行条件。

## 八个内置模板

| 标题 | builtin_key | 模块与目标 | 内容与施加行为 |
| --- | --- | --- | --- |
| Family | family | familize；resident、selection | 将成员组织为 Family，创建门面、steward、Router 及内部结构。默认路由阈值 60、回顾间隔一天。 |
| Agent Loop | agent_loop | structure；resident | 新建评审角色。目标完成一轮后进入评审，未完成时将意见回给目标，DONE 或轮数上限结束循环。role 可选 critic、questioner、ideator、supervisor，默认 critic；max_rounds 默认 5，task 可编辑。 |
| 固定提醒 | fixed_reminder | structure；resident | 目标每完成一轮就投递同一条 message，由 max_rounds 限制轮数，默认 5。 |
| 定时工作 | scheduled_work | structure；resident | 定时与手动入口向目标发送 task；task 必填，interval_seconds 默认 3600，jitter_seconds 默认 0。 |
| Leader Pulse | leader_pulse | structure；canvas | 自动绑定当前 Lineage Leader，按 interval_seconds 投递 prompt，默认每 900 秒。 |
| 修订回流 | revision_return | prompt；resident、selection | 一次投递可编辑的修订 prompt，不建立持续循环。 |
| 扩展想法 | expand_ideas | explore；resident | Fork 探索者，生成 idea 候选 Seed；count 默认 3，task 可编辑。 |
| 扩展方案 | expand_plans | explore；resident | Fork 探索者，生成 plan 候选 Seed；count 默认 3，task 可编辑。 |

内置模板进入货架后与用户模板一样可编辑、复制和删除。script 是可创建的模块类型，但不占这八份内置内容中的一项。

## 存储与内置生命周期

`templates` 是独立 DomainDatabase 域。`templates` 表按 `template_id` 保存模板正文、参数、元数据与版本审计；`targets`、`parameters`、`body` 使用 JSON 字段。它不保存 Lineage 中的已施加实例，Function Block 归属和图元素仍由 Lineage 存储负责。

`installed_builtins` 表保存 `builtin_key` 与安装时间，区分“从未安装”和“安装后主动删除”。新版本带来的新 builtin_key 首次出现时自动安装；已经安装后被删除的模板不会因服务重启或刷新重新出现。

内置 ID 根据 builtin_key 稳定生成：`template_` 加上 `SHA256("builtin-template:" + builtin_key)` 的前 12 位小写十六进制字符。`builtin_source` 记录上次安装或刷新所依据内容的哈希。当前存储内容仍与该哈希一致时，代码中的更新可以刷新条目并递增版本；内容已被用户改过时保留用户内容。哈希覆盖模板内容字段，不把审计字段当作用户编辑。

`RestoreTemplate` 按确定的 template_id 读取代码中的内置定义，覆盖当前编辑版本，或重新创建已删除的内置条目，并将其视为已安装。恢复更新 builtin_source 与审计信息；已有记录版本递增，已删除后重新建立的记录从版本 1 开始。自定义模板没有代码来源，不能用 RestoreTemplate 恢复。自动刷新和显式恢复都不回写已施加的图。

## 权限口径

模板是全 Portal 共享的普通资料。各操作沿当前权限方案鉴权，本篇“指令一览”列出默认方案要求；不能把已认证或创建者身份当作模板写入授权（decision_b820faf42218）。模板写入记录真实 created_by/updated_by，施加到 Lineage 的步骤继续按其目标判定。

`ApplyTemplate` 要求目标 `lineage_id` 的 contributor，因为施加可能修改图、创建 Resident 或执行指令。`CreateTemplate` 和 UpdateTemplate 一样要求对“公共内容”有 contributor，因为 Lineage 模板是所有人共用的定义；携带 `from_selection` 时还要求其中 `lineage_id` 的 viewer，因为需要读取图与角色设置。此检查也适用于带选区的 dry_run。内嵌 ISA 调用继续使用施加者身份和各指令自己的权限规则。模板不是额外授权凭证。

## 货架与编辑器

模板货架和编辑器的样式由页面共享资源加载器加载并等待就绪，样式表留在 document head；HATG 面板替换或关闭不撤销其他面板正在使用的样式。加载失败时保留可重试的 HATG 加载状态。

货架沿用原 Block 区的横向排版：左侧一列是“TEMPLATE 模板”标题与新建按钮，右侧是一行可横向滚动的模板图块，依次为施加、结构、团队三组，组前有竖排的分组名。每个图块是一只鸽子加标题。内置模板沿用原 Function Block 的鸽子（Agent Loop 0、扩展想法 2、修订回流 3、Family 4、扩展方案 34、Leader 脉冲 35、固定提醒 37、定时工作 38，即鸽子图集的序号），其他模板按 template_id 取鸽子；施加后保留为 Function Block 的模板在鸽子右下角带 ∞。说明、是否保留结果、可拖放目标写在悬停提示中。拖放时按 targets 提示可接受的 Resident、选区或画布。点击模板打开编辑器；新建入口提供 Prompt、结构和脚本，已有 explore/familize 内容使用各自模块的字段描述。画布有选区时，选区工具条显示“存为模板”与“做成改写模板”；Function Block 菜单也有“存为模板”。它们都通过 CreateTemplate.from_selection 生成模板（“做成改写模板”带 `rewrite: true`，把选区作为已有结构），随后直接打开新模板的编辑器，用户接着修改标题、参数和图。

模板标题、说明、行为步骤、角色说明、施加结果和服务端错误在显示时经过 Portal 的界面翻译：内置模板的中文文案在英文界面显示对应英文。翻译只作用于显示，模板正文、参数值和施加内容保持原文。

编辑器提供通用标题、说明、分组、目标范围、参数页与模块内容页，标题旁显示模板的鸽子。Prompt 编辑消息与投递方式；脚本使用代码编辑区；专用模块使用 editor.fields。结构模板有图、列表、JSON 三种视图，默认是图视图：

- 图视图用 HATG 编辑区的图形引擎画出模板表示的图。角色画成 Resident 卡片，组件画成原语节点，连线带着端口画出；节点 ID 就是模板内的 key。
- 工具条添加角色（目标 Resident、Lineage Leader、新建 Resident、Fork）和组件（Event、Effect、循环、Filter、Selector、Reducer）。组件可以点击加入，也可以拖进图中落在指定位置。Fork 角色以当前选中的角色或第一个角色为来源。
- 先点输出端口、再点输入端口创建连线，端口兼容规则与 HATG 编辑区相同。
- 拖动节点调整位置，位置写入 body.layout。
- 选中节点或连线后，图右侧的检查器显示它的设置，字段与 HATG 编辑区的检查器相同，两处共用同一套元素字段模块：
  - Event 与 Proposal 连线编辑本次调用的参数（例如发给 Resident 的 Prompt：沿用触发来源、固定值、从触发数据取值或拼接），以及接收方不可消费时的策略、积压上限、过期时间和是否附带触发信封；Inspect 连线编辑检查对象，检查对象与 Lineage 中的 Inspect 连线一样保存在连线顶层的 `observations`。
  - Event 选择事件类型并填写该类型的参数；Effect 编辑脚本与具名端点，可从指令列表插入调用行；Loop 设置最大轮数与停止后的行为；Filter、Selector、Reducer 设置实现方式、Prompt 与各自的规则。
  - 新建与 Fork 角色选择 Provider、账号和模型，Fork 角色还选择来源角色与 Fork 模式；目标与 Lineage Leader 角色说明施加时由谁担任。
  - 每个元素都可以在“配置 JSON”（角色为“起始设置 JSON”）中直接编辑完整内容。

  检查器的修改直接写入模板草稿，点“保存”才写入模板。施加后才有意义的内容不在模板中显示，包括立即触发、运行状态、等待队列、资源地址和已保存脚本的诊断。绑定到模板参数的设置保存 `{{param.<name>}}`：数字和选项字段以文本显示这个引用，修改其他设置时引用保持不变，填入具体值即解除绑定。
- 检查器的 key 字段可以改名。修改角色 key 会同步改写连线端点、Fork 来源、layout，以及标题、设置、脚本和连线配置中引用该角色的 `{{role.<key>.id}}`、`{{role.<key>.ids}}`、`{{role.<key>.title}}`；只替换 key 完全相同的引用，前缀相同的其他角色不受影响。按 Delete 或“移除”删除选中元素，节点连同其连线一起删除，连线连同观察它的 Watch 连线一起删除。
- 已有元素（match 项）画成带“已有”标记的节点与连线：保留为灰色虚线框，修改为琥珀色，删除为红色并划掉标题；连线标签写明“已有”、类型与查找数量。工具条的“+ 已有元素”添加一个待查找的节点，默认是 Event，可在检查器中改类型。两端都是目标、Leader 或已有节点的连线，可在检查器“这条连线”中选择“施加时新增”或“已有，施加时查找”。对已有元素按 Delete 只把它标为删除；“移出查找”才把它从模板中去掉。已有 Resident 不能删除。
  - 检查器为已有元素显示“施加时”（保留、删除）、连线的“查找数量”（恰好一条、全部逐条施加）与 Event 的“事件类型条件”，下方是与 HATG 编辑区相同的设置字段。在保留的元素上修改设置，元素自动变为“修改”，只把改动的字段写入 `update.config`，原配置保留在 `config` 中用于比较；“撤销修改”恢复为保留。
  - 新建组件和连线的检查器可选“沿用已有元素的设置”，对应 `config_from`。

列表视图与 JSON 视图编辑同一份 body；列表视图另有“已有元素”一页。编辑过程通过 CreateTemplate 或 UpdateTemplate 的 dry_run 获得规范结果与 describe 预览，保存时才写入模板。

`editor.fields` 的字段说明包含 `name`、`type`、`label`，并可带 `default`、`options`、`multiline`、`language`、数值范围等。需要配置新建角色时，结构模板读取 body.roles 中的 new/fork；其他模块提供 `editor.resident_roles`，每项含 `key`、`title`、`from`，以及可选 Provider、账号、settings、说明。Family 提供 steward/router，探索提供 explorer。新增模块的角色表单复用这一描述。

施加时存在参数或新建角色则展示参数与起始设置表单，同时显示行为预览；持续结构可填写结果标题。没有参数也没有新建角色时直接施加。带已有元素的结构模板在施加前总是打开施加表单：表单以 `ApplyTemplate` 的 dry_run 按目标列出会找到、修改、删除的元素和将新建的数量，同时在画布上标出这些元素；某个“恰好一条”的连线找到多条时，表单提供候选下拉框，选定结果作为 `match_choices` 提交。编辑器支持另存为副本、删除与恢复内置；删除或恢复只改变货架资料。改变 kind 需要创建另一份模板，不能在已有条目上换执行类型。

施加表单的行为说明代入当前填写的参数，并随输入更新。例如间隔填写 300 时，说明显示 300 秒。预览先翻译模板说明，再把参数值作为纯文本插入；空参数保留引用以提示尚未填写。模板编辑器继续显示参数引用，施加预览不改写模板正文或已保存的默认值。

## 指令一览

| 指令 | 改变状态 | 说明 | 默认方案要求 |
| --- | --- | --- | --- |
| `ListTemplates` | 否 | 列出共享模板与模块描述，可按施加目标过滤。 | 对“共享”有 @viewer |
| `GetTemplate` | 否 | 读取一份模板、行为预览与编辑字段。 | 对“共享”有 @viewer |
| `CreateTemplate` | 是 | 新建模板，或从选区生成结构模板；支持只校验的 dry_run。 | 对“公共内容”有 @contributor；对 `from_selection.lineage_id` 有 @viewer |
| `UpdateTemplate` | 是 | 修改模板内容；支持 dry_run，不回写已有图。 | 对“公共内容”有 @contributor |
| `DeleteTemplate` | 是 | 删除模板，保留已经施加的结果。 | 对“公共内容”有 @admin |
| `RestoreTemplate` | 是 | 从代码恢复内置模板，包括已删除的内置条目。 | 对“公共内容”有 @contributor |
| `ApplyTemplate` | 是 | 在指定 Lineage 上向目标施加模板；结构模板支持只预览的 dry_run。 | 对 `lineage_id` 有 @contributor |

## 参数名契约

以下参数位于调用的 arguments 内；通用调用信封、幂等键和执行回执遵守 HATG ISA 公共契约。创建与更新虽然支持 dry_run，其指令声明仍属于改变状态的指令。模板版本是内容审计版本，模板指令没有另行声明模板专用的 expected_version 参数。

| 指令名 | arguments 参数名 |
| --- | --- |
| `ListTemplates` | `target_kind` |
| `GetTemplate` | `template_id` |
| `CreateTemplate` | `template`、`from_selection`、`dry_run` |
| `UpdateTemplate` | `template_id`、`template`、`kind`、`category`、`title`、`description`、`targets`、`parameters`、`body`、`dry_run` |
| `DeleteTemplate` | `template_id` |
| `RestoreTemplate` | `template_id` |
| `ApplyTemplate` | `lineage_id`、`template_id`、`target`、`parameters`、`title`、`resident_settings`、`match_choices`、`dry_run` |

### ListTemplates

参数：可选 `target_kind`，取 resident、selection、canvas。不传时列出全部模板。

结果：`{templates, kinds}`。templates 的每项是完整模板，附带 behavior 与 editor；kinds 的每项为 `{key, title, targets, editor}`，供界面发现已登记模块。target_kind 只过滤模板列表，不改变模块目录。

### GetTemplate

参数：必填 `template_id`。

结果：`{template}`，包含存储字段、`behavior: {steps, persistent, roles?}` 与 `editor`。不存在的模板返回未找到错误，不把已删除的内置条目自动装回。

### CreateTemplate

参数：可选 `template`、`from_selection`、`dry_run`。未提供 from_selection 时，template 至少给出合法 kind、title 与该模块所需 body；category、targets、parameters 等按通用与模块规则补齐。

`from_selection` 形如 `{lineage_id, element_ids, rewrite?}`；`rewrite: true` 把选区作为已有结构生成改写模板，规则见“改写已有结构”。直接保存一个 Function Block 可以提交 `{from_selection: {lineage_id, element_ids: [function_block_id]}, template: {title: "我的模板"}}`。title 放在同级 template 内；from_selection 本身只描述来源。需要时 template 的内容字段覆盖抽取的默认内容。

结果：`{template}`。正常创建分配新的 template_id，保存 version 与审计，再附加行为和编辑器描述。`dry_run: true` 只返回规范化内容与行为预览，不保存条目、不分配正式模板 ID；from_selection 仍会读取选区。施加目标、Provider 可用性等运行条件由 ApplyTemplate 校验。

### UpdateTemplate

参数：必填 `template_id`；变更可放入 `template` 对象，也可使用同级 `kind`、`category`、`title`、`description`、`targets`、`parameters`、`body`；可选 `dry_run`。两种写法同时出现时，同级字段优先。

变更按内容字段替换；body、parameters 等字段不是局部路径补丁。kind 只能保持原值，未知内容字段被拒绝。正常更新递增 version，保留创建审计并更新修改审计。

结果：`{template}`。`dry_run: true` 返回合并后的规范内容、行为预览与编辑描述，模板存储和版本保持不变。任何更新都不升级或重建已有 Function Block。

### DeleteTemplate

参数：必填 `template_id`。

结果：`{template_id}`。删除模板记录，不删除任何已施加元素。内置模板的安装记录保留，自动刷新不会重新安装它；不存在的 ID 返回未找到错误。

### RestoreTemplate

参数：必填 `template_id`，必须对应代码中的内置模板。

结果：`{template}`，包含恢复后的正文、内置来源、版本审计、行为与编辑描述。条目已删除时也可用该固定 ID 恢复。自定义模板或不属于代码内置集合的 ID 被拒绝。

### ApplyTemplate

参数：必填 `lineage_id`、`template_id`、`target`；可选 `parameters`、`title`、`resident_settings`、`match_choices`、`dry_run`。target 使用本文约定的目标对象；parameters 是参数名到实际值的映射；title 默认为模板标题；resident_settings 按模块角色 key 指定本次创建的 Provider、账号与运行设置。`match_choices` 形如 `{已有元素 key: [元素 ID]}`，把该 key 的候选限制在给定元素中，用于选定“恰好一条”的连线。

结果固定包含：

```json
{
  "function_block_ids": [],
  "created": {"resident_ids": [], "component_ids": [], "wire_ids": []},
  "delivered": [],
  "trigger_path_ids": [],
  "summary": "施加结果说明"
}
```

`function_block_ids` 列出本次新建的块；created 只列新建元素，不重复列入既有目标或 Family 成员；delivered 中每项含 resident_id 与 message_id；trigger_path_ids 汇总本次施加产生的 Trigger Path ID；summary 是用户可读结果。模块可以补充其专有结果，例如脚本 output、探索者与候选信息、Family 的角色与成员信息；结构模板另返回 `deleted_ids` 与 `updated_ids`，列出本次删除（含连带删除的连线）和修改的已有元素。没有某类结果时保留空数组。

`dry_run: true` 只适用于结构模板，其他模块返回错误。它不创建、不修改任何元素，在统一字段之外返回 `preview`，每个目标一项：

```json
{
  "target_resident_id": "resident_…",
  "found": [{"id": "…", "kind": "event", "title": "…", "key": "in", "op": "keep"}],
  "removed": [{"id": "…", "kind": "…", "title": "…"}],
  "updated": [{"id": "…", "kind": "…", "title": "…"}],
  "ambiguous": [{"key": "in", "candidates": [{"id": "…", "kind": "event", "title": "…"}]}],
  "created": {"roles": 1, "components": 0, "wires": 2},
  "error": ""
}
```

`removed` 包含连带删除的连线；`ambiguous` 列出仍需选定的 key 与候选；没有标签的连线以“来源标题 → 目标标题”作为 title，便于区分候选；某个目标找不到已有元素时，`error` 给出原因，其他目标照常预览。模板编辑时的正文预览仍通过 CreateTemplate/UpdateTemplate 的 dry_run 完成。
