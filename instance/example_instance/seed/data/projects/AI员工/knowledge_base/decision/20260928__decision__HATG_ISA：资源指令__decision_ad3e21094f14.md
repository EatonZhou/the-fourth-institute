---
kb_id: decision_ad3e21094f14
type: decision
project: AI员工
created_at: '2026-09-28'
version:
  number: 45
  created_at: "2026-09-28T10:45:00Z"
  created_by:
    user: "weihao"
    agent: "resident_2a1fc6cd272d"
  updated_at: "2026-11-04T11:12:12Z"
  updated_by:
    user: ""
    agent: "resident_83d75ad3a6c7"
importance: 8
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/tests/cases/test_isa_g7_audit.py
  - ${code_root}/tests/cases/test_isa_uploads.py
  related:
  - decision_355ed742bca5
  - decision_4648f7fa3295
  - decision_61e50306cc79
  - decision_898b8911a2ff
  - decision_4a9ba5dfada6
  - decision_b6e74d78f7b6
  - decision_8838269736d3
  - decision_4aa13bc21e8e
  - decision_7ef392b5f60d
  - decision_b820faf42218
  - decision_600ae27b0538
  supersedes: []
  derived_from:
  - decision_898b8911a2ff
  - thinking_ca8f397a47de
---
# HATG ISA：资源指令

资源指令让人和 Agent 查找、读取和保存 TFI Portal 中的持久内容。Resident 查资料、读取某份 Design、保存报告，页面打开资源详情，都使用这一类指令。本类指令都由 ResourceService 执行。所有指令共同遵守的调用格式、执行流程和错误语义见 decision_898b8911a2ff。

本篇负责资源 ISA 的指令名、参数和返回信封；ResourceService 方法与提交语义以 decision_b6e74d78f7b6 为准，资源类型声明以 decision_355ed742bca5 为准。权限要求见下表及正式 GetInstructionSchema；查找和读取仍有 viewer 要求，不能理解为免检。权限引擎与默认方案分别以 decision_b820faf42218、decision_600ae27b0538 为准；领域的私有性、存在性和字段约束继续生效，授权不扩大资源能力。

## 操作的对象

- **资源**：在 ResourceService 中登记、拥有统一 ID 的对象，例如 `decision_*`、`paper_*`、`repo_*`、`workspace_*`、`device_*`。GPU、磁盘等设备也是资源，它们的登记和修改使用本类指令，运行状态由运行环境指令（decision_4527448ad07f）读取。一条资源记录包含类型、标题、所属项目、正文或文件位置，以及该类型支持的操作。统一 ID 与地址规则见 decision_4a9ba5dfada6，ResourceService 的行为见 decision_b6e74d78f7b6。
- **资源地址**：统一 ID，或统一 ID 加内部路径。例如 `workspace_1a2b3c4d5e6f/file/report.md` 指向某个工作区中的 `report.md`。读取类指令都接受地址。
- **工作区文件**：Workspace、Resident Folder 或 Lineage Folder 中的文件和目录。三者都用根对象的 ID 加 `/file/<相对路径>` 引用。工作区与 Resident 的关系见 decision_8838269736d3。

## 指令一览

