---
kb_id: decision_850e6248d680
type: decision
project: AI员工
created_at: '2026-07-14'
version:
  number: 62
  created_at: '2026-07-14'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-11-01T03:39:06Z'
  updated_by:
    user: ''
    agent: resident_24014e2b9bf6
importance: 9
metaness: 3
ai_generated: false
links:
  source:
  - ${code_root}/scripts/tfi_development_runtime.py
  - ${code_root}/scripts/tfi_dev.py
  - ${code_root}/scripts/portal_services.py
  - ${code_root}/scripts/portal_services
  - ${code_root}/scripts/tfi_release_install.py
  - ${code_root}/scripts/tfi_release_platform_linux.py
  - ${code_root}/scripts/tfi_instance_config.py
  - ${code_root}/scripts/tfi_instance_manager.py
  - ${code_root}/scripts/tfi_shutdown.py
  - ${code_root}/scripts/portal_source_layout.py
  - ${code_root}/management_common/instance_config.py
  - ${code_root}/management_common/portal_paths.py
  - ${code_root}/management_common/instance_paths.py
  - ${code_root}/management_common/loaded_modules.py
  - ${code_root}/management_common/portal_network.py
  - ${code_root}/management_common/file_lock.py
  - ${code_root}/management_common/service_socket.py
  - ${code_root}/management_portal/__init__.py
  - ${code_root}/management_portal/portal_deployment_router.py
  - ${code_root}/management_portal/portal_deployment_registry.py
  - ${code_root}/management_portal/web_server.py
  - ${code_root}/management_portal/web_isa.py
  - ${code_root}/management_portal/hatg/catalog/environment.py
  - ${code_root}/management_portal/hatg/socket_rpc.py
  - ${code_root}/management_portal/execution/service_lifecycle.py
  - ${code_root}/management_portal/test_framework/dev_target.py
  - ${code_root}/tests/cases/test_development_runtime.py
  - ${code_root}/tests/cases/test_portal_services.py
  - ${code_root}/tests/cases/test_portal_deployment_router.py
  - ${code_root}/tests/cases/test_instance_config.py
  - ${code_root}/tests/cases/test_tfi_shutdown.py
  - ${code_root}/tests/cases/test_portal_shared_source_bootstrap.py
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
# Portal Dev 与 Release 部署、用户路由与服务控制面

Portal 开发站在一个已安装实例中同时提供两个网页入口：Dev 运行共享开发工作树中的最新代码，Release 运行最后一次发布的代码。两个入口共用同一个数据库和同一组后台服务。本 Design 说明这些进程由哪些 systemd 用户服务组成、各自从哪棵源码树加载代码、浏览器请求怎样被送到其中一个入口，以及运维人员和 Agent 怎样通过服务控制面读取状态、按组件重启，并在发布后激活新代码。

代码怎样从 Dev 发布到 Release 见 `decision_08bb2d9141f9`，运维命令的完整步骤见 `decision_ab9880dc4c9d`；重启时 Web 怎样停止、组件按什么顺序处理、浏览器怎样重新加载见 `decision_37f98f8fcb4b` 和 `decision_f25266e5a0e7`；Web 进程内的 ASGI 与长连接见 `decision_ecf0f2b2de28`；健康接口、构建标识和激活记录见 `decision_99bb5dc0f96b`；网络入口、TLS 和路由器的转发安全规则见 `decision_85222f2d316b`。

## 基本概念

**已安装实例**是安装器 `scripts/tfi_release_install.py` 建立的一套 Portal：一个安装根目录、一个 PostgreSQL 数据库和一组 systemd 用户服务，服务名为 `<service_prefix>-<组件>.service`。安装配置（target，保存在 `config/target.json`）记录服务前缀、端口、数据目录和运行用的 Python。下文的 `config/`、`runtime/` 都相对于安装根目录。

**Dev 与 Release** 是同一个已安装实例中的两个部署，即两个 Web 入口，对应同一 Git 仓库登记的两个工作树：Dev 是 `/opt/tfi/the-fourth-institute-dev`（分支 `dev`），Release 是 `/opt/tfi/the-fourth-institute`（分支 `main`），Portal 代码都在其中的 `code/` 目录。路由器、服务控制面和进程环境变量 `PORTAL_DEPLOYMENT` 用部署 ID `dev`、`release` 称呼这两个入口。它们共享数据库、账号、Session、执行队列和后台服务，只有网页进程分开（`decision_08bb2d9141f9`）。

**配对运行时**（`scripts/tfi_development_runtime.py`）给已安装实例加上 Dev Web 和用户入口路由器，并负责按组件重启、发布激活和状态读取。它的配置是安装配置中的 `development` 段，记录两棵源码树的 `code/` 目录、两个 Web 端口、路由器端口和可选的 HTTPS 网关单元。没有这一段的独立安装实例由安装器管理，服务控制面对它只返回“不支持”。

