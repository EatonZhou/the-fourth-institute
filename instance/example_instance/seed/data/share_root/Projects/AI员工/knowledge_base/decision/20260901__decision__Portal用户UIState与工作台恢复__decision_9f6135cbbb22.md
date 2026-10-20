---
kb_id: decision_9f6135cbbb22
type: decision
project: AI员工
created_at: '2026-09-01'
version:
  number: 9
  created_at: '2026-09-01'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T19:42:46Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_865c3c24f251
importance: 8
metaness: 2
ai_generated: true
links:
  source:
  - ${code_root}/management_portal/static/portal_state.js
  - ${code_root}/management_portal/static/portal_page_state.js
  - ${code_root}/management_portal/static/portal_live_tour.js
  related:
  - decision_00546e28e1e0
  - decision_741b04821171
  - decision_6e230393221c
  - decision_538e155e1102
  - decision_79de71d9890d
  - decision_06defc47e960
  supersedes: []
  derived_from: []
---
# Portal Page State 的页面偏好保存与恢复

TFI Portal 的一个页面通常包含搜索框、筛选器、可折叠区域、列表或卡片、当前选择、详情面板和可调整布局。用户离开再回来时，希望这些显示方式保持熟悉。Portal Page State 是浏览器中的页面偏好层，负责保存这类操作位置，并在同一浏览器再次打开页面时恢复。

对用户来说，它减少重复设置页面的工作。对页面开发者来说，它提供安全的浏览器存储、默认值合并、防抖写入、页面退出提交和临时演示隔离。服务端任务状态、资源权限和业务对象由对应服务和工作台保存。

## 先认识页面中的对象

| 概念 | 含义 |
|---|---|
| 页面偏好 | 只改变当前页面怎样显示或停留在哪里的值，例如搜索词、筛选条件、折叠状态、布局比例、最近选择和未提交的界面操作。 |
| 页面标识 | 区分不同页面偏好空间的名称；源码参数名为 `page` 或 `pageId`。 |
| 用户标识 | 在同一浏览器内区分不同登录用户的名称。没有用户时使用 `anonymous`。 |
| 快照 | 页面在某一时刻希望保存的完整偏好对象。 |
| 补丁 | 只包含本次变化字段的普通对象；写入时浅层合并到现有状态。 |
| 默认值 | 页面首次打开或没有有效存储时使用的字段。已保存字段覆盖同名默认值。 |
| 防抖写入 | 连续变化先在内存中合并，短暂等待后只落盘一次，避免拖动或连续输入频繁访问存储。 |
| 临时页面事务 | 只在内存中生效的一段页面变化。演示结束时整体回滚，不污染用户原有偏好；源码称为 ephemeral transaction。 |
| 接口历史 | 当前 Portal 地址和上一个不同地址的浏览器记录，用于返回最近接口。这里的“接口”指用户打开的 Portal 页面地址。 |
| `localStorage` | 浏览器提供的长期本地键值存储，关闭标签页后通常仍保留。 |
| `sessionStorage` | 浏览器提供的标签页会话存储，生命周期比 `localStorage` 短。 |

## 设计点

### 页面恢复自己的显示方式

- **页面在首次渲染前读取偏好。** 页面先定义默认筛选、折叠、选择、布局比例和草稿位置，再叠加同一浏览器中已经保存的字段；依赖这些值的列表、卡片和详情随后渲染。
- **不同用户和页面互不覆盖。** 最终存储键同时包含用户标识和页面标识。账号变化后，新读写进入新的用户空间。
- **损坏或受限的浏览器存储不会阻断页面。** 空文本、无效 JSON、`null`、数组或其他非普通对象状态按空对象处理；浏览器拒绝访问、写入或删除时返回安全结果。
- **页面卸载会提交尚未落盘的变化。** 标签页进入 `pagehide`，或页面主动销毁状态实例时，防抖队列立即提交。
- **销毁后的实例只允许安全读取。** 销毁之后的写入、替换和快照保存都不进入浏览器存储，已经卸载的页面因此改变不了偏好。