| 指令 | 改变状态 | 作用 | 默认方案要求 |
| --- | --- | --- | --- |
| `SearchResources` | 否 | 按条件查找资源 | 对“共享”有 @viewer |
| `ResolveResource` | 否 | 把地址解析为资源记录 | 对 `address` 有 @viewer |
| `ReadResource` | 否 | 读取资源正文或资源内部文件 | 对 `address` 有 @viewer |
| `ListResourceTypes` | 否 | 列出全部资源类型的声明、状态与有效能力 | 对“共享”有 @viewer |
| `GetResourceType` | 否 | 读取某个资源类型的声明、状态与有效能力 | 对“共享”有 @viewer |
| `UpdateResourceType` | 是 | 启用或停用一个资源类型，可选信封版本前提 | 对“共享”有 @admin |
| `RegisterResource` | 是 | 登记一个新资源 | 对新对象的所属位置有 @contributor |
| `CreateWorkspace` | 是 | 在空位置创建 Workspace | 固定槽免检；给出 `owner_id` 时对该所属对象有 @contributor |
| `RegisterWorkspace` | 是 | 将现成目录接入为新的 Workspace | 固定槽免检；给出 `owner_id` 时对该所属对象有 @contributor |
| `GetDirectoryCleanup` | 否 | 读取对象的目录文件清理进度 | 固定槽免检；领域只向原操作调用者或有对象读取权限的调用者返回清理记录 |
| `UpdateResource` | 是 | 修改资源的正文或元数据 | 对 `resource_id` 有 @contributor |
| `UnregisterResource` | 是 | 撤销资源登记 | 对 `resource_id` 有 @admin |
| `RunResourceAction` | 按动作声明 | 执行资源类型声明的专用动作 | 对 `resource_id` 有 @contributor |
| `CreateUpload` | 是 | 开始一次文件上传，取得上传地址 | 免检 |
| `UpdateWorkspaceFile` | 是 | 写入工作区中的文本文件 | 对 `address` 有 @contributor |

## 查找与读取

### 对象文件引用

`ResolveResource` 和 `ReadResource` 的必填参数 `address` 接受合法的字符串资源地址，也接受对象文件引用。结构化形式必须恰好包含 `workspace_id`、`repo_id`、`article_id` 中的一个，以及字符串 `relative_path`；ID 的类型必须与字段匹配，不能附加其他字段。例如：

```json
{"address": {"workspace_id": "workspace_0123456789ab", "relative_path": "reports/result.md"}}
```

`relative_path` 用 `/` 分隔，空字符串指向对象目录根；拒绝绝对路径、盘符、`.`、`..`、空路径段及软链接越界。服务先确认对象存在并检查调用者权限，再按对象当前登记的位置读取文件；对象移动不改变引用，不回退到旧的绝对路径。对象文件引用及其对应的 `<资源ID>/file/<相对路径>` 字符串形式分别报告三种失败：对象不存在或已删除、无权访问、文件不存在，遵循目录协议 decision_4aa13bc21e8e。其他资源地址隐藏不可见资源的既有规则不替代这项契约。

合法的 `<资源ID>/file/<相对路径>` 字符串地址继续可用，其他类型原有字符串寻址能力保留。Workspace、Repo 和 Article 文件的读取结果可带 `file_reference`，供报告和交付物保存稳定引用；根对象 ID 字符串仍表示资源本身。ResourceService 的 `resolve`、`reference`、`read_content` 与 `read_content_range` 使用相同的 `address` 契约，区间读取经既有读取/下载通道完成，见 decision_b6e74d78f7b6。

### 上传文件引用

`ResolveResource` 和 `ReadResource` 也接受恰好一个 `upload_id` 字段的文件引用，例如 `{"address":{"upload_id":"upload_ba95597e97d28c70bae9994c"}}`。值沿用上传时取得的句柄。`/api/uploads/<句柄>` 和本实例正式 public/internal origin 或部署显式登记的 public_origin_aliases 下的完整 URL 可作为等价地址，仅允许可选的 `download=1` 查询参数。其他站点、片段和额外字段会被拒绝，不触发远程抓取。

上传文件由上传领域检查活跃认证身份、完成状态、清理登记和安全路径；所有已认证 User/Resident 都可读。上传句柄不作为资源树节点，普通资源仍按表中的 viewer 权限检查。`ResolveResource` 返回文件 `target` 和只读操作，根 `resource` 为空；`ReadResource` 支持 `meta_only`、`preview`、有界正文及超限下载，返回 `file_reference`、原文件名、类型、大小与内容地址，二进制预览不加载全部字节。

### `SearchResources`

**按关键词、类型、项目和状态查找调用者可以看到的资源。**

