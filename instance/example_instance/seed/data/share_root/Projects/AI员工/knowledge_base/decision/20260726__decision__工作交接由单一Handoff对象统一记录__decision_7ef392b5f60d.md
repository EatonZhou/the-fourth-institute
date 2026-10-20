---
kb_id: decision_7ef392b5f60d
type: decision
project: AI员工
created_at: '2026-07-26'
version:
  number: 19
  created_at: '2026-07-26'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T05:11:04Z'
  updated_by:
    user: ''
    agent: resident_511e229bc8c3
importance: 8
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/tests/cases/test_isa_g5_audit_regressions.py
  - ${code_root}/management_portal/static/hatg_panel.js
  - ${code_root}/management_portal/society_service/lineage_event_reconciliation.py
  - ${code_root}/management_portal/hatg/catalog/lineage_definition.py
  - ${code_root}/management_portal/society_service/isa/definition.py
  - ${code_root}/management_portal/society_service/lineage_handoff_execution.py
  - ${code_root}/management_portal/society_service/lineage_handoff_preparation.py
  - ${code_root}/management_portal/society_service/lineage_handoff_backend.py
  - ${code_root}/management_portal/society_service/lineage_state_domains.py
  - ${code_root}/management_portal/society_service/lineage_domain.py
  - ${code_root}/tests/cases/test_lineage_handoff_backend.py
  - ${code_root}/tests/cases/test_lineage_backend.py
  related:
  - decision_4adcc1ff2f2c
  - decision_4637ca4a69e2
  - decision_898b8911a2ff
  - decision_8838269736d3
  - decision_ccb99a00eebd
  - decision_6fc653d55c79
  - decision_6ad0384e020d
  supersedes: []
  derived_from: []
---
# Workspace Handoff 的图关系、准备与交付

Workspace Handoff（工作区交接）让同一个 Lineage 中的一个 Resident 把自己的工作目录交给另一个 Resident 使用。交接画在 Lineage 图上，是从来源 Resident 指向目标 Resident 的一条连线；Portal 按连线上的设置准备目录（直接共享、复制一份或建立 Git worktree），再为目标 Resident 建立工作区绑定。交接只准备目录，不给目标发任务；需要目标开始工作时，由 Lineage 中的事件连线启动它。

本篇说明交接的组成、三种准备方式、准备的过程和结果，以及怎样在页面和指令中使用。工作区绑定本身的规则见 decision_8838269736d3。

## 基本概念

| 名称 | 含义 |
| --- | --- |
| 来源 Resident、目标 Resident | 交出目录的一方和接收目录的一方，必须是同一 Lineage 中两个不同的 Resident。 |
| 交接关系 | Lineage 图中一条 `type=handoff` 的连线，有稳定的交接 ID（`handoff_*`）。同一对来源和目标之间只有一条。 |
| 交接项 | 交接中的一项内容：来源的哪个工作区、其中哪一部分、用什么方式准备、放到哪里。一条交接最多 24 项。 |
| 交接说明 | 写给目标的文字，说明这些目录与目标任务的关系，最多 8000 个字符。 |
| 准备 | 按交接项准备目录并为目标建立绑定的一次执行。每次准备在每个交接项下记录一次尝试（attempt）。 |
| 就绪（ready） | 所有交接项都已准备好、目标的绑定都已建立。 |

```text
来源 Resident ──交接关系（交接项、说明）──> 目标 Resident
                     │
                     │ 准备：直接共享 / 复制 / Git worktree
                     ▼
          目标得到工作区绑定，交接进入 ready
                     │
                     │ workspace_handoff.ready 事件（可选）
                     ▼
          事件连线调用目标的 Invoke 端口，目标开始工作
```

## 设计点

### 交接关系

