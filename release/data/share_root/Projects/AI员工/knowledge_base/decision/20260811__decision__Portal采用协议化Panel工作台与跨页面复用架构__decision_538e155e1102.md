---
kb_id: decision_538e155e1102
type: decision
project: AI员工
created_at: '2026-08-11'
version:
  number: 15
  created_at: '2026-08-11'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-20T00:00:39Z'
  updated_by:
    user: ''
    agent: resident_6b7d6cdae31c
importance: 9
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/static/portal_workbench.js
  - ${code_root}/management_portal/static/portal_workbench_context.js
  - ${code_root}/management_portal/static/portal_command_registry.js
  - ${code_root}/management_portal/static/portal_navigation.js
  related:
  - decision_79de71d9890d
  - decision_4348ed990621
  - decision_741b04821171
  - decision_9f6135cbbb22
  supersedes: []
  derived_from: []
---
# Portal Workbench 与 Panel Package 组合协议

Portal 的复杂页面用 **Workbench（工作台）** 把聊天、导航、预览、终端和各种编辑器排成可以拖动、隐藏、折叠和恢复的面板。一种工具要放进 Workbench，先要登记为一个 **Panel Package（面板包）**：它说明“这种面板怎样创建和管理”，相当于一份插件合同，不包含任何具体 Resident、文档或会话的数据。

本文面向编写共享面板和组装页面的开发者，逐项规定：面板包怎样登记，面板实例怎样启动和退出，面板怎样读写工作台的共享选择，命令怎样统一执行，以及面板的局部状态怎样保存和恢复。Workbench 的可见布局、运行组件和恢复架构见 decision_4348ed990621，用户界面状态的保存位置见 decision_9f6135cbbb22；聊天、预览、导航各自的行为见它们的 Design。

## 先认识四个参与者

- **页面宿主**：创建一个 Workbench，决定初始面板、默认布局、存储位置和初始的共享选择。
- **Panel Package**：一种面板类型的合同，声明它的标题、图标、最小尺寸、能力、关心的共享选择、命令、状态保存格式和创建函数。
- **面板实例**：某种面板在某个 Workbench 中的一次具名使用。实例有自己的 ID、自己的 DOM 根节点和可选的资源绑定。
- **面板运行对象（runtime）**：创建函数为一个实例返回的对象。它接收生命周期通知，维护面板内部的交互，并在结束时释放资源。

Workbench 交给面板的 **运行上下文（runtime context）** 是面板读写共享选择、查找同一工作台中其他面板和执行命令的入口。保存面板状态时，Workbench 把状态装进一个 **持久化封套**，记录它来自哪种面板、按哪个格式版本编码。

## 登记 Panel Package

调用 `PortalWorkbench.registerPanelPackage(package)` 时，一次提供完整合同。缺少任何必填项立即失败，不会只登记一部分。

| 字段 | 含义与约束 |
|---|---|
| `type` | 必填，稳定、全局唯一的面板类型（也可以写作 `id`） |
| `version` | 正整数，面板包实现的版本 |
| `presentation.title`、`presentation.icon` | 用户看到的标题和图标，都必填 |
| `sizing.minWidth`、`sizing.minHeight` | 正数；登记后最小宽度至少 120，最小高度至少 100 |
| `instances` | `single`（一个工作台中只有一个）或 `multiple` |
| `capabilities` | 字符串数组，声明面板的能力。其中 `snapshot` 表示面板会保存局部状态，`restore` 表示面板能恢复局部状态 |
| `behavior` | 必填对象，声明能否隐藏、折叠、移动、关闭和是否锁定位置 |
| `context.channels` | 字符串数组，声明面板关心的共享选择通道；`*` 表示全部 |
| `commands` | 可选命令数组；每项必须有非空 `id` 和可调用的 `execute` |
| `persistence.schemaVersion` | 正整数，局部状态的编码版本 |
| `create` | 为每个实例创建运行对象的函数 |
| `component` | 可选，面板对应的组件名称 |

规则：

- `persistence` 只能包含 `schemaVersion`、`serialize` 和 `restore`，出现其他字段时整个包登记失败。
- 能力和通道去掉重复项。任一命令无效时整个包登记失败；命令可以声明 `capability`，它必须出现在包的 `capabilities` 中。
- `behavior` 中 `canHide`、`canCollapse`、`canMove` 默认开启，只有显式写 `false` 才关闭；`closable` 和 `locked` 只有显式写 `true` 才开启。Workbench 按这份合同生成操作入口并限制布局修改；实例描述只能在此基础上进一步收紧，不能放宽。
- 登记成功后返回冻结的合同对象，外观、尺寸、能力、通道、命令和保存设置都变为只读。
- 同一 `type` 不能登记第二次。面板包登记后在整个页面生命周期内有效，没有注销方法；可能重复加载的模块先用 `PortalWorkbench.registeredPanelPackages()` 检查是否已登记。

