---
kb_id: decision_692a484e9bea
type: decision
project: AI员工
created_at: '2026-09-01'
importance: 8
ai_generated: true
links:
  source:
  - ${code_root}/management_portal/telepus_runtime.py
  - ${code_root}/management_portal/telepus_instructions.py
  - ${code_root}/management_portal/telepus_web_isa.py
  - ${code_root}/tests/cases/test_watchboard_applications.py
  - ${share_root}/Projects/AI员工/knowledge_base/agents/lineage_c625a4bee56b/script/src/portal_facade.py
  - ${share_root}/Projects/AI员工/knowledge_base/agents/lineage_c625a4bee56b/script/src/telegram_adapter.py
  - ${share_root}/Projects/AI员工/knowledge_base/agents/lineage_c625a4bee56b/script/src/portal_resident_instructions.py
  - ${code_root}/management_portal/isa_client.py
  - ${code_root}/management_portal/http_agent_workbench.py
  - ${code_root}/management_portal/static/watchboard_applications/telepus.js
  - ${code_root}/management_portal/watchboards/service.py
  - ${code_root}/management_portal/watchboards/telepus.py
  - ${code_root}/management_portal/telepus_contacts_store.py
  - ${code_root}/management_portal/society_service/resident_domain.py
  - ${code_root}/management_portal/static/lineage_workbench_watchboard.js
  - ${code_root}/management_portal/serve_management_portal.py
  - ${code_root}/tests/cases/test_resident_forward_contacts.py
  - ${code_root}/tests/cases/test_resident_watchboard.py
  related:
  - decision_4527448ad07f
  - decision_898b8911a2ff
  - decision_0571f3741e23
  - decision_bd5217d08c09
  - decision_96a887d83e8a
  - decision_edc470e09f5a
  - decision_0f57656a2acd
  - decision_c524c775e0be
  - decision_34158fdd4078
  - decision_b820faf42218
  supersedes: []
  derived_from: []
version:
  number: 19
  created_at: '2026-09-01'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T21:32:41Z'
  updated_by:
    user: ''
    agent: resident_511e229bc8c3
metaness: 2
---
# Telepus 外部消息、联系人映射与 Resident 投递

Telepus 是 Portal 的 Telegram 入口。用户在 Telegram 中给 Telepus 机器人发消息，Telepus 找到这个 Telegram 用户对应的 Portal 用户，以该用户的身份把消息发给选定的 Resident，再把 Resident 的回复发回 Telegram。Telepus 本身是一个 Resident，负责 Telegram 通信的程序放在它的 Lineage Folder 中，由独立基础服务运行；Watchboard 为它提供状态和联系人界面。

本篇说明 Telepus 的组成、服务开关与运行状态、怎样在 Portal 中管理联系人（Telegram 用户与 Portal 用户的对应），以及消息怎样进入 Resident。消息进入 Resident 之后的排队与执行见 decision_edc470e09f5a。

## 组成

| 部分 | 说明 |
| --- | --- |
| Telepus Resident | 固定 ID `resident_7fdebb465cdf`，是 Lineage `lineage_c625a4bee56b` 的 Leader。它使用普通 Resident 身份承担入口维护与协调。 |
| Telegram 程序 | Telepus 的桥接程序，位于 `<Telepus Lineage Folder>/script`（当前为 `${share_root}/Agents/lineage_c625a4bee56b/script`），由用户级 systemd 服务 `telepus-telegram-router` 常驻运行。它从 Telegram 拉取新消息、解析命令、调用 Portal、发送回复。 |
| 联系人 | 一个 Telegram 数字用户 ID 与一个 Portal 用户的对应。 |
| 联系人表 | PostgreSQL 表 `telepus.telegram_contacts`，每个联系人一行：`telegram_user_id`、`name`、`portal_actor`。 |
| Contacts 区域 | Portal 在 Telepus 的 Watchboard 中提供的联系人管理界面。 |

Watchboard 是 Resident 的扩展面板：Resident 可以安装一套脚本，Portal 在工作台中显示脚本的状态、配置和命令，并可以为特定 Resident 附加专用界面（Watchboard 应用）。Telepus 的联系人管理就是这样一个应用（Watchboard 见 decision_0571f3741e23）。Telepus 的展示清单不提供通用运行命令，消息桥接由 router、portal-refresh、breakglass-worker 三个 systemd 服务承担。通用 Watchboard 的 Lineage/RunScript 安装流程不迁移或停止它们；令牌、桥接数据库、联系人代表身份和故障恢复由基础服务边界另行定义。

