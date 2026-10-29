---
kb_id: decision_6591e7793116
type: decision
project: AI员工
created_at: '2026-09-28'
version:
  number: 46
  created_at: '2026-09-28T10:45:00Z'
  created_by:
    user: tfi-exported-user
    agent: resident_2a1fc6cd272d
  updated_at: '2026-10-29T11:24:16Z'
  updated_by:
    user: ''
    agent: resident_dbfb834a0539
importance: 8
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/application_targets.py
  - ${code_root}/management_portal/society_service/application_target_configuration.py
  related:
  - decision_2fe6fd5367fb
  - decision_46c37026e8ea
  - decision_4648f7fa3295
  - decision_898b8911a2ff
  - decision_3e07c6d35cac
  - decision_6fc653d55c79
  - decision_eb066fbea4ed
  - decision_8fe87d812a4c
  - decision_4637ca4a69e2
  - decision_b820faf42218
  - decision_600ae27b0538
  supersedes: []
  derived_from:
  - decision_898b8911a2ff
  - thinking_ca8f397a47de
---
# HATG ISA：组织与分工指令

组织与分工指令处理 Lineage 之间和 Portal 全局的组织关系：创建和管理 Project、在 Lineage 之间迁移 Resident 或一组对象。单个 Lineage 内部的增删改使用 Lineage 定义指令（decision_4637ca4a69e2）。所有指令共同遵守的调用格式、执行流程和错误语义见 decision_898b8911a2ff。

## 操作的对象

- **Project**：一个项目，拥有稳定的项目 ID、类型（`company`、`academic`、`personal` 或 `other`）、状态、截止日期、标签、说明、成员角色和位于 `${share_root}/Projects/<项目 ID>` 的项目目录。项目目录包含知识库、源码等种子目录和一份自由撰写的摘要 `summary.md`。见 decision_4648f7fa3295。
- **Principal 层级**：Portal 按 Portal Admin、User、Project、Lineage、Resident 五层组织持续协作。Project 层 Lineage 由 Project Resident 担任 Leader，并引用同项目各子 Lineage 的 Leader。层级在 Lineage 和 Resident 创建时确定（CreateProject 建立 Project 层 Lineage），没有事后修改层级的指令。层级规则与导航见 decision_3e07c6d35cac。
- **跨 Lineage 迁移**：把一个 Resident，或一组对象（Resident、组件及它们之间的连线），从一个 Lineage 移到另一个 Lineage，保留身份、会话和运行游标。迁移是可恢复的操作，有自己的操作 ID。
各应用通过自己的结构化配置选择接收 Resident 或 Lineage。语义搜索、资源聊天、摘要、Notice 分类和 TAU 不共享任意职责名登记。接收目标改变不授予权限，执行仍使用认证身份，由权限系统按执行者自身的权限判定（decision_b820faf42218）。配置缺失或目标删除时返回明确错误，不回退到 Leader。

## 指令一览

| 指令 | 改变状态 | 作用 | 默认方案要求 |
| --- | --- | --- | --- |
| `CreateProject` | 是 | 创建一个 Project 及其目录 | 免检 |
| `ListProjects` | 否 | 列出能看到的 Project，带团队规模与最近变化 | 对“共享”有 @viewer |
| `GetProject` | 否 | 读取 Project 信息、摘要、团队、资源数量与最近修改的资源 | 对 `project_id` 有 @viewer |
| `UpdateProject` | 是 | 修改 Project 的状态、截止日期、类型、说明、标签、摘要或头像 | 对 `project_id` 有 @admin |
| `DeleteProject` | 是 | 删除一个 Project 及其全部内容 | 对 `project_id` 有 @admin |
| `RenameProject` | 是 | 修改 Project 的 ID 并移动项目目录，Portal 中的引用一起改 | 对 `project_id` 有 @admin |
| `GetKnowledgeTags` | 否 | 读取 Project 登记中的知识标签 | 对“共享”有 @viewer |
| `UpdateKnowledgeTags` | 是 | 用一份完整列表替换 Project 登记中的知识标签 | 对“共享”有 @admin |
| `MoveResident` | 是 | 把一个 Resident 迁移到另一个 Lineage | 对 `lineage_id` 有 @contributor；对 `resident_id` 有 @admin；对 `target_lineage_id` 有 @admin |
| `MoveLineageObjects` | 是 | 把一组对象迁移到另一个 Lineage | 对 `lineage_id` 有 @admin；对 `target_lineage_id` 有 @admin |
| `GetMoveOperation` | 否 | 读取迁移操作的进度 | 对“共享”有 @viewer |

