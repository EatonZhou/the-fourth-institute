---
kb_id: decision_481dc4e6292c
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 31
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T08:31:22Z'
  updated_by:
    user: ''
    agent: resident_13db052ea5c9
importance: 8
metaness: 1
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/scheduled_triggered_monitor.py
  - ${code_root}/management_portal/agent_status_backend.py
  - ${code_root}/management_portal/static/home.js
  related:
  - decision_ef80c6d3b5ee
  - decision_5ff0cb51ed84
  - decision_898b8911a2ff
  - decision_68215b3a825b
  - decision_2a3ff353165c
  - decision_4637ca4a69e2
  - decision_5b5653c3eaa5
  - decision_8b510cbd9c58
  supersedes: []
  derived_from: []
---
# Home 定时与自动触发：查看与操作

Home 的 Agent Status 包含“定时与自动触发”面板（Scheduled & Triggered）。它列出由时间或外部事实自动启动工作的 Resident，以及调用 RunScript 的 Effect。这些 Resident 都是普通 Resident，没有单独的种类：可以是团队 Lineage 中的一名成员，也可以独自占一个 Lineage。工作何时开始、做什么，由所在 Lineage 中的事件源和连线决定（decision_5ff0cb51ed84）。

面板显示的每一项都从 Resident、Lineage 和脚本运行的当前状态读取，页面上的每个操作都对应一条 HATG ISA 指令（decision_898b8911a2ff）。运行计划来自 Lineage 的事件源；触发过程从 Trigger Path 读取，Resident 实际工作从它关联的 Agent Execution 读取，脚本运行记录来自 RunScript。面板自己不保存这些状态。

## 哪些工作出现在面板中

- **由时间或外部事实驱动的 Resident 出现在面板中。** 调用者可见的 Resident 中，所在 Lineage 有 `time.reached` 或 `file.changed` 事件源连到它的 Invoke 端口的，列为一项。判断只看 Lineage 中的实际连接，不读取任何类型字段。
- **一个 Resident 或脚本 Effect 只占一行。** 它有多个触发事件源时，在同一行中分别列出每个事件源的计划。
- **调用 RunScript 的 Effect 也列为一项。** `time.reached` 或 `file.changed` 通过 Event 连线接到该 Effect 的 Invoke 端口时，面板解析 Effect 的指令脚本，发现 RunScript 调用便显示该行。条件分支和循环中的调用也属于该 Effect；注释或普通字符串不构成调用。
- **按 Lineage 分组。** 同一 Lineage 的 Resident 和脚本 Effect 归在一组，组标题显示 Lineage 名称和该 Lineage 的 Resident 总数，由此可以看出这个 Resident 是团队中的一员，还是独自占一个 Lineage。组按 Lineage 标题排序，组内按工作标题排序；标题为空时使用 ID，排序不区分大小写。面板顶部汇总 Lineage 数、Resident 数、脚本 Effect 数和事件源数。
- **一个 Lineage 读取失败只影响对应的行。** 该行显示读取错误，其他行照常显示和操作。

## 每一行显示什么

| 内容 | 含义 | 读取来源 |
| --- | --- | --- |
| 名称 | Resident 标题和 `resident_id`；所属 Lineage 及其 Resident 总数显示在分组标题中 | `GetResident`、经授权的 Lineage 定义读取 |
| 触发计划 | 每个触发事件源的计划（指定时间、固定间隔或日历规则）、随机偏移、下一次计划触发时间；暂停的事件源标为“已暂停” | 经授权的 Lineage 定义读取、`GetComponentRuntime` |
| 当前运行 | Resident 的工作状态、当前处理的任务、等待数量和接单开关 | `ListResidentStates` |
| 最近运行 | 最近一次 Trigger Path 的状态、发生与结束时间，以及连续失败次数；按 Resident 过滤，不受同一 Lineage 中其他 Resident 的触发次数影响。路径可以关联独立的 Execution | `ListTriggerPaths(resident_id)`、`GetTriggerPath` |
| 模型设置 | 当前 Provider、账号、模型与推理强度 | `GetResidentSettings` |
| 告警 | 见“健康状态” | 由以上各项计算 |

