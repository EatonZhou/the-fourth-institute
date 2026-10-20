---
kb_id: decision_fb910c43ab07
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 15
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T19:43:45Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_865c3c24f251
importance: 10
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/tests/cases/test_resident_hatg_credentials.py
  - ${code_root}/management_portal/society_service/resident_workspace_domain.py
  - ${code_root}/scripts/resident_hatg_instruction.py
  - ${code_root}/management_portal/page_api_auth.py
  - ${code_root}/management_portal/asgi.py
  - ${code_root}/management_portal/agent_token_auth.py
  - ${code_root}/management_portal/society_service/resident_hatg_credentials.py
  - ${code_root}/management_portal/serve_management_portal.py
  - ${code_root}/management_portal/manage_agent_tokens.py
  - ${code_root}/tests/cases/test_agent_token_auth.py
  - ${code_root}/tests/cases/test_security_wave4.py
  - ${code_root}/tests/cases/test_portal_security_repair_wave6.py
  related:
  - decision_ab9880dc4c9d
  - decision_6eac174d85bf
  - decision_b820faf42218
  - decision_898b8911a2ff
  - decision_3fe3cca8fd95
  - decision_0f57656a2acd
  - decision_b6e74d78f7b6
  - decision_4a9ba5dfada6
  - decision_d918ebebc062
  supersedes: []
  derived_from: []
---

# Resident Bearer 令牌与机器接口认证

Resident 是在 Portal 中登记、持续工作的 Agent 身份。它没有浏览器登录页面，但有时需要在自己的工作进程之外调用 Portal：在命令行中用脚本批量执行 ISA 指令，或者调用 Test、Paper、WALL 等页面自己的后端接口。Portal 为每个 Resident 签发一个 **Bearer 令牌**，这些调用用它证明“我是哪个 Resident”。本 Design 说明令牌怎样签发、保存、使用和停用。令牌只负责认证；Resident 能做什么由权限系统按 Resident 自己的权限判定（decision_b820faf42218），认证与领域业务边界继续生效，见 decision_3fe3cca8fd95。

Resident 在一轮工作中通过 Harness 工具调用 ISA 指令时不需要令牌：执行层知道当前在运行哪个 Resident，直接把它作为调用者（decision_898b8911a2ff）。令牌只用于从工作进程之外发起的 HTTP 请求。

## 基本概念

**Bearer 令牌**是一段高熵随机字符串。“Bearer”（持有者）表示任何拿到原文的人都能用它发起请求，因此它必须像密码一样保管。调用方把它放在 HTTP 请求头 `Authorization: Bearer <令牌>` 中。

**令牌登记表**是 Portal 保存的服务端记录，位于 PostgreSQL 表 `portal_identity.agent_tokens`，Resident 令牌以 Resident ID 登记，RunScript 运行另有独立记录。表中保存验证摘要、调用者、启用状态与托管来源；脚本记录还保存 caller_id、required_scope=instructions、expires_at 与运行期 lease_path。登记表不保存令牌原文。字段固定为 `agent_id` 主键，以及 `token_lookup`、`token_hash`、`enabled`、`managed_by`、`caller_id`、`required_scope`、`expires_at`、`lease_path`；不再使用 `config_json`。`token_lookup` 支持候选定位，`token_hash` 承担验证，两者职责不同，均保留。写入拒绝未定义字段。

**令牌文件**是 Portal 为 Resident 写出的令牌原文，权限 0600。根目录优先取 `PORTAL_RESIDENT_HATG_TOKEN_ROOT`，默认是 `portal_paths.path("secrets_dir") / "resident_hatg"`，文件名为 `<resident_id>.token`。签发端与命令行客户端使用同一规则；已有站点通过配置保留真实旧路径，不搬移或重签令牌。

**托管记录与手工记录**：Portal 自动签发的记录标记为托管（`managed_by: portal_resident_hatg`），Portal 负责首次创建和删除身份时停用它；管理员用命令行工具创建的是手工记录，Portal 不会轮换它。

## 令牌在哪里使用

| 使用场景 | 请求地址 | 除令牌外还需要的请求头 |
| --- | --- | --- |
| 命令行客户端 `scripts/resident_hatg_instruction.py` 执行 ISA 指令 | `/api/instructions/execute` | `X-Portal-Execution-ID`（可选，当前执行 ID，只用于记账） |
| 调用页面后端接口：Test（`/test/api/…`）、Paper（`/paper/api/…`）、WALL | 各页面接口 | `X-Portal-Lineage-ID`（Resident 所在的 Lineage，必填） |

同一批地址也接受人类用户的登录会话和个人访问令牌（以 `dbu_` 开头，见 decision_6eac174d85bf）；Portal 先按会话、再按个人访问令牌、最后按 Resident 令牌识别调用者。

## 设计点

### 身份