## Project

Project 的资源操作由 ResourceService 执行；GetKnowledgeTags 与 UpdateKnowledgeTags 由 SocietyService 执行（decision_898b8911a2ff 的“指令在哪里执行”）。

### `CreateProject`

**创建一个 Project，建立项目目录、种子文件及其 Project 层 Lineage 和 Leader，并让创建者成为该 Project 的 admin。**

- 参数：`project_id`（同时作为项目名称，是 Project 唯一的名称；1 到 80 个字母（包括中文）、数字或 `_ . @ + -`）、`project_type`、`description`（可选）、`status`（可选）、`ddl`（可选）、`tags`（可选）、`language`（可选，`zh-CN` 或 `en`，默认 `zh-CN`；只选择新项目种子文档的语言，不保存为项目元数据或修改已有正文）。
- 结果：新 Project 的信息。
- 项目 ID、已登记路径或目标目录冲突时拒绝创建，不覆盖已有内容。项目目录固定为 `${share_root}/Projects/<项目 ID>`。
- 创建后短暂等待资源索引收录新项目，随后内部幂等创建 Project 层 Lineage 和 Leader；通过 RegisterResource 注册 Project 也执行同一流程。
- 项目登记完成而主体初始化失败时，返回可重试的恢复回执。使用原幂等键重试只补齐主体，不重复创建项目目录。前端读取导航里的既有主体，不在打开项目时创建。

### `ListProjects`

**列出调用者能看到的全部 Project：登记信息，以及团队规模、正在运行的 Resident 数和最近一次变化的时间。**

- 参数：无。
- 结果：`projects` 按项目登记的顺序排列，每项包括：
  - 登记字段：`project_id`、`status`、`project_type`、`description`、`ddl`、`tags`；
  - 团队：`project_resident_id`（Project 层 Lineage 的 Leader）、`lineage_count`（普通 Lineage 数）、`resident_count`、`running_resident_count`；
  - `last_changed_at`：项目下资源最近一次修改的时间，项目还没有资源时为空。
  另有 `status_vocab`：项目可用的状态。
- 列表只含调用者能读的 Project。团队数字只统计调用者能读的 Lineage：Project 层 Lineage 和普通 Lineage 计入，个人和 User 层 Lineage 不计入。Project 层 Lineage 的画布中引用的子 Lineage Leader 只算作其所在 Lineage 的成员。
- 项目页的列表（decision_46c37026e8ea）、Society 与 HATG 的项目选择都读这条指令。

### `GetProject`

**读取 Project 的登记信息、`summary.md`、团队、各类资源的数量和最近修改的资源。**

- 参数：`project_id`；`include_resources`（可选，默认 true）。为 false 时不统计项目下的资源，`resource_counts` 与 `recent_changes` 为空：统计要逐一检查调用者能读的资源，是这条指令最慢的部分，只需登记信息与团队的读取跳过它。
- 结果：
  - `project`：项目的资源记录，另带 `status`、`project_type`、`description`、`ddl` 与 `tags`；
  - `summary`：`summary.md` 的内容，不存在时标明；
  - `team`：`project_resident_id`、`project_lineage_id`、`resident_count`、`running_resident_count`，以及 `lineages`。`lineages` 列出每个普通 Lineage 的 `lineage_id`、`title`、`leader_resident_id`、`member_count` 与 `running_count`，计入规则与 `ListProjects` 相同；
  - `resource_counts`：资源类型到数量的映射；
  - `recent_changes`：最近修改的资源，新的在前，最多 20 项，每项为 `resource_id`、`resource_type`、`title`、`url`、`modified_at`；
  - `status_vocab`：项目可用的状态，与 `ListProjects` 相同。
