---
kb_id: decision_6476c69b33da
type: decision
project: AI员工
created_at: '2026-09-01'
version:
  number: 24
  created_at: '2026-09-01'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-20T00:02:52Z'
  updated_by:
    user: ''
    agent: resident_83d75ad3a6c7
importance: 9
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/resources/providers/repo.py
  - ${code_root}/management_portal/http_repo.py
  - ${code_root}/management_portal/repo_workspace_backend.py
  - ${code_root}/management_portal/repo_credentials_backend.py
  - ${code_root}/management_portal/repo_workspace.html
  - ${code_root}/management_portal/static/repo_workspace.js
  - ${code_root}/management_portal/resource_identity_backend.py
  - ${code_root}/management_portal/registry_sync.py
  - ${code_root}/management_portal/serve_management_portal.py
  - ${code_root}/tests/cases/test_resource_identity_backend.py
  - ${code_root}/tests/cases/test_repo_workspace_backend.py
  - ${code_root}/tests/cases/test_repo_credentials_backend.py
  - ${code_root}/tests/cases/test_exported_repo_source.py
  related:
  - decision_36f46b395d8e
  - decision_ad3e21094f14
  - decision_b6e74d78f7b6
  - decision_34158fdd4078
  - decision_6c2f91a7d84e
  - decision_956154cd02f6
  supersedes: []
  derived_from: []
---
# Repo：代码库登记、工作区与 Git 操作

Repo 页面（`/resource/repo`）是 Portal 中管理代码库的地方。Repo 是 repository（代码库）的简称；Git 是记录文件版本和协作历史的系统。页面分两层：**Repo Board** 列出当前用户有权访问的代码库；**Repo Workspace** 打开代码库的实际内容根，用户可以浏览目录、README 和文件；Git 来源还支持查看改动、阅读提交历史、切换分支、提交和与远端同步。

Repo 也可以明确登记为不附带 Git 工作副本的导出源码树：它保留统一 ID 与资源文件地址，同时提供 ResourceService 文件读取和 Repo Workspace 只读浏览。文件编辑、提交、历史、分支和远端操作只适用于 Git 来源；导出源码不会因所在目录或祖先存在 `.git` 而获得这些能力。

本 Design 说明 Repo 的身份与登记、两类内容来源、两个页面、文件与 Git 操作的边界，以及每位用户私有的远端凭据。Article 也使用 Git，但只作用于文稿目录，见 decision_956154cd02f6；资源登记指令见 decision_ad3e21094f14。

## 核心概念

- **Repo**：一个代码库在 Portal 中的登记，身份为 `repo_<12 位十六进制>`。代码库移动位置或更换远端时，ID 不变。
- **Git checkout**：磁盘上含有 `.git` 的实际工作目录。Git 来源的 Repo 可以登记多个 checkout，登记中选定其中一个作为**主 checkout**；Repo Workspace 的全部操作都在主 checkout 内进行。导出源码复用 `canonical_checkout` 字段表达唯一内容根，不表示本地 Git 能力。
- **Repo 登记**：PostgreSQL 资源登记表 `repo` 集合中每个 Repo 一条记录，采用 `tfi.repo.v1`，保存来源类型、内容根，以及适用时的 checkout、远端、所属 Project、简介、推送策略和 SSH 私钥引用。
- **远端（remote）**：登记的远程仓库。`fetch` 取回远端信息，`pull` 把远端变化合并到当前分支，`push` 把本地提交发送到远端。
- **推送策略**：登记中的开关，决定自动提交之后是否继续 push。它不绕过远端自己的权限。
- **Git 凭据**：每位 Portal 用户私人保存的远端认证信息（HTTPS 的访问令牌或 SSH 口令），新建格式为 `tfi.repo-git-credential.v1`，认证解密后仅接受该精确格式，不改验密算法或回写原 token。凭据加密目录由 `PORTAL_REPO_CREDENTIAL_ROOT` 配置，缺省为 `portal_paths` 的 `secrets_dir/repo-git-credentials`；密钥路径由 `PORTAL_REPO_CREDENTIAL_KEY_PATH` 配置，缺省为 `secrets_dir/repo-git-credentials.master-key`。环境变量由公共产品环境读取器只按规范的 `PORTAL_*` 名称读取，任何接口都不返回其中的秘密内容，只返回凭据 ID 和脱敏的匹配信息。
- **Git 常用词**：分支（branch）是一条可以切换的提交线；标签（tag）标记一个提交；`HEAD` 是当前检出的位置；diff 是文件变化；blame 逐行显示最后修改它的提交和作者；已跟踪（tracked）文件是 Git 在管理的文件，未跟踪（untracked）文件是新出现、还没加入 Git 的文件。

