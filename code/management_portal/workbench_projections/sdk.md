# Portal Projection SDK v2（兼容 v1）

## Resident 本身份的原生浏览器接入

Resident 可以用自己的 Portal Bearer 与 `X-Portal-Lineage-ID` 打开 `/workbenches`、设置预览和运行页。页面、工作台 API、原生 Chat 的投影读取、宿主身份与偏好、静态依赖和 `/api/events` 均使用当前认证主体；服务验证 Resident 当前属于声明的 Lineage，随后由工作台和目标资源继续检查权限。不得把工作台的创建者或绑定门面当作调用者。`/api/me` 返回 `principal: {kind: "resident", id: "resident_…"}`，`username` 保留同一个 ID 供共享 UI 使用，不创建人类账号或个人 AMA。

自动浏览器可以复用源码中的 `scripts/resident_browser.cjs`。在自己的运行环境中提供 Playwright、本站实际 Web origin 和自己的稳定身份：

```js
const { chromium } = require("playwright");
const { openResidentPortal } = require("<Portal code root>/scripts/resident_browser.cjs");
const portal = await openResidentPortal({
  chromium, origin: "<Portal origin>",
  residentId: "<当前 Resident ID>", lineageId: "<当前 Lineage ID>",
});
try {
  await portal.page.goto("<Portal origin>/workbenches?panel=settings&id=<workbench ID>");
  // 使用真实预览、Activity Rail、Chat 和 iframe 内的 Projection SDK。
  // 部署或断线恢复后，先 await portal.verifyIdentity() 再继续。
} finally { await portal.close(); }
```

启动器从当前 Portal runtime 的凭据引用读取本人的令牌，核对注入身份，建立无登录 Cookie 的独立浏览器，并在导航前校验 `/api/me`。令牌只留在启动器进程与发给指定 Portal 的请求头中，不进入页面脚本、URL、storageState 或产物。使用直接原生 URL：启动器拒绝重定向响应和其他源的请求，其余响应按原样转发；原生通知流直接连接同一 Portal。不要导出请求头、HAR、trace 或凭据内容。用户登录 Cookie 的优先级和同源写入检查保持原有合同；账号安全页面仍要求人类会话。

草稿用设置页预览，已发布版本用 `/workbenches/view?id=…&region=…`。接入成功只证明当前 Resident 的访问结果；业务数据、模型结果、产品发布和其他身份的验收仍分别核验。

## 工作台定义与多功能区

Portal 开发投影使用 `development_app` 中的应用工程。先提交源码，再在仓库运行以下命令生成固定提交的三区工作台定义：

```sh
python3 -m management_portal.workbench_projections.development_projection --commit <完整40位Git提交SHA>
```

`--commit` 必填；加 `--example` 可只生成 D3 与 Worker 平台示例。命令仅输出应用包定义，不保存或发布。输出包含 `application`、`manifest`、空 `panels` 与空 `html`；sidebar 的 source 使用 `package:architecture`、`package:progress`、`package:evolution`，三区共同发布和订阅 focus。通过 HATG `UpdateResource` 保存定义，宿主构建指定提交，成功后再独立提交 `published_revision` 发布。草稿不改变在线页面；生成和反投影动作也支持 manifest/panels/application。

工作台支持 1–32 个功能区。数据库中已有的 v1 单 `html` 工作台和 v2 `manifest`/`panels` 片段由宿主继续兼容；片段 source 必须与 panels 的相对 HTML 路径对应，清单与片段源码合计最多 400 KB，遵守下文片段模板约束。Portal 开发投影工程已移除旧片段源码和生成分支，新的定义均通过上述应用包入口生成。

宿主以一个 Panel Package 实例承载每个功能区，切换保留 iframe、筛选和草稿；布局由 Workbench 按用户保存。`?region=<sidebar.id>` 打开指定功能区。Activity Rail 沿用公共排序。chat 可选；提供时 bindings 必须将 facade 映射到真实 Resident。宿主使用原生 Chat Workspace，发送前复核绑定，成员移出后报告 `projection.resident_unbound`。

功能区可声明自己的 `sidebar[].chat: {binding, role: "facade"}`，连接另一个已绑定团队；未声明的区域继续使用页面 chat。宿主装配独立原生对话面板，`chat-` 前缀保留给这些面板。`await Projection.openChat()` 打开当前分栏对应的对话。分栏对话发送时重新检查绑定，并附带该用户通过 `Projection.state()` 保存的当前分栏状态，因此应只保存本区域需要的清单和请求回执，不保存秘密。分栏反馈同样发给对应门面。绑定失效时明确报错，不退回主门面。

打开已发布页面时，宿主同时读取工作台定义和校验门面绑定；门面校验通过后立即并行加载原生 Resident 投影与会话，不等待工作台详情或应用资产完成。装配 Chat 继续使用原生数据仓库合并中的请求及已加载快照，不另建投影专用聊天缓存。装配前核对已授权目标与本次定义的绑定；并发发布导致目标不同则显示绑定变化错误，不挂载错误的会话。无 Chat 的页面正常装配；读取失败或离开页面会释放预取订阅，晚到结果不能重新装配页面。

功能区异步装配、尺寸重绘、隐藏恢复和读取保存状态时，必须保持用户当前输入焦点与选区。保存的图节点焦点只代表阅读状态，不授权调用 DOM `focus()`；重绘前应同时核对本 iframe 文档具有焦点且活动元素属于待替换的图节点，满足时才恢复该节点。用户主动点击、键盘导航和关闭详情返回仍按当前操作管理焦点。`Projection.focus()` 是语义对象情境，与键盘输入焦点分别管理。