- 参数：`q`（关键词，可选）、`resource_type`（可选）、`project_id`（可选，只返回属于这个 Project 的资源）、`status`（可选）、`limit`（可选）、`cursor`（可选，上一页返回的 `next_cursor`，原样交回）。
- 结果：资源摘要列表，每项包含统一 ID、类型、标题、摘要、所属项目和打开地址；命中总数；还有下一页时另返回 `next_cursor`。`project` 在只属于一个项目时是项目 ID，属于多个项目时是 ID 列表，没有项目时是空列表；资源有状态时同时返回 `status`。
- 项目、知识条目、待办和 Skill 是可按类型检索的资源。项目标签属于 Project 的 `tag_vocab`；Agent 的操作说明按其实际登记的 Skill 或知识条目读取。

### `ResolveResource`

**把一个资源地址解析为资源记录，并说明调用者可以对它执行哪些操作。**

- 参数：`address`。
- 结果：资源记录、地址中的内部目标，以及调用者当前可以执行的操作。
- 除上述对象文件引用及其对应的字符串文件地址外，地址不存在或调用者无权读取时返回 `instruction.not_found`。Runtime 不根据相似标题、路径或 ID 猜测其他资源。

### `ReadResource`

**读取资源正文，或读取资源内部某个文件的内容。**

- 参数：`address`、`max_bytes`（可选，默认 4 MiB）、`meta_only`（可选）、`preview`（可选）。后两个参数只用于文件地址。
- 结果：内容类型、版本令牌 `version`，以及内容本身：不超过 `max_bytes` 时直接返回文本或 base64 编码的字节；根资源有版本号时返回该版本号的字符串，缺少版本号时使用资源修改时间或内容摘要。超过时返回一个有时效的下载地址和内容大小，调用者从下载地址分段读取。下载地址形如 `/api/resources/download/<令牌>`，15 分钟内有效，只有发起读取的调用者可以使用，支持单段 `Range` 请求；结果同时给出 `expires_at` 与内容摘要作为 `version`。
- 读取内部文件时使用 `<资源ID>/file/<相对路径>`，文件的 `version` 是内容摘要。读取知识条目根正文得到的数字版本字符串可以用于 `UpdateResource`；下载结果中的内容摘要用于标识本次内容。
- 文件地址的根可以是 Workspace（`workspace_<id>`）、Resident（`resident_<id>`，指 Resident Folder）或 Lineage（`lineage_<id>`，指 Lineage Folder）。Resident Folder 与 Lineage Folder 的文件要求对该 Resident 或 Lineage 有 viewer，Workspace 文件按 decision_8838269736d3 的规则检查。Resident Folder 与 Lineage Folder 无权读取与文件不存在返回相同结果；Workspace、Repo 和 Article 的对象文件引用及其字符串形式遵循上文分别报告三种失败的规则。路径不能含 `.` 或 `..` 段，不跟随符号链接。Resident Folder 与 Lineage Folder 中点开头的路径和凭证类目录不可读取；Workspace 中这些路径仍可按地址读取，只在目录子项里隐藏。
- 地址指向目录时（`<资源ID>/file` 本身即根目录），结果的内容类型为 `application/vnd.tfi.directory+json`，`entries` 列出至多 200 个直接子项，每项含 name、kind、size、modified_at、mtime_ns、相对路径 `path` 和自己的文件地址 `address`。目录在前，按名称排序；点开头与凭证类子项不列出，只计入 `hidden_count`，`truncated` 表示还有未返回的子项。
- 文件地址的结果还给出 name、kind（directory、markdown、json、jsonl、text、image、pdf 或 binary）、modified_at、mtime_ns 和 `content_url`。`content_url` 是 Portal 页面显示和下载该文件的地址 `/api/resources/content?address=…`，每次请求重新检查权限，支持单段 Range，加 `&download=1` 作为附件下载；图片、PDF、音视频内联显示，其他类型按附件返回；响应都带 `X-Content-Type-Options: nosniff` 与 `Content-Security-Policy: sandbox`。Workspace 文件的结果另带 `allows.UpdateWorkspaceFile`，表示调用者能否把这个文件写回同一地址，页面的保存按钮据此显示。
- `meta_only` 只返回元数据，不读内容。`preview` 返回不超过 `max_bytes` 的 UTF-8 文本：文件更长时返回前段并标记 `truncated`，截断的预览不带 `version`；图片、PDF 与其他二进制文件不返回内容，只标记 `binary`。页面的文件预览使用 2 MiB 的 preview。
- 下载地址只对本次调用者有效，到期后失效；每次读取都重新经过权限检查。

