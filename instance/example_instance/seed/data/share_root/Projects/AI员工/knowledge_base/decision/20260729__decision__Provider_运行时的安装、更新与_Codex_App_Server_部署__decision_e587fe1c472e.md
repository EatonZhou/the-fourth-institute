---
kb_id: decision_e587fe1c472e
type: decision
project: AI员工
created_at: '2026-07-29'
version:
  number: 15
  created_at: "2026-07-29"
  created_by:
    user: ""
    agent: ""
  updated_at: "2026-10-21T18:10:48Z"
  updated_by:
    user: ""
    agent: "resident_977d1a613d44"
importance: 9
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/config/systemd/portal-codex-app-server@.service.in
  - ${code_root}/scripts/portal_service_units.py
  - ${code_root}/scripts/portal_instance_storage.py
  - ${code_root}/management_portal/execution/account_hosting.py
  - ${code_root}/management_portal/execution/account_registrations.py
  - ${code_root}/tests/cases/test_provider_updates.py
  - ${code_root}/scripts/prepare_agent_runtimes.py
  - ${code_root}/scripts/update_provider_runtimes.py
  - ${code_root}/management_portal/execution/provider_updates.py
  related:
  - decision_850e6248d680
  - decision_7956d7cb4bde
  - decision_ab9880dc4c9d
  - decision_6b9e42f1c8a7
  - decision_3948ce9b9443
  - decision_3dd52c0692c1
  - information_de50748841c9
  - decision_3e8c2a7f91d4
  supersedes: []
  derived_from:
  - information_de50748841c9
---
# Provider 运行时的安装、依赖更新与 Codex App Server 部署

Portal 的 Resident 通过几种原生 Provider 工作：Codex、Claude Code、Kimi Code 和 DSH。每种 Provider 都依赖一个由第三方发布的运行时程序包，安装在 Portal 主机上。运维人员需要能回答三个问题：现在各 Provider 运行的是哪个版本，新版本怎样装上并切换过去，切换失败时怎样退回。本 Design 说明运行时的安装位置、版本切换与自动更新，以及 Codex App Server 按账号部署的进程结构。

Codex 会话协议见 decision_3e8c2a7f91d4，Codex 账号选择见 decision_3dd52c0692c1；Claude Code 守护进程和 Kimi Code 服务的进程细节分别见 decision_3948ce9b9443 与 decision_6b9e42f1c8a7。

## 基本概念

- **运行时（runtime）**：Provider 实际运行的程序包，例如 npm 包 `@openai/codex` 提供的 `codex` 命令。
- **版本目录**：每个版本单独安装的目录，位于站点 `deployment.providers.runtime_root` 下，未配置时使用实例 `${staging_root}/provider-runtimes/`，名称带版本号，例如 `codex-cli-0.159.2`。安装新版本只新增目录，不改动正在使用的版本。
- **当前链接**：指向正在使用的版本目录的符号链接。服务只通过当前链接启动运行时，所以切换版本就是改变链接指向并重启服务。
- **验证标记**：版本目录中的 `.portal-validated` 文件，表示这个目录已经装好并通过了启动检查。
- **systemd 用户服务**：Linux 上以部署服务的操作系统账号运行的后台服务，异常退出后由 systemd 自动拉起；Windows 使用安装器的服务控制器，不依赖 systemd 或固定主机用户名。

## 各 Provider 的运行时

| Provider | 来源 | 版本目录 | 当前链接 | 使用它的服务 |
| --- | --- | --- | --- | --- |
| Codex | npm 包 `@openai/codex` 的 `latest` 版本 | `codex-cli-<版本>` | 站点 `providers.codex_binary` 或已解析的 Codex 可执行入口；Windows 使用运行时根中的 `codex-cli-current`，安装布局由平台适配器决定 | 已登记且正在运行的 `portal-codex-app-server@<account_id>.service` 实例 |
| Claude Code | PyPI 包 `claude-agent-sdk`，安装在独立的 Python 虚拟环境中 | `claude-agent-sdk-<版本>` | `claude-agent-sdk-current` | `portal-claude-code.service` |
| Kimi Code | npm 包 `@moonshot-ai/kimi-code` 的 `latest` 版本 | `kimi-code-<版本>` | `kimi-code-current` | `portal-kimi-code.service` |
| DSH | npm 包 `@deepseek-ai/dsh` 与 `@deepseek-ai/dsh-llm` 的 `next` 版本，加上 Portal 仓库中的桥接脚本 `management_portal/dsh_runtime/bridge.mjs` | `dsh-runtime-<dsh 版本>-<dsh-llm 版本>` | `dsh-runtime-current` | 没有常驻服务；每个 DSH 会话按需启动自己的进程 |