## 创建 Workbench 和实例

`PortalWorkbench.create(options)` 用稳定的 `id` 和根元素 `root` 创建工作台；同 ID 的旧工作台先被销毁。常用选项：

| 选项 | 作用 |
|---|---|
| `initialEditors` | 初始面板实例的描述列表 |
| `defaultLayout`、`presets`、`defaultPreset` | 默认布局和可选的预设布局 |
| `store` | 布局存储；不提供时按当前登录用户保存（未登录时只保存在内存中） |
| `context`、`contextStore` | 初始的共享选择，或页面自己提供的共享选择存储 |
| `onPanelEvent` | 接收面板发出的事件 |
| `restorePanel`、`createEditorDescriptor`、`resolveEditorResource`、`editorTypes` | 重新载入时重建动态实例、按资源打开新实例 |
| `label`、`toolbarPlacement`、`toolbarHost`、`toolbarRight`、`showSingleTabHeader`、`customizeLabel`、`fitViewport`、`insets`、`mobilePrimaryPanelId` | 工具栏、标签头和视口适配 |

Workbench 本身只保存布局、活动标签、隐藏、折叠和位置，不保存会话正文、资源权限或文件内容。

**实例描述**给出实例的 `type`、`id` 和可选项：`title`、`icon`、`options`（交给创建函数）、`binding`（资源绑定）、`required`、`defaultHidden`、`mobilePrimary`、`minWidth`、`minHeight`、`inset`（`none`、`compact` 或 `comfortable`）、`closable`、`locked`、`canHide`、`canCollapse`、`canMove`、`singleton`、`reusable`、`openSource`、`restoreOnReload`、`serialize`、`openResource`、`onShow`、`onHide`。实例描述不能提供 `create`，创建函数只来自登记的面板包。

Workbench 为每个实例调用一次创建函数：

```text
create({
  ...options,            // 实例描述中的 options
  root, panelId, workbenchId,
  context,               // 这个面板的运行上下文
  panelPackage,          // 冻结的合同
  commands,              // id(命令名)、execute(命令名, 调用参数)
  emit,                  // emit(事件名, 内容)
})
```

`root` 是实例专用的 DOM 根节点；创建函数可以返回运行对象，也可以返回一个元素。创建失败时，Workbench 移除根节点并抛出错误。实例元素带有 `role="region"` 和以标题命名的 `aria-label`，屏幕阅读器可以按标题找到面板。

布局改变时，Workbench 把每个实例的根节点移到它所在的面板框中；不在当前布局中的实例放在工作台的停放区。移动使用浏览器的原子移动（`moveBefore`），保留节点状态；只有浏览器不支持时，普通 DOM 面板改用保留输入值、焦点和选区的兼容移动，含自定义元素、Shadow DOM、嵌入文档或媒体的面板拒绝普通移动。移动后检查每个根节点确实直属于自己的面板框，失败时回退到上一份完整布局。事务细节见 decision_4348ed990621。

## 运行对象的生命周期

```text
create
  ↓
connect → restore（有可恢复状态时）→ setContext → show
                                              ↕
                                           hide / show
                                              ↓
                                  hide → disconnect → destroy
```

| 方法 | 何时调用、做什么 |
|---|---|
| `connect(context)` | 实例启动时调用一次；建立共享选择、命令和外部适配器的关系 |
| `restore(value, context)` | 包声明了 `restore` 能力、且存在格式版本完全一致的保存状态时调用；只收到包已经检查过的值 |
| `setContext(values, context, change)` | 启动时调用一次；之后在面板关心的通道真正变化时调用。`values` 只包含面板关心的通道 |
| `show(context)` | 实例进入可见集合时调用 |
| `hide(context)` | 实例离开可见集合时调用 |
| `snapshot(context)` | 包声明了 `snapshot` 能力时，保存布局前调用；返回可恢复的局部状态，返回 `undefined` 表示不保存 |
| `canClose()` | 用户关闭可关闭的实例前调用；可返回布尔值或 Promise，由实例确认未保存内容，`false` 或拒绝表示保留实例 |
| `disconnect(context)` | 实例退出时断开外部关系 |
| `destroy(context)` | 最后释放监听、请求、模型和 DOM 资源 |

