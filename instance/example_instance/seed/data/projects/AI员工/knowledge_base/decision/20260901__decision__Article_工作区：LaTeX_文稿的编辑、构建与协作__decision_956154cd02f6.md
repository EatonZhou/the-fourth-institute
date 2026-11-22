---
kb_id: decision_956154cd02f6
type: decision
project: AI员工
created_at: '2026-09-01'
version:
  number: 58
  created_at: "2026-09-01"
  created_by:
    user: ""
    agent: ""
  updated_at: "2026-11-22T13:19:02Z"
  updated_by:
    user: ""
    agent: "resident_62dbdb2288fb"
importance: 8
metaness: 2
ai_generated: true
links:
  source:
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/resources/directory_registrations.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/static/article_workspace_registration.js
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/static/portal_resource_registration_service.js
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/resource_auto_registration.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/resources/providers/article.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/http_articles.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/article_files.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/article_settings.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/article_registration.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/article_semantic_index.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/article_build.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/article_synctex.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/article_git.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/registry_sync.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/article_texlab_backend.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/article_workspace.html
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/static/article_workspace.js
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/repo_workspace_scoped_git.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/serve_management_portal.py
  - /opt/tfi/the-fourth-institute-dev/code/tests/cases/test_article_identity_backend.py
  related:
  - decision_4aa13bc21e8e
  - decision_ad3e21094f14
  - decision_eb066fbea4ed
  - decision_b6e74d78f7b6
  - decision_6476c69b33da
  - decision_538e155e1102
  - decision_61e50306cc79
  - decision_34158fdd4078
  supersedes: []
  derived_from: []
---
# Article 工作区：按资源地址打开、编辑、构建与协作

Article 工作区是 Portal 中编写论文源码的页面，入口为 `/resource/article`。这里的 **Article** 指 Project 中一篇可以编译的文稿：它由一组 LaTeX 源码组成。LaTeX 是用文本源码描述论文内容和排版的系统，`.tex` 是正文，`.bib` 是参考文献，`.sty`、`.cls` 是样式和文档类。页面把文件树、源码编辑、PDF 构建与预览、源码与 PDF 之间的定位、语言辅助、Chat 和限定在文稿目录内的 Git 放在同一个工作台中。

本 Design 说明 Article 的身份与登记、页面区域、文件读写边界、PDF 构建、源码与 PDF 定位、语言辅助和 Article 范围的 Git。相邻主题：
- 普通代码仓库的工作区、远端和凭据由 Repo 负责，见 decision_6476c69b33da。Article 复用同一套底层 Git 操作，但只作用于文稿目录。
- 页面顶部的“语义检索”使用 Portal 共享的语义搜索，见 decision_eb066fbea4ed。
- 资源登记与撤销登记使用资源指令 `RegisterResource`、`UnregisterResource`，见 decision_ad3e21094f14。

## 核心概念

- **Article**：同一篇文稿的全部源码，正式身份为 `article_<12 位十六进制>`。
- **源码根**：Article 当前使用的文稿目录。文件树、搜索、构建和文件修改都只在这个目录内进行。
- **变体（variant）**：同一篇文稿的另一个目录，例如投稿版和 arXiv 版。一个 Article 可以保留多个变体，登记中明确记录当前使用哪一个作为源码根。
- **主文稿**：构建时交给 LaTeX 编译的 `.tex` 文件。它必须是包含 `\documentclass` 的完整文档，只被其他文件引用的片段不能作为主文稿。
- **Article 登记**：PostgreSQL 资源登记表 `article` 集合中每个 Article 一条记录，记录 Article ID、所属 Project、源码根、变体和主文稿。构建设置和最近一次构建报告由独立的 Article 构建存储持久化，分别保存在 `build_settings` 与 `build_reports` 表中；登记、构建设置和报告由各自存储入口维护。
- **构建缓存**：默认位于实例 `cache/` 下的 `article_builds/`只读取规范环境键，每个 Article 一个独立目录。编译产生的辅助文件、日志和 PDF 都写在这里，不写进源码目录；缓存可以随时删除，下次编译重建。
- **构建报告**：一次构建的持久记录，格式为 `tfi.article-build.v1`，内容包括构建前检查的结果、编译器退出码、诊断信息、本次是否成功，以及上一次成功的 PDF 是否仍可用。
- **诊断**：构建或语言检查给出的一条问题，带有严重程度和类别。
- **SyncTeX**：LaTeX 编译时生成的源码与 PDF 位置对照表。从源码的行和列找到 PDF 中的页和坐标称为正向定位，反过来称为反向定位。
- **TexLab**：为 LaTeX 提供补全、悬停说明、跳转定义、符号列表和诊断的语言服务进程。编辑器通过 LSP（Language Server Protocol，语言服务协议）与它通信。
- **内容摘要（revision）**：读取文件时返回的 SHA-256 值，根据文件内容计算，用来识别用户看到的是哪个版本。

## 页面组成

### 顶部 Article 选择区

顶部有 Article 搜索框（按标题、Project 或 ID 搜索）和“语义检索”按钮，旁边是当前 Article 选择器和“登记”按钮（文件加号图标）。选择器展开后可以搜索已登记的 Article、切换当前文稿，或撤销当前 Article 的登记。

### 个人收藏

PDF / Build 工具栏最右侧提供单颗 16px 的无边框星标：空心表示未收藏，实心表示已收藏，点击切换状态。左侧图标栏的星标入口打开收藏侧栏，复用工作台工具聚焦与布局恢复。顶部资源导航不重复放置收藏控件。列表以标题为主，只列出当前用户收藏的 Article 根资源；完整标题和地址可从悬停提示查看。空列表说明如何添加，读取失败显示错误及重试入口。

个人收藏复用正式资源收藏服务与 presentation store 的 resource 分区，以认证用户和 Article 稳定地址为键；刷新或换浏览器后从服务端恢复，不向 Article 登记写入收藏属性。共享资源收藏组件负责星标、按类型分组的列表、保存失败反馈与跨标签页同步；Article 负责提供当前文章地址。Knowledge 分组仍只显示知识类型，Article 收藏不混入该分组。