## 应用包工程与不可变产物

工作台的 `application` 登记 `{repo_id, directory, commit, entries, max_bytes?}`：repo_id 为已登记 Git 仓库，directory 是仓库内相对目录，commit 为完整提交 SHA；entries 将入口名映射到 dist 内 HTML 文件。例如 `{architecture: "architecture.html"}` 对应清单 source `package:architecture`。纯应用包的 panels 为 `{}`、html 为空，也可以与片段功能区组合。

工程包含 package.json 与 `build` 脚本，不要求提交锁文件。保存修订时，服务以当前调用者读取固定提交的 Git blob，在 Bubblewrap 隔离环境中解析当前 npm 登记镜像版本。解析和安装可以联网，但禁用生命周期脚本；实际应用构建在无网络隔离环境中执行。依赖许可、路径、体积和产物均检查；失败不保存新修订，也不改变在线页面。宿主生成 `application_build`，记录提交、产物内容哈希、入口、文件摘要及实际安装依赖的版本、许可和完整性；调用方不能提交此字段。修订与构建回执同事务保存，发布前再次检查不可变产物。

构建器每次从 package.json 重新解析依赖，并在临时目录生成仅供本次构建校验的完整性记录；源码里的旧锁文件会被忽略。依赖只接受 `registry.npmjs.org` 的 HTTPS 包及 SHA-512 完整性，不支持 workspaces、本地或 Git 依赖。解析和安装使用构建专用缓存，不能读取宿主 npm 配置或凭据；源码构建只能只读访问该缓存且不能联网。依赖缓存合计上限 256 MiB；安装和构建各限时 120 秒。

每个功能区默认 8 MiB（`max_bytes` 可调整，最高 64 MiB），按入口 HTML 引用资产去重计量；独立 Worker 等未直接引用资产保守计入每个入口。应用全部产物最多 64 MiB，源码最多 32 MiB、4096 个文件。不可变产物默认保存在 `${staging_root}/shared/projection_applications`，可由 `PORTAL_PROJECTION_ARTIFACT_ROOT` 配置；每次读取复核内容哈希、尺寸和路径。

宿主从同源、当前用户鉴权的修订资产接口读取 HTML/JS/CSS，然后把产物交给无同源权限的 sandbox iframe 执行。宿主不执行应用代码。应用 iframe 外只有一个可信空白容器，以父层 `frame-src 'none'` 阻止应用自行导航发出网络请求；代码只进入内层 sandbox，SDK 连接真正的 Portal 宿主。首版脚本及 Worker 必须自包含；多文件源码可任意组织，构建时 bundle，运行时分块 import 暂不支持。内层 CSP 禁止 URL 脚本、eval、网络请求、表单提交与嵌套文档；样式和主题由宿主注入。Web Worker 只运行经本修订资产校验取得的脚本，Canvas/WebGL 可以正常使用。

片段与应用包均支持浏览器原生全屏：在用户点击处理器内调用 `element.requestFullscreen()`，用 `document.exitFullscreen()` 或浏览器退出操作返回；监听 `fullscreenchange` 更新按钮和画布尺寸。应用应先检查 `document.fullscreenEnabled` 并处理请求拒绝。宿主在 iframe 链上委派全屏权限，进入与退出保留原 iframe、Worker 和草稿；不改变工作台布局、sandbox、资源鉴权或网络限制。全屏需要浏览器支持与即时用户操作，不新增 SDK 消息或自动进入行为。

