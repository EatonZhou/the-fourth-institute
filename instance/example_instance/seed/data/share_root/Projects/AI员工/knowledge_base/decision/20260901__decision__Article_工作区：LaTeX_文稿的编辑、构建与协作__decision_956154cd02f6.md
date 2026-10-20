---
kb_id: decision_956154cd02f6
type: decision
project: AI员工
created_at: '2026-09-01'
version:
  number: 8
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
  - ${code_root}/management_portal/static/portal_resource_registration_service.js
  - ${code_root}/management_portal/resource_auto_registration.py
  - ${code_root}/management_portal/resources/providers/article.py
  - ${code_root}/management_portal/http_articles.py
  - ${code_root}/management_portal/article_identity_backend.py
  - ${code_root}/management_portal/registry_sync.py
  - ${code_root}/management_portal/article_texlab_backend.py
  - ${code_root}/management_portal/article_workspace.html
  - ${code_root}/management_portal/static/article_workspace.js
  - ${code_root}/management_portal/repo_workspace_backend.py
  - ${code_root}/management_portal/serve_management_portal.py
  - ${code_root}/tests/cases/test_article_identity_backend.py
  related:
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
- **构建缓存**：默认位于 `portal_paths.path('staging_root') / 'shared/portal_cache/article_builds'`，可用 `PORTAL_ARTICLE_BUILD_ROOT` 显式设置；只读取规范环境键，每个 Article 一个独立目录。编译产生的辅助文件、日志和 PDF 都写在这里，不写进源码目录；缓存可以随时删除，下次编译重建。
- **构建报告**：一次构建的持久记录，格式为 `tfi.article-build.v1`，内容包括构建前检查的结果、编译器退出码、诊断信息、本次是否成功，以及上一次成功的 PDF 是否仍可用。
- **诊断**：构建或语言检查给出的一条问题，带有严重程度和类别。
- **SyncTeX**：LaTeX 编译时生成的源码与 PDF 位置对照表。从源码的行和列找到 PDF 中的页和坐标称为正向定位，反过来称为反向定位。
- **TexLab**：为 LaTeX 提供补全、悬停说明、跳转定义、符号列表和诊断的语言服务进程。编辑器通过 LSP（Language Server Protocol，语言服务协议）与它通信。
- **内容摘要（revision）**：读取文件时返回的 SHA-256 值，根据文件内容计算，用来识别用户看到的是哪个版本。

## 页面组成

### 顶部 Article 选择区

顶部有 Article 搜索框（按标题、Project 或 ID 搜索）和“语义检索”按钮，旁边是当前 Article 选择器和“登记”按钮（文件加号图标）。选择器展开后可以搜索已登记的 Article、切换当前文稿，或撤销当前 Article 的登记。

### 工作台

| 区域 | 内容 |
| --- | --- |
| 左侧图标栏 | 六个图标，依次切换侧栏内容：文件树、全项目搜索、源代码管理（Git）、Chat Workspace、编辑器与 LaTeX 设置、Article 登记管理 |
| 侧栏 | 显示当前图标对应的面板。文件树面板下方是“工程语义导航”，有大纲、标签、文献、图片、问题五个标签页 |
| 编辑区 | 用文档标签打开多个文件；工具栏有保存、撤销、重做、自动换行、定位到 PDF、把当前内容发给 Chat，以及 Code / Visual 两种模式。状态栏显示行列、语言服务状态、编码和 LaTeX 状态 |
| 输出区 | 重新编译、编译选项、停止、从头构建、LaTeX 日志、PDF 下载、缩放和 PDF 预览 |

编辑区的两种模式：
- **Code**：编辑源码。编辑器优先使用浏览器内的 Monaco 代码编辑器；Monaco 加载失败时，改用普通文本框编辑。
- **Visual**：用 Portal 共享的预览组件显示当前文件的渲染结果，例如 Markdown 渲染后的效果或图片本身。打开图片、PDF 等非文本文件时只有 Visual 模式。

文件树标题旁的三个图标分别用于新建文件、新建目录和上传文件。点击文档标签只切换编辑内容，不保存；保存图标把当前内容写入文件；撤销、重做只作用于编辑历史；自动换行只改变显示。

## 登记和选择 Article