所有方法都是可选的。关闭一个可关闭的面板或销毁 Workbench 时，仍可见的实例先收到 `hide`，随后全部待销毁实例收到 `disconnect`、`destroy`。用户关闭实例前等待可选的 `canClose()` 通过；取消或失败时原布局与内容保持。任一方法抛出错误时，Workbench 记录错误并继续，其他面板和面板包登记不受影响；面板应在自己的区域显示可以理解的错误。Workbench 不调用 `resize` 和 `focus` 之类的其他方法：面板需要尺寸时自己观察根节点；工作台布局与尺寸变化后还会向面板根节点发送 `portal:workbench-resize` 事件。“聚焦”命令只切换活动标签。

在调用 `show` 和 `hide` 之前，Workbench 先提交新的可见集合；回调中同步触发的聚焦、显示或隐藏只会看到已提交的状态，同一次真实的可见性变化不会重复通知。

## 运行上下文

运行上下文只在当前 Workbench 和当前实例中有效，读取范围限于面板包声明的通道。

| 成员 | 作用 |
|---|---|
| `workbenchId` | 当前 Workbench 的稳定 ID |
| `context` | 只读，当前共享选择中面板关心的那些通道的值 |
| `contextStore` | 高级用法：带修订号的共享选择存储，读取同样限于面板关心的通道 |
| `getSelection(channel)` | 读取一个通道的当前值；不在面板关心范围内的通道返回 `undefined` |
| `setContext(channel, value, meta)` | 写入任意非空通道 |
| `select(channel, value, meta)` | 只写标准选择通道 |
| `subscribe(channels, listener, options)` | 订阅一个通道、通道数组或 `*`（只包含面板关心的通道），返回取消函数；实例销毁时自动取消 |
| `emit(sourcePanelId, event, detail)` | 发布面板事件 |
| `getPanel(panelId)` | 取得同一 Workbench 中的实例 |
| `invoke(panelId, method, ...args)` | 调用同一 Workbench 中另一个实例运行对象的公开方法 |
| `commandScope` | 当前 Workbench 的命令作用域 |

标准选择通道有五个：`inspectionTarget`（正在查看的对象）、`conversationTarget`（当前对话）、`documentTarget`（当前文档）、`graphSelection`（Lineage 图中的选择）和 `commandContext`（命令附加信息）。向 `select` 传其他名称会抛错；`setContext` 可以写其他非空通道，例如导航视图 `navigationView`。订阅只在命中的通道值真正改变时通知；`immediate: true` 先发送一次当前值，不增加修订号。

面板用创建函数中的 `emit(event, detail)` 发布事件，Workbench 在根元素上发出 `portal:workbench-panel-event`，并交给页面的 `onPanelEvent`。以下事件名由 Workbench 直接转成共享选择：

| 事件 | 效果 |
|---|---|
| `inspect`、`inspection-change` | 选择 `inspectionTarget` |
| `conversation-change` | 选择 `conversationTarget` |
| `document-change` | 选择 `documentTarget` |
| `graph-selection` | 选择 `graphSelection` |
| `command-context` | 选择 `commandContext` |
| `view-select` | 写入 `navigationView` |
| `open-resource` | 交给统一打开服务打开资源，成功后选择 `documentTarget`（decision_79de71d9890d） |

选择值依次取 `detail.value`、`detail.target`、`detail.selection`，都没有时取 `detail` 本身。

运行上下文不取消面板的请求，也不判断异步结果是否过时。面板切换目标或销毁时，自己用 `AbortSignal`、请求 ID 或目标修订号丢弃迟到的结果。

## 命令

**Command Registry（命令注册表）**让工具栏、菜单和快捷键执行同一组命令。全局资源搜索只使用它登记打开入口的快捷键，不展示工作台命令。每个 Workbench 有一个命令作用域，ID 为 `workbench.<工作台 ID>`；作用域 ID 重复时创建失败，同一作用域内命令 ID 重复时登记失败。登记、注销、上下文和忙碌状态变化都会在 `document` 上发出 `portal:commands-change`；销毁作用域时一次移除其中全部命令。

Workbench 自动登记这些命令：

- `workbench.reset-layout`：恢复默认布局。
- 每个面板实例：`panel.<实例 ID>.toggle-visibility`（显示或隐藏）、`panel.<实例 ID>.toggle-collapsed`（折叠或展开）、`panel.<实例 ID>.focus`（聚焦）；可关闭的实例还有 `panel.<实例 ID>.close`。
- 面板包声明的命令，登记为 `panel.<实例 ID>.<命令 ID>`。执行时命令收到调用参数，以及面板关心的通道值 `context`、实例的运行对象 `panel`、`panelId` 和工作台控制器 `workbench`。面板可以用创建函数收到的 `commands.execute(命令 ID)` 执行自己的命令。

