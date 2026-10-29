---
kb_id: decision_44cf587c1721
type: decision
project: AI员工
created_at: '2026-10-01'
version:
  number: 158
  created_at: '2026-10-01'
  created_by:
    user: ''
    agent: resident_4b1da6140cf2
  updated_at: '2026-10-29T15:50:45Z'
  updated_by:
    user: ''
    agent: resident_24014e2b9bf6
importance: 8
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/society_service/family_workflows.py
  - ${code_root}/management_portal/society_service/lineage_program.py
  - ${code_root}/management_portal/society_service/lineage_program_effect_execution.py
  - ${code_root}/management_portal/society_service/resident_context/service.py
  related:
  - decision_0f57656a2acd
  - decision_6fc653d55c79
  - decision_4adcc1ff2f2c
  - decision_5b5653c3eaa5
  - decision_2fe6fd5367fb
  - decision_3e07c6d35cac
  - decision_d19b9909a674
  - decision_2a3ff353165c
  - decision_8fe87d812a4c
  - decision_bd5217d08c09
  - decision_b820faf42218
  - decision_600ae27b0538
  supersedes: []
  derived_from: []
metaness: 3
---

# Family：把多个 Resident 组合为对外一个 Resident

Family 把同一 Lineage 中的业务 Resident 组合成一个对外入口：门面保存外部请求，Router 选择承接者，成员完成实际工作，Steward 维护分工、记忆、积压与恢复。平台维护转交关系和运行保障，使请求、执行结果与故障都能回到原入口。

Family 是一种特殊 Function Block，不新增执行主体层级。门面、Router、Steward、成员各有 Resident 身份；门面不运行模型。正式资源地址为 `family_…`，内部块键 `family_id=fbi_…` 保留为图中的局部身份。

## 专题与权威边界

本文维护组成、归属、生命周期和共享边界；具体机制分别在下列 Decision 中维护。

| 阅读目的 | 规范 |
| --- | --- |
| 请求怎么选人、转交、改派、撤回与回复 | 消息路由与对外对话 |
| Router/Steward 由什么 Event 触发，执行什么 Effect，收到什么 prompt | Event、Effect 与 Prompt |
| 摘要谁维护、队列怎么整理、额度或成员故障怎么恢复 | 记忆、队列与故障恢复 |
| 画布、工作流程编辑、门面 Chat 与状态表怎么使用 | 画布、菜单与状态交互 |
| 用户个人助手的创建、历史、偏好和组织确认 | 私人 Family 与个人助手 |

接口参数以 [Family 内部 ISA](/r/decision_b77fde81e7a0) 和各指令 schema 为准。通用 [Function Block](/r/decision_5b5653c3eaa5)、[Resident 摘要](/r/decision_d19b9909a674)、[Mailbox](/r/decision_15f7df737ed5)、[Session 迁移](/r/decision_c47a13e8b962) 继续拥有各自协议；Family 专题不建立平行定义。

## 角色与协作

| 对象 | 责任 |
| --- | --- |
| 门面 Resident | 外部身份与输入存储；依据转交记录投影实际成员的进度和回复，无模型 Session |
| 业务成员 | 承接任务、维护自身近期事实及共享工作产物，按职责协作 |
| Router Resident | 持续保留上下文，按需读取职责、摘要、状态和历史，输出承接目标与判断；选人任务不负责执行业务或自行投递 |
| Steward Resident | 判断职责缺口、组织调整、Mailbox 整理与故障恢复；归纳成员时间层，按需汇总门面 |
| 平台与运行图 | 监听事件、绑定原请求、校验投递、保存状态、执行无模型巡检和恢复升级 |

一条门面请求的典型过程是：引用成员消息的请求交回该成员，Family 以外的 Resident 接着发来的请求交给承接它上一条请求的成员；其余请求（包括单成员和空业务名册）由 Effect 发精简任务给 Router，再依据 Packet 转交；职责缺口交 Steward 安排。零成员保留原输入等待。转交后的执行与回复跟随同一原请求；低额度、关闭接单、改派和取消不能另造替代业务任务。