实际运行版本以当前链接指向的版本和更新检查输出为准；待切换版本由 `provider-updates.json` 保存。DSH 首次部署的当前链接可以指向仓库中按开放版本声明安装的依赖，后续更新使用独立版本目录。每次启动 DSH 进程时，启动方从自己的 Portal 代码树复制 bridge.mjs 到该会话的 profile 目录；依赖仍通过运行时当前链接解析。Dev 与 Release 各自的桥接脚本随各自代码发布生效，不依赖第三方运行时版本更新。

## 设计点

- **每个 Provider 自己声明怎样更新运行时。** Provider 目录中的 `AgentProvider.runtime_updater` 指向 `provider_updates` 里的一个运行时类（`CodexRuntime`、`ClaudeRuntime`、`KimiRuntime`、`DshRuntime`，都继承 `ProviderRuntime`）。这个类给出发布源上的版本（`available`）、当前链接的版本（`installed`，与前者按同样顺序列出各个包）、怎样安装和检查新版本目录（`stage`）、当前链接与它在新目录中的目标（`link`、`link_target`）、要重启的服务（`services`），以及除 Execution 之外是否还有人在用（`busy`，Claude Code 守护进程的运行中的轮次）。更新检查只遍历声明了运行时的 Provider，不按名称分支；Portal 不安装运行时的 Provider 不声明，也不参与检查（2026-10-11 起）。
- **服务只通过当前链接找到运行时。** 新增版本目录、改动系统里其他位置的同名命令或 `PATH`，都不会改变 Provider 正在运行的版本。
- **一次更新检查依次处理声明了运行时的 Provider。** 对每个 Provider，检查程序读取发布源上的最新版本，与当前链接的版本比较；只有更新的版本才会被安装。运行时由多个包组成时（DSH 的 `dsh` 与 `dsh-llm`），任一包有新版本都会触发安装。已有待切换版本时仍检查发布源；新目标通过安装和启动检查后才替换待切换记录，随后尝试空闲切换。检查或安装失败时保留此前的待切换版本并报告错误。当前版本已经满足发布目标时清除过期的待切换记录。
- **DSH 新版本按目标 LLM 包声明的 Cordis 依赖安装。** 更新器读取目标 `@deepseek-ai/dsh-llm` 的 `peerDependencies.@deepseek-ai/cordis`，将该版本范围写入新版本目录的安装清单，再由 npm 解析并校验依赖。仓库清单负责首次部署且不规定固定版本；更新过程保留正在使用的依赖。发布源查询或依赖安装失败时保留当前运行时并报告错误。
- **新版本先安装到自己的目录并通过启动检查。** 检查内容按 Provider 不同：
  - Codex：`codex --version` 与目标版本一致，并且 `codex app-server --help` 能正常运行；
  - Kimi Code：`kimi --version` 与目标版本一致，并且 `kimi web --help` 仍提供 Portal 依赖的 `--no-open` 选项；
  - Claude Code：虚拟环境中导入的 SDK 版本一致，SDK 自带的 `claude` 程序能输出版本；
  - DSH：将桥接脚本及其同目录的 `.mjs` 模块复制到新版本目录；桥接脚本通过语法检查，并能连同依赖模块加载和导出 Portal 调用的 `apply` 函数。

  通过后写入验证标记。已有验证标记的目录不再重复安装，只重新检查。