- `resource_counts` 与 `recent_changes` 不含 Lineage、Resident、Workspace 和 Family，团队另由 `team` 给出。两者都只含调用者能读的资源。
- 团队由 SocietyService 的 Project 领域给出；Resident 的实时状态和工作摘要由调用者另外读取（`ListResidentStates`、`GetResidentSummaries`）。

### `UpdateProject`

**修改 Project 的状态、截止日期、类型、说明、标签、摘要或头像。**

- 参数：`project_id`、`changes`（可以包含 `status`、`ddl`、`project_type`、`description`、`tags`、`summary` 与 `avatar`）。
- 结果：修改后的项目信息；修改了头像时带上保存后的 `avatar`。
- `description` 去掉首尾空白后保存；`tags` 是字符串数组，去掉空白、空项和重复项后整体替换原标签。
- `summary` 以完整文本替换 `summary.md`；不存在时创建。
- `avatar` 为 `{"sprite_index": 0–41}`、`{"upload_id": …}`（`CreateUpload` 用途 `avatar` 的已完成上传）或 `null`（回到名称首字），见 decision_5bc3fdfc7eec。DeleteProject 同时删除头像，RenameProject 把头像移到新 ID 下。

### `DeleteProject`

**删除一个 Project 及其下的全部内容：项目的资源、Lineage 与 Resident，以及项目目录下的全部文件。**

- 参数：`project_id`。
- 结果：被删除的项目 ID，随之删除的资源、Lineage 与 Resident 的 ID，以及 `unlinked`：其他功能共享库中只去掉了与该 Project 关联、本身保留的对象，按功能键分组（例如 `paper` 是论文 ID）。
- 删除可以处理有内容的项目。项目中的 Resident 完成或停止排队任务、运行任务和 Fork 创建任务，相关迁移结束后，可以删除其 Lineage。先注销项目下的资源登记，再删除全部 Lineage（包括 Project 层 Lineage）及其中的 Resident，最后删除 `${share_root}/Projects/<项目 ID>` 下的全部文件和目录。Lineage 与 Resident 目录同样直接删除，不保留任何归档；项目登记与成员角色同时移除，并写入审计记录。完整顺序见 decision_4648f7fa3295。
- 删除后不能恢复。谁可以执行由权限系统决定：默认模板要求对该 Project 有 admin，即创建者、管理员或经实例中增加的表项、临时凭证取得 admin 的主体。
- 中途失败时返回错误，已经删除的内容不恢复；再次调用会继续删除剩余部分。资源注销失败时返回 `instruction.failed`，`details.failed` 列出失败的资源，项目文件保持不动。

### `RenameProject`

**修改 Project 的 ID，并把项目目录移到 `${share_root}/Projects/<新 ID>`；Portal 中对旧 ID 和旧目录的引用一起改。**

