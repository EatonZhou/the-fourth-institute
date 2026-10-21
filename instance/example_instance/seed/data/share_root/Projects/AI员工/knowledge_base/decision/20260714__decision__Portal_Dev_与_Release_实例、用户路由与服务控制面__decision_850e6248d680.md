---
kb_id: decision_850e6248d680
type: decision
status: obsolete
project: AI员工
created_at: '2026-07-14'
version:
  number: 50
  created_at: "2026-07-14"
  created_by:
    user: ""
    agent: ""
  updated_at: "2026-10-21T13:11:45Z"
  updated_by:
    user: ""
    agent: "resident_977d1a613d44"
importance: 9
metaness: 3
ai_generated: false
links:
  source:
  - ${code_root}/scripts/portal_services.py
  - ${code_root}/scripts/portal_service_control.py
  - ${code_root}/management_common/file_lock.py
  - ${code_root}/management_portal/portal_deployment_registry.py
  - ${code_root}/management_portal/portal_deployment_router.py
  - ${code_root}/scripts/portal_dev_generation.py
  - ${code_root}/scripts/portal_dev_restart_requests.py
  - ${code_root}/scripts/portal_dev_source_reloader.py
  - ${code_root}/scripts/portal_service_units.py
  - ${code_root}/scripts/run_portal_shared_component.py
  - ${code_root}/config/portal_instances.json
  - ${code_root}/management_common/instance_paths.py
  - ${code_root}/scripts/portal_instance_storage.py
  - ${code_root}/systemd/portal-user-router.service
  - ${code_root}/tests/cases/test_portal_instance_storage.py
  - ${code_root}/tests/cases/test_portal_deployment_router.py
  - ${code_root}/tests/cases/test_portal_services.py
  - ${code_root}/tests/cases/test_portal_dev_source_reloader.py
  related:
  - decision_ab9880dc4c9d
  - decision_08bb2d9141f9
  - decision_37f98f8fcb4b
  - decision_99bb5dc0f96b
  - decision_6febe7bb57f9
  - decision_6e230393221c
  supersedes: []
  derived_from: []
---
# Portal Dev 与 Release 实例、用户路由与服务控制面

> **历史规范（已废止）**：当前开发站采用同一实例内的两个 Git worktree、两个 Web 入口和一组共享后台，现行规范为 `decision_08bb2d9141f9`。下文保留旧部署的审计记录；其中旧仓库路径、端口、分支和双后台服务命令不得用于当前部署。

Portal 同时运行 Dev 和 Release 两个实例：Dev 运行开发工作树中的最新源码，Release 运行已经发布的网页代码。本 Design 说明这两个实例由哪些服务组成、运行文件放在哪里、浏览器请求怎样被送到其中一个实例，以及运维人员和 Agent 怎样通过服务控制面查看、启动、停止和重启实例，Dev 又怎样在源码变化后自动更新受影响的服务。

代码怎样从 Dev 发布到 Release 见 `decision_08bb2d9141f9`；重启时浏览器收到的倒计时通知和组件重启顺序见 `decision_37f98f8fcb4b`；路由器提供的数据库运行状态和组件生命周期公告见 `decision_99bb5dc0f96b`。

## 基本概念

**实例**是一组可以一起启动、停止和检查的 Portal 服务进程。当前有两个实例：`dev` 和 `release`。每个实例由一个网页进程和一组后台服务组成，在 Linux 上以 systemd 用户服务运行，并由一个 systemd target（把多个服务编成一组的单元）统一启停。服务控制面还提供 Windows SCM 与 macOS launchd 适配器；在这些系统上，安装器必须先登记原生服务。两个实例读写同一个 PostgreSQL 数据库。

**组件**是实例中可以单独重启的一类服务。控制面使用下面五个组件名：

