---
kb_id: decision_b6e74d78f7b6
type: decision
project: AI员工
created_at: '2026-07-26'
version:
  number: 27
  created_at: '2026-07-26'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T19:59:40Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_865c3c24f251
importance: 10
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/tests/cases/test_workspace_resource_rename.py
  - ${code_root}/management_portal/resources/workspace_store.py
  - ${code_root}/management_portal/resources/providers/workspace.py
  - ${code_root}/management_portal/http_resources.py
  - ${code_root}/management_portal/resources/service.py
  - ${code_root}/management_portal/resources/runtime.py
  - ${code_root}/management_portal/resources/registration.py
  - ${code_root}/management_portal/resources/contracts.py
  - ${code_root}/management_portal/resources/access.py
  - ${code_root}/management_portal/resources/query.py
  - ${code_root}/management_portal/resources/index_runtime.py
  - ${code_root}/management_portal/resources/providers
  - ${code_root}/management_portal/resource_index_backend.py
  - ${code_root}/management_portal/resource_views.py
  - ${code_root}/management_portal/knowledge_item_chat_backend.py
  - ${code_root}/knowledge_board_lan/knowledge_backend.py
  - ${code_root}/management_portal/resources/mutation.py
  - ${code_root}/management_portal/resources/action.py
  - ${code_root}/management_portal/resources/providers/__init__.py
  - ${code_root}/management_portal/resources/server.py
  - ${code_root}/management_portal/resources/transport.py
  - ${code_root}/management_portal/resources/rpc_auth.py
  - ${code_root}/tests/cases/test_resource_service.py
  - ${code_root}/tests/cases/test_resource_service_process.py
  - ${code_root}/tests/cases/test_resource_portal_routes.py
  related:
  - decision_4648f7fa3295
  - decision_36f46b395d8e
  - decision_cf27d39e39cd
  - decision_8dc6b3d03ca7
  - decision_4a9ba5dfada6
  - decision_34158fdd4078
  - decision_3ece26686464
  - decision_8273271b67b1
  - decision_b820faf42218
  - decision_600ae27b0538
  supersedes: []
  derived_from:
  - decision_8dc6b3d03ca7
---
# ResourceService 调用协议：查询、读取、变更与动作

Portal 页面、Resident 和 Portal 的其他服务都通过 ResourceService 查询和操作资源（`decision_cf27d39e39cd`）。本 Design 是它的调用协议：逐个说明调用方可以使用哪些方法，每个方法接收什么参数、返回什么结果、在什么情况下失败，以及跨进程调用时请求和错误怎样传输。ResourceService 内部由哪些组件组成、怎样运行，由 `decision_cf27d39e39cd` 说明。

## 基本概念

| 名称 | 含义 |
| --- | --- |
| 访问者（actor） | 一次请求代表的 Portal 用户或 Resident，决定这次请求能发现、读取和管理哪些资源 |
| 资源 ID | 资源稳定不变的根身份，例如 `decision_1a2b3c4d5e6f`，不随标题和路径变化（`decision_4a9ba5dfada6`） |
| 资源地址 | 资源 ID，或资源 ID 加内部路径，例如 `workspace_1a2b3c4d5e6f/file/report.md` |
| 请求内容（payload） | 登记、修改或动作提交的 JSON 对象，字段由资源类型的领域定义 |
| 领域提供器 | 某种资源类型接入 ResourceService 的实现，负责该类型的解析、正文读取、登记、修改、撤销登记或动作 |
| 资源索引 | 各领域资源摘要组成的查询表，可以重新生成，不保存正文和业务最终状态（`decision_8dc6b3d03ca7`） |
| 权限系统 | 按当前权限模板生成的 P、A、R 三棵树和权限表，决定访问者对资源能否发现、读取和管理（decision_b820faf42218） |
| 索引确认 | 一次领域修改之后，资源索引已经反映这次修改。索引可能稍晚于已经成功的领域修改 |

本篇把登记、修改和撤销登记合称**变更**；把资源类型自己定义的命令称为**动作**，例如 Paper 的参考文献提取、Project 的摘要保存。

## 四种调用入口