- **交接只能发生在同一 Lineage 的两个不同 Resident 之间。** 来源和目标相同时报错“Handoff 不能交接给来源节点自身”，不在同一 Lineage 时报错“交接只能发生在同一 Lineage 的两个 Resident 之间”。
- **同一对来源和目标只有一条交接。** 用 `CreateWorkspaceHandoff` 为已有交接的一对 Resident 再次创建时，返回已有交接的 ID，并标明 `created=false`。
- **交接的来源必须是来源 Resident 已绑定的工作区。** 每个交接项用工作区 ID、root ID 或路径指明来源；Resident Folder、Lineage Folder 和知识库不能作为来源，报错“来源必须是该节点已经绑定的 Workspace；Resident Folder、Lineage Folder 和知识库不能直接交接”。
- **交接关系和准备结果分开保存。** 交接项、说明和标签属于 Lineage 定义，修改它们是一次 Lineage 定义提交；准备状态、尝试记录和结果属于运行状态，由准备过程写入，编辑图不会改变它们。
- **删除交接只删除这条关系。** 已经复制的目录和已经建立的绑定保留。副本是独立 Workspace；删除包含它的 Resident、Lineage 或 Project 目录前，系统列出仍活跃的独立 Workspace 并拒绝删除，调用者需先迁出或退役这些目录；页面删除前提示“这不会删除两端 Agent”。

### 交接只准备目录

- **交接不发送任务，也不启动目标。** 交接没有提示词，进入 ready 也不会给目标发消息。要让目标在交接就绪后开始工作，在 Lineage 中放一个 `workspace_handoff.ready` 事件源（观察这条交接），连到目标 Resident 的 Invoke 端口（decision_4adcc1ff2f2c）；另有 `workspace_handoff.failed` 事件源在准备失败时触发。
- **准备状态与目标的执行状态分开。** 交接 ready 只说明目录和绑定已经准备好；目标之后的任务成功或失败，不会改变交接的状态，准备好的目录也保留，修复后可以重新触发事件，不必再次复制。

### 已准备的交接不再改变

- **准备开始后，交接项不能再修改。** 交接处于正在准备或 ready 时，修改交接项报错“交接已准备或正在准备，不能再修改交接项；需要交接其他内容时删除这条交接后重新建立”。说明和标签仍可以修改。
- **ready 的交接不会重新准备。** 对 ready 的交接再次准备，直接返回现有结果；正在准备时再次准备，报错“Handoff 正在准备，请等待当前 attempt 完成”。草稿和失败的交接可以准备或重试。

### 失败不留下半成品

- **任一交接项失败，整条交接失败。** 交接状态变为 `failed`，错误写在交接和每个交接项最近一次尝试上；其他交接项已经完成的部分不会让交接显示为成功。
- **失败时只清理本次新建的目录。** 本次准备复制出的目录和新建的 Git worktree 与分支被删除；来源目录、事先已经存在的目录和其他交接的结果不受影响。直接共享没有新建任何目录，失败时不删除文件。
- **每次重试都是新的尝试。** 旧的尝试和错误保留（每个交接项最多保留最近 40 次），用户可以看出问题是否已经解决。

## 三种准备方式

每个交接项的 `transfer` 选择准备方式：`mode` 为 `direct`（直接共享）或 `copy`（创建副本），`method` 为具体方法。省略时默认直接共享；选择 `copy` 而省略 `method` 时默认普通复制。组合不合规时报错，不会替换成其他方式：

| `mode` | `method` | 做法 | 适合 |
| --- | --- | --- | --- |
| `direct` | `direct` | 把来源工作区本身绑定给目标，不复制文件 | 两个 Resident 在同一目录中协作 |
| `copy` | `filesystem_copy` | 把来源中选定的部分复制到新目录，再绑定新目录 | 目标需要一份独立修改的副本 |
| `copy` | `git_worktree` | 从来源的 Git 仓库新建分支和 worktree，再绑定 worktree | 两个 Resident 分别修改同一代码库 |

### 直接共享

目标得到的绑定与来源使用同一个工作区 ID 和同一个目录，之后双方看到同一组文件，任何一方的修改另一方都会看到。交接项中的 `scope`（范围）只记录在结果中，绑定覆盖整个来源工作区。

