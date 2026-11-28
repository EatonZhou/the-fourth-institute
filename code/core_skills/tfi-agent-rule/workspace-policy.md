# 项目工作目录约定

Resident 默认不碰其他 Project、其他 Lineage、`secrets/`、provider-state 和各 Provider 账号目录；Release 代码只通过发布更新。普通任务在相应用途的 workspace/ 内创建目录、运行代码和存放交付物。

## 确定任务文件的存放位置

- **Lineage Workspace**：凡由 Lineage 分派的任务，从第一份任务文件起就写入该 Lineage 的 Workspace，即使只有一位执行者。用户直接委托且有明确团队目标时，任务文件也写入对应的 Lineage Workspace。
- **Project Workspace**：用户直接委托且有明确项目目标时，任务文件写入对应的 Project Workspace。同一项目内，第二个 Lineage 开始依赖一项成果时，由项目统一维护；记录维护人和各方可用的稳定引用。已有 HDD 实体可继续原位使用。
- **Resident Workspace**：用户直接委托且没有明确团队或项目目标时，使用本节点的自有 Workspace。
- **独立 Workspace**：仅在用户明确指定时创建，由用户决定资产归属和维护人。跨项目复用本身不触发创建。独立登记时，Portal 的目录归属为 Workspace 自身，资产的业务归属与维护人按用户决定记录。

Provider 的默认 cwd 始终使用 Resident 自有 Workspace。写入其他 Workspace 时，从绑定记录或已知 Workspace ID 取得登记路径，用目标文件的绝对路径或工具的输出路径参数指定落点。

## 在命令中定位 Workspace

使用 `PORTAL_RESIDENT_WORKSPACE` 定位个人工作区。团队或项目工作区已绑定时，可使用 `PORTAL_LINEAGE_WORKSPACE` 或 `PORTAL_PROJECT_WORKSPACE`。按稳定 ID 引用时使用 `PORTAL_WORKSPACE_<大写 ID 后缀>`。绑定变更在下一轮命令环境中生效。变量缺失时，通过 HATG 查询当前绑定和登记路径。

## 规范目录树

Resident 日常写入有两个去向：任务代码、过程记录和交付物写入相应的 `workspace/`；需要沉淀为项目知识的条目通过知识接口维护在 `knowledge_base/`。仅在团队约定或个人职责变化时，更新相应的 `LINEAGE.md` 或 `resident.md`。

**Portal 保留目录。** Portal 后端可按需在 Lineage 和 Resident 目录下创建并维护 `runtime/`。用户、Resident 和任务脚本禁止将其用作工作目录，或在其中创建、修改、清理文件。处理消息时可读取 Portal 为该消息提供的附件；任务代码、缓存、临时文件和交付物均应写入相应的 Workspace。每日巡检按每个实体 runtime/ 的磁盘占用分别检查，试行告警线为 Lineage 1 MiB、Resident 64 MiB（为消息附件预留空间）。超过阈值记录路径和实际占用，读取失败记录巡检错误。

```text
projects:<项目>/
├── README.md                          项目说明和目录入口
├── workspace/                         项目公共工作区
├── knowledge_base/                    项目知识库，通过知识接口维护
└── agents/
    └── lineage_<ID>/
        ├── LINEAGE.md             团队约定，低频维护
        ├── README.md
        ├── workspace/                 团队工作区
        └── residents/
            └── resident_<ID>/
                ├── resident.md     身份与长期职责，低频维护
                ├── workspaces.md   Portal 根据绑定记录生成，供查阅
                └── workspace/         个人工作区，Provider 默认 cwd
```

Resident 如需记录交接笔记，使用个人 workspace/diary.md，按需创建。

每个 Workspace 建立时都包含以下两项，业务目录按任务需要添加。

```text
workspace/
├── README.md                          使用说明、SSD 配额和使用时限
└── HDD/                               存放 HDD 资源软链和对应登记表
```

HDD/ 是实体目录。按需在其中建立软链并登记：generated data 和 generated weight 各最多一个，指向共享资源的软链数量不限。

管理目录通过对应的 Portal 操作变更；知识条目通过知识接口维护。共享输入按版本引用 HDD 实体。

在同一个 Workspace 内复用文件且确需软链时，使用相对路径，并确认解析后的目标仍在该 Workspace 内。迁移后移除旧入口。目录布局巡检核对受管目录的直接子项；软链巡检另行检查 Workspace 内外的链接位置和目标。遇到拒读，报告具体路径和检查范围。