HTML 可以引用本修订的独立 script 和 stylesheet。CSS 必须自包含：构建产物不能保留 `@import`（包括 data import）、本地或外部 `url()`，仅允许 `data:` 和 `#fragment`。应在构建时展开样式依赖并内联图片；模板为常用图片与 woff2 配置 data URL loader，但当前 CSP 未开放 font-src/media-src，字体仍使用系统字体，音视频没有正式加载入口。Worker 路径从产物根目录解析。图形动画、截图、媒体与导航的具体设计边界见 [工程 README](development_app/README.md#产品体验设计的公共能力)。

- `await Projection.createWorker("layout.worker.js")`：创建本修订的独立 Worker，返回标准 Worker 对象；使用 postMessage 通信，用 terminate 释放，页面退出时也会清理。Worker 没有 Projection SDK 或用户凭据。
- `await Projection.libraries()`：读取宿主登记库清单；`await Projection.loadLibrary(name)` 将登记的 UMD 入口加载到当前 sandbox，返回库元数据。应用也可以把固定 npm 依赖打包进入口。
- `Projection.icon(name)`：从宿主公共图标目录创建 SVG 节点，未知名称返回 null。图标和基础控件共用 Portal 的主题变量。

登记库为 D3 7.9.0、dagre 1.1.5、Cytoscape 3.33.1、marked 18.1.1、DOMPurify 3.4.17 和 portal-graph 1.0.0。清单保留每个文件的 SHA-256、体积、类型、来源与许可证；读取时重新核对字节。`portal-graph` 加载为 `PortalGraphGeometry`，Lineage 同时使用它进行视图坐标变换、贝塞尔采样与命中计算。各库的 API 和更新步骤见 `static/projection_libraries/README.md`。

Portal 开发投影模板在 `management_portal/workbench_projections/development_app`，区域 mount、共享权限读取、Markdown 渲染和生命周期合同见该目录 README。构建器由 application_build.py 负责，修订存储由 Store/tables 负责，资产鉴权由 application_http.py 负责，浏览器隔离与消息通道由 host/SDK 负责；这些机制由 `decision_7a5ae487e2e9` 约束。

## 区域工程接入

架构区独占 `development_app/src/architecture` 与本区测试；入口 `index.js` 导出 `mount(root, context)`，可异步返回 `{show, hide, destroy}`。共享入口负责启动，区域不自行调用 start，也不导出单独 unmount；卸载落在 destroy。隐藏保留实例，show/hide 首次按实际可见性调用；pagehide 时 signal 中止，清理订阅和 Worker，异步 mount 晚到的运行对象也会销毁。`context` 包含 `region`、`sdk`、`signal`、`resources`、`onDispose`、`onFocus` 与 `watch`；具体参数与共享实现见工程 README。

资源访问通过 `context.resources` 或 `sdk.resource`，不传 actor。`resources.readable(items, getRef)` 只返回当前用户可读条目，统计和连线在过滤后建立；不可见前置仍按未知处理，不能因移除它而判定依赖已满足。`onFocus(fn)` 先异步补当前快照，再接收通知；用回调的 revision 排除晚到的详情读取。`sdk.context()` 也可读取当前 focus，subscribers 只限制推送。`watch` 当前每个区域保留一组 refs，新调用替换旧订阅，应汇总需要观察的地址；共享生命周期自动取消订阅。`onDispose` 保留异步清理的 Promise，在途重复调用共用同一结果；成功只清理一次，失败报告 `Region cleanup failed` 并允许重试。hide/destroy 可以不等待；需要确认结果的调用方仍可 await 并得到原始失败，不会被共享层吞掉。

`sdk.resource('read', {address, format?})` 经宿主读取单份完整正文，固定上限为 **16 MiB**，页面参数不能提高此预算；它独立于应用包产物大小。宿主把预算交给 ResourceService，仍按当前用户重新鉴权并返回完整正文的 `metadata.content_sha256`。超限明确失败，不截断、不改读 current 地址；父应用继续按固定描述符核对字节数与哈希。`format:'yaml'` 使用相同预算与原始字节哈希。ResourceService 的通用默认上限保持 4 MiB。

批量入口复用相同 SDK 和 HTTP 通道，一批对应一次 HTTP，不要求升级应用宿主或另开网络能力：

```js
await sdk.resource('resolve_many', { addresses });
await sdk.resource('read_many', { addresses, format: 'text' }); // 或 yaml
// POST /api/workbenches/resource
// {id: workbenchId, operation: 'read_many', arguments: {addresses, format: 'text'}}
```

两种批次均为 1–128 个完整地址（每个最多 4096 字符）；按输入顺序返回 `results`，重复地址也独立核验，不折叠成根资源。成功项为 `{address, ok:true, result}`，其中 `result` 与对应单条操作相同；失败项为 `{address, ok:false, status, error:{code,error,details,...}}`，保留资源服务的错误码与 HTTP 状态，其他条目继续。批量 resolve 的不存在/无权限是逐项失败；单条 resolve 仍返回 `readable:false`。resolve 的成功结果只有可读性、类型、标题和链接，无正文或内部解析元数据。请求结构/身份覆盖无效、工作台不可访问时整批 HTTP 失败。

`read_many` 串行读取，累计原始正文最多 16 MiB；每次将剩余预算传给 ResourceService，再检查返回字节，不截断。已读正文即使 YAML 解析失败也计入 `total_bytes`；返回另含 `max_total_bytes`。某项超过剩余预算保留底层超限错误，后续较小项仍可读取；预算用尽的后续项明确报 `projection.resource_budget`/413，且不发读取。应用按固定描述符分组、减小批次重试失败项，并逐份校验完整字节数/SHA；一批不是事务快照，不得把 HTTP200 当作全部条目成功。未知批量参数不能改变条目数、字节预算或 actor。

工作台访问在请求开始及批次交付前重新检查；每个地址的 resolve/read 继续由 ResourceService 按当前 actor 对完整子路径鉴权。read 自带解析鉴权，不需先 resolve，先前 resolve 或根权限不能代替它。平台不缓存授权或正文，不提供跨批一致性；调用方负责有限并发、同批证据复用与生命周期代次。取消/隐藏后调用方停止新批并丢弃旧结果，已经发出的 HTTP/RPC 可能完成，不能把丢弃结果宣称为服务端取消。

布局可直接 `import dagre from '@dagrejs/dagre'`、`import * as d3 from 'd3'` 或使用 Cytoscape，依赖由构建打包。`await sdk.loadLibrary('portal-graph')` 后使用 `globalThis.PortalGraphGeometry`，仅提供坐标、连线与命中几何，不包含 PortalHatgGraph 页面控制器。Worker 可以导入已锁定的 npm 依赖，不能使用宿主 SDK 或主线程登记库全局。每区在入口旁添加 `layout.worker.js` 即自动构建，例如 `src/architecture/layout.worker.js` 输出 `architecture.worker.js`，通过 `await sdk.createWorker('architecture.worker.js')` 创建；用 `onDispose(() => worker.terminate())` 释放，并在异步创建后检查 signal。平台示例保留 `layout.worker.js` 产物名。区域无需修改 entries、package 或 build 配置。

真实历史文件沿统一资源地址读取：`repo_<id>/commit/<完整40位小写SHA>/file/<仓库相对路径>`。例如 `resources.read('repo_5d287fed9c7e/commit/' + commit + '/file/architecture/modules.yaml', 'yaml')`；服务读取该提交的 Git blob，当前用户权限仍逐次检查。当前工作区文件用 `repo_<id>/file/<路径>`，不能作为历史快照。仓库类型目前没有提交列表或目录枚举动作；时间滑块的候选 SHA 应来自可读的真实提交/发布记录，或数据维护者提供的可读索引。缺少候选时显示“缺少提交记录”，不从时间戳推算提交。文件影响分析也须使用实际文件地址，不能把目录或 glob 当成已读文件。

架构区图布局使用独立的 `src/architecture/graph-layout.worker.js`，存在时打包为 `architecture-layout.worker.js`，区域通过 `sdk.createWorker('architecture-layout.worker.js')` 创建。消息合同由区域维护（当前输入为 `{requestId, nodes, edges}`）；平台只负责隔离构建和加载。上述通用 `layout.worker.js` 约定继续可用，两个产物不互相覆盖。

共享 `resources.read` 直接请求正文，读取自身完成当前 actor 的解析与鉴权，不以预先 resolve 为必要步骤。若读取时撤权或删除，底层 read 抛出的 `permission_denied`、`file_not_found`、`resource_not_found` 统一返回 `{readable:false, ref, data:null}`。区域收到此结果应清除该对象的旧正文、观察与计数。其他服务、无效参数及版本错误继续抛出，不能改写为不可读。此约定只在共享 helper 收口，底层 `sdk.resource('read')` 保留原始错误合同。

## 情境、反馈和变化

对象格式为 `{ref, kind, status, source, observed_at}`。status 为 current/proposed/simulated/verified；source 是依据的资源地址或空字符串，observed_at 是带时区 ISO 时间。ref 使用统一资源地址，源码定位可加 `#L123`。宿主检查对象与依据的读取权限，确认框按 Portal 界面语言显示状态，发送的对话上下文不随界面语言转换，无依据显示“缺少依据”；同一页面同 ref 的矛盾状态报 `projection.status_conflict`。

- `await Projection.focus(object)`：发布 focus，仅清单 publishers 可调用，返回 `{object, revision}`。宿主在异步对象鉴权前为本页请求排序；被后续合法发布者请求取代或来自已销毁区域的结果以 `projection.cancelled` 拒绝，不写入对象登记、不递增 revision 或推送。后续请求鉴权失败时保留最后已提交的 focus，不恢复先前仍在途的选择。该请求排序与 SDK 收到通知后的 revision 过滤分别负责提交前和读取后的乱序。
- `await Projection.context()`：清单中的任何功能区都可通过结果的 `focus` 读取通道当前 `{object, revision}`，无选择时 object 为 null；不受 subscribers 限制。
- `Projection.onFocus((object, {revision}) => …)`：返回取消函数。任何清单功能区都立即读取当前情境；只有声明的 subscribers 接收后续变更推送。异步读详情时记录 revision，丢弃旧结果。
- `await Projection.openPanel(panelId)`：宿主视图导航扩展，只切换到本页清单 sidebar 中已有的功能区，保留实例和当前情境，同步活动轨与已发布页的 region 深链接。成功返回 true；布局固定等 Workbench 约束阻止切换时返回 false。非字符串、未知 ID、facade 和 v1 页面均返回 `projection.panel_unknown`。可先 `focus(module)` 再 `openPanel("architecture")`，不执行资源动作或增加业务权限。
- `await Projection.openPage(url)`：通过 PortalNav 打开服务端显式登记的只读页面入口。接受根相对或当前 Portal 同源 HTTP(S) 绝对 URL（最多 4096 UTF-16 码元），拒绝外源、协议相对、用户凭据、空白/控制字符、反斜杠、编码/越级路径，以及未登记的 API、下载、登出等路径。服务端从 `RouteDefinition(page=True)` 的精确 GET 文档路由生成目录，投影不复制页面白名单，目标页面继续自身鉴权。返回 `{opened, mode: "local" | "page", url}`；page 只表示宿主当前标签已发起导航，不能视为目标已加载或有权限，原文档退出后 Promise 可能不可继续观察。当前 v2 工作台的 `/workbenches/view?id=<当前ID>&region=<区域ID或facade>` 由已装配 Workbench 原地处理，保留 iframe、focus 和草稿；省略 region 与显式 region=facade 等价，聚焦已有原生门面对话；未装配 Chat 则报错，不空转成功。当前工作台只接受唯一 id、至多一个 region，无其他查询/锚点。未知/未装配面板抛 `projection.page_target`；布局拒绝返回 opened:false，URL 不变且不刷新兜底。地址或路径拒绝为 `projection.page_url`，目录加载失败为 `projection.page_unavailable`。其他目标页面可携带其自身解释的查询/锚点。本接口不发送反馈、不执行 HATG 或资源动作，不扩大 sandbox 权限。
- `await Projection.feedback({ref, anchor, scene, text})`：先 focus 注册该对象，宿主确认后经原生 Chat Workspace 发给 facade。对象状态、依据、锚点和情境作为 context_reference 附加。取消时 Promise 以 `projection.cancelled` 拒绝。模拟对象不能发送反馈或调用真实 HATG 指令。
- `feedback` 可附 `subject` 作为本次确认的讨论对象名称：非空字符串，最多 240 个 UTF-16 码元。它只用于展示，不进入语义五字段、不改变 ref/权限/发送内容；省略时宿主尝试读取资源标题，仍无法取得则显示真实 ref。宿主通过当前用户权限解析真实 facade 绑定的收件人名称，页面不能指定收件团队。反馈沿用同一个原生确认：主体显示对象名称、状态、收件团队和完整发送文字，宿主标签、状态说明与按钮遵循 PortalI18n 当前语言，名称及发送文字保留原文；对象五字段、收件身份、anchor/scene 默认折叠，可展开核对。JSON 情境仅在详情中格式化展示，发送仍保留原始编码。全部名称和用户文字作为纯文本呈现，确认按钮为“确认发送”，取消、Esc、冻结/隐藏/切换对象等既有取消规则不变。
- `feedback` 的 `anchor`、`scene` 可省略或为 null（均视为空字符串），也可传字符串或 JSON 对象/数组。字符串逐字保留，不做 trim、解析或再次 JSON 编码；对象/数组完整编码为 JSON 字符串后写入 `context_reference.context_text` 对应字段。嵌套值限字符串、布尔值、有限数字、null、普通对象和连续数组；循环、undefined、BigInt、非有限数字、稀疏数组及 Date/Map/Set 等非 JSON 类型均拒绝，不静默丢字段或改成 null。按最终字段字符串的 UTF-16 码元计量：anchor 最多 2048，scene 最多 8000；恰好上限允许，超限不截断。宿主在确认前校验，类型/编码/长度错误以 `projection.feedback_context` 拒绝且不发送。无法通过 postMessage 结构化克隆的值会由浏览器拒绝，同样不会发送。应用可保留草稿、缩减内容后重新显式确认；既有演化区 JSON 字符串无需改动。
- `await Projection.cancelFeedback()`：使本区域当前反馈的等待确认和发送前检查失效，返回 `{cancelled: boolean}`；没有待处理反馈时为 false。区域因来源版本变化、冻结或本地选择变化而废弃发送基线时调用此方法。宿主在功能区隐藏/销毁、focus 更新及所观察资源变化时也自动取消；确认后和原生 Chat 准备提交时再次检查令牌、focus 版本及对象权限。旧确认不会自动重试。该接口不能撤回已经交给 Chat 发送的消息，`cancelled` 不是消息撤回回执。
- `const stop = await Projection.watch(refs, event => …)`：每个功能区一组引用；新调用替换该组。宿主将完整组去重，按每次最多 128 个引用顺序调用 observe，全部成功后才安装订阅；HTTP 单次上限与逐批当前用户鉴权不变。调用方不要分多次 watch（会替换前组）。任一批失败时 watch 拒绝，旧订阅、监听器及 stop 保持；成功后旧 stop 不影响新组。刷新同样整组核验后发出一次 refs/observed_at（最后一批的观察时间，不表示跨批原子快照），失败只发 error/code 与空 refs，不发送部分成功，面板重新读取详情。宿主匹配 Portal SSE 的 `changed`、`resource_projection_changes` 与全部已鉴权引用根；无关执行/对话事件不刷新，重连或缺少细分目标的资源快照触发重新核对。不给 iframe 开放流式 HATG。隐藏暂停刷新，显示重新整组核验；已在途请求可完成，但不再发后续批或通知。替换、stop 和销毁使旧刷新失效，销毁也阻止在途注册回写。`stop()` 成功清空观察；若其请求已被后续 watch/stop 取代且宿主回报 `projection.cancelled`，旧 stop 正常结束且不清空新监听器。其他失败（包括未被取代的取消）保留旧组并拒绝，调用方可以重试。
- `Projection.onVisibility(visible => …)`：返回取消函数，可暂停面板自己的可见性工作，显示时重新读取一次。首次注册回调当前状态。
- v2 的 `Projection.state()` 按当前用户和功能区隔离保存，写一个功能区不会覆盖另一个功能区。v1 保留原有整页偏好。
- `Projection.resource("read", {address, format: "yaml"})` 返回解析后的 `data` 与 metadata；默认 read 保留原有 content 返回值。两种格式都在宿主读取原始 bytes 后、解析或编码前计算 `metadata.content_sha256`（64 位小写十六进制）。共享 `resources.read` 原样保留此 metadata。需要 YAML 与行锚点原文本配对时，只使用哈希相同的一对；不同则重新读取整对，不能混用。知识正文的 `metadata.record.version.number` 保持原样，继续作为 Plan/Design 版本；内容哈希用于核对字节一致性，不替代该版本号。
- `Projection.resource("resolve", {address})` 按当前登录用户鉴权，仅返回 `{readable, resource_type, title, portal_url}`，不返回正文、本地路径或内部元信息。不存在或无权读取时统一返回 `{readable: false, resource_type: "", title: "", portal_url: ""}`，界面按“缺少依据”显示；服务故障仍报告错误。源码行锚点先从 address 去掉 `#L123` 再解析，语义对象保留原 ref。需要正文时调用 read；默认 read 的 `encoding` 为 base64 时，应解码后再解析 JSON 或展示文字。

门面对话的运行、排队和未读标记来自原生 Chat Workspace 状态，同时写入活动轨入口的可访问名称与提示；恢复布局后的发送仍须与当前 facade 绑定一致。进展摘要按当前产品 Design 呈现成果与体验，来源及验证细节按需展开；没有可读取依据时不标记为已验证。摘要条目使用批次文件独立行锚点，以保持跨功能区对象身份一致。

实现归属：page.py 校验清单，tables.py 的 page_packages 与 versions 同事务保存；semantics.py 鉴权对象；projection_host.js 管理 Panel 生命周期、Context 和原生 Chat 桥接；projection_sdk.js 只负责隔离通道。设计索引为 SPEC.md 的 decision_7a5ae487e2e9 与 decision_13e1a748cf04。

首个工作台是 `workbench_75b51c00005e`，入口 `/workbenches/view?id=workbench_75b51c00005e`。提交 `development_app` 源码后，用本文开头的 `development_projection --commit <SHA>` 生成应用包定义，通过 HATG UpdateResource 保存构建成功的新修订并独立发布。读取 architecture/modules.yaml、architecture/evolution.yaml 与 IMPROVE.md；阶段摘要的接入由汇报成员提供真实资源和结构，无摘要时显示缺少依据。

## 页面代码与基础模板

宿主页、Studio 预览和内置组合页复用 Lineage 同一公共布局：`portal_workbench.css` 的 `portalWorkbenchPageLayout`，直接包含 `portalActivityRail` 与 `portalWorkbenchViewportRoot` 内容槽位。该组件统一侧栏尺寸、border-box 占位、间距和窄屏横排；调用方不复制 grid 列宽、侧栏定位或内容补偿边距。外层已有活动轨时将内层 rail 设为 hidden，公共模板同时取消其占位。页面仅决定业务视图与容器高度，iframe 内部内容仍由应用负责。协议依据 `decision_7a5ae487e2e9` 的“页面模板必须在代码层复用”。

Workbench 的 `html` 字段保存业务内容片段及其内联业务脚本，禁止使用完整 HTML 文档。设计 Resident 直接通过 Workbench ResourceService 动作保存页面代码，并在 Chat 中以自然语言说明结果；聊天正文不是页面定义或反投影计划的机器传输通道。宿主统一挂载 PortalWorkbench、Panel Package、Activity Rail，并向隔离业务区域注入 Portal 公共控件样式及当前主题。主题变化会同步到业务区域。

片段不能包含 `html`、`head`、`body`、`nav`，不能创建品牌、全局侧栏、hero 页面模板或重写 `:root` / 基础主题变量。允许局部业务布局 CSS；按钮使用 `portalButton`（主操作 `is-primary`），表单由 `portalField` 包裹，工具栏使用 `portalToolbar`，分区使用 `paneHead` / `paneBody`。不要加载或重建 Portal 的页面级组件。

模型组合、图表、筛选、摘要、进度、局部确认等属于业务内容；全局导航、页面框架、共享主题和基础控件属于 Portal。创建、生成、保存和发布时会校验片段契约。

Portal 宿主统一提供 TFI Portal 导航；投影源码只负责业务界面，不复制 Portal 顶栏、绑定配置横条或手动连接入口。

投影读取器只接受完整的 canonical `tfi.<tag>.vN`，格式名称和版本必须精确匹配。
`Projection.resource('configuration', {})` 在工作台访问校验后固定返回 `{format_namespaces: []}`；
该操作不读取本站历史前缀配置，调用参数必须为空对象。应用启动无需读取它。
历史前缀即使通过配置或函数参数提供也不接受；不改写资源原文、持久 ID、原始摘要或绑定字段。
页面与绑定设置位于 `/workbenches?panel=settings&id=workbench_*`，AI 工坊属于同一 Workbenches 页面内的模块。
导航位置是即时设置，不依赖源码发布；选择菜单/顶部/隐藏后直接生效。页面内容与团队绑定仍需显式发布。
进入 Lineage 后，关联投影在公共组件选择器中作为独立编辑器添加和恢复，不嵌入 Navigation，也不在组件内部重建 Workbench、顶栏或 Activity Rail。
工作台账号授权不代替 Lineage 授权，SDK 始终使用当前登录用户执行 HATG，并按用户隔离本地草稿。

内置页面复用真实业务身份登记，不按访问账号创建同名空团队。通用框架最多支持 256 个 Lineage，具体团队划分、稳定角色、动态目标及权限策略由所属业务规范定义。新角色按该身份契约注册，保留已有会话与证据；失效或无权绑定明确报错，不创建替代团队或借用运行账号。

开发投影的原生页面外壳由开发投影和已安装功能登记的原生视图组成，当前是 WALL 和 Test。默认 `/development` 显示现有投影区域组和门面对话；`/development?view=wall` 与 `?view=test` 按需装配原生 `PortalWall`、`PortalTestWorkbench`。WALL/Test 的资源、修订、绑定、所属 Lineage、业务接口和当前用户鉴权仍由原领域拥有。页面合并不迁移业务身份或数据。

这些入口由 `development_workspace.js` 的 `PortalDevelopmentWorkspace` 统一装配。功能在 `portal_features.py` 的 `web.native_view` 接入点登记自己的视图：编号、活动轨图标与标题，以及一个模板文件（WALL 为 `wall.html`，Test 为 `test_framework/page/test_native_view.html`），文件中是 `<id>NativeView` 模板和 `<id>NativeViewAssets` 依赖清单，不提供独立页面。页面启动时读取 `GET /development/native-views` 得到已安装视图的清单并列入活动轨；首次选择某个视图时读取 `?view=<id>` 的模板，再载入它的样式和脚本。视图脚本载入后调用 `PortalDevelopmentWorkspace.registerView(id, controller)` 登记 `activate`、`deactivate`、`setVisible`、`destroy`。地址中的视图没有安装时打开开发投影。切换保留已装配节点，不搬移或重建投影 iframe/原生 Chat；宿主组合页面显隐和区域显隐向 SDK 通知，并撤销隐藏区域尚未确认的反馈。WALL 离开时停用刷新，返回沿用原实例；Test 仅首次选择时调用其 mount。原生视图加载失败显示本视图错误和重试，默认开发投影启动只读取视图清单，不读取各视图的模板、样式和脚本。旧 `/wall`、`/wall/` 以 302 转到新入口，保留 view/project/block/semantic/case/run 参数；带 case/run 的旧链接默认进入 Test，其他默认进入 WALL。顶层导航不再单列 WALL。

外层组合者调用 `PortalWorkbench` 实例的 `setVisible(boolean)` 同步该工作台的顶栏工具与面板 `show/hide`；它不修改保存的布局、不销毁面板、不写入用户的面板隐藏偏好。创建时默认 `visible: true`，保持旧调用方在隐藏容器中装配的生命周期；只有显式 `visible: false` 或 `setVisible(false)` 才暂停面板。异步装配须在实际创建时读取外层当前选择，Test 通过 `mount({isVisible})` 取得该值。选中 WALL/Test 时，`portal_projections.js` 仍按原业务名称初始化各自内置登记，不借用 development 的 Lineage。

Portal 开发投影通过共享 `development` 登记提供内置入口 `/development`，出现在固定顶栏与目录内置组，不占个人扩展名额。登记保留现有 workbench ID、修订、构建产物及团队绑定；owner 是维护归属，不能据此将它显示为个人工作台。显式启用使用 `UpdateResource` 的 `changes={expected_revision,activate_builtin:true,builtin:"development",previous_app_id}`，与修改内容分开提交；已登记的其他内置类型不能改名，冲突不部分迁移。所有读取、动作和 Chat 继续使用当前访问者身份，草稿及布局按用户保存。

内置入口仍运行普通投影应用包，旧 `/workbenches/view?id=…` 地址继续可用。`Projection.openPage("/development?region=architecture")` 在当前内置工作台原地切区，省略 region 聚焦门面对话；查询参数只允许一个 region，不接受 id 或锚点。打开其他工作台仍按原页面导航契约执行。

固定 AI 功能绑定流程入口，由 Event／Effect 向稳定成员投递逐次 prompt。持续对话直接复用已解析 Resident 的 Chat Workspace；仅首次开通可有创建和初始化流程。页面本地交互仍留在前端，正式产物由所属领域统一提交和查询。具体对应表只查业务规范：WALL `decision_06defc47e960`、Paper `decision_e37f240e186d`、Test `decision_f2a6a5dd4646`；SDK 不复制其角色和身份规则。

页面演进后可在工坊内请求反向整理：依据页面源码、业务规范、当前绑定和图版本生成语义差异，优先复用现有成员，经 owner 审核并确认计划哈希后才应用。当前应用器只做新增待命成员、手动 Event、消息 Effect 和对应连接；不自动执行任务、删除、迁移或授权。可同时保存 `page_update` 业务片段和入口别名为草稿，发布独立。人工修订保留原结果与失败原因，明确标记来源，不冒充 AI 输出。通用边界见 `decision_f67ae5e70793`，工坊功能由 `decision_4bc03b20c30f` 定义。

The host injects `window.Projection` before your HTML scripts. Do not overwrite it.

## 异步输入与回复正文

TriggerEvent 回执中每个已执行 Effect 的 `delivery.results[].typed_outputs.message_id` 是实际输入消息 ID，不是回复正文，也不能用页面请求键替代。
用 `Projection.eventInputIds(receipt)` 取出这些 ID（只读、不发请求，没有已执行投递时返回空数组），立即保存到页面状态。
使用 `GetInputState` 查询该 ID；返回值 `input_state` 的 `state` 是 `queued/running/completed/failed/cancelled` 字符串，
`reply_message_ids` 是真实关联回复 ID：输入结束后是它的最终回复（最后一条回复它的消息）。队列或运行中继续只读轮询，并提供“刷新结果”；刷新页面后恢复原消息 ID，不能重投。
对关联回复调用 `GetMessage`，从返回的 `message.content_parts` 中读取 `type=text` 的 `text`，并核对 `reply_to_message_id`。
Portal 的消息角色是 `user/agent/system`；Resident 正式回复的 `role` 是 `agent`，不是提供方协议中的 `assistant`。不要在业务片段里另写角色转换或回复关联算法。
页面读取一次输入的状态和回复应使用 `await Projection.inputResult(messageId, {binding: "main", role: "coordinator"})`。
该只读 helper 使用同一绑定查询状态和关联消息，严格核对输入 ID、回复 ID、父消息 ID 和 `agent` 角色，返回 `{message_id, state, reply_message_ids, messages, text}`。
`queued/running/failed/cancelled` 不将进度当作回复，`messages=[]`、`text=""`；只有 `completed` 的关联回复可展示。`messages` 保留正式消息中的非文本内容，`text` 仅拼接文本段。
空回复返回 `projection.reply_unavailable`，关联或角色错误返回 `projection.reply_mismatch`，权限错误原样传播；均保留已提交的输入 ID，不能自动重投或换 Resident。
每次调用只观察一次，不创建轮询器、不提交工作；页面决定刷新/轮询与销毁时机。示例：

```js
const result = await Projection.inputResult(savedMessageId, {binding: "main", role: "coordinator"});
statusNode.textContent = result.state;
if (result.state === "completed") outputNode.textContent = result.text;
```

不要把本地输入、事件受理、消息 ID、工具进度或空结果当成 AI 回复。终态停止轮询；失败/取消明确显示，重新提交必须是新的显式操作。
请求期间切换业务对象时，不让旧请求回写当前页面。页面关闭停止定时器，查询失败保留原消息 ID 供恢复。

- `await Projection.context()` returns `{workbench_id, title, bindings, preview, themes}`. `themes` is the read-only current host theme catalogue `[{id, label}]`, with translated menu labels from `PortalTheme.options()`; null means the host catalogue is unavailable. Reading this list does not change appearance. Release selections belong in application state, independently of the user's current theme.
- `await Projection.hatg("GetLineage", {}, {binding: "main"})` returns `{lineage, version}`; `lineage` is the real HATG graph: `title`, `revision`, `nodes`, `edges`, `program`. Each Resident node has `id`, `title`, `status`, `last_run`; `id` is the Resident ID; graph nodes do not expose a duplicate `resident_id` alias. Status can be idle/queued/running/blocked/done; display its actual value. The host fills `lineage_id` from the binding.
- `await Projection.hatg("ListResidentStates", {resident_ids: [...]})` reads current runtime states. The response is `{states: [{resident_id, state: {work_state, active_message_id, queued_count, intake_state}, ...}]}`. Read `item.state.work_state`, not `item.status`. Refer only to Residents returned by the graph; missing items are unknown, never silently assumed idle.
- `await Projection.hatg("GetInputState", {message_id: "..."}, {role: "coordinator"})` follows a submitted message. Returns `input_state` with `state` and `reply_message_ids` (once it finished: its final reply, the last message answering it).
- `await Projection.hatg("TriggerEvent", {prompt: "user's current request"}, {binding: "main", flow: "analyze", idempotency_key: "stable-request-key"})` triggers `bindings.main.events.analyze`. The host confirms the action and resolves the actual Event Source ID. The result is `{event_id, trigger_path_id, delivery}`; `Projection.eventInputIds(result)` returns the canonical input message IDs (`delivery.results[].typed_outputs.message_id` of each executed Effect); save them at once and read progress with `Projection.inputResult`; acceptance of an Event is not completion of AI work. The Effect owns the fixed prompt; `prompt` is appended to it. Reuse the same key only for the same logical request. Optional `content_parts` carries images, files and context references under the standard Resident message contract; text belongs in `prompt`.
- `await Projection.hatg("DeliverMessage", {message: {message_id: "msg_" + crypto.randomUUID().replaceAll("-", ""), content_parts: [{type: "text", text: "user's request"}]}}, {role: "coordinator"})` requests actual work. The trusted host asks the user to confirm mutations. Do not automatically submit work on load. Keep the message_id to observe progress. Catch and display `projection.resident_unbound` without choosing a replacement Resident.
- `await Projection.state()` loads this user's page preferences; `await Projection.state({selectedModels: [...], selectedOptimizations: [...]})` replaces them. Page state is independent from Lineage state.
- The trusted host prompts before unloading while a state write is pending or after a failed write, until a retry succeeds. Applications must display save errors and retain their editable draft; sandboxed frames cannot provide this browser prompt themselves.
- `await Projection.resource("read", {address: "decision_..."})` reads a referenced resource via ResourceService. `query` takes ResourceService query arguments. The host preserves the current actor and read permissions.
- `Projection.openLineage("main")` opens the ordinary Lineage page outside the sandbox.

All methods return Promises and throw Errors with `code` on failure. Read requests may be repeated for refresh; clear timers on page unload. Avoid fixed progress percentages. Model/optimization selections, comparisons, draft plans and other UI workflows can run locally without HATG mutation.

`projection.cancelled` means the user cancelled in the trusted host and no instruction was submitted. Roll back any prepared submission marker, persist that rollback and allow another deliberate submission. A network timeout after confirmation is different: retain the message ID and query its real state before retrying. Local confirmation forms may handle submit with `preventDefault`; network form submission remains blocked.
Use a clean Chinese interface with labelled selects/checkboxes, visible summary of choices, refresh and explicit action buttons, real progress cards, error/empty/loading states, responsive CSS. An explicit action never silently does nothing: while it cannot run (data still loading, another request in flight), disable its button, including a page confirmation button, or say why. No external assets, network requests, modules or navigation; all JS/CSS must be inline. Use semantic HTML and textContent for data from the team.

For domain dashboards, read authorized, registered evidence and follow the domain's comparison and validation contracts. Missing evidence is unknown, not zero or completion. Keep submitted actions, live Resident work, and validated business results separate. Domain-specific catalogs, metrics, and workflow bindings belong in the corresponding business or example documentation.

## 打包发布服务桥

`Projection.release(operation, arguments)` 只供已发布 `packaging` 区域调用；预览或其他区域拒绝。宿主添加真实 workbench ID 和 panel，经固定发布服务入口处理，不能传任意 URL 或 ISA。配置读写及发布运行不通过通用 DeliverMessage 创建第二套执行链。当前操作为 `configs.list/get/save`、`runs.list/get/start` 、`candidates` 和 `targets`；具体配置/运行及幂等规则由发布后端权威维护。普通 Resource/ISA 鉴权保持各自边界，发布业务不得以客户端伪造身份跳过权限。

发布桥固定 POST `/api/workbenches/release`，参数 `{id,panel:"packaging",operation,arguments}`。配置写入使用 `{name,snapshot,request_key,config_id?,expected_revision?}`；创建 ID 由服务生成，更新按 revision 检查。运行启动使用 `{request_key,config_id,snapshot,mode,tau_validation,provenance}`，返回服务端 `run_id`。请求 key 是网络重试身份，不显示为已创建运行。`candidates` 只接受服务端封闭读操作及参数，页面不传 actor；其他区域的 Resource/ISA 与原任务对话不改用此通路。

打包发布入口的 native/detail 请求附 `panel=packaging`；服务只返回当前已发布修订的发布面板，并标记 `release_scope: "packaging"`。此标记让宿主从 `release-assets` / `release-libraries` 加载该面板文件，预览和普通工作台仍使用原鉴权路径。发布草稿沿 state 的 `panel=packaging` 协议进入独立偏好存储，服务兼容既有草稿。普通团队对话不预加载，用户显式打开时仍通过原 facade 鉴权和 Society 入口。

`targets({snapshot,features})` 返回权威目标目录与 resolution（selected_targets/support_matrix/capability_gaps）；供发行目标选择及缺口呈现，不能据勾选或 installed 推断完整验证成功。
