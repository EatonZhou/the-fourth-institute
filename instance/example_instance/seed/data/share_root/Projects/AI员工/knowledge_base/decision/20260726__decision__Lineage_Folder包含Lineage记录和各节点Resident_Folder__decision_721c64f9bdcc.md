---
kb_id: decision_721c64f9bdcc
type: decision
project: AI员工
created_at: '2026-07-26'
version:
  number: 9
  created_at: '2026-07-26'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-18T21:04:27Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_73cb83291316
importance: 8
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/society_service/lineage_storage.py
  - ${code_root}/management_portal/society_service/state_store.py
  - ${code_root}/management_portal/society_service/lineage_domain.py
  - ${code_root}/management_portal/society_service/resident_catalog.py
  - ${code_root}/tests/cases/test_resident_folder_creation_contract.py
  - ${code_root}/tests/cases/test_lineage_backend.py
  related:
  - decision_4637ca4a69e2
  - decision_8838269736d3
  - decision_ccb99a00eebd
  - decision_d918ebebc062
  - decision_6fc653d55c79
  supersedes: []
  derived_from:
  - decision_ccb99a00eebd
---
# Lineage Folder 的聚合布局与节点目录

Lineage 是 Portal 中一组有协作关系的 Resident。Lineage Folder 是这个 Lineage 在文件系统中的目录：用户和 Agent 从这里找到 Lineage 的共同说明、每个 Resident 的目录和运行资料。本篇说明 Lineage Folder 放在哪里、里面有什么、怎样创建、搬迁和删除，以及它与数据库记录的关系。

单个 Resident 目录的内容见 decision_ccb99a00eebd；Resident 从哪个目录启动任务见 decision_8838269736d3。

## 位置

每个 Lineage 有一个固定的入口路径 `${share_root}/Agents/<lineage_id>`（部署时可用环境变量 `PORTAL_LINEAGE_FOLDER_ROOT` 更换 `${share_root}/Agents`）。实际目录由入口本身或其符号链接目标确定，不根据 Project 归属猜测。

| 入口形态 | 实际目录 |
| --- | --- |
| 普通目录 | `${share_root}/Agents/<lineage_id>` 本身；有 Project 归属也可能采用此布局 |
| 符号链接 | 当前登记的目标，可能是历史位置或 Project 下的目录 |

`lineage_id` 是 Lineage 的稳定 ID，目录名使用它而不是标题，Lineage 改名时路径不变。通过入口路径总能找到一个 Lineage 的目录，不需要知道它属于哪个 Project。

## 目录布局

```text
<Lineage Folder>/
├── README.md           目录说明：Lineage 标题、Lineage ID 和各子目录的用途
├── LINEAGE.md          所有 Resident 共同的目标、背景和协作说明，初始为空
├── residents/
│   └── <resident_id>/  每个 Resident 的目录（Resident Folder）
│       └── workspace/  该 Resident 的自有工作区
└── runtime/            Lineage 级运行组件的资料
```

| 路径 | 内容 | 谁写入 |
| --- | --- | --- |
| `README.md` | Lineage 标题、Lineage ID，以及 `LINEAGE.md`、`residents/`、`runtime/` 各自的用途 | 创建 Lineage 时由 SocietyService 生成一次 |
| `LINEAGE.md` | 各 Resident 共同的目标、背景和协作说明 | 用户或有权限的 Resident |
| `residents/<resident_id>/` | 单个 Resident 的目录 | 见 decision_ccb99a00eebd |
| `runtime/` | Lineage 级运行组件拥有的资料 | 对应的运行组件 |

## 设计点

- **每个 Lineage 只有一个目录。** Resident 再多，它们的目录都放在这一个 Lineage Folder 的 `residents/` 下，按 `resident_id` 命名；Resident 改名时目录不变。
- **创建 Lineage 时建立目录骨架。** SocietyService 在数据库中登记 Lineage 之后创建目录，只补齐缺失的 `residents/`、`runtime/`、空的 `LINEAGE.md` 和 `README.md`；已经存在的文件不会被覆盖，用户编辑过的内容保持原样。
- **目录中的说明文件只是给人看的。** 成员关系、owner、Leader、组件与连线和 Lineage 版本都保存在 SocietyService 的数据库中；手工修改 `README.md` 或 `LINEAGE.md` 不会增加成员、授予权限或改变 Lineage。
- **Lineage 和 Resident 的目录都不是启动目录。** 每个 Resident 从自己的 `residents/<resident_id>/workspace/` 启动任务。
- **修改 Lineage 所属的 Project 会搬迁目录。** `UpdateLineage` 修改 `project_id` 时，SocietyService 把实际目录移到新 Project 的登记根目录下 `knowledge_base/agents/<lineage_id>/`（或移回入口位置），并更新入口符号链接；这个 Lineage 中有 Resident 正在排队、执行或停止时拒绝，报错“Lineage 仍有任务正在排队或处理，停止或完成后再修改所属项目”。
- **删除 Lineage 会移除入口。** `DeleteLineage`（decision_4637ca4a69e2）删除其中的 Resident 及其自有工作区，然后删除 Lineage 目录并解除入口。目录内若还有独立活跃 Workspace（例如交接副本），删除计划先拒绝并列出其 ID 与路径；Project 删除使用相同检查。先迁出或退役这些 Workspace 后才能删除目录。
- **新文件要说明归属。** 在 Lineage Folder 中新增文件或子目录时，需要说明哪个组件拥有它、能否从数据库重建、以及骨架更新时是否允许覆盖；说不清的文件不放在 Lineage Folder 根目录。Resident 的任务文件放进工作区，可复用的项目知识放进 Project 的知识库，正式交付物放进 Artifact Store。

## 实现说明

- `society_service/lineage_storage.py` 负责入口路径：`canonical_lineage_folder` 计算 `${share_root}/Agents/<lineage_id>`，`bind_lineage_folder` 为 Project 中的 Lineage 原子地建立符号链接，`relocate_lineage_folder` 在修改所属 Project 时移动目录并改指向，`unbind_lineage_folder` 在删除时移除入口。同一个 Lineage 的入口已经指向别处时，操作以冲突错误结束，不会出现两个目录。
- `society_service/lineage_domain.py` 的 `_ensure_lineage_folder_files` 创建骨架，`_lineage_readme_text` 生成 `README.md`。
- Resident 目录在 Resident 创建时建立（decision_ccb99a00eebd）。目录缺失时，系统可以按数据库中的成员关系补齐固定骨架；工作文件无法从数据库推导，补齐骨架不代表恢复了这些文件。数据库中没有记录的目录不会因此成为成员。