添加时检查资源可访问，取消时允许资源已失效或失去访问权限；不可用条目仅显示地址和状态。点击列表项重新解析当前权限与规范页面地址，然后进入该 Article 页面，沿用页面离开前的未保存编辑检查。身份切换清空旧用户的收藏投影并丢弃迟到响应，服务端始终使用认证身份，不接受请求体指定收藏所属用户。

### 工作台

| 区域 | 内容 |
| --- | --- |
| 左侧图标栏 | 七个工具入口：文件树、收藏、全项目搜索、源代码管理（Git）、Chat Workspace、编辑器与 LaTeX 设置、Article 登记管理；点击未展开的工具时恢复并聚焦其原区域；再次点击当前展开的工具时收起 |
| 导航工具组 | 七个独立 Panel Package 默认组合在左侧同一个标签组。文件树面板下方是“工程语义导航”，有大纲、标签、文献、图片、问题五个标签页 |
| 编辑区 | 用文档标签打开多个文件；工具栏有保存、撤销、重做、自动换行、定位到 PDF、把当前内容发给 Chat，以及 Code / Visual 两种模式。状态栏显示行列、语言服务状态、编码和 LaTeX 状态 |
| 输出区 | 重新编译、编译选项、停止、从头构建、LaTeX 日志、PDF 下载、缩放和 PDF 预览 |

编辑区的两种模式：
- **Code**：编辑源码。编辑器优先使用浏览器内的 Monaco 代码编辑器；Monaco 加载失败时，改用普通文本框编辑。
- **Visual**：用 Portal 共享的预览组件显示当前文件的渲染结果，例如 Markdown 渲染后的效果或图片本身。打开图片、PDF 等非文本文件时只有 Visual 模式。 打开 PPT/PPTX 时，Article 原始文件入口在 viewer 授权与源码根路径检查后，将文件交给共享演示文稿转换服务，当前文件标签显示派生 PDF，支持逐页阅读与缩放；转换和缓存遵循 decision_61e50306cc79。文件读取响应仅对 `.ppt/.pptx` 明确返回 `preview_url='/api/articles/{article_id}/raw?path={urlencode(relative_path)}&preview=pdf'`，其他文件返回空值；Article 前端原样传给 PreviewDocument，通用预览组件不再从原稿 URL 推断转换接口。同内容重复打开复用缓存，文件内容改变才产生新结果。下载仍取得原始演示文稿，转换失败在预览内提示并保留下载入口，不改变文稿源码或 LaTeX 构建产物。

工具默认共用左侧标签组，源码与 PDF 继续并排显示。在工作台允许调整布局时，从组顶端的标签条将任一工具拖到其他区域的边缘，可在右侧、左侧、上方或下方建立独立区域；拖到另一组的标签条或中央则并回该组。复用 Workbench 的拖放、分隔线、隐藏和布局保存能力。各工具最小宽度为 180px，Chat 使用共享聊天组件的最小宽度；同组采用成员的约束。关闭工具只隐藏该工具，左侧图标可重新打开。再次点击已展开工具的图标时，纯工具标签组折叠到活动栏，保留原位置与宽度；工具与源码或 PDF 混放时只隐藏该工具，继续显示内容面板。点击其他工具切换并展开该工具；独立停靠的其他工具组保持原状。布局锁定时遵循 Workbench 的固定布局约束。业务流程要求打开工具（例如加入聊天、深链接导航）始终打开并聚焦，不触发收起。

Ctrl+B（macOS 为 ⌘B）收起或恢复最左侧的纯工具标签组。收起时保留活动栏入口，恢复时保留该组宽度、活动工具、源码选区及未提交输入；独立拖到右侧的 Chat 或其他工具不受影响。源码编辑器（Monaco 或备用文本框）内可使用，搜索框、聊天输入框和对话框保留各自按键行为。布局已锁定、工作台不可见或最左组包含源码/PDF 时不执行，不把内容编辑区当作侧栏隐藏。折叠状态沿既有 Workbench 布局持久化，不另建布局副本。

Workbench 按账号记忆各组位置、宽度、活动标签及隐藏/折叠状态，普通刷新保持该布局。旧版单一 navigation 面板在原位置展开为工具标签组，保留原有分区尺寸、隐藏状态和具名布局。首次访问显示文件树；链接显式带 `chat=1`、`chat=true` 或 `chat=open` 时聚焦 Chat 当前所在区域。

拖放移动现有工具根节点，不重建编辑器或聊天会话，保留搜索输入、Git 提交输入、聊天草稿和引用、源码选区及阅读位置。聊天复用共享 Chat Workspace；刷新恢复可见 Chat 时加载当前 Article 的既有对话，不发送消息。拖出 Chat 后，原工具组仍可切换文件树、Git 等内容；切换 Article 时，可见的 Chat 更新对话对象，可见的 Git 加载当前稿件的状态和记录。工具显隐由各面板生命周期独立管理，最近一次图标点击不再控制其他区域是否显示。

分隔线拖动期间，Workbench 实时分配区域尺寸，Monaco 通过自身 automaticLayout 跟随容器；Article 不再叠加逐帧的编辑器 layout 调用或重复 ResizeObserver。PDF 保留原有位图、比例与阅读位置，暂时按区域裁切，不在 resize-live 通知中遍历更新各页缩放或启动清晰重绘；进入连续调整时取消尚未执行的布局帧和重绘定时器，并使等待中的旧缩放请求失效。松开或取消拖动后，沿 Workbench 的最终尺寸通知合并一次 PDF 尺寸适配、恢复页内锚点，再延迟合并清晰重绘。普通布局切换、键盘调整和显式缩放仍使用正常的尺寸适配路径。