Router、Steward 使用自身 Provider 会话与设置。完整指南只在会话首轮注入，后续发本轮目标、必要事实、指南路径及版本；资料由角色按需读取。首轮与逐轮模板、直接服务派发和自动 Event 的区别由工作流专题精确定义。

## 创建范围与成员归属

**局部选区与 Lineage 范围是两种显式范围。** Familize 局部选区至少包含一个有效业务成员；折叠 Family 卡片解析成内层门面，整个子块成为外层元素。普通组件和连线不能算成员，普通 Function Block 不能冒充 Family。

CreateLineage 的界面默认勾选 Familize，可取消；API 未传 familize 保持普通创建。Settings 的 Familize Lineage 使用 scope=lineage，保存 lineage_scope。同一 Lineage 最多一个范围 Family，包含未归属其他 Family 的业务 Resident 和顶层子 Family 门面；已有普通分组保留，内部角色和内层成员不重复加入。重复创建返回现有范围 Family，该层不能再嵌入其他 Family。

空 Lineage 可以先建范围 Family，保留无成员时的原请求；创建 Lineage 已提交而 Familize 失败时，错误带已建 Lineage ID，界面进入其 Settings 修复，不重复创建 Lineage。

**每个 Resident 最多直接属于一个 Family，成员关系构成树。** 所有直接成员必须属于同一 Lineage；内层门面可同时是内层 facade 与外层 member，执行身份以其内层角色为准。创建和名册变更检查循环、唯一归属及角色约束，Router、Steward 不充当本 Family 业务成员。

CreateResident/ForkResident 优先遵循显式 family_id；否则成员或 Steward 创建的新 Resident 加入创建者所在 Family，再否则按 Lineage 范围 Family 默认加入。没有范围 Family 时，在折叠门面外新建不推断归属；展开 Family 内的新建请求显式带 family_id。加入时维护监听和路由，并通知 Steward 更新分工；Steward 自己创建不再自我通知。

**Family 创建者只记录实际人类调用者。** human_familizer_id 由 families 保存；Resident 创建的 Family 留空，不能推测为 owner、最近发送者或管理员。它是联络事实，不授予权限。业务联系人及持续角色故障升级的区别见运行维护专题。

模板施加由 ApplyTemplate 调用 Family 模块，分别配置 Router、Steward 的运行设置，门面无需模型配置。用户可指定名称；局部选区缺省以某个成员标题加“-family”，范围 Family 用 Lineage 标题。Family 名称同时作为门面和块标题。

## 结构维护与普通工作流

| 指令 | 改变状态 | 责任 | 默认方案要求 |
| --- | --- | --- | --- |
| `Familize` | 是 | 建立组合、角色、内部运行图与入口，完成必要的 leader 交接 | 对 `lineage_id` 有 @contributor |
| `UpdateFamily` | 是 | 修改成员、名称、目标、route_threshold、review_interval_seconds、自动转组开关 auto_migrate、账号种类优先级 auto_migrate_priority 和普通元素归属 | 对 `lineage_id` 有 @contributor |
| `Unfamilize` | 是 | 解除组合，保留业务成员、子 Family 与普通工作流 | 对 `lineage_id` 有 @contributor |
| `DeleteFamily` | 是 | 删除整个 Family 及内部业务对象 | 对 `lineage_id` 有 @admin |

权限由当前启用方案判定，表中默认规则不替代实际核权。通用 UpdateFunctionBlock 对 Family 只改坐标，不能绕开专用生命周期指令改角色。

Family 模块按稳定 element_key 维护生成结构，未变化组件保留 ID；普通工作流使用 workflow: 键，名册同步不把它们当作生成对象重建。用户编辑的 Event 参数和指南/prompt 保留，模板拥有的结构与脚本仍由模板协调；精确字段归属见工作流专题。

UpdateFamily.add_element_ids/remove_element_ids 处理当前 Lineage 的普通组件及连线，成员和子 Family 则通过名册处理。可收纳无直接归属或祖先 Family 的普通工作流，不接管其他块或生成结构；加入连线的两端必须在递归范围内。移出组件时关联内部线一并解除归属，有父容器则回到父容器，不删除对象。列表重复、交叉或越界整次拒绝。