| 组件名 | 作用 | Dev 中的服务 | Release 中的服务 |
| --- | --- | --- | --- |
| `web` | 提供页面、HTTP API 和浏览器实时连接 | `management-portal.service`，端口 18086，运行 Dev 工作树 | `management-portal-release.service`，端口 18085，运行 Release 检出目录（`release` 分支） |
| `resource` | ResourceService，资源读写 | `portal-dev-resource-service.service` | `portal-release-resource-service.service` |
| `execution-service` | ExecutionService，接收和调度执行请求 | `portal-dev-execution-service.service` | `portal-release-execution-service.service` |
| `execution-worker` | ExecutionWorker，实际运行 Agent 任务 | `portal-dev-execution-worker.service` | `portal-release-execution-worker.service` |
| `lineage` | SocietyService，Lineage 与 Resident 协作 | `portal-dev-society-service.service` | `portal-release-society-service.service` |

每个实例还有自己的 Core NATS 消息服务（`portal-dev-nats.service`、`portal-release-nats.service`），随 target 启停。Release 的四个后台组件也从 Dev 工作树启动（通过 `scripts/run_portal_shared_component.py`），与 Dev 运行同一份共享源码；两个实例只有网页代码按发布版本区分。

**用户路由器**（Portal Router，`portal-user-router.service`，端口 18084）是浏览器访问 Portal 时最先到达的入口。它为每个请求选择一个实例并转发过去。两个实例共用这一个路由器。

**实例登记表**（registry）是路由器读取的实例清单 `deployments.json`，列出可以选择的实例、它们的本机地址和显示顺序。登记表中的实例类型除 `dev`、`release` 外还可以是 `exploration`（探索实例），用于临时登记额外的本机 Portal 以便比较；服务控制面只管理 `dev` 和 `release`。

**服务控制面**是命令 `./scripts/portal_services`（实现在 `scripts/portal_services.py`），所有启动、停止、重启、状态和健康检查都通过它进行。操作系统服务管理器的调用由 `scripts/portal_service_control.py` 统一承接。

**Dev 代码标识**（build ID）是 Dev 源码内容的摘要，形如 `dev-` 加 16 位十六进制数。每个 Dev 组件记录自己最近一次启动时加载的代码标识，健康检查结果中会显示它，便于判断组件是否已经加载了最新代码。

**Dev 源码重载器**（`portal-dev-source-reloader.service`）是常驻进程，负责在收到重启请求或发现源码变化后，调用控制面重启受影响的 Dev 组件。

## 设计点

### 浏览器请求的选路

- **路由器把每个请求转发给恰好一个实例。** 它不改投另一个实例，也不重放已经送出的请求，避免有副作用的操作执行两次。
- **目标实例拒绝连接时，路由器等它重启完成。** Web 重启时端口大约关闭 3 秒，崩溃的进程在 `RestartSec` 后回来。连接被拒绝说明请求还没有送到实例，所以路由器按退避间隔重试连接（第一次隔 0.25 秒，之后每次翻倍，最长 2 秒一次；与后台服务的连接等待共用 `management_common/service_socket.py` 的 `retry_delays`），最多等 30 秒，连上后才送出请求；用户看到的是稍慢的一次请求，而不是 502。等待期间客户端断开（刷新、关闭页面）时，路由器放弃这次请求，实例上线后不会再收到它。超过 30 秒仍连不上时，路由器返回 `502` 和错误码 `backend_unavailable`；浏览器的页面请求（GET 且 Accept 含 `text/html`）收到一张 5 秒后自动重试的页面（带 `Retry-After: 5`），API 请求仍收到 JSON。其他连接错误直接返回 502。
- **用户可以选择自己使用的实例，选择保存在浏览器 Cookie 中。** 路由器把用户名和实例 ID 一起写入 Cookie `db_portal_instance`，有效期 180 天。只有 Cookie 中的用户名与当前登录用户一致、实例仍在登记表中时，这个选择才生效；其他用户的 Cookie 值对当前用户无效。
- **没有有效选择时，开发者默认进入 Dev，其他用户默认进入 Release。** 开发者名单由环境变量 `PORTAL_ROUTER_DEV_USERS` 显式配置，发行副本默认不指定开发者。首选实例未登记时，依次改用 Release、Dev，都没有时使用实例 ID 按字母排序的第一项。未登录的请求按非开发者处理。
- **账号安全相关的请求总是交给 Dev。** Passkey（`/api/auth/passkeys/`）、找回密码（`/api/auth/password-reset/`、`/forgot-password`、`/reset-password`）、账号接口（`/api/account/`）、设置页（`/settings.html`）及其脚本和样式（`/static/portal_passkeys.js`、`/static/portal_ui_primitives.js`、`/static/settings.css`、`/static/settings.js`）只要 Dev 已登记，就转发给 Dev；未登录用户访问 `/login` 和 `/login/verify` 也转发给 Dev。
- **只有允许网段内的客户端可以连接路由器。** 当前允许 `127.0.0.0/8`、`${lan_host}/24` 和 `${lan_host}/24`，其他来源的连接被直接关闭。
- **登记表是实例和地址的唯一来源。** 路由器启动时登记表缺失或无效，路由器启动失败；运行中登记表被改坏时，路由器继续使用最后一份有效内容，文件修正后自动生效。

