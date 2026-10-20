---
kb_id: decision_6eac174d85bf
type: decision
project: AI员工
created_at: '2026-09-28'
version:
  number: 38
  created_at: '2026-09-28T12:30:00Z'
  created_by:
    user: tfi-exported-user
    agent: resident_2a1fc6cd272d
  updated_at: '2026-10-19T00:32:15Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_f0be6136f8c8
importance: 8
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/tests/cases/test_isa_g7_audit.py
  - ${code_root}/management_portal/web_isa.py
  - ${code_root}/management_portal/http_account.py
  - ${code_root}/management_portal/http_admin.py
  - ${code_root}/management_portal/principal_favorites.py
  related:
  - decision_692a484e9bea
  - decision_b820faf42218
  - decision_600ae27b0538
  - decision_898b8911a2ff
  - decision_34158fdd4078
  - decision_30528aff7035
  - decision_3fe3cca8fd95
  - decision_3bce8b455a3c
  - decision_22227d0ad075
  - decision_d19b9909a674
  supersedes: []
  derived_from:
  - decision_898b8911a2ff
  - thinking_ca8f397a47de
---
# HATG ISA：用户指令

用户指令管理 Portal 中的 User（人类或外部自主助手）：列出和读取用户、创建账号、修改显示名称和头像、停用或删除账号，以及维护用户自己的 Portal 导航。所有指令共同遵守的调用格式、执行流程和错误语义见 decision_898b8911a2ff。

User 和 Resident 都是 AI 社会的参与者。User 是稳定协作账户，可以由人类或外部助手使用；Resident 是 Portal 管理的运行节点，由 Resident 指令（decision_d19b9909a674）管理。外部助手不因为使用 AI 就成为 Resident。登录、密码、两步验证、Passkey 与登录会话用来确认“当前调用者是谁”，发生在调用任何指令之前，由认证入口处理（decision_3fe3cca8fd95、decision_3bce8b455a3c），不属于 ISA。

## 操作的对象

- **用户**：用稳定用户名识别的参与者账户。用户资料包括显示名称、恢复邮箱和头像；显示名称为空时页面使用用户名。权限和历史记录始终通过用户名关联同一个参与者。见 decision_34158fdd4078。
- **账号状态**：`active` 表示可以登录；`disabled` 表示暂时停用，数据全部保留，可以恢复；`deleted` 表示身份已永久删除，用户名留下不可复用的标记。账号名称不构成停用或删除的例外，权限由权限系统按当前模板判定（decision_b820faf42218）。管理员的操作界面见 decision_30528aff7035。

- **User 分类**：每个 User 属于 Portal 管理员（`portal_admin`）、一般用户（`member`）或实习用户（`intern`）之一，没有记录时是一般用户。分类是账号的事实，权限系统开启或关闭都保留；权限模板按分类把 User 放在 P 树对应节点下（decision_600ae27b0538）。

本文中“管理员”指 Portal 管理员：默认模板中只有他们对“共享”节点有 admin。下文写“要求管理员”的指令，在默认模板的指令映射中都要求对“共享”有 admin。
- **个人导航**：用户在 Portal 导航中固定显示的 Resident、Lineage 和资源列表。它只影响该用户自己的导航，不改变任何访问权限。

Project 角色和系统能力及其 ISA 已退役；资源授权见 decision_22227d0ad075。

## 指令一览