阅读位置在当前浏览器按账号和 Article 分开记忆：保存源码标签顺序、当前文件、每个文件的光标/选区和滚动位置，以及 PDF 的页码、页内位置和缩放比例。离开页面或切换 Article 时立即记录，编辑器和 PDF 滚动时合并记录；再次进入后重新读取最新源码，随后恢复位置，不在位置记录中保存正文或草稿。文件已删除或无权打开时跳过，当前文件不可用时回退到可用标签或主文稿；PDF 页数减少时恢复到最后可用页。首次进入、浏览器存储不可用或没有该 Article 的记录时使用默认位置。保留最近 30 篇 Article、每篇最多 50 个源码标签的位置，侧栏视图与布局继续使用各自已有的持久状态。

同一 Article 点击重新编译后，PDF 区域立即显示“正在编译…”，记录点击时的阅读页码、页内位置和缩放，暂时隐藏旧 PDF。此等待状态覆盖源码保存、编译请求和新 PDF 的加载绘制；新预览先建立各页的布局尺寸，由宿主恢复目标位置，再优先绘制目标视口所有可见页；不先绘制第一页，其余页面按视口接近情况加载。新预览在独立的隐藏容器内完成目标视口所有可见页的绘制，随后恢复滚动并显示新 PDF，首次可见时即处于原阅读位置。普通重新编译与从头构建使用同一行为，新 PDF 页数减少时回退到最后可用页。编译失败或停止时退出等待状态并显示诊断日志，旧预览仍保留；保存失败、PDF 加载或绘制失败时也退出等待并保留已有预览。切换 Article 时清除等待状态、取消过期隐藏预览，不影响新 Article。被动刷新 PDF 时仍允许在旧预览上阅读，替换前采用最新位置；首次打开不继承其他 Article 的位置，显式源码到 PDF 定位以用户指定的目标为准。

文件树标题旁的三个图标分别用于新建文件、新建目录和上传文件。点击文档标签只切换编辑内容，不保存；保存图标或 Ctrl+S（macOS 为 Cmd+S）保存当前文件后重新编译 Article，并更新右侧 PDF；撤销、重做只作用于编辑历史；自动换行只改变显示。

源码编辑区默认在停止输入 0.5 秒后自动保存到原文件，Monaco 与基础文本编辑器使用同一保存流程。自动保存不触发编译，也不排队追加编译；“编辑时自动编译”仍是用户单独开启的独立选项。自动保存不弹出成功提示，编辑器状态显示保存中、已自动保存或保存失败；失败保留草稿，可以手动保存或重试。切换文件标签后，待保存的原文件仍按自己的路径保存；关闭文件、切换 Article 后取消尚未开始的自动保存，旧请求不改写当前文档。

每个文档的自动保存、手动保存与编译前保存串行执行。保存期间继续输入的内容保留为新的草稿，在前一次保存完成后继续自动保存；只有服务器已确认的内容成为已保存基线。自动保存前读取磁盘当前内容并与基线核对，发现外部修改时暂停该文档的自动保存、保留草稿并提示用户核对后手动保存。该核对沿用文件接口的非事务语义，读写间发生的并发修改仍服从文件接口的后写生效规则。

显式保存后的编译不依赖“编辑时自动编译”开关。当前文件没有未保存修改时，保存快捷键仍可重新编译。编译前先保存该 Article 其他已打开文件的未保存修改；保存失败时保留编辑内容并显示错误，不启动本次编译。编译期间继续保存时，合并为当前编译结束后的一次后续编译，使用最新已保存内容；停止编译同时取消排队的编译。保存和编译结果始终归属于发起操作的 Article，切换文稿后旧请求不能覆盖新页面。

### 源码评论

LaTeX 编辑区提供独立的“评论”面板。Monaco 源码编辑器选中文字后，可在右键菜单选择“添加评论”，直接展开面板并进入评论输入；无需预先打开工具栏评论。操作在读取评论权限前固定原选区，等待期间切换稿件、文件或修改源码时取消本次创建。空选区或只读编辑器禁用该菜单项，实际创建仍核验 Article 协作权限。选中源码文字后可添加讨论串，评论保留作者、当地时间、引用原文和回复；列表默认显示未解决讨论，可切换已解决或全部。点击引用原文打开对应文件并定位选区，Monaco 同时高亮当前文件中的未解决评论；窄编辑区将评论面板放在编辑器下方。

评论属于 Article 的共享协作数据，由 Article 评论模块保存在 PostgreSQL 的 `comment_threads` 表中，不写入源码或 Git，也不触发构建。创建前先保存编辑器中的未保存文件，再按保存后的 UTF-16 选区偏移核对磁盘原文。评论正文限制为 6000 字符，引用选区限制为 8000 个 UTF-16 单元，每个讨论串最多保存 500 条消息。

读取沿用 Article 阅读权限；添加、回复、解决和重新打开沿用 Article 的 UpdateResource 协作权限。服务端从当前会话取得作者，只有消息作者可以编辑或删除自己的消息；删除保留消息位置并显示已删除。回复通过行锁事务追加，编辑、删除和状态变更检查讨论串版本，防止覆盖其他协作者的更新。保存失败保留输入草稿，版本冲突须刷新讨论并重新打开编辑。

评论锚点保存文件路径、选区原文及其前后文。原文前方发生修改后，通过唯一原文或前后文匹配重新定位；原文被修改、删除或存在无法区分的重复时，保留评论并标记“原文已变化或无法定位”，不猜测对应选区。评论面板与页面可见时每 5 秒读取最新讨论，另有手动刷新入口；切换 Article 时清理上一稿件的显示、草稿与未完成请求。

### 将稿件上下文加入 Chat