**组件**是服务控制面可以单独重启的一类服务。控制面使用下面五个组件名：

| 组件名 | 作用 | 对 `dev` 重启的单元 | 对 `release` 重启的单元 | 加载的源码 |
| --- | --- | --- | --- | --- |
| `web` | 页面、HTTP API 和浏览器实时连接 | `<前缀>-dev-web.service`，端口 28401 | `<前缀>-web.service`，端口 28400 | 各自的工作树；共享包来自 Dev |
| `resource` | ResourceService，资源读写 | `<前缀>-resource.service` | 同一单元 | Dev 工作树 |
| `execution-service` | ExecutionService，接收和调度执行请求 | `<前缀>-execution.service` | 同一单元 | Dev 工作树 |
| `execution-worker` | Worker Manager 及其管理的 ExecutionWorker，实际运行 Agent 任务 | `<前缀>-worker.service` | 同一单元 | Dev 工作树 |
| `lineage` | SocietyService，Lineage 与 Resident 协作 | `<前缀>-society.service` | 同一单元 | Dev 工作树 |

已安装实例还有 `<前缀>-postgres.service`、`<前缀>-nats.service`（Core NATS 消息服务）和 Provider 账号服务（例如 `<前缀>-codex-app-server@<账号>.service`）。它们不属于这五个组件，代码激活不重启它们。

**共享包**是 `scripts/portal_source_layout.py` 的 `SHARED_PATHS` 列出的五个目录：`management_common`、`management_portal/execution`、`management_portal/resources`、`management_portal/society_service` 和 `management_portal/hatg`。配对运行时为每个进程设置 `PORTAL_SHARED_SOURCE_ROOT=<Dev 的 code/>`；`management_portal` 包被导入时安装一个导入钩子，把这五个包定向到该目录，所以从 Release 工作树启动的进程也使用 Dev 中的共享包。

**用户入口路由器**（Portal Router，`management_portal/portal_deployment_router.py`）是浏览器请求最先到达的 Portal 进程。它为每个请求选定 `dev` 或 `release`，再把请求转发给对应的 Web。本站运行两个读取同一份登记表的路由器：

- `<前缀>-router.service`：配对运行时生成的路由器，端口 28399，即安装配置中的公共 Web 端口，也是各服务环境中 `PORTAL_URL` 指向的地址。
- `portal-user-router.service`：本站原有的 HTTPS 网关路由器，监听 `127.0.0.1:18084`；主机 Nginx 把局域网 HTTPS 和 WireGuard 隧道流量转发给它（`decision_85222f2d316b`）。它登记在 `development.gateway_unit` 中，配对运行时只给它追加一个 systemd drop-in（附加到已有单元上的配置片段），单元文件本身和它读取的网络设置文件不由配对运行时生成。

**部署登记表**（`config/deployments.json`）列出路由器可以选择的部署 ID、显示名称和本机地址。两个路由器的环境变量 `PORTAL_DEPLOYMENT_REGISTRY_PATH` 都指向它。

**服务控制面**是读取和重启这些服务的入口：命令 `./scripts/portal_services`（实现在 `scripts/portal_services.py`，在 `code/` 目录执行）和 ISA 指令 `GetPortalStatus`、`RestartPortal`。它们都调用配对运行时，只操作 systemd 用户单元，不修改数据库。

**发布激活**是发布命令 `python3 code/scripts/tfi_dev.py publish`（ISA `PublishRelease`）把 Release 工作树快进并推送之后，由配对运行时按本次改动的文件重启受影响单元并检查健康的过程。它在一个由 `systemd-run --user` 启动的临时单元中运行，发布命令只拿到结果文件（receipt）的路径。

**构建标识**（build ID）是进程启动时从环境变量 `PORTAL_BUILD_ID` 读到的代码标识：Dev Web 为 `dev-<Dev 工作树 HEAD 提交的前 12 位>`，其他组件为发布激活写入 `config/generation.env` 的 `portal-<Release 提交的前 12 位>`。它只用于显示和诊断，来源和读取方式见 `decision_99bb5dc0f96b`。

## 设计点

### 进程与源码