| 入口 | 谁使用 | 访问者从哪里来 | 说明 |
| --- | --- | --- | --- |
| Python 方法 | ResourceService 进程内的代码，以及 `local` 模式下的测试和脚本 | 调用时传入的 `actor` | 直接调用 `ResourceService` 的同名方法 |
| Resource RPC | Portal Web、SocietyService、ExecutionService 等其他进程 | 请求中的签名认证信息 | 通过 Unix socket 调用同名方法，方法名为 `resource.<方法>` |
| Portal HTTP 读取接口 | 浏览器页面 | 登录 Session | `GET /api/resources/resolve`、`reference`、`content`、`capabilities`，以及 `/api/resource-index/query`、`suggest`、`status`、`resources/<ID>` |
| HATG ISA 资源指令 | Resident，以及页面上的修改操作 | 指令入口确认的调用者（`decision_898b8911a2ff`） | `SearchResources`、`ReadResource`、`RegisterResource`、`RunResourceAction` 等，由 ResourceService 执行（`decision_ad3e21094f14`） |

四种入口的传输格式和大小上限不同，同一个业务操作的参数含义、授权结果、返回内容和错误类别保持一致。

## 通用规则

- **访问者只来自认证入口。** RPC 请求的参数中不能再写 `actor`；服务端从签名认证信息中取得访问者。
- **只接受根 ID 的方法拒绝内部地址。** 同时收到 `resource_type` 时，类型必须与 ID 一致。
- **变更的请求内容必须是 JSON 对象。** 动作没有提供请求内容时使用空对象；动作名只接受小写的点分格式，例如 `summary.save`。
- **领域提供器返回的记录保持原资源 ID。** 返回对象不能再用 `id` 或其他 `*_id` 字段表达第二个根身份。
- **认证、操作授权与领域约束分别履行职责。** ISA 按正式权限声明进入当前权限系统；ResourceService 门面要求认证的 User/Resident，领域继续校验存在性、版本和业务限制。不存在由 `test_lab` 等旧系统能力绕过当前方案的扩展；签名或 local 调用方式本身不授予业务权限（decision_b820faf42218）。
- **`capabilities` 描述类型安装了哪些能力，不授予权限。** 每次修改和动作都重新检查当前权限和资源状态。

## 身份、健康与能力

### 身份和地址的纯函数

这些方法只解释传入的值，不读取领域状态，也不经过 RPC：

- `new_id(resource_type, existing_ids)` 生成一个不在给定集合中的候选资源 ID。
- `validate_id(...)` 校验 ID 并返回解析出的身份；`is_valid_id(...)` 做同样的校验，只返回是否有效。
- `parse_address(address)` 拆分资源地址。`portal_url_for_address(address)` 返回该地址在浏览器中的打开地址；类型没有页面入口或类型与 ID 不一致时返回空值（规则见 `decision_4a9ba5dfada6`）。

### `status()` / `resource.status`

**读取 ResourceService 的健康状态，不要求业务访问者；部署服务的 RPC 仍须认证。**

结果包含服务协议版本 `tfi.resource-service.v3`、当前构建 ID、实例名、预热状态（`ready`、`warming`、`warm_error`）、资源索引的健康摘要（记录数、运行中与等待中的适配器、每个适配器的最近检查、最近成功和错误）和通知总线的健康信息。客户端可以设置等待时间；socket 不存在、连接失败或超时时抛出 `ResourceServiceUnavailable`。

### `capabilities(resource_type)` / `resource.capabilities`

**读取一种资源类型安装了哪些能力。**

- 结果字段：`indexed`（资源索引是否收录）、`resolvable`、`readable`（能否读取正文）、`registerable`、`updatable`、`content_updatable`（能否修改正文）、`unregisterable` 和 `actions`（已安装的动作名列表）。
- 类型未登记时是输入错误。

## 发现、解析与读取

### `query(**条件)` / `resource.query`

**从资源索引中查询当前访问者可以发现的资源摘要。**

