---
kb_id: decision_ccb99a00eebd
type: decision
project: AI员工
created_at: '2026-07-26'
version:
  number: 16
  created_at: '2026-07-26'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T10:41:36Z'
  updated_by:
    user: ''
    agent: resident_37dd0dcc31bf
importance: 9
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/static/lineage_workbench.js
  - ${code_root}/management_portal/http_agent_workbench.py
  - ${code_root}/management_portal/society_service/resident_runtime_domain.py
  - ${code_root}/management_portal/society_service/resident_record_domain.py
  - ${code_root}/management_portal/society_service/resident_workspace_domain.py
  - ${code_root}/management_portal/society_service/resident_catalog.py
  - ${code_root}/management_portal/society_service/state_store.py
  - ${code_root}/tests/cases/test_resident_folder_creation_contract.py
  - ${code_root}/tests/cases/test_portal_resident_rule_injection.py
  related:
  - decision_0571f3741e23
  - decision_5bf5bfbd52cb
  - decision_c47a13e8b962
  - decision_c616d197abbb
  - decision_741b04821171
  - decision_8838269736d3
  - decision_721c64f9bdcc
  supersedes: []
  derived_from: []
---
# Resident Folder、连续性记录与节点 owned Workspace

Resident 是 Portal 中能长期存在、持续工作的 AI 节点。每个 Resident 在所属 Lineage Folder 下有一个固定目录，叫 Resident Folder。它集中存放这个 Resident 的长期说明、跨会话的连续性记录、工作区导航、运行资料和它自己的工作区；Resident 换了模型会话、服务重启或换了 Provider 之后，这些文件仍在原处。

本篇说明 Resident Folder 的位置和固定内容、每个文件由谁写、怎样创建和修复，以及用户怎样在页面上浏览 Resident 的工作区文件。Lineage Folder 的布局见 decision_721c64f9bdcc；工作区绑定与启动目录见 decision_8838269736d3。

## 目录布局

```text
<Lineage Folder>/residents/<resident_id>/
├── resident.md       Resident 的长期说明
├── diary.md          跨会话的连续性记录
├── mailbox.md        给其他协作者的文件留言入口
├── workspaces.md     工作区绑定的可读清单
├── runtime/          运行资料
├── extensions/       Resident 扩展（例如 Watchboard，decision_0571f3741e23）的脚本与配置，按需创建
└── workspace/        该 Resident 的自有工作区，也是默认启动目录
```

`resident_id` 是 Resident 的稳定 ID。Resident 改名时目录不变，避免路径失效。Resident Folder 本身用于存放说明和运行资料；任务的代码、草稿和报告放在 `workspace/` 或其他绑定的工作区中。

## 固定内容与写入者

| 路径 | 内容 | 谁写入 | 能否从数据库重建 |
| --- | --- | --- | --- |
| `resident.md` | Resident 是谁、长期职责和希望它持续遵守的说明 | 有管理权限的用户，或得到用户明确要求的 Resident | 不能，保留人工内容 |
| `diary.md` | 跨会话仍有价值的工作背景和进展 | 同上 | 不能 |
| `mailbox.md` | 其他协作者用文件留言的约定入口 | 协作约定中的写入方 | 不能 |
| `workspaces.md` | 自有工作区和引用工作区的清单 | Portal 按自有归属与额外绑定生成 | 能 |
| `runtime/` | 运行组件的资料，见下文 | 各运行组件 | 取决于组件 |
| `workspace/` | 自有工作区中的任务文件 | Resident 和用户 | 不能 |

`runtime/` 中当前有以下内容，每一项都由对应组件创建和清理：

| 路径 | 用途 |
| --- | --- |
| `runtime/backend/` | Provider 的本地运行目录，例如 Claude Code 的配置目录 |
| `runtime/authorization/` | 存在受保护 ISA 时生成的可信执行授权上下文 |
| `runtime/tools/` | 执行工具的缓存与临时文件 |
| `runtime/extensions/` | Resident 扩展（例如 Watchboard）每次运行的记录 |

运行设置、会话、当前工作、事件、恢复状态、token usage 和对话保存在 PostgreSQL 中。`runtime/` 保存 Provider 与工具的本地资料，不充当这些状态的真值。

## 设计点

### 每个 Resident 有独立目录

- **Resident 在数据库中创建之后才建立目录。** 两个 Resident 不共享目录；Fork 出的 Resident 也有新目录（decision_5bf5bfbd52cb）。
- **删除和迁移按 Resident ID 定位目录。** 服务使用准确的 `resident_id` 和已经校验过的权限找到目录，不按标题或相似路径猜测。删除 Resident 时，关联工作区按存活 Resident 引用决定去留：无人引用则清理，有其他使用者则保留目录、稳定 ID 和绑定，并继续完成 Resident 删除。工作区嵌在节点目录内时保留它及必要父目录，其余节点文件清理；具体生命周期见 decision_8838269736d3。迁到另一个 Lineage 时目录随 Resident 搬到新 Lineage Folder 下。

### 长期说明与连续性记录