Article 与 Repo 采用一致的文件聊天操作。鼠标在源码编辑器内完成非空选区并松开主键后，自动把精确原文、文件路径、基准 revision、脏状态及行列范围加入当前 Article 的聊天草稿；程序定位、恢复选区和拖动中的中间状态不触发。后续鼠标选区替换上一条自动引用，用户显式加入的整文件、诊断引用及已有输入保留。自动引用不切换工具面板、不抢编辑焦点、不保存或发送；聊天未就绪时等待同一次初始化，只接纳最新选区，切换 Article 后丢弃旧请求。鼠标完成选择的识别由共享 PortalCodeEditor.bindSelectionCommit 管理，Article 的 Monaco 与基础文本编辑器使用相同机制。

“当前文件加入聊天”按钮和 Ctrl+Alt+Enter（macOS 为 ⌘⌥Enter）把当前完整编辑缓冲作为独立引用加入聊天并聚焦输入区，包括未保存修改；有选区时仍加入整份文件。整文件引用携带 Article ID、工程目录、文件路径、基准 revision、脏状态和 content，selection 为空，标签及发送文本不附带 L1 等行号。选区引用才携带行号范围。该快捷键只在源码编辑器内响应，搜索和聊天输入保留自己的按键行为；输入法组词和按键长按不重复添加。操作只加入引用，不补写提示或发送消息。内容在调用时固定，等待聊天加载期间改选或切文件不改变该快照，切换 Article 则取消添加。工具栏与辅助技术提示标明快捷键。

## 登记和选择 Article

- **链接中显式指定 Article ID 时，页面始终打开这个资源。** 规范入口为 `/resource/article?item=<Article ID>`。已在列表中时使用该记录；列表索引暂未收录时，请求 `/api/resources/resolve?address=<Article ID>`，按 HTTP 的平铺响应读取 `resource_type/root_id/target_kind/exists/record`。只有类型为 `article`、根ID与请求完全相同、目标为 Article 本身且存在时，才用 `record` 加载目录、构建配置和主文稿；HATG 的 `resource/target` 包装不作为此 HTTP 响应解析。资源不存在、无法读取或地址类型不符时保留原链接，显示加载错误。
- **没有显式 ID 时，页面恢复上次选择的有效 Article。** 上次选择已不在列表时使用列表第一项；列表为空时显示未选择状态。
- **选择器的加载提示对应实际请求。** 页面初始空壳显示选择 Article；请求列表或解析显式地址期间显示加载状态。列表为空时退出加载并显示未选择；请求失败时退出加载并显示错误，已取得的其他 Article 仍可选择。刷新列表失败时保留已打开稿件及未保存修改。

**用户在页面上选择 Project 内的文稿目录登记后，Portal 为它创建一个 Article 身份。注销 Article 按《目录与路径协议》（decision_4aa13bc21e8e）处理：注销资源身份及其范围内的嵌套登记，并将其拥有的源码目录与文件排入持久清理；删除范围外的独立资源和未登记邻居不受影响。显式登记与扫描登记采用同一生命周期，不根据登记方式推断源码保留。**

**创建者身份删除不注销 Article。** 删除创建它的 User、Resident 或 Lineage 时，保留 Article 的稳定 ID、历史署名、所属项目、全部登记根与内容；源码位于身份目录内时同时保留必要父目录。原创建者的权限随身份删除失效，其他主体继续按当前权限访问。只有明确进入 Article 或 Project 内容删除范围时，才按目录协议注销并清理；身份保留规则不自动恢复此前已注销的文稿。

1. **登记前检查。** 用户打开“登记”对话框选择目录后，页面调用 `POST /api/articles/preflight` 列出主文稿候选和问题。这一步不创建身份；本机缺少 TeX 工具、宏包或字体只作为提示，不阻止登记。
2. **提交登记。** 页面用 `RegisterResource`（资源类型 `article`）提交目录路径，可以同时指定所属 Project、主文稿和编译器。目录必须位于已登记的 Project 内，否则拒绝登记。
3. **识别主文稿。** 没有指定主文稿时，服务在目录中寻找候选文件：`.tex`、`.md`、`.markdown` 文件都可以作为候选，`README.md` 除外。包含 `\documentclass` 的完整 `.tex` 文档优先；只有 Markdown 文稿时也可以登记，但不能编译成 PDF。标题从主文稿中提取，提取不到时使用“<Project> Article”或目录名。
4. **合并变体。** 同一 Project 内标题相同的多个目录归为同一个 Article，已登记的目录按路径归回原 Article；同时对上两个 Article 时拒绝登记。登记中记录当前源码根和其余变体，匹配与写回由 Repo 和 Article 共用的 `registry_sync.py` 完成。调整源码根、变体或主文稿时，Article ID 保持不变。
5. **撤销登记。** 用户在选择器中确认永久删除所属目录与文件后，页面调用 `UnregisterResource`。服务按 decision_4aa13bc21e8e 在注销事务中删除 Article 登记、注销其范围内的嵌套登记和绑定，并持久保存所属目录的清理任务。登记注销与后台文件清理分别返回状态；文件清理失败保留任务并重试，不恢复登记。自动登记按规范目录位置保存忽略记录（资源登记表的 `auto_registration_ignore` 集合），已注销或待清理的资源不会被扫描重新登记。

**自动登记**由 ResourceService 后台发现已登记 Project 中的有效稿件目录，复用 Article 正式登记入口。发现范围、目录判定、Article 与 Repo 的分类顺序、忽略记录和调度统一见 decision_36f46b395d8e。

### 已注销 Article 的恢复证据

恢复调查可通过资源指令 `PreviewArticleRestore` 核对原 ID、真实登记工具回执、独立登记账本与保留回执、源目录及主阅读 PDF、目标 Project 内的新目录、墓碑和清理竞争状态。此入口只返回当前证据，固定 `can_apply=false`，不恢复文稿，不复活创建者 Resident。

原登记回执能证明的首次登记字段与删除前最终版本分别记录。较晚 PDF、较早 LaTeX/PPT 和后续修订片段可各自作为来源；不能据此声称最终源码完整，也不能从当前执行者推断原作者。历史来源、格式或预算导致的不可展开保留为明确缺口，不等同于原始记录不存在。