### `ListResourceTypes`

**列出全部资源类型的声明、安装与启停状态，以及有效能力。**

- 无业务参数。
- 结果：`types` 列表和 `registry` 元数据。每项包含类型元数据、`state`、`installed`、`enabled`、`version`、`instance_count`、原始 `declaration` 与提供者动作目录。注册表元数据包含 ID 语法与 `id_exceptions`。

### `GetResourceType`

**读取一个资源类型的声明、状态，以及是否可以登记、修改、撤销登记和专用动作。**

- 参数：`resource_type`，与 `RegisterResource`、`SearchResources` 同名。
- 结果：类型元数据、`state`、`installed`、`enabled`、`version`、`instance_count`、独立原始 `declaration`，以及 `registerable`、`updatable`、`unregisterable` 和动作目录。动作包含名称、说明、参数定义、结果定义与是否改变状态。
- 顶层 `registerable` 是声明政策与按需装载的 mutation 提供者登记机制的合取；未安装类型为 false。`declaration` 保留全部原始字段，不受有效能力覆盖。类型声明与状态规则见 decision_355ed742bca5。

## 登记与修改

### `UpdateResourceType`

**启用或停用一个资源类型，保留已有实例。**

- 业务参数仅为 `resource_type` 和 `enabled`；目标是类型键 `resource_type`。结果为 `type`、`enabled`、`state` 和递增后的 `version`。
- 版本前提只放在调用信封 `expected_version` 中。调用者可原样传回 List/Get 返回的非负整数 `version`，也可使用以类型键为键的映射；没有状态记录的版本为 0。省略前提时直接更新当前状态。
- 提供前提时，在保存类型状态的同一事务内比较。版本过期返回 `instruction.version_conflict`，`details` 包含 `object_id`（类型键）、`expected_version`、`current_version`，状态与版本均不修改。页面刷新当前类型后让用户决定是否重试，不自动覆盖。
- 未安装类型不能启用；域原因 `resource_type.not_installed` 经 ISA 归一化为 `instruction.invalid_arguments`，保留在 `details.reason`。

### `RegisterResource`

**按资源类型的要求登记一个新资源，记录认证调用者为创建者。**

- 参数：`resource_type`、`fields`（该类型要求的字段对象）。例如 Markdown 知识条目在 `fields` 中提交 `project_id`、`title` 和 `body`，ResourceService 分配统一 ID、生成文件名和头部。
- 结果：新资源的统一 ID 与资源记录。
- **放在 Project 下的类型必须给出位置，没有隐含的默认 Project**（weihao 2026-10-18）。知识条目与实验写 `fields.project_id`；repo 与 Workbench 写 `fields.project_ids`（Project ID 数组），公共内容写 `["@public"]`，`@public` 不能与 Project 同时给出；文章由所在目录决定，目录必须在已登记的 Project 内；设备没有位置。Workbench 从页面设计流程创建时取所绑定 Lineage 的 Project，Lineage 不属于任何 Project 时为公共内容。自动登记（扫描 Project 目录发现的 repo 等）按路径确定位置，不受这条约束；通过 RegisterResource 登记 repo 或 Workbench 而不给 `project_ids` 时登记失败。
- `fields.project_id` 与 `fields.project_ids` 在指令 schema 中声明，该声明不限制各资源类型原有的其他 fields。新资源的创建者记录为认证调用者。
- **登记权限以拟放置位置判定。** RegisterResource 的正式权限声明把 `resource_type` 与 `fields.project_id` / `fields.project_ids` 交给方案的 `place` 规则，要求 contributor；它与登记后入树使用同一放置契约。各类型的默认位置、公共内容和多 Project 的父节点选择，统一见 decision_600ae27b0538；领域仍校验 Project 是否存在和必填位置，权限通过不代替输入校验。

