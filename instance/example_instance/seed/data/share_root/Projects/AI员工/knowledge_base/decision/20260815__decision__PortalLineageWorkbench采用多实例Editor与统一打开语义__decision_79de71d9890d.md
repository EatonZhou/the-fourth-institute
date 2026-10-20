---
kb_id: decision_79de71d9890d
type: decision
project: AI员工
created_at: '2026-08-15'
version:
  number: 16
  created_at: '2026-08-15'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-20T00:00:45Z'
  updated_by:
    user: ''
    agent: resident_6b7d6cdae31c
importance: 9
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/static/portal_editor_open_service.js
  - ${code_root}/management_portal/static/portal_editor_open_commands.js
  - ${code_root}/management_portal/static/portal_workbench.js
  - ${code_root}/management_portal/static/lineage_workbench.js
  - ${code_root}/management_portal/static/lineage_graph.js
  - ${code_root}/management_portal/static/hatg_panel.css
  related:
  - decision_61e50306cc79
  - decision_9f6135cbbb22
  supersedes: []
  derived_from:
  - decision_538e155e1102
---
# Society Workbench 多实例 Editor 与统一打开协议

Society Workbench 是 Portal 中查看和操作 Lineage 的工作台页面。用户在这里从导航树、消息、Lineage 图中的节点、文件链接或全局资源搜索结果打开 Resident 的对话、文档和工具。每次打开都要决定：放进当前的编辑区、开一个新标签，还是在旁边分出一栏。本文规定这个决定怎样作出，让所有入口使用同一套规则，不会随意覆盖用户正在处理的内容。

本文负责：用户可以选择的打开方式、编辑区实例的身份、何时可以安全复用、新实例放在哪里、打开方式菜单、并发打开的结果，以及恢复和删除时的处理。它不读取资源，也不保存正文：权限检查、未保存的草稿和保存由各编辑区自己负责；标签和分栏由 Workbench 负责（decision_538e155e1102）；文件与资源怎样加载和显示见 decision_61e50306cc79。

## 用户看到的编辑对象

- **编辑区（Editor）**：Workbench 中打开一个 Resident 对话、文档、资源或工具的面板。“编辑”也包括只读查看。
- **编辑实例（Editor Instance）**：一次可以单独定位的打开。它有稳定的实例 ID、编辑区类型、当前打开的目标和所在的标签组。
- **打开目标（binding）**：实例当前打开的是什么，例如资源 ID、文件路径或 Resident。
- **活动实例**：当前获得焦点或正在显示的编辑实例。
- **新标签**：在一个标签组中增加一个编辑实例。
- **在旁边打开**：在左、右、上或下方向新建一个相邻分栏。
- **打开方式菜单**：列出“Open in New Tab”和“Open to the Side”，有时还有其他项（见下文）。普通点击不经过菜单，按复用方式打开。

每个实例有四个标记，决定它能否被另一个目标安全复用：`dirty`（有未保存的内容）、`locked`（不允许换成别的目标）、`reusable`（允许复用，默认允许）和 `singleton`（同类型在当前工作台只有这一个实例）。

## 设计点

- **普通点击优先复用一个安全的同类型实例。** 有未保存内容、被锁定或声明不可复用的实例，不会被另一个目标悄悄替换。
- **“在新标签打开”总是新建实例。** 实例放进指定的标签组或当前位置；同一请求执行两次会得到两个标签。
- **“在旁边打开”在指定方向新建分栏。** 没有给方向或方向无效时放在右侧。
- **单实例编辑区始终只有一个。** 即使用户选择新标签或在旁边打开，也复用这个实例；切换打开目标前，由这个实例自己检查未保存内容，询问用户是否放弃。
- **所有入口提交同一种打开意图。** 普通点击、打开方式菜单、导航树、文件链接、全局资源搜索结果和面板内的动作，都遵守同样的复用和防覆盖规则。
- **Lineage 图中 Resident 节点的打开方式按钮在卡片右上角内侧。** 桌面端在鼠标悬停、节点被选中或键盘焦点进入节点时显示；触屏等粗指针设备上一直显示。按钮是没有边框和阴影的低调图标，不与节点状态、端口和标题争夺注意力。
- **打开方式菜单贴着触发按钮的右下角展开，没有按钮时贴着鼠标位置。** 打开后焦点落在第一项；点击外部或按 Esc 关闭，焦点回到触发按钮。
- **Resident 的打开方式菜单还提供对话、Family 状态与详情入口。** “打开对话”聚焦这个 Resident 已经打开的对话（优先选择当前布局中可见的那个），没有时按复用方式打开；“Family 状态”打开这个 Resident 的 Family 状态面板；“Menu”打开这个 Resident 的详情菜单（个人背包）。
- **打开失败时显示错误通知，保留当前工作台。** 通知内容为“打开编辑器失败：<原因>”，当前实例和布局不变。