| 参数 | 含义 | 默认值与范围 |
| --- | --- | --- |
| `q` | 关键词，或一个完整的资源地址 | 可选 |
| `resource_types` | 类型集合 | 可选 |
| `facets` | 记录必须同时具有的全部分类 | 可选 |
| `projects` | 与这些 Project 有交集 | 可选 |
| `statuses` | 精确状态 | 未提供时不返回 `deleted` 的记录 |
| `attribute_filters` | 属性值与给定集合有交集 | 可选 |
| `include_attributes` | 是否返回完整的 `attributes` | 默认 `false` |
| `limit`、`offset` | 分页 | `limit` 默认 50，范围 1–5000；`offset` 不小于 0 |

- `q` 能解析为资源地址时，先按当前访问者解析这个地址；解析成功后跳过关键词评分，其他条件照常生效。
- 查询等待相关适配器第一次生成完成；从未成功的适配器会立即触发一次生成，仍未完成时抛出 `ResourceServiceUnavailable`。
- 结果包含资源列表、`total`、`offset`、实际使用的 `limit` 和资源索引的更新时间。
- 查询的描述补齐、当前事实、动态筛选和结果隔离由 decision_8dc6b3d03ca7 定义；本方法只返回符合本次条件与访问边界的摘要，不返回权限快照。

### `suggest(actor, q, limit, resource_type)` / `resource.suggest`

**为全局搜索框返回候选资源。** 非空 `q` 只匹配资源 ID 和标题；`resource_type` 可选，在评分和权限计算之前缩小候选范围。结果 `resources` 中每项是当前访问者可以发现的资源摘要和匹配分数；空 `q` 返回空列表。结果没有总数和分页。候选处理与结果隔离遵守 decision_8dc6b3d03ca7 的查询边界。浏览器通过 `GET /api/resource-index/suggest` 调用。

### `get(resource_id, actor)` / `resource.get`

**按根 ID 读取资源摘要，协作类型优先使用当前描述记录。**

- Resident、Lineage、Workspace 有协作描述时直接生成摘要，不等待索引；非 Workspace 协作类型再补齐当前领域事实。每次调用应用当前身份与私有性约束，返回记录不改写索引或推进适配器进度。
- Workspace 描述缺失时返回不存在，不能从旧索引恢复目录路径。Resident/Lineage 的历史记录在尚无描述时保留原索引读取契约；非协作类型也使用所属适配器索引。索引路径只等待相关适配器就绪，不要求全量刷新。
- ID 与类型不一致为输入错误；不存在或私有对象不可见时可返回 `KeyError`，身份读取拒绝为 `PermissionError`，具体错误映射见下文。
- `get` 可返回已删除摘要供诊断；这不使对象重新活动。`resolve`、`reference` 和精确地址搜索仍按领域存在性规则拒绝已删除对象。

### `resolve(address, actor)` / `resource.resolve`

**把根地址或内部地址解析成领域对象，并检查读取权限。** ResourceService 按根类型选择解析器，统一结果格式后检查读取权限，成功时返回解析结果（格式 `tfi.resource_resolution.v2`，字段见 `decision_4a9ba5dfada6`）。没有解析器、对象不存在、根身份不一致或结果格式错误时明确失败。

### `reference(address, actor)` / `resource.reference`

**生成适合保存或放进上下文的简短引用。** 解析和授权成功后返回规范地址、根 ID、类型和标题，可以附带摘要、状态、版本、分类、打开地址、编辑提示和下载提示。地址指向内部对象时，优先显示内部对象的标题、摘要或文件名，同时保留根 ID 和完整地址。

### `read_content(address, actor, max_bytes)` / `resource.read_content`

**读取资源正文。** 解析与授权后调用该类型的正文读取函数，返回原始字节、`content_type` 和解析结果；类型没有正文读取函数时返回“不支持”。浏览器下载使用解析器给出的文件名。

- RPC 的 `max_bytes` 默认 4 MiB，限制在 1 字节到 200 MiB 之间。
- `ReadResource` 指令默认 `max_bytes` 为 4 MiB；正文更大时返回只限本人、有时效的下载地址（`decision_ad3e21094f14`）。

### `read_content_range(address, actor, start, end)` / `resource.read_content_range`

**读取正文的一个字节区间。** 领域解析器提供了区间读取函数时可用，用于大文件分段下载和浏览器的区间请求。

## 协作资源

### 协作资源的同步

