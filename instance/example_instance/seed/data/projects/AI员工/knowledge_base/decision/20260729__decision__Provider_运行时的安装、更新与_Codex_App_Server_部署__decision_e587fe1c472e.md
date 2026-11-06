---
kb_id: decision_e587fe1c472e
type: decision
project: AI员工
created_at: '2026-07-29'
version:
  number: 26
  created_at: "2026-07-29"
  created_by:
    user: ""
    agent: ""
  updated_at: "2026-11-06T01:17:37Z"
  updated_by:
    user: ""
    agent: "resident_60ae6d0cdf74"
importance: 9
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/scripts/instance_service_definitions.py
  - ${code_root}/scripts/portal_service_units.py
  - ${code_root}/scripts/instance_services.py
  - ${code_root}/scripts/tfi_release_platform_linux.py
  - ${code_root}/management_portal/service_runtime.py
  - ${code_root}/management_common/instance_manifest.py
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
- **版本目录**：每个版本单独安装的目录，属于代码层依赖，放在代码目录的 `dependencies/` 下（《Portal 实例协议》），名称带版本号，例如 `codex-cli-0.159.2`。安装新版本只新增目录，不改动正在使用的版本。
- **当前链接**：指向正在使用的版本目录的符号链接。服务只通过当前链接启动运行时，所以切换版本就是改变链接指向并重启服务。
- **验证标记**：版本目录中的 `.portal-validated` 文件，表示这个目录已经装好并通过了启动检查。
- **systemd 用户服务**：Linux 上以部署服务的操作系统账号运行的后台服务，异常退出后由 systemd 自动拉起；Windows 使用安装器的服务控制器，不依赖 systemd 或固定主机用户名。

## 各 Provider 的运行时

| Provider | 来源 | 版本目录 | 当前链接 | 使用它的服务 |
| --- | --- | --- | --- | --- |
| Codex | npm 包 `@openai/codex` 的 `latest` 版本 | `codex-cli-<版本>` | 站点 `providers.codex_binary` 或已解析的 Codex 可执行入口；Windows 使用运行时根中的 `codex-cli-current`，安装布局由平台适配器决定 | 已登记且正在运行的 `portal-codex-app-server@<account_id>.service` 实例 |
| Claude Code | PyPI 包 `claude-agent-sdk`，安装在独立的 Python 虚拟环境中 | `claude-agent-sdk-<版本>` | `claude-agent-sdk-current` | `portal-claude-code.service` |
| Kimi Code | npm 包 `@moonshot-ai/kimi-code` 中当前原生适配器支持的最新稳定版本 | `kimi-code-<版本>` | `kimi-code-current` | `portal-kimi-code.service` |
| DSH | npm 包 `@deepseek-ai/dsh` 与 `@deepseek-ai/dsh-llm` 的 `next` 版本，加上 Portal 仓库中的桥接脚本 `management_portal/dsh_runtime/bridge.mjs` | `dsh-runtime-<dsh 版本>-<dsh-llm 版本>` | `dsh-runtime-current` | 没有常驻服务；每个 DSH 会话按需启动自己的进程 |
| OpenCode | npm 包 `opencode-ai` 的 `latest` 版本；安装时同时记录各服务可调用工具的模型清单 | `opencode-<版本>` | `opencode-current` | 没有常驻服务；每个 Portal 会话由 `management_portal/opencode_bridge.py` 启动一个 `opencode serve` |
| Google Antigravity | ACP 注册表中 `antigravity-acp` 的 `agent.json` 声明的版本，归档只从 `dl.google.com` 下载 | `antigravity-<版本>` | `antigravity-current`，校验通过后写入 `.portal-validated` 才计为已安装 | 没有常驻服务；ACP 适配器为每个绑定启动原生 agent |

本站将每个稳定入口的绝对路径显式保存在 `config/site.json` 的 `deployment.providers` 中：Codex 用 `codex_binary`，Claude SDK 用 `claude_runtime_current`，Claude daemon 代码包用 `claude_app_server_current`，Kimi 用 `kimi_runtime_current`，DSH 用 `dsh_runtime_current`，OpenCode 用 `opencode_runtime_current`。这些字段保存软链接本身的位置，版本目录由链接目标选择；路径解析仅规范化父目录，保留最后一级链接。运行时更新器、客户端和服务安装器使用相同字段，显式路径非法时报告错误，不改用另一程序。未配置的源码安装保留首次接入发现与平台引导默认值。

