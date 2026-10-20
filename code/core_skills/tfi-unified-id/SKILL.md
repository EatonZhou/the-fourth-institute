---
name: tfi-unified-id
description: 创建、登记、查找或解析 TFI Portal 统一资源 ID 与内容地址时使用；收到资源地址时先解析实际对象。
metadata:
  id: skill_3b1feeb182a3
---

# TFI Portal 统一资源 ID

## 身份与地址

除 User、Project 等类型目录明确列出的例外，资源使用 `<type>_<12位小写十六进制码>`。类型键来自 `ListResourceTypes`，不自造前缀。ID 保持不变；标题、目录、Provider session ID 和 Git commit 都不能代替资源身份。

- Resident 使用 `resident_*`，Lineage 使用 `lineage_*`，Workspace 使用 `workspace_*`；Chatroom 是独立的 `chatroom_*`，不是 Resident。
- 普通知识条目使用 `kb_id`，TODO 使用 `id`。Skill 包的入口使用 `metadata.id`；项目知识目录里的单篇 Skill 文档按知识格式使用 `kb_id`，不把这两种存储形态混为一谈。
- Resident、Lineage、绑定和运行状态通过 SocietyService 的正式指令读写。PostgreSQL 保存结构化真值；Folder 中的文件、Provider 缓存与投影不能替代它，不能通过编辑 JSON 或猜测目录迁移身份。
- Project ID 和路径从 `GetProject` 读取，User 身份来自当前认证上下文。禁止借用 owner、发送者或历史对话中的身份。

内容地址由根 ID 加提供者支持的后缀组成，例如 `skill_<code>/file/SKILL.md` 或 `workspace_<code>/file/<相对路径>`。路径每一段分别按 UTF-8 URL 编码，斜杠分隔目录；不要二次编码、拼出越界路径，或假定每种资源都支持同样后缀。精确能力和地址以解析结果为准。

## 读取

1. 有 `hatg_instruction` 时使用当前 Resident 调用；没有时使用 `${code_root}/scripts/resident_hatg_instruction.py --resident-id <本人ID>`。首次调用先读对应 `GetInstructionSchema`。
2. 已知 ID 或嵌套地址用 `ResolveResource`，需要正文再用 `ReadResource`；类型、标题、状态、存在性和能力均以实际返回为准。解析元数据不代表已经读到正文。
3. 不知道 ID 时用 `SearchResources`，按类型、项目等条件缩小范围并处理分页。Resident 会话用 `GetConversation` / `ListMessages` 等专用接口，不从运行目录扒取历史。
4. 未找到、未部署、停用和无权限分别报告，不伪造地址或降级为其他身份。附加引用已解析时复用它的地址，不再根据标题猜路径。

Git Repo 是否支持提交地址由其 source kind 决定；发行的 `exported_tree` 是当前代码目录，不因为具有 Repo 身份就拥有 Git 历史。读取当前架构使用当前根描述符，不自行构造历史提交快照地址。

## 创建与修改

1. 读 `GetResourceType` 的 `installed`、`enabled`、`registerable`、`updatable`、`unregisterable` 和专用 `actions`。类型声明与运行能力分开判断。
2. 支持统一登记时用 `RegisterResource`，ID 由服务分配；fields 必须遵循该提供者支持的字段。Project、Resident、Lineage 等使用各自专用创建指令。
3. Skill 包由文件发现，不支持 `RegisterResource`。只在这类文件自带身份的创建流程中生成新 ID，并写入其正式身份字段。生成一个 ID 本身不会登记资源，也不会赋予访问权限。
4. 修改前读取内容和版本。按该提供者的更新契约提交 `UpdateResource` 或专用指令；支持版本检查时携带版本，冲突后重读并合并。不要假设所有提供者都接受同一种 CAS 参数。
5. 撤销登记与删除文件的语义因类型而异，先核对 `GetResourceType`；只处理用户授权范围，不用全盘扫描替代单项操作。

Repo、Article、Workbench 的项目关联使用其提供者支持的字段；真实 Article 存储不能重复登记为 Repo。没有 Git 的目录按实际 Workspace 或 exported-tree 能力处理，不为登记擅自执行 git init。

## 本地地址工具

`./scripts/resource_id.py` 提供 ID 生成、语法校验和路径编码。执行前从当前实例上下文取得真实源码根并设置 `PORTAL_ROOT`；不要写死开发机路径。Resident 传本人身份（环境已注入时无需重复传入）：

```text
python3 <Skill目录>/scripts/resource_id.py --resident-id <本人ID> new skill
python3 <Skill目录>/scripts/resource_id.py --resident-id <本人ID> validate <地址>
python3 <Skill目录>/scripts/resource_id.py --resident-id <本人ID> encode-path <相对路径>
```

它通过当前身份读取类型目录；`validate` 只验证语法，不能证明对象存在或可访问。资源读写仍按上面的 HATG 流程。内部维护扫描不是普通任务的必经步骤，不能用扫描结果代替授权、正式登记或当前状态。