Article 按登记 Project 的当前权限方案放置。预检展示项目祖先的持久共享继承及截断情况，排除临时凭证；给申请者增加一项许可不能消除其他主体已有的继承权限。当前继承不代表原 ACL 已被找回。原作者、旧 ACL、最终稿或具体方案的真实用户决定无法核验时，结果明确保留缺口；`scope_digest` 仅供方案比较，不能作为批准凭据。参数与权限合同见 decision_ad3e21094f14，目录观察见 decision_4aa13bc21e8e。

### 原ID部分恢复的阅读入口

`RestoreArticle` 将已验证的剩余材料恢复到原 Article ID，在原 Project 内建立独立目录。主文件为 `main.md` 恢复说明，明确最终版本未找齐，并通过正式文件链接提供用户给出的旧PDF、历史LaTeX/PPT及可证修改片段。Article 主文件仍限 `.tex/.md/.markdown`，不把旧PDF直接登记为主文件，也不自动拼稿或编译成所谓最终版。

部分恢复后，经授权可将保留的 LaTeX 工作树接回 Article 根目录，并通过同根 `RegisterResource` 显式指定 `primary_file=main.tex`。主文稿与相对引用的 `sections/figs/refs` 保持一致；编译工作目录仍是 Article 根，不随嵌套主文件改变。同根重新登记保持原 ID、首次登记时间和已有 `recovery` 的独立副本，未携带该字段的普通登记不生成恢复事实；独立恢复审计不改写。显式主文件同步到该 ID 的构建设置，未指定的构建选项保留。恢复说明、历史快照及版本缺口继续保留，历史稿编译成功仅证明该工作树可编译。

真实登记工具、墓碑及原稿清理记录可共同建立原身份依据；独立账本或回执缺失保留未知原因，冲突仍拒绝。已证原标题及首次登记时间保留，原作者、历史权限和最终稿未知不补造。恢复人、时间、候选版本与证据分别记入恢复审计。

当前项目共享范围必须有真实有权用户对具体方案的明确决定；临时执行授权不变成永久ACL。现有精确拒绝保留，意外直接永久许可另行审查，不能声称申请者私有或旧ACL原样恢复。源完整递归版本、当前共享范围、权限和路径在最终提交重核；动作返回 `final_version_recovered=false`。按原链接能打开说明和旧材料才属于部分恢复交付，功能发布或预检成功均不等于文章恢复。指令合同见 decision_ad3e21094f14，事务与文件归属见 decision_4aa13bc21e8e。

部分恢复的实现分工由 ResourceService 的应用装配合同约束（decision_cf27d39e39cd）：Article 仅提供待登记文稿表示；历史工具与审批消息经应用适配器，以原执行者和精确选择器同步读取 Society 权威记录。ResourceService 保留原事务、权限锁和提交前二次复核；缺失或重复能力明确失败。该分工不授予新的恢复权限，不改变上述用户确认与部分恢复语义。

## 浏览、编辑和上传文件

**有 viewer 角色的用户可以浏览和读取文稿文件，有 contributor 角色的用户可以修改；所有文件操作都限制在源码根之内。**

- **路径边界**：服务拒绝绝对路径、包含 `..` 跳出目录的路径，以及解析后位于源码根之外的目标。文件树和搜索跳过编译生成的文件、忽略项和符号链接。
- **读取**：打开文本文件时，服务返回内容和内容摘要，单个文件最大 8 MB。图片、PDF 等文件按原始字节读取：浏览器能显示的类型直接显示，其他类型作为下载返回。
- **打开状态**：用户打开文件时，编辑器状态栏显示正在打开的具体路径；成功激活后恢复该文件的已保存或未保存状态，失败时显示无法打开的路径。TexLab 为跳转定义进行的后台文件预加载不改写当前文件的保存状态。切换 Article 后，旧文件请求的返回和失败提示不覆盖新 Article 的状态。
- **外部修改同步**：页面接收当前 Article 的资源变化通知后检查源码；页面可见时每 5 秒检查一次，以覆盖 Resident 直接修改文件的情况，回到页面时立即检查，隐藏时暂停定时检查。文件树与已打开文档使用同一个刷新调度，Git 拉取后的刷新也复用该流程。先比较文件元数据版本，变化时再读取正文并比较内容摘要；内容未变时保留编辑模型、光标与撤销历史。干净文档更新后保持标签顺序和阅读位置，已删除的干净文档关闭。有未保存修改的文档保留草稿，并对同一外部版本提示一次，用户核对后再保存；不自动合并或提交。请求期间的新编辑、保存、关闭文件或切换 Article 均使旧结果失效，网络失败由后续检查重试。
- **保存**：请求必须携带读取文件时获得的 `expected_revision`（文件字节的 SHA-256）。服务在同一父目录写锁内比较当前摘要，匹配后写入临时文件并原子替换，返回新 revision；缺失或格式不正确返回 400，过期返回 409 与当前 revision。浏览器不再为每次保存预读全文，保存失败保留草稿。保存期间继续输入只改变草稿，不改变本次提交；成功后以提交内容推进保存基线，后续输入仍待保存。外部文件变化沿既有刷新流程检查。出现冲突后，人工保存读取最新内容并展示与待提交草稿的比较；用户取消则保留草稿，明确确认后使用该次比较取得的 revision 再次条件保存。比较期间继续输入仍保持为未保存草稿；远端再次变化时继续返回冲突，不自动覆盖。
- **新建文件与目录**：可以在源码根内新建空文件或目录，目标已经存在时返回 409。Article 接口不提供移动和删除。
- **上传**：单个文件最大 25 MB，默认不覆盖已有文件。路径或格式错误返回 400，超过大小限制返回 413。
- **工程语义导航**：服务从 LaTeX 源码中提取章节、标签、引用、图片和问题，生成语义索引，用于侧栏的五个标签页。索引可以随时从源码重新生成。章节还有自己的资源地址，例如 `article_*/section/...`，它们属于同一个 Article 资源。

## 构建 PDF