### 普通复制

- **`scope` 选择复制的范围。** 省略或为 `.` 时复制整个来源工作区；指定时必须是来源工作区内的相对路径，可以是目录或单个文件。绝对路径（包括 Windows 盘符路径）、空路径段、`.` 和 `..` 路径段一律拒绝，报错“工作区内容范围必须是安全的相对路径”。
- **一些内容不复制。** 目录或文件名为 `.git`、`.venv`、`node_modules`、`__pycache__`、`.pytest_cache`、`.mypy_cache`、`.ruff_cache`、`test-results`、`credentials`、`secrets`、`private_keys`、`.env`、`.env.local`、`.env.production` 的内容和所有符号链接不复制；交接项的 `parameters.exclude` 可以再列出最多 80 个名称。所选范围本身属于排除项时报错。
- **复制有上限。** 最多 60,000 个文件、总计 2 GiB；每个文件复制前按同一排除规则检查累计文件数和字节数，超出时立即中止并删除本次已复制的部分。
- **副本放在允许的目录下。** 目标路径默认为 `<Lineage Folder>/handoff-workspaces/<交接 ID>/<交接项 ID>`；自行指定时必须位于允许的根目录之内（`<Lineage Folder>/handoff-workspaces`、`${staging_root}/shared/portal_explorations`，以及环境变量 `PORTAL_HANDOFF_TARGET_ROOTS` 列出的目录），并且事先不存在。
- **结果记录复制了什么。** 包括文件数、总字节数、按每个文件的 SHA-256 计算出的内容哈希、完成时间，以及来源在准备时的 Git 状态（是否为仓库、提交、分支、是否有未提交修改）。

### Git worktree

Git worktree 是与原仓库共享历史、但位于另一个目录的工作副本。

- **只能交接整个仓库。** `scope` 必须为 `.`，来源目录必须位于 Git 仓库中；即使来源是仓库子目录，新 worktree 的绑定也覆盖仓库根。
- **需要一个新的分支名。** `parameters.branch` 必填，只能包含字母、数字和 `._/-`，最长 160 个字符，并且仓库中还没有这个分支。
- **起点默认是来源当前的提交。** `parameters.base_commit` 可以指定其他提交。
- **可以带上未提交的修改。** `parameters.include_worktree_changes=true` 时，把来源相对所选 `base_commit`（默认 HEAD）的修改作为补丁应用到新 worktree，并复制未被 Git 忽略的新文件（排除规则同普通复制）。
- **默认位置**为 `${staging_root}/shared/portal_explorations/<交接 ID>/<交接项 ID>/worktree`，指定位置的规则同普通复制。结果另外记录分支名、起点提交和补丁哈希。

### 长时间命令

外部 Git 命令最长运行 30 分钟，部署时可以用环境变量 `PORTAL_HANDOFF_COMMAND_TIMEOUT_SECONDS` 修改（空值、非数字或不大于零时仍为 30 分钟）。超时后这次准备失败，报错“工作区交接命令运行超过 N 秒：<命令>。可通过 PORTAL_HANDOFF_COMMAND_TIMEOUT_SECONDS 调整超时时间。”

普通复制在进程内执行，按文件数和总字节数限制规模；复制耗时由所选内容和存储速度决定。

## 准备的过程

1. 检查操作者是 Lineage 的 owner，交接不是正在准备或已经 ready。
2. 把交接状态设为 `preparing`，在每个交接项下新增一次尝试。
3. 逐项准备目录：直接共享只检查来源；复制和 Git worktree 生成新目录。
4. 为每个结果建立目标的工作区使用关系（只含 Workspace ID 和用途），并确认绑定确实存在；交接来源与操作结果保存在 handoff 记录中；直接共享还要确认目标拿到的是来源的同一个工作区 ID。目标已经绑定了同一目录时复用这条绑定。
5. 全部成功时，交接状态变为 `ready`，记录 `ready_at` 和每项结果；任何一步失败时，清理本次新建的目录，交接状态变为 `failed` 并记录错误。