- **一个令牌只代表登记表中对应的那个 Resident。** 认证成功后调用者就是这个 Resident ID；请求头、Lineage 名称、工作目录和提示词都不能把它换成别的身份。
- **令牌本身不附带任何权限。** 操作按 Resident 自己在权限表中的权限，以及对象状态与数据完整性规则判断（decision_b820faf42218）。
- **调用页面接口时，Resident 必须声明所在的 Lineage，而且确实属于它。** Portal 用 `X-Portal-Lineage-ID` 查询 Lineage 当前成员，Resident 不在其中时返回 403。这项检查只确认调用者身份；操作其他 Lineage 中的对象仍由权限系统与领域业务规则判断。
- **命令行执行指令时只附带本轮的执行 ID。** 客户端从 `PORTAL_EXECUTION_CONTEXT_FILE` 指向的文件读取 `execution_id`（只读取规范环境键，不回写环境），放入 `X-Portal-Execution-ID`，用于把调用记入这次执行；它不携带权限，也不改变调用者身份。

### 签发与维护

- **Portal 只为没有登记记录的 Resident 自动发放令牌。** 已有记录的启用状态和摘要保持不变；已启用且文件匹配时返回文件位置，停用、文件缺失或摘要不匹配时不发放。管理员停用或轮换不会被后续执行覆盖。
- **手工记录不会被 Portal 轮换。** 同一 Resident 已有手工记录时，只要令牌文件与之匹配就继续使用；不匹配时 Portal 不签发，命令行客户端提示尚未获得凭证。
- **令牌原文只在签发时出现一次。** 登记表、列表输出和日志只包含 Resident ID 与启用状态。

### 停用

- **删除 Resident 会停用它的令牌。** 删除提交后，Portal 把登记记录设为未启用并删除令牌文件；记录保留用于审计。
- **管理员可以随时停用或轮换任一令牌。** 停用和轮换从下一次请求起生效，不回滚已经开始处理的请求。
- **Resident 令牌没有到期时间。** 撤销依靠停用、轮换或删除 Resident。RunScript 令牌有 expires_at，且仅在运行租约文件存在时接受；它代表发起 User 时，每次 ISA 请求还须确认该 User 仍活跃。
- **Resident 离开 Lineage 后，声明原 Lineage 的页面接口请求被拒绝。** 命令行执行指令不检查 Lineage 成员关系，身份仍然有效，能做什么由权限系统与领域业务规则判断。

## 认证过程

```text
客户端发送 Authorization: Bearer <令牌>
  → 去掉首尾空白，计算 SHA-256 摘要，按摘要在登记表中找候选记录
  → 跳过未启用的记录，核对脚本有效期、作用域与运行期租约
  → 用 PBKDF2-SHA256 重新计算慢哈希并作恒定时间比较
  → 成功：形成登记的 Resident 或 RunScript 发起者身份；User 发起者须仍为 active
  → 页面接口：再核对 X-Portal-Lineage-ID 中的成员关系
  → 交给权限系统与拥有服务的业务规则判断、执行
```

令牌认证失败时不形成调用者身份，返回 401。身份已确认后，页面接口声明的 Lineage 与成员关系不符返回 403；随后仍需通过权限与领域检查。认证方案名 `Bearer` 不区分大小写。

## 令牌怎样生成和保存

令牌原文由密码学安全随机源生成（32 字节，URL 安全的 Base64 编码）。登记表保存两种校验材料：

| 校验材料 | 形式 | 用途 |
| --- | --- | --- |
| 查找摘要 | `sha256:` 加原文的 SHA-256 十六进制 | 快速定位候选记录，不能单独完成认证 |
| 慢哈希 | PBKDF2-SHA256，16 字节随机盐，26 万次迭代 | 确认提交的原文确实属于这条记录 |

登记表的读写使用数据库事务，并对同一 Resident 加事务级锁，避免并发签发互相覆盖。令牌文件先写入临时文件，设置 0600 权限后原子替换。

## 管理命令

管理员在已配置数据库连接的 Portal 运行环境中使用 `python -m management_portal.manage_agent_tokens`：

| 子命令 | 效果 |
| --- | --- |
| `set <resident_id>` | 生成新令牌，写入登记表并启用，在终端显示一次原文。原有令牌立即失效。 |
| `disable <resident_id>` | 把记录设为未启用，保留记录。 |
| `list` | 列出 Resident ID 与启用状态，不显示令牌或摘要。 |

登记与维护的运维说明见 `decision_ab9880dc4c9d`。

## 相关 Design

- 认证与授权的整体结构：decision_3fe3cca8fd95。
- ISA 指令的调用入口与命令行客户端：decision_898b8911a2ff。
- 权限系统与执行者自身权限：decision_b820faf42218。
- Resident 身份与 Lineage 成员关系：decision_0f57656a2acd。