**contributor 点击编译后，服务用 `latexmk` 编译主文稿，把 PDF 和日志写入构建缓存；同一个 Article 同一时间只允许一次编译。**

LaTeX 命令、进程执行、日志解析和增量判断由共享构建器持有；其实现位于 `management_common/latex_build.py` 及同层的工具链、源码识别、预检和诊断模块，不读取资源登记、权限或报告数据库。每个资源适配器持有独立的 `BuildRuntime`，活动构建与停止请求不跨适配器共享。Article 的 Project 关联与身份字段由 Article 适配器补入预检，独立预检和编译报告中的预检保持一致；Article 适配负责文稿身份、设置、主文件和报告位置，Repo 适配可以提供隔离源码副本及独立输出缓存。二者不互相登记资源，Article 的停止、草稿模式、诊断与 SyncTeX 合同保持在 Article 适配内。BibTeX 在所选主文件目录解析相对引用，辅助输出限定到本次构建目录，不因父目录参考文献放宽全局写权限。

1. **设置。** 设置面板保存以下选项，格式为 `tfi.article-build-settings.v3`，不支持的取值会被拒绝：
   - 编译器：`pdflatex`、`xelatex` 或 `lualatex`；
   - 主文稿（必须是包含 `\documentclass` 的 `.tex` 文件）；
   - 是否使用项目自己的 `.latexmkrc`、是否允许 shell escape；
   - 自动编译、编译模式（`normal` 或 `draft`，draft 只改变编译参数，不改源码）、语法检查；
   - 出错处理：`continue`（遇错继续）或 `stop`（遇错停止）。
2. **构建前检查。** 编译前，服务检查：目录中有可编译的 `.tex` 文档；主文稿存在且包含 `\documentclass`；所选编译器、`latexmk`、宏包和字体都可用。有错误级问题时不启动编译，页面禁用编译与从头构建，并显示缺失项；问题写入构建报告。

   PDF 输出区在缺少可安装的 `.cls`、`.sty`、`.bst` 文件或 TeX 字体度量 `.tfm` 文件时显示“安装缺失依赖”。用户点击后，服务重新检测该 Article 的源码依赖，以及上次失败或带诊断完成的编译日志中明确报缺的宏包和字体度量。字体度量常由宏包间接引入，例如 `pslatex` 把数学符号字体换成依赖 Zapf Chancery 的虚拟字体，源码里不出现字体名；pdfTeX 缺少度量时仍生成 PDF，构建以带诊断状态结束。服务从当前 TeX Live 仓库按文件查找唯一正式包，使用本机 `tlmgr` 安装；文稿与客户端不能指定命令、仓库或任意包名。安装使用共享工具链互斥锁，页面显示安装状态；失败保留原因并允许重试。安装完成重新预检，通过后自动重新编译并更新当前 Article 的 PDF。没有 `tlmgr`、缺少系统字体或无法唯一找到包时说明未解决项，交由管理员处理；不会在打开页面、保存文件或后台预检时自动下载安装。切换 Article 后在途安装不更新其他文稿的页面。
3. **编译。** 服务在受控环境中运行 `latexmk`，辅助文件、日志、PDF 和报告都写入该 Article 的构建缓存。请求一直等到编译结束才返回，单次编译最长 120 秒。latexmk 只跟踪文稿自身的文件：上一次构建失败、停止或编译器返回非零后，源码未变时它会直接报告“Nothing to do”和上次的错误。因此在这些情况下服务加 `-g` 强制重跑一次，安装缺失宏包或字体后点击编译即可得到真实结果；上一次成功后照常增量编译。普通编译保留缓存中的有效 PDF、辅助文件和依赖记录，让 latexmk 对未变化的稿件直接复用产物；草稿模式的包装文件只在内容变化时重写，避免其修改时间触发无效重编。源码或构建输入发生变化时由 latexmk 正常重编。从头构建继续清空缓存。命令返回非零且缓存 PDF 未更新时按失败处理，不把旧文件作为本次新结果发布；遇错继续模式确实生成了新 PDF 时，保留带诊断的结果。
4. **并发。** 同一个 Article 已有编译在进行时，新的编译请求返回 422 和“LaTeX compilation is already running”。“停止”按钮结束正在进行的编译进程组。“从头构建”先清空这个 Article 的构建缓存，再重新编译。
5. **结果。** 编译失败时返回 422，附带日志最后 12,000 个字符。构建报告记录编译器退出码、检查结果和解析出的诊断。本次失败不影响上一次成功的 PDF：页面退出“正在编译…”状态并显示本次诊断日志，用户可返回查看保留的旧 PDF。
6. **刷新页面。** 页面刷新后从构建报告恢复构建状态。PDF 地址总是指向最近一次成功的 PDF；从未成功构建时返回 404。

## 在源码和 PDF 之间定位

**contributor 可以从源码行跳到 PDF 中对应的位置，也可以从 PDF 中的位置跳回源码。**

- 正向定位提交文件、行和列，返回 PDF 页码和坐标；反向定位提交页码和坐标，返回源码根内的文件、行和列。
- 没有安装 `synctex` 时，页面禁用源码/PDF 双向定位并在相应位置说明缺少工具；编辑和 PDF 阅读仍可使用。没有 PDF 或 SyncTeX 文件时接口返回 404；文件存在但找不到对应位置时返回 422。
- SyncTeX 结果中位于源码根之外的文件会被过滤掉，不返回给页面。

## 语言辅助

**编辑 `.tex`、`.bib`、`.sty`、`.cls` 文件时，页面通过 TexLab 提供补全、悬停说明、跳转定义、符号列表和诊断；TexLab 不可用时编辑照常进行。**

