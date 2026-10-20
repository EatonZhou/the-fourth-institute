---
kb_id: decision_3fe3cca8fd95
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 30
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T19:43:45Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_865c3c24f251
importance: 10
metaness: 3
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/resources/rpc_auth.py
  - ${code_root}/management_common/principal.py
  - ${code_root}/management_portal/user_enrollment.py
  - ${code_root}/management_portal/asgi.py
  - ${code_root}/management_portal/page_api_auth.py
  - ${code_root}/management_portal/permission_system/__init__.py
  - ${code_root}/management_portal/portal_access_control.py
  - ${code_root}/management_portal/portal_session_auth.py
  - ${code_root}/management_portal/portal_passkeys.py
  - ${code_root}/management_portal/portal_two_factor.py
  - ${code_root}/management_portal/portal_password_reset.py
  - ${code_root}/management_portal/agent_token_auth.py
  - ${code_root}/management_portal/resources/access.py
  related:
  - decision_b820faf42218
  - decision_600ae27b0538
  - decision_22227d0ad075
  - decision_6eac174d85bf
  - decision_5bc3fdfc7eec
  - decision_85222f2d316b
  - decision_96a887d83e8a
  - decision_898b8911a2ff
  - decision_6c3d74a9bc9d
  - decision_34158fdd4078
  - decision_3bce8b455a3c
  - decision_ad7bdfde49ef
  - decision_626fb02b3894
  - decision_fb910c43ab07
  - decision_30528aff7035
  - decision_ae422b92c6f3
  supersedes: []
  derived_from: []
---

# Portal 身份认证与领域授权架构

Portal 的每个请求都要回答两个问题：发出请求的是谁，以及这个身份能不能对目标对象做这件事。前者叫**认证**，后者叫**授权**。Portal 先用登录会话、个人访问令牌、Resident 令牌或受控的本机连接确认身份，再由权限系统判定这个身份能否执行该指令，最后由负责目标对象的服务检查业务规则并执行。本 Design 说明这条链路的整体结构；各环节的细节由相邻 Design 负责，文中在对应位置给出 ID。

## 先认识几个概念

**Principal（主体）** 是 Portal 能为之授权、并把操作记在其名下的稳定身份。Portal 有三类主体：

| 主体 | ID 形式 | 例子 |
| --- | --- | --- |
| User：人类用户 | 用户名，1–80 个 ASCII 字母、数字或 `_ . @ + -` | `TFI portal` |
| Resident：持续工作的 Agent 身份 | `resident_` 加 12 位小写十六进制 | `resident_1a2b3c4d5e6f` |
| 服务：Portal 自身的基础设施 | 固定登记的名称 | `portal`、`ai-socket-relay` |

用户名不能以 `@` 开头，不能以 `resident_` 开头，也不能与服务名称相同，因此三类 ID 不会互相冒充。认证成功后形成的当前主体也称为 **actor**；后续服务只接收已经确认的 actor，不从页面参数、显示名称、工作目录或提示词中推断身份。

**身份凭据**是证明“我是谁”的材料，例如密码、Passkey、登录会话和令牌。它们只用于认证。本 Design 中的“凭据”都指身份凭据；权限系统中的“临时凭证”是一个主体借给另一个主体的权限，是另一回事，见 decision_b820faf42218。

**权限系统**回答“这个主体能否对这个资源执行这个动作”。它把主体、动作、资源分别放进 P、A、R 三棵树，用权限表中的 permit/deny 表项判定，架构与开关见 decision_b820faf42218，默认模板见 decision_600ae27b0538。

**ISA 指令**是人和 Agent 操作 Portal 对象所用的具名操作，例如 `GetUser`、`ForkResident`，统一提交到 `/api/instructions/execute` 或由执行层直接执行，见 decision_898b8911a2ff。Resident 工作时，执行层把这些指令作为 **Harness 工具**（执行层提供给模型、可在一轮工作中调用的工具）交给模型。

**拥有服务**是保存某类对象并执行其修改的 Portal 服务：ResourceService 拥有资源与权限实例；SocietyService 拥有 Lineage 与 Resident；Portal Web 拥有账号、会话和页面偏好。它们在执行操作前调用授权判断，并继续检查自己的业务规则。

## 身份从哪里来

```text
浏览器登录会话 db_session ──────────┐
User 个人访问令牌（dbu_ 前缀）──────┤→ User
Resident 令牌（命令行、页面接口）──┐│
Resident 执行中的 Harness 工具 ────┼┘→ Resident
Lineage 运行中的 Effect ───────────┘   （使用这次运行的发起者）
本机 Unix socket 与签名 RPC ───────────→ 服务转交已确认的 actor
                         ↓
          权限系统判定 → 拥有服务检查业务规则 → 执行
```

