---
kb_id: decision_3bce8b455a3c
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 10
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T08:32:40Z'
  updated_by:
    user: ''
    agent: resident_d43295e5e043
importance: 10
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/portal_access_control.py
  - ${code_root}/management_portal/portal_session_auth.py
  - ${code_root}/management_portal/portal_password_reset.py
  - ${code_root}/management_portal/serve_management_portal.py
  - ${code_root}/tests/cases/test_portal_password_reset.py
  - ${code_root}/tests/cases/test_portal_security_repair_wave1.py
  related:
  - decision_6c3d74a9bc9d
  - decision_34158fdd4078
  supersedes: []
  derived_from: []
---
# Portal 登录 Session、密码与重置协议

人类用户通过登录页进入 Portal。密码登录成功后，Portal 在浏览器中保存一份有期限、可撤销的登录证明；用户随后访问页面时不需要重复输入密码。忘记密码的用户可以通过已登记恢复邮箱取得一次性重置链接。本 Design 从用户经历开始说明登录、登出、修改密码和密码重置，并在后半部分记录精确的会话与接口协议。

## 先认识登录过程中使用的对象

**登录页**是未登录用户提交用户名和密码的公开页面。已登录用户再次访问它时会返回 Portal 首页。

**密码摘要**是服务端由密码计算出的不可逆验证数据。Portal 不保存明文密码，验证时使用同一算法比较摘要。

**浏览器会话**是登录成功后保存在 Cookie 中的签名 Token，名称为 `db_session`。它携带用户名、有效期和账号会话版本，使后续请求能够恢复当前人类身份。

**会话版本**的代码字段名是 `session_version`。它是账号级撤销开关：数字增加后，此前签发给该用户的所有浏览器会话都会失效。

**两步登录挑战**是密码正确且账号启用 TOTP 后产生的短期中间状态。用户完成动态口令或恢复码验证后才获得普通浏览器会话。

**密码重置 Token**是通过恢复邮箱交付的一次性随机字符串。服务端只保存它的摘要，并限制使用期限。

## 用户会经历的四条路径

```text
正常登录：用户名和密码 → 可选的两步验证 → 进入 Portal
主动登出：点击登出 → 撤销该账号旧会话 → 返回登录页
修改密码：当前会话 + 当前凭据 + 新密码 → 当前浏览器继续登录
忘记密码：提交恢复邮箱 → 邮件链接 → 设置新密码 → 重新登录
```

## 设计点

### 登录与失败反馈

- **未登录用户可以使用密码登录页。** 浏览器向 `/login` 提交用户名和密码；已登录用户访问登录页时直接返回 Portal 首页。
- **Portal 在验证密码前检查请求来源和输入边界。** 空用户名、空密码、过大的表单和来自其他网站的请求都会在产生登录状态前被拒绝。登录及其他浏览器写操作先检查 `Origin`，缺少时检查 `Referer`；来源存在且协议或主机不符则拒绝。两者均缺少的命令行请求可以继续执行认证与授权，不由来源检查授予权限。
- **部署可以限定可登录用户名。** 只有 active 账号能登录和继续使用会话；`PORTAL_ALLOWED_USERS` 为空时不额外限制用户名，非空时要求在该集合中。
- **登录失败不暴露账号是否存在。** 未知用户名、错误密码、不可登录账号和部署未允许账号都返回相同失败页面。未知用户名仍执行同等代价的 PBKDF2 验证。
- **密码正确后按账号安全设置继续。** 未启用 TOTP 的账号直接建立浏览器会话；已启用账号进入两步验证页；支持 Passkey 的浏览器还会看到独立的 Passkey 登录入口。

### 两步登录衔接

- **启用 TOTP 的账号先获得五分钟登录挑战。** 挑战最多允许五次失败，期间尚未签发普通会话。
- **当前挑战只能由对应用户完成。** 正确动态口令或未使用恢复码会清除挑战并建立普通会话；错误输入减少剩余次数。
- **挑战过期、次数耗尽或 Portal 重启后需要重新输入密码。** 这项临时状态只存在当前进程，既有普通会话不受影响。

### 登出

- **登出只能由已登录浏览器从当前 Portal 页面发起。** `GET /logout` 不执行登出，浏览器通过同源 `POST /logout` 提交操作。
- **登出会撤销该账号此前签发的浏览器会话。** Portal 增加会话版本、清除当前 Cookie 并返回登录页，密码、业务权限和 `dbu_` 个人令牌保持不变。

### 修改自己的密码