展开一行后，详情显示连线上写明的任务说明、Lineage 诊断和最近若干次 Trigger Path 的记录，并提供“打开 Lineage”入口，进入该 Society 页面查看和编辑完整流程。

### 脚本 Effect 的行

脚本 Effect 显示名称、Effect ID、脚本文件、每个事件源的计划与暂停状态、最近脚本运行的状态及开始时间。字面量路径直接显示；依赖事件或前序指令结果的路径标为“动态路径（运行时确定）”，详情展示最近运行实际使用的路径。一个 Effect 调用多个脚本时，路径汇总在同一行，最近运行按该 Effect 的脚本开始时间排序。

脚本状态与历史通过 `ListScriptRuns(lineage_id, effect_id)` 读取；事件源的投递结果通过 `GetComponentRuntime` 读取，Effect 的执行结果通过 `GetLineage` 读取。Effect 在 RunScript 启动前失败时，即使没有脚本运行记录也显示异常及失败原因。`running`、`succeeded`、`failed`、`timed_out`、`cancelled`、`lost` 分别显示运行中、成功、失败、超时、已取消、运行丢失。失败、超时、取消和丢失参与异常与连续失败判断；成功时间用于陈旧检测。读取失败保留该行并显示错误，不影响同一 Lineage 的 Resident 行。

脚本行复用事件源的修改计划、暂停、恢复和手动入口；这些操作作用于事件源。脚本行不提供 Resident 模型设置和 CancelInput，停止脚本使用 RunScript 的取消指令。展开后提供脚本历史、诊断与打开 Lineage 的入口。

## 健康状态

一行的健康状态由以下事实综合得出，任一事实都不能单独代表健康：

| 事实 | 显示 |
| --- | --- |
| Lineage 诊断存在错误 | 异常 |
| 自动事件源运行态为 `blocked` | 异常，显示该源的 `error` 原因并引导检查 Lineage 自动运行身份 |
| 最近一次 Trigger Path 以失败或取消结束，或连续失败两次及以上 | 异常 |
| 事件源最近一次投递含失败，或 Effect 在启动脚本之前执行失败 | 异常，显示投递来源或 Effect 错误 |
| 距离上一次成功运行已超过两个计划周期 | 警告：运行陈旧 |
| Resident 的接单开关已关闭 | 警告：任务只排队、不执行 |
| 触发事件源已暂停 | 已暂停（不计为异常） |
| 全部定时已达到最大触发次数 | 警告：不会再运行，可以归档或删除 |
| 全部定时是已经运行过的一次性定时 | 警告：不会再运行，可以归档或删除 |
| 连续失败 5 次以上且超过 7 天没有成功 | 警告：长期失败，请检查任务或停用定时 |

触发事件源处于启用状态，只说明计划仍在生效，不能说明工作本身健康。

## 操作

| 页面操作 | 对应指令 | 效果 |
| --- | --- | --- |
| 暂停调度 / 恢复调度 | `PauseComponent(paused=true)` / `PauseComponent(paused=false)`，作用于该 Resident 或脚本 Effect 的触发事件源 | 暂停期间到期的触发被跳过；恢复后从下一次计划时间继续，不补发暂停期间的触发 |
| 修改定时 | `UpdateComponent`，修改 `time.reached` 事件源的计划与随机偏移 | 从下一次计划开始按新时间触发 |
| 运行一次 | `TriggerEvent`，作用于同一 Lineage 中连到该 Resident 或脚本 Effect 的手动事件源 | 立即投递一次任务，不改变后续计划 |
| 取消当前运行 | Resident 使用 `CancelInput(resident_id, message_id)`；运行中的脚本使用 `CancelScriptRun(script_run_id)` | 停止所选运行，不影响后续计划；同一 Effect 的并行脚本分别提供取消入口 |
| 修改模型 | `UpdateResidentSettings`，修改模型与推理强度 | 从下一轮工作开始生效；切换 Provider 或账号不在面板进行，需打开 Lineage 使用 `RestartSession` |

