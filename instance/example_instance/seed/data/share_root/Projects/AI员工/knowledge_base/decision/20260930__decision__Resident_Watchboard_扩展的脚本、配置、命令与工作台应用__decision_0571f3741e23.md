---
kb_id: decision_0571f3741e23
type: decision
project: AI员工
created_at: '2026-09-30'
version:
  number: 22
  created_at: '2026-09-30T13:04:00Z'
  created_by:
    user: tfi-exported-user
    agent: resident_83172d354aa9
  updated_at: '2026-10-19T08:43:49Z'
  updated_by:
    user: ''
    agent: resident_2e9ea0072551
importance: 6
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/static/lineage_workbench.js
  - ${code_root}/management_portal/static/portal_resident_service.js
  - ${code_root}/management_portal/society_service/watchboard_lineage.py
  - ${code_root}/management_portal/society_service/resident_watchboard_domain.py
  - ${code_root}/management_portal/watchboards/service.py
  - ${code_root}/management_portal/watchboards/telepus.py
  - ${code_root}/management_portal/http_agent_workbench.py
  - ${code_root}/management_portal/static/lineage_workbench_watchboard.js
  - ${code_root}/management_portal/static/watchboard_applications/registry.js
  - ${code_root}/tests/cases/test_resident_watchboard.py
  - ${code_root}/tests/cases/test_watchboard_applications.py
  - ${share_root}/Skills/create-resident-watchboard/SKILL.md
  related:
  - decision_898b8911a2ff
  - decision_ce44377c3257
  - decision_ccb99a00eebd
  - decision_0f57656a2acd
  - decision_692a484e9bea
  - decision_5bf5bfbd52cb
  - decision_8838269736d3
  supersedes: []
  derived_from: []
---
# Resident Watchboard 的脚本、配置、命令与工作台应用

Watchboard（监控板）是可以安装在 Resident 上的扩展面板。有些 Resident 背后有自己的确定性脚本，例如每天生成日程草稿、收集论文或检查知识库。安装 Watchboard 后，Resident 拥有一个脚本目录，里面放这些脚本、它们的配置和状态文件；Portal 在 Society 页面的 Resident 对话（Agent Console，decision_ce44377c3257）旁边显示一块面板，让管理这个 Resident 的用户查看脚本状态、编辑配置、运行脚本声明的命令，并使用为特定 Resident 定制的操作界面。Resident 自己的对话和执行不受影响，Watchboard 只是在它旁边提供一块可操作的面板。

本篇说明 Watchboard 的目录与清单文件、安装与移除、读取与权限、通用面板、配置编辑、命令运行，以及为特定 Resident 提供专用界面的工作台应用。Resident 身份与字段归属见 decision_0f57656a2acd，Resident 文件夹结构见 decision_ccb99a00eebd。

## 基本概念

| 名称 | 含义 |
| --- | --- |
| 安装定义 | Resident 所属 Lineage 中的一组组件：面板标识事件源，以及每条命令的手动事件源和 RunScript Effect。组件存在时面板已安装。 |
| 脚本目录 | Resident 自有工作区中的 `watchboard/`，保存清单、脚本、配置和状态。下文的相对路径都相对于这个目录。 |
| `agent.yaml` | 脚本清单：入口脚本、配置文件、状态文件和可以运行的命令。Portal 只运行其中声明的命令。 |
| `watchboard.yaml` | 展示清单：面板的标题、说明和布局名称，供通用面板和工作台应用识别。 |
| 配置文件 | 脚本读取的配置，默认 `config/config.yaml`。有权限的用户可以在面板中编辑。 |
| 状态文件 | 脚本写出的当前状态，默认 `state/status.json`。面板读取并显示它。 |
| 命令 | `agent.yaml` 中声明的一项操作，例如 `status`（读取状态）、`run-once`（执行一次工作循环），运行时调用入口脚本并传入命令名和参数。 |
| 运行记录 | 手动触发产生的 Lineage Run，以及其中 RunScript 指令产生的 ScriptRun；记录状态、输出和产物。 |
| 工作台应用 | 为特定 Watchboard 提供的专用界面和操作，例如 Telepus 的联系人管理（decision_692a484e9bea）、WireGuard 的设备管理。没有匹配的应用时显示通用面板。 |