## 在页面上使用

Context 图显示 Resident 之间的交接关系；Workspace 图按交接项将关系投影为实际工作区卡片之间的连线。一个交接含多个工作区时逐项显示，点击任一投影边仍查看原交接的完整详情。仅在当前工作区登记与两端 Resident 绑定可以确认端点时绘制；没有有效端点的历史记录保留在原交接中，不以某个代表工作区代替。直接共享的两端为同一工作区时显示自环，普通绑定线只表示 Resident 使用该工作区。

在 Society 页面点击工具栏的“工作交接”，在图上依次选择来源和目标 Resident，打开“建立 Workspace Handoff”对话框：

- 逐项填写交接项（来源工作区、范围、准备方式和目标位置），并填写交接说明。
- 勾选“保存后立即准备 Workspace”时，保存后马上准备；否则交接保存为草稿。
- 页面要求操作者对来源和目标 Resident 都有编辑权限，否则提示“Handoff 会读取来源 Binding 并修改目标 Binding，因此需要同时拥有来源与目标 Resident”。

交接显示为图上的一条连线。选中后，侧栏显示来源、目标、每个交接项和状态（草稿、正在准备、已就绪、失败及原因），可以修改标签和说明、删除交接；草稿和失败的交接可以点“准备 / 重试”。

## 指令

交接的指令属于 HATG ISA 的 Lineage 定义类（decision_4637ca4a69e2）。创建、修改、删除和准备要求操作者是 Lineage 的 owner；读取要求能查看这个 Lineage。

| 指令 | 作用 | 主要参数 | 返回 |
| --- | --- | --- | --- |
| `CreateWorkspaceHandoff` | 创建交接；这对 Resident 已有交接时返回已有的 | `source_resident_id`、`target_resident_id`、`items`、`explanation`、`label` | `handoff_id`、`created` |
| `UpdateWorkspaceHandoff` | 修改交接项、说明或标签 | `handoff_id`、`changes`（只能含 `items`、`explanation`、`label`；`explanation` 为空字符串时清除） | 修改后的交接 |
| `DeleteWorkspaceHandoff` | 删除交接关系 | `handoff_id` | `handoff_id` |
| `PrepareWorkspaceHandoff` | 准备目录并建立绑定 | `handoff_id`，或这对 Resident 的 `source_resident_id` 与 `target_resident_id` | `status`（`preparing`、`ready`、`failed`）、各交接项、错误 |
| `GetWorkspaceHandoff` | 读取交接定义和最近一次准备的结果 | `handoff_id` | 交接、`status`、各交接项、错误 |

- **修改交接项时提交完整列表。** `changes.items` 替换全部交接项，每项都要写明来源和准备方式；省略 `transfer` 的项按直接共享处理。
- **没有交接项的交接共享来源的当前工作区。** 创建时不给 `items`（包括 `PrepareWorkspaceHandoff` 按 Resident 对新建的交接），交接只有一项：直接共享来源 Resident 当前工作目录所在的 Workspace。
- **准备失败作为结果返回。** 准备失败已经记录在交接上时，`PrepareWorkspaceHandoff` 返回 `status=failed` 和错误，不作为指令错误。
- **交接指令不需要 `expected_version`。** 它们直接作用于 Lineage 的当前状态（decision_898b8911a2ff）。

## 实现说明

- `society_service/isa/definition.py` 实现上述指令；`lineage_domain.py` 的 `create_handoff`、`update_handoff`、`delete_handoff`、`execute_handoff` 负责权限、锁和状态变化。
- `lineage_handoff_preparation.py` 生成交接项并按“先准备目录、再建立绑定”的顺序执行；`lineage_handoff_backend.py` 负责校验、复制、Git 命令、超时和清理；`lineage_handoff_execution.py` 记录每次尝试。
- 准备目录和运行外部命令时不持有 Lineage 的状态锁，只在开始和结束时短暂加锁写入状态。
