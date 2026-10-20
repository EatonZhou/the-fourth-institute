# Resident Folder

Resident Folder 是一个 Lineage 节点的身份与记录目录，属于 System Store，不是 Workspace。

```text
<lineage_folder>/residents/<resident_id>/
├── resident.md
├── diary.md
├── mailbox.md
├── workspaces.md
├── workspace/
└── runtime/
```

这些名称是基础接口，不限制增加职责明确的 Markdown 或结构化文件。

## 文件职责

- `resident.md`：节点长期身份、职责、偏好和边界。
- `diary.md`：节点跨会话连续工作所需的简短记录。
- `mailbox.md`：协作者用文件留言的约定入口；不自动进入模型输入，不代替 Portal 消息。
- `workspaces.md`：Workspace Registry 与 Lineage Binding 的可读投影。
- `workspace/`：单独登记的节点自有 Workspace，保存 Seed、报告和其他运行产物。
- `runtime/`：Provider 本地目录、工具缓存、授权上下文及扩展运行资料；运行配置、session、job、事件、恢复状态和 token usage 的真值在 PostgreSQL。

Resident Folder 不创建 `knowledge_base/`。项目代码、实验和任务文档写入 Workspace；可复用项目知识写入 `GetProject` 返回的实际项目目录下的 `knowledge_base/`。交付物通过当前可用的 Workspace 文件地址或相应资源入口提供，不凭目录名另造存储协议。

## Workspace 与 Context fork

Resident Folder 根目录和 Lineage Folder 都不能成为默认 cwd。每个节点使用自己的 `workspace/`。用户指定的其他目录登记为 Workspace 并加入 Binding。

默认 cwd 与 Binding 用于启动、导航和交接，不构成身份、资源授权或文件系统沙箱。Resident 可以按任务需要在 runtime 实际允许的任意目录工作；客户端不得根据当前目录选择 Resident 身份或 bearer。

Context fork 创建自己的节点和 `workspace/`，并继承父节点绑定的其他 Workspace。父节点自有 Workspace 不继承；继承不复制外部目录，也不生成 Workspace 交接边。新节点只使用当前 Lineage 节点布局，不创建其他 fork runtime 布局。

## 修改与内容分流

修改 Resident Folder 或 Binding 前，检查 actor 权限，说明修改对象与效果，并取得用户确认；当前请求已经明确要求时不重复询问。

身份与长期职责写入 `resident.md`；连续性写入 `diary.md`；Portal 协作消息通过正式消息接口处理；Workspace Binding 先更新数据库真值再生成清单；组件本地运行资料写入 `runtime/`；任务文件写入 Workspace。看到额外文件时先判断实际职责，不因其未列在基础结构中而删除。