Codex 全部账号共用站点 `codex_binary` 和运行时版本，各账号仍有独立的 `CODEX_HOME`、会话与 socket。账号内 `packages/app-server-daemon` 是 Codex 自身的受管 daemon 安装目录，与 Portal 的共享入口分别管理；程序清理须先检查真实服务命令和进程引用，不把账号凭据或会话纳入程序包清理范围。

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
- **切换范围由 Provider 的运行时更新器给出。** 检查程序把当前链接原子地换成新版本目录，依次重启更新器返回的服务，并确认它们处于运行状态。Codex 更新器从账号登记查询模板实例，只选择当前 `active` 的实例：已停用登记但仍在服务的进程也更新，原本停止的实例保持停止。账号之间隔离身份与会话，共用运行时版本；验证更新生效时核对被更新实例实际运行的二进制和版本。服务定义由实例清单与平台渲染器生成，按已登记账号组件选择，不把运行中的实例名当作仓库 unit 文件。
- **切换失败时退回原版本。** 任一服务重启失败，检查程序把当前链接改回原来的目录并再次重启这些服务，然后报告错误；安装失败时当前版本保持不变。
- **待切换的版本会在空闲后自动启用。** 出现待切换版本时，检查程序启动临时的 systemd 用户服务 `portal-provider-pending-activation`，每 30 秒尝试切换一次，最多等待 24 小时。该服务通过 `EnvironmentFile` 加载 Portal 的 PostgreSQL 环境文件，默认路径为 `~/.config/tfi/portal-postgres.env`，可由 `PORTAL_POSTGRES_ENV_FILE` 指定。数据库连接用于检查 Provider 的活动 Execution；连接不可用时报告错误并保留待切换状态。连接凭据不写入启动命令参数。
- **同一时间只运行一次检查。** 检查程序持有文件锁 `provider-updates.lock`，位于 provider-state 区域的根目录；另一次检查正在运行时，新的调用立即返回 `busy`。待切换的版本和最近一次检查时间保存在同目录的 `provider-updates.json`。

更新检查由 `scripts/update_provider_runtimes.py` 执行一次（加 `--activate-pending` 时只尝试启用待切换的版本）。它输出 JSON 结果，列出每个 Provider 的已安装版本与可用版本、本次启用的 Provider、仍在等待的 Provider 和错误；有错误或检查正忙时以非零状态退出。

Provider 声明了仓库内的运行时包（`AgentProvider.runtime_package`，目前只有 DSH 的 `management_portal/dsh_runtime`）时，第一次部署由 `scripts/prepare_agent_runtimes.py` 按仓库中的开放版本声明安装这个目录，并在这个 Provider 的当前链接不存在时让它指向这个目录；之后的版本切换由更新检查负责。

## 原生运行时的协议兼容边界

ProviderRuntime 同时声明可安装版本的来源与已安装版本是否符合当前 Adapter 的原生协议。程序能输出版本、文件存在与原生协议受支持分别判断；不兼容版本不能计为 ready。安装与更新选择受支持范围内的最新稳定版本，沿同一 stage、空闲切换和失败回退流程恢复，保留账号、会话与持久执行身份。

当前 Kimi Adapter 使用原始实时事件与 snapshot 合同，兼容版本范围为 `<2.5.0`。2.4.1 的 server_hello 虽声明 protocol_version=2，仍提供 accepted_subscriptions 确认、原始事件及 snapshot/status；该版本受支持。2.5 移除这些旧合同，改用 transcript 订阅和控制合同，需要完整适配后才能扩展支持范围。握手版本号不能单独判断两者兼容性。client_hello 的空成功 ACK 不代表已订阅一个 Session；遇到不支持的协议明确拒绝，不能依赖历史拼装提供流式完成结果。当前首次接入选择该范围内最新稳定版，既有兼容运行时继续复用。

Antigravity 登录先运行 Portal 的 Python ACP 包装入口。隔离环境必须只补入选中实例的非秘密 PORTAL_INSTANCE_ROOT，确保运行时路径模块可导入；不继承服务环境、数据库凭据或其他账号认证。