Resident、Lineage 和工作区的关系由协作领域（SocietyService）保存。资源的读取权限由权限系统决定（decision_b820faf42218），ResourceService 的资源记录不保存 ACL 或可见范围；同步进来的协作描述同时更新对象在权限树上的位置。SocietyService 通过以下受信方法把协作对象同步到 ResourceService：

- `sync_collaboration_resource(s)` / `resource.collaboration.sync` 成组提交协作描述记录，每组只含 `descriptor`。资源 ID 在一批中不能重复；任一组无效时，整批在写入之前拒绝。已删除的对象只合并生命周期字段。内容相同的组不改写记录，写入成功后才通知资源索引。只有 SocietyService 的内部身份可以调用。
- `workspace_id_for_path(path, preferred_id)` / `resource.collaboration.workspace_id_for_path` 为规范路径返回工作区 ID：已绑定的路径返回现有 ID；尚未绑定时使用 `preferred_id` 或分配新 ID。两次绑定几乎同时发生时，同一路径可能登记出多个工作区 ID：`preferred_id` 是其中之一时返回它，否则返回按字典序最小的那个，此后的绑定都落到同一个 ID 上。

## 登记、修改与撤销登记

### `register(...)` / `resource.register`

**按资源类型登记一个新资源。**

- 参数：`resource_type`、`payload`，可选的 `automatic`、`wait_for_index`、`index_timeout`（默认 15 秒）。
- `payload` 必须是对象，不能包含内部字段 `registration_key`。
- 权限：ISA 的 RegisterResource 按拟放置位置要求 contributor；CreateProject 等专用指令依各自正式声明授权，不使用旧的 `project.create` 系统能力。普通门面登记校验认证身份；`automatic=true` 只供受信的自动登记任务使用，跳过该身份检查，不是对外授权入口（decision_36f46b395d8e）。
- 由服务分配 ID 的类型（知识条目、Experiment、工作台）先由登记账本保留 ID，其他类型由领域自己生成 ID（`decision_cf27d39e39cd`）。
- 领域返回的记录必须包含类型匹配的规范 ID。核对通过后，ResourceService 为新资源建立授权记录，调用所属适配器同步生成并持久化这一条索引摘要，完成后才返回成功；无需等待串行刷新队列或扫描全量资源。
- 结果中的 `created` 只说明领域文件是否新建。

### `update(...)` / `resource.update`

**修改一个已存在资源的正文或元数据。**

- 参数：`resource_id`、`payload`，可选的 `resource_type`（必须与 ID 一致）、`wait_for_index`、`index_timeout`。
- 权限：UpdateResource 按正式声明要求目标资源的 contributor；专用 Project、Resident 或 Lineage 指令依其各自声明授权。门面校验认证身份，领域保留业务约束，不存在通用的 Resident 全局管理旁路。
- 领域返回的记录不能改变资源 ID。成功后发送 `updated` 变化通知。
- 领域抛出 `ResourceVersionConflict` 时，错误中保留调用方提交的版本 `expected_version` 和当前版本 `current_version`，调用方据此重新读取、合并后再提交。

Workspace 名称更新只接受非空 `title` 与必填 `expected_version`，令牌原样取自 `ResolveResource.result.resource.version`；原子鉴权、行锁 CAS、返回版本与错误合同统一见 decision_8838269736d3「名称更新与版本冲突」。类型是否可更新从 GetResourceType 的顶层 `updatable` 读取。

### `unregister(...)` / `resource.unregister`

**结束一个资源的登记。**

- 权限：UnregisterResource 的正式声明要求目标资源的 admin；领域删除适配器仍履行自己的业务约束。
- 领域决定对象是软删除、物理删除还是只取消登记。
- 领域失败时，登记账本继续保留这个 ID；成功后才释放，并发送 `deleted` 变化通知。

### 等待索引确认

