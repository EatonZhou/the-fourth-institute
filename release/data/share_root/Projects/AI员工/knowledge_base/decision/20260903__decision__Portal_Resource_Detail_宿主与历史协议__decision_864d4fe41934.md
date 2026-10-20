---
kb_id: decision_864d4fe41934
type: decision
project: AI员工
created_at: '2026-09-03'
version:
  number: 10
  created_at: '2026-09-03'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T01:51:04Z'
  updated_by:
    user: ''
    agent: resident_60ae6d0cdf74
importance: 9
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/static/portal_resource_detail.js
  - ${code_root}/management_portal/static/portal_detail_surface.js
  - ${code_root}/management_portal/static/portal_detail_workbench.js
  - ${code_root}/management_portal/static/portal_resource_header.js
  related:
  - decision_769a35d6b5c7
  - decision_61e50306cc79
  - decision_538e155e1102
  - decision_9f6135cbbb22
  - decision_741b04821171
  supersedes: []
  derived_from: []
---
# Portal Resource Detail 宿主与历史协议

Portal 的很多页面先显示一组卡片或列表，例如知识条目、Repo、实验和待办；用户点开其中一项后，在当前页面上方弹出这一项资源的详情。Resource Detail（资源详情宿主）是这些页面共用的一套机制：它根据资源决定打开哪个详情页、让浏览器地址随选择变化，使后退、前进和关闭都能回到正确的位置，并在关闭或切换前保护未保存的修改。详情内容可以直接放在当前页面中，也可以放在一个同源的 iframe 里，由另一个 Portal 页面显示。

本篇说明资源怎样对应到详情地址、浏览器历史怎样管理、两种宿主怎样打开和关闭详情，以及 iframe 内外的消息协议。资源的内容、权限和保存由各领域的服务和 ResourceService 负责；详情中的文档标签页、草稿和预览由文档工作区负责（decision_769a35d6b5c7）。

## 基本概念

| 名称 | 含义 |
| --- | --- |
| 集合页 | 打开详情之前的列表、卡片或搜索结果页面。 |
| 详情区域 | 弹出层中显示资源标题栏和内容的区域，内容处于加载中、空白、错误或就绪之一。 |
| 资源标题栏（Resource Header） | 显示资源标题、类型、身份和关闭按钮。 |
| 资源绑定 | 规范化后的三个值：`resourceId`（资源 ID）、`resourceType`（资源类型）和 `detailKind`（使用哪种详情）。它说明正在打开的是哪一项资源、用哪个详情页显示。 |
| 入口适配器 | 为一种资源类型生成详情页地址的规则。 |
| 页内宿主（Local Host） | 在当前页面的弹出层中读取并显示详情。 |
| 框架宿主（Frame Host）与嵌入页（Guest） | 父页面的框架宿主管理弹出层和浏览器历史，iframe 中的 Portal 页面作为嵌入页显示领域详情。 |
| 放弃确认 | 关闭或切换前检查是否有未保存的修改，可以是异步的。 |
| 详情历史 | 宿主自己添加的浏览器历史记录。宿主只管理这部分，页面的其他历史不受影响。 |

## 设计点

- **连续打开不同资源时，每次选择都形成一条浏览器历史。** 用户可以用后退和前进在打开过的资源之间切换。历史中只保存资源地址和宿主标记，不保存文档内容。
- **关闭详情只撤销宿主自己添加的历史。** 用户从其他页面进入时留下的历史保持不变。
- **有未保存的修改时，关闭和切换都先确认。** 切换资源、按 Escape、点击背景遮罩或点击关闭按钮，都经过同一个放弃确认；用户选择留下时，当前文档和地址保持不变。
- **只显示最后选择的资源。** 较早发出的慢请求晚到时被丢弃。当前资源读取失败时，错误只显示在详情区域中，背后的列表或卡片不变。
- **iframe 中的详情 15 秒内没有报告就绪时显示错误。** 错误可以关闭，也可以重试。
- **嵌入页报告资源被修改或删除后，集合页可以更新列表。** 报告修改时集合页可以刷新这张卡片，报告删除时可以移除这一项；普通关闭只关闭详情。

## 资源与详情地址

`normalizeResource(input, fallbackKind)` 把资源整理成资源绑定。输入可以是字符串或对象：`resourceId` 依次取 `resourceId`、`resource_id`、`id`；`resourceType` 缺失时从统一资源 ID 的前缀推出（例如 `decision_…` 推出 `decision`）；`detailKind` 默认等于资源类型。缺少可用的 ID 或类型时立即报错。

