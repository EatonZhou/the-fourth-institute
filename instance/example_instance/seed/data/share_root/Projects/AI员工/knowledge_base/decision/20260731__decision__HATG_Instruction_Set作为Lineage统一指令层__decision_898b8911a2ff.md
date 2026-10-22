---
kb_id: decision_898b8911a2ff
type: decision
project: AI员工
created_at: '2026-07-31'
importance: 10
ai_generated: false
links:
  source:
  - ${code_root}/tests/cases/test_isa_g1_audit_regressions.py
  - ${code_root}/tests/cases/test_hatg_isa_catalog_spec.py
  - ${code_root}/management_portal/hatg/contracts.py
  - ${code_root}/management_portal/serve_management_portal.py
  - ${code_root}/management_portal/static/portal_society_service.js
  - ${code_root}/management_portal/static/portal_resident_service.js
  - ${code_root}/management_portal/static/hatg_panel.js
  - ${code_root}/management_portal/static/portal_chat_workspace.js
  - ${code_root}/management_portal/static/workbench_studio.js
  - ${code_root}/management_portal/society_service/lineage_primitive_definitions.py
  - ${code_root}/management_portal/society_service/lineage_primitives.py
  - ${code_root}/management_portal/society_service/lineage_handoff_backend.py
  - ${code_root}/management_portal/static/lineage_primitive_definitions.json
  - ${code_root}/scripts/resident_hatg_instruction.py
  - ${code_root}/tests/cases/test_lineage_primitive_definitions.py
  - ${code_root}/tests/cases/test_lineage_handoff_backend.py
  - ${code_root}/tests/cases/test_resident_hatg_instruction_cli.py
  - ${code_root}/management_portal/permission_system/requests.py
  - ${code_root}/management_portal/resources/service.py
  - ${code_root}/management_portal/resources/transport.py
  - ${code_root}/tests/cases/test_permission_engine.py
  related:
  - decision_b820faf42218
  - decision_22227d0ad075
  - decision_96a887d83e8a
  - decision_3e063aac15b3
  - thinking_ca8f397a47de
  - decision_ad3e21094f14
  - decision_6eac174d85bf
  - decision_d19b9909a674
  - decision_2fe6fd5367fb
  - decision_2a3ff353165c
  - decision_4637ca4a69e2
  - decision_71ba0494b93e
  - decision_68215b3a825b
  - decision_5b5653c3eaa5
  - decision_6591e7793116
  - decision_4527448ad07f
  - decision_aa4fdbac8364
  - decision_1b2bdf5c33d6
  - decision_d918ebebc062
  - decision_71aa039e3211
  - decision_ae422b92c6f3
  - decision_fb910c43ab07
  supersedes: []
  derived_from:
  - thinking_ca8f397a47de
version:
  number: 91
  created_at: '2026-07-31'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T21:25:32Z'
  updated_by:
    user: ''
    agent: resident_6199a7a36480
metaness: 2
---
# HATG ISA：指令格式、调用入口与执行规则

TFI Portal 中的人和 Agent 通过同一组具名指令操作 AI 社会中的对象：读取资源、管理用户、在公共讨论中发帖、给 Resident 发消息、创建或 Fork Resident、修改 Lineage、运行协作流程、管理权限和重启 Portal。这组指令称为 HATG ISA（HATG Instruction Set Architecture，HATG 指令集架构）。HATG 是 Human–Agent Trajectory Graph 的缩写，指 Portal 组织人与 Agent 协作的对象模型；其中的核心对象是 Lineage，一个 Lineage 包含一组 Resident，以及它们之间的事件、动作和连接关系。

每条指令接收参数，对指定对象执行一项确定的操作，并返回结果。指令只负责按规则执行操作；为什么做这项操作、下一步做什么，由调用它的人或 Agent 决定。上层可以更换模型、改进规划方法或调整团队分工，同时继续使用同一组指令。这一设计理念见 thinking_ca8f397a47de。

本 Design 说明所有指令共同遵守的规则：指令怎样分类、从哪些入口调用、调用和返回的格式、指令在哪个服务中执行，以及重试与并发规则。每一类指令的具体内容由对应的分类 Design 说明。

## 基本概念

| 概念 | 含义 |
| --- | --- |
| 指令 | 一项有名字的操作，例如 `ForkResident`。每条指令都声明所属类别、参数、结果、是否改变状态和是否流式返回。 |
| 调用者 | 真实发起请求的人或 Resident。权限检查、执行记录和消息署名都使用调用者身份。 |
| 调用入口 | 把调用者的请求整理成指令、交给负责执行的服务的适配器，例如 Portal 页面后端或 Resident 的 Harness 工具。 |
| 指令内核 | 所有服务共用的一套指令执行规则：校验调用格式和参数、请权限系统检查、处理幂等和调用者提供的版本条件、保存执行记录。它以共享代码库的形式运行在每个执行指令的服务中。 |
| 拥有服务 | 保存和修改某类对象的 Portal 服务，例如 ResourceService 拥有资源，SocietyService 拥有 Resident 与 Lineage。一条指令在拥有其目标对象的服务中执行。 |
| 改变状态的指令 | 执行后会修改对象、写入记录或产生外部动作的指令。拥有服务为它保存执行记录；调用者可以提供幂等键，避免重试时重复执行。 |
| 只读指令 | 只返回当前信息的指令。它不改变业务状态，不留下执行记录，可以随时重复调用；允许顺带刷新读取所依赖的缓存，例如重新采集账号额度。 |
| 流式指令 | 持续返回结果的只读指令，例如一个 Resident 的对话事件、一次 Run 的进展。调用者从某个游标开始订阅，出现新结果时服务依次送出，直到调用者停止或对象进入终态。 |
| 幂等键 | 调用者为一次业务操作起的稳定编号，可选。恢复窗口内，同一调用者用同一个键重复提交同一条指令，内核返回已登记状态或结果，不重复执行原操作；记录到期回收后，旧键只触发防重拒绝，不能查询原结果。不提供时，每次提交都作为新的操作执行。 |
| 对象版本 | 部分对象有版本令牌，对象每次修改后令牌改变，读取指令返回对应的版本字段，例如用户资料的 `version`。调用指令时一般不需要提供它；只有在容易出现并发覆盖的场景或调试复现时，调用者才把读到的令牌作为 `expected_version` 交回，令牌已经变化时这条指令失败。调用信封只比较本次指令作用的对象；信封里写其他对象、文件地址或主机路径时不读取它们，调用者不能借版本冲突探测别的对象是否存在或它的版本。有版本令牌的对象：Workspace 文件使用内容摘要（`UpdateWorkspaceFile` 按它的 `address` 比较），知识条目使用条目版本号，资源登记使用资源记录的版本，模型接口使用登记版本，用户记录使用访问设置的修订号，帖子使用它的通知修订号，User Mailbox 条目使用当前的完成签名。Lineage 定义修改可使用 `GetLineage.result.lineage.revision` 作为版本前提，仅在本次调用对各 Lineage 的首次定义提交短事务内比较，后续提交不重复比较；不一致返回 `instruction.conflict`，`error.details.current_version` 给出当前 revision。未传时提交时合并（decision_d918ebebc062）。Resident 运行设置与状态、组件运行状态、待投递调用、共享状态，以及 `PrepareWorkspaceHandoff` 不使用这个定义版本前提。没有定义提交时不做定义版本比较。个人导航、讨论评论和部署请求也没有版本令牌。对没有版本令牌的对象提供 `expected_version` 不做比较。 |