- **用户修改密码时需要重新证明自己仍控制账号。** 请求必须来自有效会话并提供正确当前密码；账号启用 TOTP 时还需要当前动态口令或一枚未使用恢复码。
- **新密码需要两次输入一致并符合统一策略。** 新密码不能与当前密码相同，任何校验失败都不会替换服务端摘要。
- **修改成功后其他浏览器退出，当前浏览器继续登录。** Portal 替换密码、清除未使用的重置 Token、撤销该用户全部 `dbu_` 个人令牌、增加浏览器会话版本，并为当前浏览器签发新版本会话。

### 请求密码重置

- **公开重置请求始终返回相同接受提示。** Portal 不说明恢复邮箱是否存在，也不说明账号状态，降低枚举账号的风险。
- **只有唯一匹配、已经建立密码凭据的 active 账号才会收到邮件。** 尚在创建或权限初始化过程中、只有目录记录而没有初始密码的账号不能借此建立登录凭据。 服务还要求 SMTP 邮件发送已经配置，并对同一邮箱和客户端地址执行进程内频率限制。
- **每个用户同时只有最新重置 Token 有效。** 新请求替换旧 Token，默认有效期为三十分钟；日志和持久状态都不保存原文。
- **邮件无法排队或投递时对应 Token 立即作废。** 系统不会保留一份用户从未收到的可用重置凭据。
- **重置链接使用部署配置的 HTTPS 公共地址。** Portal 不使用请求中可能被伪造的 Host 生成邮件链接，重置页也阻止浏览器把 Token 放入 Referer。

### 确认密码重置

- **新密码通过校验后，Portal 才使用并作废这枚一次性 Token。** 两次新密码不一致或不符合策略时，Token 在期限内仍可重新使用。
- **有效 Token 只能成功使用一次。** 未知、过期和已使用 Token 返回统一失败；Token 成功使用后，服务再次确认账号仍允许登录且正式密码凭据仍存在；曾经发出的 Token 也不能为未完成创建的账号建立首个密码。
- **重置成功会撤销全部旧会话并要求重新登录。** Portal 同时撤销该用户全部 `dbu_` 个人令牌，在重置页要求重新登录；密码变化通知失败时不暴露内部存储信息。

### 安全失败与状态边界

- **密码、账号、会话密钥和重置 Token 分别由自己的存储组件负责。** 账号和凭据记录通过身份数据库对应关系表及事务提交；会话密钥由部署配置的密钥文件提供。跨组件操作按固定顺序完成各项写入。
- **安全状态不可读时操作停止。** Portal 不会因密码数据库、签名密钥或 Token 状态损坏而跳过验证。

## 密码与会话机制

### 密码策略和存储

密码长度必须为 12 至 256 个字符，并覆盖小写字母、大写字母、数字和符号中的至少三类。PortalCredentialStore 使用随机 16 字节盐和 260000 次 PBKDF2-SHA256 计算摘要。PBKDF2-SHA256 是一种故意增加猜测成本的密码派生算法；验证时使用恒定时间比较，降低通过耗时差异推断摘要的风险。

密码数据库只允许服务账号读取。修改一名用户密码时，存储组件在数据库事务内保留其他用户记录并更新目标行，明文密码不会进入账号资料和审计日志。

### 会话 Token

SessionAuthority 是签发和验证浏览器会话的服务端组件。版本 1 Token 使用 HMAC-SHA256 签名，包含用户名、签发时间 `iat`、过期时间 `exp`、账号会话版本和随机 nonce。HMAC-SHA256 是使用服务端密钥验证内容未被修改的签名算法；nonce 是每次签发时生成的随机值。

会话默认最长有效三十天，部署可以调整期限。签名密钥至少为 32 字节；Web 在密钥缺失时安全生成并持久保存新的随机密钥，Router 只读该密钥。替换或重新生成密钥会使原签名会话失效。验证会检查 Token 结构、版本、签名、时间、用户名、账号 active 状态和当前会话版本。

`db_session` Cookie 使用 `HttpOnly`、`Path=/` 和 `SameSite=Lax`。这些属性分别阻止页面脚本读取、让 Cookie 作用于 Portal 全站，并限制跨站请求携带；跨站顶层安全导航仍可携带 Lax Cookie。可信 HTTPS 请求还会增加 `Secure`。登出使用相同作用域和零有效期清除 Cookie。

### 两步登录 Cookie

登录挑战通过独立 Cookie 交付，使用 `HttpOnly`、`SameSite=Strict` 和 `Path=/login`，只在登录路径中使用。可信 HTTPS 请求还增加 `Secure`。挑战保存用户名、五分钟期限和剩余尝试次数，成功或失效后清除。

