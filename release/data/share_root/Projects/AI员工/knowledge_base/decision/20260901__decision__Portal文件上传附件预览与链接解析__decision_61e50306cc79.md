---
kb_id: decision_61e50306cc79
type: decision
project: AI员工
created_at: '2026-09-01'
version:
  number: 31
  created_at: '2026-09-01'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T08:24:24Z'
  updated_by:
    user: ''
    agent: resident_96243cc26373
importance: 9
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/hatg/catalog/resource.py
  - ${code_root}/management_portal/http_media_range.py
  - ${code_root}/management_portal/static/portal_chat_workspace.js
  - ${code_root}/management_portal/static/portal_instruction_client.js
  - ${code_root}/management_portal/asgi.py
  - ${code_root}/management_portal/resources/isa.py
  - ${code_root}/management_portal/serve_management_portal.py
  - ${code_root}/management_portal/resources/service.py
  - ${code_root}/management_portal/resources/providers/workspace.py
  - ${code_root}/management_portal/static/portal_agent_preview_source.js
  - ${code_root}/management_portal/static/portal_resource_chips.js
  - ${code_root}/management_portal/static/portal_preview_contract.js
  - ${code_root}/management_portal/static/portal_preview_runtime.js
  - ${code_root}/management_portal/static/portal_preview_renderers.js
  - ${code_root}/management_portal/static/portal_workspace_surface.js
  - ${code_root}/management_portal/static/portal_file_links.js
  - ${code_root}/management_portal/static/lineage_workbench.js
  - ${code_root}/management_portal/society_service/workspace_contracts.py
  - ${code_root}/tests/cases/test_paper_registration_upload.py
  related:
  - decision_e5f66e2f6a5e
  - decision_864d4fe41934
  - decision_769a35d6b5c7
  - decision_ad3e21094f14
  - decision_dca903d71575
  - decision_538e155e1102
  - decision_b6e74d78f7b6
  - decision_34158fdd4078
  - decision_3ece26686464
  - decision_9f6135cbbb22
  supersedes: []
  derived_from: []
---
# Portal 文件入口、上传与 Preview/Renderer 协议

Portal 中很多地方需要“打开一个文件看看”：Resident 在回复中提到的文件路径、知识条目中的统一资源 ID、Repo 中的源码、项目状态里引用的证据。用户点击后，文件在当前页面的预览区域中显示：标题、正文或图片视频、缩放工具，以及下载、刷新、编辑按钮。预览可以出现在 Workbench 的预览面板、文档工作区（decision_769a35d6b5c7）或页面中的弹出层里。

为了让所有入口以同样的方式打开文件，Portal 用一条共享的预览链路：先把链接整理成一个打开请求，找到能承载它的页面区域，由知道这类来源的组件读出内容并整理成统一的文档，再选择合适的渲染器显示。本篇说明这条链路的各个环节、标准渲染器，以及上传、保存和版本冲突的规则。图片的全屏查看与标注见 decision_dca903d71575；标签页、未保存草稿和工具栏见 decision_769a35d6b5c7。

## 基本概念

| 名称 | 含义 | 负责 |
| --- | --- | --- |
| 预览请求（`PreviewRequest`） | 用户想打开什么、从哪个页面发起。 | 定位信息（资源地址、链接或路径）、标题、来源和打开上下文；这时内容还没有读取。 |
| 宿主（host） | 能承载预览的页面区域，例如 Workbench 的预览面板。 | 决定是否接管一个请求，显示加载中和错误，管理当前显示的文档。 |
| 来源适配器（source adapter） | 知道怎样读取某一种来源的组件，例如统一资源、Resident 工作目录中的文件。 | 读取文档；有权限时保存文档。 |
| 预览文档（`PreviewDocument`） | 来源读取后整理成的统一文档。 | 内容、类型、版本、下载地址和能力。 |
| 渲染器（renderer） | 把一种文档显示到页面上的组件。 | 判断能否显示某份文档，显示它，并在切换时释放自己的状态。 |

**能力**（capabilities）说明服务端或来源明确允许对这份文档做什么：下载、刷新、编辑、保存。读取成功、文件扩展名或地址本身都不会带来写入权限。

## 设计点