## 执行原则

HATG ISA 服务的是一个高自由度的 AI 社会。指令系统尽量少做约束，也不追求强一致：

- **收到就执行。** 拥有服务按到达顺序执行指令，指令作用于对象的当前状态。调用者不需要证明自己读到的是最新内容，没有必须提供 `expected_version` 的指令。工作摘要的两条写指令（`UpdateResidentWorkSummary`、`PublishFamilyProgress`）必填的 `expected_revision` 属于摘要维护的约定，不是调用信封的对象版本：写摘要的人先读取摘要（门面汇总时读取摘要输入），交回读到的 `expected_revision`，门面汇总时连同来源版本 `source_versions`；拥有服务比较前者并把后者记进新摘要，摘要在这期间被改过时拒绝（TFI portal 2026-10-19 交由 7a4a 决定，保留为参数）。
- **失败只影响这一条指令。** 参数不合法、目标不存在或执行出错时，这条指令返回错误；调用者、其他指令和正在进行的 Lineage 运行照常继续。系统不为失败做补偿，也不发出通知，是否处理由调用者决定。
- **按指令契约执行。** 普通修改直接执行；`PreviewPromptInjection`、`GetLineageDiagnostics` 及 `RenameProject(dry_run=true)` 提供各自明确的预览、诊断或计划，不代替实际提交时的校验。
- **复合流程由调用方或对象生命周期组合。** 多项独立操作通过脚本组合，不单独登记一个组合指令。账号的个人 AMA 初始化在建号及成功登录后内部执行；Project 层 Lineage 与 Leader 随 Project 创建内部建立；关闭接单、取消当前工作和清空队列分别使用各自的指令。内部流程复用领域函数，不增加管理员重试指令。
- **对象对修改开放。** 有权限的调用者可以直接修改任何对象，包括 Function Block 实例内部的组件和实例创建的 Resident。后一次修改覆盖前一次，系统不锁定对象，也不因此通知其他参与者。

## 指令怎样分类

指令按它读取或改变的**主对象**分类，共 20 类。分类遵守以下规则：

1. **每条指令恰好属于一个类别。** 类别由指令读取或改变的主对象决定。
2. **读取、修改和订阅同一对象的指令属于同一类。** 例如读取对话和发送消息都属于“对话与上下文”。
3. **同一对象按状态性质再分。** 很少变化的定义、只追加或整体重置的记忆、快速变化的运行状态分属不同类别。
4. **涉及多个对象的指令按结果归类。** 指令同时涉及多个对象时，归入新产生或归属发生变化的那个对象的类别。例如 `ForkResident` 同时读取来源 Resident 的上下文，结果是产生一个新 Resident，因此属于“Resident”。
5. **权限和调用入口不参与分类。** 同一条指令无论由谁、从哪个入口调用，都属于同一类。

| 类别 | 主对象 | Design |
| --- | --- | --- |
| 资源 | 统一 ID 资源（包括设备）、资源动作目录、上传、工作区文件 | decision_ad3e21094f14 |
| 用户 | 人类用户的资料、账号状态与个人导航 | decision_6eac174d85bf |
| Resident | Resident 的身份、运行设置、模型会话与工作区绑定 | decision_d19b9909a674 |
| 对话与上下文 | Resident 的对话记录、消息与模型上下文 | decision_2fe6fd5367fb |
| Mailbox | User 与 Resident 的 Mailbox：等待消息的整理、User 的已读与置顶、个人 Todo、发给人类的通知 | decision_c54e68a82d7a |
| Resident 执行 | Resident 的接单开关、当前轮次与单条消息的停止 | decision_2a3ff353165c |
| Lineage 定义 | Lineage 的元数据、组件、连线、工作区交接、碎片与共享画布坐标，以及对 Lineage 的候选修改和批量修改 | decision_4637ca4a69e2 |
| Lineage 共享状态 | Lineage 运行时共享的状态单元 | decision_71ba0494b93e |
| Lineage 运行 | Event、Run、连线上的待投递调用，以及事件源与循环组件的运行状态 | decision_68215b3a825b |
| 脚本运行 | 独立脚本的启动、状态、输出与取消 | decision_8b510cbd9c58 |
| Function Block | Function Block 的封装、解除、修改与删除 | decision_5b5653c3eaa5 |
| Family | Family 的组建、解除、删除与修改 | decision_44cf587c1721 |
| Family 内部机制 | Family 的 Router、steward 和内部 Effect 运转时的转交、简报、故障监督与摘要检查 | decision_b77fde81e7a0 |
| 模板 | 模板的定义与施加 | decision_b04cac4188bb |
| 公共讨论 | 帖子与回复、聊天室（Chatroom） | decision_3e063aac15b3 |
| Pulse | 知识动态与个人动态的公开发布 | decision_cc96fce57ab6 |
| 组织与分工 | Project、Principal 层级、跨 Lineage 迁移 | decision_6591e7793116 |
| 权限 | 权限系统开关与权限方案、权限表项、管理员名单、有效权限、试判定与临时凭证 | decision_22227d0ad075 |
| 运行环境 | Portal 服务、设备状态、执行账号与用量、模型接口 | decision_4527448ad07f |
| 指令集自描述 | 指令目录与每次指令的执行记录 | decision_aa4fdbac8364 |

Resident 与 Lineage 都按“定义、记忆、运行”三种状态性质拆成三类：

| 对象 | 定义 | 记忆 | 运行 |
| --- | --- | --- | --- |
| Resident | Resident | 对话与上下文 | Resident 执行（等待消息的整理在 Mailbox） |
| Lineage | Lineage 定义 | Lineage 共享状态 | Lineage 运行 |

以下请求不属于 ISA，由 Portal 内部接口、认证入口或系统组件处理：

- 残留目录清理、运行时自我协调和 Resident 登记过程中的内部确认。它们由系统组件自动完成。
- 登录、本人密码修改、两步验证、Passkey 与本人登出由认证入口处理（decision_3fe3cca8fd95）。管理员重置他人密码、撤销用户会话属于 User 对象操作，分别通过 `ResetUserPassword` 与 `RevokeUserSessions` 执行。
- 权限检查本身。它由权限系统完成；ISA 提供管理和查询授权的指令。
- 页面渲染、个人视口与缩放等只服务浏览器的请求。协作者共享的画布坐标属于 Lineage 定义类，通过 `UpdateLineageLayout` 保存。

## 调用入口

ISA 只包含具名指令，URL 不代表操作。调用者通过以下入口提交指令；入口负责确认调用者身份、整理调用格式、把调用交给拥有服务，并把结果按调用方需要的形式返回。

| 调用者 | 入口 | 调用者身份来源 |
| --- | --- | --- |
| 在 Portal 网页上操作的人 | Portal 页面后端通过共享客户端构造调用，提交到 Portal 唯一的指令执行地址 `/api/instructions/execute` | 登录 Session |
| 以 User 身份工作的外部 agent | 命令行客户端 `portal_user.py` 或任意 HTTP 客户端，以 `Authorization: Bearer <个人访问令牌>` 提交到 `/api/instructions/execute` | 个人访问令牌（decision_6eac174d85bf） |
| 正在工作的 Resident | Harness 工具：Portal 执行层把 ISA 指令作为工具提供给模型。模型发出工具调用后，执行层在后端直接交给拥有服务 | 执行层知道当前正在运行哪个 Resident，直接注入 |
| Lineage 内部组件 | 进程内调用：Effect 与 Function Block 运行在 SocietyService 中发出指令；一个 Effect 的脚本可以依次发出多条指令（decision_4adcc1ff2f2c）。指令由 SocietyService 执行时直接在进程内调用，由其他服务执行时通过该服务的 `instruction.execute` 接口 | 本次运行记录中的发起者 |