## 登录、登出与密码修改接口

`GET /login` 返回公开登录页。未登录浏览器用 `POST /login` 提交用户名和密码；统一登录失败会重定向到 `/login?error=1`。启用 TOTP 的账号密码验证成功后进入 `/login/verify`，完成动态口令或恢复码后才签发 `db_session`。

`GET /logout` 返回 405，并声明只允许 POST。`POST /logout` 要求有效会话和同源请求，成功后增加账号会话版本、清除 Cookie 并返回登录页。

`POST /api/account/password` 要求有效会话和同源请求。`current_password` 是当前密码字段，`new_password` 是新密码字段；新密码还要与确认值一致。启用 TOTP 时需要动态口令或恢复码。成功后记录 `password_changed` 审计事件。

## 密码重置接口

| 用户动作 | 接口 | 请求与结果 |
| --- | --- | --- |
| 请求重置邮件 | `POST /api/auth/password-reset/request` | 最多读取 4096 字节，邮箱最多 255 个字符；始终返回 202 接受提示 |
| 打开重置页 | 邮件中的 HTTPS 链接 | 页面采用 `Referrer-Policy: no-referrer`，不向其他站点发送 Token |
| 确认新密码 | `POST /api/auth/password-reset/confirm` | 最多读取 8192 字节；验证两次密码后原子消费 Token |
| 修改已登录账号密码 | `POST /api/account/password` | 要求有效会话、同源、当前密码和账号所需第二因子 |

重置请求按规范化邮箱每十五分钟最多三次，并按客户端地址每十五分钟最多十次；计数只存在当前进程，重启后清空。服务端为 Token 生成高熵原文，只保存 SHA-256 摘要。邮件工作由有界后台队列执行。

重置邮件使用部署明确配置的固定 HTTPS 公共地址：优先取显式 `PORTAL_PUBLIC_ORIGIN`，未设置该环境变量时取站点配置 `deployment.auth.public_origin`。未配置或显式空值表示没有公共地址，邮件恢复保持未就绪，不猜测当前站点。确认重置成功后记录 `password_reset_completed` 审计事件，并增加账号的 `session_version`。接口不会在重置页面为用户直接签发新的登录会话。

## 状态所有权与恢复

| 状态 | 负责组件 |
| --- | --- |
| 账号状态、恢复邮箱、会话版本 | PortalAccessStore |
| 密码摘要 | PortalCredentialStore |
| 会话签名密钥和 Token 验证 | SessionAuthority |
| 密码重置 Token | PasswordResetStore |
| 邮件排队和投递 | PortalPasswordResetService 与 SMTP mailer |
| 两步登录挑战 | Portal Web 当前进程 |

修改密码和确认重置跨越多个状态所有者。系统先验证所有输入，再依次提交密码、Token 清理、会话撤销和审计；当前不承诺跨业务操作单一事务。未来可以增加单设备会话 ID，同时保留 `session_version` 作为账号总撤销开关。新的登录方式仍应签发同一种浏览器会话，使业务页面无需识别用户采用了哪种认证方式。

密码摘要、账号资料、Passkey、TOTP 和重置摘要由 `portal_identity` 中对应的关系表保存，`identity_store.IdentityState` 提供各领域的事务入口；Session 校验按数据库修订刷新有效账号集合。

会话签名密钥和 TOTP 加密密钥的路径由 `portal_session_auth` 统一解析。显式环境变量 `PORTAL_SESSION_SIGNING_KEY_PATH`、`PORTAL_TWO_FACTOR_KEY_PATH` 优先于站点配置 `deployment.auth.session_signing_key_path`、`deployment.auth.two_factor_key_path`；环境变量存在但为空时也不回退站点配置。两级均未配置时分别使用 `portal_paths.path("secrets_dir") / "session_signing.key"`、`"two_factor.key"`。默认 SessionAuthority 与 Web 使用同一解析入口。配置路径不搬移旧密钥、不改密钥内容或认证算法；部署已有密钥的位置应显式保留。

## 认证尝试窗口

密码和第二因子按规范化用户名、验证阶段共享数据库尝试窗口：每阶段在 900 秒内最多接受十次尝试，成功后清除该阶段记录。登录和敏感操作再认证共用这些窗口；重新开始登录挑战不清除第二因子失败累计。每次检查清理过期记录，总容量有界，达到容量时拒绝创建新窗口。公开重置请求继续按邮箱与客户端地址在进程内限速，清理过期键并限制表容量。
