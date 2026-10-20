---
kb_id: decision_30528aff7035
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 42
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T08:32:15Z'
  updated_by:
    user: ''
    agent: resident_d43295e5e043
importance: 9
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_common/portal_network.py
  - ${code_root}/management_portal/http_admin.py
  - ${code_root}/tests/cases/test_portal_network_settings.py
  - ${code_root}/management_portal/static/portal_user_enrollment.js
  - ${code_root}/management_portal/http_user_enrollment.py
  - ${code_root}/management_portal/user_enrollment.py
  - ${code_root}/management_portal/web_isa.py
  - ${code_root}/management_portal/http_admin.py
  - ${code_root}/management_portal/portal_access_control.py
  - ${code_root}/management_portal/serve_management_portal.py
  - ${code_root}/management_portal/static/portal_management.js
  - ${code_root}/tests/cases/test_portal_access_control.py
  - ${code_root}/tests/cases/test_security_wave5.py
  - ${code_root}/management_portal/join.html
  - ${code_root}/management_portal/login.html
  related:
  - decision_3286d4167180
  - decision_600ae27b0538
  - decision_ae422b92c6f3
  - decision_e486671af066
  - decision_22227d0ad075
  - decision_6eac174d85bf
  - decision_5bc3fdfc7eec
  - decision_3fe3cca8fd95
  - decision_34158fdd4078
  - decision_b820faf42218
  - decision_ea13b85216d0
  supersedes: []
  derived_from: []
---

# Portal Management：用户账号管理

Portal Management 是 Portal 的管理页面，从页面右上角的账号菜单进入（decision_e486671af066）。管理员在这里维护可以登录 Portal 的人类用户：查看名册，创建账号或发出邀请，修改资料和账号状态，帮用户恢复登录，永久删除账号。本文说明这些账号操作。页面中的权限表项与权限模板分栏属于权限配置，见 decision_5bc3fdfc7eec、decision_b820faf42218 与 decision_ea13b85216d0。

## 基本概念

- **User 分类**：每个 User 是 Portal 管理员、一般用户或实习用户之一，没有记录时是一般用户（decision_6eac174d85bf）。默认模板中只有 Portal 管理员对 R 树的“共享”节点有 admin（decision_600ae27b0538）。分类用 UpdateUser 的 `changes.category` 修改：管理员名单变更按 decision_b820faf42218 §3.1 的独立保底规则执行；一般用户与实习用户之间的调整按指令权限判定。首个账号与管理员名单的完整约束由同一节维护。账号写入由权限系统按当前模板判定；账号名称不构成停用、删除或邀请的例外，见 decision_34158fdd4078。
- **账号状态**：决定用户能否建立新的登录会话。`active` 可以登录；`disabled` 暂时停用，保留恢复所需的全部数据；`deleted` 已永久删除，用户名留下不可复用的删除标记。
- **会话版本**：每个账号记录一个会话版本号。浏览器登录会话保存签发时的版本号；版本号增加后，这个账号此前的所有浏览器会话都在下一次请求时失效。个人访问凭据记录自己的撤销状态；安全操作另行撤销全部个人令牌。
- **恢复邮箱**：接收密码重置链接的邮箱，每个用户最多一个，不能与其他用户重复。
- **邀请**：管理员为一个尚不存在的用户名创建的一次性注册链接，被邀请人打开链接后自己设置密码。
- **访问审计**：管理员账号操作的追加记录，保存操作者、动作、目标、时间和操作细节，不保存密码、令牌或其他秘密。

## 页面结构

管理页面左侧是纵向分栏导航，右侧是工作区。与账号及权限直接相关的分栏如下；资源类型、AI 后端、数据目录、网络等分栏按各自领域提供管理入口：