- **登记成功即确认单条索引投影。** `register` 和 `RegisterResource` 无论是否给出 `wait_for_index`，都会在返回前同步持久化新资源摘要；这个参数不使登记进入后台刷新等待。`CreateProject` 同样走登记流程。
- **`wait_for_index` 对修改和撤销登记等待索引反映这次变更。** 领域修改在等待之前已经提交。HATG 的 `UpdateResource` 与 `UnregisterResource` 请求索引确认，最多等待 20 秒。
- **修改或撤销登记没有确认时，结果中带 `index_projection`。** 等待超时或刷新失败时 `reason` 为 `refresh_incomplete`，没有对应的适配器时为 `no_targets`，通知发送失败时为 `notification_failed`；同时给出 `status: unconfirmed`、`wait_requested` 和 `targets`。登记投影失败不返回成功结果，而是报告带 `domain_committed: true` 的提交后错误。
- **调用方遇到提交后错误时保留资源 ID 并重新读取。** 领域事实可能已提交，不能盲目再次创建对象；成功的登记结果可立即用于查询、解析及读取。

### 正文修改记录

Resident 在一次执行中修改资源正文时，ResourceService 为每次实际写入保存一条修改记录，供这次执行的对话和审查查看（`decision_f560803947c6`）。

- **执行 ID 随请求传入。** Harness 工具入口直接带上当前执行的 ID；命令行客户端读取 `PORTAL_EXECUTION_ID`，只读取规范环境名，并通过既有请求头 `X-Portal-Execution-ID` 发送。执行 ID 只用来归属修改记录，不增加任何权限。
- **写入前核对执行。** ResourceService 向 ExecutionService 查询这个执行：执行属于当前访问者并且仍在运行时，才记录这次修改。
- **记录内容。** 每条记录的格式为 `tfi.resource-content-change.v1`，包含 `change_id`、`resource_id`、`address`、`path`、`actor`、`execution_id`、`created_at`，以及修改前后的快照；快照保存原文、内容的 sha256 和领域版本。记录在领域修改锁内、写入成功后立即保存，按实际写入顺序排列。
- **已接入的写入。** 知识条目正文与附件、Skill 文本、Experiment 文档。正文没有变化的写入不生成记录。登记、撤销登记、没有执行 ID 的写入和元数据动作不生成记录。
- **记录失败不影响写入结果。** 执行不属于当前访问者、执行已经结束，或正文已写入而记录保存失败时，写入照常成功，结果带 `change_recording: {status: unconfirmed, reason}`，`reason` 分别为 `execution_actor_mismatch`、`execution_not_running`、`receipt_failed`。文件写入和记录保存不在同一个事务中，崩溃造成的缺失不会事后补造。
- **读取记录。** `execution_content_changes(execution_id, actor)` / `resource.execution_content_changes` 返回这位访问者在该执行中产生的记录；知道别人的执行 ID 也读不到别人的记录。SocietyService 在执行结束后用它生成对话中的资源修改片段。

## 领域动作

### `action(...)` / `resource.action`

**执行资源类型声明的一个动作。**

- 参数：`resource_id`、`action`、`payload`（默认空对象），可选的 `resource_type`。
- 按资源类型和动作名找到已安装处理函数。RunResourceAction 通过正式动作目录校验参数，并按指令声明要求目标资源 contributor；领域继续履行自身约束。通用动作注册表不再维护一套 viewer/contributor/admin 的旧 operation 映射，也不能以动作后缀推导授权。
- 注册表校验类型、动作名和可调用处理函数，重复绑定默认拒绝，装载失败原子撤回；ISA 声明与有效能力的关系见 decision_355ed742bca5。

- 处理函数必须返回对象，ResourceService 原样返回，不包装成变更记录。动作的请求字段、幂等规则和结果结构由领域定义；请求内容中的 `timeout_seconds` 只延长客户端的等待时间。
- 动作不自动发送变化通知，会改变状态的动作由所属领域通知自己的索引。

## 资源索引控制

以下方法供受信的 Portal 服务使用：

- **`refresh(adapters, path, wait, timeout)`** 排队刷新资源索引：给出 `adapters` 时只刷新这些适配器，给出 `path` 时刷新负责该路径的适配器，两者都没有时刷新全部；找不到负责该路径的适配器时抛出 `ValueError`。`wait=true` 时等待刷新完成，超时不会取消已经排队的刷新。
- **`projection_state(adapter)`** 返回一个适配器的刷新进度，包括版本代次、最近检查和最近成功时间；适配器不存在时报错。
- **`notify_source_changed(path, resource_id, resource_type, kind, wait, timeout)`** 发送领域变化通知，`kind` 为 `created`、`updated`、`deleted`、`moved` 或 `unknown`。通知或等待失败不会撤回之前的领域修改。