```text
Telegram 用户
   │ 消息
   ▼
Telegram 程序（独立桥接服务）
   │ 1. 按联系人表找到对应的 Portal 用户
   │ 2. 选择目标 Resident
   │ 3. 以该 Portal 用户身份投递消息
   ▼
目标 Resident（普通消息排队与执行）
   │ 回复
   ▼
Telegram 程序 ──发送回复──> Telegram 用户

Portal 用户 ──Contacts 区域──> 联系人表 <──读取── Telegram 程序
```

## Telepus 服务开关与运行状态

### 管理范围

Telepus 的服务开关管理本站 Linux 用户级 systemd 中的三个固定服务：`telepus-breakglass-worker.service`、`telepus-portal-refresh.service`、`telepus-telegram-router.service`。它独立于 Resident 的接收开关、联系人绑定和 Watchboard 展示开关；服务状态直接读取 systemd，不用页面缓存或另存的布尔值代替实际状态。

开启表示启用并启动这三个服务；关闭表示停止并禁用其中已安装的服务。关闭保留账号、令牌、联系人映射、桥接数据库和已有消息数据，也不取消已经投递到 Resident 的工作。开关不负责安装服务或依赖，不迁移运行目录，不改写联系人代表身份。

### 正式指令与权限

| 指令 | 参数 | 返回 | 行为 |
| --- | --- | --- | --- |
| `GetTelepusStatus` | 无 | `{status: object}` | 只读查询三个服务的启用、运行和安装状态。 |
| `SetTelepusEnabled` | 必填 `enabled: boolean` | `{status: object}` | `true` 启用并启动，`false` 停止并禁用；返回操作后的真实状态。 |

两条指令属于运行环境分类，是无对象 target 的全局服务指令。它们使用固定判定槽，按当前权限方案配置判定，不属于免检接口；处理接口还要求调用者为 Portal 管理员，否则返回 `instruction.forbidden`。指令分类、命名和参数总表见 decision_4527448ad07f 与 decision_898b8911a2ff，本篇约定 Telepus 的运行行为。

### 状态含义

`status` 包含以下字段：

| 字段 | 含义 |
| --- | --- |
| `available` | 当前宿主能够查询 Linux systemd 用户服务。查询成功但部分服务未安装时仍为 `true`。 |
| `units` | 每个固定服务的 `name`、`loaded`、`active`、`enabled`。分别对应已加载、正在运行和已启用；`enabled-runtime` 也计为已启用。 |
| `missing` | 未加载的固定服务名称列表。 |
| `enabled` | 任一服务已启用或正在运行即为 `true`，用于表示尚有服务处于开启状态；不代表整体健康。 |
| `running` | 三个服务均正在运行。 |
| `complete` | 三个服务均已安装、已启用且正在运行。只有此状态满足完整开启要求。 |
| `error` | 查询失败或平台能力不足的诊断；查询成功时为空字符串。 |

非 Linux 平台，或 `systemctl --user show` 不可执行、超时、返回失败时，`available=false`、`units=[]`、`missing` 列出全部固定服务，三个聚合布尔值均为 `false`，并给出 `error`。这些值表示状态不可读取，不能据此认定服务已经停止。查询的超时上限为 15 秒。

### 控制过程与失败边界

每次设置先读取状态。无法查询时拒绝操作，返回 `telepus.service_unavailable`（HTTP 503）；请求开启但有服务缺失时返回 `telepus.service_missing`（HTTP 409，`commit_state=not_committed`），不执行控制命令，也不自动安装。

开启对全部三个固定服务执行 `systemctl --user enable --now`；关闭只对已加载的服务执行 `systemctl --user disable --now`，因而部分服务缺失不妨碍关闭剩余服务。关闭时若没有已加载服务，则直接返回查询结果。控制命令的超时上限为 45 秒。

控制命令不可执行、超时或返回失败时，返回 `telepus.service_control_failed`（HTTP 503）。命令返回成功后仍须重新查询：开启必须达到 `complete=true`，关闭必须达到 `enabled=false`；查询不可用或未达到请求状态时返回 `telepus.service_control_incomplete`（HTTP 503），不以命令成功代替状态确认。