`registerAdapter(type, adapter)` 为一种类型登记入口适配器，适配器提供 `location()`（自己生成地址）或 `path`（详情页路径）。同一类型再次登记时替换旧的登记；注销只移除仍属于自己的那次登记，旧的注销调用不会误删之后登记的适配器。

`adapterFor(resource)` 先按 `detailKind` 查找适配器，再按 `resourceType` 查找。`locationFor(resource, options)` 生成详情地址：有 `location()` 时调用它；否则在 `path` 上把资源 ID 写入参数（默认参数名 `item`）。嵌入 iframe 时默认加上 `embedded=1`，适配器可以改变这两个参数名；本次打开的请求 ID 和调用方给出的其他查询参数也写入地址。没有适配器时抛出错误，由页面决定改用通用资源入口还是显示错误。

Portal 从服务端发布的 `PortalResourceRoutes.routes` 读取 `path`、`parameter`、`detail_kind` 和 `embed_parameter`，为支持嵌入的资源登记默认适配器。嵌入页同样从该契约读取资源与嵌入参数，知识类型由 `detail_kind: "knowledge"` 归组。当前入口如下：

| 类型 | 详情页 | 资源参数 | 嵌入参数 |
| --- | --- | --- | --- |
| `knowledge` | `/resource/knowledge` | `item` | `embedded` |
| `skill` | `/resource/skill` | `item` | `embedded` |
| `article` | `/resource/article` | `item` | `embedded` |
| `experiment` | `/resource/experiment` | `item` | `embedded` |
| `repo` | `/resource/repo` | `item` | `embedded` |
| `device` | `/resource/device` | `item` | `embedded` |
| `review`（知识条目） | `/resource/knowledge` | `item` | `embedded` |
| `todo` | `/resource/knowledge` | `item` | `embedded` |

## 详情历史

`createDetailHistory(options)` 创建一段详情历史，默认用 `item` 参数记录资源。宿主用当前页面路径和参数名标识自己，在浏览器历史的状态中写入 `portalResourceDetail`（宿主标记）和 `portalResourceDetailDepth`（第几层详情），同时保留页面原有的状态、查询参数和 hash。

- **打开与切换**：第一次打开详情时新增一条历史，之后每切换一次资源再新增一条，层数加一。后退和前进时，宿主从地址恢复对应的资源。
- **关闭**：只有一层时，关闭退回到集合页那一条历史；有多层时，关闭一次跨过宿主添加的全部层数。当前地址中没有本宿主的标记时，关闭用替换地址的方式清除资源参数。
- **只记录地址和选中的资源。** 页面状态（decision_9f6135cbbb22）和文档内容不写入这些记录。

`acceptCurrent()` 记下宿主已经接受的位置：宿主完成一次由后退或前进引起的资源切换后确认这个位置；直接调用 `open`、`push` 或 `replace` 成功时自动记下。放弃确认被拒绝或超时时，`restoreAccepted()` 回到最后接受的位置：能确认属于同一段详情历史时，按层数反方向移动，相邻记录的后退和前进仍然可用；无法确认时只恢复当前这一条，不猜测跳到外部历史。宿主不能用无条件的 `forward()` 代替它。

## 页内宿主

`createLocalHost(options)` 在收到弹出层元素后创建页内宿主，提供 `open`、`close`、`destroy`、`snapshot`，以及标记“资源已修改”“资源已删除”的方法。

- **切换资源前等待放弃确认。** 确认返回 `false` 时保持当前文档和地址。确认只针对发起时的那次打开：等待期间又发生了打开、关闭或销毁，旧的确认不能关闭新的资源或恢复旧的选择；同时有多个等待确认的打开时，只接受最新的一个。确认出错时保留当前详情。
- **新的打开作废旧的读取。** 每次打开都有新的序号和取消信号，并中止上一次可以取消的读取；过期的读取即使晚到，也不会显示。成功后状态中保存内容和这次打开的上下文，失败只在这个宿主中显示错误。
- **关闭后焦点回到打开前的位置。** `destroy` 中止读取并释放对话框，隐藏的宿主不会再显示旧结果或占住键盘焦点。

## 框架宿主与嵌入页的消息协议