新增成员时可自动收纳专属连通组：其 Resident 边界非空且全部在 Family 递归范围内，没有其他块所有权冲突。共享分支和边界不明的脚本不猜归属，可以显式收纳。普通参数更新不自动收回用户移出的元素；不以标题、应用名或缓存判断所有权。

收纳不改变 Event、Effect、连线 ID、业务配置、游标、暂停状态和执行目标。外部入口与内部工作流的界线按结构范围确定：范围外指向新增成员的入口改接门面，范围内显式执行目标保留；即使连线存放在别的块也依语义判断。不得为 Familize 重建缺失工作流或把业务 Effect 的执行者换成门面。

auto_migrate 与 auto_migrate_priority 只决定额度不足时是否自动更换成员、Router、Steward 的账号及备用账号种类的顺序，不改变名册和结构；判定与恢复规则见 记忆、队列与故障恢复。

## 个人聊天偏好

| 指令 | 改变状态 | 责任 | 默认方案要求 |
| --- | --- | --- | --- |
| `GetFamilyChatPreferences` | 否 | 读取调用者本人在该门面上设为 Mute 的成员和 Solo 成员 | 对 `resident_id` 有 @viewer |
| `UpdateFamilyChatPreferences` | 是 | 开启或关闭调用者本人对一个业务成员的 Mute 或 Solo | 对 `resident_id` 有 @viewer |

两条指令的 `resident_id` 是 Family 门面，只读写实际调用者自己的偏好；可以查看门面的用户即可管理自己的偏好。偏好只约束调用者此后发出的普通消息的承接者及其门面对话中显示哪些成员的往来，不改变成员接单状态、名册、原消息、权限或其他用户的偏好。多个 Mute、单个 Solo、Solo 期间与 Mute 的关系、问题卡片与引用的处理由 消息路由与对外对话 维护。

## leader 与权限

被组合成员包含 Lineage leader 时，leader 交给门面；Lineage 范围及私人 Family 同样使用门面 leader。外部 leader 入口和上层结构引用随交接更新，内部显式执行目标保留。

门面身份用于对外署名和协调入口，**不表示成员、Router、Steward 可以通用地借用发送者或门面的权限**。自动 Event 使用明确执行主体并按当前权限核验；有限的原请求转交授权和附件读取由消息路由契约限定。实际执行者始终可追溯。

Steward 的管理能力来自权限模板对当前 Family 所在 Lineage 的授权，随角色变更、迁移和解散调整；不以额外临时凭证另造常驻权限。权限树与默认方案见 [权限资源规则](/r/decision_600ae27b0538)。

## 退出、迁移与删除

**成员删除保留历史身份。** DeleteResident 移除有效名册及监听，后续不再选择、恢复该成员；deleted_at/deleted_by 表达删除，对话、轨迹与历史引用按权限保留。删除不等于可复活的临时关闭。门面、Router、Steward 不能绕过 Family 约束单独删除。

**MoveResident 迁出即离开原 Family。** 名册和结构在同一提交中更新，权限树随新 Lineage 移动。已经交给该成员的请求仍由它完成，回复沿 family_relays 回门面；以后引用其旧回复时因不在名册而重新路由。迁移结构信号通知 Steward，自己发起则不重复通知。内部三个角色不能单独迁出；可移动整个 Family 或先解除。私人默认 AMA 的迁移限制由个人助手专题定义。

**Unfamilize 先使内部角色安静，再拆结构。** 关闭三个角色接单、停止当前工作并撤回队列；等待期间不持有 Program 投递锁。停下后在 Lineage 执行权内再次清理迟到输入，交还 leader、改接所有保留的门面入线，删除生成结构。超时返回可重试冲突，图、leader、连线不发生部分拆除；保留业务成员不被停止。

解除保留普通工作流和子 Family；解除内层时，其保留成员进入外层名册。门面为 leader 时交给指定成员，默认原 leader；原 leader 已离开须显式指定。无原 leader 的范围 Family 有成员时可交名册首位，无成员则恢复空 Lineage。范围解除后停止新 Resident 的默认入组。