- **点击文件链接或资源引用时，在当前页面打开预览。** 当前页面有能接管的宿主时在宿主中打开；没有时使用统一的备用打开方式。
- **先显示加载中，再显示内容。** 没有能显示这种格式的渲染器时，显示“不支持”的说明和下载入口，不留空白。
- **文件入口的右键菜单提供“在新标签页打开”和“下载”。** 新标签位于当前 Preview 的文档标签栏，原文档与草稿保留；Workspace 文件行还提供省略号按钮，触屏和键盘用户也能打开菜单。普通点击继续按宿主的复用规则打开。
- **下载使用原始文件名，在新标签页中进行。** 当前 Portal 页面不会被替换。
- **按钮按能力显示。** 只有能力允许时才显示编辑、保存、刷新和下载；只读文档即使显示了全文也没有保存按钮。
- **保存时文件已被别人改过，保留草稿并提示。** 用户可以重新读取、对比或另存；无权限和资源已删除显示不同的错误。
- **带行号的代码链接打开后定位到这一行。** 找不到这一行时仍然显示文档。
- **上传失败只影响这一个附件或表单。** 用户可以重试或移除；预览链路不负责删除业务的临时文件。
- **连续缩放在每一帧只处理最后一次。** 切换文档或关闭时丢弃还没处理的缩放，旧手势不会改变新文档的视图。
- **图片的“适合窗口”以预览区域的外框尺寸为准。** 缩放后出现滚动条时，适合窗口的基准不变；尺寸变化在一帧内合并处理，相同的值不重复写入，所以不会来回抖动。

## 请求与文档：`PortalPreviewContract`

### 整理打开请求

`normalizeRequest(input)` 只接受对象，从 `href`、`path` 或 `resourceAddress` 生成打开请求，包含 `locator`（定位信息）、`locatorKind`、`title`、`source`（默认 `chat`）和打开上下文；输入不是对象时立即报错。定位信息优先使用统一资源地址，没有时使用链接或路径。

统一资源地址保持原样。绝对路径、`file://` 链接、Markdown 中的相对链接和带行列号的定位由文件链接组件（`portal_file_links.js`）转换成服务端可以验证的目标；识别不出的文字保持为普通链接，浏览器不直接读取本机路径。

### 判断文件类型

`normalizeKind` 是 Portal 中唯一判断文件类型的地方，依次按明确声明的类型、MIME 和文件名决定 `contentKind`。资源、文件链接和各页面只提交自己已知的事实，不维护另一张扩展名表。

- `directory`、`remote-link`、`office` 可以由来源直接声明。
- 常见源码、XML、YAML、TOML、纯文本、CSV、日志，以及 `README`、`LICENSE`、`Makefile` 这类没有扩展名的文本，按文本或代码显示。
- 仍然判断不出时按 `text` 处理；只有调用方明确要求时才作为 `binary`。
- 来源明确给出的类型和 MIME 优先于文件名，所以没有扩展名、或者扩展名与 MIME 不一致的文件，按真实内容显示。

`hasFileShape` 只回答“这看起来是不是一个文件”：带任意扩展名的名称、规范的无扩展名文本文件名和 `.env` 都算，未知扩展名的文件最终作为可以下载的 `binary` 文档。文件链接组件扫描文字时只提取候选，再交给它确认；增加一种预览格式只需要修改这里，聊天和页面中的链接识别不用跟着改。

### 整理文档

`normalizeDocument(input)` 生成预览文档：

| 字段 | 取值规则 |
| --- | --- |
| `id`、`title` | `id` 依次取身份、路径、标题；`title` 依次取标题、名称、路径的文件名，最后为 `Preview`。 |
| `downloadName` | 依次取 `downloadName`、`download_name`、`filename`、`name`、路径的文件名和标题。 |
| 内容 | `path`、`mediaType`、`language`、`contentKind`、`content`。 |
| 地址 | `rawUrl`（原始字节）、`previewUrl`、`downloadUrl`。 |
| `version` | 依次取 `version`、`etag`、`mtime_ns`、`entry.mtime_ns` 或 `entry.mtime`。 |
| 能力 | 下载默认取决于是否有下载地址，刷新默认允许，编辑和保存默认取决于 `editable`；输入中的 `capabilities` 可以逐项覆盖。 |

`contentKind` 的取值为 `markdown`、`json`、`jsonl`、`text`、`code`、`image`、`video`、`audio`、`pdf`、`directory`、`remote-link`、`office` 和 `binary`。

`assertSourceAdapter` 要求来源适配器有 `id` 和 `load`；`assertRenderer` 要求渲染器有 `id`、`accepts` 和 `render`。缺少时在登记时立即报错，不会等到用户打开文件才失败。

## 预览运行时：`PortalPreview`