```text
<脚本目录>/
├── README.md          目录说明
├── watchboard.yaml    展示清单
├── agent.yaml         脚本清单
├── config/config.yaml 配置文件
├── src/main.py        入口脚本
├── state/status.json  脚本写出的状态
└── logs/
```

## 设计点

### 安装与移除

- **用户从 Resident 扩展菜单安装面板。** 安装以 `<owned_workspace_id>/file/watchboard/<相对路径>` 地址补齐自有工作区中缺失的骨架文件，再在所属 Lineage 中添加组件。已有文件沿用原文。定义提交失败时面板未安装，已写文件保留供重试使用。
- **命令与面板一起定义在 Lineage 中。** 每条命令对应一个手动事件源和一个执行脚本的 Effect；更改运行计划通过 Lineage 定义操作完成。清单是安装输入，修改清单后可以移除再安装以重新生成命令映射。
- **移除保留脚本、配置和状态。** 移除删除这块面板对应的组件及关联连线，后续不能再从面板触发。已经启动的脚本仍按其运行记录管理，需要停止时使用取消操作。
- **Fork 保留独立文件，安装使用新 Resident 的组件引用。** 来源文件复制到目标自有工作区，不沿用来源组件 ID。目标显式安装后才有自己的命令入口，不自动启动后台消费者。


### 读取与权限

- **普通 Resident 的 Watchboard 对有效的认证身份开放。** 个人 AMA 及其分支仍检查其工作区访问权限。
- **写入由各指令统一授权。** 安装与移除使用 Lineage 定义指令，配置使用工作区文件指令，运行和取消使用 Lineage/脚本运行指令。动态保护集合决定所需资源与级别；策略变更前后使用同一调用链。界面的能力字段只控制展示，不能替代指令授权。
- **清单有错时仍然可以读取。** `agent.yaml` 不是 YAML 对象、`commands` 不是映射或声明了命令却缺少 `entrypoint` 时，读取结果带上清单错误，面板显示错误，其他内容照常显示；此时所有命令都不能运行。

读取结果包含：脚本目录及其是否存在、两个清单的内容和错误、最多 8 个配置文件（内容、大小、修改时间、能否编辑）、状态文件及其解析后的内容、最多 40 个命令、各命令最近的脚本运行记录，以及权限。结果带有签名（`signature`），内容不变时签名不变，页面据此跳过重绘。

### 通用面板

- **面板显示在对话右侧，可以拖动调整宽度，也可以收起。** 收起状态和宽度保存为这个用户的工作台偏好。
- **打开时立即加载。** 切换 Resident、打开页面或重新显示时立即读取完整的 Watchboard；之后只在有命令正在运行时每 2 秒刷新一次，空闲时不轮询。第一次读取失败时自动重试，最多 3 次，之后显示错误和“重试”按钮。
- **没有匹配的工作台应用时显示通用面板。** 通用面板从上到下显示：
  - 标题（依次取 `watchboard.yaml`、`agent.yaml` 的标题和 Resident 标题）与说明；
  - **Run Controls**：状态文件中的状态摘要、每条命令一个运行按钮、“Full config”（打开配置编辑弹窗）、“Raw status”（查看状态文件原文），以及最近一次运行的结果；

### 编辑配置

- **配置按文件整体保存。** 用户从“Full config”打开配置文件，保存时以新文本替换文件。
- **弹窗只编辑脚本目录内已存在的小型文本文件。** 读取展示最多 120,000 字节；二进制和截断内容不可编辑。实际文件写入使用工作区文件服务的路径、文件类型和大小约束。
- **内容变化时拒绝覆盖。** 读取返回文件内容的 SHA256 摘要；浏览器以 `expected_version` 原样传回。版本不同提示冲突，用户重新读取后再决定怎样合并。


### 运行命令

- **运行按钮触发已经安装的命令组件。** 有必填参数时，面板先收集 JSON 参数对象，再触发对应事件源。尚未安装的清单命令没有运行入口。
- **脚本作为 RunScript 主机进程运行。** Effect 保存入口、命令映射、参数声明、可选时限和互斥键。脚本以 dirty-base 用户访问主机文件、凭证、服务与网络；Portal 不向运行环境额外注入服务凭据。
- **状态、日志和取消属于同一次脚本运行。** 面板显示等待、运行及终态，使用“输出”按游标读取 stdout/stderr，使用“取消”停止仍在运行的脚本。脚本退出状态和日志由 RunScript 保存，不再由面板维护另一份运行记录。
- **同一面板的命令共享互斥键。** 并行触发交给 RunScript 处理，不在浏览器或 Watchboard 服务中再启动互斥线程。需要周期执行时，用户在 Lineage 中增加时间事件源，脚本每次执行有界的一轮工作。


