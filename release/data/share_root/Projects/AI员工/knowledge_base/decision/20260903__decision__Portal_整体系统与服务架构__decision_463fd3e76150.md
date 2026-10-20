---
kb_id: decision_463fd3e76150
type: decision
project: AI员工
created_at: '2026-09-03'
importance: 10
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/asgi.py
  - ${code_root}/management_portal/web_instruction_server.py
  - ${code_root}/management_portal/web_isa.py
  - ${code_root}/management_portal/hatg/kernel.py
  - ${code_root}/management_portal/hatg/routing.py
  - ${code_root}/management_portal/hatg/socket_rpc.py
  - ${code_root}/management_portal/resources/isa.py
  - ${code_root}/management_portal/society_service/isa/__init__.py
  - ${code_root}/management_portal/execution/isa.py
  - ${code_root}/management_portal/execution/scheduler.py
  - ${code_root}/management_portal/notifications/bus.py
  - ${code_root}/management_portal/portal_deployment_router.py
  related:
  - decision_b820faf42218
  - decision_3fe3cca8fd95
  - decision_08bb2d9141f9
  - decision_99bb5dc0f96b
  - decision_ad3e21094f14
  - decision_4bc03b20c30f
  - decision_5b5653c3eaa5
  - decision_4adcc1ff2f2c
  - decision_6fc653d55c79
  - decision_0f57656a2acd
  - decision_4a9ba5dfada6
  - decision_b1ab45c4b3ec
  - decision_b6e74d78f7b6
  - decision_25eb93d803a4
  - decision_abaedeefca6f
  - decision_86aed2f0e8c7
  - decision_ecf0f2b2de28
  - decision_850e6248d680
  - decision_ef80c6d3b5ee
  - decision_cf27d39e39cd
  - decision_3ece26686464
  - decision_1b2bdf5c33d6
  - decision_898b8911a2ff
  - decision_5c62deb8253a
  - decision_425e428410e0
  - decision_741b04821171
  - decision_4348ed990621
  - decision_96a887d83e8a
  - decision_06defc47e960
  - decision_f2a6a5dd4646
  supersedes: []
  derived_from:
  - thinking_218380bb8148
  - thinking_fa75e877a6e4
  - thinking_6a81f20aeb57
  - thinking_ca8f397a47de
  - thinking_ab37a18be710
version:
  number: 40
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T19:59:40Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_865c3c24f251
metaness: 3
---
# Portal 系统架构

Portal 是一个让多人和多个 AI Agent 在同一环境中工作的系统：大家共享知识和材料，由 Agent 组成的团队承担大型任务，人随时查看进展并介入。本 Design 说明 Portal 由哪些部分组成、它们怎样协作，以及整个系统共同遵守的几条架构规则。各部分的细节由本文引用的 Design 说明。

Portal 的设计遵循四个理念：

- **分层解耦。** 工作界面、Agent 的协作方式和底层服务可以各自演进。调整页面或接入新的模型后端时，参与者继续通过同一组资源和操作入口工作。
- **可以按需扩展。** 用户可以围绕自己的目标组建团队、配置自动化流程，并让 Agent 为任务生成合适的工作界面。可复用模块、Harness 插件、模型后端和主题提供不同层面的扩展。
- **多人、多 Agent 共同演化。** 参与者在各自的权限范围内共享材料、接续工作、提出改进。
- **边使用边开发。** Portal 自身的开发也由 Agent 团队承担，用户在使用中持续参与软件的改进。

## 核心概念

- **资源**：Portal 中可以被查找、引用和使用的持久内容，例如知识条目、论文、代码仓库、实验材料、测试和设备。每项资源有一个稳定的统一 ID（decision_4a9ba5dfada6），人和 Agent 都用这个 ID 取得它。**知识库**是其中保存长期理解的部分，包括设计规范（Design）、思考、研究发现、问题和计划。
- **Resident**：一个长期存在的 Agent 身份。它保留自己的对话记录、工作区和运行设置，可以跨多次模型会话持续工作。需要探索不同方向或并行工作时，可以从已有 Resident 的上下文 Fork 出新的 Resident（decision_0f57656a2acd）。同一 Lineage 中的多个 Resident 还可以组合为 Family：外部只面对一个门面 Resident，请求由 Family Router 转给最相关的成员（decision_44cf587c1721）。
- **Lineage**：一组 Resident 及其协作关系组成的团队。Lineage 中的事件源、动作组件和连线规定什么情况下由谁开始工作、结果交给谁，从而把自动执行、人工判断和根据结果调整后续工作组织成一个流程（decision_6fc653d55c79、decision_4adcc1ff2f2c）。
- **HATG ISA**：Portal 中所有操作共用的一组具名指令，例如创建 Resident、投递消息、修改 Lineage、读取资源。人在页面上的操作、Agent 在工作中的调用和 Lineage 内部组件触发的动作，都通过这组指令完成（decision_898b8911a2ff）。HATG 是 Human–Agent Trajectory Graph（人机协作轨迹图）的缩写。