- **切换只在这个 Provider 没有进行中的执行时发生。** ExecutionService 中还有该 Provider 的排队、已领取或运行中的执行，或者 Claude Code 守护进程报告有运行中的轮次时，新版本进入“待切换”状态，当前版本继续服务。
- **切换范围由 Provider 的运行时更新器给出。** 检查程序把当前链接原子地换成新版本目录，依次重启更新器返回的服务，并确认它们处于运行状态。Codex 更新器从账号登记查询模板实例，只选择当前 `active` 的实例：已停用登记但仍在服务的进程也更新，原本停止的实例保持停止。账号之间隔离身份与会话，共用运行时版本；验证更新生效时核对被更新实例实际运行的二进制和版本。静态安装器单独渲染共享模板，不把运行中的实例名当作仓库 unit 文件。
- **切换失败时退回原版本。** 任一服务重启失败，检查程序把当前链接改回原来的目录并再次重启这些服务，然后报告错误；安装失败时当前版本保持不变。
- **待切换的版本会在空闲后自动启用。** 出现待切换版本时，检查程序启动临时的 systemd 用户服务 `portal-provider-pending-activation`，每 30 秒尝试切换一次，最多等待 24 小时。该服务通过 `EnvironmentFile` 加载 Portal 的 PostgreSQL 环境文件，默认路径为 `~/.config/tfi/portal-postgres.env`，可由 `PORTAL_POSTGRES_ENV_FILE` 指定。数据库连接用于检查 Provider 的活动 Execution；连接不可用时报告错误并保留待切换状态。连接凭据不写入启动命令参数。
- **同一时间只运行一次检查。** 检查程序持有文件锁 `provider-updates.lock`，位于站点 `deployment.providers.state_root`（未配置时为实例 `${staging_root}/provider-update-state/`）；另一次检查正在运行时，新的调用立即返回 `busy`。待切换的版本和最近一次检查时间保存在同目录的 `provider-updates.json`。

更新检查由 `scripts/update_provider_runtimes.py` 执行一次（加 `--activate-pending` 时只尝试启用待切换的版本）。它输出 JSON 结果，列出每个 Provider 的已安装版本与可用版本、本次启用的 Provider、仍在等待的 Provider 和错误；有错误或检查正忙时以非零状态退出。

Provider 声明了仓库内的运行时包（`AgentProvider.runtime_package`，目前只有 DSH 的 `management_portal/dsh_runtime`）时，第一次部署由 `scripts/prepare_agent_runtimes.py` 按仓库中的开放版本声明安装这个目录，并在这个 Provider 的当前链接不存在时让它指向这个目录；之后的版本切换由更新检查负责。

## 首次接入的环境准备

AI Backend 页面通过 PrepareProviderRuntime 准备所选 Provider。已有可用运行时直接复用，包括开发机现有程序；不强制独立安装或隔离目录，不在接入动作中升级现有可用程序。直连模型 API 无需本地 Agent 程序。缺失的运行时由原 ProviderRuntime.available/stage 和统一激活 owner 完成，沿用更新锁、空闲保护、切换与失败回退，不建立另一套安装器。

任务在服务端异步运行，preparations 记录 preparing、ready、pending、failed 及进程身份。页面可关闭并重新打开观察；进程退出或本进程任务已结束而状态仍 preparing 时标为 interrupted，可重试。准备失败仅返回安全提示；原始安装器错误不进入页面。运行时准备与账号授权、服务可执行性分别判断，实际模型回复由账号接入测试验证。Codex 在程序尚未安装时允许先登记，登录或执行检查时沿原服务控制入口补齐实例；程序路径优先使用站点配置和 PATH 里的现有程序，再用受管路径。Claude 的未运行 daemon 不视为有活跃任务。

## 一次更新怎样进行

```text
读取发布源的最新版本 ──不比当前新──→ 跳过
        │更新
        ↓
安装到新的版本目录，运行启动检查，写入验证标记
        ↓
该 Provider 有进行中的执行？──是──→ 记为待切换，由 portal-provider-pending-activation 每 30 秒重试
        │否
        ↓
原子替换当前链接 → 重启该 Provider 更新器选中的服务 → 确认服务运行
        │失败
        ↓
链接改回原版本并重启服务，报告错误
```