### 工作台应用

- **应用按 Watchboard 数据自动匹配。** 浏览器依次检查已注册的应用，第一个匹配的应用替换通用面板；服务端同样为第一个匹配的服务端应用补充它需要的数据。
- **应用出错时退回通用面板。** 服务端补充数据失败时，读取结果带上 `application_error`（“工作台应用投影暂不可用”），其余内容照常返回；浏览器端应用渲染失败时，显示通用面板和提示“工作台应用暂不可用，已显示通用面板。”。一个应用的故障只影响这块面板。
- **切换 Resident 时，应用实例和弹窗一起释放。** 旧 Resident 的响应到达时，不会替换新 Resident 的面板，也不会关闭新面板中的弹窗。

当前注册的应用：

| 应用 | 匹配条件 | 提供的内容 |
| --- | --- | --- |
| Telepus | Resident 是固定的 Telepus Resident | Telegram 联系人与 Portal 用户的对应表；保存（`save`）与删除（`delete`）联系人（decision_692a484e9bea） |
| WireGuard | `watchboard.yaml` 的 `layout` 为 `wireguard-device-manager` | 设备与连接管理界面 |

Telepus 有服务端部分，负责补充数据和执行操作；WireGuard 只有浏览器部分，使用通用的读取、配置和命令接口。2026-10-05 起，DailySchedule、RobotCompany、PaperCollector 与 KnowledgeHygiene 四个应用随对应的旧 Agent 程序一起删除：这些工作改由 Resident 按职责说明完成（decision_5ff0cb51ed84），不再有 Watchboard 脚本目录。

Telepus 联系人的授权、身份映射和状态保存由 decision_692a484e9bea 定义。Watchboard 仅承载其应用视图和操作入口。

## 实现说明

### 组件与接口

`resident_watchboard_domain.py` 只投影清单、配置与状态。`watchboard_lineage.py` 从当前定义生成安装计划：面板标识事件源使用 `client_key=watchboard.<resident_id>.view`，各命令组件 ID 由 Resident 和命令身份确定。文件写入与图提交相互独立；Lineage 定义使用读取时的 revision 作冲突前提，组件和连线作为一次原子修改提交。

浏览器 `PortalResidentService` 组合以下既有指令。Effect 和脚本执行逻辑没有 Watchboard 专用指令：

| 操作 | 调用 |
| --- | --- |
| 安装 | `UpdateWorkspaceFile` 补齐文件 → `ApplyLineageChanges` 创建组件和连线 |
| 移除 | `ApplyLineageChanges` 删除组件和连线，保留工作区文件 |
| 保存配置 | `UpdateWorkspaceFile`，携带内容版本 |
| 运行 | `TriggerEvent`，以 prompt 传递序列化参数对象 |
| 跟踪运行 | `GetTriggerPath` 按原触发路径查找 RunScript 调用，`GetScriptRun` / `ListScriptRuns` 读取状态 |
| 输出、取消 | `ReadScriptRunOutput` / `CancelScriptRun` |

`GET /api/agent-workbench/residents/watchboard?id=<resident_id>` 仍是页面只读投影入口；工作台应用的操作入口属于应用自身。安装、移除、配置保存和运行命令均没有专用 HTTP 写入口或 Domain RPC。

`run_watchboard_command.py` 是 RunScript 的轻量入口：从 Effect 输入取出安装时的命令定义，把声明过的参数转换成 argv，然后 exec 工作区内的入口脚本。参数转换在这个入口统一完成；取消、时限、退出码和日志由 ScriptRun 持有。


### 清单格式

`agent.yaml` 必须是一个 YAML 对象：

- `entrypoint`：声明了命令时必填，入口脚本路径必须位于脚本目录内且真实存在；
- `commands`：必填，以命令 ID 为键、命令声明为值的映射，最多读取 40 条。每条命令可以有 `description`、`risk`、`timeout_seconds`、`map_to`（代替命令 ID 传给脚本）和 `args`（参数声明，每个参数可以指定 `option` 作为命令行选项名，缺省为 `--参数名`）；
- 可选字段：`config` 或 `configs`（配置文件路径）、`status_path`（状态文件路径）、`default_timeout_seconds`。每条命令可用 `timeout_seconds` 覆盖默认时限；时限为可选正整数，无最大值，均未提供时持续运行到结束或取消。清单不声明定时，时间事件源在 Lineage 中定义（decision_5ff0cb51ed84）。