- **宿主**：`registerHost(name, handler, { priority })` 登记一个宿主，同名再次登记时替换；注销只移除仍属于自己的那次登记。`request` 按优先级从高到低依次询问宿主（同优先级按登记顺序），宿主返回 `false` 时继续询问下一个，全部拒绝时返回 `false`。
- **来源**：`registerSource(adapter)` 要求来源 ID 唯一，注销只移除这次登记，重复注销没有作用。`load` 遇到未知来源时报错，读取结果再经过 `normalizeDocument`。来源没有保存能力时 `save` 返回只读错误；草稿先转成字符串，保存结果也重新整理。
- **下载链接**：`configureDownloadLink(link, { url, filename })` 是所有宿主共用的下载按钮设置方法。地址为空时隐藏链接并去掉 `href`、`download`、`target` 和 `rel`；地址有效时显示链接，文件名只取最后一段，并设置 `download`、`target="_blank"` 和 `rel="noopener noreferrer"`。它不读取资源、不判断权限，下载地址由宿主从已经授权的文档中取得。
- **渲染器**：`registerRenderer(renderer, { priority })` 按唯一 ID 和优先级排序，`rendererFor` 返回第一个接受这份文档的渲染器（`accepts` 抛出的错误直接交给调用方，方便定位）。`render` 在同一区域显示新文档前先释放旧的显示；返回值包含 `document`、`renderer`、`promise` 和 `dispose`，渲染器的清理出错时 `dispose` 也会释放这个区域。

## 文件入口的操作

共享 Preview 运行时的 `openFileMenu({event, trigger, request, onOpen})` 负责文件操作菜单。聊天中的本地文件链接、可预览的资源链接和 Workspace 文件行提交同一份定位请求；菜单保留路径、资源地址和行号。没有自定义打开回调时交给 `PortalPreview.request`，有回调时把完整请求传给宿主。

- **“在新标签页打开”提交 `mode: "new-tab"`。** 宿主把该方式传给文档标签页控制器；保留、固定与已打开目标的处理统一由 decision_769a35d6b5c7「打开与切换文档」规定。这个标签属于 Preview，Workbench 编辑区实例的“新标签”仍按 decision_79de71d9890d 管理。
- **下载独立于当前预览。** 本地文件先通过现有 metadata 接口检查路径和访问能力，再使用文件链接组件生成的下载地址；资源文件沿用资源来源给出的下载地址与能力。文件内容响应仍由服务端鉴权，菜单不改变活动文档、不替换当前页面，也不把整份文件转成浏览器内存中的 Blob。
- **只在能定位下载目标时提供下载。** 仅有相对路径、尚未解析为本地文件或资源地址的入口保留新标签操作；目录不作为文件下载。读取或下载准备失败通过局部通知反馈，原预览不变。
- **右键菜单跟随指针，按钮菜单贴近触发按钮。** 打开后焦点进入第一项，上下方向键切换选项，Esc 或点击外部关闭；关闭时恢复到仍存在的触发元素。

文件链接组件维护本地文件下载地址的生成，资源组件维护资源下载地址与读取权限。菜单只选择操作，文档标签页控制器拥有保留、切换、固定、关闭和草稿状态；页面适配器负责把完整打开请求传到对应来源。

## 标准渲染器

渲染器按优先级选择：远程链接 100、目录 90、PDF 与 Office 80、图片视频音频 70、文本 50、不支持 -1000。

| 渲染器 | 显示方式 |
| --- | --- |
| 远程链接 | 以当前页面为基准把相对地址转换成完整地址，提供“打开”和“复制”，两者使用同一个地址，并保留 `%26` 这类百分号编码。远程页面作为链接卡片显示，不作为可编辑的正文。 |
| 目录 | 列出目录中的条目，只读。 |
| PDF 与 Office | PDF 按可见页面逐页绘制；没有地址时在局部显示无法预览；文字层失败不影响已经绘制的页面。Office 文件只读显示。 |
| 图片 | 100% 时不放大到超过原始尺寸；全屏查看和编辑交给共享的图片组件。 |
| 视频与音频 | 只预先读取元数据，打开时不下载整个文件；拖动进度时由同源的内容地址按字节区间返回。 |
| 文本 | Markdown 默认以可视模式显示，折叠 YAML 头部；只有标准的 Mermaid 代码块显示为图，一张图出错只标记这个代码块。JSON、JSONL、XML、YAML 使用各自的语法高亮。 |
| 不支持 | 有地址时提供下载；没有可以下载的来源时显示不支持的说明。 |