## 打开意图及规范化

调用方提交一个**打开意图（intent）**，描述要打开什么和希望放在哪里：

| 字段 | 含义 |
|---|---|
| `type` | 编辑区类型；可以直接给出，也可以从 `resource.kind` 或 `resource.type` 得出 |
| `resource` | 打开目标，可以包含 ID、路径和标题 |
| `mode` | `reuse`、`new-tab` 或 `side`；缺失或无效时按 `reuse` |
| `side` | `left`、`right`、`top` 或 `bottom`；缺失或无效时按 `right` |
| `targetInstanceId` | 优先考虑的实例 |
| `targetStackId` | 新实例优先放入的标签组 |
| `source` | 打开动作来自哪里 |
| `selection` | 打开后要定位的选区 |

`normalizeIntent` 先确定类型，确定不了时立即报错，工作台保持不变。规范化后的意图保留打开目标、来源、选区和目标位置，并把 `resource.kind` 设为确定的类型。打开方式只说明“复用还是新建、放在哪里”，不表示资源已经读取、权限已经通过或未保存内容已经保存。

## 怎样决定复用还是新建

`resolve(intent, state)` 是纯计算：它读取实例列表、活动实例和标签组，返回“复用某个实例”或“新建实例并放在某处”，不修改工作台。菜单预览和真正打开使用同一个算法。判断顺序：

1. 已有同类型的单实例编辑区时，总是复用它。
2. 打开方式为 `reuse` 时，依次考虑 `targetInstanceId` 指定的实例、当前活动实例、从最近到最早的其他实例，选第一个满足条件的：类型相同、没有声明不可复用、没有锁定、没有未保存内容。
3. 找不到可复用的实例时新建。标签组依次取：意图的 `targetStackId`、指定实例所在组、活动实例所在组、工作台的活动目标所在组、当前聚焦的标签组。
4. `side` 放在请求的方向；`reuse` 和 `new-tab` 的新实例放在标签组中央（即作为新标签）。

## `PortalEditorOpenService` 接口

`PortalEditorOpenService.create({state, createInstance, reuseInstance})` 需要宿主提供三个函数，缺一个时创建失败：

- `state()`：返回当前实例、活动实例和标签组的只读状态。
- `reuseInstance(instanceId, intent, context)`：让已有实例打开新的目标。
- `createInstance(intent, context)`：创建并放置新实例；`context` 带有 `stackId` 和 `zone`（`center` 或方向）。

服务提供：

- `resolve(intent)`：只返回决定。
- `open(intent)`：为每次调用分配递增的请求编号，执行决定，返回 `{requestId, decision, instanceId, value}`。宿主函数抛出的错误原样传给调用方。

服务不取消或合并并发的请求，也不保证相同的 `new-tab` 意图只执行一次。宿主在写入共享选择或页面地址前要确认目标仍然有效；布局按实际提交的顺序决定最终位置。模块还直接公开 `normalizeIntent` 和 `resolve`，供不需要宿主的调用方使用。

## 打开方式菜单接口

`PortalEditorOpenCommands.create({owner, onCommand, label, additionalCommands})` 在 `owner` 元素中创建菜单（`owner` 和 `onCommand` 必填），返回：

- `open({event, trigger, context, focus})`：按触发按钮或鼠标位置打开菜单，`context` 在执行命令时原样交回。
- `close()`：关闭菜单。
- `destroy()`：释放浮层并移除菜单。
- `element`：菜单的 DOM 元素。

固定的两项是 `open-new-tab`（新标签）和 `open-side`（右侧分栏），点击后调用 `onCommand(命令, context)`，由宿主提交对应的打开意图。`additionalCommands(context)` 可以按当前上下文追加带 `run` 函数的菜单项，Society Workbench 用它加入“打开对话”、“Family 状态”和“Menu”。菜单本身不创建面板。