- 用同一幂等键重试时返回同一个 ID，不会登记两次。
- `RegisterResource` 返回成功前同步持久化单条索引摘要；`UpdateResource` 与 `UnregisterResource` 请求有时限的索引确认。提交后失败与未确认结果见 decision_b6e74d78f7b6，不把领域已提交误判为未执行。资源类型要求目录是 Git 检出而目录不是时，以 `instruction.conflict` 失败，`details.reason` 为 `git_checkout_required`；在 `fields` 中给出 `initialize_git: true` 可让服务先初始化 Git。

### `CreateWorkspace` 与 `RegisterWorkspace`

**CreateWorkspace 在尚未占用的位置创建空 Workspace；RegisterWorkspace 将已经存在的目录登记为一个新的 Workspace 对象。** 两者由 ResourceService 执行，WorkspaceStore 保存身份、位置、来源和目录归属。通用 `RegisterResource` 不开放 Workspace 登记；新对象的接入使用 `RegisterWorkspace`，Resident 的使用关系继续由绑定指令管理。

| 参数 | 类型 | 必填 | 含义 |
| --- | --- | --- | --- |
| `path` | string | 是 | 区域内或区域外的绝对目录路径，最长 4096 字符；父目录必须存在 |
| `operation_id` | string | 是 | 本次业务操作的稳定 ID，最长 200 字符；重试沿用同一值 |
| `owner_id` | string | 否 | 目录所属的 Resident、Lineage 或 Project ID；省略时属于新 Workspace 自己 |
| `title` | string | 否 | Workspace 名称，最长 200 字符；未提供时按 Workspace 的默认名称规则处理 |

两条指令的结果都包含 `workspace_id`（稳定的新对象 ID）与 `resource`（Workspace 资源记录）。CreateWorkspace 的来源为 `origin=created`，RegisterWorkspace 为 `origin=adopted`。来源由操作决定，不能由调用者另传 `origin` 改写。`owner_id` 只决定目录随谁删除，不授予权限，也不建立 Resident 绑定；省略它不会把调用者隐式设为所属对象。

声明包含固定判定槽和可选的 `owner_id` 判定槽，后者只接受 Resident、Lineage、Project。默认方案将固定槽设为免检，给出所属对象时要求对该对象有 contributor；所属对象必须存在且未删除。Workspace 登记后的权限继续按当前权限方案及 decision_8838269736d3 判断。

`operation_id` 在认证调用者作用域内保存创建身份和进度。同一调用者以相同操作 ID 和相同参数重试，沿用原 Workspace ID 与目录；改用不同路径、归属、标题或创建/接入方式时拒绝。同一请求的传输重试仍遵循 ISA 信封 `idempotency_key` 规则，业务操作 ID 不替代信封键。

CreateWorkspace 要求目标位置不存在；RegisterWorkspace 要求目标是现成目录，不移动、不改名、不覆盖内容。两者都拒绝仍被清理任务占用的位置、冲突登记及落在本机其他实例根目录或区域内的位置。参数非法以 `instruction.invalid_arguments` 失败，位置已占用以 `instruction.conflict` 失败，父目录、接入目录或所属对象不存在以 `instruction.not_found` 失败；权限拒绝沿用 ISA 授权错误。文件系统故障保留创建进度，同一操作 ID 可继续，目录和登记都就绪才返回创建完成。

### `GetDirectoryCleanup`

**读取某个对象产生的文件清理任务，不发起删除，也不改变清理进度。**

| 参数 | 类型 | 必填 | 含义 |
| --- | --- | --- | --- |
| `resource_id` | string | 是 | 被删除或产生清理任务的对象 ID；对象注销后仍用原 ID 查询 |