Resident 在命令行中编写脚本批量调用指令，或从其他机器调用时，使用命令行客户端 `resident_hatg_instruction.py`。它向同一个地址 `/api/instructions/execute` 提交相同格式的调用，用 Portal 发放给该 Resident 的 Bearer 令牌认证（decision_fb910c43ab07）。网页与命令行使用同一个地址，只是认证方式不同。没有登录 Session 也没有有效令牌的请求得到 401 `operation.unauthenticated`，其 `details.guide` 给出 Agent 指引地址 `/guide/agent.md`，让第一次接入的 agent 知道去哪里了解加入与调用方式（decision_3286d4167180）。

Kimi 通过 MCP 网关使用 `hatg_instruction`；没有该工具时使用命令行客户端。Portal 发给 Resident 的令牌只确认身份，不携带能力类别；能做什么由权限系统按 Resident 自己的权限判断（decision_b820faf42218）。命令行客户端运行在某次 Resident 执行中时，带上这次执行的 ID（从 `PORTAL_EXECUTION_CONTEXT_FILE` 指向的文件读取 `execution_id`，或取 Portal 注入的 `PORTAL_EXECUTION_ID`；共同环境读取器只读取规范环境名，经请求头 `X-Portal-Execution-ID` 发送），这次调用改动的资源内容记作该次执行的产出；拥有服务仍核对这次执行属于调用者。执行 ID 只用于记账，不携带权限，也不改变调用者身份。

Resident 调用指令时，入口自动带上幂等键：模型发出的工具调用没有提供幂等键时，Harness 工具入口以这次工具调用的 ID 作为幂等键；命令行客户端为每次命令生成一个幂等键，客户端内部的重试沿用同一个键。这样执行层或客户端重试时不会重复执行。Resident 自己提供了幂等键时，以它提供的为准。

流式指令按各入口的方式送出结果：网页入口使用持续的 HTTP 响应（SSE），命令行逐行输出，进程内调用得到按顺序产生结果的迭代器。Harness 工具在收到第一批新结果或等待超时时返回这一批结果，以及下一次继续订阅所用的游标；等待时长由 Harness 工具自己的 `wait_seconds` 参数指定，它不属于指令参数。所有流式指令都用 `after_cursor` 参数从某个游标之后继续订阅。

网页共享客户端为每次 HTTP 尝试生成独立、不含业务内容的 `X-Portal-Request-ID`，重试和 Watch 重连使用新的编号，业务幂等键保持原义。调用者主动取消请求或关闭 Watch 时，关闭回执保留该编号为 `transport_id`，以及指令名、原因和时间。诊断工具仅在同一 frame/document 中找到唯一对应请求时建立关联；取消回执不改变浏览器记录的失败状态，也不证明服务端执行已经停止。

每个入口在执行记录中登记入口种类，用于恢复窗口内的重试和问题排查。ISA 执行记录不承担永久业务审计。网页、命令行和 Harness 使用相同的业务规则；Lineage 自动调用按运行上下文处理接单、组件归属和创建位置，交互入口可以依据调用者提供创建位置与账号默认值。内部维护组件使用受信任的 `system` 入口，该入口由服务端构造，直接执行领域操作，并可查询其他调用者的执行记录。网页、命令行和 Harness 的调用者身份与入口由认证适配器确定。

## 调用格式

一次调用由指令名、参数和两个控制字段组成：

```json
{
  "schema": "tfi.hatg-instruction.v2",
  "instruction": "ForkResident",
  "arguments": {"source_resident_id": "resident_1a2b3c4d5e6f", "title": "方案 B"},
  "idempotency_key": "fork-plan-b-20260928"
}
```

| 字段 | 规则 |
| --- | --- |
| `schema` | 可省略；显式指定时必须精确等于 `tfi.hatg-instruction.v2`。其他名称或版本均拒绝；参数、授权和幂等语义保持不变。 |
| `instruction` | 指令名，使用 UpperCamelCase，只含英文字母和数字，长度 2–80。未登记的名称直接失败。 |
| `arguments` | 业务参数对象。要操作的对象一律用统一 ID 写在参数中，例如 `resident_id`、`lineage_id` 或资源地址。未声明的参数会被拒绝。可选参数显式写成 `null` 时视为没有提供，幂等指纹也按省略处理；类型为 `any` 的参数保留 `null` 作为业务值。 |
| `idempotency_key` | 可选，最长 240 字符。提供时，恢复窗口内同一调用者用同一个键重复提交同一条指令，不重复执行原操作；执行记录已到期回收的旧键被拒绝，调用者须先核对业务结果再决定是否以新键提交。不提供时，每次提交都会执行，重试同一调用会再执行一次。Resident 通过 Harness 工具或命令行调用时，入口自动补上幂等键（见“调用入口”）。只读指令忽略此字段。 |
| `expected_version` | 可选，一般不提供。调用者需要确认目标对象没有被别人改过时（例如容易出现并发覆盖的场景，或调试复现），把读取指令返回的 `version` 原样交回；版本已经变化时，通用对象返回 `instruction.version_conflict`；Lineage 定义修改返回 `instruction.conflict`，当前 revision 在 `error.details.current_version`。指令同时作用于多个对象时，写成以对象 ID 为键、版本令牌为值的对象。任何改变状态的指令都接受这个字段，没有必须提供它的指令；目标对象没有版本令牌时不做比较。 |

调用格式不包含调用者身份，身份由入口注入。调用格式也不包含顶层的目标 Lineage：目标对象写在参数中，入口和拥有服务根据目标 ID 找到负责它的领域模块。

Lineage 运行中产生的指令（例如 Effect 生成的指令）属于哪一次 Run，由 SocietyService 的进程内入口随调用传递给指令内核，并写入运行记录。调用者既不需要也不能在调用格式中提供运行归属。

## 返回格式

新返回结果使用 `tfi.hatg-instruction-result.v2`；读取只接受该精确格式（decision_463fd3e76150），回执字段或执行语义保持不变：

| 字段 | 含义 |
| --- | --- |
| `instruction_id` | 本次执行的稳定编号，可用 `GetInstructionExecution` 查询。编号中包含拥有服务的标记，查询时据此找到保存记录的服务。只读指令也返回编号，但不保存执行记录。 |
| `instruction` | 执行的指令名。 |
| `status` | `committed` 表示已执行完成，`failed` 表示失败。 |
| `replayed` | 为 `true` 时表示本次命中了已经完成的同一请求，操作没有重复执行。 |
| `result` | 成功时的结果对象，内容由各指令说明。重复请求返回原操作产生的对象引用；调用者通过对应的读取指令取得对象的最新展示内容。 |
| `error` | 失败时的错误对象。 |

错误对象包含 `code`（机器可读的错误码）、`message`（可显示的说明）、`retryable`（能否用同一调用重试）、`commit_state`（`committed`、`not_committed` 或 `unknown`，说明领域修改是否已经提交）和 `details`（结构化细节）。常用错误码如下：