### 实例目录

- **两个实例的运行文件都放在实例根目录下。** 根目录由 `config/portal_instances.json` 的 `root` 决定，当前是 `${instances_root}`；环境变量 `PORTAL_INSTANCES_ROOT` 可以覆盖，覆盖时所有相关进程必须使用相同的值。
- **实例根目录只保存运行文件。** 它保存登记表、发布锁，以及 `dev/`、`release/` 下的 socket、操作锁、代码标识、运行环境、组件生命周期公告、健康文件和独立发布单元的日志。Git 忽略这个目录，Dev 代码标识的计算也排除它。业务数据库、AgentData、Resident Folder 和业务工作区由各自的服务管理，不在这里。
- **Worker 健康文件随所属进程回收。** Worker manager 在读取完整的 scheduler 注册列表后，清理已注销或早已不在注册列表中的本实例 Worker 文件；仍由 manager 管理、PID 存活、状态读取失败或文件在检查期间更新的记录保留。其他实例的文件不受影响。

### 服务控制

- **服务管理器接口统一为 `start`、`stop`、`restart`、`status` 和 `health`。** Linux 适配 systemd 用户服务，Windows 适配 SCM，macOS 适配 launchd 用户 Agent。服务名和 Dev/Release 成员来自同一份实例布局；非 Linux 平台按实例成员顺序启停，状态字段归一为 `load`、`active`、`sub` 和 `pid`。实例级命令继续检查业务健康端点。
- **原生服务登记是启动前提。** Linux 的控制面安装 systemd unit；Windows 和 macOS 的控制面检查服务已登记，缺失时明确报错。当前仓库尚无 Windows/macOS 原生服务安装器。核心 RPC 已按平台选择端点：Linux/macOS 使用 Unix socket，Windows 为 Dev/Release 各服务分配独立的本机 TCP 端口；签名密钥仍随实例逻辑路径定位。Windows 的 RPC 密钥默认放在用户 LocalAppData 下并按实例隔离；NTFS ACL 仍需实机验收。macOS ScriptRun 已使用独立会话 runner 执行和取消；Windows ScriptRun 的逐运行本地 ISA relay 与部分 Provider 进程交接仍需原生实现；路径迁移已可由独立后台进程执行，Windows 在停服前检查目录符号链接权限，不满足时拒绝迁移并保持原配置，实际复制过程尚待实机验收，因此原生适配器本身不构成完整的跨平台部署支持。
- **热交接能力按平台处理。** Linux 保留 ExecutionService 的 reload 和 Worker 的主进程信号；其他平台在队列排空后执行服务重启。Dev 重载器在 Linux 由信号唤醒，在其他平台轮询持久化重启请求。