多个服务的控制不是原子事务，失败也没有自动回滚保证；服务可能已部分变化。调用方应展示失败诊断并重新读取实际状态，按诊断修复环境或重试，不能把失败当作“状态未变”，也不能只按请求值显示开启或关闭。当前开关仅支持上述 Linux systemd 用户服务，不提供其他平台的等价控制实现。

## 设计点

### 先登记联系人才能使用

- **只有登记过的 Telegram 用户可以使用 Telepus。** 程序按发信人的 Telegram 数字 ID 查找联系人；没有登记时回复“未授权使用 Telepus。”，告诉对方自己的 Telegram 数字 ID，并请对方登录 Portal 在 Telepus → Contacts 绑定本人，或请管理员通过指令代绑。
- **一个 Telegram ID 只对应一个 Portal 用户，一个 Portal 用户也只对应一个 Telegram ID。** 避免同一个人出现冲突的对应关系。
- **消息以对应的 Portal 用户身份投递。** Telepus 不用自己的权限代发：目标 Resident 是否可见、能否接收消息，都按这个 Portal 用户检查。

### 联系人表由 Portal 写入、Telegram 程序读取

- **Portal 页面、代绑和代解绑指令写入，Telegram 程序只读。** 表放在 Telegram 程序自己的 schema（`TELEPUS_DATABASE_SCHEMA`，即 `telepus`）中，由 Portal 建表并独占写入；Portal 不读写这个 schema 中的其他表。Telegram 程序收到每条消息时按发信人的 Telegram 数字 ID 查一次表，绑定变化对下一条消息立即生效。
- **每次修改是一个数据库事务。** 保存、替换和删除都持有同一把 `contacts` 锁，先读出当前联系人、检查唯一性和归属，再写入；冲突时事务不提交，原联系人不变。
- **删除 Portal 账号时一并删除它的联系人。** Telepus 在功能登记中提供 `user.deleted` 接入点（decision_15aab18c0ab3）：账号删除流程经它删除所有对应该用户名（不区分大小写）的联系人，并在 `deleted_data.telepus` 中报告（decision_30528aff7035）。

### Telegram 程序只为仍然绑定的用户刷新回复

聊天会话记住设置目标时的 Portal 用户。后台刷新服务（`telepus-portal-refresh`）定期以这个用户身份拉取 Resident 的新回复。刷新前，程序重新按联系人表确认该 Telegram 用户仍然对应同一个 Portal 用户；联系人被删除或改绑到其他用户时，这个会话不再刷新，结果记为 `unbound`，不计为错误，也不会以已删除或其他用户的身份读取 Portal。

### 联系人管理只属于 Telepus

- **只有固定 ID 的 Telepus Resident 有 Contacts 区域。** 其他 Resident 的 Watchboard 不显示它，从 Telepus Fork 出的 Resident 也不会得到它。

## 在页面上管理联系人

Contacts 区域位于 Telepus 的 Watchboard 中：

- 标题“Contacts”右侧的加号按钮打开“Add user”对话框。
- 每个联系人一行，显示名称和“Telegram <遮蔽的 ID>”，右侧有编辑和删除按钮。遮蔽的 ID 只保留开头 2 位和末尾 2 位，例如 `12...56`。
- 没有联系人时显示“还没有已注册 Telegram 用户。”。
- 任何登录用户都可以用加号绑定本人的 Telegram。编辑和删除按钮只对当前用户的联系人行可用，其他用户的联系人行只读。

“Add user”和“Edit user”对话框显示当前登录用户名，只允许填写 `telegram id`，并提供“保存”和“Close”按钮。ID 为空时提示“请填写 Telegram id token。”；保存期间按钮不可用；成功后关闭对话框并刷新列表，失败时保留对话框并显示错误。只有对应当前登录用户的联系人可以编辑或删除。删除前确认，确认框中显示名称和遮蔽的 ID。管理员代绑、代解绑通过下述 HATG 指令执行。

列表中只显示遮蔽的 ID，但编辑时需要完整 ID，因此页面收到的联系人数据包含完整 ID；只有能查看 Telepus Watchboard 的用户才能取得这些数据。

## 联系人的规则