| 分栏 | 内容 | 说明 |
| --- | --- | --- |
| 用户 | 用户名册、资料编辑、新建用户、邀请、登录恢复、删除、最近 50 条审计 | 本文 |
| 权限表项 | User 与 Resident 名册，选中主体名下的表项和临时凭证（GetEffectivePermissions）；用户分栏的“查看权限表项”按钮打开这里 | decision_5bc3fdfc7eec |
| 权限方案 | 内置与自定义权限方案的列表，以及规则的可视化编辑、保存与启用 | decision_ea13b85216d0 |
| 执行设置 | 全系统的执行设置，分三组：“权限”组是“权限系统”开关和当前权限模板的选择；“记录”组是“模型请求时间记录”和“Harness 记录”两个开关；“保留天数”组是“Harness 记录与产物”（21–365 天，默认 30） | decision_25eb93d803a4、decision_b820faf42218 |

全系统设置只放在管理页面；账号菜单（Menu）只放个人设置。

权限系统关闭时（decision_b820faf42218），任何已登录 User 都可以打开管理页面，权限表项分栏隐藏，直接打开权限表项分栏的地址会转到用户分栏；权限方案分栏保留，方案照常可以查看和编辑，只是暂不参与判定，并可以选定开启时使用的方案（decision_ea13b85216d0）。“权限系统”开关勾选表示开启，说明文字先讲勾选和不勾选各自的效果，开关旁标出“当前：已开启”或“当前：已关闭”。切换前弹出确认，说明关闭或开启的后果。切换后刷新页面才会显示或隐藏权限表项分栏。“权限”组的方案选择用 ListPermissionSchemes 列出内置方案与自定义方案；换用其他方案时先以 ActivatePermissionScheme 的 `dry_run` 报告目标方案实例的补齐数量和授权差，确认后再调用 ActivatePermissionScheme 切换（decision_22227d0ad075）。

执行设置分栏第一次打开时用 GetExecutionSettings 读取全部设置，之后每个控件单独保存：开关切换后立即调用 UpdateExecutionSettings；天数输入在回车或失去焦点时检查，不是范围内整数时恢复已保存的值并提示允许范围，不发送请求，合法且有变化时才保存。保存失败时只恢复这一个控件，并显示服务端错误；保存成功只重画这一个控件，其他输入框里尚未提交的内容保留。

- **分栏在第一次打开时才加载。** 切换分栏保留草稿、滚动位置和选择；再次打开时复用已加载的内容，只刷新过期的数据。关闭页面时，页面汇总所有分栏未保存的修改，确认后才放弃。
- **用户列表按用户名排序，不区分大小写。** 列表不显示已永久删除的用户；Portal 管理员和实习用户在用户名后标出分类（`ListUsers` 每项的 `category`），一般用户标出账号状态。编辑区的“User 分类”下拉框随“Save access”保存，分类有变化时在同一次 UpdateUser 的 `changes` 中带上 `category`；资料字段和分类下拉首先依据该行 `allows.UpdateUser` 决定能否编辑；涉及管理员名单时再按调用者及目标的管理员身份限制选项，自身管理员的分类和状态保持锁定。页面首次加载时选中当前调用者，找不到时选中第一位用户；没有用户时编辑区显示空状态。

## 谁能使用

管理入口由 `/api/me` 的 `access.allows` 和管理员身份决定；能进入页面不表示能执行所有操作。用户指令及其默认分类由 decision_6eac174d85bf、decision_600ae27b0538 维护，管理员独立保底由 decision_b820faf42218 §3.1 维护。每个服务端请求重新检查所调用指令和领域约束。

- **管理接口只接受浏览器登录会话。** 未登录时进入登录流程；Resident 的 Bearer 令牌不能直接调用这些接口。
- **页面检查只决定显示什么，服务端检查决定能否执行。** 页面读取 `/api/me` 的 `access.allows`和 `access.user_category`，管理员或能通过 CreateUser、GetExecutionSettings、UpdateExecutionSettings、ListResourceTypes、ListProviders 之一的调用者可加载面板；每个请求在服务端重新判断。
- **管理员身份不写入业务 owner 列表。** 管理员不会因此成为任何 Lineage 或资源的 owner，也不能借此恢复已删除的对象或绕过领域的生命周期规则。

## 查看账号

- **选中用户后显示服务端的当前状态。** 编辑区显示显示名称、恢复邮箱、账号状态。
- **读取结果不含认证秘密。** 密码摘要、会话签名密钥、两步验证种子、Passkey 凭据和密码重置令牌都只留在各自的服务端存储中。