## Codex App Server 的部署

Codex 的每个执行账号由登记中的 `account_id` 标识，并关联供会话使用的 `route_id`。服务控制面为账号管理 `portal-codex-app-server@<account_id>.service` 实例；账号目录、socket 和实例名都从登记标识及站点配置推导，服务数量不固定。

| 对象 | 来源与职责 |
| --- | --- |
| 账号目录 `CODEX_HOME` | `<站点账号根目录>/codex/<account_id>`，保存该账号的凭据、配置与原生会话 |
| Unix socket | `<实例根目录>/sockets/codex-<account_id>.sock`，供客户端连接该账号实例 |
| 模板源 | `config/systemd/portal-codex-app-server@.service.in`，声明所有账号共用的进程设置 |
| 已安装模板 | 用户 systemd 目录中的 `portal-codex-app-server@.service`，由服务控制面按站点路径、Codex 可执行文件和出口配置渲染 |

账号登记入口与登录完成流程经 `account_hosting` 调用服务控制面；服务控制面负责安装模板、启停和移除实例。常规模板安装只安装并 reload 定义，实例的启停属于显式账号生命周期操作。登记停用只阻止新工作派发，保留现有进程；删除必须满足账号空闲条件，具体规则见 decision_7956d7cb4bde。

所有实例使用同一份模板和当前 Codex 运行时，启动命令为 `codex app-server --listen unix://<该账号 socket>`；可执行文件路径在安装时解析。每个实例有独立的账号目录与 socket，登录身份、连接和原生会话不会在账号间混用。

| 设置 | 值与作用 |
| --- | --- |
| 启动条件 | 对应账号目录存在 `auth.json`，所有账号遵循同一条件 |
| 启动前 | 创建 socket 父目录并删除自己的旧 socket 文件 |
| `Restart` / `RestartSec` | `always` / 2 秒 |
| `TimeoutStopSec` | 15 秒 |
| `MemoryHigh` / `MemoryMax` / `MemorySwapMax` | 32 GiB / 36 GiB / 1 GiB |
| 网络出口 | 渲染器按站点配置的 `PORTAL_PROVIDER_EGRESS_ENV_FILE` 生成 `EnvironmentFile`；未配置时不写入该指令。出口配置须保留本机连接的绕过规则 |

旧的固定账号 unit、仓库账号 YAML 与旧路径不是部署来源。迁移保留的旧目录反向链接和旧 socket 别名仅承接存量引用；相关检查、迁移和恢复操作统一见 `decision_ab9880dc4c9d`。

**进程重启不改变 Portal 的记录。** Resident 的会话归属、消息历史、执行检查点和终态保存在 Portal 中。客户端重新连接同一账号的 socket，按已保存的会话 ID 继续工作；其他账号的路由归属不受影响。

## 相关实现与测试

更新检查的实现在 `management_portal/execution/provider_updates.py`，入口脚本是 `scripts/update_provider_runtimes.py`，首次安装 DSH 运行时由 `scripts/prepare_agent_runtimes.py` 完成。`tests/cases/test_provider_updates.py` 验证：每个 Portal 安装运行时的 Provider 都声明了运行时类、只在空闲时切换、DSH 的 `dsh-llm` 单独更新也会触发安装、目标 LLM 要求的 Cordis 依赖用于独立安装且保留仓库运行时、服务重启失败时退回原版本，以及安装不完整的 Kimi 目录会在切换前重新安装。

Claude SDK 的安装统一调用 `ClaudeRuntime.stage`；`scripts/install_claude_code_backend` 使用同一入口，并从 `requirements-claude-code.txt` 读取配套依赖。声明的 SDK 版本用于首次安装，已经安装的更高版本继续保留。Portal 自己的 daemon 代码另按 app-server 版本复制安装，源码发布和 daemon 副本启用需要分别确认。更新检查与待切换启用共用状态文件锁和原子保存流程。