- **`status` 只读取状态，随时可以运行。** 它报告实例中每个服务的运行状态、各组件健康、执行队列中进行和排队的任务数，以及实例整体是否可用。
- **RPC 健康探针保持客户端依赖边界。** 导入 ResourceService 的传输客户端时，不加载数据库实现、权限存储或本地服务工厂。控制面使用系统 Python 运行健康检查时不要求安装服务端的 PostgreSQL 驱动；实际数据库访问由服务端负责。
- **同一实例上的启动、停止和重启依次执行。** 控制面为每个实例持有一把操作锁；后到的操作等待前一个操作结束后再执行。
- **停止实例前，默认等待执行队列清空。** 控制面等待正在执行和排队的任务数都变为零；`--drain-timeout` 可以设置最长等待秒数（默认 0，表示一直等）。`--no-drain` 在队列非空时立即拒绝停止；`--force` 跳过这项检查，由调用方承担中断任务的后果。队列数来自执行调度器，包含所有实例提交的任务。
- **重启固定给浏览器 5 秒倒计时。** `restart` 默认重启 `resource`、`execution-service`、`lineage` 和 `web`，`--component` 可以指定一个或多个组件；需要重启 ExecutionWorker 时必须显式指定 `execution-worker`。通知、排空和组件的重启顺序见 `decision_37f98f8fcb4b`。
- **Dev 重启共享组件时，Release 的同名组件随后也重启。** `resource`、`execution-service`、`lineage`、`execution-worker` 在两个实例中运行同一份源码，Dev 的这些组件重启成功后，控制面在 Release 上重启相同的组件，使两边加载同样的代码。
- **重启成功的条件是实例健康且被重启的服务换了新进程。** 控制面最多等待 90 秒；超时或失败时返回错误，组件保持失败时的状态，控制面不自动恢复旧代码。
- **CPU 紧张时，用户请求经过的服务先运行。** 路由器、两个 Web、两个实例的 NATS、ResourceService、ExecutionService、SocietyService 和 PostgreSQL 的 unit 设 `CPUWeight=1000`。Provider 单元里运行的 Agent 命令保持默认权重 100；测试框架的执行单元设 `CPUWeight=20` 和 `nice 10`。CPU 空闲时权重不起作用；多个 Agent 同时跑测试、机器满载时，消息投递和页面读取仍能及时拿到 CPU。ExecutionWorker 保持默认权重。

### Dev 源码变化后的自动更新

- **`request-restart dev` 登记一次重启请求，由源码重载器执行。** 命令写入请求后立即返回请求 ID，并唤醒重载器；只有 Dev 支持这种延后执行的请求。Release 的 ISA 重启请求在独立 `portal-restart-<id>.service` 中执行，使 Web 重启后编排进程仍能完成剩余组件重启、健康检查和生命周期公告。这是开发者修改 Dev 源码后让修改生效的常用方式。
- **不指定组件时，重载器只重启源码有变化的组件。** 重载器比较每个组件上次成功部署时的源码状态和当前源码，按文件到组件的对照表算出需要重启的组件；没有变化时直接确认请求，不重启。
- **指定组件时，重载器无条件重启这些组件。** 即使源码状态没有变化也会重启，用于修复没有正确加载代码的组件。
- **连续的请求合并执行。** 重载器执行期间到达的新请求会合并到同一个待处理请求中，执行结束后继续处理，旧的执行不会误删新请求。
- **失败的请求不自动重试。** 一次重启失败后，请求保留，重载器在请求内容没有更新前不再执行它；提交新的请求或修改源码后才会再次尝试。
- **重载器每小时扫描一次源码。** 扫描发现有组件的源码变化而没有被部署时，自动重启这些组件；同一份源码状态失败过一次后，等源码再次变化才重试。

### 共享授权代码与配置的部署

- **授权相关的公共代码变化会重启全部五个组件。** 统一授权入口、Portal 访问控制、Principal、授权上下文协议和 HATG 指令层（文件见实现说明）被所有组件读取；重载器把这些文件的变化计入全部组件，每个组件重启成功后才记为已部署。
- **后台服务导入的公共模块变化也重启全部五个组件。** `management_common/` 中的数据库访问、身份、操作错误和会话产物等模块被四个后台服务和 Web 共同导入，并且是 Release 的共享源码；这些文件变化时重载器重启全部组件，只有 Web 独用的 `knowledge_metadata.py` 和 `portal_agent_http.py` 只重启 `web`。
- **权限系统的存储格式是 Dev 与 Release 之间的共享协议。** 两个实例读取同一份权限实例与模板记录（PostgreSQL schema `permission`，decision_b820faf42218）。两个后台服务池从共享源码运行；Release Web 的非共享模块来自 `release` 分支，公共包按 `decision_08bb2d9141f9` 的共享路径加载。修改表结构或模板格式前，必须确认两个实例正在运行的各读者都能读取新格式；无法读取时按错误处理，不当作放行。
- **SocietyService 按实际运行、数据库与领域依赖报告健康。**
- **两个实例的 SocietyService 共享 Resident 任务的恢复锁。** 一个实例重启释放锁后，另一个实例可能接手同一个 Resident 任务，所以把浏览器切到 Dev 不能隔离后台恢复；重启前需要确认所有参与恢复的实例都能读取当前授权配置。恢复已经持久化的执行请求时，SocietyService 直接读取原请求和原执行 ID，不重新构造 Prompt 或重新计算权限；新请求照常做完整的授权检查。