相互联系的 Resident、Lineage 与共享资源构成一个 AI 社会。用户可以与单个 Resident 深入讨论，也可以在团队或项目层面安排目标、查看进展、介入关键环节；团队之间通过共享的材料和成果接续工作。

## 扩展方式

用户可以创建自己的 Lineage，安排成员、触发条件和成果交接，也可以放入可复用模块（Function Block，decision_5b5653c3eaa5）再按任务调整。一个 Lineage 可以通过工作台（Workbench）呈现为适合任务的页面：把团队正在处理的资源、进展和可执行操作组织成研究工作台、看板、对话或 Lineage 关系视图。Agent 可以按用户的目的生成和调整这些页面（decision_4bc03b20c30f），用户也可以继续修改。一个 Lineage 可以有多种页面，一个页面也可以组合多个 Lineage 的工作。

Portal 自带的几个领域页面采用同样的方式，每个页面背后都有负责它的 Lineage：

| 页面 | 用户可以完成什么 |
| --- | --- |
| **Paper** | 阅读论文，组织评阅、研究笔记和实验，把论文材料与负责研究的团队联系起来 |
| **Test** | 查找测试、安排验证、查看运行证据和审查结果，判断一次修改是否可用（decision_f2a6a5dd4646） |
| **WALL** | 浏览软件设计、改进建议与开发进展，把问题交给相关成员，并体验开发结果（decision_06defc47e960） |
| **Workbenches** | 打开和管理用户自己的工作台页面 |

其他扩展方式：

| 扩展方式 | 作用 |
| --- | --- |
| **资源类型** | 按业务增加资源类型，定义它的内容结构、展示方式和专用操作（资源动作目录，decision_ad3e21094f14），新对象即可接入统一的查找、引用和权限 |
| **可复用模块** | 保存成员职责、协作结构和交付要求，放入其他 Lineage 复用 |
| **Harness 插件** | 在模型调用前后增加上下文处理、工具使用优化和恢复辅助，按任务组合启用（decision_5c62deb8253a） |
| **AI Provider** | 接入不同的模型服务和 Agent 运行后端，通过同一个执行入口使用 |
| **主题** | 调整颜色、字体、间距、图标和组件外观 |

## 组成部分

```text
            浏览器页面            Agent（Harness 工具 / 命令行）
                 │                          │
          用户路由（选择 Dev 或 Release 实例）   │
                 │                          │
             Portal Web ──────── /api/instructions/execute
                 │
   ┌─────────────┼─────────────────────┬──────────────────────┐
   ▼             ▼                     ▼                      ▼
ResourceService  SocietyService       ExecutionService ──→ ExecutionWorker
   │             │                     │                      │
资源索引与文件   Lineage 与 Resident    调度与账号            Harness / Provider 适配层
                 状态                                          │
                                                           AI Provider
        └──────── PostgreSQL（各服务各自的 schema）───────┘
        └──────── NATS 变化通知 → Portal Web → SSE → 浏览器 ┘
```

| 组成部分 | 职责 |
| --- | --- |
| **浏览器页面** | 组织用户看到的工作台、对话和 Lineage 画布 |
| **用户路由** | 按用户的选择把请求送到 Dev 或 Release 的 Web；两边各有服务进程池，后台共享源码与业务数据，发布边界见 decision_08bb2d9141f9，选路见 decision_850e6248d680 |
| **Portal Web** | 确认调用者身份，提供页面和 HTTP 接口，把指令调用交给负责的服务，并执行用户、公共讨论（含聊天室）、User Mailbox 和 Portal 运行环境类指令 |
| **ResourceService** | 为人和 Agent 提供统一的资源访问：查找、读取、登记、修改资源，执行资源动作；在进程内运行权限系统，维护权限实例并处理权限指令（decision_b6e74d78f7b6、decision_b820faf42218） |
| **SocietyService** | 管理 Resident、对话、Lineage 定义与运行：投递消息、触发事件、执行动作组件、记录运行（decision_1b2bdf5c33d6） |
| **ExecutionService** | 调度模型任务：排队、按账号额度和并发派发、记录用量（decision_25eb93d803a4） |
| **ExecutionWorker** | 领取任务并实际运行模型会话，处理中断和恢复；Harness 在模型调用周围组合插件，Provider 适配层把统一调用翻译成具体 Provider 的协议（decision_425e428410e0） |
| **PostgreSQL** | 独立的 PostgreSQL 服务，Dev 与 Release 共用；各领域只修改自己拥有的数据分区（decision_86aed2f0e8c7） |
| **NATS** | 服务提交修改后发送变化通知的消息通道（decision_abaedeefca6f） |