- **Markdown 中的 HTML 和图片来源只接受许可列表中的内容。** 链接目标两侧意外多出的空格（例如 `[播放]( /api/resources/content?... )`）会被去掉，再经过同一套地址检查；代码块、行内代码和检查不通过的目标保持原样或显示为纯文本。所有使用共享 Markdown 渲染器的页面采用同一规则。
- **Markdown 表格可以横向滚动。** 单元格中连续的数字、标识符和文件名不在任意字符处断行。
- **文本的放大查看复制当前已渲染的文档和 Portal 样式**，不把文本文件交给只用于媒体和下载的原始字节地址。

## 缩放与文件定位

- **图片和 PDF 使用共享的缩放控制。** 图片的比例范围为 0.25–6，PDF 为 0.2–4。桌面上按住 Ctrl 滚动滚轮缩放，触屏双指缩放和 Safari 手势使用同一套逻辑，“Reset”回到 100%。缩放以指针位置为中心，缩放比例在停止操作 180 毫秒后保存。
- **连续手势由共享的帧合并器处理。** `createFrameCoalescer(callback, onError)` 为一次交互保存最后一次待处理的值：`schedule` 合并到下一帧，`flush` 立即处理，`cancel` 丢弃，`dispose` 取消后不再接受新的调度。回调出错时交给 `onError`。预览缩放和 Article 的 PDF 手势共用它。
- **文件定位支持 `#Lx-Ly` 和 `:行:列` 两种写法。** 统一资源地址优先于路径判断；历史 `/nas_ro/` 前缀在页面取得有效 `window.__PORTAL_SITE__.paths.share_root` 时转换到该共享根，未取得时保留原路径交由服务端解析；相对路径可以使用调用方给出的 `basePath`；本地路径只按允许的根目录判断。上传图片、知识条目讨论图片和旧 `/resident_chat_images/` 图片由共享 `imagePreviewUrl` 按下文「消息中的图片用上传地址显示」生成受保护的地址；其他本地文件通过 `link-file` / `link-file-raw` 读取，路径和权限由服务端检查。
- **Resident 回复中的相对文件链接相对于这个 Resident 的工作目录。** 页面依次使用 Resident 文件夹的 `primary_root_id`、标为 `is_primary` 或 `default_cwd` 的根目录；只有一个根目录时才直接使用它。链接必须是具有文件形状的相对本地路径，页面把它拼成 `<根目录 ID>/<相对路径>`，交给 Resident 文件来源读取和检查权限。普通网页地址、本页锚点和不像文件的相对链接不会被当成本地文件，也不会用浏览器当前目录、Lineage 文件夹或 Resident 文件夹的路径去猜测。
- **Markdown 中只有未修饰的左键点击由预览接管。** 已经在 `a`、`button`、`pre` 中或带有跳过标记的区域不重复处理；调用方没有提供 `onOpen` 时交给 `PortalPreview.request`。

## Chat 宿主的脚本加载

文件链接与消息图片的地址转换由 `PortalFileLinks` 统一负责，文件类型由 `PortalPreviewContract` 判断。挂载 Chat 的页面在初始化对话前加载这两个共享组件；静态页面在 Chat 脚本前声明依赖并保持可确定的执行顺序，按需加载入口等待依赖加载完成后再打开组件。工作台设置与预览、原生 Society 页面、嵌入式讨论和全局导航中的 Resident Menu 遵守同一约定。消息渲染继续调用共享组件，宿主承担依赖完整性，不以空地址或复制转换函数掩盖加载遗漏。

## 上传与附件

所有上传都通过 HATG ISA 指令 `CreateUpload`（decision_ad3e21094f14）完成，分两步：

1. 浏览器调用 `CreateUpload`，写明用途（`purpose`）、目标、文件名、媒体类型和大小，得到上传 ID 和数据通道地址 `/api/uploads/<上传 ID>`。数据通道 24 小时内有效。
2. 浏览器用 `PUT` 把文件字节写入数据通道，页面显示上传进度。写入完成后，上传 ID 可以放进消息、帖子或业务表单中引用。