- **链接中显式指定 Article ID 时，页面始终打开这个资源。** 已在列表中时使用该记录；列表索引暂未收录时，通过 ResourceService 直接解析已登记资源，再加载其目录、构建配置和主文稿。资源不存在、无法读取或地址类型不符时保留原链接，显示加载错误。
- **没有显式 ID 时，页面恢复上次选择的有效 Article。** 上次选择已不在列表时使用列表第一项；列表为空时显示未选择状态。

**用户在页面上选择 Project 内的文稿目录登记后，Portal 为它创建一个 Article 身份；撤销登记只删除登记，保留全部源码。**

1. **登记前检查。** 用户打开“登记”对话框选择目录后，页面调用 `POST /api/articles/preflight` 列出主文稿候选和问题。这一步不创建身份；本机缺少 TeX 工具、宏包或字体只作为提示，不阻止登记。
2. **提交登记。** 页面用 `RegisterResource`（资源类型 `article`）提交目录路径，可以同时指定所属 Project、主文稿和编译器。目录必须位于已登记的 Project 内，否则拒绝登记。
3. **识别主文稿。** 没有指定主文稿时，服务在目录中寻找候选文件：`.tex`、`.md`、`.markdown` 文件都可以作为候选，`README.md` 除外。包含 `\documentclass` 的完整 `.tex` 文档优先；只有 Markdown 文稿时也可以登记，但不能编译成 PDF。标题从主文稿中提取，提取不到时使用“<Project> Article”或目录名。
4. **合并变体。** 同一 Project 内标题相同的多个目录归为同一个 Article，已登记的目录按路径归回原 Article；同时对上两个 Article 时拒绝登记。登记中记录当前源码根和其余变体，匹配与写回由 Repo 和 Article 共用的 `registry_sync.py` 完成。调整源码根、变体或主文稿时，Article ID 保持不变。
5. **撤销登记。** 用户在选择器中撤销登记时，页面调用 `UnregisterResource`。服务删除 Article 登记，保留源码，并把该文稿的主文稿路径加入自动登记的忽略列表（资源登记表的 `auto_registration_ignore` 集合），自动登记不会马上把它重新登记回来。之后用户手动登记同一目录时，服务从忽略列表中移除这些路径。

**自动登记**由 ResourceService 后台发现已登记 Project 中的有效稿件目录，复用 Article 正式登记入口。发现范围、目录判定、Article 与 Repo 的分类顺序、忽略记录和调度统一见 decision_36f46b395d8e。

## 浏览、编辑和上传文件

**有 viewer 角色的用户可以浏览和读取文稿文件，有 contributor 角色的用户可以修改；所有文件操作都限制在源码根之内。**

- **路径边界**：服务拒绝绝对路径、包含 `..` 跳出目录的路径，以及解析后位于源码根之外的目标。文件树和搜索跳过编译生成的文件、忽略项和符号链接。
- **读取**：打开文本文件时，服务返回内容和内容摘要，单个文件最大 8 MB。图片、PDF 等文件按原始字节读取：浏览器能显示的类型直接显示，其他类型作为下载返回。
- **保存**：保存时服务先写入临时文件再替换原文件，读者不会看到写了一半的内容。保存接口不比较内容摘要，两人同时保存同一文件时，后保存的内容生效。保存失败时页面保留未保存的编辑内容并显示错误。
- **新建文件与目录**：可以在源码根内新建空文件或目录，目标已经存在时返回 409。Article 接口不提供移动和删除。
- **上传**：单个文件最大 25 MB，默认不覆盖已有文件。路径或格式错误返回 400，超过大小限制返回 413。
- **工程语义导航**：服务从 LaTeX 源码中提取章节、标签、引用、图片和问题，生成语义索引，用于侧栏的五个标签页。索引可以随时从源码重新生成。章节还有自己的资源地址，例如 `article_*/section/...`，它们属于同一个 Article 资源。

## 构建 PDF

**contributor 点击编译后，服务用 `latexmk` 编译主文稿，把 PDF 和日志写入构建缓存；同一个 Article 同一时间只允许一次编译。**

1. **设置。** 设置面板保存以下选项，格式为 `tfi.article-build-settings.v3`，不支持的取值会被拒绝：
   - 编译器：`pdflatex`、`xelatex` 或 `lualatex`；
   - 主文稿（必须是包含 `\documentclass` 的 `.tex` 文件）；
   - 是否使用项目自己的 `.latexmkrc`、是否允许 shell escape；
   - 自动编译、编译模式（`normal` 或 `draft`，draft 只改变编译参数，不改源码）、语法检查；
   - 出错处理：`continue`（遇错继续）或 `stop`（遇错停止）。