## 恢复、关闭与失败

- **重新载入时，Workbench 只恢复允许恢复的实例描述、打开目标和位置。** 编辑区自己的局部状态由它的面板包恢复。
- 进入或刷新 Society 页面时，URL 明确指定的 Resident/Lineage 优先于保存的默认 Chat/HATG 绑定；在构造实例可能发起读取之前就使用该目标，状态恢复也不得覆盖它。明确 Resident URL 显示主 Chat 并以它为当前打开目标；其他有效并排实例保留自己的绑定。没有明确对象 URL 时才恢复已保存的活动 Editor 与页面上次选择。
- **恢复后仍要重新读取和检查权限。** 资源已删除、当前用户无权读取或类型不再支持时，这个实例显示一个可以关闭的错误页，其他标签照常工作。
- **多个对话同时可见，不等于它们都成为当前打开目标。** 只有 Workbench 当前目标编辑区的显示回调可以改变页面的当前选择；其他对话保留各自的目标，不会因为显示顺序或请求完成顺序被改指向别的 Resident。
- **Lineage 图切换到指定 Lineage 失败时，清空旧图并显示加载错误。** 图实例丢弃旧目标的图和订阅，保留请求的目标，不把另一个已有的图当作这个目标显示；晚到的旧请求按实例的目标修订号丢弃。
- **关闭有未保存内容的编辑区时，由它自己询问是否放弃。** Workbench 等待实例的 `canClose()` 结果；实例通过自己的文档会话确认未保存内容。用户取消后，布局、标签顺序和可见文档都不变。确认关闭或销毁后，编辑区取消读取，释放编辑器模型、选区监听和临时 URL；晚到的响应不能再把内容挂回来。
- **页面地址只保存可以分享的资源目标。** 实例 ID、分栏、活动标签和未保存草稿属于当前工作台和编辑区。
- **打开完成后，宿主可以更新 `documentTarget`。** `inspectionTarget` 和 `conversationTarget` 是独立的共享选择通道，不随之改变。

### 删除当前打开的 Lineage

侧栏和 Lineage 图设置中的删除操作交给同一个删除流程。含有 Personal AMA Resident 的 Lineage 不能删除，页面提示“Personal AMA 的 Leader Resident 不能删除。”。用户确认后，流程先暂停这个 Lineage 的图、对话和工作区读取，丢弃已发出请求的迟到结果，再执行 `DeleteLineage`。

- 成功后，从页面地址中去掉这个 Lineage、其中 Resident 和组件的链接，选择一个仍存在的目标，再恢复加载。
- 失败时恢复原来的目标。

删除过程中收到的通知和刷新，也按这个目标切换过程处理。

### 删除当前打开的 Resident

普通 Resident、派生出的 Resident 和对话房间使用同一个删除流程。Personal AMA Resident 不显示删除操作。确认框说明删除的后果；被删除的是 Lineage 的 Leader 时，确认框另外说明“删除后系统从其余 Resident 中随机选出新的 Leader”。执行 `DeleteResident` 之前，流程关闭它的对话，撤销当前对话目标、浮层菜单和页面地址中的 Resident 链接，并暂停它的身份、导航、会话控制和工作区读取。

- 成功后清理目录、对话房间关联、最近会话和本地选择。删除派生 Resident 时，Chat 回到它的父 Resident；其他情况下 Chat 显示“该 Resident 已删除。”，不改为打开 Leader 或其他 Resident。
- 失败时恢复原来的页面地址和对话目标。

各个删除入口只提交删除意图，服务调用和界面清理由共享流程完成。

## 扩展

新增编辑区类型时，通过面板包和“资源类型对应哪种编辑区”的匹配规则接入，链接处理代码不增加分支。新增打开位置时，扩展打开意图和 Workbench 的放置方式。资源保存、版本冲突和内容显示仍由 Workspace Surface 和 Preview 负责。

### 实现来源与入口定位

打开意图、复用与新实例位置由 `portal_editor_open_service.js`、`portal_editor_open_commands.js` 及工作台接入实现。`portal_society_service.js` 与 `portal_resident_service.js` 提供领域读取和指令客户端，供具体编辑区加载目标、导航或执行操作；它们不实现打开方式本身，也不把打开成功解释为读取或权限检查成功。上述客户端保留为关联入口说明，具体打开合同的实现来源列对应编辑服务与工作台文件。