- **Telegram ID 是 5 到 32 位数字。** 不符合时报错“Telegram id token 必须是 5-32 位数字”。
- **任何登录用户都可以绑定或解绑本人的 Telegram。** 绑定只检查 Telegram ID 的格式和唯一性，不验证该 Telegram ID 是否归当前用户所有。
- **页面保存和删除的对象由当前认证 User 决定。** 请求只提交 Telegram ID，服务端从认证上下文取用户名；即使调用者是管理员，页面操作也只影响本人。联系人名称使用目标账号的规范用户名。
- **不能重复。** Telegram ID 已被其他联系人使用时报错“这个 Telegram id token 已存在”；Portal 用户已对应其他 Telegram ID 时报错“这个 Portal user 已绑定到其它 Telepus contact”。出错时原有联系人不变。
- **可以修改自己联系人的 Telegram ID。** 编辑时附带原来的 ID（`previous_id_token`）；服务持联系人锁重新读取旧记录，确认它仍绑定当前 User 后再保存。旧记录缺失、归属变化或属于其他用户时返回 409，原联系人不变；保存成功后旧 ID 被新 ID 取代。保存与删除使用同一联系人锁，避免并发更新互相覆盖。
- **联系人管理只绑定已存在的活动账号。** 目标 User 未登记、已停用或已删除时保存失败；创建用户通过 `CreateUser` 完成。联系人保存结果返回 `contact`，其中包含 Telegram ID、名称和绑定用户名。
- **删除只删除期望用户的对应关系。** 用户名与 Telegram ID 必须仍匹配，归属变化返回 409；不存在的 ID 也算成功，Portal 用户保留。管理员可以为已停用但尚未删除的账号解绑。

## 管理员代绑与代解绑

`BindTelepusContactForUser` 与 `UnbindTelepusContactForUser` 属于 Web 执行的用户 ISA（decision_6eac174d85bf）。默认模板要求两者的调用者是管理员（对“共享”节点有 admin，decision_600ae27b0538），只对某个 User 账号有 admin 不满足此要求。处理器只校验参数、账号状态与当前绑定，不自行判断管理员或 owner；指令直接调用联系人领域，不经过 Watchboard 的应用操作权限。

| 指令 | 参数 | 结果 |
| --- | --- | --- |
| BindTelepusContactForUser | `username`、`id_token`；替换该用户已有 ID 时提供 `previous_id_token` | `contact`：`telegram_user_id`、`name`、`portal_actor` |
| UnbindTelepusContactForUser | `username`、`id_token` | `username`、`telegram_user_id`、`removed`；不存在的 ID 返回 `removed: false` |

两种入口共用联系人锁、账号查询和唯一性校验。替换的旧 ID 必须仍属于目标用户，不能借 `previous_id_token` 覆盖别人的联系人；要重新分配一个 ID，应先按旧用户显式解绑，再给新用户绑定。任何冲突都保留原联系人。指令执行遵循公共幂等协议，重放只返回对象引用；最新名称与归属以 Contacts 数据为准。

## 接口

页面通过 `POST /api/agent-workbench/residents/watchboard-application` 操作联系人：

| 字段 | 值 |
| --- | --- |
| `resident_id` | Telepus Resident 的 ID |
| `application_id` | `telepus` |
| `operation` | `save` 或 `delete` |
| `args` | `save`：`id_token`，编辑时加 `previous_id_token`；`delete`：`id_token`。出现其他字段时报错。 |

服务先确认请求带有认证身份且 Watchboard 已启用（否则报错“工作台应用操作需要认证身份和已启用的 Watchboard”），再取得 Watchboard 脚本目录下的应用锁 `.portal-application.lock`；持锁后重新读取 Watchboard、再次检查权限，并确认脚本目录没有变化、应用属于这个 Resident，之后才修改联系人表。成功时返回完整的 Watchboard 数据，页面据此重绘。无权限返回 403，Resident 不存在返回 404，参数或应用不符返回 400，旧联系人绑定冲突返回 409。

Watchboard 数据中的联系人部分：