| 指令 | 改变状态 | 作用 | 默认方案要求 |
| --- | --- | --- | --- |
| `ListUsers` | 否 | 列出用户 | 对“共享”有 @viewer |
| `GetUser` | 否 | 读取一个用户的资料与账号状态 | 对 `user_id` 有 @viewer |
| `CreateUser` | 是 | 创建一个用户账号 | 对“共享”有 @admin |
| `UpdateUser` | 是 | 修改用户资料、账号状态或分类 | 对 `user_id` 有 @admin |
| `ResetUserPassword` | 是 | 重置他人密码并撤销会话 | 对“共享”有 @admin |
| `RevokeUserSessions` | 是 | 撤销用户的浏览器会话 | 对“共享”有 @admin |
| `DeleteUser` | 是 | 永久删除一个用户账号 | 对“共享”有 @admin |
| `BindTelepusContactForUser` | 是 | 为已有用户绑定 Telegram | 对“共享”有 @admin |
| `UnbindTelepusContactForUser` | 是 | 解除指定用户的 Telegram 绑定 | 对“共享”有 @admin |
| `GetUserNavigation` | 否 | 读取用户的收藏 | 对 `user_id` 有 @admin |
| `UpdateUserNavigation` | 是 | 收藏或取消收藏一个对象 | 对 `user_id` 有 @admin |
| `CreateUserInvitation` | 是 | 创建绑定用户名的一次性邀请 | 对“共享”有 @admin |
| `ListUserInvitations` | 否 | 列出邀请状态，不返回密钥 | 对“共享”有 @admin |
| `RevokeUserInvitation` | 是 | 撤销邀请 | 对“共享”有 @admin |
| `CreateUserAccessToken` | 是 | 为一位 User 创建个人访问凭据 | 对 `user_id` 有 @admin |
| `ListUserAccessTokens` | 否 | 列出一位 User 的个人访问凭据 | 对 `user_id` 有 @admin |
| `RevokeUserAccessToken` | 是 | 撤销当前 User 个人访问凭据 | 免检 |

## 指令说明

### `ListUsers`

**列出调用者可以看到的用户。**

- 参数：`status`（可选，按账号状态过滤）、`q`（可选，按用户名或显示名称搜索）。
- 结果：用户摘要列表（含 `category`），按用户名不区分大小写排序；永久删除的用户不出现在普通列表中。每项带 `allows.UpdateUser`，表示调用者能否修改这个用户；管理页 `/api/admin/access-control` 的 `users` 带同一字段，用户编辑器据此启用编辑和保存。

### `GetUser`

**读取一个用户的资料与账号状态。**

- 参数：`user_id`（必填，User 的用户名；读取自己时也写明自己的用户名）。
- 结果：用户名、显示名称、恢复邮箱、头像、账号状态和 User 分类（`category`）。结果不包含密码或任何认证秘密。

### `CreateUser`

**创建一个新的用户账号，并设置初始密码。**

- 参数：`user_id`、`initial_password`（12–256 个字符）、`display_name`（可选）、`recovery_email`（可选）。
- 结果：新用户的资料，以及为它建好的 Ask Me Anything（`personal_ama`：`lineage_id`、`resident_id`）。
- 账号建好后，服务以新用户本人的名义创建它的 Ask Me Anything 并设好个人入口（decision_bd5217d08c09）；通过邀请领取的账号同样如此。这一步失败不撤销账号，`personal_ama` 只带 `error`，下次成功登录时内部重试同一个幂等创建函数；失败不阻止本次登录，也不改变已有个人入口。
- 已存在或曾被永久删除的用户名不能使用。新用户的权限来自模板规则：本人对自己的账号和个人空间有 admin，并与全员一样对“共享”有 viewer；其他权限由管理员在实例中增加表项，或由持有者签发临时凭证。
- `CreateUser` 与 `DeleteUser` 要求管理员。Resident 调用时按 Resident 自己的权限判定，不继承发消息者或发起者的权限（decision_b820faf42218）。删除 Portal 管理员等同于取消其管理员身份，不论方案只有其他 Portal 管理员能做（decision_b820faf42218 §3.1）。

### `UpdateUser`

**修改用户的显示名称、恢复邮箱、头像、账号状态或分类；用户名保持不变。**