命令登记时的默认值：标题取 ID，分类为 `General`，没有快捷键，`allowInInput` 与 `allowConcurrent` 为 `false`，优先级为 0。`visible` 和 `enabled` 可以是布尔值，也可以是根据作用域当前上下文计算的函数。

- `list` 不返回不可见的命令，按优先级从高到低、同优先级按登记顺序排列。
- 不允许并发的命令执行期间标为忙碌、暂时不可用，工具栏和菜单看到同样的状态。
- 执行未登记的命令 ID 抛出错误；不可见、不可用或忙碌中的命令返回 `false`。命令在 Promise 完成或失败前保持忙碌。
- Portal 根作用域的全局快捷键先于工作台、页面和编辑器处理；工作台命令的数字优先级不能覆盖全局命令。共享注册表统一处理捕获、输入范围、输入法保护和单次执行，精确规则见 decision_741b04821171。工作台命令只接收未被消费的事件；默认不在输入元素中触发，需要时显式声明 `allowInInput: true`。

## 保存与恢复局部状态

Workbench 在页面离开（`pagehide`）和布局变化时保存状态。对每个声明了 `snapshot` 能力、且 `snapshot` 返回了值的实例，写入：

```text
{
  packageType,     // 面板包类型
  packageVersion,  // 保存时的面板包实现版本
  schemaVersion,   // value 的编码版本
  value            // 经过可选的 persistence.serialize 处理后的状态
}
```

封套不授予任何资源的读取或写入权限。

恢复时，Workbench 依次检查：面板包声明了 `restore` 能力 → 封套的 `packageType` 与当前实例的面板包一致 → `schemaVersion` 与当前面板包完全相等 → 可选的 `persistence.restore(value, meta)` 返回了值 → 调用运行对象的 `restore`。任何一步不满足时只放弃这个实例的局部状态，其他面板和布局照常恢复。Workbench 不做格式迁移；需要保留旧状态时，由独立工具在加载前转换。

动态实例描述和局部状态是两层数据。只有 `restoreOnReload` 的实例会记下类型、ID、标题、图标、资源绑定和锁定状态（位置另存）；重新载入时 Workbench 先重建这些实例，再恢复它们的局部状态。缺了其中一层时，不从另一层推测内容。

## 接入步骤

1. 在页面的一个固定模块中登记面板包（已登记则跳过）。
2. 页面创建 Workbench，提供默认布局、初始实例、共享选择和存储。
3. 创建函数在 `root` 中建立界面，返回这个实例专用的运行对象。
4. 运行对象在 `connect` 中建立关系，在 `setContext` 中读取目标，在 `show` 中开始可见时才需要的工作，在 `hide` 中暂停这些工作。
5. 运行对象用 `snapshot` 只保存可以恢复的局部状态；正文和权限通过领域接口重新读取。
6. 运行对象在 `disconnect` 和 `destroy` 中释放订阅、进行中的请求、临时 URL 和 DOM。

新增一种面板就新增一个面板包；改布局改 Workbench；改变局部状态的编码就提高该面板包的 `schemaVersion` 并更新保存和恢复函数；改变聊天、预览或导航的行为就修改对应的运行对象。这样一处机制的变化不会牵动无关的面板。

## 样式边界

Workbench 的边框、背景、内边距、单个标签时是否显示标签头，以及分隔条外观，通过共享组件公开的 `--portal-workbench-*` CSS 变量配置。页面只在 Workbench 宿主元素上设置这些变量，不选择或覆盖 `.portalWorkbench*` 内部结构。页面需要调整共享详情面板中的内容时，给自己的元素加上功能类名并只选择这些类，不覆盖 `.portalDetail*` 内部类。


### 绑定恢复状态事件

面板包通过创建参数 `emit` 报告当前绑定的恢复状态：直接读取所绑定业务对象确认 404 时发 `binding-unavailable`，后续当前绑定读取成功发 `binding-ready`。Workbench 仅据此控制该实例是否写入动态实例描述与状态封套，不删除面板、业务数据或独立草稿。面板负责校验目标修订，过时请求及辅助请求的错误不得发出此事件；403、网络错误、限流与 5xx 不视为永久失效。页面可处理同一事件取消指向已失效对象的本页回退选择，不影响其他有效对象。