## 实现说明

### 实例登记表

登记表位于 `<实例根目录>/deployments.json`，当前 schema 为 `tfi.portal-deployments.v1`，读取仅接受该精确格式；`instances` 列表中每一项包含：

| 字段 | 规则 |
| --- | --- |
| `id` | 小写字母或数字开头，由小写字母、数字和 `-` 组成，最长 64 个字符；同一登记表内唯一 |
| `label` | 显示名称，1 到 80 个字符 |
| `kind` | `dev`、`release` 或 `exploration` |
| `origin` | 路由器转发的目标，必须是 `http://` 加本机回环 IP 地址和端口，不带路径、查询或片段 |
| `browser_origin` | 可选；测试框架在浏览器中直接打开该实例时使用的地址，必须是同端口的 `http://localhost` |
| `order` | 整数，用于排序 |
| `build_id` | 可选的显示用代码标识，超过 240 个字符的部分被截掉 |
| `enabled` | 缺省为启用；停用的项在读取时被忽略 |

读取后至少要有一个启用的实例，结果按 `order`、`label`、`id` 排序。当前登记 Dev（`127.0.0.1:18086`，order 100）和 Release（`127.0.0.1:18085`，order 110）。路由器每次使用登记表前比较文件的修改时间和大小，有变化才重新读取；读取失败时保留上一份有效内容，并在进程内记下错误。

### 路由器接口

| 路径 | 方法与权限 | 结果 |
| --- | --- | --- |
| `/__portal_router/health` | 任意方法，无需登录 | `200 {"ok": true}`，只说明路由器进程能响应 |
| `/__portal_router/status` | `GET`、`HEAD`，无需登录 | 数据库运行状态，字段含义见 `decision_99bb5dc0f96b`；其他方法返回 `405` |
| `/__portal_router/preference` | `GET`、`HEAD`，需要登录 | 返回 `ok`、`username`、`default_instance`（默认实例）、`preference`（已保存的选择或 `null`）、`effective_instance`（实际使用的实例）和 `instances`（可选实例的 `id`、`label`、`kind`、`build_id`）；未登录返回 `401` |
| `/__portal_router/preference` | `POST`，需要登录 | 请求体 JSON 的 `instance` 为已登记的实例 ID 时，写入 Cookie 并返回与 `GET` 相同的内容；实例未知或 JSON 无效返回 `400`，请求体超过 4096 字节返回 `413`，分块传输返回 `501` |
| 其他路径 | 任意方法 | 按上文规则选定实例后转发 |

路由器从 Portal 登录 Cookie 中验证当前用户（使用与 Portal 相同的会话签名密钥）。偏好 Cookie 内容是 Base64 编码的 `{"v": 1, "u": 用户名, "b": 实例 ID}`，属性为 `Path=/`、`HttpOnly`、`SameSite=Lax`，请求经 HTTPS 转发（`X-Forwarded-Proto: https`）时加 `Secure`。转发时路由器逐块传递请求和响应，去掉 `Connection`、`Keep-Alive`、`Transfer-Encoding`、`Upgrade` 这类逐跳头；请求目标不是以 `/` 开头的路径时返回 `400`，分块上传的请求体返回 `501`。后端响应的等待上限默认 3600 秒（`PORTAL_ROUTER_BACKEND_TIMEOUT`）。

### 控制面命令