## 内容来源与稳定文件地址

`source_kind` 是 Repo 记录中的稳定语义，不是临时安装开关。增加该字段不改变 `tfi.repo.v1`、原 Repo ID 或 `repo_<id>/file/<相对路径>` 地址。

| 来源声明 | 内容根与存活 | 允许的能力 |
| --- | --- | --- |
| 省略 `source_kind`，或值为 `git_checkout` | 沿用有效 Git 工作副本规则；任一副本有效即可保留登记 | 资源文件读取及原有 Repo Git 工作区能力 |
| `source_kind: exported_tree` | 必须明确提供非空、绝对路径 `canonical_checkout`；只认这个目录，不要求 `.git`，不回退其他 checkout | 通过 ResourceService 读取根内文件，并在 Repo Workspace 浏览概览、目录、README 和文件；文本与 YAML 均可读 |
| 其他值，包括空值 | 拒绝该声明；核对时保留原记录并报告验证错误 | 不猜测来源，不降级或自动改类型 |

导出/导入生成端须明确写入 `source_kind: exported_tree`、安装后物化的唯一 canonical 根，以及 `checkouts: [{"path": 同一规范根}]`；清空本机 `remotes` 和 SSH 私钥引用，关闭推送。checkout 不携带来源仓库的 `git_common_dir`、`head` 等本机 Git 状态。需要保留的来源版本可以放在普通溯源字段中，不参与本机身份匹配或能力判断；源记录不因此被改写。模板中的安装路径变量必须先物化为目标绝对路径，运行时不解释占位符。

导出源码按以下边界读取和维护：

- `repo_<id>/file/<相对路径>` 沿用 ResourceService 的身份、权限、路径校验与根目录包含检查；绝对路径、向上越界或经符号链接逃出根目录的访问被拒绝。导出类型不放宽匿名或无权读取。
- `repo_<id>/commit/<40 位 SHA>/file/<相对路径>`、文件写入、diff、历史、分支、提交、同步和远端设置明确拒绝导出源码。即使根自身或祖先存在有效 `.git`，也不借用该仓库提供能力；只读浏览不生成虚假的分支、提交、干净工作区或同步状态。
- 来源类型和根由 Repo 领域统一判断。导出身份只以 canonical 路径参与登记匹配；残留的来源远端、Git 目录或其他 checkout 不参与。普通 Git 自动扫描或手动登记不能把它静默合并、改类为 Git Repo。
- 根缺失或不再是目录时，按正式资源注销流程处理；权限、读取错误或声明损坏不作为已删除事实。自动清理与 Article 分类边界见 `decision_36f46b395d8e`。普通 Git 的失效规则保持不变。
- 这一合同不要求创建伪 `.git`，不关闭自动核对，不按某个 Repo ID 特判存活，也不通过修改源记录或目标业务表绕过正式登记流程。

## Repo Board

| 区域 | 内容 |
| --- | --- |
| 工具栏 | 搜索框（按名称、简介、Project 或 ID）、语义检索、Project 筛选、刷新、“全部 AI 提交”、Board / Workspace 切换 |
| Repo 卡片 | ID、名称、Project、最多两行简介、远端、最近变化和自动提交状态，以及打开 Workspace 的入口 |
| 注册窗口 | 选择 checkout、Project，以及是否初始化 Git |

- 列表从资源索引分页读取（每页最多 500 条），直到取得当前用户可见的全部 Repo。搜索或筛选条件改变时，页面取消旧请求，旧结果不会覆盖新结果。
- 自动提交状态每批最多读取 120 个 Repo。状态或简介读取失败时，卡片照常显示，并提示该部分不可用。缺少简介的 Repo 由页面批量请求生成简介。
- 配置远端地址只证明远端已登记。卡片显示“已配置远程”，不能仅凭 ahead/behind 缺省或为零宣称“已同步”；领先、落后数量仍按本地 Git 状态显示。
- “全部 AI 提交”为当前列出的、有改动的 Repo 逐个执行 AI 提交（见下文）。
- 卡片高度由内容决定：简介最多两行，同一行卡片可以对齐，但不为对齐留出大块空白。

## Repo Workspace

Workspace 按概览声明的能力呈现操作。Git 来源顶部是三条紧凑的工具带：Repo 身份和全局操作、五个标签页、当前分支与文件操作。空间足够时每条保持一行，不够时才换行，可用操作保持可见。