## 一次变更怎样完成

```text
认证入口确认访问者
  → ResourceService 核对身份、类型与权限
  → 领域提供器提交领域修改
  → ResourceService 核对返回的根身份，同步授权记录
  → 登记：所属适配器同步投影单条摘要并持久化后返回
  → 修改或撤销登记：发送变化通知，按需等待后台索引确认
```

领域修改先于索引确认，因为正文和业务状态属于领域，索引只是可以重建的摘要。登记在同一请求内投影单条索引记录；修改或撤销登记的通知失败、等待超时，调用方报告“领域已提交、索引未确认”，后台刷新和周期核对补上索引。

领域提交之后的步骤（核对返回记录、释放登记账本中的 ID、同步授权记录、登记的单条索引投影、其他变更的变化通知）抛出异常时，`register`、`update`、`unregister` 统一抛出 `ResourceOperationError("resource.mutation_post_commit_failed", ..., 500)`。`details` 包含 `domain_committed: true`、`resource_id`、`resource_type`、`mutation`（`create`、`update`、`delete`）、失败步骤 `stage`（`mutation_result`、`registration_cleanup`、`authorization_sync`、`index_projection`、`index_notification`）和原因 `cause`。通过指令调用时结果的 `commit_state` 为 `committed`，不可原样重试；调用方重新读取资源确认当前状态。修改或撤销登记等待索引超时仍以 `index_projection` 返回已提交结果。

## Resource RPC 协议

### 请求与响应

请求与响应封套、请求 ID 匹配和跨进程错误表示遵循 decision_96a887d83e8a。本篇只定义 ResourceService 的正文传输、等待时间及领域错误映射。
- **读取正文**（`resource.read_content`、`resource.read_content_range`）先返回一行描述（长度、内容类型和解析结果），再发送该长度的字节。字节不足、长度非法或连接中断时抛出 `ResourceServiceUnavailable`，不把部分字节当作完整内容。
- 同一个 socket 还提供 HATG 指令方法 `instruction.execute`（`decision_96a887d83e8a`）和授权系统的方法。

### 签名认证

所有部署服务请求的共享认证、密钥安全、nonce 与重放规则，以 decision_96a887d83e8a 为唯一权威。ResourceService 的带访问者方法从签名取得 actor，拒绝在 params 重报；没有业务访问者的控制请求也须由受信服务签名。

签名格式及精确校验也遵循上述 RPC 规范，ResourceService 不另定义一套认证格式。

### 客户端等待时间

客户端对普通请求默认等待 10 秒；需要等待资源索引首次生成的读取至少等待 35 秒；读取正文至少等待 60 秒。`wait_for_index`、带等待的 `refresh` 和动作的 `timeout_seconds` 会在请求的等待时间上再加 5 秒余量。

等待时间只决定客户端何时停止等待，不会取消服务端的执行。修改请求超时或断线后，调用方重新读取资源的当前状态，不能据此认为修改没有发生。

## 错误

### 错误码

ResourceService 的错误模块统一负责错误的编码、跨进程还原和 HTTP 状态。领域提供器、Python 方法、RPC 和 HTTP 接口共用下表：

| code | Python 异常 | HTTP | details |
| --- | --- | --- | --- |
| `git_checkout_required` | `GitCheckoutRequired` | 409 | 规范化的绝对路径 `path`、`can_initialize_git: true` |
| `resource_version_conflict` | `ResourceVersionConflict` | 409 | `expected_version`、`current_version` |
| `file_exists` | `FileExistsError` | 409 | 原异常带有系统错误信息时，保留 `errno`、`strerror`、`filename`、`filename2` |
| `file_not_found` | `FileNotFoundError` | 404 | 同上 |
| `resource_not_found` | `KeyError` | 404 | 空对象 |
| `permission_denied` | `PermissionError` | 403 | 系统异常附带路径时保留其错误信息 |
| `invalid_argument` | `ValueError` | 400 | 空对象 |
| `resource_service_unavailable` | `ResourceServiceUnavailable` | 503 | 空对象 |
| `resource_operation_error` | `ResourceOperationError` | 由错误自带的 `http_status` 决定 | 领域的 `code`、`http_status` 和领域 `details` |
| `internal_error` | `RuntimeError` 及其他异常 | 500 | 空对象 |