| 命令 | 作用 |
| --- | --- |
| `status [--instance dev\|release] [--schema]` | 输出实例状态；`--schema` 同时检查 PostgreSQL 能否连接并列出 schema |
| `health [--instance dev\|release] [--schema]` | 运行同一组实例健康探针，返回是否可用 |
| `start <实例>` | Linux 安装 systemd unit；其他平台检查原生服务登记，然后启动实例 |
| `stop <实例> [--drain\|--no-drain] [--drain-timeout 秒] [--force]` | 等待队列清空后停止实例 |
| `restart <实例> [--component 组件 ...]` | 带 5 秒通知重启选中组件并验收 |
| `request-restart dev [--component 组件 ...]` | 登记 Dev 重启请求，交给源码重载器执行 |

`status` 与 `health` 为每个实例输出：target、网页服务和路由器服务的服务管理器状态，各后台服务的状态与进程号，执行队列计数（`active`、`queued`），ExecutionService、ExecutionWorker、ResourceService、SocietyService、Web（`/api/health`）和路由器（`/__portal_router/health`）的健康结果，Release 的发布清单，以及可选的数据库检查。`runtime_ok` 在全部服务处于运行状态、全部健康结果为成功（使用 `--schema` 时还包括数据库检查）时为真；`instance_consistent` 检查 Web 报告的实例名与所查实例一致。两者都成立时 `ok` 为真。

`start` 在 Linux 安装并加载该实例的 systemd unit，在 Windows/macOS 核对原生服务已登记；Dev 还会先为全部组件记录当前代码标识。随后启动 target、网页服务和路由器，等待实例健康后移除维护栅栏，Dev 最后启动源码重载器。

`stop` 在不跳过检查时先确认执行服务可以查询队列（查询失败时拒绝停止），再按设置等待或拒绝，然后写入 `<实例根目录>/<实例>/maintenance.json` 维护栅栏。Dev 先停止源码重载器，再依次停止网页服务、target 和各后台服务。栅栏存在时 restart 和自动部署拒绝动作，显式 start 在健康验收通过后解除栅栏。运维人员也可以先创建该文件保护受控组件维护；共享组件更新前同时检查两个实例的栅栏。

`restart` 的步骤是：检查维护栅栏；安装 unit；Dev 为选中组件记录新的代码标识；确保 target 和路由器在运行；记录选中服务当前的进程号；按 `decision_37f98f8fcb4b` 的顺序通知、排空和重启；等待验收。验收每 0.5 秒检查一次，最多 90 秒，要求 `runtime_ok` 为真、Web 报告的实例名正确，并且选中服务（ExecutionWorker 除外，它通过重新加载换代）的进程号都已改变。

操作锁文件是 `<实例根目录>/<实例>/portal-services.lock`，使用阻塞式文件锁：后到的命令一直等待，拿到锁后再执行。`status` 和 `request-restart` 不取这把锁。

### Dev 代码标识

`scripts/portal_dev_generation.py` 计算 Dev 代码标识。它纳入以下范围内所有已跟踪文件和未被 Git 忽略的未跟踪文件：目录 `knowledge_board_lan`、`management_common`、`management_portal`、`project_board_lan`、`scripts`、`skill_board_lan`、`systemd`、`tests`，以及文件 `config/portal_instances.json`、`package.json`、`package-lock.json`、`requirements-portal-runtime.txt`；其中 `__pycache__`、重启通知文件和 `.pyc`、`.pyo`、`.swp`、`.tmp`、`~` 结尾的文件除外。标识是这些文件相对路径与内容的 SHA-256 摘要前 16 位，加 `dev-` 前缀。

控制面启动或重启 Dev 组件前把当前标识写入 `dev/source-generation.env`（Dev 服务通过 systemd 读取为环境变量 `PORTAL_BUILD_ID`），并在 `dev/component-generations.json`（新写 schema 为 `tfi.portal-component-generations.v1`）中更新被重启组件的标识。Release 的后台组件启动时从这个文件读取对应组件的标识。标识用于诊断显示，重启验收以进程号变化和健康检查为准。

### 源码重载器

控制面在 Dev 及对应 Release 共享组件重启成功后，保存每个组件重启前的源码快照；重载器每次处理请求或扫描前重新读取这份已部署记录。记录保存每个组件上次成功部署时的源码状态（每个相关文件的路径、修改时间和大小），文件是 `dev/deployed-component-source-state.json`。它按下表把变化的文件换算成组件，发布工具也用同一张表决定要重启的共享组件：