- **一个数据库、一组后台服务、两个 Web。** 两个入口的请求都由同一组 ResourceService、ExecutionService、SocietyService 和 Worker Manager 处理，Resident 任务、恢复锁和执行队列只有一份；把浏览器切到 Dev 不会隔离后台工作。
- **后台服务从 Dev 工作树运行。** 四个后台单元的 `PYTHONPATH` 和工作目录都是 Dev 的 `code/`。服务重启时读入的是 Dev 工作树当时的文件内容，包括尚未提交的修改，所以重启任何后台组件，Dev 与 Release 两个入口同时受影响（`decision_08bb2d9141f9` 的“并行修改的明确取舍”）。
- **两个 Web 的非共享代码各自来自本工作树。** Dev Web 运行 Dev 工作树，Release Web 运行 Release 工作树中最后发布的提交；页面、静态文件和 Web 独有的 Python 模块因此可以不同。HATG 指令目录属于共享包，Web 指令实现属于各自的 Web（`decision_08bb2d9141f9`）。
- **权限系统的存储格式是两个 Web 与后台服务之间的共享协议。** `management_portal/permission_system/` 不在共享包中：后台服务和 Dev Web 使用 Dev 的实现，Release Web 使用 Release 的实现，三者读写同一份权限实例与模板记录（PostgreSQL schema `permission`，decision_b820faf42218）。修改表结构或模板格式前，必须确认 Release Web 当前的代码也能读取新格式；读取失败按错误处理，不当作放行。
- **代码在进程启动时加载。** 配对运行时不监视源码变化。Dev 中的 Python 修改要等负责的单元经 `restart`、`request-restart`、`RestartPortal` 或一次发布激活重启后才生效；没有被重启的进程继续运行原来的代码，也继续报告原来的构建标识。

### 浏览器请求的选路

- **路由器把每个请求转发给恰好一个入口。** 它不改投另一个入口，也不重放已经送出的请求，避免有副作用的操作执行两次。
- **目标 Web 拒绝连接时，路由器等它重启完成。** 连接被拒绝说明请求还没有送到 Web。路由器按 0.25、0.5、1、2 秒（之后每次 2 秒）的间隔重新连接，最多等 90 秒，连上后才送出请求；等待期间客户端断开时放弃这次请求。超过 90 秒仍连不上时返回 `502`（错误码 `backend_unavailable`）；浏览器的页面请求（`GET` 或 `HEAD`，且 Accept 含 `text/html`）收到一张每 5 秒自动重试的提示页。
- **已登录用户可以选择自己使用的入口，选择保存在浏览器 Cookie 中。** 路由器把用户名和部署 ID 一起写入 Cookie `db_portal_deployment`，有效期 180 天。只有 Cookie 中的用户名与当前登录用户一致、该部署仍在登记表中时，这个选择才生效。
- **没有有效选择时进入 Release。** 默认进入 Dev 的用户名单来自环境变量 `PORTAL_ROUTER_DEV_USERS`，未设置该变量时取站点配置 `deployment.compatibility.domain_values.router_dev_users`。配对运行时启动路由器时把这个变量设为空，本站 HTTPS 网关单元也设为空，所以当前所有用户在没有选择时都进入 Release。默认部署未登记时依次改用 Release、Dev。
- **账号安全相关的请求在 Dev 已登记时总是交给 Dev。** 包括 Passkey、找回密码、账号接口、设置页及其脚本与样式；未登录用户访问 `/login` 和 `/login/verify` 也交给 Dev。同一次安全操作因此不会跨越两个版本不同的 Web。完整路径和其余转发规则见 `decision_85222f2d316b`。
- **登记表是入口和地址的唯一来源。** 路由器启动时登记表缺失或无效就启动失败；运行中登记表被改坏时继续使用最后一份有效内容，文件修正后的下一次请求自动生效。

### 服务控制

- **读取状态不改变任何服务。** `status`、`health` 和 `GetPortalStatus` 对每个入口读取其 Web 的 `/api/health`；Web 可以连接、`ok` 不为假、报告的部署 ID 与所查入口一致时，该入口可用。
- **按组件重启依照调用方给出的顺序逐个执行。** 缺省组件为 `web`、`resource`、`execution-service`、`lineage`；ExecutionWorker 只在显式指定 `execution-worker` 时处理。对 `dev` 或 `release` 重启后台组件，重启的都是同一个共享单元；`web` 只重启所选入口的 Web。
- **重启成功的条件是每个单元操作都成功、所选入口的 Web 健康。** 用 `USR1` 强制更换 ExecutionWorker 时，还要等到新的 Worker 接手。任何一步失败时返回错误，后续组件不再处理，控制面不自动换回旧代码。Web 怎样停止、ExecutionWorker 的换代信号见 `decision_37f98f8fcb4b`。
- **从 Web 发起的重启在临时单元中执行。** `request-restart` 和 `RestartPortal` 先校验组件名，再用 `systemd-run --user` 启动一次性的 `tfi-pair-restart-<id>` 单元执行同样的重启，立即返回请求 ID。发起请求的 Web 被重启后，这个单元继续完成剩余组件和健康检查。每次请求启动一个新单元，请求之间不合并。
- **同一入口的命令行重启依次执行。** `portal_services restart` 持有该入口的操作锁，后到的命令等前一个结束再执行；`status`、`health` 和 `request-restart` 不取这把锁。
- **发布激活只重启受改动影响的单元。** 激活按本次发布改动的文件选出单元（对照表见实现说明），逐个重启，最后检查两个 Web 和公共路由器的健康，并把结果写入 receipt（`decision_99bb5dc0f96b`）。没有受影响的单元时不重启任何服务。
- **整站启动和停止由安装器负责。** 服务控制面只提供状态读取和重启；`scripts/install_tfi_release start|stop --root <安装根目录>` 启动或停止整个已安装实例，影响两个入口共用的全部服务（`decision_ab9880dc4c9d`），过程见实现说明。