| 错误码 | 含义 | 网页入口的 HTTP 状态 |
| --- | --- | --- |
| `instruction.unknown` | 指令名未登记 | 400 |
| `instruction.invalid_arguments` | 参数缺失、类型错误或包含未声明字段 | 400 |
| `instruction.forbidden` | 权限系统拒绝本次调用 | 403 |
| `instruction.not_found` | 目标对象不存在，或调用者无权看到它 | 404 |
| `instruction.version_conflict` | 调用者提供了 `expected_version`，而目标对象的版本已经变化 | 409 |
| `instruction.idempotency_conflict` | 同一幂等键对应了不同的调用内容 | 409 |
| `instruction.in_progress` | 使用同一幂等键的请求仍在执行 | 409 |
| `instruction.conflict` | 目标对象的当前状态不允许这次操作，例如向已暂停的事件源触发 Event、同名对象已经存在、本指令修改或引用的对象已被另一条指令删除 | 409 |
| `instruction.owner_unavailable` | 拥有目标对象的服务暂时不可用 | 503 |
| `instruction.projection_pending` | 修改已经保存，但页面读取的汇总数据或资源索引还没有同步完成 | 503 |
| `instruction.failed` | 其他执行失败 | 500 |

Python 入口将失败的指令结果转为业务异常时，保留外层回执的指令名和编号于 `details.instruction_result={instruction, instruction_id}`，以便业务任务失败记录定位原调用。该字段只标识实际收到的外层回执：入口生成的编号不是拥有服务的执行记录。原 `details.instruction_id`（例如 `instruction.in_progress` 指向的原执行）、错误码、retryable 和 commit_state 原样保留；该诊断信息不改变重试许可，不证明业务已经提交，也不触发自动重发。

流式指令的每个结果项包含 `cursor` 和该项内容；流结束时送出终态或错误。调用者用最后收到的 `cursor` 重新订阅，就能不重复、不遗漏地继续。

各分类 Design 可以增加领域错误码。拥有服务内部的领域错误按 HTTP 状态归入上表：版本冲突为 `instruction.version_conflict`，其他 409 为 `instruction.conflict`，401 与 403 为 `instruction.forbidden`，404 与 410 为 `instruction.not_found`，429 和可重试的 5xx 为 `instruction.owner_unavailable`，其他 4xx 为 `instruction.invalid_arguments`，其余为 `instruction.failed`；领域自己的错误码保留在 `details.reason` 中。调用者遇到未知错误码时保留其状态和说明，不从错误文字推断操作是否已经提交。

## 命名规则

- **指令名由“动词 + 对象”组成。** 对象名称取自分类 Design 中的主对象名称，同一对象在所有指令中使用同一个名称，例如 `GetResident`、`UpdateResident`、`DeleteResident`。
- **优先使用六个通用动词。** 读取一个对象、列出多个对象、按内容搜索、创建、修改和删除对象分别使用下表前六个动词。修改对象的一部分（例如表情回应、正文）也用修改动词，对象名写明修改的部分。
- **通用动词无法准确表达时使用表中的领域动词。** 新增指令只能使用下表中的动词；需要新动词时先在表中登记含义和例子。表中每个动词都至少有一条指令在用，指令删除或改名后不再使用的动词同时从表中移除。目录测试逐条比对两边。

| 动词 | 含义 | 例子 |
| --- | --- | --- |
| `Get` | 按 ID 读取一个对象 | `GetResident` |
| `List` | 按条件列出多个对象 | `ListChatrooms` |
| `Search` | 按内容搜索 | `SearchResources` |
| `Create` | 创建对象 | `CreateChatroomMessage` |
| `Update` | 修改对象或它的一部分 | `UpdateChatroomReaction` |
| `Delete` | 删除对象 | `DeleteLineage` |
| `Absorb` | 把一个 Resident 的上下文并入另一个 | `AbsorbContext` |
| `Activate` | 让一个已保存的配置开始生效 | `ActivatePermissionScheme` |
| `Answer` | 回答对象提出的请求 | `AnswerResidentRequest` |
| `Apply` | 把一组修改或一个模板应用到对象上 | `ApplyLineageChanges` |
| `Bind` | 建立两个对象之间的关联 | `BindWorkspace` |
| `Cancel` | 停止或撤回进行中的工作 | `CancelInput`、`CancelSubagent` |
| `Check` | 检查条件并返回结论，不修改对象 | `CheckPermission` |
| `Claim` | 领取待处理的工作 | `ClaimResidentSummaryJobs` |
| `Clear` | 清空对象内容，对象本身保留 | `ClearMailbox` |
| `Complete` | 由模型补全一次回答 | `CompleteChat` |
| `Deliver` | 把消息投递给 Resident | `DeliverMessage` |
| `Dispatch` | 按 Family 场景模板派发内部任务 | `DispatchFamilyTask` |
| `Familize` | 把一组 Resident 组织成 Family | `Familize` |
| `Feature` | 设为精选 | `FeaturePost` |
| `Fork` | 从一个 Resident 分出新的 Resident | `ForkResident` |
| `Forward` | 把已有消息转发给其他对象 | `ForwardMessages` |
| `Functionalize` | 把一组组件封装为 Function Block | `Functionalize` |
| `Inject` | 把一项内容插入队列 | `InjectMailboxItem` |
| `Issue` | 签发凭证 | `IssueTemporaryCredential` |
| `Leave` | 调用者退出对象 | `LeaveChatroom` |
| `Mark` | 设置读取等标记 | `MarkChatroomRead` |
| `Merge` | 把多项合并为一项 | `MergeMailboxItems` |
| `Move` | 改变对象所在的位置或顺序 | `MoveResident` |
| `Open` | 打开或关闭开关，或打开会话 | `OpenIntake`、`OpenTerminal` |
| `Pause` | 暂停或恢复运行 | `PauseComponent` |
| `Pin` | 置顶 | `PinPost` |
| `Prepare` | 生成待确认的草稿，不提交 | `PrepareWorkspaceHandoff` |
| `Preview` | 读取将要产生的结果，不提交 | `PreviewPromptInjection` |
| `Promote` | 把种子提升为正式对象 | `PromoteSeed` |
| `Probe` | 发起实际请求，检测外部接口支持的能力；可能产生外部调用费用，不保存检测配置 | `ProbeModelApi` |
| `Publish` | 发布到更大的可见范围，或撤回发布 | `PublishRelease`、`PublishPulseItem` |
| `Read` | 读取对象的内容 | `ReadResource` |
| `Register` | 把已存在的外部对象登记为资源 | `RegisterResource` |
| `Relay` | 由中间对象转交消息 | `RelayFamilyMessage` |
| `Release` | 交还已领取的工作 | `ReleaseResidentSummaryJob` |
| `Rename` | 改变对象的 ID 名称 | `RenameProject` |
| `Renew` | 延长有效期 | `RenewTemporaryCredential` |
| `Reorder` | 调整多项的顺序 | `ReorderMailbox` |
| `Request` | 提出请求，由服务稍后处理 | `RequestResidentSummaryRebuild` |
| `Requeue` | 把结束的消息放回队列 | `RequeueMessage` |
| `Reset` | 恢复到初始状态 | `ResetUserPassword` |
| `Resolve` | 把地址或待决项解析为确定结果 | `ResolveResource` |
| `Restart` | 停下后重新启动 | `RestartPortal`、`RestartSession` |
| `Restore` | 从删除或旧版本中恢复 | `RestoreTemplate` |
| `Retract` | 撤回自己发出的内容 | `RetractChatroomMessage` |
| `Retry` | 重新进行没有完成的工作，已完成的部分不重做 | `RetryResidentInitialization` |
| `Revoke` | 撤销凭证或邀请 | `RevokeUserAccessToken` |
| `Run` | 执行一次脚本或动作 | `RunScript` |
| `Set` | 设置运行服务的目标状态，由必填参数给出状态 | `SetTelepusEnabled(enabled)` |
| `Split` | 把一项拆成多项 | `SplitMailboxItem` |
| `Submit` | 提交已完成的工作结果 | `SubmitResidentSummary` |
| `Trigger` | 触发事件 | `TriggerEvent` |
| `Unbind` | 解除 `Bind` 建立的关联 | `UnbindWorkspace` |
| `Unfamilize` | 解散 Family | `Unfamilize` |
| `Unfunctionalize` | 展开 Function Block | `Unfunctionalize` |
| `Unregister` | 取消登记，外部对象本身保留 | `UnregisterResource` |
| `Watch` | 流式等待变化 | `WatchMailbox` |