- **每个操作只改变一件事。** 暂停调度不关闭接单，也不取消当前运行；取消当前运行不暂停调度；修改定时不立即触发。
- **Lineage 中没有连到该 Resident 或脚本 Effect 的手动事件源时，“运行一次”不可用。** 按钮说明缺少手动入口；内置 Function Block“定时工作”自带手动入口（decision_5b5653c3eaa5）。
- **身份阻塞和计划等待按权威运行态展示。** 自动源的显式运行 User 由 Lineage 创建与设置入口配置，面板不按创建者、owner 或当前浏览者代填。`GetComponentRuntime` 返回 `state=blocked` 时，即使从未有 Trigger Path，也将该行标为异常并显示具体原因。主体恢复有效后重新读取同一状态与计划，旧错误不继续保持。下一次实际时间尚未建立时明确显示等待建立，周期说明不能替代到期时间。手动运行仍按实际调用者和自己的入口检查权限。
- **操作前由权限系统检查调用者。** 检查失败时页面显示拒绝原因，该行状态保持不变。
- **操作完成后重新读取面板。** 指令成功后，页面绕过汇总缓存重新读取全部行。汇总仍在重建时明确提示“操作已提交，正在刷新状态”，并继续读取；取得新状态后才按新状态显示。汇总中的异常任务只影响任务健康状态，不改变已经提交的操作结果。指令失败时恢复按钮并显示失败原因；指令已提交但刷新失败时明确提示“操作已提交，但状态刷新失败”，不重放操作。
- **“运行一次”遇到正在运行的任务时排队。** 手动触发的 Event 在目标忙碌时默认进入等待队列（连线明确配置 `unavailable_policy` 时以配置为准），不再静默丢弃。

## 定时与自动触发的工作怎样执行

- **按时触发。** Lineage 协调器在每个时间事件源的下一次到期时间唤醒其 Lineage；文件事件源每 60 秒查看一次。Dev 与 Release 共用 Lineage 数据，由数据库中的租约决定哪个实例负责定时（90 秒有效、每 30 秒续期；持有者停止后另一实例接管并重新检查全部到期事件源）。已达到最大触发次数或到期时间已过的事件源不安排唤醒。
- **Resident 与脚本 Effect 各自完成适合的工作。** 需要模型判断的任务投递到 Resident；确定性维护投递到 Effect，由它调用 RunScript 异步运行沙箱脚本，结束后产生 `script.finished`。系统层定时器保留独立于 Portal 的 Resource Backup（decision_8b510cbd9c58）。
- **自动运行的上下文与失败处理由运行域定义。** 默认全新上下文、连线的 `context_policy: "session"`、投递方式持久记录，以及失败处理者、管理员通知和去重规则，统一见 decision_5ff0cb51ed84；消息与执行关联见 decision_68215b3a825b。Home 只读取这些事实展示健康状态，不再维护一份上下文或通知规则，也不以面板刷新触发恢复执行。

## 页面怎样读取

汇总先按当前调用者发现可见 Lineage，再读取用于识别触发连线的定义：Lineage 名称、定义版本、成员标识与标题、组件及连线。候选发现不需要构造完整工作台视图，也不需要展开各成员的工作区、设置或运行状态。每个 Lineage 的定义读取仍遵守 `GetLineage` 在当前权限模板中的要求（默认对该 Lineage 要求 viewer），并检查该调用者的当前领域可见性；删除中的 Lineage 不作为可用定义返回。权限或领域读取失败继续作为该 Lineage 的读取错误显示，不能伪装成没有任务。

同一次汇总读取可以批量读取候选定义。权限检查在该请求内对同一调用者逐个 Lineage 一次性判定（权限端口的 `authorize_reads`，decision_b820faf42218）；只有获准的对象进入领域读取。批量定义读取复用一个只读快照，每个 Lineage 仍独立执行领域可见性与删除保护检查。单个对象的拒绝、缺失或读取错误不能隐藏其他获准对象；权限服务不可用必须显示读取失败，不能作为权限豁免或空结果。请求结束后不保留这次授权结果。

