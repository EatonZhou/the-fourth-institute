---
kb_id: decision_a144bf700017
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 11
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T19:43:45Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_865c3c24f251
importance: 8
metaness: 1
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/static/portal_command_palette.js
  - ${code_root}/management_portal/static/portal_command_registry.js
  - ${code_root}/management_portal/static/portal_navigation.js
  - ${code_root}/management_portal/serve_management_portal.py
  related:
  - decision_741b04821171
  - decision_8dc6b3d03ca7
  - decision_4a9ba5dfada6
  - decision_b3022c27d22f
  supersedes: []
  derived_from: []
---
# Portal 全局资源搜索

Portal 全局资源搜索是每个业务页面都可以使用的资源入口。用户点击顶栏的 Search 按钮，或按 `Ctrl/⌘ K`，会打开居中的搜索对话框。结果只包含当前用户有权发现的 TFI Portal 资源，不包含页面命令。

资源索引和访问控制由 ResourceService 负责；搜索框负责输入、类别选择、结果浏览和导航。Portal 命令注册表（Command Registry，decision_741b04821171）只为这个入口登记全局快捷键，不把其他命令加入搜索结果。

## 搜索中的对象

| 对象 | 用户看到什么，系统怎样理解 |
|---|---|
| Search 启动按钮 | 顶栏账号区域旁的入口；顶栏缺少宿主时，面板仍可由程序打开。 |
| 搜索面板 | 覆盖在当前页面上的对话框，包含查询输入框、资源类别选择和结果列表。对话框的层级、Escape、背景点击和焦点由共享弹层规则处理（decision_b3022c27d22f）。 |
| 查询 | 用户在输入框中的文字；非空时按资源 ID 或标题匹配，不把摘要作为匹配字段。 |
| 资源类别 | 搜索框中的下拉选择，默认“全部类型”；选项来自资源索引状态的 `resource_types`。 |
| 结果列表 | 查询下方可用方向键或指针浏览的条目集合。当前选择具有高亮和 Enter 提示。 |
| 资源结果 | 一个统一资源的类型、标题、规范地址、摘要和目标 URL。统一资源使用稳定的 `<类型>_<12位小写十六进制码>` ID。 |
| 当前选择 | 方向键、鼠标移入或首次打开确定的一个结果。Enter 只激活它。 |
| 规范解析 | 导航前让服务端把资源地址换算成它现在的页面地址，因为资源移动或改名后旧地址可能已失效。 |
| 资源索引 | 服务端为可发现资源建立的查询数据。搜索结果先经过当前用户权限过滤。 |
| 查询缓存 | 浏览器进程内按“资源类别 + 查询文字”保存最近 20 组结果。缓存只用于立即恢复界面，身份变化会整体清空。 |

## 设计点

### 打开、关闭和浏览

- **Search 按钮和 `Ctrl/⌘ K` 从页面任何位置打开面板。** 光标在输入框中时快捷键同样有效，且优先于页面上的其他快捷键。
- **打开后光标在输入框中，选中第一项。** 打开时保留上次的查询和类别，先显示已有的缓存结果，不立即搜索资源；面板已打开时再次打开不会重复搜索。
- **按 Escape 或点击背景关闭面板。** 关闭时保留查询文字和缓存结果，清除选中项，取消等待中和进行中的搜索。再次打开时恢复上次的查询。
- **查询输入框中的方向键和结果区指针控制同一个选中项。** 输入框获得焦点且不处于输入法组合时，上下方向键在第一项和最后一项处停止；鼠标移到某项上即选中它，点击时输入框保持焦点。类别选择器及其他控件保留自身的键盘行为。
- **查询输入框中的 Enter 只激活当前结果。** 输入法组合期间不执行；没有结果时不执行残留动作。

### 查询资源

- **非空查询只匹配资源 ID 和标题。** 匹配不区分大小写；摘要可以显示在副标题，但不参与搜索。空查询不请求结果，也不显示命令。
- **资源类别默认是“全部类型”。** 打开面板时从索引状态取得可选类别；选择具体类别后，服务端只在该类别中筛选候选，再排序并验证访问权限。类别切换会立即重新查询，空查询时只更新选择。
- **输入停止 120 毫秒后才搜索资源。** 每次最多返回 30 项；同一“类别 + 查询”有缓存时先显示缓存，搜索完成后更新。输入变化会取消旧请求，连续打字时中间的查询不会执行。
- **没有结果或搜索失败时，面板保持打开。** 搜索中且没有缓存结果时显示 `Loading…`；空查询显示 `Search Portal objects by ID or name` 引导，非空查询没有结果时显示 `No results.`；请求失败显示 `Search unavailable. Please try again.`。