结果为 `items` 数组。每项包含 `cleanup_id`（清理任务 ID）、`owner_id`（产生清理任务的对象）、`operation_id`（原操作）、`location`（待清理位置）、`actor`（原操作调用者）、`created_at`、`available_at`（下次可尝试时间）、`completed_at`、`attempts`、`last_error` 和 `status`。状态为 `pending`（待执行）、`retry`（等待重试）或 `completed`（已完成）。没有清理任务时返回空数组，空数组本身不证明登记仍存在或已注销。

固定槽在默认方案中免检，领域继续检查可见性：原操作调用者可以读取自己的清理记录；其他调用者必须有对应对象的读取权限，否则返回 `instruction.forbidden`。注销结果与文件清理进度分别报告；清理失败不恢复登记，后台持久重试，重启后继续。该入口不授予对象权限，不提供任意目录清理或管理员重试动作。

### `UpdateResource`

**按资源类型修改正文或元数据。**

- 参数：`resource_id`、`changes`（要修改的字段）。
- 结果：修改后的资源记录与 `version`。知识条目使用新版本号的字符串；其他类型返回其版本或修改时间，类型没有提供时返回空字符串。
- 修改 Knowledge Provider 管理的知识条目主正文时，可以在调用中附带 `expected_version`（当前版本号）；版本不一致返回 `instruction.version_conflict`。Knowledge 的 `changes.text`（完整文本）与 `changes.body`（仅 Markdown 主文件正文）必须且只能提供一个。`body` 保留服务端锁内读取的元数据，仅由系统推进版本；允许空字符串清空正文，不接受 null，不用于伴随文件、Skill、Article 或版本恢复。版本恢复规则见 decision_3ece26686464。Skill 的文本保存仍沿 Skill 自身生命周期，不能把知识主文件的数字版本与恢复链规则套到 Skill。
- Workspace 只允许非空 `title` 与必填 `expected_version`；版本原样取自 `ResolveResource.result.resource.version`，在同一行锁内鉴权、比较和写入（decision_8838269736d3）。Workbench 使用 `changes.expected_revision`。其他类型按自己的字段契约处理；ISA 对不支持信封 `expected_version` 的类型返回 `instruction.invalid_arguments`。

### `UnregisterResource`

**撤销一个资源的登记。**

- 参数：`resource_id`。
- 结果：被撤销的统一 ID。
- 撤销登记后正文是否保留，由该资源类型的 Design 规定。

Workspace 支持 `UnregisterResource(resource_id=workspace_id)`，要求目标 Workspace 的 `@admin`，类型能力 `unregisterable=true`；个人 AMA 的私有边界仍先行适用，无权限返回 `instruction.forbidden`。目录生命周期遵循 decision_4aa13bc21e8e：绑定、自有引用或其他对象的引用不阻止注销；注销解除使用绑定，已删除身份不得复活，删除 Workspace 不终止其他 Resident 的执行。

来源为 `created` 的目录随对象删除，区域内外采用同一规则；目录中的嵌套登记、接入目录和手放文件随外层目录处理。直接注销 `adopted` 目录只删除其登记并保留文件。区域根、实例根及其上级目录永不删除，软链接只删除链接本身；目录已不存在时仍可完成注销。

注销对象、关联登记和绑定与记录待清理目录在同一次数据库写入中完成，随后返回注销结果。后台清理失败记录原因并持久重试，不回滚登记；未清理完的位置继续占位，创建和接入拒绝，扫描跳过。调用者用 `GetDirectoryCleanup(resource_id=workspace_id)` 单独读取文件清理进度。注销前在事务中重新鉴权，目录登记以权威存储为准；提交后的索引确认继续遵循 decision_b6e74d78f7b6。

### `RunResourceAction`

**执行资源类型在动作目录中声明的一个专用动作。**

- 参数：`resource_id`、`action`、`arguments`。
- 结果：由该动作的声明定义。
- 未声明的动作被拒绝。动作是否改变状态由声明决定：改变状态的动作保存执行记录，只读动作直接返回。

## 资源动作目录

已经接入 ISA 的领域操作通过 `RunResourceAction` 执行：资源类型在指令目录中声明动作，并由领域 Provider 提供执行函数。顶层指令保持稳定，领域可以独立增加动作。调用者通过 `GetResourceType` 查询当前可执行的动作；只在领域服务登记、尚未在 ISA 声明的操作由对应页面或领域接口执行。

