# 项目工作目录约定

Resident 可按现有权限读取项目。普通任务在相应用途的 workspace/ 内创建目录、运行代码和存放交付物。Portal 开发源码和共享 Skill 仍遵循各自的维护规则。

## 确定任务文件的存放位置

- **Lineage Workspace**：凡由 Lineage 分派的任务，从第一份任务文件起就写入该 Lineage 的 Workspace，即使只有一位执行者。用户直接委托且有明确团队目标时，任务文件也写入对应的 Lineage Workspace。
- **Project Workspace**：用户直接委托且有明确项目目标时，任务文件写入对应的 Project Workspace。同一项目内，第二个 Lineage 开始依赖一项成果时，由项目统一维护；记录维护人和各方可用的稳定引用。已有 HDD 实体可继续原位使用。
- **Resident Workspace**：用户直接委托且没有明确团队或项目目标时，使用本节点的自有 Workspace。
- **独立 Workspace**：仅在用户明确指定时创建，由用户决定资产归属和维护人。跨项目复用本身不触发创建。独立登记时，Portal 的目录归属为 Workspace 自身，资产的业务归属与维护人按用户决定记录。

Provider 的默认 cwd 始终使用 Resident 自有 Workspace。写入其他 Workspace 时，从绑定记录或已知 Workspace ID 取得登记路径，用目标文件的绝对路径或工具的输出路径参数指定落点。

## Provider 的 Workspace 路径变量（拟议接口）

Provider 启动任务时，从当前 Resident 的绑定记录取得各 Workspace 的真实绝对路径，并注入执行命令的进程环境。自有 Workspace 也包含在内。每项绑定都有一个以稳定 Workspace ID 命名的变量，例如 workspace_4d69f2c8b978 对应 PORTAL_WORKSPACE_4D69F2C8B978。多个同类绑定分别保留，重命名或调整绑定顺序不影响变量名。

常用位置提供短名：PORTAL_RESIDENT_WORKSPACE 指向本节点自有 Workspace；PORTAL_LINEAGE_WORKSPACE 指向本节点所在 Lineage 的 `<项目>/agents/lineage_<ID>/workspace/`，PORTAL_PROJECT_WORKSPACE 指向所在项目的 `<项目>/workspace/`，均按规范目录树中的路径确定。对应 Workspace 未绑定时，省略该短名。独立 Workspace 按 ID 引用。

绑定发生变化时，下一次启动命令前按最新记录刷新受管变量，并移除已解绑项。变量仅提供路径导航，访问权限沿用现有机制。此接口实施前仍通过绑定记录取得路径。

## 规范目录树

Resident 日常写入有两个去向：任务代码、过程记录和交付物写入相应的 `workspace/`；需要沉淀为项目知识的条目通过知识接口维护在 `knowledge_base/`。仅在团队约定或个人职责变化时，更新相应的 `LINEAGE.md` 或 `resident.md`。

**Portal 保留目录。** Portal 后端可按需在 Lineage 和 Resident 目录下创建并维护 `runtime/`。用户、Resident 和任务脚本禁止将其用作工作目录，或在其中创建、修改、清理文件。处理消息时可读取 Portal 为该消息提供的附件；任务代码、缓存、临时文件和交付物均应写入相应的 Workspace。每日巡检按每个实体 runtime/ 的磁盘占用分别检查，试行告警线为 Lineage 1 MiB、Resident 64 MiB（为消息附件预留空间）。超过阈值记录路径和实际占用，读取失败记录巡检错误。

```text
/share/Projects/<项目>/
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

旧项目逐项迁移。发现额外路径时，由维护人核对使用者、归属和保留需求。巡检每日扫描项目、团队和节点的受管层级，跳过 Workspace、知识条目、运行资料和旧业务目录内部，不跟随软链。报告中首次扫描已有的例外与此后的变化分开列出。报告用于发现目录偏差，不能证明某次写入的执行者，亦无法观察两次扫描之间已经撤销的变化。