- **大小和类型由用途决定。** 用途有 `conversation_attachment`（对话附件）、`post_image`（帖子图片）、`paper_pdf`、`article_file`、`figure_input` 和 `avatar`（User、Resident、Project 或 Lineage 的头像）。一般上限为 512 MiB；`post_image` 上限为 12 MiB，只接受 PNG、JPEG、GIF 和 WebP；`avatar` 上限为 2 MiB，只接受 PNG、JPEG 和 WebP；`paper_pdf` 必须是 PDF。服务端在写入时按实际字节检查大小和类型，字节数与声明不一致时拒绝。整个 HTTP 请求体上限为 520 MiB，超过时返回 413。部署入口的反向代理请求体上限至少为 520 MiB，以便 512 MiB 文件通过完整上传链路。
- **只有创建上传的人能写入字节。** 数据通道过期或已经写完时拒绝再次写入。
- **读取上传内容受权限限制。** 创建者和 Portal 管理员都能读取；帖子图片所有登录用户都能读取；目标是 Resident 的上传，能看到这个 Resident 的用户可以读取。其他人得到 404。
- **Resident 通过工具调用上传时使用已有的工作区文件。** `CreateUpload` 提供 `source_address` 时，服务端直接复制这个文件，不需要数据通道。
- **删除上传使用 `DeleteUpload`。** 删除授权与被引用上传的处理统一遵守 decision_ad3e21094f14 的 `DeleteUpload` 合同；能读取附件或具有管理员身份不构成删除权的额外来源。
- **上传存储在实例间共享的持久目录。** `resources.isa.upload_root()` 优先使用 `PORTAL_UPLOAD_ROOT`，未设置时使用 `instance_paths.instance_root() / "uploads"`；实例根由 `portal_paths` 的 `instances_root` 提供。每个上传使用 `<上传根>/<上传 ID>/`，保存文件和 `meta.json`。服务同一批会话的 Dev 与 Release 必须读取同一上传存储，重启切流不能使已上传附件失效。已有部署的旧目录和符号链接继续承担历史路径兼容，路径配置调整不能丢弃已引用文件；规范不固定某台机器的安装根。
- **对话附件**：Chat Workspace 把图片和普通文件都作为 `conversation_attachment` 上传，目标是当前 Resident；上传完成后，附件在消息中以上传 ID 引用。内容图片再由共享的图片组件插入正文。上传没有浏览器端的去重，同一文件选择两次会产生两个附件；发送按钮只在请求进行中锁定。还没发送的附件随输入框草稿保存上传 ID，页面自己生成的 `blob:` 预览不随草稿保存；刷新页面或 Portal 重启后恢复草稿时，图片缩略图改用 `/api/uploads/<上传 ID>` 显示，附件仍可直接发送。
- **消息中的图片用上传地址显示。** Chat、Chatroom 和 Society 页面的消息与 Markdown 图片通过 `PortalFileLinks.imagePreviewUrl` 取得显示地址：`/api/uploads/<上传 ID>` 原样使用；位于上传存储目录 `uploads/upload_<…>/` 下的文件路径换成同一个上传地址；知识条目讨论的附件图片和旧的 Resident 聊天图片使用各自的读取接口。已发送消息中的上传图片因此在刷新或重开会话后仍能显示。

## 读取、保存与冲突

### 统一资源

