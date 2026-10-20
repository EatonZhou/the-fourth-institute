---
kb_id: decision_3dd52c0692c1
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 18
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T08:43:43Z'
  updated_by:
    user: ''
    agent: resident_2e9ea0072551
importance: 9
metaness: 2
ai_generated: true
links:
  source:
  - ${code_root}/management_portal/execution/account_registrations.py
  - ${code_root}/management_portal/execution/account_hosting.py
  - ${code_root}/tests/cases/test_resident_provider_account_inheritance.py
  - ${code_root}/tests/cases/test_execution_accounts.py
  - ${code_root}/management_portal/society_service/resident_runtime_domain.py
  - ${code_root}/management_portal/codex_capabilities.py
  - ${code_root}/management_portal/execution/account_adapters.py
  - ${code_root}/management_portal/execution/accounts.py
  - ${code_root}/management_portal/execution/backends.py
  - ${code_root}/management_portal/execution/scheduler.py
  - ${code_root}/management_portal/agent_status_backend.py
  - ${code_root}/management_portal/static/home.js
  - ${code_root}/management_portal/index.html
  - ${code_root}/tests/cases/test_execution_layer.py
  - ${code_root}/tests/cases/test_codex_account_status_sections.py
  related:
  - decision_7956d7cb4bde
  - decision_d40f59f5508f
  - decision_4527448ad07f
  - decision_f1331d57be89
  - decision_91eb3626d323
  - decision_8ac0b5a6c0e5
  - decision_e587fe1c472e
  - decision_e30897dd3dc3
  - decision_4dea0db1f45a
  - decision_c47a13e8b962
  - decision_3e8c2a7f91d4
  supersedes: []
  derived_from: []
---
# Codex 账号路由、会话归属与故障切换

Portal 可登记多个 Codex 登录账号，每个账号有独立的账号目录和 Codex App Server 实例。Resident 开始一个新的 Codex 会话时，Portal 要决定用哪个账号；首选账号的 App Server 连不上时，可以换到事先登记的备用账号；会话一旦在某个账号上建立，之后的每一轮都回到这个账号。

本 Design 说明 Codex 账号怎样登记、新会话怎样选择账号、什么情况下换到备用账号，以及已有会话的归属和运行设置的校验。所有 Provider 共用的执行账号目录、额度保护和调度设置见 decision_d40f59f5508f；Home 页面怎样展示账号额度见 decision_4dea0db1f45a；App Server 进程怎样按账号部署见 decision_e587fe1c472e；Codex 会话协议见 decision_3e8c2a7f91d4。

## 基本概念

- **执行账号**：ExecutionService 中一个可以承担执行的账号，用全局唯一的 `account_id` 标识（decision_d40f59f5508f）。Codex 账号沿用登记中的账号 ID，例如 `codex_one`。
- **路由 ID（`backend_route_id`）**：Codex 内部区分账号的名字，对应账号登记的 `route_id`，例如 `one`。它保存在会话记录里，Codex 客户端据此连接对应账号的 App Server。
- **Codex 账号路由**：一个 Codex 登录账号在 Portal 中的配置，包括它的账号目录（`CODEX_HOME`）、App Server 的 Unix socket、初始并发上限和备用路由列表。
- **容量分区**：调度器为每个账号单独计算并发和派发间隔的单位。Codex 账号的容量分区键是 `codex:<路由 ID>`，例如 `codex:one`。
- **会话归属**：Portal 会话记录中保存的 Provider、路由 ID 和原生会话（Codex 中称为 thread）ID。归属一旦建立，这个会话后续的每一轮都回到同一个账号。

## Codex 账号与执行账号

- **对外账号 ID 与路由 ID 由登记关联。** 例如 `codex_one` 对应路由 `one`，传入任一种名字都找到同一个账号；容量分区沿用 `codex:<路由 ID>`，已保存的并发和派发间隔跟着账号走。
- **每个请求在进入队列前确定账号。** Codex 请求按会话记录中的路由 ID，或按下文的新会话规则得到路由，再换算成执行账号；这个账号决定容量分区和用量归属。
- **已有会话只能用它自己的账号。** 会话已经绑定 thread 时，请求必须带着会话记录中的路由 ID。换账号要通过 `RestartSession` 迁移会话（decision_d19b9909a674）。
- **用量按实际执行的账号记录。** 统计口径取执行时真正使用的路由（包括换到备用路由后的路由），而不是 Resident 当前设置中的账号，因此会话迁移后历史用量仍记在原账号上。统计规则见 decision_f1331d57be89。

## Codex 账号路由

### 账号登记与路由投影

Codex 账号由 PostgreSQL 的 `execution_accounts.registrations` 保存，账号登记和管理入口见 decision_7956d7cb4bde。`CodexRoutes` 从登记派生路由；账号数量、默认账号、备用顺序和初始容量都以当前登记为准，不再由仓库 YAML 定义。

| 字段 | 含义 |
| --- | --- |
| `account_id` | 执行账号的全局标识，也是服务实例和账号目录的标识 |
| `route_id` | Codex 客户端和已有会话使用的路由标识 |
| `enabled` | 是否接收新工作 |
| `max_concurrency` | 初始并发上限；运行中生效的容量由调度设置管理 |
| `config.is_default` | 未显式指定路由时使用的默认账号标记 |
| `config.fallback_account_ids` | 按顺序排列的备用账号 ID，路由投影将它们换算为路由 ID |

账号目录与 socket 由站点路径和 `account_id` 推导：`<站点账号根目录>/codex/<account_id>` 与 `<实例根目录>/sockets/codex-<account_id>.sock`，不作为任意主机路径写入登记。