导出源码只显示 Code 浏览、目录与文件导航、README、文件预览和内容根。Git 专有标签、分支切换、提交、同步、History、Blame 及新建、编辑、删除入口隐藏，相关快捷键不发出请求。旧链接若指定 Git 专有标签，页面先取得概览能力，再回到 Code，继续按原目录或文件浏览。界面隐藏不能代替后端拒绝。

| 标签 | 内容 |
| --- | --- |
| Code | 文件树、文件查找、README 与概览、文本或 Markdown 预览、编辑入口 |
| Changes | 改动文件列表（已暂存、未暂存、未跟踪、冲突）、diff、提交说明和“Commit”按钮 |
| Commits | 分页的提交列表，可以按版本、标题、作者、SHA 筛选；提交详情和其中的改动 |
| Branches | 当前分支、其他分支、标签和上游分支；可以新建或切换分支 |
| Settings | 远端地址、推送策略、SSH 私钥引用，以及当前用户的凭据列表（脱敏） |

- 文件树是 Code 页的主体，About 是可以调整宽度的辅助侧栏。标签页的页级操作（筛选、新建、选择版本、刷新）放在标签栏右侧，内容区不再重复标签名称。
- 文件编辑、新建分支、AI 提交进度、Git 认证和快捷键帮助都在单独的浮层中打开，浮层的关闭按钮只关闭当前操作。
- 从其他标签页回到 Code 时，如果当前目录或文件还没有载入，页面按地址栏中的路径重新读取。
- Settings 的远端表单按可用宽度排列输入项；窄视口改为单列。输入框的边框与内边距计入列宽，控件保持在所属列内，相邻输入项不重叠。
- 保存远端、推送策略或 SSH 私钥引用失败时，显示错误并恢复提交前的控件可用状态；保留未提交的表单值，用户可以直接修改或重试，无需刷新页面。
- 这些设置保存成功后，如果回执中的 `index_projection.status` 为 `unconfirmed`，页面同时显示保存成功和搜索索引尚未确认更新的提示，说明搜索结果可能暂时显示旧信息、无需重复保存；索引等待失败不会把已经完成的保存显示为失败。

## 登记、发现和打开 Repo

**普通 Git Repo 在注册窗口选择 checkout 后，通过 `RegisterResource` 登记；Repo Workspace 从主 checkout 读取。导出源码记录由上文的明确导出/导入合同建立，普通注册窗口不把无 `.git` 目录自动当成导出源码。**

1. 登记字段为 `path`（checkout 目录）、可选的 `project_ids` 和 `initialize_git`。目录默认必须已经是 Git 仓库；设置 `initialize_git: true` 时，服务先在目录中初始化 Git。
2. 目录必须位于已登记的 Project 内。已经作为 Article 登记的文稿目录不能再登记为 Repo。
3. 对 Git 来源，checkout 的路径、Git 目录（`git_common_dir`）或身份远端与某个已有 Repo 相同时，加入该 Repo；同时对上两个 Repo 时拒绝登记；都对不上的新代码库获得新的 Repo ID。单个 checkout 失效时，只要还存在有效副本，Repo ID 与登记继续保留；全部 checkout 均已失效时，自动核对注销 Repo，目录与 Git 历史不删除。检查无法确认有效性时保留登记并报告错误；生命周期规则见 decision_36f46b395d8e。
4. Git Repo 的设置修改使用 `UpdateResource`，`operation` 取以下三种之一：`set_remote`（远端名称、地址和推送地址）、`set_policy`（推送开关）、`set_ssh_key`（SSH 私钥引用）。
5. 两类来源均通过 `UnregisterResource` 撤销登记，只删除登记并加入自动登记的忽略列表；磁盘内容保持不变。

## 浏览代码和历史

**viewer 可以读取 Repo 内容根内的文件；Git 来源还可以读取改动和历史。导出源码的内容根只取明确登记的 canonical 路径，不回退其他 checkout。**