- **预览与诊断使用明确名称。** `PreviewPromptInjection` 读取提示预览，`GetLineageDiagnostics` 返回图诊断；修改仍在提交时校验。
- **不为同一操作设置多个名称。** 调用者不同、入口不同或选项不同时，仍使用同一条指令；例如五种 Fork 方式都使用 `ForkResident`，由 `fork_mode` 参数区分。
- **开关是一条指令加一个必填布尔参数。** 打开和关闭、设置和取消属于同一操作，用正向动词命名，由参数给出目标状态，例如 `PinPost(pinned)`、`FeaturePost(featured)`、`PinMailboxItem(pinned)`、`PauseComponent(paused)`、`OpenIntake(open)`、`PublishPulseItem(published)`（TFI portal 2026-10-18）。
- **同一种读取覆盖多个对象时用一条指令，由参数选择对象。** 例如 `ListInputHistory`、`SearchMessages` 用 `resident_id(s)` 或 `chatroom_id` 二选一，`GetDeviceStatus` 用 `kind` 选择 GPU 或磁盘；两种对象由不同服务保存时，声明按参数分流（`argument_owners`）。

## 指令在哪里执行

所有指令遵守同一套规则，执行则发生在拥有目标对象的服务中。每条指令在声明中写明自己的拥有服务，入口按声明把调用通过该服务的 RPC 送过去；拥有服务在自己的进程中运行指令内核并执行领域操作。这样，读取资源的请求直接到达 ResourceService，不经过其他服务中转；一个服务暂时不可用时，也只影响由它执行的那些指令。

指令的执行服务由指令声明中的 `owner` 确定（核心类别的声明在 `management_portal/hatg/catalog/`，可选功能的声明在功能自己的 `<功能>_instructions.py`），`ListInstructions` 与 `GetInstructionSchema` 返回同一目录。ResourceService、Portal Web、SocietyService、ExecutionService 分别执行声明归属自己的指令；不按类别推测例外指令的服务。

一条指令操作的状态分在两个服务时，声明用 `argument_owners` 列出 (参数, 服务)：调用带了该参数（非空）就在那个服务执行，否则在 `owner` 执行。入口路由、服务端的本地执行检查和内核启动时的实现核对都按这条规则；每个相关服务都要登记实现，`GetInstructionSchema` 返回 `argument_owners`。目前 `GetMailbox` 和 `ClearMailbox` 使用：带 `resident_id` 在 SocietyService 读取或取消该 Resident 的等待消息，带 `user_id` 在 Portal Web 读取或清理 User Mailbox（decision_c54e68a82d7a，TFI portal 10-18）。`SearchMessages` 和 `ListInputHistory` 带 `chatroom_id` 时在 Portal Web 执行，这部分实现由聊天室功能登记；没有安装聊天室时，Web 对带 `chatroom_id` 的调用返回参数错误（decision_15aab18c0ab3）。

指令集自描述类中，读取指令目录在入口本地完成；`GetInstructionExecution` 可以按 `instruction_id` 中的服务标记路由，也可以按原提交的 `instruction + idempotency_key` 路由至原指令 owner。后者只读匹配当前调用者的原请求，取回真实执行 ID 后仍按原记录真实归属授权；不执行原指令，不允许代查其他调用者的幂等键。按参数分流的指令不能用幂等键查询，因为仅凭指令名找不到记录所在的服务，需要用 `instruction_id`。

每个拥有服务都提供同一个内部 Unix socket 指令接口 `instruction.execute`，接收调用、入口种类和入口已经确认的调用者身份，传输与错误语义遵循 decision_96a887d83e8a。Portal Web 也提供这个接口：Harness 工具入口运行在 ExecutionWorker 中，通过它调用用户、公共讨论、User Mailbox、Portal 服务与设备状态等由 Portal Web 执行的指令，不经过 HTTP 回环。

入口所在的进程本身就是拥有服务时（例如 SocietyService 执行 Effect 生成的 Lineage 指令），直接在进程内调用。拥有服务执行一条指令时如果需要其他服务配合，例如 Fork 需要 ExecutionService 复制模型轨迹，由拥有服务通过领域客户端直接调用对方的领域接口；这种协作属于这条指令的实现，在服务之间直接进行，不经过指令入口。

## 一次指令怎样执行

1. 调用入口确认调用者身份，整理出调用格式，并按指令声明把调用交给拥有服务。
2. 拥有服务中的指令内核按指令名取得该指令的声明，校验参数的必填项、类型和未知字段。
3. 指令内核通过权限端口检查权限（decision_b820faf42218）。指令声明的判定槽写明这次调用按哪些参数指向的对象判定，每个判定槽是一个 A 元素；启用方案把每个 A 元素放在一个等级（A 树分类节点）下。一次调用由此拆成一组 (调用者, A 元素, 资源) 请求，由 PAR 引擎按调用者自身的表项一次判定，全部允许才执行；方案没有分类的 A 元素落在“未归类”下，只有管理员能通过，不需要权限的指令在方案中显式放在“免检”下。拥有领域仍校验对象状态、参数、输入归属和业务不变量。修改被拒时返回 `instruction.forbidden`，读取被拒时返回 `instruction.not_found`，领域模块不会被调用；未在指令目录注册的名称仍返回 `instruction.unknown`。账号管理与权限指令同样由方案的指令映射约束；聊天室的发言和管理按聊天室上的表项判断，成员和召唤记录还用于个人列表与提醒（decision_a6bebf4ac283）。目录与定义查询向已经认证的调用者开放；执行记录查询按记录实际归属检查：Lineage 范围的记录要求对所属 Lineage 有 `GetLineage` 的权限，调用者作用域的记录只向原调用者开放。内部维护的 `system` 入口使用服务端信任身份，直接进入领域操作。权限系统关闭时没有权限判定，端口只检查调用者已登录、目标不是别人的个人 AMA。
4. 只读指令直接调用领域模块读取当前事实并返回；流式指令读取当前事实后保持订阅，按顺序送出后续变化。
5. 调用者提供了幂等键时，指令内核先以“调用者、指令名、幂等键”登记本次请求：恢复窗口内已经完成的相同请求返回已登记结果；同一键提交了不同内容时返回 `instruction.idempotency_conflict`。旧执行记录已经到期回收、仅剩防重摘要时，返回 `instruction.conflict`，`details.reason=idempotency_window_expired`、`commit_state=unknown`，不重新执行。没有提供幂等键时不做重复请求匹配，每次提交都作为新请求登记并执行。
6. 调用者提供了 `expected_version` 且目标对象有版本令牌时，按拥有领域的版本规则检查。Lineage 定义修改使用 `GetLineage.result.lineage.revision`，仅在本次调用对各 Lineage 的首次定义提交短事务内比较，后续提交不重复比较；回滚不消耗前提。ISA 通过调用上下文传递前提，不用事务或 Lineage 锁包住外部工作或整个多步处理器。不一致以 `instruction.conflict` 结束并返回 `error.details.current_version`；不经过内核预读再写。省略该前提时提交时合并，结构校验失败、引用已删除对象或同一 Function Block 定义并发修改时整体冲突。跨 Lineage 可使用以 Lineage ID 为键的版本映射（decision_4637ca4a69e2）。
7. 领域模块在自己的事务中提交修改；指令内核在独立的短事务中登记请求和保存执行结果，这样通知与其他服务读到的都是已经提交的状态。领域事务失败时其修改回滚，内核保存失败信息；领域已提交后发生执行记录写入失败，不能据此断言业务回滚或自动重新执行。仍在执行的调用持有独立的数据库会话锁，该锁只标识指令活动，不锁住业务行或延长业务事务。执行先取得活动锁，再登记可见的执行记录；执行结束或进程退出时释放锁。拥有服务的维护流程、同键重试以及通过原归属权限检查的执行记录查询，均以该锁确认调用是否仍然活动。记录仍为 `executing` 且已没有活动调用持锁时，立即结算为已中断，返回 `instruction.failed`（`commit_state=unknown`、`details.reason=interrupted`），不等待固定时长、不重新执行；仍持锁的长调用继续保持 `executing`。目标对象的当前值不能单独证明原调用的提交结果，不能据此补造成功回执。调用者先查询目标对象、已有运行事实或外部幂等结果，确认后才决定是否以新键提交；未知结果本身不能被记成业务成功或失败。
8. 修改已经保存，但页面读取的汇总数据或资源索引还没有同步完成时，返回 `instruction.projection_pending`。调用者用同一个幂等键重试，或由拥有服务的后台维护继续恢复。恢复只读取回执中的对象引用和当前领域事实，补做投影，不再次调用业务处理器；没有幂等键的记录也能由维护流程处理。Web、ResourceService、ExecutionService 遇到 Lineage／Resident 投影回执时，通过内部 RPC 交回 SocietyService 的同一恢复入口。重试与后台恢复使用指令活动锁互斥，投影期间不持有数据库事务。
9. Effect、Function Block 运行等在执行中产生新指令时，新指令重新经过上述完整流程，不继承外层已经通过的检查。