| 变化的文件 | 需要重启的组件 |
| --- | --- |
| `management_portal/portal_access_control.py`，`management_common/principal.py`、`execution_context_file.py`，以及 `management_portal/hatg/`、`management_portal/permission_system/` 下的文件 | 全部五个组件 |
| `management_portal/ai_provider_registry.py`、`requirements-portal-runtime.txt` | 全部五个组件 |
| `management_portal/execution/` 下的文件 | `execution-service`、`execution-worker`；除 `service.py`、`scheduler.py`、`worker.py`、`worker_manager.py`、`runtime.py`、`backends.py`、`binding.py`、`codex_accounts.py`、`codex_accounts.yaml`、`session_manager.py` 以外的文件另加 `web`；`event_projection.py` 和 `harness_` 开头的文件另加 `lineage` |
| `management_portal/resources/` 下的文件 | `resource`；`__init__.py`、`address.py`、`contracts.py`、`transport.py` 另加 `web` |
| `management_portal/society_service/` 下的文件 | `lineage`；`__init__.py`、`contracts.py`、`transport.py` 另加 `web` |
| `management_portal/workbench_projections/`，`management_portal/` 下的 `lineage_workflows.py`、`semantic_search_surfaces.py`、`application_targets.py`、`isa_client.py`、`paper_projection.py`、`wall_workflows.py`，以及 `management_portal/test_framework/` 下的 `workflows.py`、`resource_index.py` | `web`、`resource` |
| `management_common/` 下的其他文件（数据库访问、身份、操作错误、会话产物等，后台服务同样导入） | 全部五个组件；只有 `knowledge_metadata.py`、`portal_agent_http.py` 只重启 `web` |
| `management_portal/codex_app_server_client.py`、`claude_code_client.py`、`kimi_code_client.py` | `execution-worker`；它们也是 Provider 声明加载的模块，按下文的加载范围实际重启全部五个组件 |
| `systemd/portal-dev-<组件>.service`（四个后台组件各自的 unit） | 对应组件 |
| `systemd/management-portal-routed.conf`、`management_portal/management-portal.service` | `web` |
| 其他 `systemd/` 下的文件 | 全部五个组件 |
| `tests/cases/` 下的测试源码、`case_ids.json` 和 TAU、设计 Live 等显式用例 JSON | 无；测试目录按请求从这些文件构建，任一文件修订变化时重建 |
| `scripts/` 下的文件 | 不重启（脚本在调用时加载） |
| 其他 `management_portal/` 和各看板目录下的文件 | `web` |

**每个组件还会因为它加载的任何文件变化而重启，不论上表是否列出这个文件。** 重载器从各组件的入口模块出发，沿 import 在仓库中逐个找到被加载的文件。Execution 组件沿模块顶层 import；Web、ResourceService 和 SocietyService 同时沿函数内 import，因为请求处理会延迟加载并缓存这些模块。Web 跳过 client 模式下从不加载的进程内 SocietyService（`management_portal.society_service.server`）：

| 组件 | 入口模块 |
| --- | --- |
| `web` | `management_portal.asgi` |
| `lineage` | `management_portal.society_service.server` |
| `resource` | `management_portal.resources.server` 和 `management_portal.society_service.server` |
| `execution-service` | `management_portal.execution.service` |
| `execution-worker` | `management_portal.execution.worker`、`management_portal.execution.worker_manager` |

ResourceService 在自己的进程内运行 SocietyService（`PORTAL_SOCIETY_SERVICE_MODE` 未设置时为 `local`），因此它也加载 `society_service` 和 SocietyService 导入的执行层模块，例如 `agent_data_store.py`。每个文件解析出的 import 按修改时间和大小缓存。