Claude 原生 session/close 指定 session_id 时释放该确切原生会话的所有空闲客户端，包括交接准备绑定与正式 Resident 绑定下的客户端。存在活跃客户端时不部分释放；旧 session_id 不得关闭同 key 的新会话。释放进程和注销目录分别回读，不能将目录失败重试当作已删除。

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

Codex 的每个执行账号由登记中的 `account_id` 标识，并关联供会话使用的 `route_id`。服务控制面为账号管理 `portal-codex-app-server@<account_id>.service` 实例；账号目录取自账号登记，socket 和实例名从登记标识推导，服务数量不固定。

| 对象 | 来源与职责 |
| --- | --- |
| 账号目录 `CODEX_HOME` | 账号登记中的 `config.account_dir`，保存该账号的凭据、配置与原生会话；放在哪里由主机用户决定（《Portal 实例协议》「执行账号目录」） |
| Unix socket | 实例 `runtime/` 下的 `s/codex-<account_id>.sock`，供客户端连接该账号实例 |
| 定义来源 | `scripts/instance_service_definitions.py` 遍历实例清单的 `services.components`，交给对应平台渲染器生成各组件定义 |
| 已安装定义 | Linux 用户 systemd 目录中的 `<service_prefix>-codex-app-server@<account_id>.service`；每个已登记账号有独立定义。`scripts/instance_services.py` 安装并核对归属，`service_runtime` 从清单解析程序与账号目录 |

账号登记入口与登录完成流程经 `account_hosting` 调用服务控制面；服务控制面负责登记组件、安装定义、启停和移除实例。常规定义安装只安装并 reload，实例的启停属于显式账号生命周期操作。登记停用只阻止新工作派发，保留现有进程；删除必须满足账号空闲条件，具体规则见 decision_7956d7cb4bde。

所有账号组件使用同一套平台渲染规则、当前 Codex 运行时与 `management_portal.codex_app_server_host` 启动入口；可执行文件从当前部署配置派生，账号目录取自账号登记。Host 在账号目录作为进程工作目录的条件下启动 `codex app-server`，线程任务的工作目录继续由每次请求单独设置。每个实例有独立的账号目录与 socket，登录身份、连接和原生会话不会在账号间混用。

| 设置 | 值与作用 |
| --- | --- |
| 启动配置 | 账号组件必须已登记，`service_runtime` 按该账号解析目录、程序与 socket；账号认证就绪另由账号接入流程检查 |
| 进程入口 | 平台定义启动 `management_portal.service_runtime --component codex-app-server@<account_id>`，再进入该账号的 `codex_app_server_host` |
| Linux `Restart` / `RestartSec` | `always` / 3 秒 |
| Linux `TimeoutStopSec` | 270 秒 |
| `MemoryHigh` / `MemoryMax` / `MemorySwapMax` | 32 GiB / 36 GiB / 1 GiB |
| 网络出口 | 平台定义由实例清单生成服务环境，`service_runtime` 按本站配置准备 Provider 进程环境；出口配置须保留本机连接的绕过规则 |

旧的固定账号 unit、仓库账号 YAML 与旧路径不是部署来源。迁移保留的旧目录反向链接和旧 socket 别名仅承接存量引用；相关检查、迁移和恢复操作统一见 `decision_ab9880dc4c9d`。

**进程重启不改变 Portal 的记录。** Resident 的会话归属、消息历史、执行检查点和终态保存在 Portal 中。客户端重新连接同一账号的 socket，按已保存的会话 ID 继续工作；其他账号的路由归属不受影响。

## 相关实现与测试

更新检查的实现在 `management_portal/execution/provider_updates.py`，入口脚本是 `scripts/update_provider_runtimes.py`，首次安装 DSH 运行时由 `scripts/prepare_agent_runtimes.py` 完成。`tests/cases/test_provider_updates.py` 验证：每个 Portal 安装运行时的 Provider 都声明了运行时类、只在空闲时切换、DSH 的 `dsh-llm` 单独更新也会触发安装、目标 LLM 要求的 Cordis 依赖用于独立安装且保留仓库运行时、服务重启失败时退回原版本，以及安装不完整的 Kimi 目录会在切换前重新安装。