没有符合条件的自动触发连线时，不读取该 Lineage 的组件运行状态、诊断或 Trigger Path 历史。找到候选后，按原有对象范围读取这些事实及 Resident 详情，保留任务、计划、暂停、健康状态、历史归属和不可读详情的显示规则。独立 Lineage 的明细最多四路并发读取，各工作线程继承同一调用者的授权上下文；单个 Lineage 的读取错误保留在自己的行中。定义投影与源对象分离，不持有跨请求的授权结果，也不扩大下面既有按调用者隔离的汇总缓存范围。

读取一个 Lineage 的组件运行状态时，同批事件源复用一次该 Lineage 的事件源运行事实读取，再分别生成视图；组件字段、排序与单组件过滤语义保持一致。循环组件的视图与暂停、恢复、重置等控制操作继续遵循各自的既有契约。

Portal Web 的 `scheduled_triggered_monitor.py` 提供一个只读的汇总读取（`GET /api/agent-status/scheduled-triggered`），格式名 `tfi.scheduled_triggered_monitor.v1`，一次返回全部行。它按上表及脚本行规则调用各对象的读取指令并组合结果，每行以 `kind` 区分 Resident 与 Effect，分别提供 `resident_id` 或 `effect_id`；Effect 的页面行键由 Lineage ID 与 Effect ID 共同组成。每行附带读取时间、读取错误和所属 Lineage 的 Resident 总数（`lineage_resident_count`）。Home 使用面板 ID `scheduled-triggered`；页面中的状态、操作和样式统一使用 `scheduledTriggered` / `scheduled-triggered` 名称。Portal Web 按调用者缓存这份汇总 30 秒。首次读取在后台构建，HTTP 立即返回 `status=loading, loaded=false`，可见面板每五秒读取同一份构建结果。`refresh=1` 只使当前调用者的缓存过期，保留上次结果用于显示；已有缓存时，操作前发起的构建脱离当前缓存，不能覆盖操作后的新结果。首次冷构建仍由并发读取复用。返回旧结果时附 `refreshing=true`，页面每五秒普通读取直至当前构建完成，避免旧状态被当作刷新完成，也不反复使同一构建失效。后台构建保留请求的授权上下文，失败时显示读取错误，不能作为没有任务的空结果。

- **面板显示时才读取。** 面板首次显示时绕过汇总缓存请求当前状态；面板不可见时不请求。Agent Status 工具栏的刷新按钮在面板可见时也会重新请求汇总（不绕过缓存）。
- **有工作进行中时每 5 秒跟进一次。** 读取结果中任一行有正在处理的消息，或工作状态为排队、运行、停止中，或最近一次 Trigger Path 仍在运行时，页面 5 秒后带 `refresh=1` 再读一次。连续快速跟进 120 次后改为每 30 秒读取，直到工作结束或面板隐藏。静默读取不显示加载占位，失败时保留已显示的行。
- **空闲面板每 30 秒更新，隐藏时停止。** 所有工作结束或列表为空时，可见面板继续每 30 秒绕过缓存读取，发现其他页面新增、修改或删除的定时流程。页面转到后台、面板隐藏或卸载时释放计时器；从后台返回或面板重新显示时立即读取当前状态并重新计算跟进周期。
- **同一时间只有一次读取。** 普通读取复用进行中的请求；强制读取等待当前请求结束后，再绕过缓存读取，提示区分领域操作已提交与状态已刷新。请求被取消时恢复之前的状态，页面恢复后可以重新读取。
- **面板保留未提交的编辑。** 其他区域刷新不会重建本面板；本面板的数据变化时保留未提交的输入和焦点。提交中的行在操作成功后的重读中采用服务端配置。

Home 的 Agent Status 布局内容版本为 2，与 Workbench 布局结构的版本分别管理。首次读取各用户保存的旧布局时，Home 将面板引用迁移为 `scheduled-triggered` 并写回，保留布局位置、隐藏与折叠状态、保存的工作区和恢复位置；用户自定义的名称与面板内容保持原样。身份在页面加载后才确定时，也在恢复该用户布局之前完成迁移。后端汇总格式用于 HTTP 响应和内存缓存，不持久保存。

页面上的操作直接通过 Portal 的指令执行地址提交对应指令，不经过这个汇总读取。