### 运行目录

- **配置与后台运行文件在安装根目录下。** `config/` 保存安装配置和由它生成的文件，`runtime/` 保存后台服务的 socket、Worker 健康文件和激活记录。
- **两个 Web 各自的运行文件在部署目录下。** 实例 `runtime/` 下的 `deployments/<部署>/` 存放该部署自己的运行文件，例如该 Web 接收 ISA 指令的 socket；两个 Web 共用的上传文件放在 uploads 区域（《Portal 实例协议》）。业务数据库、AgentData、Resident Folder 和业务工作区由各自的服务管理，不在这些目录中。

## 实现说明

### 配置与进程

`instance.json` 是实例与部署的唯一配置来源。`deployments.dev.code_root` 和 `deployments.release.code_root` 指向两个工作树的 code 目录，`web_port` 指定各自端口。`tfi_development_runtime.py configure` 将配对写入清单；不维护 Git common directory 内的额外配置。主机登记表只保存实例名称与根目录。

服务由 `management_portal.service_runtime` 按实例清单启动。后台组件使用 Dev 代码，Web 使用所属部署的代码；所有服务收到 `PORTAL_INSTANCE_ROOT`，Web 另收到 `PORTAL_DEPLOYMENT`。服务使用操作系统 HOME。`services.components` 声明实例拥有的组件；网关是 `<前缀>-gateway`，监听 `network.entry_port`，不另建配对路由器。Provider 程序及 Python 来自代码依赖，账号目录是主机上的显式登记。

### 路由与控制

部署路由表从清单的 deployments 直接生成。`/__portal_router/health` 返回网关健康；`/__portal_router/status` 返回数据库运行状态。登录后的 `GET /__portal_router/preference` 返回 `default_deployment`、`effective_deployment`、`preference` 与 `deployments`；POST 接受 `{"deployment":"dev"}` 或 `release`，写入 `db_portal_deployment` Cookie。不读取旧 Cookie。Cookie 仅选择入口，登录身份仍由会话验证。

`portal_services status --all` 或 `--deployment dev|release` 查询部署，返回 `deployment` 或 `deployments`。`restart` 与 `request-restart` 接受部署 ID，组件名为 web、resource、execution-service、execution-worker、lineage。ISA `GetPortalStatus`、`RestartPortal` 使用 `deployment` 参数；`PublishRelease` 使用同一发布器。Web 重启只影响指定部署，其余组件由同一实例共享。

显式 Worker 重启发送 USR1 并等待新 Worker 就绪；发布只发送 HUP，按构建版本决定是否换代。普通发布保留 PostgreSQL、NATS 与 Provider 账号服务。

### 发布与目录

`tfi_dev.py publish` 提交 Dev、快进 Release 并从 Release 推送 main，然后为所选实例排队激活任务。激活根据文件所属组件与运行进程的导入记录确定重载范围，依次处理 resource、execution、society、worker、web、dev-web、gateway。回执记录 ok、components 和 health.dev/release/public；推送成功不替代激活成功。

实例根保存 instance.json、site.json、secrets、database、cache、runtime 与默认 data 区域。Web 的 socket、RPC 签名密钥与重启锁在 `runtime/deployments/<部署>/`；Release 的 generation.env 在 `runtime/deployments/release/`。运行操作与激活回执在 runtime；业务上传在 uploads 区域，Provider 会话状态在 provider-state 区域。配置不再拆分到 config/。

### 生命周期

`tfi_release_install.py` 提供 create、start、stop、status、retire、first-admin、configure-ports、update-code、list。通过 --instance 或 PORTAL_INSTANCE_ROOT 选择实例。停止核对实例拥有的全部服务，包括网关和 Provider 激活作业；启动按依赖先起 PostgreSQL，再启动其余声明组件；退役停止并删除实例单元、注销主机登记并删除实例根。普通代码更新替换代码、重新启动，数据库由服务启动时更新；需要退路时先使用备份软件备份。