- 页面通过一个接口向 TexLab 发出七种请求：`status`（是否可用及原因）、`sync`（同步当前内容）、`diagnostics`、`completion`、`hover`、`definition`、`document_symbols`。
- 除 `status` 外，每个请求都带上编辑器中的当前内容和版本号，结果对应用户正在编辑的内容，即使它还没有保存。
- 每个 Article 源码根使用一个 TexLab 进程。源码根变化或进程退出时，服务重新启动它，避免两个文稿共用一个进程。
- TexLab 主动推送的诊断只保存在进程内，Portal 重启后由下一次 `sync` 重新产生。
- 参数错误或 TexLab 响应超时返回 400；TexLab 无法启动或不可用时返回 503，`status` 请求始终返回 `available` 和原因。TexLab 失败不会影响页面中的编辑内容。

### 语言服务的操作归属与回收

TexLab 由 Article 后端持有并通过共享进程运行器管理整个原生进程树。有界资源操作中的语言服务按“资源生命周期、Article”复用；不同操作以及普通编辑器各自保持独立的文档与诊断状态，不能把首个请求的生命周期租约转移给其他调用者。普通编辑器的语言服务由 Web 生命周期回收。

所属资源生命周期进入关闭后，Article 后端等待当前语言请求结束，拒绝重新启动该会话，关闭 LSP 连接并回收全部原生后代，然后释放管理记录。清理失败保留进程所有权并重试；不能因为请求已返回、浏览器已关闭或数据库连接已释放就认定语言服务结束。资源生命周期封口等待语言服务释放同一执行租约，不清除租约文件绕过仍存活的进程。读取已有文稿不转移文稿所有权，回收语言服务不删除文稿或影响其他编辑器。

## Article 范围的 Git

**Git 面板显示和提交源码根内的变化；同一个仓库中源码根之外的文件不会被带进提交。** 面板采用源代码管理的日常工作顺序：审阅更改、暂存、提交、与远端交换提交，再查看分支图。

- viewer 可以查看状态、差异和历史；所有写操作由当前会话用户的 Article contributor 权限核验。尚未纳入 Git 的文稿可初始化本地仓库，初始化不把目录登记为 Repo。
- “更改”和“已暂存的更改”分组可折叠，显示文件名、目录、状态与数量。每个文件可打开源码、查看对应侧的差异、暂存或取消暂存；两组支持批量暂存或取消。工作区差异比较暂存区与磁盘，已暂存差异比较 HEAD 与暂存区，部分暂存文件分别显示两侧。
- 放弃操作先在页面确认，再仅恢复所选文件的未暂存修改；已暂存内容保留。未跟踪文件会被删除，目录、子模块和冲突文件不在此操作范围内。路径必须位于源码根。
- 提交按钮和 Ctrl/Cmd+Enter 提交已暂存更改。下拉菜单提供“暂存全部并提交”“提交并推送”“提交并同步”；后续远端操作失败时本地提交保留并明确显示结果。提交说明为单行、最多 200 个字符。提交前检查整个暂存区，若包含源码根之外的文件则拒绝提交。
- 独立仓库提供获取、拉取、推送、同步，以及创建和切换本地分支。操作目标为当前分支的 upstream；只有一个远端且未配置 upstream 时使用同名分支。页面显示远端、分支及本地已知的领先/落后提交数，获取后更新这些计数。获取不修改工作文件，推送发送本地提交并设置 upstream，同步依次拉取再推送。
- 拉取仅 fast-forward，推送不强制覆盖远端。分叉或网络失败保留当前状态并显示 Git 错误；不自动合并、变基或隐藏工作区更改。拉取、同步、分支切换要求工作区干净。Article 是更大仓库的子目录时，这些仓库级操作交给对应 Repo 页面，以免影响源码根之外的内容。
- 远端操作复用当前用户的 Git 凭据。认证失败时通过页面输入用户名与密码或访问令牌，只有明确勾选“记住”才持久保存；凭据不写入稿件、远端 URL 或日志。
- 图表从 Git 历史的父提交关系绘制分支和合并，显示作者、摘要、短提交号及本地/远端分支标记，点击提交查看稿件范围内的差异；按需加载更多，最多展示最近 500 条。源码根是子目录时，历史限定于该目录。提交时间统一按浏览器所在时区显示，并标明本地时间和 UTC 偏移；原始提交记录不作修改。获取、拉取、推送与同步使用不同的图标，悬停说明保留操作效果与不可用原因。
- Git 变更请求按当前 Article 串行执行，避免按钮重复触发。编辑器有未保存草稿时，提交、拉取、同步、切换分支和放弃操作要求先保存。工作文件变更完成后复用 Article 内容刷新流程，更新文件树、历史、语义索引和干净文档；请求期间新产生的草稿保留，切换 Article 后旧响应不能更新新页面。

## 组件分工