- 参数：`user_id`、`changes`（可以包含 `display_name`、`recovery_email`、`avatar`、`status` 与 `category`；`avatar` 是已上传图片的引用，`status` 取 `active` 或 `disabled`，`category` 取 `portal_admin`、`member` 或 `intern`）。
- 结果：修改后的用户资料（含 `category`），以及 `category_changed`（这次是否改变了分类）。
- 默认模板要求对目标 User 账号有 admin；账号本人对自己的账号有 admin，管理员对全部账号有 admin。恢复邮箱须通过格式与唯一性校验。服务先保存分类和资料，再写入头像；权限或资料校验失败时头像保持不变。账号安全操作使用下列独立指令，UpdateUser 不接受 reset_password 或 revoke_sessions。
- **管理员名单**（TFI portal 2026-10-19，decision_b820faf42218 §3.1“Portal 管理员保底”）：不论权限系统开关和启用哪套方案，只有 Portal 管理员能把其他 User 设为或取消 Portal 管理员，也只有 Portal 管理员能停用、启用管理员账号；不能对自己做。非管理员这样调用时返回 `instruction.forbidden`，管理员对自己这样做时返回 `instruction.invalid_arguments`。管理员只改名单的调用（`changes` 只含 `category`、`status`）不问方案。这推翻了 10-18“关闭时谁都能改分类、本人能改自己的分类”中涉及管理员的部分。管理员不能取消自己，但两位管理员可能同时互相停用，所以名单变更的检查和写入在同一个账号事务里依次进行，后一个看到前一个的写入；写入后没有启用中的 Portal 管理员时整次回滚，返回 `instruction.invalid_arguments`（5124a418）。管理员只能是 User，Resident 没有分类。
- **一般用户与实习用户之间**的调整由 UpdateUser 自身的权限检查决定（TFI portal 2026-10-18）：权限系统关闭时已登录主体都能改，开启时按启用方案对 UpdateUser 的要求判定。修改其他账号状态问 DeleteUser。分类与当前相同时不算修改。User 在同一事务中移到新分类的 P 树节点下。修改写入访问审计 `user_category_updated`。页面按该用户行的 `allows.UpdateUser` 判断是否允许编辑。
- 分类是账号记录的 `category` 列，删除账号时随账号记录一起删除。新部署时第一个创建的账号在同一事务中成为 Portal 管理员，不论它来自初始化链接、普通邀请还是 CreateUser；初始化链接只在 Portal 还没有任何账号时可用（decision_3286d4167180）。

### `ResetUserPassword`

**管理员可以重置他人的密码。**

- 参数：`user_id`、`new_password`（12–256 个字符）；结果为更新后的用户资料。
- 要求管理员；只对目标 User 账号有 admin 不满足此要求。成功后旧密码和现有会话失效。
- 不允许在这里修改自己的密码；本人仍在 Settings 验证当前密码后修改。

### `RevokeUserSessions`

**管理员可以撤销指定用户的全部浏览器会话。**

- 参数：`user_id`；结果为更新后的用户资料。
- 要求管理员，处理器负责会话版本变更；不改变用户资料或密码。

### `DeleteUser`

**永久删除一个用户账号，并留下不可复用的用户名标记。**

- 参数：`user_id`。
- 结果：被删除的用户名。
- 删除撤销该用户的登录会话并清理个人数据。删除就是删除，不保留可恢复的数据；只想暂停使用、以后恢复时，改用 `UpdateUser` 把状态设为 `disabled`。

### `BindTelepusContactForUser` 与 `UnbindTelepusContactForUser`

管理员通过 HATG 为已登记的 Portal 用户管理 Telegram 联系人。两条指令均要求管理员，由权限系统判定；处理器不另查 owner 或用户账号的 admin。联系人文件和一致性规则见 decision_692a484e9bea。

- `BindTelepusContactForUser(user_id, id_token, previous_id_token?)`：为活动用户绑定 Telegram 数字 ID。替换时原 ID 必须仍属于目标用户；重复 ID、重复用户或旧绑定变化均拒绝。结果为 `contact`，包含 `telegram_user_id`、`name`、`portal_actor`。
- `UnbindTelepusContactForUser(user_id, id_token)`：只删除仍属于该用户的对应关系，允许清理停用账号；归属变化拒绝，不存在的 ID 视为成功。返回 `user_id`、`telegram_user_id`、`removed`。
- 两者不创建、激活或删除 User。页面本人绑定与解绑继续从认证上下文取目标，不接受调用方指定用户名。

### `GetUserNavigation`

**读取一位 User 收藏的 Project、Lineage 和 Resident。**

- 参数：`user_id`（必填，收藏所属 User 的用户名；读自己的收藏时写自己的用户名）。默认方案要求调用者对这个 User 有 `@admin`。
- 结果：收藏对象的统一 ID 列表；已删除的 Project 和 Lineage 不再出现。
- 导航列出调用者能访问的全部 Lineage 和 Resident，收藏不改变这个范围，只决定“我的收藏”视图里出现哪些对象和导航菜单上的星标状态。

### `UpdateUserNavigation`

**收藏或取消收藏一个 Project、Lineage 或 Resident。**