## 创建账号

管理员可以用两种方式增加用户：在 “New user” 表单中直接创建，或者发出邀请。

### 直接创建

- **新账号需要唯一的用户名和初始密码。** 用户名为 1–80 个字母、数字或 `_`、`.`、`@`、`+`、`-`，不能占用 Resident 或服务的命名空间（decision_ae422b92c6f3）。密码长度为 12–256 个字符，并且至少包含大写字母、小写字母、数字、符号中的三类。
- **显示名称和恢复邮箱可以在创建时填写。** 显示名称为空时使用用户名；恢复邮箱要通过格式和唯一性校验。
- **已存在或曾被永久删除的用户名不能使用。** 服务端同时检查密码记录、账号记录和删除标记。
- **新账号的权限来自模板规则。** 服务端建立账号、写入初始密码，并把账号同步给权限系统：账号进入 P 树，R 树中出现它的 User 账号和个人空间；默认模板中本人对这两者有 admin，并与全员一样对“共享”有 viewer。创建者的权限不会复制给新账号。需要更多权限时，管理员在创建完成后从表单打开该账号的权限表项，再在相应资源的 Package 中为它在实例中增加表项。
- **创建失败时保留输入，不自动重试。** 页面显示失败原因，管理员核对后再次提交。

### 邀请

- **邀请绑定一个用户名，有效期 1–30 天，默认 7 天。** 管理员填写用户名、显示名称、邀请对象（人类或 Agent）和有效天数后，浏览器生成一个随机密钥，只把它的 SHA-256 摘要交给服务端（ISA 指令 `CreateUserInvitation`，decision_6eac174d85bf），并把邀请链接 `<Portal 地址>/join#invite=<密钥>` 显示给管理员转交。服务端不保存密钥本身，也不记录邀请对象。
- **邀请对象只决定链接和转交说明。** 选 Agent 时链接末尾加 `&for=agent`，加入页直接打开 agent 路径。页面同时生成一段可直接发送的转交说明：人类版包含链接、有效天数和新用户指引地址 `/guide`；Agent 版包含链接、Agent 指引地址 `/guide/agent.md`，以及下载客户端并领取邀请的两行命令（`curl -fsSO <Portal 地址>/guide/portal_user.py`、`python3 portal_user.py --url <Portal 地址> --credentials … join`）。见 decision_3286d4167180。
- **每个用户名同时只能有一个待接受的邀请。** 用户名已经存在或被删除过时，不能邀请。

### 加入页

被邀请人打开链接或粘贴邀请码后，加入页 `/join` 按以下顺序引导：

1. **先确认邀请。** 页面把密钥交给 `POST /api/auth/invitations/preview`，显示“你受邀以 <显示名称>（<用户名>）加入 Portal，邀请有效至 <时间>”。邀请无效、过期或已被使用时只显示这一句并请对方索要新邀请，不显示后续表单。链接的 fragment 读出后立即从地址栏移除。
2. **选择谁来使用这个账号。** “我自己”设置密码，在浏览器中使用 Portal；“一个 agent”用个人访问令牌调用 API。链接带 `for=agent` 时默认选中后者。
3. **人类：设置密码。** 密码规则逐条显示是否满足，不满足或两次输入不一致时不提交。成功后显示“账号 <用户名> 已创建”、Passkey 与两步验证的建议，以及“登录”按钮；登录页 `/login?username=<用户名>` 预填用户名，光标落在密码框。
4. **Agent：首推命令行领取。** 页面给出已填好 Portal 地址的下载与 `join` 命令：令牌在 agent 所在机器上生成，只保存在本地 0600 文件中。备选“改为在浏览器中生成令牌”：浏览器生成 `dbu_` 令牌并只提交摘要，成功后令牌只显示一次，同时给出交给 agent 的说明（Portal 地址、用户名、调用入口、Agent 指引）。重试沿用同一个令牌，响应丢失时不会产生第二个未知令牌。

加入页与登录页使用同一套卡片样式和公共顶栏；底部提供“已有账号？登录”和“新用户指引”。