`createFrameHost(options)` 创建框架宿主，要求 iframe 位于弹出层内部，并且与详情区域不是同一个元素。打开后先显示加载中，默认等待嵌入页报告就绪 15 秒（可以用 `readyTimeout` 修改）。嵌入页用 `createGuest(options)` 创建自己的一端。

两端只交换类型为 `portal:resource-detail.v1` 的消息。框架宿主只接受同源、来自当前 iframe、请求 ID 和资源 ID 都匹配的消息。权限不通过消息传递，嵌入页的请求继续使用同一个 Portal 登录会话。

| 嵌入页发出 | 框架宿主的反应 |
| --- | --- |
| `ready` | 从加载中变为就绪。 |
| `changed` | 标记当前资源已修改。 |
| `deleted` | 标记当前资源已修改并已删除。 |
| `failed` | 显示错误。 |

- **关闭和切换都先询问嵌入页。** 嵌入页就绪后，框架宿主发出 `request-close`，默认等待 2500 毫秒（可以用 `closeTimeout` 修改）。嵌入页按自己的放弃确认回复 `close`（允许）或 `close-blocked`（留下）。等待期间原资源继续显示，切换只记住最新的目标；收到允许后才加载目标并更新地址，被拒绝或超时时丢弃这个目标。已经就绪的同一资源再次打开时保持当前页面，不重新加载，也不丢弃草稿。尚未就绪、处于错误状态或资源已经删除时，可以直接关闭。
- **关闭请求的条件保持不变。** 框架宿主保留发起关闭时的上下文，嵌入页的回复不会丢掉 `updateLocation: false` 这类控制历史的条件。嵌入页的异步确认只针对当前资源和请求 ID，资源变化或销毁后失效；对同一目标重复关闭时共用一次确认，确认出错按拒绝关闭处理。
- **嵌入页可以更新当前资源。** 嵌入页调用 `setResource` 更新自己当前的资源绑定；初始化参数只提供初始值，之后的通知不会用初始值覆盖当前资源，以函数形式给出的参数则继续读取领域状态。
- **报告失败时显示错误并交还焦点。** 收到 `failed` 时，框架宿主从就绪或加载中变为错误，清除就绪标记和关闭计时器，把仍停在 iframe 中的焦点交给父页面的错误区域。读取失败和权限拒绝各自保留原来的错误文字。
- **关闭和销毁后不再响应旧的 iframe。** 框架宿主关闭后把 iframe 地址设为 `about:blank`；`destroy` 隐藏详情，移除消息监听和全部计时器，之后的打开、旧的读取结果和旧的关闭确认都不能让它重新工作，重复销毁没有作用。嵌入页销毁后不再响应消息。旧的嵌入页因为窗口、请求 ID 和资源 ID 三项都不匹配，不能影响之后的打开。
- **浏览器恢复了旧的 iframe 时重新发送当前请求。** 浏览器的历史恢复可能让 iframe 回到旧的页面、取消新的导航。框架宿主只认它已经接受的那次打开：旧页面加载后，框架宿主重新发送一次当前请求，不接受旧请求 ID 的就绪，也不重新开始超时计时。关闭和销毁时撤销这项恢复。

## 接入方式

集合页先为资源类型登记入口适配器，再选择页内宿主或框架宿主。用户打开资源时，页面把资源绑定交给宿主；宿主负责地址、焦点、取消过期请求和关闭协商。页内宿主的读取函数或嵌入页再通过领域接口读取内容。

各部分的分工：

| 部分 | 负责 |
| --- | --- |
| 资源详情宿主 | 宿主状态、消息协议、错误显示和资源释放。 |
| 页面 | 选择资源，提供领域的读取函数、渲染函数和容器。 |
| 资源标题栏、详情区域、详情工作台（`portal_detail_workbench.js`） | 显示身份，提供加载中、空白、错误、就绪四种状态的结构，组装详情中的面板。 |
| 文档工作区 | 文档读取、草稿、保存、标签页和工具栏（decision_769a35d6b5c7）。 |
| 预览渲染器 | 内容显示（decision_61e50306cc79）。 |
| 领域服务 | 数据和用户权限。 |

同一资源在集合页、嵌入页和独立页面中，按相同的资源绑定、权限、状态和可用空间采用同一套行为。使用 iframe 时不再额外套一层详情区域，嵌入页自己的边框和布局负责显示，窄屏和宽度变化由共享的页面结构处理。宿主不根据“iframe 已就绪”或“内容已显示”推断用户能否修改资源，能否修改始终由领域服务给出。