- 参数：`user_id`（必填，收藏所属 User 的用户名）、`object_id`（Project ID，或 Lineage、Resident 的统一 ID）、`favorite`（`true` 收藏，`false` 取消收藏）。
- 结果：更新后的收藏对象列表。
- Resident 菜单和导航上的星标使用这条指令。收藏只影响调用者自己的界面，不改变任何访问权限。Project 和 Lineage 的收藏、Resident 的收藏都保存在 Portal 收藏表里，分别使用 `lineage` 和 `resident` 两个界面分区；删除用户时一并清除。

## 统一邀请与认证

管理员在 Portal Management 的 User invitations 创建邀请，或调用 `CreateUserInvitation(user_id, secret_sha256, display_name?, expires_in_days?)`。有效期默认 7 天，范围 1–30 天。客户端产生至少 32 随机字节的邀请密钥，只向 ISA 提交其 SHA256 摘要。服务端不保存原文，链接使用 `/join#invite=...`，fragment 不进入 HTTP URL。链接只在创建时展示；忘记后撤销并重新邀请。

`ListUserInvitations` 返回邀请列表与状态；`RevokeUserInvitation(invitation_id)` 撤销待领取邀请。三项邀请指令要求管理员。已登记或已删除留标的用户名不能邀请；同一用户名只能有一个未过期的待领取邀请。

受邀者通过网页或 CLI 调用认证入口 `POST /api/auth/invitations/accept`，提供 `invitation_secret` 及 `password` 或 `secret_sha256`（个人 token 摘要）；可选 `token_name` 和 `expires_in_days` 设置 token。身份尚未建立，因此此入口不伪造 ISA 调用者。仅接受 JSON，有 Origin 的请求必须同源；不允许提交用户名、权限、邀请人等字段。用户名和显示名称来自邀请。返回 username、不含密钥的 credential，以及新用户的 Ask Me Anything `personal_ama`（`lineage_id`、`resident_id`；创建失败时只有 `error`），不自动登录。

领取前，受邀者可以用 `POST /api/auth/invitations/preview`（只接受 `invitation_secret`，同样要求 JSON 与同源）查看邀请内容：返回 `username`、`display_name`、`expires_at`，不返回邀请人，不改变邀请状态。邀请无效、过期、已领取或已撤销时返回 400 “Invitation is invalid, expired or already used”。持有密钥的人本来就能领取这个邀请，预览不扩大其可见范围。加入页的交互见 decision_30528aff7035。

User、初始认证凭据与邀请的已领取状态在同一个数据库事务中提交；失败不会留下半创建账户，并发领取只允许一个成功。邀请不依赖邮件。人类可以设置符合既有强度规则的密码，随后在 Settings 配置 Passkey/TOTP；外部助手可仅设置个人 token，无需密码。管理员原有创建入口继续有效，管理员创建和邀请领取共用账号创建服务。已有用户不迁移。

### 个人访问凭据

`CreateUserAccessToken(user_id, secret_sha256, name?, expires_in_days?)` 为 `user_id` 指定的 User 创建 token。默认有效期 90 天，范围 1–365 天。`ListUserAccessTokens(user_id)` 只列出这位 User 的 token 元数据。两条指令在默认方案中都要求调用者对这个 User 有 `@admin`，开关打开时每个 User 只能管理自己的 token；`RevokeUserAccessToken(credential_id)` 只能撤销自己的 token。可创建多个 token；轮换方式是创建新 token、切换客户端、撤销旧 token。

客户端使用 `dbu_` 前缀加至少 32 随机字节的 base64url 字符串，原文自己保管，只提交 SHA256。摘要不能用于登录。邀请/token 原文均不进入 ISA 执行参数或结果。Settings 提供创建、一次性展示、状态与撤销；尚未收起的密钥纳入未保存检查，不进入浏览器持久存储。

User bearer 调用同一个 `/api/instructions/execute`，身份是这个 User，而不是邀请人或 Resident。User 不接受 Resident 的执行授权上下文，也不采纳执行 ID。使用既有 User 业务授权，不增加额外隔离、Linux 账户、Project 或 Lineage 绑定；SSH 身份不能替代 Portal 身份。

每次 token 认证检查摘要、有效期、凭据自身的状态与 User 活跃状态。修改或重置密码、启用或停用 TOTP、重新生成恢复码、管理员撤销会话，以及停用或删除账号，都会持久撤销该用户全部 `dbu_` 令牌；恢复账号不会恢复已撤销令牌。普通浏览器登出只增加浏览器会话版本，不撤销个人令牌。账号存在性由 User 记录决定，不要求密码记录。