Provider 的模块按名称加载，也计入加载范围。每个 Provider 的声明（`management_portal/execution/provider_catalog.py` 中的 `AgentProvider(...)`）用字符串写出它的模型目录、Adapter、运行时更新器和凭证路径函数，例如 `management_portal.opencode_provider_catalog`、`management_portal.opencode_backend.OpenCodeBackend`；读取声明的服务在用到时用 `import_module` 加载它们。重载器把这些调用里以 `management_common` 或 `management_portal` 开头的字符串当作 `provider_catalog` 的 import，取其中最长的、对应仓库文件的前缀（类名、函数名不是模块）。五个组件的入口都会加载 `provider_catalog`，所以这些模块及其 import 变化时五个组件都重启；表中 `codex_app_server_client.py` 等客户端因此也会重启全部组件。新的按名称加载方式需要在重载器的 `NAMED_MODULE_CALLS` 中登记。

可选功能的接入点同样按名称加载（decision_15aab18c0ab3）。重载器只读取 `management_portal/portal_features.py` 中 `PortalFeature(...)` 接入点表里的完整字符串常量，`modules` 中的模块名模式不算载入；某个服务读取哪些接入点按 `FEATURE_HOOK_READERS` 计算：`web.*`、`watchboard.*` 和 `application.*` 只算 Web，`resource.*` 算 Web 和 ResourceService，其余接入点算所有读取声明的服务。功能的指令声明模块（`isa.instructions`）与 `hatg/` 一样，改动时重启全部组件；它的 Web 处理模块（`web.instructions`）只重启 Web。

`request-restart dev` 把请求写入 `dev/restart-request.json`（新写 schema 为 `tfi.portal-dev-restart-request.v2`，读取仅接受该精确格式）。第一条待处理请求生成 `restart_request_<uuid>` 形式的 ID；之后的请求在文件锁内合并组件集合，沿用同一个 ID，并把 `revision`（修订号）加一。任一提交未指定组件时保留 `auto: true`，实际部署集合为显式组件与自动检测变化的并集。随后命令启动重载器；该单元使用 Type=notify，在安装信号处理器后报告 READY，systemctl start 返回后再发送 SIGUSR1，避免信号落在处理器安装前。

重载器在最近一次请求后等待 5 秒安静窗口，从首个待处理请求起最多等待 30 秒；两个时间戳随请求文件保存，重载器重启不重置合并窗口。该窗口用于合并待部署组件，与浏览器停止倒计时分别计时；实际重启的通知和排空流程见 `decision_37f98f8fcb4b`。

重载器读取请求的 ID 和修订号，执行重启，成功后只有当文件中的 ID 和修订号仍与读取时相同，才删除请求文件；执行期间到达的新请求使修订号变化，文件因此保留到下一轮。执行失败时，重载器记住失败的“ID:修订号”，不再执行同一内容，并在日志中记录阻塞事件；修正后重新提交请求，形成新的修订号再重试。重载器本身每 3600 秒扫描一次源码。

### systemd unit 的安装与实例目录迁移

`systemd/` 中的 unit 源文件用占位符 `@PORTAL_INSTANCES_ROOT@` 表示实例根目录。组件、单元与路径由 `scripts/portal_source_layout.py` 统一声明。控制面和准备脚本经 `scripts/portal_service_units.py` 统一调用 `scripts/portal_instance_storage.py` 的 `install_unit`，把占位符替换为当前根目录，原子写入 systemd 用户目录后重新加载 systemd。已安装的 unit 必须是替换后的结果；直接链接源文件会让启动命令包含未替换的路径。所有单元（包含 Release Web）从 Dev 共同源码安装；一次发布只安装并 daemon-reload 一次，后续组件编排复用这次安装。Provider 单元来自 Provider 声明的 runtime.services，安装只刷新 systemd 定义，不重启正在运行的 Provider。Telepus 状态配置由仓库 drop-in 安装到两个 Web 与 SocietyService 单元；Release 共享组件关闭字节码写入。

需要把实例根目录搬到同一文件系统的新位置时，`portal_instance_storage.py migrate` 用原子交换移动目录，保留目录本身、已打开的文件和 socket，并在旧位置留下指向新位置的链接。删除这个链接之前，必须确认 Release、已安装的 unit 和运行环境中的绝对路径都已经不再引用旧位置。迁移不发布 Release，不改变浏览器选路，也不重建 Resident Session。