| 调用方式 | 使用的身份凭据 | 形成的 actor | 细节 |
| --- | --- | --- | --- |
| 浏览器中的人类用户 | 登录后浏览器携带的 `db_session` 会话 Cookie | User | decision_3bce8b455a3c |
| 人类用户的命令行或外部助手 | `Authorization: Bearer` 中的个人访问令牌，以 `dbu_` 开头 | User | decision_6eac174d85bf |
| 正在工作的 Resident 调用 ISA 指令 | 无需令牌；执行层知道当前在运行哪个 Resident，直接把它作为调用者 | Resident | decision_898b8911a2ff |
| Resident 的命令行客户端和页面接口 | Portal 为 Resident 签发的 Bearer 令牌 | Resident | decision_fb910c43ab07 |
| Lineage 中的 Effect、Function Block 运行 | 运行记录中的发起者 | 发起者（User 或 Resident） | decision_ae422b92c6f3 |
| Portal 各服务之间 | 本机 Unix socket，ResourceService 另要求签名 | 转交入口已确认的 actor | 下文“后台服务” |

## 设计点

### 先认证，再授权

- **请求只使用经过验证的身份。** 浏览器请求从有效会话取得用户名，令牌请求从令牌的服务端记录取得 User 或 Resident ID，服务之间的请求转交入口已经确认的 actor；请求参数、显示名称和工作目录不能改写它。
- **身份确认后，每次操作仍单独授权。** 登录或令牌有效只说明是谁；能否读取、修改或执行由权限系统和拥有服务的业务规则在每次请求时判断。
- **认证与授权都在修改之前完成。** 身份无效时请求在入口被拒绝（HTTP 401）；身份有效但无权操作时，拥有服务在写入任何状态前拒绝（HTTP 403，或对读取返回“不存在”）。

### 人类用户登录与会话

密码、两步验证和 Passkey 登录最终都建立同一种 `db_session`。业务页面接收已验证的 User 身份，并按账号当前状态和会话版本复核；登录方式不改变业务授权。

登录、Cookie、同源校验、密码与重置流程的权威协议是 decision_3bce8b455a3c；TOTP 与恢复码见 decision_ad7bdfde49ef，Passkey 见 decision_626fb02b3894，敏感操作的当前凭据要求见 decision_6c3d74a9bc9d。此处只说明这些认证组件与业务授权的连接。

### 令牌

- **个人访问令牌代表签发它的 User。** 用户在 Settings 中创建，调用 `/api/instructions/execute` 或 Test、Paper、WALL 等页面后端接口时，身份就是这位 User，权限与网页登录相同。令牌原文只由用户保存，Portal 只保存摘要。
- **Resident 令牌只代表登记记录中的 Resident。** 它用于 Resident 在命令行中调用 ISA 指令，以及调用 Test、Paper、WALL 等页面自己的后端接口；令牌证明身份，操作仍受权限系统及领域数据校验约束。见 decision_fb910c43ab07。
- **Resident 调用页面接口时必须说明所在的 Lineage。** 请求头 `X-Portal-Lineage-ID` 给出 Lineage ID，Portal 确认该 Resident 当前确实属于这个 Lineage 后才继续。这项检查确认调用者身份，目标对象的权限仍另行判断，调用者可以操作其他 Lineage 中的对象。

### 后台服务

- **服务之间通过本机 Unix socket 通信，并转交已认证的 actor。** 各拥有服务的 `instruction.execute` 接口（decision_96a887d83e8a）接收入口已经确认的调用者身份；ResourceService 的 RPC 另外要求用本机签名密钥签名，签名有效期 30 秒。
- **服务来源本身不授予业务权限。** 通过内部连接到达的请求仍按其中转交的 User 或 Resident 授权；服务名称 `portal` 只用于署名系统产生的消息和通知，不能作为 Lineage 自动运行的业务主体。

### 权限判定与管理边界

未知指令名由目录拒绝。指令声明给出判定槽及其参数对象，权限方案把这些 A 元素归入树中的节点；未分类元素仍落在默认位置参与判定，不能因没有映射而放行。声明、方案与引擎的分工以 decision_b820faf42218 §3.1–3.5 为准。判定只看执行者自身权限，不继承消息发起者的权限；额外授权使用临时凭证。身份、对象状态、版本和数据完整性检查仍然保留。

账号写操作统一经过 ISA，各指令的判定对象和默认分类见 decision_6eac174d85bf，默认方案见 decision_600ae27b0538。管理员名单、首个账号与权限开关的保底规则集中在 decision_b820faf42218 §3.1；普通分类、资料和账号状态操作不能一概视为管理员专属。本人修改密码仍须验证当前凭据。Portal 管理页面按调用者的 `access.allows` 和管理员身份呈现入口，具体按钮按所调用指令授权，页面不代替服务端检查（decision_30528aff7035）。