### 连续操作不会互相覆盖

- **同一页面实例的连续补丁会合并。** 防抖期间后来的字段与前面的字段组成一次浅层补丁；相同字段以后来的值为准。
- **立即写入先处理待写内容。** 普通写入、完整替换或快照写入开始前，包装器先提交已有防抖补丁，避免较早定时任务随后覆盖新状态。
- **用户或临时事务变化会切断批次。** 如果待写补丁属于另一个用户或另一个事务，包装器先提交旧批次，再建立新批次。

### 演示可以改变真实页面但不留下偏好

- **一个浏览器文档同一时刻只有一个临时事务。** 同一个事务标识重复开始会复用；另一个事务仍活动时启动新事务会报错。
- **事务中的读写只改变内存层。** 页面读取时临时字段覆盖持久字段，写入和替换不会进入浏览器长期存储。
- **回滚会丢弃整个匹配事务。** 标识不匹配时返回失败且不改变状态。Portal 的实时演示用它隔离搜索、筛选、视图和选择变化。

### 用户可以返回上一个不同页面

- **Portal 自动记录当前地址和上一个不同地址。** 地址包含路径、查询参数和井号片段；重复访问同一地址只更新时间和标题。
- **返回动作只在目标有效时导航。** 上一个地址不存在或等于当前地址时不动作。
- **返回快捷键优先于页面和编辑器，在存在目标时执行。** `Ctrl/Meta+Tab` 与 `Ctrl/Meta+反引号` 登记为根作用域的 `portal.interface.previous`，允许在编辑区和输入框触发，统一遵循全局命令优先级和输入法保护（decision_741b04821171）。没有 Shift、没有 Alt；没有可返回目标时保留浏览器默认行为。浏览器自身截留的按键无法传入页面。

## 实现：状态由谁负责

### 两层组件

| 组件 | 源码名称 | 职责 |
|---|---|---|
| 浏览器状态底层 | `PortalState` | 统一文本和 JSON 存储、页面状态键、浅合并与替换、临时事务、集合转换和接口历史。 |
| 页面生命周期包装器 | `PortalPageState` | 为一个页面组合默认值、动态用户、防抖补丁、快照、`pagehide` 提交和销毁。 |

页面第一次加载时，包装器解析当前用户与页面标识，要求底层读取状态，然后将页面默认值与持久值组成快照。用户操作产生补丁时，包装器先在内存合并；计时结束、立即写入、用户变化、事务变化、`pagehide` 或实例销毁都会触发提交。底层根据当前临时事务决定写入内存层还是 `localStorage`。

这一分层让底层拥有一致的损坏恢复和事务语义，页面只拥有自己有哪些字段以及何时把字段应用到界面。Portal Refresh Coordinator 负责数据何时重新读取，见 decision_6e230393221c；Page State 只保存影响页面显示的选择，不保存刷新请求或服务端快照。

## 精确存储和恢复协议

### 键、用户和数据形状

- 页面状态使用根前缀 `tfi.portal.pageState.v1`，完整键格式为 `tfi.portal.pageState.v1.<user>.<page>`。底层会把用户和页面组成部分转成字符串并去除首尾空白；空用户回退 `anonymous`，空页面回退 `page`。
- `PortalPageState.create(pageId, options)` 优先使用 `options.user`，其次使用 `options.getUser`。函数型用户来源在每次读写时重新求值。
- 底层默认使用 `localStorage`；只有存储方法收到 `{scope: "session"}` 时改用 `sessionStorage`。空键不读写。
- 页面状态根只接受非空、非数组的普通对象。写入普通对象之外的完整值时保存空对象。

### 读取、补丁和替换