- **`resident.md` 在需要时交给模型。** 它作为 `<resident-identity>` 块出现在输入开头，出现条件见 decision_c616d197abbb。新 Resident 的 `resident.md` 初始为空；Fork 出的 Resident 复制来源的 `resident.md` 一次，之后两份各自变化。
- **`diary.md` 和 `mailbox.md` 是文件约定。** 它们不会自动进入模型输入，也不代替 Portal 的对话或 Lineage 中的事件；Resident 需要时自己读写。
- **用户明确要求修改时，Resident 才改写长期说明和连续性记录。** 用户要求修改自己有权管理的 Resident 的这些文件，就是这次写入的授权；没有这种要求时，Resident 不自行改写。

### 工作区清单

- **`workspaces.md` 由自有归属与额外绑定生成。** 工作区绑定变化时，Portal 先更新数据库中的绑定，再重新生成这个文件。手工在文件中加一行路径不会建立绑定，也不会授予访问权。

### 数据库与文件的分工

- **身份、成员关系和权限在数据库中。** Resident 的 ID、名称、所属 Lineage、运行设置快照和会话绑定由 SocietyService 与 ExecutionService 保存，Lineage 的 owner 决定谁能查看和管理。修改 Markdown 文件不会改变这些记录；需要改 owner、成员关系或工作区绑定时，调用对应的指令。

## 在页面上浏览工作区文件

Society 页面的节点面板提供 Resident 的文件树，页面中的文件预览也按同样的方式读取文件。文件树的根就是这个 Resident 的工作区：自有 Workspace 和额外绑定的 Workspace，每个根用实际 workspace_id 标识；Resident Folder 与 Lineage Folder 使用独立的系统目录标识。

- **只有对这个 Resident 有工作区权限的人可以浏览和读取。** 通常是 Lineage 的 owner；其他人报错“只有 Lineage owner 可以读取 Resident workspace”。只有 Resident 的 owner 可以编辑文件。
- **每次读取都同时指定 Resident、Workspace ID 和根内相对路径。** 越出根目录的 `..` 路径、绝对路径和未登记的 Workspace ID 都会被拒绝，不会改用其他目录。多个根中的同名文件因此互不混淆。
- **目录按需展开。** 第一次只列出根下的直属项；页面把已展开的目录作为 `expanded` 参数提交，服务只继续读取这些目录的直属项，不递归扫描整个工程。“尚未读取”和“确实为空”是不同的状态；读取失败时报告错误，不显示成空目录。
- **敏感文件被隐藏。** 路径中任何一段含有 `secret`、`token`、`credential`、`password`、`passwd`、`auth`、`private_key`、`id_rsa`、`id_ed25519`、`.env` 或 `.git` 时，文件树不列出它，读取和编辑也被拒绝；结果中给出隐藏的数量。列目录不跟随符号链接，读取时校验真实路径仍在所选根内。
- **编辑有类型和大小限制。** 只能编辑已存在的普通文件，不能编辑目录或符号链接，文件必须是支持的文本类型并且不超过 120,000 字节。
- **在同一页中预览。** 在文件树中选择文件，内容显示在同页的预览区；选择另一个文件后可以从文件树回到原文件，不打开新的浏览器页面，也不切换当前 Resident。

文件树的行显示、选中和展开状态、打开菜单与事件释放属于共享文件浏览组件（decision_741b04821171）：一个文件树实例只绑定当前 Resident 和根；重新绑定或销毁时先释放旧的监听、菜单和待执行的定位；目录请求按 Resident、root 和请求代次核对后才应用，迟到的旧请求不会覆盖新目标；刷新失败时保留已显示的内容并提示错误。这些浏览状态只影响界面怎样访问已有文件，不改变目录、绑定或权限。

## 创建与修复

1. SocietyService 在数据库中创建 Resident 身份和成员关系。
2. 根据所属 Lineage Folder 和 `resident_id` 计算目录路径，创建缺失的固定文件和子目录。
3. 登记 Workspace 并设置 Resident 的 owned_workspace_id，自有归属不再重复写入额外绑定表，然后生成 `workspaces.md`。
4. Fork 出的 Resident 复制来源的 `resident.md`，并在对新 Resident 可见之前提交其数据库执行配置。

重复执行创建步骤时只补齐缺失项，已经存在的 `resident.md`、`diary.md`、`mailbox.md` 和工作区内容保持原样。数据库身份已创建而目录步骤失败时，服务记录失败，之后按 Resident ID 重新执行缺失的步骤，不把 Resident 报告为完整可用。

文件预览与原始下载同时检查请求路径和符号链接解析后的相对路径；指向 `.env` 等敏感文件的别名同样拒绝。

### 实现来源与入口定位

Resident 目录定位与准备分别见 `resident_record_domain.py#_resident_folder_path` 和 `resident_workspace_domain.py#_ensure_resident_workspace`；`resident_domain.py` 为公开聚合入口，实际目录规则不由该重导出文件另行实现。实现来源保留具体文件，入口在此作为调用定位。