- **资源来源通过 ResourceService 读取当前用户能看到的主要内容。** 资源引用中带有来源给出的 `content_kind`、`media_type` 和非负的 `content_size`；没有明确 MIME 时可以从文件名推断，但不覆盖来源的明确声明。页面先读引用，再把这些事实交给 `PortalPreviewContract`，所以打开没有扩展名的文件时不需要先读正文。 标题、摘要、版本和能力同样沿用引用；只有服务端同时声明允许编辑和保存时，资源预览文档才可编辑。小型文本正文以同源凭据、不使用缓存，并携带取消信号和超时读取。
- **资源地址可以指向资源内部的文件。** 例如 `workspace_<ID>/file/path/video.mp4`。同源的 `/api/resources/content?address=<地址>` 链接，只有路径和参数符合内容地址的规则、地址通过统一资源地址检查时才进入预览。浏览器解析查询参数后可能把百分号编码还原成中文等字符，资源链接适配器会把这些路径段重新编码成规范地址，不会丢掉 `address` 或降级成普通网页链接卡片。
- **文本最多内联显示 2 MiB。** 引用中已知超过 2 MiB 时，直接生成一份带原因（“文件超过 2 MiB 的文本预览上限，请下载后查看。”）和下载入口的只读文档，不读取正文；大小未知时按读取流累计字节，超过就取消读取；浏览器不支持流式读取且响应没有声明长度时，不内联显示。
- **图片、视频、音频、PDF 和其他二进制文件不在打开时读取全部字节。** 页面只生成带 `rawUrl`、`downloadUrl` 和只读能力的文档，实际字节由对应的渲染器按需读取；没有渲染器时显示原因和下载按钮。下载地址为 `/api/resources/content?address=<地址>&download=1`，已登录的浏览器可以直接打开。
- **HTTP 响应的类型与引用一致。** 来源声明的 MIME 同时用于引用和 `/api/resources/content` 的 `Content-Type`，只有未声明时才按文件名推断。
- **内容地址支持字节区间。** 响应带有 `Accept-Ranges: bytes`，支持 `start-end`、`start-` 和 `-末尾长度` 三种单个区间，返回 206、准确的 `Content-Range` 和区间长度；无法满足或无法解析时返回 416 和 `Content-Range: bytes */<大小>`；不支持多个区间。带 `If-Range` 的请求只有实体标签与当前文件一致时才返回区间，否则返回完整的当前文件。视频因此可以读取元数据、拖动进度条并从新位置继续播放。
- **不公开真实路径的来源按段读取。** 来源只公开内容大小、修改版本和文件名；HTTP 层每次最多读取 1 MiB 并写入响应，每一段都重新经过 ResourceService 的权限检查和来源自己的路径检查。读取期间大小或修改版本变化时终止响应，不会把两个版本拼成一个文件。
- **保存资源使用 `UpdateResource`。** 编辑时由 Document Session 保存草稿（decision_769a35d6b5c7）；资源来源以根 `resource_id` 定位目标，在 `changes.address` 与 `changes.text` 中提交地址和新文本，并按该类型支持的合同携带读取版本 `expected_version`（decision_ad3e21094f14）。成功回执返回资源记录与版本；文档来源将已提交草稿与回执合成为新的文档基线，不假定指令回执自带正文。版本冲突返回 `instruction.version_conflict`，保留草稿；无权限或资源已删除时同样保留草稿，且不影响其他文档。刷新时跳过缓存重新读取，用新版本作为基线。

### 工作目录与其他文件

- **Resident 和 Lineage 工作目录中的文件用 `UpdateWorkspaceFile` 保存**，把打开时内容的摘要作为 `expected_version` 提交；文件在此期间被改过时返回版本冲突，草稿保留。
- **Knowledge、Repo 和 Resident 文件不各自维护格式表或预览接口。** Knowledge 文件统一通过资源来源读取；Repo 工作区把路径、MIME、是否二进制和是否截断交给 `PortalPreviewContract`，后端一次流式扫描同时计算大小和摘要，内存中最多保留文本上限加一个字节；Repo 和 Resident 的来源适配器只负责读取、权限、版本和保存。
- **文件路径由服务端检查。** 服务端检查允许的根目录、真实路径、符号链接和当前用户的权限；无权限和文件已删除分别显示，不会退化成读取任意路径。
- **删除 Resident 后清理关联页面。** 删除成功回执到达后，工作台关闭该 Resident 的聊天目标与文件预览标签，停止后续预览刷新，并移除失效的深链接；整条 Lineage 删除时对其成员执行同样清理。Unfamilize 等操作返回的 `deleted_resident_ids` 同样触发清理，不依赖指令名称是否以 Delete 开头。清理先于图的重新读取，图刷新失败也不能让页面继续访问已经删除的对象。未保存的文件草稿沿用标签页的关闭确认规则。

## 一次预览的流程

```text
文件链接 / 路径 / 统一资源地址
              ↓ normalizeRequest
          预览请求
              ↓ 按优先级选择宿主
          来源适配器读取（load）
              ↓ normalizeDocument
          预览文档
              ↓ rendererFor
            渲染器
              ├─ 只读显示
              └─ 保存：UpdateResource 或 UpdateWorkspaceFile，带读取时的版本
```

新增一种格式时登记渲染器；新增一种存储来源时登记来源适配器；新增一种页面容器时登记宿主；新的上传业务在 `CreateUpload` 中定义自己的用途、限制和权限。文档工作区负责标签页、工具栏、放弃确认和 Document Session；资源详情宿主负责页面地址和浏览器历史（decision_864d4fe41934）；资源引用标记负责统一资源 ID 的标题和打开入口（decision_e5f66e2f6a5e）；预览链路只负责请求、文档和显示。

## 预览工具栏

工具栏布局、文件路径显示和选区加入 Chat 由文档工作区统一负责，见 decision_769a35d6b5c7「布局」与「标签页、编辑工具与选区引用」。预览来源只提供文档能力，渲染器只提供内容显示与选区；页面不另建工具栏或无选区的整份文档引用入口。