**DeleteFamily 递归删除整组对象。** 先确定角色、成员、子 Family 和内部工作流，关闭接单并等待停止，再在执行权内核对安静状态，原子删除图和身份。停止等待中内容变化返回可重试冲突，不部分删除。所在 Lineage 与 Family 外成员保留，外部失效连线按通用规则清理，Workspace/Session 随 Resident 删除，对话轨迹保留。

## 状态所有权与资源读取

| 权威状态 | 所有者 |
| --- | --- |
| Family 标题、目标、范围、角色关系、创建人、生命周期 | families、family_residents |
| 块坐标、元素 key、组件、连线及用户工作流配置 | 通用 Lineage/Function Block 运行图 |
| 原输入到实际成员的关联、改派、进展批次 | Family 转交与进展领域 |
| Resident 输入终态、执行与输出 | Resident/Execution 领域 |
| 时间层摘要与覆盖 | Resident 摘要领域；Family 内按角色分工写入 |
| 账号准入、Session 迁移 | Execution 与 Restart/lifecycle |
| Family 恢复、额度复核与升级记录 | Family 监督领域 |
| 用户个人的 Mute／Solo 偏好，及按原输入冻结的路由约束 | family_chat_preferences、family_chat_inputs |

families 的 family_id 与 function_block_id 相同，resource_id 是持久的正式资源身份。family_residents 保存 member/facade/router/steward；普通 Resident 更新不能覆写归属。完整图和 Family 专表在同一事务更新，读取时附加只读 function_blocks[].family 投影，不在普通块配置里再存一份名册。

投影包含角色、成员、标题、目标、阈值、回顾周期、previous_leader_id、human_familizer_id、lineage_scope、resource_id，以及私人 Family 的 AMA 与历史分界。route_threshold 默认 60（0–100），review_interval_seconds 默认 86400 且为正；具体 Event/prompt 用户配置以运行图为准。读取专表列出列名，避免在线加列改变长连接预备查询的结果类型。

Family 的正式资源类型为 family，归属当前 Lineage 并投影 Project；迁移保持 resource_id，权限关系随 Lineage 移动。ResolveResource/ReadResource/SearchResources 从当前业务事实投影，索引不能覆盖真实名册或生命周期。family_id 块键只在指定 Lineage 中匹配，不能借此访问别的 Family。

创建、重命名与名册变更发布同一资源描述符；Unfamilize 或删除 Lineage 保存删除墓碑并撤下有效发现。现存资源可周期对账重建，删除投影按提交回执重试；同一删除时间的墓碑内容必须稳定，不能事后改写再当同次删除重放。描述符、发现摘要和 UI 均不是第二套权威状态。

## 维护边界

修改角色触发或 prompt 更新工作流专题；改变恢复策略更新运行维护；改变对话语义更新消息路由；界面与私人助手分别更新所属专题。主文仅在组成、归属、生命周期或跨领域契约改变时更新。

运行保障的已实现能力与未完成补强在运行维护中分开说明。不能把“希望始终可运行”写成已有的无条件保证，也不能把旧事故、发布记录或测速保留为当前规范。实施与验证留在任务记录、Git 和历史版本中。


## 参数名契约

下表列出 arguments 内的顶层参数名；必填条件、类型和枚举以 GetInstructionSchema 为准。调用信封中的 expected_version、idempotency_key 与 wait_seconds 不列入 arguments。

| 指令名 | arguments 参数名 |
| --- | --- |
| `Familize` | `lineage_id`、`resident_ids`、`scope`、`title`、`goal`、`parameters`、`resident_settings`、`near_resident_id`、`placement`、`x`、`y` |
| `Unfamilize` | `lineage_id`、`family_id`、`successor_resident_id`、`rewire_to_resident_id` |
| `DeleteFamily` | `lineage_id`、`family_id` |
| `UpdateFamily` | `lineage_id`、`family_id`、`add_member_ids`、`remove_member_ids`、`add_element_ids`、`remove_element_ids`、`title`、`goal`、`route_threshold`、`review_interval_seconds`、`auto_migrate`、`auto_migrate_priority` |
| `GetFamilyChatPreferences` | `resident_id` |
| `UpdateFamilyChatPreferences` | `resident_id`、`member_id`、`control`、`enabled` |