- 参数：`project_id`、`new_project_id`（规则与 `CreateProject` 的项目 ID 相同）、`dry_run`（可选；只检查并报告将要修改的内容，不做修改）。
- 结果：新旧项目 ID 与目录、暂停接单的 Resident、各表改写的行数（含权限树路径）、改写的文件与链接、执行环境状态，其他功能共享库中改用新 ID 的对象（`relations`，按功能键分组，例如 `paper` 是论文 ID；`dry_run` 时是将要改的对象），以及提到旧目录的服务脚本（`external_references`，Portal 不修改，需人工检查）。`dry_run` 另外返回正在执行的 Resident 与阻止改名的挂载和服务。
- 默认模板要求对该 Project 有 admin。新 ID 已被其他 Project 使用、目标目录已存在、旧 ID 同时是 Project 状态或知识标签时拒绝。只改 ID 而目录已经是 `${share_root}/Projects/<新 ID>` 时不移动目录。
- 执行顺序：项目 Lineage 中的 Resident 和工作区在项目目录中的 Resident 先暂停接单；其中有 Resident 正在执行时拒绝，接单恢复原状。然后移动目录，在一个数据库事务中改写全部 Portal 数据：值为旧 ID 的字段与 JSON 键（登记、成员角色、权限树节点与表项、Lineage、Resident、资源索引等）、`project:<ID>` 形式的键段、旧目录下的路径，以及权限树中含旧 ID 的元素路径（decision_b820faf42218）。事务失败时目录移回原处。最后改写目录内提到旧路径的文本文件、`summary.md` 与 `agent_port/boot.md` 标题中的项目 ID、`#project("<ID>")` 标记，更新 git worktree 的双向链接、`${share_root}/Projects` 与 `${share_root}/Agents` 中指向旧目录的符号链接，以及按工作目录保存的执行环境状态（Codex 信任目录、Claude Code 项目文件夹、Kimi 会话目录），然后恢复接单。
- 历史记录保留原文：对话事件、聊天消息、执行记录、审计与指令记录不改写。旧路径前后紧接其他路径字符时视为别的路径，不改写。
- Portal 之外的挂载点位于项目目录中，或 systemd 服务配置使用该目录时拒绝改名：这些需要先由管理员迁移。
- 移动后修改文件出错时不回滚，错误列在结果的 `files.errors` 中。

### `GetKnowledgeTags`

**读取全部 Project 共用的知识标签（Project 登记中的 `tag_vocab`）：每个标签的 ID、`knowledge_base` 子目录、说明与可选参数。**

- 参数：无。
- 结果：`tags`（每项含 `id`、`folder`、`description`，需要时含 `param`）与 `version`（标签列表的内容版本）。
- 由 SocietyService 的 Project 领域执行；任何用户和 Resident 都可以读取。

### `UpdateKnowledgeTags`

**用一份完整列表替换全部 Project 共用的知识标签，用于新增、改名和删除标签。**

- 参数：`tags`（完整的标签列表；`folder` 缺省与 `id` 相同）。用 `GetKnowledgeTags` 返回的 `version` 作为 `expected_version`，期间标签被别人修改时返回 `instruction.version_conflict`，不覆盖。
- 结果：保存后的 `tags` 与新的 `version`。
- 标签 ID 以小写字母开头，只含小写字母、数字和下划线；`folder` 是 `knowledge_base` 下的一层目录名。ID 或 `folder` 重复、缺少 `description` 时拒绝修改。
- 只改登记：新建的 Project 按其中的 `folder` 建立 `knowledge_base` 子目录；已有项目的目录迁移、历史条目的处理与模板说明由调用者完成（`add-mindflow-tag`、`rename-mindflow-tag`、`delete-mindflow-tag`）。
- 标签决定所有项目的知识库目录，只有 Portal 管理员可以修改。

## 跨 Lineage 迁移

### `MoveResident`

**把一个没有被流程引用的 Resident 迁移到目标 Lineage，保留其身份和会话；Family 成员随之离开 Family，继承等关系断开。**