## 各部分怎样协作

**指令调用。** 人在页面上的操作由 Portal Web 整理成 HATG ISA 指令，提交到唯一的指令地址 `/api/instructions/execute`；正在工作的 Resident 通过 Harness 工具调用指令，由执行层在后端直接交给负责的服务；Lineage 内部的动作组件在 SocietyService 进程内发出指令。每条指令在声明中写明由哪个服务执行，入口按声明把调用交给该服务。每个服务（ResourceService、SocietyService、ExecutionService、Portal Web）都在自己的 Unix socket 上提供 `instruction.execute` 方法，在本进程中运行共用的指令内核，完成参数校验、权限检查和执行记录（decision_898b8911a2ff、decision_96a887d83e8a）。

**服务之间的协作。** 一条指令需要其他服务配合时，负责它的服务通过本机 Unix socket 上的 RPC 直接调用对方的领域接口，例如 Fork Resident 时 SocietyService 请 ExecutionService 复制模型轨迹。页面读取数据使用 HTTP 与 JSON，只依赖公开的对象和操作含义，服务内部实现可以独立调整。

**模型工作。** SocietyService 把需要模型完成的工作交给 ExecutionService 排队；ExecutionWorker 领取任务、运行会话，并把消息和结果写回 Resident 的对话。AgentData 保存人机对话与 Agent 的执行轨迹，用于恢复、审计和改进系统（decision_b1ab45c4b3ec）。

**变化通知。** 服务提交修改后，通过 NATS 发送变化提示；Portal Web 通过 SSE（服务器向浏览器持续推送消息的连接）通知相关页面重新读取。不同用户、Agent 和页面因此能及时看到同一批工作的变化，状态和规则仍由负责对象的服务维持。

## 持久协议版本与命名兼容

每个服务按当前契约创建和读取持久数据；产品更名的标签别名与数据结构版本分别处理：

- **运行时校验当前结构与版本。** 数据表结构和格式版本必须与当前契约一致；不一致时服务拒绝处理并报告需要迁移，不用字段默认值、旧字段推断或旧位置回退来兼容。
- **公共格式按完整名称与版本精确匹配。** 当前公共格式写出 `tfi` 标签；共同读取谓词 `schema_matches` 只接受与当前规范期望值精确相同的完整格式名和版本。读取校验不转换内容、不放宽版本、不改写历史记录或摘要，也不授予调用权限。历史地图及已激活投影绑定仍按原始字节和哈希解释。
- **迁移用独立脚本在维护时进行。** 数据格式改变时，先用 `scripts/` 下的一次性迁移脚本在受控维护中转换，并记录每一项结果。迁移完成、数据已是新格式后，脚本和只读取旧结构的兼容代码一起从仓库删除，迁移过程由 Git 历史保留；同一结构与版本的标签更名也须显式迁移，运行时不保留历史别名。业务服务的启动和请求路径不调用迁移代码。
- **缺少可靠证据的历史记录保持原样。** 迁移脚本不猜测补全无法确定的字段。

各领域的具体结构版本由其契约常量与领域 Design 维护：执行调度见 decision_7956d7cb4bde，执行输入控制见 decision_6ad0384e020d，对话通知见 decision_edc470e09f5a，Panel 状态见 decision_4bc03b20c30f。系统总览不复制这些易随领域演进变化的版本号。

## 相邻 Design

- Dev 与 Release 实例、用户路由和服务控制：decision_850e6248d680；发布流程：decision_08bb2d9141f9。
- 服务运行状态与组件生命周期：decision_99bb5dc0f96b。
- 数据所有权与数据库分区：decision_86aed2f0e8c7；变化通知：decision_abaedeefca6f、decision_ecf0f2b2de28。
- 权限：decision_3fe3cca8fd95、decision_b820faf42218、decision_600ae27b0538。
- 规范的写法与索引：decision_ef80c6d3b5ee。