- **每个动作都有完整声明。** 声明包含动作名、所属资源类型、一句话说明、参数定义、结果定义和是否改变状态。动作名写作“对象.动词”，例如 `summary.save`、`git.commit`。`GetResourceType` 与 `ListInstructions`（decision_aa4fdbac8364）都能读到这些声明。
- **通用登记受类型政策约束。** 类型允许统一登记且提供者有登记机制时使用 `RegisterResource`；chatroom、paper、search 等政策关闭的类型由各自专用指令创建。统一登记拒绝的域原因是 `resource_type.not_registerable`，ISA 以 `instruction.invalid_arguments` 返回，并在 `details.reason` 保留该原因。修改和撤销登记继续按类型能力使用通用指令，动作目录只放通用指令无法表达的领域操作。
- **动作只做确定性操作。** 需要模型完成的工作，例如深读论文、解释图表或评审，由承担该职责的 Resident 完成，发起方从应用配置取得目标 Resident，用 `DeliverMessage` 按 `resident_id` 投递（decision_2fe6fd5367fb），不设为资源动作。

动作名称和有效能力通过 `GetResourceType` 查询，不在此维护第二份静态动作清单。Device 凭据与连接、Workbench 保存等动作的输入和领域约束由相应 Design 说明；Paper、Article、Repo、Test、Project 的页面或领域接口是否进入 ISA，同样以正式动作声明为准。Search 已无“修改 owner 列表”的资源动作，不能凭历史目录调用。

## 上传

大体量的文件内容不放在指令参数中。上传分两步：先用 `CreateUpload` 取得上传句柄和数据通道地址，再把文件字节写入该地址。上传完成后得到一个上传引用，可以放进消息的内容分段、帖子正文，或作为领域动作（例如 Paper 登记 PDF）的参数。

### 权限实例与资源生命周期

资源登记、修改和撤销登记提交后，ResourceService 同步权限事实；完整事实收集同时使用用户与 Project 登记、协作描述、功能的 `permission.records` 和索引快照，不把索引当作唯一事实来源。权限方案、实例补齐与规则合并以 decision_b820faf42218 为准，默认放置和授权以 decision_600ae27b0538 为准。资源指令没有另一个初始化权限入口。

跨领域提交顺序、提交后失败、索引确认和重读规则统一见 decision_b6e74d78f7b6；本篇只规定 ISA 如何呈现这些结果。

### `CreateUpload`

**开始一次文件上传，返回上传句柄和写入字节的数据通道地址。**

- 参数：`purpose`（用途：对话附件、帖子图片、Paper PDF、Article 文件或图表输入）、`target`（可选，目标工作区或资源地址），以及文件来源二者之一：`source_address`（已有的工作区文件地址），或 `file_name`、`media_type` 与 `size`（稍后通过数据通道写入字节）。
- 结果：上传 ID。使用 `source_address` 时，服务直接从该文件复制内容，上传 ID 立即可用；否则同时返回数据通道地址和到期时间，字节写入完成后上传 ID 才可使用。
- Resident 通过 Harness 工具调用时使用 `source_address`，不需要自己传输字节。
- `purpose` 还包括头像（`avatar`，PNG、JPEG 或 WebP，最大 2 MiB，不接受动画），供 `UpdateUser`、`UpdateResident`、`UpdateProject` 和 `UpdateLineage` 设置头像；权限系统让上传只归上传者时（PAR），只有上传者本人能用它设置头像（decision_5bc3fdfc7eec）。帖子图片完成后所有登录用户都可以读取，内容校验为 PNG、JPEG、GIF 或 WebP，最大 12 MiB。上传完成后的字节由所有活跃且已认证的 User/Resident 按上述上传文件引用读取。
- 大小和类型限制由用途决定，以 decision_61e50306cc79 的上传用途契约为准；本篇不另列一份附件容量默认值。服务在写入目标工作区前校验调用者、目标、大小和类型。见 decision_61e50306cc79。`target` 为 Resident ID 时（对话附件），调用者需要能看到该 Resident；其他 `target` 必须是调用者可以修改的工作区或资源地址，否则以 `instruction.not_found` 或 `instruction.forbidden` 失败。图片按文件内容识别类型，Paper PDF 要求内容以 `%PDF-` 开头；使用 `source_address` 复制时同样检查。