### 业务事实与授权事实

Lineage、Resident、Workspace、Family steward 与聊天室成员等事实由各领域保存，权限实例据此放置对象并展开关系规则。事务型对象在同一事务内同步；没有可共享事务的资源在提交后同步并由补齐修复遗漏。各类事实的唯一所有者、写入时机和恢复边界见 decision_b820faf42218 §5.7。

### 撤销、失败与审计

- **身份或权限变化从下一次请求起生效。** 会话撤销、令牌停用和权限变化不回滚已经完成的操作，也不改写已有对话和执行记录。
- **读不到账号、令牌或授权记录时拒绝请求。** Portal 不在这些记录损坏时退回到调用方自报的身份，对外错误也不暴露存储细节。
- **ISA 授权读取遇到 ResourceService 连接不可用时，在业务执行前返回可重试失败。** 回执为 `instruction.owner_unavailable`、`retryable=true`、`commit_state=not_committed`，不向调用方暴露 socket 路径或原始传输诊断。此处只允许调用方在服务恢复后重新请求，不自动重发业务修改；明确拒绝、无效授权结果与未知异常继续拒绝，不能借此放宽权限。
- **已完成迁移的授权存储可以与正常读取并行重新打开。** 重新初始化不重复申请结构迁移所需的排他锁；旧存储缺少字段时仍完成必要迁移并保留已有记录。初始化失败仍拒绝请求，不降低身份或权限检查。
- **管理员的账号操作和维护操作写入审计。** 审计保存操作者、动作、目标和时间，不保存密码、令牌、两步验证种子等秘密。

## 授权在哪里检查

1. ISA 内核在执行前按模板的指令映射把一次调用拆成若干 (执行者, 动作, 资源) 请求，逐个交给 PAR 引擎判定。拒绝的读操作隐藏对象存在性。
2. 页面和领域入口检查身份、对象状态与业务数据；账号写入统一调用 ISA。领域内部检查具体操作时（例如 Workspace 文件读写），同样通过权限端口询问对应指令及参数，不另问抽象等级。
3. 资源列表与搜索隐藏个人 AMA 的私有记录；Mailbox、收藏、活跃 Resident 等聚合视图按调用者能读的条目过滤；读取单个资源正文时按模板要求的 viewer 判定。附件路径等数据完整性约束保留。

以上三处都只通过权限端口 `management_portal/permission_system` 提问。管理员在执行设置中关闭权限系统后，端口改用开放实现，不加载也不调用 PAR 代码；身份认证、防冒充、个人秘密和个人 AMA 的归属照常检查，见 decision_b820faf42218。

## 组件与状态

| 组件 | 保存或负责的内容 |
| --- | --- |
| PortalAccessStore（Portal Web） | 账号资料与状态、会话版本号；表 `portal_identity.user_accounts` |
| PortalCredentialStore（Portal Web） | 密码摘要与密码强度规则（decision_3bce8b455a3c） |
| SessionAuthority（Portal Web） | 签发与验证 `db_session` |
| 两步验证与 Passkey 存储（Portal Web） | TOTP 种子、恢复码、Passkey 公钥 |
| UserEnrollment（Portal Web） | 邀请与 User 个人访问令牌摘要 |
| AgentTokenStore | Resident 与 RunScript 令牌的校验材料、启用状态及脚本有效期；表 `portal_identity.agent_tokens` |
| 权限系统（ResourceService 内） | 当前方案、P/A/R 树、权限表和临时凭证；管理员分类与创建者事实由领域记录提供；数据库 schema `permission` |
| SocietyService | Lineage 归属、Resident 成员关系与自动运行身份 |

## 一次请求的判断过程

```text
读取身份凭据（会话 Cookie、Bearer 令牌或内部连接转交的 actor）
  ↓
验证签名、摘要与账号或令牌的当前状态
  ↓
形成 User、Resident 或服务 actor
  ↓
拥有服务确定真实目标对象和所需操作
  ↓
权限系统按模板拆分请求并判定，领域检查业务边界
  ├─ 拒绝：在任何修改前返回错误
  └─ 允许：拥有服务检查业务规则，执行并提交
```

Resident 一轮工作中调用的指令以 Resident 自己为执行者判定。执行层只向 Resident 的命令行客户端提供本轮执行的 `execution_id`，用于把调用记入这次执行；它不携带权限，也不改变调用者身份。

## 扩展方式

新增 ISA 必须进入指令目录；在指令声明与当前方案中写明判定槽及其分类；未分类的要求使用 A/R 树默认节点参与权限判定，显式免检必须由方案分类表达。业务子对象仍由拥有服务检查状态、成员和本人范围。新增的权限判断经权限端口提问，并在 PAR 实现和开放实现中各给出答案。