**首个管理员的设置链接也走加入页。** 新部署没有任何账号时（已删除账号的记录也算账号），部署者在主机上运行 `scripts/portal_setup.py first-admin` 得到一条带 `first_admin` 标记的一次性邀请。加入页预览显示“你将以 <显示名称>（<用户名>）成为这个 Portal 的首个管理员”，账号创建后自动加入管理员名单，成功页提供“打开管理员指引”。已有任何账号时，命令和链接都拒绝。规则见 decision_3286d4167180。

- **被邀请人提交后由服务端创建账号。** 页面把密钥和密码（或令牌摘要）提交给 `/api/auth/invitations/accept`。邀请过期、已被使用或已撤销时返回 “Invitation is invalid, expired or already used”。接受成功后账号立即有效，并写入 `user_invitation_accepted` 审计。
- **管理员可以查看和撤销邀请。** 列表显示每个邀请的状态：`pending`、`accepted`、`revoked`，或已过期的 `expired`；只有 `pending` 的邀请可以撤销。

## 修改账号

- **资料与状态通过 UpdateUser 提交。** 显示名称、恢复邮箱、头像和账号状态分别由字段校验约束；用户名不能修改。Project 角色与系统能力不再读写。
- **全部字段校验通过后才写入。** 未知用户、非法邮箱、重复邮箱都会让本次修改失败，已有数据保持不变。
- **所有账号按同一生命周期规则停用和删除。** 当前模板的指令映射确定操作所需的等级，账号名称不增加限制。
- **恢复邮箱改变后，旧的密码重置链接作废。** 该用户尚未使用的重置令牌被清除。

## 登录恢复与会话撤销

- **管理员可以重置他人密码。** ResetUserPassword 的 new_password 提交新密码，不需要目标用户的当前密码或两步验证码，新密码仍须满足密码规则。不能通过这条路径改调用者自己的密码；本人在 Settings 提交当前凭据。设置后，尚未使用的重置令牌被清除，撤销全部 `dbu_` 个人令牌，会话版本加一，用户所有设备需要重新登录，并写入 `password_reset` 审计。账号状态保持不变。
- **管理员通过 RevokeUserSessions 撤销目标用户的全部登录会话。** 这个操作把会话版本加一，并持久撤销该用户全部 `dbu_` 个人令牌，不改变密码或其他用户的会话；页面提示该用户所有设备需要重新登录。

## 永久删除

- **删除就是删除。** 账号只有“删除”一种移除方式，不提供保留数据、日后恢复的中间状态。只想暂停使用时，把状态设为 `disabled`：密码、两步验证、Passkey、个人偏好、收藏和上传文件都保留，改回 `active` 即可恢复。
- **永久删除需要再输入一次完整的用户名。** 输入与目标用户名完全一致之前，删除按钮不可用。管理员不能删除自己。
- **永久删除清除个人认证与个人数据，保留共享的业务历史。** 服务端先停用账号并撤销会话，再依次删除个人偏好、全部收藏（Lineage、Resident、工作台、Paper 等各处的收藏）、个人上传目录和头像，然后由每个已安装的功能删除自己为该用户保存的数据（功能接入点 `user.deleted`，见 decision_15aab18c0ab3；目前是 Telepus 删除 Telegram 联系人绑定），再删除密码、两步验证状态和 Passkey；最后把账号标记为 `deleted`，写入 `user_deleted` 审计。Project、Lineage、对话和执行历史继续保留。
- **删除结果逐项说明。** 响应的 `deleted_data` 列出实际删除的数据类别，各功能删除的数据按功能键报告（例如 `telepus`），`retained` 列出保留的类别。这些步骤分属不同的存储，不在同一个事务中；中途失败时已完成的步骤不会恢复，管理员依据响应和审计核对。
- **永久删除后的用户名不能再次使用。** 删除标记防止新用户继承旧历史中同名的引用。

## 已退役的模式与角色入口

A/B 切换、恢复 A、旧首次初始化/迁移、Project 角色和系统能力表单已删除。页面按管理对象提供用户、权限表项、权限方案、资源类型、AI 后端、执行设置、数据目录和网络设置分栏。权限系统开启时，管理页面入口与指令授权都由当前模板按指令判定。