上传后的文件是普通文件；Resident 可按实际文件路径使用系统工具删除，无独立的上传删除指令。浏览器移除附件通过上传文件 HTTP 接口完成，文件及保留登记的生命周期见 decision_61e50306cc79。

### `UpdateWorkspaceFile`

**把文本内容写入工作区中的一个文件。**

- 参数：`address`（`<工作区ID>/file/<相对路径>`）、`content`。
- 结果：写入后的内容摘要和修改时间。
- 路径必须位于该工作区内。`address` 只接受 Workspace 文件地址，绝对路径返回 `instruction.invalid_arguments`（weihao 2026-10-18）；页面拿到的链接文件和 Watchboard 配置由服务端给出所在 Workspace 的文件地址（链接文件的 `file_address`、配置的 `address`），不在任何登记 Workspace 中的文件只读。文件的版本令牌是内容的 SHA-256。需要避免覆盖别人刚写入的内容时，可以在调用中附带 `expected_version`（`ReadResource` 返回的文件版本）；内容已经变化时返回 `instruction.version_conflict`，文件保持不变。一般调用不需要提供。

## 参数名契约

下表列出 arguments 内的顶层参数名；必填条件、类型和枚举以 GetInstructionSchema 为准。调用信封中的 expected_version、idempotency_key 与 wait_seconds 不列入 arguments。

| 指令名 | arguments 参数名 |
| --- | --- |
| `SearchResources` | `q`、`resource_type`、`project_id`、`status`、`limit`、`cursor` |
| `ResolveResource` | `address` |
| `ReadResource` | `address`、`max_bytes`、`meta_only`、`preview` |
| `ListResourceTypes` | 无 |
| `GetResourceType` | `resource_type` |
| `UpdateResourceType` | `resource_type`、`enabled` |
| `RegisterResource` | `resource_type`、`fields` |
| `CreateWorkspace` | `path`、`operation_id`、`owner_id`、`title` |
| `RegisterWorkspace` | `path`、`operation_id`、`owner_id`、`title` |
| `GetDirectoryCleanup` | `resource_id` |
| `UpdateResource` | `resource_id`、`changes` |
| `UnregisterResource` | `resource_id` |
| `RunResourceAction` | `resource_id`、`action`、`arguments` |
| `CreateUpload` | `purpose`、`target`、`source_address`、`file_name`、`media_type`、`size` |
| `UpdateWorkspaceFile` | `address`、`content` |

### Project 知识迁移动作

`RunResourceAction(resource_id=<目标Project>, action="knowledge.move", arguments={items:[{resource_id, expected_version}], operation_id?})` 统一完成普通 Markdown Knowledge 的项目归属、主文件和伴随目录、版本审计、目录登记、权限事实与在线索引迁移。一次接受一至一百条不同 ID，必须携带各条目当前版本；限 created 登记、Project 类型目录内的平面主 Markdown 与 `<ID>/` 伴随目录，同一文件系统；adopted、Skill、Experiment、跨文件系统和含独立资源或保留登记的伴随目录不适用。默认权限要求源、目标 Project 与源知识条目的 contributor 权限。

`operation_id` 可省略，省略时从调用者、目标和条目版本列表稳定生成；重试保留相同参数。结果给出 `operation_id`、`status`（`completed` 或 `partial`）、已提交的 `committed`、未提交 ID 列表 `pending`、索引确认 `projected` 与可选 `error`。部分完成时沿同一操作继续，已提交版本不重复增加；指令幂等键只重放旧回执，继续处理应使用新的指令调用键。预检、恢复与并发语义见 decision_3ece26686464。