需要调用外部服务或执行长时间工作的指令（例如 Fork、工作区交接、调用模型接口）先在事务中保存执行意图，再在锁外完成外部工作，最后按执行记录归并结果。进程重启后，拥有服务依据执行记录和目标对象的当前状态继续处理；无法确认某个外部动作是否已经执行时，不盲目重做。

指令内核为非流式指令记录慢调用的位置（`management_portal/hatg/slow_call_profile.py`）。服务进程不允许外部调试器附加，所以处理函数运行超过 0.5 秒后，一个采样线程每 50 毫秒读一次它所在线程的调用栈；调用超过 2 秒结束时，服务在标准错误输出写一行 `HATG slow instruction profile:`，内容是拥有服务、指令名、用时、样本数、每个 Portal 代码帧出现在多少个样本中，以及最常见的最内层帧（例如等在哪条数据库查询或哪把锁上）。这一行只用于排查，不进入执行记录。

慢调用记录只覆盖指令处理函数。要看整个服务进程的 CPU 花在哪里（包括后台恢复、事件投递和定时唤醒），向 SocietyService 或 ResourceService 的 Python 进程发送 SIGUSR2：`kill -USR2 <pid>`。systemd 记录的 MainPID 是外层的 `sg`，需要取它的子进程。服务的主循环收到信号后启动一个采样线程（`management_common/cpu_profile.py`），在之后 30 秒里每 20 毫秒从 `/proc/self/task/<tid>/stat` 读一次每个线程的 CPU 时间；只有 CPU 时间增加的线程才记录调用栈，并按它用掉的时钟滴答加权，等在锁、网络或数据库上的线程不计入。结束时在标准错误输出写一行 `Service CPU profile:`，内容是按线程名合计的滴答数（名字里的数字记为 N）、占用 CPU 最多的 Portal 代码帧、最内层帧和调用栈。采样期间再收到信号不另起一次。刚启动的二三十秒主要是启动开销，采样应避开发布重启。

## 重试、幂等与并发

- **传输失败、超时或拥有服务暂时不可用时，带幂等键的调用保留原键重试。** 浏览器对进行中、投影待完成和 `instruction.owner_unavailable` 使用有限退避重试，保持参数和幂等键不变；取消操作立即停止等待，超出次数后保留原错误。 恢复窗口内得到的是已登记结果、进行中状态或等待恢复的结果；窗口记录已回收的旧键被明确拒绝，不能因响应丢失就换键盲目重做。不带幂等键的调用重试会再执行一次。
- **版本冲突后重新读取再决定。** 只有调用者提供了 `expected_version` 才会出现版本冲突。调用者读取对象的当前内容，按新内容重新决定修改；使用幂等键时为新的修改换一个新键，旧键搭配新参数会被判为冲突。
- **权限、参数和业务规则拒绝不自动重试。** 调用者修正原因后作为新请求提交。
- **执行记录按时间保留，分区只表示归属。** 各拥有服务保存自己的写操作记录；以 Lineage 为目标的记录归入该 Lineage，其他记录归入该服务中的调用者作用域。只读调用继续不进入全局执行记录表。已结束且符合清理条件的写操作记录从最近一次 `updated_at` 起至少保留 7 天，七天后由有界维护流程回收；数量用于容量监控，不以每组条数提前淘汰。
- **各拥有服务定期恢复并清理，空闲服务也会维护。** 服务启动后运行独立维护线程，此后每分钟分批结算中断记录、恢复投影和回收已到期记录。每批有固定上限，待投影记录按游标轮转，暂时不可用的目标不会阻塞其余记录。投影成功后清除待投影错误；目标不存在、恢复被拒绝或回执无效时，以投影恢复失败结算，保留 `commit_state=committed`、对象引用和失败原因，不将已提交的领域修改写成回滚。临时故障保留原待投影回执，后续维护继续尝试。
- **终态中的未知结果保留到窗口结束。** `failed` 且 `commit_state=unknown` 表示调用已经结束、业务结果仍不能确定；它从最近一次 `updated_at` 起保留至少 7 天，随后按正常流程清理并留下防重摘要。中断结算或投影恢复结算会更新 `updated_at`，因此结算后仍有完整的恢复窗口。清理不改写业务结果。
- **尚未结算或仍参与 Effect 恢复的记录继续受保护。** 清理跳过 `executing`、`instruction.projection_pending` 及 `recovery_key` 尚未清除的记录。活动调用结束、中断结算或投影恢复结算后，前两类进入正常保留期；Effect 依赖仍须由运行入口明确释放。
- **到期回收保留不可逆的防重栅栏。** 对带幂等键的过期请求，拥有服务在同一清理事务中删除执行记录并写入 `instruction_retry_guards`。栅栏只有 `request_digest`（“调用者、指令名、幂等键”的 SHA-256 摘要）和 `expired_at`，没有身份、指令名称、明文请求键、参数正文、响应或可读执行结果。再次提交同一请求身份，无论参数是否相同，都返回 `instruction.conflict`（HTTP 409，`details.reason=idempotency_window_expired`，`details.retention_days=7`，`commit_state=unknown`），不自动重放，也不能从摘要恢复原记录。操作者先核对业务结果，再决定是否以新键提交；旧键不会因此变成新的执行机会。
- **恢复依赖由其拥有者明确释放。** Effect 的长期完成事实持久保存后，Lineage 运行入口才向相关指令拥有服务发送内部受认证的 release ack。确认校验原调用者、对应 `recovery_key` 和有限的指令 ID，只清除匹配的恢复依赖；它不属于可由用户或 Resident 直接调用的 ISA。确认失败时依赖仍受保护，持久清理任务继续重试，不能先删 ISA 记录再补完成事实。
- **恢复窗口与长期执行历史各负其责。** ISA 记录及到期摘要用于重试和防重，均不是永久审计。长期 Run 事实独立保留必要调用摘要、业务对象与执行/产物 ID，Agent trace 保留模型执行内容，不依赖过期 ISA 响应解释结果。业务对象继续保存当前状态；此保留规则不要求引入历史图版本，也不为每次改名或设置修改生成永久业务变更历史。
- **执行记录只保存恢复所需的内容。** 记录包括请求键、调用者、入口种类、内容指纹、执行状态、业务对象引用和失败信息，不保存完整响应、Lineage 快照或消息正文。