MigrateBusinessOwners 已移出公共 ISA。历史迁移证据保留，清理数据须另行授权并满足版本、范围与逐条处置门禁。

## 网络设置

“网络设置”仅对 Portal 管理员展示并按需加载，提供“允许网段”和“网络代理”两组独立表单。允许网段按行输入 IPv4/IPv6 地址或 CIDR，约束 Router 的直接连接方；本机回环地址始终保留。代理提供 HTTP、HTTPS、通用代理 URL 和不走代理地址列表，说明协议为空时使用通用代理、全部为空时直连。

页面通过 GET/POST `/api/admin/network` 读写站点配置，真实接口与生效边界见 decision_85222f2d316b。保存携带读取时的 version；一组成功后只更新该组与版本，不覆盖另一组草稿。加载失败可重试，保存期间禁止重复提交，失败或版本冲突保留输入，切换分栏不丢草稿，关闭存在未保存内容的管理窗口需要确认丢弃。

保存网段后用于 Router 的下一次新连接。代理用于新启动的服务和 AI 进程，既有服务需重启；页面明确展示这一条件，不将保存成功称为所有运行进程已切换，也不在保存时自动重启会话或服务。

## 一次管理操作怎样完成

```text
管理员在页面选择用户和操作
  → 页面提交 ISA 指令
  → 服务端确认真实调用者，由权限系统按当前模板判定
  → 服务端校验目标用户和字段
  → 修改对应的存储，写入访问审计
  → 页面重新读取服务端状态并显示结果
```

保存一个账号只刷新受影响的数据，其他账号和其他分栏的草稿保持不变。页面只在服务端返回成功后显示修改已生效；失败时保留输入，并显示服务端的实际状态。

## 数据保存在哪里

身份模块在请求进程启动时只初始化当前表结构，不运行 `ALTER TABLE` 等历史迁移。已有数据库升级由独立迁移操作完成；数据库备份持有读锁时，账号读取与 Resident 启动仍可进行。

| 存储 | 保存内容 |
| --- | --- |
| 账号存储 `PortalAccessStore`（PostgreSQL 表 `portal_identity.user_accounts`） | 用户名、显示名称、恢复邮箱、账号状态、会话版本、删除标记 |
| 邀请与个人访问凭据 `UserEnrollment`（PostgreSQL 表 `portal_identity.user_invitations`、`portal_identity.user_access_tokens`） | 邀请记录和凭据记录，只保存密钥摘要 |
| 密码、两步验证、Passkey、密码重置各自的存储 | 密码摘要、两步验证种子与恢复码状态、Passkey 公钥、有期限的重置令牌 |
| 偏好、收藏、头像与上传目录 | 用户个人范围的数据 |
| Telepus 联系人表 `telepus.telegram_contacts` | Telegram 数字 ID 绑定的 Portal 用户；Telegram Router 按它授权消息，删除账号时一并删除该用户的绑定 |
| 访问审计表 `portal_identity.access_audit` | 管理操作记录；只追加，Portal 不修改或删除已有记录 |

账号记录在数据库事务中逐条修改：服务端读取最新记录，只改动请求涉及的用户，多个管理员的修改按提交顺序生效。每次修改账号记录都写入一条 `user_access_updated` 审计，记录修改后的状态、是否配置了恢复邮箱以及是否撤销了会话。

## 接口

页面通过两类入口完成操作：

- **ISA 指令**（`/api/instructions/execute`，decision_898b8911a2ff）：`CreateUser`、`UpdateUser`（资料与状态）、`ResetUserPassword`、`RevokeUserSessions`、`DeleteUser`、`CreateUserInvitation`、`ListUserInvitations`、`RevokeUserInvitation`。
- **管理接口** `/api/admin/access-control`：
  - `GET` 返回 密码重置邮件是否已配置、未删除的用户、最近 50 条审计。
  - 此地址只提供 GET；全部账号写入通过 ISA 提交。

ISA 拒绝未知字段和不合法的 changes。字段非法返回 400，无权操作返回 403，目标用户不存在返回 404，用户名或邮箱冲突返回 409；其他异常返回不含内部细节的 500。