登记入口校验字段类型、标识格式、Provider 内路由唯一性及默认标记唯一性；备用列表不得重复或指向自身。缺少已启用的默认账号时，未显式指定路由的新会话报告错误；未知账号或路由同样报错，不从主机目录补找。候选路由只包含已启用的账号。路由投影按短缓存重新读取登记，已有会话仍以保存的路由归属为准。

运行中的并发、派发间隔和额度保护以 ExecutionService 的调度设置为准，可通过 Home 或 `UpdateProviderAccount` 修改（decision_d40f59f5508f）。已有账号的 ID、容量分区键与会话归属在登记存储变更后保持连续。

### 设计点

- **新会话的首选路由按顺序确定。** 请求显式指定了路由时用它；否则用登记中已启用且 `config.is_default=true` 的账号。后续组件只使用确定下来的路由。
- **Fork 默认使用来源 Resident 当前的账号。** 调用 `ForkResident` 时没有指定 `account`，新 Resident 与来源使用同一个账号，并继承来源的全部运行设置；指定了 `account` 时才换账号：来源已经有会话时，新 Resident 不在新账号上续接来源的 thread，而是在新账号上新建会话，并以 Portal 种子方式把来源上下文复制过去（decision_5bf5bfbd52cb）；运行设置按新账号的模型目录重新校准。Fork 界面只为 Codex 提供账号选择（账号适配层的 `resident_selection`），其他 Provider 的 Fork 默认沿用来源账号。
- **只有“App Server 不可用”才会换到备用路由。** 在首选路由上创建新 thread 时，客户端报告 `CodexAppServerUnavailable`（例如 socket 无法连接），服务才按 `fallback_account_ids` 的顺序尝试下一条路由。登录失效、协议错误、其他运行错误和“创建结果不确定”都停留在原路由并报告失败。
备用账号已进入额度保护或并发已满时，跳过该账号并继续检查下一条备用路线；这些拒绝不改变当前执行的容量归属。只有所有备用账号都不可用时才终止回退并报告失败。

- **备用只展开一层。** 候选列表是首选路由加上它自己的备用路由，不会继续展开备用路由的备用路由，不会沿多条路由递归查找。
- **请求可以关闭备用。** 运行设置 `allow_backend_route_fallback` 为 `false` 时只尝试首选路由；默认为 `true`，显式指定路由的请求同样会附带备用候选。
- **换到备用路由后，调度和记录同步改变。** 服务先通知执行上下文改用新路由并发出 `backend_route_fallback` 事件（记录原路由、新路由和原因），调度器把这次执行从原容量分区移到新分区；成功后实际路由写入执行结果和会话记录。
- **已经开始的会话不再换账号。** 会话已经绑定 thread，或者本轮输入已经被 App Server 接受后，任何错误都不会让它换到另一个账号重发，避免产生重复的 thread、重复的输入和分叉的历史。

```text
请求进入 ExecutionService
        ↓
显式路由，或按全局默认账号得到首选路由
        ↓
调度器在该路由的容量分区中排队
        ↓
在首选路由的 App Server 上创建 thread
        │ 仅当 App Server 不可用
        ↓
依次尝试首选路由的备用路由（发出 backend_route_fallback）
        ↓
实际成功的路由写入执行结果与会话记录
        ↓
这个会话之后的每一轮、恢复、中断和容量统计都使用该路由
```

### 已有会话的归属

会话记录（由 SessionManager 保存，decision_91eb3626d323）是会话归属的唯一依据。普通的下一轮和不换账号的 Fork 都使用记录中的路由；App Server 客户端的缓存、当前工作目录或账号目录扫描都不能改变它。

读取原生 rollout 时，调用方必须提供会话已绑定的账号和合法 UUID 格式的 thread ID。查找只在该账号的 `sessions` 与 `archived_sessions` 内进行，不跨账号推断归属。

### 运行设置的完整组合

Codex 的运行设置以“账号、模型、推理强度（Effort）、速度（service tier）”四项为一个整体校验。可选的模型、每个模型支持的推理强度和速度，都来自目标账号 App Server 的 `model/list` 结果；这份目录按账号缓存 60 秒，标记为隐藏的模型不列出。

- **显式修改时整体校验。** 创建 Resident、修改运行设置或切换账号时，四项组合必须在目标账号的目录中可用；不可用时整个操作失败，不写入其中一部分。
- **自动修正为可用组合。** 执行前，Portal 用当前目录重新检查 Resident 保存的组合；目录已经变化、组合不再可用时，自动换成目标账号的默认模型、该模型的默认推理强度和标准速度，并保存下来，Resident 不会因此停止工作。
- **速度字段总是可见。** 目录没有提供速度选项时，页面仍显示速度字段并标为不可用，保存值和实际请求都使用标准速度。

## 新增 Codex 账号

管理员在 AI Backend 管理页创建 `provider=codex`、`kind=subscription_login` 的账号，或调用 `RegisterProviderAccount`，设置账号标识、路由、初始容量、默认标记和备用账号。登记入口创建派生账号目录；已启用账号通过服务控制面准备对应的模板实例。

订阅登录在页面中完成。实例以该账号目录中的 `auth.json` 为启动条件，登录完成后启动对应的 App Server；登录状态与服务状态分别观察。完整的登记、登录、停用和删除规则见 decision_7956d7cb4bde，实例部署见 decision_e587fe1c472e。Home 使用登记的完整执行账号 ID，展示规则见 decision_e30897dd3dc3。

新增账号不改变已有账号的 `account_id`、路由 ID 和会话记录。