Claude SDK 的安装统一调用 `ClaudeRuntime.stage`；`scripts/install_claude_code_backend` 使用同一入口，并从 `requirements-claude-code.txt` 读取配套依赖。声明的 SDK 版本用于首次安装，已经安装的更高版本继续保留。Portal 自己的 daemon 代码另按 app-server 版本复制安装，源码发布和 daemon 副本启用需要分别确认。更新检查与待切换启用共用状态文件锁和原子保存流程。


### Linux 首次接入的已有运行时发现

显式配置与已激活本站链接优先。没有这些配置时，Codex 从 PATH 和当前 OS 用户的标准 `.local/bin`、`bin` 发现程序；服务隔离 HOME 不改变这个用户身份。Claude 仅将通过真实解释器验证（SDK 版本、配套数据库依赖和 bundled CLI）的环境视为可复用运行时，候选来自当前/PATH/标准用户 Python 以及既有标准 Claude 用户服务的精确 ExecStart；不读取服务 Environment、其他用户目录或任意隐藏 venv。裸 Claude 命令可用不等于完整 SDK 就绪。

发现只读，不创建第二套环境或替换外部运行时。观察缓存限当前运行时对象；更新器的 activation_link 与发现路径区分，显式更新沿原 stage/空闲切换合同。已有 SDK 不代表新实例 daemon 已运行；首次接入仍须通过新实例自己的服务与 socket 验证实际执行。

### Linux 首个 Claude 账号的服务就绪

CheckProviderAccount 在 Linux 上通过 `portal_service_units.start_claude_service` 幂等准备并启用本站 Claude daemon，再等待本站 socket 的协议健康响应，最后才提交普通持久 Execution。SDK 目录可复用，但 daemon、代码包、服务名、配置与 socket 属于当前安装实例；不继承其他实例的 CLAUDE_CODE_SOCKET，也不将旧服务健康视为本站就绪。

标准 `install_claude_code_backend` 和接入流程使用同一安装 owner 与实例安装锁。已验证的 SDK 直接复用，不查询上游或另建 SDK；缺失 SDK 仍由原准备入口安装。daemon 代码包按已有版本不可变保存，真实导入验证显式禁止生成字节码，重复安装与首次 unit 失败后重试保持幂等。已有服务的所属实例、配置和 drop-in 须通过校验后才能使用；正常接入使用 enable/start，不重启已运行工作。启动失败或有界就绪等待超时保留登记和授权，返回安全、可重试诊断，不能把请求提交到未就绪的后台。


### 原生历史索引使用账号内相对路径

Codex 原生 `threads.rollout_path` 保存 `sessions/...`、`archived_sessions/...` 或 `legacy-thread-history/...`，位置相对于账号登记中的账号目录。索引不保存账号目录的绝对路径；账号目录移动并更新登记后，服务从新位置启动即可沿用同一条相对索引。进程的启动目录固定为账号目录，与 Resident 工作区和每轮请求的 cwd 分开。

`execution.codex_rollout_index.prepare_account_index` 是原生索引迁移与写入规则的唯一 owner。Host 启动前处理已有索引；原生程序创建新 SQLite schema 后，在公开连接端点前再次安装写入规则。SQLite 的 INSERT 和 rollout_path UPDATE 触发器把原生创建、恢复、归档写入的当前账号绝对路径原子转为相对路径，避免恢复线程后重新留下绝对地址；配置根目录变化时由同一 owner 更新规则。

迁移先用 SQLite 在线备份保存原索引，再核对账号内文件名与 session_meta 的 thread 身份，按旧值条件更新；缺文件、身份不符、路径越界或存在歧义时保留原记录并报告。备份在该账号的 `portal-index-backups/` 下。历史 JSONL、线程 ID、Portal 会话绑定与任务工作区不因索引迁移而变化。当前原生协议依赖进程启动目录解析相对路径；运行时更新须验证历史读取、resume 和 fork，以及迁移账号目录后保持这些行为。

POSIX Host 使用私有临时端点完成准备后再发布正式端点；原生 CLI 提供指向其私有 daemon socket 的符号链接时，核对 socket 与父目录归属和写权限。Windows 在相同准备完成后发布私有连接描述符。Linux 服务定义显式部署刷新可以替换经本站清单与组件归属核验的自有定义；普通账号启动不会覆盖不同的服务定义。