- **领域错误使用 `ResourceOperationError`。** 领域可以直接抛出 `ResourceOperationError(code, message, status, details=...)`，或让自己的异常继承它。领域决定拒绝原因和领域错误码，错误模块负责传输和 HTTP 状态；新增领域错误不需要修改错误模块。跨进程时它使用固定的 `resource_operation_error` 格式，`details` 中保存非空的领域 `code`、400–599 的整数 `http_status` 和对象形式的领域 `details`。客户端只还原 `ResourceOperationError` 本身，字段缺失或非法时还原为 `RuntimeError`。
- **跨进程还原只认公开的错误码。** 未知错误码或缺少必要字段的错误一律还原为 `RuntimeError`，也不采用其中的重试提示。socket 连接失败、超时、非法 JSON、响应 ID 不匹配和正文字节不足统一转换为 `ResourceServiceUnavailable`。领域异常在网络异常处理范围之外还原，因此远端的 `FileExistsError` 等文件错误保持原有含义。

### HTTP 接口中的错误

HTTP 接口返回上表中的状态码和 `{error, code, details}`：

- `git_checkout_required` 和 `resource_version_conflict` 同时把 `details` 中的字段放到顶层，例如 `path`、`can_initialize_git`、`expected_version`、`current_version`。
- 服务不可用时另外返回可读的 `detail` 和 `retryable: true`。这个提示说明可以稍后再读，不代表可以自动重发修改请求。其他错误不附带可重试标记。
- 工作台的专用 HTTP 接口复用同一套映射，例如 `projection.conflict` 返回 409，`projection.not_found` 返回 404。

### 指令中的错误

通过 HATG 资源指令调用时，ResourceService 的错误转换为指令错误码（`decision_898b8911a2ff`）：

| ResourceService 错误 | 指令错误码 |
| --- | --- |
| `permission_denied` | `instruction.forbidden` |
| `resource_not_found`、`file_not_found` | `instruction.not_found` |
| `file_exists` | `instruction.conflict` |
| `invalid_argument` | `instruction.invalid_arguments` |
| `git_checkout_required` | `instruction.conflict`，`details` 中含 `reason: git_checkout_required`、`path`、`can_initialize_git` |
| `resource_version_conflict` | `instruction.version_conflict`，`details` 中含 `expected_version` 与 `current_version` |
| 服务不可用 | `instruction.owner_unavailable` |

Repo 登记遇到目录还不是 Git 仓库时，调用方可以提示用户选择初始化；在请求中加入 `initialize_git: true` 后重试才会初始化并登记，第一次返回的冲突没有修改目录（`decision_b0997bc3a922`）。

## 状态归属

| 状态 | 保存者 | ResourceService 的角色 |
| --- | --- | --- |
| 资源正文与业务状态 | 领域 | 调用领域并核对结果 |
| 服务分配的登记 ID | 登记账本 | 保留、使用和释放 |
| 查询摘要与适配器进度 | 资源索引 | 查询、刷新和等待确认 |
| 权限树、权限表与临时凭证 | 权限系统（schema `permission`） | 登记、修改、删除时同步对象位置；经权限端口检查访问者的能力 |
| 正文修改记录 | 修改记录表 | 在写入时保存，按执行读取 |
| 登录 Session、Resident 令牌 | 各认证入口 | 接收认证入口确认的访问者 |

新增一种资源类型时，领域登记类型、解析器或索引适配器、变更提供器和动作提供器；通用的读取和修改直接使用 ResourceService，不在每个处理函数中另写权限判断。要让指令对这种类型的资源要求等级，模板的 R 树要为它声明位置（所属对象类型由 `permission_system/scheme/vocabulary.py` 给出，未声明的类型挂在默认节点下），并在指令映射中写明哪个参数要求哪个等级（decision_b820faf42218、decision_600ae27b0538）。