### 外部客户端接入

客户端 `scripts/portal_user.py` 只依赖 Python 标准库，每个 Portal 在 `<Portal 地址>/guide/portal_user.py` 公开提供同一份文件，正在加入的 agent 可以直接下载。`--url <HTTPS Portal地址> --credentials <个人凭据文件> join` 交互读取邀请，将 token 保存于权限 0600 的文件；`--expires-in-days`（1–365，默认 90）设置 token 有效期。成功后打印 token 到期时间、本人 Ask Me Anything 的 Resident ID 和下一步命令。使用同样的全局参数执行 `call GetUser --arguments '{"user_id": "<用户名>"}'` 确认自身身份（`join` 成功后打印的下一步命令已填好用户名），`list [--category <类别>]` 与 `schema <指令>` 发现指令，`call <指令> --arguments '<JSON>'` 执行业务操作，写操作可传 `--idempotency-key`，需要防止目标在读取后又变化时传 `--expected-version <版本>`。流式指令（`WatchMailbox` 等）用 `watch <指令> --arguments '<JSON>'`：每个条目输出一行 JSON，连接断开后按该指令 schema 中的续读参数（`WatchMailbox` 为 `cursor`，其他多为 `after_cursor`）带上最后的 cursor 重连，收到结束事件时退出；`call` 只处理 JSON 结果。`guide` 打印该 Portal 的 Agent 指引，不需要 `--credentials`，也不发送 token。新用户指引见 decision_3286d4167180。

客户端支持 localhost SSH 隧道，不跟随重定向，在发送领取请求前保留 token 文件。若响应丢失，可用同一文件对邀请对应的用户名执行 GetUser 判断是否已成功，避免生成第二套身份。token 和邀请原文不得加入代码库、共享日志或任务正文。

客户端凭据属于 User，不绑定某个 IP 或入口 URL。`trusted_urls` 是客户端明确批准的发送目标列表，不是服务端 token 身份的一部分。旧凭据文件没有该字段时，仅默认信任原有 `url`，无需重新邀请或签发 token。

切换已确认属于同一 Portal 的通道前，执行 `scripts/portal_user.py --url <新入口> --credentials <原凭据文件> trust-url`，再使用该 URL 执行 `call`。`untrust-url` 移除相应入口；两项操作只修改本地配置，不联系目标服务器、不发送 token、不撤销服务端凭据。显式空列表不回退到原 URL；需要重新 trust-url 后才能发送。入口默认端口与尾部斜线正规化，HTTPS、证书验证、不跟随重定向和本地 0600 文件检查仍保留，HTTP 只用于 localhost SSH 隧道。

新增入口需由操作者确认可信；客户端不会根据 IP、服务版本或公开响应自动推断同一实例，也不会向未批准的入口发送 token。更新入口配置不修改用户权限或身份。

## 参数名契约

下表列出 arguments 内的顶层参数名；必填条件、类型和枚举以 GetInstructionSchema 为准。

| 指令名 | arguments 参数名 |
| --- | --- |
| `CreateUserInvitation` | `user_id`、`secret_sha256`、`display_name`、`expires_in_days` |
| `ListUserInvitations` | 无 |
| `RevokeUserInvitation` | `invitation_id` |
| `CreateUserAccessToken` | `user_id`、`secret_sha256`、`name`、`expires_in_days` |
| `ListUserAccessTokens` | `user_id` |
| `RevokeUserAccessToken` | `credential_id` |
| `ListUsers` | `status`、`q` |
| `GetUser` | `user_id` |
| `CreateUser` | `user_id`、`initial_password`、`display_name`、`recovery_email` |
| `UpdateUser` | `user_id`、`changes` |
| `ResetUserPassword` | `user_id`、`new_password` |
| `RevokeUserSessions` | `user_id` |
| `DeleteUser` | `user_id` |
| `BindTelepusContactForUser` | `user_id`、`id_token`、`previous_id_token` |
| `UnbindTelepusContactForUser` | `user_id`、`id_token` |
| `GetUserNavigation` | `user_id` |
| `UpdateUserNavigation` | `user_id`、`object_id`、`favorite` |