2. **构建前检查。** 编译前，服务检查：目录中有可编译的 `.tex` 文档；主文稿存在且包含 `\documentclass`；所选编译器、`latexmk`、宏包和字体都可用。有错误级问题时不启动编译，页面禁用编译与从头构建，并显示缺失项；问题写入构建报告。
3. **编译。** 服务在受控环境中运行 `latexmk`，辅助文件、日志、PDF 和报告都写入该 Article 的构建缓存。请求一直等到编译结束才返回，单次编译最长 120 秒。
4. **并发。** 同一个 Article 已有编译在进行时，新的编译请求返回 422 和“LaTeX compilation is already running”。“停止”按钮结束正在进行的编译进程组。“从头构建”先清空这个 Article 的构建缓存，再重新编译。
5. **结果。** 编译失败时返回 422，附带日志最后 12,000 个字符。构建报告记录编译器退出码、检查结果和解析出的诊断。本次失败不影响上一次成功的 PDF：页面继续显示旧 PDF，同时标明本次构建失败。
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

## Article 范围的 Git

**Git 面板只显示和提交源码根内的变化，同一个 Git 仓库中源码根之外的文件不会被带进提交。**

- viewer 可以查看源码根相关的状态、改动对照和提交历史；contributor 可以执行初始化、暂存（或取消暂存）和提交。
- 文稿目录还不在 Git 仓库中时，contributor 可以在页面上初始化 Git。初始化不会把目录登记为 Repo。
- 暂存只接受源码根内的路径。提交前，服务检查整个暂存区：只要其中有源码根之外的文件，就拒绝提交。
- 提交说明最多 200 个字符。路径越界、暂存区包含源码根之外的文件等被拒绝的请求返回 409；Git 冲突和命令错误按 Git 的原始结果返回，页面如实显示失败。

## 组件分工

| 组件 | 位置 | 职责 | 出错后 |
| --- | --- | --- | --- |
| Article 工作区页面 | `management_portal/article_workspace.html`、`static/article_workspace.js` | 图标栏、侧栏、编辑区、输出区、未保存内容和操作反馈 | 保存失败保留编辑内容；刷新后重新读取持久状态 |
| Article 登记提供者 | ResourceService 中的 `resources/providers/article.py` | 登记、撤销登记、维护自动登记忽略列表 | 撤销登记保留源码 |
| Article 后端 | `article_identity_backend.py` | 源码根边界、文件读写、搜索、设置、语义索引、构建、SyncTeX 和 Git | 临时文件替换保存；语义索引可重建 |
| 构建执行 | `article_identity_backend.py` 中的编译部分 | 每个 Article 最多一次编译、写构建缓存与报告 | 停止结束进程组；报告保留失败原因；从头构建只清空目标缓存 |
| TexLab 管理 | `article_texlab_backend.py` | 每个源码根一个 TexLab 进程、同步编辑内容 | 进程退出或源码根变化时重启；下次 `sync` 恢复诊断 |
| 自动登记 | `resource_auto_registration.py` | 按 decision_36f46b395d8e 发现稿件并调用正式登记 | 跳过忽略列表中的路径 |
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
| `GET /api/articles/{id}/files` | 源码树 |
| `GET /api/articles/{id}/file?path=` | 文本内容和内容摘要（加 `meta=1` 只返回文件信息） |
| `GET /api/articles/{id}/raw?path=` | 原始字节，浏览器能显示的类型直接显示，加 `download=1` 强制下载 |
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
| `POST /api/articles/{id}/compile` | 编译，可带 `from_scratch: true` 从头构建 |
| `POST /api/articles/{id}/stop` | 停止正在进行的编译 |
| `POST /api/articles/{id}/synctex/source-to-pdf`、`/synctex/pdf-to-source` | 正向和反向定位 |
| `POST /api/articles/{id}/texlab` | TexLab 请求，`action` 取上述七种之一 |
| `POST /api/articles/{id}/git/init`、`/git/stage`、`/git/commit` | 初始化、暂存与提交 |

登记和撤销登记不使用 HTTP 地址，而是资源指令 `RegisterResource`（`resource_type: article`，字段 `path`、可选的 `project_ids`、`primary_file`、`compiler`）和 `UnregisterResource`。