- Workspace 概览包含 `source_kind` 与 `capabilities`。`browse_files` 表示目录和文件浏览；`git` 表示 Git 查询及操作；`write_files` 同时受来源与目录写权限约束；`search_files` 和 `statistics` 表示当前以 Git 文件清单计算的可选查找与统计。导出源码仅声明 `browse_files: true`，其余为 false。页面先读取概览，再请求允许的标签数据，不向不支持的查找或统计接口发请求。
- Git 概览继续包含当前分支、工作区状态、分支与标签、脱敏的远端地址、推送策略和最近提交；Code 页显示目录后异步加载短时缓存的 About 统计。导出概览和目录不调用 Git，`head`、`status` 为 null，分支、标签、远端为空；目录直接枚举 canonical 根内内容，文件只返回内容及文件元信息，`editable: false`。两类目录接口都不等待统计扫描。
- 文件树每次最多返回 1,000 个条目。文件读取只返回 2 MiB 以内的文本，二进制文件和超限文件不按文本返回。文件名查找最多返回 120 条结果，返回匹配路径与所在目录，不返回内容行号或摘录。
- 提交列表支持按路径、数量、偏移和版本读取；提交详情和 diff 都有大小限制，diff 超过 2 MiB 时截断并标记 truncated，响应保留可用部分。
- 服务先规范化相对路径，拒绝绝对路径、`..`、反斜杠、`.git`，以及解析后位于内容根之外或经目录别名进入 `.git` 的目标。符号链接自身最多返回链接元信息，不读取目标字节；README 不跟随符号链接；普通文件入口拒绝设备、管道等特殊文件。

## 编辑文件

**Git 来源的 contributor 可以在主 checkout 内保存、新建、删除和移动文件；保存时可以附带读取时的内容摘要，防止覆盖别人刚做的修改。**

- 读取文件时返回内容摘要。保存时带上它（`expected_hash`），服务在写入前比较：不一致时返回 409，页面提示重新读取；不带时直接写入。
- 同一 checkout 的保存、新建、删除和移动按顺序执行；文本以临时文件替换的方式写入。
- 新建时目标已经存在返回 409；移动只能在主 checkout 内重命名。
- 新建、删除和移动没有请求编号。结果不确定时，调用者先重新读取目标状态，再决定是否重试。

## 提交

**手工提交把当前全部改动提交为一次提交；AI 提交由模型写出提交说明，再按推送策略决定是否推送。**

- **手工提交**：Changes 页中的“Commit”把工作区中全部改动（相当于 `git add -A`）提交，说明不能为空，没有改动时不创建空提交。
- **AI 提交**：服务先读取工作区状态和 diff。发现疑似包含密钥的文件时立即停止，不把内容发送给模型。随后由登记的模型接口写出标题和原因，创建本地提交。推送策略开启且登记了远端时继续 push。模型失败返回 502，Git 命令失败返回 422，工作区冲突返回 409。
- **推送被拒**：远端拒绝写入时，本地提交保留，服务自动关闭该 Repo 的推送策略。

## 分支、同步与认证

**用户可以新建或切换分支，并对登记的远端执行 fetch、pull 或 push；需要认证时，服务只使用当前用户自己的凭据。**

- 有未提交改动或 Git 冲突、切换不安全时，切换失败，工作区保持原样。
- 同步只针对登记中的远端；页面和错误信息中的远端地址都经过脱敏。
- **凭据匹配**：服务只把匹配的凭据交给这一次 Git 命令。HTTPS 按协议、主机和用户名匹配，有用户名时优先完全匹配；远端地址没有用户名时，只有该主机恰好有一条候选凭据才自动使用。SSH 通过登记的私钥引用执行，私钥内容不会出现在命令输出中。
- **缺少凭据**：返回 409，错误码 `git_auth_required`，附带协议、主机、可选用户名和提示。页面打开认证浮层，用户输入令牌或口令并选择是否保存。保存后，用户需要自己重新点击原来的操作，服务不会自动重试。
- **认证被拒**：远端明确拒绝时，服务把该凭据标为无效并清除保存的秘密内容，之后不再自动使用它。
- **删除凭据**：按凭据 ID 删除，重复删除返回同样的结果。

## 丢弃改动

**用户确认“Discard”后，服务恢复全部已跟踪文件，并删除主 checkout 内的未跟踪内容。**

这是多个 Git 步骤，没有整体回滚：某一步失败时，已经完成的步骤保留，服务返回执行后的真实工作区状态。

## 权限

- Repo Board、读取和自动提交状态只包含当前用户有权访问的 Repo；读取 Workspace 需要 viewer 权限。
- 保存文件、提交、切换分支、同步、丢弃和管理凭据需要 contributor 权限。
- 凭据只属于保存它的用户，其他用户看不到，也不会被使用。

## 组件与流程