| 字段 | 含义 |
| --- | --- |
| `application_id` | `telepus` |
| `telegram_users.enabled` | 固定为 `true`，表示有联系人功能 |
| `telegram_users.count` | 联系人数 |
| `telegram_users.users` | 联系人列表，按名称排序；每项有 `telegram_user_id`（完整 ID）、`display_id`（遮蔽的 ID）、`name`、`portal_actor`（Portal 用户 ID）和 `current_actor`（是否就是当前登录用户） |
| `telegram_users.current_user` | 当前认证用户的规范用户名，供绑定对话框显示 |
| `telegram_users.permissions.can_manage` | 当前身份能否发起应用操作；页面保存与删除仍只针对当前认证 User，编辑和删除按钮还要求该行的 `current_actor` 为真 |

## 消息怎样进入 Resident

Telegram 程序处理每条消息的过程：

1. 从 Telegram 拉取新消息（长轮询）。
2. 按联系人表找到发信人对应的 Portal 用户；没有对应时回复未授权说明。
3. 确定目标 Resident：已经有目标时继续发给它；否则可以直接描述问题，由程序推荐合适的 Resident，也可以用 `/router` 浏览目录、用 `/route <resident_id>` 或直接发送 Resident ID 指定。
4. 以联系人对应的 Portal User 为 caller，通过 `isa_client.call` 调用 `DeliverMessage`，指定 `activation_policy=enqueue_and_trigger`。桥接进程使用 socket 路由，由 Portal 的 ISA 客户端对 `instruction.execute` 请求签名，再交给拥有该指令的服务（decision_96a887d83e8a）。消息中的图片先下载到 Portal 的共享上传目录，以 `[image: <路径>]` 的形式附在文字后。
5. 读取目标的最新回复，按每段最多 3000 个字符分段发回 Telegram。

Resident 信息通过 `GetResident` 读取，对话通过 `GetConversation` 读取；状态、单条输入、队列和队列操作分别调用对应的 HATG ISA。桥接只转换 Telegram 所需的显示结果，消息作者和执行权限由 Portal 按 caller 确定。发送消息使用该消息 ID 对应的幂等键；指令拒绝或服务不可用时直接返回错误，不更换 caller 重试。

这些调用使用普通入口 `entry=web`，不依赖系统入口豁免。权限由权限系统按这位 User 自己的表项判定（decision_b820faf42218）。消息投递、Mailbox 整理和取消的默认要求分别见 decision_2fe6fd5367fb、decision_c54e68a82d7a、decision_2a3ff353165c；`CancelInput` 按目标 Resident 的 contributor 要求判定，Telepus 不因消息发送者身份另外放行。覆盖 Resident 所在 Lineage 或 Project 的表项按同一方案生效；能发送不代表能执行所有队列操作。

程序还提供查看和管理目标队列（`/queue`）、向当前任务插入补充内容（`/now`）、停止当前任务（`/stop`）、查看当前目标（`/where`）、拉取最新回复（`/refresh`）、最近使用的 Resident（`/recent`）等命令，发送 `/help` 可以看到完整列表。另有 Breakglass 通道，在 Portal 故障时提供一个独立的 Agent，同一时间只由一位用户使用。

程序把不属于任何 Project、标题为“Ask Me Anything”的 Lineage 中的 Resident 当作用户的个人入口；这与 Portal 页面按个人入口关联确定 Ask Me Anything 的方式（decision_bd5217d08c09）不同。

## 职责划分

- **Portal 负责**联系人界面和联系人表的写入、目标 Resident 的权限检查、消息的排队与执行。
- **Telegram 程序负责**与 Telegram 的连接、命令解析、目标选择和回复的发送。Resident 完成回复不代表 Telegram 已经送达。

## 故障与恢复

| 情况 | 表现 | 处理 |
| --- | --- | --- |
| 数据库不可用 | 页面读取或保存联系人时报错；Telegram 程序无法确认发信人，消息不会投递给 Resident | 数据库恢复后重新保存或重新发送 |
| 保存时 ID 或用户重复 | 页面保留对话框并显示错误，原联系人不变 | 改用其他 ID 或先删除旧联系人 |
| 页面响应丢失 | 不确定是否保存成功 | 重新打开 Watchboard，以联系人表为准；重复删除是安全的 |
| 目标 Resident 已删除或不可见 | 程序提示目标不可用 | 重新选择目标 |
| Telegram 发送失败 | Resident 的回复已完成但用户没有收到 | 用户发送 `/refresh` 重新拉取最新回复 |