### 激活结果

- **打开资源前，先确认它的当前地址。** Portal 旧资源入口和直接地址会向服务端解析当前 URL；外部 URL 直接导航。
- **地址解析失败时，仍尝试打开原链接。** 404 表示地址无法解析，其他错误也不会完全阻断已有 URL；面板回退资源结果自带的目标。
- **一次资源激活期间忽略重复点击。** 导航成功后由页面卸载结束状态；没有可用目标时恢复可激活状态。

### 权限范围

- **只显示当前用户有权发现的资源。** 服务端对建议结果验证当前主体的访问权限，精确解析再次执行资源访问控制；浏览器面板不扩张权限。
- **身份或权限更新使旧搜索结果立即失效。** 面板取消防抖与进行中请求，清空全部查询缓存、结果和选择位置。面板打开且查询非空时重新读取当前身份可见的资源；关闭或空查询时不发搜索请求。旧请求即使无法取消，也不能重新填入列表。
- **资源搜索按当前登录用户检查权限。** 服务端读取资源时按 ResourceService 的访问控制列表判断，浏览器只提交查询。

## 白盒机制：一次查询和一次激活

| 组件 | 源码名称 | 职责 |
|---|---|---|
| 全局搜索面板 | `PortalCommandPalette` | 输入、类别、缓存、请求取消、结果选择和导航编排；旧源码名称不表示结果中仍有命令。 |
| 命令注册表 | `PortalCommandRegistry` | 只登记打开搜索的 `Ctrl+K` / `Meta+K` 快捷键。详见 decision_741b04821171。 |
| 共享 Dialog | `PortalUI` / `PortalOverlay` | 面板焦点、Escape 和背景关闭。详见 decision_b3022c27d22f。 |
| ResourceService | 服务端资源层 | 类别清单、索引候选、协作描述投影、访问控制和规范地址解析。 |

```text
打开面板 → 读取索引状态的资源类别
输入或类别变化
  → 取消旧防抖与旧 AbortController
  → 立即恢复同类别、同查询的缓存；空查询到此结束
  → 非空查询请求精确地址或索引建议结果
  → request serial 确认响应仍属当前请求
  → 更新最多 20 组运行时缓存
  → 用户激活资源；必要时解析当前规范 URL → 导航
```

输入变化经过 120 毫秒防抖，类别变化立即请求。搜索同时使用浏览器取消与请求序号：取消尽快停止网络工作，序号防住无法及时取消的迟到响应。只有当前序号可以写入结果或结束 loading。

## 精确查询与导航协议

### 宿主和关闭状态

- DOM 就绪后自动创建搜索面板。Search 按钮优先插入 `.portalUser`，其次 `.portalNavTop`；两者缺失时只保留可编程入口。
- Dialog 初始焦点为 `portalCommandInput`，允许 Escape 和背景关闭。
- 关闭会递增请求序号、中止当前 AbortController、清除 120 毫秒计时器并重置 selected；query、resourceType 和运行时缓存保留。
- 外部宿主通过公开的 `close()` 与 `isOpen()` 关闭或读取状态，复用同一取消和清理流程；接口定义见下文“公开接口”。

### 资源查询

- 打开面板时调用 `GET /api/resource-index/status`，用 `resource_types` 构成类别选项；空值表示“全部类型”。成功加载后复用选项；读取失败保留默认选项，后续打开可重试。
- 非空文本查询使用 `GET /api/resource-index/suggest?q=<query>&limit=30`，选择具体类别时再带 `resource_type`，并设置 `cache: no-store`。打开面板和空查询不调用建议接口；浏览器缓存只承担即时回显。
- 客户端把结果规范为 `resource_type`、`resource_id`、`address`、`title`、`summary`、`url`；缺少 ID 或 URL 的结果被丢弃。
- address 与 summary 组成资源结果副标题；summary 不参加匹配。
- 输入或类别变化中止前一请求并增加 `requestSerial`。序号不匹配的响应不能更新结果；只有当前序号在 finally 结束 loading。缓存键同时包含类别和查询，不能把“全部类型”的结果复用成单一类别结果。
- `PortalIdentity` 的变更通知使用同一取消和请求序号机制，不由宿主页面分别处理。用户名相同的权限更新同样失效，资源可发现性始终以当前服务端响应为准。