| 组件 | 位置 | 职责 |
| --- | --- | --- |
| Repo 页面 | `management_portal/repo_workspace.html`、`static/repo_workspace.js` | Board、按能力呈现的 Workspace 与各浮层 |
| HTTP 路由 | `management_portal/http_repo.py` | 校验 Repo ID 与角色，分派读取与写入操作，转换错误码 |
| Workspace 后端 | `management_portal/repo_workspace_backend.py` | 路径边界、文件读写、Git 查询与操作、AI 提交 |
| Repo 登记 | ResourceService 中的 `resources/providers/repo.py`、`resource_identity_backend.py`；与 Article 共用的 `registry_sync.py` | 登记、修改、撤销登记。`resource_identity_backend.py` 声明 checkout 的匹配键并合并字段，`registry_sync.py` 按匹配键找到已有记录、分配新 ID 并写回 |
| 凭据存储 | `management_portal/repo_credentials_backend.py` | 按用户加密保存、匹配和失效 Git 凭据 |

```text
repo_* → Repo 登记 → source_kind
                       ├─ git_checkout → 主 checkout → Workspace 读取 / 写入 / Git
                       │                                  ↘ 访问远端 → 当前用户的凭据
                       │                                               ↘ 缺少：409 认证提示
                       │                                                  保存后用户重新操作
                       └─ exported_tree → 唯一 canonical 根 → ResourceService 文件读取 / Workspace 只读浏览
```

Repo ID 与磁盘路径分开，代码库可以通过明确维护更换位置，Git 来源可以登记多个 checkout，导出源码始终只有一个 canonical 内容根；凭据与共享的登记文件分开保存，秘密内容不会进入共享配置；Git 操作失败时以真实的工作区状态为恢复起点。

## HTTP 接口

`{repo_id}` 必须是 `repo_*` 形式，否则返回 404。Workspace 的概览、`tree` 和 `file` 读取接口支持两类来源，沿用相同的资源授权与路径检查。导出源码同时保留统一地址 `repo_<id>/file/<相对路径>`；其他依赖 Git 的查询、可选统计/查找和文件/Git 修改接口继续明确拒绝导出源码。资源文件 200 只证明该读取入口，不能替代 Workspace API、浏览器导航或目标安装后的验收。

**读取**

| 请求 | 作用 |
| --- | --- |
| `GET /api/repos/automation/status` | 指定或全部可见 Repo 的自动提交状态和最近变化 |
| `GET /api/repos/{repo_id}/workspace` | Workspace 概览 |
| `GET /api/repos/{repo_id}/workspace/tree?path=` | 目录内容 |
| `GET /api/repos/{repo_id}/workspace/stats?path=` | Code 页 About 统计（语言、代码行数、工作树大小） |
| `GET /api/repos/{repo_id}/workspace/file?path=` | 文件内容 |
| `GET /api/repos/{repo_id}/workspace/search?q=&limit=` | 搜索 |
| `GET /api/repos/{repo_id}/workspace/commits?path=&limit=&offset=&revision=` | 提交列表 |
| `GET /api/repos/{repo_id}/workspace/commit?sha=` | 提交详情 |
| `GET /api/repos/{repo_id}/workspace/blame?path=` | 逐行归属 |
| `GET /api/repos/{repo_id}/workspace/diff?path=` | 工作区改动 |
| `GET /api/repos/{repo_id}/workspace/credentials` | 当前用户的凭据列表（脱敏） |

**修改**

| 请求 | 作用 |
| --- | --- |
| `POST /api/repos/descriptions/generate` | 为缺少简介的 Repo 批量生成简介 |
| `POST /api/repos/{repo_id}/workspace/file` | 保存文件，可带 `expected_hash` |
| `POST /api/repos/{repo_id}/workspace/entry` | 新建或删除文件、目录 |
| `POST /api/repos/{repo_id}/workspace/move` | 移动或重命名 |
| `POST /api/repos/{repo_id}/workspace/commit` | 手工提交 |
| `POST /api/repos/{repo_id}/workspace/auto-commit` | AI 提交 |
| `POST /api/repos/{repo_id}/workspace/branch` | 新建或切换分支 |
| `POST /api/repos/{repo_id}/workspace/sync` | fetch、pull 或 push |
| `POST /api/repos/{repo_id}/workspace/discard` | 丢弃改动 |
| `POST /api/repos/{repo_id}/workspace/credentials` | 保存或删除凭据 |

登记、修改登记和撤销登记使用资源指令 `RegisterResource`（`resource_type: repo`）、`UpdateResource`、`UnregisterResource`，不使用 HTTP 地址。