- 参数：`lineage_id`、`resident_id`、`target_lineage_id`、`allow_empty_source`（可选，允许迁走源 Lineage 中唯一的 Resident；默认 `false`）。源有多个 Resident 时按普通迁移规则处理；迁走唯一成员时，源 Lineage 的流程与待处理 HATG 工作必须为空。
- 结果：迁移操作 ID；`family_id` 是这个 Resident 离开的 Family，不是成员时为空；`detached` 列出被断开的节点关系（`id`、`source`、`target`、`type`）。
- 拒绝时不做任何修改，错误用中文说明原因：Resident 仍被连线、组件或 Function Block 实例引用（列出这些对象）；是 Family 的门面、Router 或 steward；是个人助手的默认 AMA；是源 Lineage 的 Leader。Family 为成员生成的元素和监听连线不算引用，随成员离开一起移除。
- 迁移不删除源 Lineage，也不替换源 Lineage 的 Leader。Resident 菜单 Identity 页的 Move 使用这条指令：迁移设置显示在身份信息右侧，Resident 是 Family 成员时先说明它会离开哪个 Family；Leader、Family 角色和默认 AMA 的 Move 按钮禁用，提示原因。
- 从画布转移成功后，画布切换到目标 Lineage 并选中这个 Resident。它的 Chat 保持打开，会话不变，地址栏和 Chat 路由改到目标 Lineage；画布不改为打开源 Lineage 的 Leader。
- 迁入时保留原坐标作为布局锚点；原位置被目标图的 Resident、原语或 Function Block 占用时，自动放到最近的空位，目标图已有节点不移动。
- 不要求 Resident 先停下来：正在工作的 Resident 的自有工作区在本轮工作结束后再搬到新位置，排队中的工作等迁移完成后继续。迁移完成前，改名、安装 Watchboard 和移除 Watchboard 这些写入自有目录的操作返回错误，调用者在迁移完成后重试。

### `MoveLineageObjects`

**把一组对象及其中的 Resident 原子迁移到目标 Lineage，保留身份、会话和运行游标。**

- 参数：`lineage_id`、`object_ids`、`target_lineage_id`（省略时迁入一个新 Lineage）。
- 结果：迁移操作 ID、源与目标 Lineage ID 和被断开的关系。
- 省略目标时，新 Lineage 的标题为“<源标题> · 迁出”，Project 和 `lineage_membership` 与源相同。个人 Lineage 可以保持空的 Project。
- 迁移不要求调用者先整理选区，也不要求选中的对象先停下来。选区与外部之间的连线、继承关系和算子执行者绑定自动断开，结果中列出被断开的关系；正在进行的工作照常继续，Resident 的等待队列随它一起迁移，跨边界连线上等待投递的调用随连线删除。
- 迁入子图保持可见对象的内部相对位置；整体平移到与目标图已有对象不重叠的位置。Function Block 内部隐藏对象保留其内部布局，目标图原有位置不变。
- 选区包含源 Lineage 的 Leader 时，源 Lineage 从仍归属它的 Resident 中随机选出新的 Leader，候选排除算子执行者和其他 Lineage 的结构引用。Project 层 Lineage 必须保留自己的 Leader。完整规则见 decision_6fc653d55c79。

### `GetMoveOperation`

**读取一次迁移操作的阶段和终态。**

- 参数：`operation_id`。
- 结果：迁移阶段、终态和错误。
- 只凭操作 ID 读取，调用者需要能读取迁移涉及的两个 Lineage。

## 应用配置与执行身份

DeliverMessage 使用明确的 resident_id。GetDutyBindings、UpdateDutyBindings、ResolveDuty 和 duty 投递参数已经移除。Family 路由读取 Family 的结构化成员关系。

应用配置保存各应用把工作交给谁：`resident_discovery`、`resident_summary`、`notice_classifier`、`semantic_search:<surface>`、`resource_chat:<resource_type>` 和 `tau`。应用按明确类型读取自己的配置，不按职责名解析。

所有应用的配置都保存在 lineage schema 的核心表 `application_targets` 中，每个应用一行，以应用名为键。每行在 `resident_id` 或 `lineage_id` 中填一个，指向该应用的目标 Resident 或 Lineage；另外记录修订号、是否暂停接收、原因、修改者和修改时间。表本身不限定应用名，也不限定检索范围或资源类型，允许哪些应用由代码声明：

- 核心应用由 `application_targets.py` 声明，包括 `resident_discovery`、`resident_summary`、`semantic_search:<surface>` 和 `resource_chat:<resource_type>`。资源聊天覆盖知识条目、Skill、仓库、文章、实验和设备；Project 没有资源聊天，项目对话是 Project Resident（decision_46c37026e8ea）。
- 功能应用由功能通过 `application.targets` 接入点声明（decision_15aab18c0ab3）：Notice Board 声明 `notice_classifier`，目标是 Resident；Test 声明 `tau`，目标是 Lineage，并支持暂停接收。