建议结果由 ResourceService 按以下顺序产生：

1. 先在索引快照中按可选资源类别、ID 和标题筛选并排序候选；摘要和其他 `search_terms` 不参加此入口的匹配。
2. 对候选批次读取需要的当前协作描述和 ACL，按当前 actor 的访问规则过滤；不可见项不占用返回名额，必要时继续检查后续候选，直至达到 limit 或候选耗尽。
3. 只返回已授权资源的摘要和用于排序的 score。选择具体类别时只等待该类别所属 Adapter 就绪，无关 Adapter 故障不阻塞查询。

浏览器缓存不代替服务端授权；身份变更后所有缓存失效。一般资源查询的其他筛选、投影和分页合同由 decision_8dc6b3d03ca7 说明，不与这个即时建议接口混为一谈。

### 精确地址和规范导航

- 精确地址正则为 `^[a-z][a-z0-9]*(?:_[a-z0-9]+)*_[0-9a-f]{12}(?:/[^\s/]+)*$`。根地址要求小写资源类型与 12 位小写十六进制 ID，后面可追加不含空白和斜杠的非空路径段。
- 查询文字本身精确匹配该格式时，面板会尝试精确引用；选择了其他资源类别时不显示该结果。引用未找到返回空结果，其他请求失败显示搜索不可用。
- 目标 URL 是 `/r/`、是带 `address` 查询参数的 `/resource`、`/resource/`、`/resource.html`，或同源路径正好等于完整 address 或 root ID 时，需要规范解析。
- `GET /api/resources/reference?address=<address>` 返回规范地址信息和导航 URL。空 address 返回 400；未找到返回 404；权限不足返回 403。
- 外部 origin 不做规范查询。规范查询没有得到目标时回退原 URL。
- `navigating` 防止重复激活。成功调用 `location.assign` 后由页面卸载结束；请求失败时仍用回退目标调用 assign。
- 规范地址解析还记录发起时的身份代次。身份更新后，较早解析的成功和失败结果都不再导航，也不改写新导航的忙碌状态；新身份可以重新选择目标。搜索取消与身份失效由全局搜索面板统一执行，宿主不维护另一套导航取消规则。

### 快捷键与索引刷新

- 根 scope 登记 `portal.command-palette.open`，快捷键为 `Ctrl+K`、`Meta+K`，priority 1,000，`allowInInput: true`。根作用域使它在捕获阶段先于页面及 Monaco 等编辑器接管按键；priority 只参与全局命令之间的排序。它只负责打开搜索；搜索结果不读取 Registry 的命令列表。统一的快捷键优先级与输入法保护见 decision_741b04821171。

- `PortalCommandPalette.reload()` 先 `POST /api/resource-index/refresh`，请求 JSON 为 `{wait: true, timeout: 60}`，客户端 `timeoutMs` 为 65,000 毫秒。
- 服务端接受 `adapters`、`path`、`wait` 和 `timeout`，搜索面板只使用上述等待参数；当前路由成功返回 HTTP 202。
- 刷新结束后只在当前 query 非空时重新搜索资源。空查询只完成刷新，不执行全量查询。

## 公开接口

### `PortalCommandPalette.open()`

打开 Dialog、聚焦查询输入、把选择置于第一项，并立即显示同类别、同查询的缓存。该操作读取类别状态，但不启动资源建议查询；重复调用保持幂等的单一面板实例。

### `PortalCommandPalette.reload()`

等待资源索引刷新；当前 query 非空时再重跑资源查询，否则只完成刷新。

### `PortalCommandPalette.close()` 与 `isOpen()`

关闭面板并取消待发的资源查询；`isOpen()` 返回面板当前是否打开。Live 演示用它们记录并恢复演示前的面板状态（decision_f2a6a5dd4646），组件本身不带演示代码。

### 服务端接口

- `GET /api/resource-index/status`：返回索引状态与 `resource_types` 类别选项。
- `GET /api/resource-index/suggest`：使用 q、limit 和可选 resource_type，返回当前用户可发现的 ID／标题匹配结果。
- `GET /api/resources/reference`：按当前用户解析一个精确资源地址并返回规范导航信息。
- `POST /api/resource-index/refresh`：触发资源索引刷新；搜索面板请求等待完成。