只承载应用视图的面板使用 `commands: {}` 与 `configs: []`，可以省略 `entrypoint`。安装只建立面板标识组件，不生成脚本、配置文件、命令事件源或 Effect。

`watchboard.yaml` 提供 `title`、`description` 和 `layout`；`layout` 用来匹配专用界面，例如 `wireguard-device-manager`。

### 工作台应用的接入

服务端应用是 `watchboards/` 下的一个模块，提供 `APPLICATION_ID` 和三个函数：`matches(context)` 判断是否适用，`project(context)` 返回要补充到读取结果中的字段，`act(context, operation, arguments)` 执行一项操作。`context` 包含 Resident ID、当前用户、Watchboard 数据和脚本目录，通用命令通过页面的 Lineage 指令流程执行。`watchboards/service.py` 按 `portal_features.py` 中功能声明的顺序取各功能的 `watchboard.application` 接入点（当前只有 Telepus 的 `watchboards/telepus.py`，decision_15aab18c0ab3），依次检查这些服务端应用，第一个匹配的应用附加自己的数据和 `application_id`。没有打包 Telepus 时没有服务端应用，Watchboard 只返回通用数据。

执行应用操作时，Portal Web 先检查请求带有认证身份且 Watchboard 已启用（否则报错“工作台应用操作需要认证身份和已启用的 Watchboard”），再取得脚本目录中的文件锁 `.portal-application.lock`，锁内重新读取 Watchboard 数据并再次检查权限、脚本目录和应用匹配，然后调用 `act`，返回完整的 Watchboard 数据。每个应用自己校验操作名和参数，只接受它声明过的操作。Telepus Contacts 对任何登录用户开放绑定和解绑本人的 Telegram；页面操作从认证上下文确定用户，其他用户的联系人行只读，管理员代绑和代解绑通过专用 ISA 指令执行。

浏览器端应用调用 `PortalWatchboardApplications.register({ id, matches, create })` 注册，`create` 返回的实例提供 `render`、`bind`、`key` 和可选的 `destroy`。Watchboard 面板向应用提供视图、命令、弹窗、对话和通用呈现组件，以及 Resident Service 的应用操作接口。通用配置编辑由 Watchboard 面板自己的“Full config”弹窗负责；Society 页面的其余状态由页面自己保留。Society 页面在 `lineage.html` 中显式加载受维护的应用脚本；Resident 目录中的脚本文件只由 Watchboard 命令运行，不加载进页面。

新增一个应用只需要新增应用模块，并在注册与页面加载入口中登记；Watchboard 的通用部分和 SocietyService 不需要知道应用的名称或业务字段。

### 保留的基础服务边界

Telepus 的 Watchboard 展示清单保存在其自有工作区 `watchboard/`，使用所属 Lineage 的面板标识组件表达安装事实，清单没有命令和脚本配置。桥接由独立的 router、portal-refresh、breakglass-worker systemd 服务运行，仍使用 Lineage 的 `script/`；联系人数据仍由 Telepus 应用保存在 Lineage 的 `state/telegram/`。面板的安装和移除只改变展示组件，不启动或停止桥接服务，也不移动联系人数据。

WireGuard 的 `status` 和 `save-metadata` 采用通用的手动事件源与 RunScript Effect。脚本、配置和状态存放在 Resident 自有工作区的 `watchboard/` 内；脚本通过主机上的 SSH 访问 VPS。设备页沿用通用命令接口，刷新设备状态和保存展示资料均由 Lineage 触发，运行状态与输出由 ScriptRun 提供。

所有 Watchboard 的安装事实统一来自 Lineage 定义，清单、配置和状态由 Resident 自有工作区持有。LineageData 不保存独立的 Watchboard 配置表。没有面板标识组件的历史实例保持未安装，旧脚本文件与运行记录按各自所属领域保存。

`watchboard_capabilities` 保持 `installed`、`can_install`、`can_remove`、`can_view` 四个字段。所有面板从定义计算安装事实，Telepus 同样使用通用安装和移除操作。页面写入的最终授权由权限系统判定（decision_b820faf42218）。