| 组件 | 位置 | 职责 | 出错后 |
| --- | --- | --- | --- |
| Article 工作区页面 | `management_portal/article_workspace.html`、`static/article_workspace.js` | 组合工作台、文章会话及领域服务，通过面板公开接口连接导航、编辑、编译和聊天 | 操作失败保留当前文稿及反馈 |
| Article 工具面板 | `static/article_workspace_tools.js` | 登记 `article-files`、`article-favorites`、`article-search`、`article-git`、`article-chat`、`article-settings`、`article-library` 单实例 Panel Package；持有工具根节点与激活/显隐/销毁生命周期，使用注入的 Article 会话服务；`article_workspace_layout.js` 负责组合、图标聚焦及旧布局迁移 | 激活失败保留工具内容并提示；拖动不销毁实例 |
| Article 源码面板 | `static/article_workspace_monaco.js` | `article-editor` Panel Package 持有模板生成的 DOM、编辑器实例、事件、尺寸监听和视图状态，公开编辑与保存接口；移动和隐藏保持实例，销毁释放监听与模型 | 初始化失败沿用文本编辑器；冲突比较取消保留草稿 |
| Article 文档 | `static/article_workspace_documents.js` | 每份文档持有唯一草稿、保存基线及 revision，统一所有保存入口；页面与 DocumentSession 不再复制维护草稿 | 保存失败保留草稿；在途新输入不被保存响应覆盖 |
| Article 输出面板 | `static/article_workspace_pdf.js` | `article-output` Panel Package 持有模板生成的 DOM、PDF 控制器、缩放、阅读位置、事件、尺寸监听和清理；通过明确上下文与编译/SyncTeX 服务联动 | 失败显示错误；旧异步渲染不覆盖新文稿 |
| Article 登记提供者 | ResourceService 中的 `resources/providers/article.py` | 登记、按目录协议注销并排入所属文件清理、维护自动登记忽略列表 | 登记已注销而文件清理失败时，保留持久任务并重试 |
| Article 后端 | 14 个 `article_*.py` 模块：文件读写与搜索 `article_files`、设置 `article_settings`、语义索引 `article_semantic_index`、构建 `article_build*`、SyncTeX `article_synctex`、Git `article_git`、登记 `article_registration` 等 | 源码根边界、文件读写、搜索、设置、语义索引、构建、SyncTeX 和 Git | 临时文件替换保存；语义索引可重建 |
| Article 构建适配 | `article_build.py`、`article_build_profile.py`、`article_build_results.py` | 解析 Article 身份、设置、Project 关联、缓存租约与报告位置，持有独立运行状态 | 每个 Article 最多一次编译；停止范围不影响 Repo 构建 |
| 共享构建机制 | `management_common/latex_build.py`、`latex_toolchain.py`、`latex_profile.py`、`latex_source.py`、`latex_diagnostics.py` | 按调用方提供的源目录、主文件、输出、缓存与报告回调执行构建 | 统一处理进程树、超时、增量与日志；失败保留上次有效 PDF，报告区分本次结果 |
| TexLab 管理 | `article_texlab_backend.py` | 按资源生命周期与 Article 复用语言服务；普通编辑器独立由 Web 生命周期持有，同步编辑内容与诊断 | 开放生命周期内可重建退出或源码根变化的服务；关闭后等待当前请求、禁止重启并回收进程树，失败保留所有权重试 |
| 自动登记 | `resource_auto_registration.py` | 按 decision_36f46b395d8e 发现稿件并调用正式登记 | 跳过忽略列表中的路径 |
| 源码评论 | `article_comments.py`、`static/article_workspace_comments.js` | 共享讨论串、源码选区定位、回复与解决状态 | 事务保存；冲突保留草稿，无法定位时保留原始引用 |
| HTTP 接口 | `http_articles.py` | 校验 Article ID 与角色，调用后端，统一错误码 | — |

```text
article_* ─→ Article 登记确定源码根 ─→ 文件树与编辑内容 ─→ 保存到源码
                                      │
                                      ├─→ 构建设置 → 构建前检查 → latexmk → 构建缓存
                                      │                                     ↓
                                      │                          构建报告 + 最近成功的 PDF
                                      │                                     ↕ SyncTeX
                                      ├─→ TexLab：补全、定义、诊断（可重新生成）
                                      └─→ Git：只暂存源码根内路径，提交前检查整个暂存区
```

## HTTP 接口

以下接口中的 `{id}` 是 `article_*` 形式的 Article ID，格式不符时返回 404。读取类接口要求 viewer 角色，修改类接口要求 contributor 角色。

| 方法与路径 | 作用 |
| --- | --- |
| `GET /api/articles` | 当前用户可见的 Article 列表 |
| `GET /api/articles/{id}/comments` | 共享评论讨论串及当前用户的评论权限 |
| `POST /api/articles/{id}/comments` | create、reply、resolve、reopen、edit、delete；修改和状态变更带讨论串 version，冲突返回 409 |
| `GET /api/articles/{id}/files` | 源码树 |
| `GET /api/articles/{id}/file?path=` | 文本内容和内容摘要（加 `meta=1` 只返回文件信息） |
| `GET /api/articles/{id}/raw?path=` | 原始字节；PPT/PPTX 加 `preview=pdf` 返回受保护的转换 PDF；`download=1` 优先下载原文件 |
| `GET /api/articles/{id}/search?q=` | 在源码根内搜索 |
| `GET /api/articles/{id}/settings` | 构建设置 |
| `GET /api/articles/{id}/build` | 构建报告和编译状态 |
| `GET /api/articles/{id}/semantic` | 工程语义索引 |
| `GET /api/articles/{id}/preflight` | 当前源码根的构建前检查 |
| `GET /api/articles/{id}/pdf` | 最近一次成功的 PDF |
| `GET /api/articles/{id}/git`、`/git/diff`、`/git/history` | 源码根相关的 Git 状态、改动对照和历史 |
| `POST /api/articles/preflight` | 检查一个尚未登记的目录，不创建身份 |
| `POST /api/articles/{id}/file` | 保存文本 |
| `POST /api/articles/{id}/entries` | 新建空文件（`kind: file`）或目录（`kind: directory`） |
| `POST /api/articles/{id}/upload` | 上传一个文件 |
| `POST /api/articles/{id}/settings` | 保存构建设置 |
| `POST /api/articles/{id}/install-dependencies` | 显式安装服务端检测到的缺失 TeX 包，正文为空对象；返回安装包清单和最新预检 |
| `POST /api/articles/{id}/compile` | 编译，可带 `from_scratch: true` 从头构建 |
| `POST /api/articles/{id}/stop` | 停止正在进行的编译 |
| `POST /api/articles/{id}/synctex/source-to-pdf`、`/synctex/pdf-to-source` | 正向和反向定位 |
| `POST /api/articles/{id}/texlab` | TexLab 请求，`action` 取上述七种之一 |
| `POST /api/articles/{id}/git/{action}` | init、stage、commit、discard、branch、fetch、pull、push、sync；写操作核验 Article 协作权限，认证失败返回 409 和结构化 challenge |

登记和撤销登记不使用 HTTP 地址，而是资源指令 `RegisterResource`（`resource_type: article`，字段 `path`、可选的 `project_ids`、`primary_file`、`compiler`）和 `UnregisterResource`。