- `PortalState.read(page, user, defaults)` 先复制默认字段，再叠加已保存字段。
- 包装器 `read(extraDefaults)` 的合并顺序是页面默认值、调用时额外默认值、已保存字段。
- `write` 对顶层字段做浅合并；`replace` 完整替换页面对象。底层不解释字段业务含义。
- `writeSnapshot()` 读取页面提供的 `options.snapshot` 普通对象，并以补丁方式保存。
- 浏览器存储的读、写、删除和 JSON 解析异常均被收敛为回退结果。

### 防抖和销毁

- 包装器 `debounceWrite(patch)` 按字段合并待写补丁，`debounceSnapshot()` 先取得当前快照再进入同一队列。默认延迟 160 毫秒。
- 显式延迟优先；当前实现中的数值 0 会按假值处理并回退 `options.delay` 或 160 毫秒。
- `write()`、`replace()` 和 `writeSnapshot()` 先提交待写补丁，再按当前用户和事务作用域执行本次动作。
- `flush()` 立即提交当前补丁。首次 `destroy()` 先 flush，再移除 `pagehide` 监听；重复销毁保持幂等。

### 临时事务

- `beginEphemeral(id)` 要求去除首尾空白后仍非空。同 ID 复用当前事务；不同 ID 与活动事务冲突时抛错。
- 活动事务期间，`read` 先读取事务内对应键的值；不存在时读取持久值。`writeForTransaction` 与 `replaceForTransaction` 只在传入 ID 等于当前事务 ID 时修改内存层。
- 携带非空但不匹配 ID 的写入不生效，并返回持久状态。
- `rollbackEphemeral(id)` 只对匹配事务返回 `true`，并清空该事务的全部内存状态；其他情况返回 `false`。当前协议没有提交临时事务的动作。

### 集合转换和接口历史

- `setToArray(value)` 只接受 JavaScript `Set`；`arrayToSet(value)` 只接受数组。成员转成字符串并过滤空字符串；类型不匹配时分别返回空数组或空 Set。
- 接口历史保存在 `tfi.portal.interfaceHistory.v1`，字段为 `current` 和 `previous`。每项包含 `url`、页面标题 `title` 和毫秒时间 `at`。
- `noteInterfaceVisit()` 记录当前地址，`previousInterfaceUrl()` 返回最近一个不同地址。`goPreviousInterface()` 先询问当前页面的导航器；导航器可以原地返回时执行它，否则使用 `location.assign` 打开历史地址。
- 页面通过 `interfaceHistory.registerNavigator({canGoBack, goBack})` 提供返回能力，一次只允许一个导航器；登记返回的释放函数只移除本次登记。Lineage 用它返回先前 Resident，页面卸载时释放。导航器只处理返回动作，不监听具体组合键。

## 公开接口

### WALL 的页面偏好接入

WALL 以页面标识 `wall` 和当前账号接入 Page State。WALL 拥有字段校验、工程切换和最近打开排序的业务规则，唯一正文见 decision_06defc47e960“页面偏好与最近打开”。共享状态层只提供本地存储与生命周期，不解释这些字段，也不接管 WALL 的后端已读状态和语义搜索任务。

### `PortalState.storage`

提供 `readText`、`writeText`、`remove`、`readJson` 和 `writeJson`。调用方可以用 `scope: "session"` 选择会话存储。

### `PortalState` 页面状态方法

提供 `read`、`write`、`replace`、`writeForTransaction`、`replaceForTransaction`、`setToArray` 和 `arrayToSet`。

### `PortalState` 临时事务与历史方法

公开 `beginEphemeral`、`rollbackEphemeral` 和 `currentTransactionId`。`interfaceHistory` 提供 `keepCurrent`、`visit`、`previousUrl` 和 `registerNavigator`；共享全局命令触发返回行为。只有加载命令注册表的 Portal 页面登记返回快捷键，独立使用状态存储的文档不安装按键监听。

### `PortalPageState.create(pageId, options)`

返回 `read`、`write`、`replace`、`writeSnapshot`、`debounceSnapshot`、`debounceWrite`、`flush`、`destroy`、`setToArray` 和 `arrayToSet`。每个实例只管理一个页面标识。