声明里写明目标类型，读取时也按这个类型取值，Lineage 应用不能当作 Resident 读取。打包时去掉某个功能后，它的应用名不再被接受，但它在表里的行保留，只是没人再读；去掉功能不需要改 schema。

更换承担者是很少发生的维护操作，没有对应的 ISA 指令。维护者在主机上运行 `scripts/application_target.py`：

- `show <application>` 打印当前配置：`target_id`（Resident 或 Lineage，按应用类型固定）、`target_available`（目标当前存在且未删除）、`revision`、`admission_state`（`open`、`paused` 或 `unconfigured`）、`reason`、`updated_by`、`updated_at`。
- `set <application> --expected-revision N --actor <名字>` 加上 `--target`、`--pause`/`--resume`、`--reason` 中的一项或多项修改配置。`--expected-revision` 必须等于 `show` 读到的 revision，不一致时拒绝，避免两位维护者互相覆盖；`--actor` 记为 `updated_by`。目标必须存在且未删除，类型与应用要求一致。
- 暂停接收（`--pause`）表示应用暂停接收新工作：应用在登记新工作前检查它并明确拒绝，已经接收的工作照常完成和恢复。只有声明了支持暂停的应用实现这一检查，目前只有 `tau`：暂停后新的 TAU Campaign 在登记前拒绝（decision_c953eab654cd）。其他应用只能读取和按版本切换目标，要求暂停时明确报错。
- 支持暂停的应用切换目标前必须已经暂停接收，恢复接收另行提交；暂停、恢复和切换在同一条配置记录上串行提交，切换不会与正在登记的新工作交错。

Notice Board 分类与 Which Resident 推荐的功能开关由当前目标 Resident 所属 Lineage 中的 Event 和 Effect 负责。两项应用不支持暂停接收：应用配置只选择承担者，PauseComponent（`paused: true|false`）控制图中组件，不在应用配置复制关闭状态。应用按 `system:<application>:event|effect` 的 client_key 定位组件，核验 Event → Effect → 当前承担者的完整连接，再触发真实 Event。修改承担者后，连接不一致的请求拒绝派发，管理者须显式修复目标连线。分类保持经过认证的 portal 服务主体，推荐保持网页请求者，后续 Effect 使用该发起身份检查权限。

由人或 Resident 触发的 Event 保留原请求者作为 initiated_by；没有发起者的自动 Event（定时、文件、发布等）以 Lineage 登记的运行身份 `run_as_principal` 执行（decision_4adcc1ff2f2c）。Effect 按这个身份检查权限。Family 消息可以由明确的门面路由发出，这不改变执行身份。系统不借用 Leader、owner 或其他 Resident 的身份。

## 参数名契约

下表列出 arguments 内的顶层参数名；必填条件、类型和枚举以 GetInstructionSchema 为准。调用信封中的 expected_version、idempotency_key 与 wait_seconds 不列入 arguments。

| 指令名 | arguments 参数名 |
| --- | --- |
| `CreateProject` | `project_id`、`project_type`、`description`、`status`、`ddl`、`tags`、`language` |
| `ListProjects` | 无 |
| `GetProject` | `project_id`、`include_resources` |
| `UpdateProject` | `project_id`、`changes` |
| `DeleteProject` | `project_id` |
| `RenameProject` | `project_id`、`new_project_id`、`dry_run` |
| `GetKnowledgeTags` | 无 |
| `UpdateKnowledgeTags` | `tags` |
| `MoveResident` | `lineage_id`、`resident_id`、`target_lineage_id`、`allow_empty_source` |
| `MoveLineageObjects` | `lineage_id`、`object_ids`、`target_lineage_id` |
| `GetMoveOperation` | `operation_id` |