## 指令声明与目录

每条指令由其类别对应的声明模块登记一次。声明包含：指令名、所属类别、拥有服务、一句话说明、参数定义、结果定义、是否改变状态、是否流式、哪个参数指向目标对象，以及判定槽（这条指令按什么判定，见“声明格式”）。指令内核在每个服务启动时合并同一份声明；两个模块声明同名指令时启动失败。

可选功能的指令随功能安装：公告板、聊天室、Pulse 和 Telepus 通过 `isa.instructions` 接入点提供声明模块（`<功能>_instructions.py`），目录合并已安装功能的声明和它们新增的类别（`hatg.catalog.categories()`），`ListInstructions` 的 `category` 取值随之变化；声明的类别不在这些类别中时目录构建失败。Web 执行的功能指令，其实现由功能的 `web.instructions` 模块（`<功能>_web_isa.py`）登记到 Web 内核。功能没有安装时，它的指令不在目录中，各服务的内核也就不需要它们的实现（decision_15aab18c0ab3）。

拥有服务启动时，指令内核核对自己登记的实现：本服务拥有的指令缺少实现，或为其他服务拥有的指令登记了实现，默认拒绝启动。目录中已不存在的旧实现只记录警告并忽略。Release Web 与共享目录短暂不一致时，采用 decision_08bb2d9141f9“共享与独立的范围”规定的发布兼容：目录先新增而 Web 实现尚未发布时，Release Web 保持启动，对该调用返回 `instruction.owner_unavailable`；Dev 与共享组件的缺失实现检查保持严格。

资源类型的专用动作也在同一目录中声明，每个动作有自己的参数、结果和“是否改变状态”，通过资源指令 `RunResourceAction` 执行（decision_ad3e21094f14）。领域增加操作时只增加动作声明，顶层指令保持不变。

一条指令按什么判定写在它的声明中，随代码发布；每个判定对象要求什么等级写在启用的权限方案的指令分类中。方案保存在权限系统中，修改分类或启用另一套方案不需要发布代码或重启（decision_b820faf42218）；`GetInstructionSchema` 的 `permission` 显示启用方案对这条指令的要求。

## 新增与修改指令

指令集随系统开发不断增加和修改。本章规定一条指令进入目录前必须满足的条件、推荐的修改流程和声明格式（TFI portal 2026-10-18）。能由代码检查的条件都写成目录测试，测试不通过的修改不能发布。

### 进入目录的条件

1. **单一操作。** 一条指令作用于一个主对象，执行一项确定的操作。多步流程用 Lineage、Effect 或 RunScript 把已有指令组合起来，不登记成新指令（见“执行原则”）。
2. **不重复。** 只有现有指令加参数做不到时才新增。同一操作只有一个名字（见“命名规则”）。
3. **可判定。** 每一种合法的参数组合都能确定这次调用作用在哪些对象上。权限系统据此把一次调用拆成 (调用者, A 元素, 对象) 请求；找不到对象，就无法判定。新增指令时，在声明中写判定槽：按哪个参数指向的对象判定；不作用于具体对象的指令，例如 `GetPortalStatus`，写固定槽。同一次提交在默认方案的指令分类中给每个新 A 元素选等级（decision_600ae27b0538）。漏写分类的 A 元素落在“未归类”下，只有管理员能通过，其他调用者都被拒绝。
4. **有契约。** 所属类别的 Design 在指令表和参数名契约表中写明这条指令：主对象、参数、结果、是否改变状态、领域错误，以及判定对象（判定槽，随声明）和默认方案要求（等级与固定槽的 R 节点，以 decision_600ae27b0538 的分类为准）。默认方案要求写在指令表的“默认方案要求”列，目录测试把这一列与 default.json 逐条对比，不一致就失败。

### 推荐流程

1. **先查目录。** 用 `ListInstructions`、`GetInstructionSchema` 确认现有指令组合不出所需操作。
2. **先写契约。** 在类别 Design 中写好上一节第 4 条的内容。新主对象不属于任何现有类别时，按“指令怎样分类”新建类别和它的 Design。
3. **自查。** 新增或修改指令的人按本章逐条检查契约，再开始实现。没有专门的审查人；发布的硬门槛只有目录测试，测试判断不了的条件（是否重复、名字是否合适、是否应该用组合流程）靠这一步自查（TFI portal 2026-10-19）。
4. **同一次提交。** 指令声明（含判定槽）、实现、默认方案的分类、类别 Design 和测试一起提交，目录测试通过后发布。
5. **修改和删除走同一流程。** 改名、改参数或删除指令时，同一次提交改完所有调用方：页面、脚本、Skill、Lineage 中的 Effect 与连线配置。不保留旧名或旧参数作为别名。

### 声明格式

在“命名规则”和“调用格式”之外，指令声明还要满足：

- **目标对象用统一 ID 指定，且必填。** 指令作用的对象写在参数中，用统一 ID 或资源地址。不允许省略参数时默认为调用者自己；读取自己的资料也写明自己的 ID。唯一的例外是 RunScript 的 `lineage_id`：Effect 内由激活确定，Resident 省略时取其所在 Lineage（decision_8b510cbd9c58）；入口按这样确定的 Lineage 判定（TFI portal 2026-10-19 起要求对它有 `@admin`），User 省略时拒绝，不落到其他节点或免检。声明的 `target` 指向这个参数；修改已有对象的指令都声明 `target`，调用信封的单个 `expected_version` 按它比较。没有单一目标对象的修改不声明：认领摘要队列、新开终端、按周期与条目键指定的 Pulse 条目、重启 Portal 实例，以及全站执行设置和知识标签列表。
- **一个对象只有一种写法。** 同一个对象不能既用 ID、又用路径、账号编号或执行编号指定。指令可以作用于不同类型的对象时，每种类型一个参数，声明为“恰好给一个”，每个参数都有判定槽。例如 `GetMailbox` 用 `resident_id` 或 `user_id`。按多个条件筛选的读取指令可以声明“至少给一个”，给出的每个参数都按它的判定槽判定，例如 `ListPermissionEntries` 的 `p` 和 `r`。
- **同一类对象在所有指令中使用同一个参数名。** 例如 Resident 一律用 `resident_id`，Project 一律用 `project_id`。
- **分页读取一组条目用 `cursor`、`limit` 和 `next_cursor`（TFI portal 2026-10-19）。** `limit` 是每页最多的条数；`cursor` 是上一页结果中的 `next_cursor`，原样交回，不填时读第一页；还有下一页时结果带 `next_cursor`，它不出现或为空就表示已经读完。cursor 是不透明的字符串，调用者不解析、不自己拼写。不用 `offset`、`page_token`、`before` 这类其他名字；结果可以另外给出总数 `total`。例如 `GetConversation` 返回的 `next_cursor` 交给 `ListMessages` 继续往前读，`ListPermissionEntries` 一页页列出表项。三种读取不属于这条：按位置读取一个对象内部序列的指令用 `offset`，调用者可以从任意位置读起——`ReadScriptRunOutput`（日志的字节位置）、`GetSubagentRun`（子 agent 轨迹，界面直接从末尾附近读）、`GetTriggerPath`（一个流程的步骤、边与执行四个列表共用一个位置）；只取前几条、不翻页的读取只有 `limit`；读取某个位置之后的新变化用 Watch 指令的 `after_cursor`（见“流式指令”），`ListChatroomMessages` 的 `after` 与 `change_cursor` 读取某个位置之后的新消息和变更，也不是翻页。
- **创建指令必须给出新对象的位置。** 位置指所属的 Project、Lineage 或父对象，由参数给出，不由调用者身份推断。全局对象（例如 Project、User、聊天室、帖子、模型接口）没有所属对象，按固定槽判定，方案给固定槽指定 R 树分类节点。只有系统内部创建的对象例外，例如账号的个人 AMA。
- **判定对象可以来自已保存的记录。** 目标是一条记录、记录又属于另一个对象时，按记录所属的对象判定。这一步在入口完成：判定槽声明“这个参数是记录 ID，判定时用记录所属的对象”，入口读出记录所属的对象，再和其他请求一起交给引擎；处理代码不再问引擎（TFI portal 2026-10-18）。例如 `UpdatePermissionEntry`、`DeletePermissionEntry` 按表项已保存的 r 判定，修改 r 时新 r 也判定；`GetInstructionExecution` 读取 Lineage 范围的执行回执时按回执所属的 Lineage 判定，调用者作用域的回执只向原调用者开放。写法：判定槽写 `via="<记录>.<字段>"`，不写类型，记录字段必须在权限词汇的 `records` 中登记；字段可能为空的记录（`records` 中标 `may_be_unowned`，例如 `instruction_execution.lineage`）为空时不生成请求（`optional` 只管参数没给的情况，与此无关），调用没有其他请求就按未归类判定、只有管理员能通过，所以这类指令另有固定槽。两条都由声明规则测试逐条核对。
- **判定槽写在声明中。** 声明的 `decides` 列出这条指令按什么判定，每项是一个判定槽：`On(参数, 类型…, optional=, via=, place=)` 按参数指向的对象判定，参数可以是点分路径（如 `fields.project_id`）；`types` 限定对象类型，`via` 按已保存记录的字段判定（见上一条），`place` 按新对象将要放在哪里判定（创建与登记指令，参数是新对象的类型，`place` 写关系对象类型到参数路径）；`Fixed(名称)` 不指向对象，按方案给它的 R 树分类节点判定。“恰好给一个”与“至少给一个”仍写在参数声明中。每个判定槽是一个 A 元素：只有一个槽时元素名就是指令名，有多个槽时是 `指令:参数` 或 `指令:固定槽名`（例如 `DeliverMessage:resident_id`、`CreateTemplate:@public`）。方案只给 A 元素分类，不能改写判定槽（decision_b820faf42218 §3.2）。增删判定槽使槽数在一个和多个之间变化时，A 元素改名：同一提交更新默认方案的分类，并在提交说明中写明旧元素名；自定义方案中旧元素的分类和以它为 A 的表项、临时凭证不再生效。
- **列表与搜索的范围由参数或固定节点决定。** 给出所属对象时按它判定；列出全局对象的指令按固定槽判定。结果可以只包含调用者有权看到的对象，例如 `SearchResources`、`ListUsers`。不允许的是“省略参数时只列调用者自己的”这类由调用者身份决定范围的写法；要列自己的，就把自己的 ID 写在参数里。
- **结果中能找到对象 ID。** 创建指令的结果中能找到新对象的 ID；修改指令的结果中能找到被修改对象的 ID，对象有版本令牌时同时返回新版本。ID 可以直接是结果字段，也可以在返回的对象里。
- **说明完整。** 指令有一句话说明，每个参数和结果字段都有说明；取值有限的参数写 `enum`，数值参数写明范围。

### 目录测试

目录测试把上述条件变成发布前的检查：

- 代码目录与各类别 Design 的指令表逐条一致，类别、是否改变状态、是否流式与表中一致（`test_hatg_isa_catalog_spec.py`）；
- 所有类别的 Design 都有参数名契约表，并与声明一致；
- 声明字段齐全，参数不重名，`target` 指向已声明的参数，流式指令只读且以 `Watch` 开头；
- 修改已有对象的指令声明 `target`，例外按名字列出；流式指令都用 `after_cursor` 续读，结果项带 `cursor`；分页读取用 `cursor`、`limit` 与 `next_cursor`，按位置读取的三条指令按名字列出；每个参数和结果字段都有说明（`test_hatg_isa_declaration_rules.py`）；
- 每个拥有服务都能为自己的每条指令找到实现；
- 默认方案给每个 A 元素分类（`test_permission_engine.py`）；
- 每条指令都声明判定槽，A 元素名不重复；判定槽引用的每个参数都在指令声明中存在，类型与落点关系已登记；每一种合法的参数组合至少生成一个判定请求；`via` 只写已登记的记录字段，可能不属于任何对象的记录旁边有固定槽；指令名的动词属于“命名规则”列出的动词（`test_hatg_isa_declaration_rules.py`）。

规范生效时仍不符合的指令，按名字列在测试中，并写明在等哪项决定。一条指令改好后，测试要求把它从名单中删掉，名单只减不增；新指令不能加入名单。

引擎对一次没有生成任何判定请求的调用，按未归类处理，只有管理员能通过。这样声明或分类有漏洞时，结果是拒绝，而不是对所有人放行。


## 与权限系统的关系

ISA 定义可以执行哪些操作，权限系统决定谁能执行。普通业务调用在执行步骤 3 交给权限系统检查；自描述查询与受信任的内部维护入口按“一次指令怎样执行”中的规则处理。判定只看执行者自身的权限，Resident 不继承发消息者或发起者的权限，需要额外权限时由持有者签发临时凭证（decision_b820faf42218）。默认方案的指令分类见 decision_600ae27b0538，身份规则见 decision_3fe3cca8fd95 与 decision_fb910c43ab07。权限方案、表项、管理员名单和临时凭证的管理属于“权限”类指令，见 decision_22227d0ad075。

## 相邻 Design

- Portal 的服务划分与服务间 RPC 见 decision_463fd3e76150 与 decision_96a887d83e8a。
- SocietyService 的模块结构见 decision_1b2bdf5c33d6；SocietyService Domain RPC（decision_d918ebebc062）是 Portal 内部组件使用的受信任接口，Resident、Effect 和 Function Block 使用 ISA。
- ResourceService 的门面与 RPC 见 decision_b6e74d78f7b6。
- Lineage 相关执行记录保存在 LineageData 中，存储结构见 decision_71aa039e3211；各服务的数据所有权见 decision_86aed2f0e8c7。
- 调用者身份、消息署名与异步执行归属见 decision_ae422b92c6f3。
