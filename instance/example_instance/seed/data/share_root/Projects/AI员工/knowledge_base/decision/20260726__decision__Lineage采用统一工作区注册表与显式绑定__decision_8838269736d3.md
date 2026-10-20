---
kb_id: decision_8838269736d3
type: decision
project: AI员工
created_at: '2026-07-26'
version:
  number: 37
  created_at: '2026-07-26'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T10:44:54Z'
  updated_by:
    user: ''
    agent: resident_37dd0dcc31bf
importance: 9
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/society_service/resident_protocol.py
  - ${code_root}/tests/cases/test_resident_workspace_name_projection.py
  - ${code_root}/tests/cases/test_workspace_resource_rename.py
  - ${code_root}/management_portal/resources/providers/workspace.py
  - ${code_root}/management_portal/resources/workspace_store.py
  - ${code_root}/tests/cases/test_isa_g5_audit_regressions.py
  - ${code_root}/management_portal/society_service/isa/residents.py
  - ${code_root}/management_portal/society_service/lineage_workspace_binding.py
  - ${code_root}/management_portal/society_service/resident_record_domain.py
  - ${code_root}/management_portal/society_service/resident_workspace_domain.py
  - ${code_root}/management_portal/society_service/resident_workspace_storage.py
  - ${code_root}/management_portal/society_service/resource_client.py
  - ${code_root}/tests/cases/test_resident_workspace_storage.py
  - ${code_root}/management_portal/society_service/lineage_handoff_backend.py
  - ${code_root}/tests/cases/test_resident_folder_creation_contract.py
  - ${code_root}/tests/cases/test_lineage_handoff_backend.py
  - ${code_root}/tests/cases/test_repo_workspace_backend.py
  related:
  - decision_c616d197abbb
  - decision_5bf5bfbd52cb
  - decision_d19b9909a674
  - decision_721c64f9bdcc
  - decision_ccb99a00eebd
  - decision_7ef392b5f60d
  - decision_34158fdd4078
  - decision_b820faf42218
  - decision_600ae27b0538
  supersedes: []
  derived_from: []
---
# Resident Workspace 与使用关系

Workspace 是具有稳定资源 ID 的文件目录。每个 Resident 有一个随其生命周期管理的自有 Workspace，也可以引用独立登记的其他 Workspace。目录归属、目录使用、执行时的 cwd 分别表达，不设置 development、work 等目录类别。

## 基本概念

Society 页的 Workspace 图以工作区卡片为主对象，用稳定 Workspace ID 去重，并展示当前 Resident 的全部实际使用关系。Resident 是只读的小圆形名字/ID标记；点击工作区卡片复用 Navigation 的工作区目录入口，按当前资源权限解析，不改变 Resident 绑定或当前 Chat。图投影、工作区交接连线和局部布局规则见 decision_4adcc1ff2f2c。

目录元信息与目录内容分开读取。尚未加载子项的工作区不能显示为已确认的空文件夹；Navigation 展开它或通过工作区资源打开它时读取实际目录，保留读取错误与重试入口。已有目录内容不被后续仅含元信息的绑定更新清空。

Workspace ID 标识目录资源，路径是该资源当前的位置。自有归属保存在 Resident 上，使用关系保存在绑定表上。默认 cwd 由自有 Workspace 推导；为一次任务选择其他 cwd 不改变归属和绑定。Binding 表达 Resident 对目录的使用关系。通过 Resident 读取绑定目录时，ISA 按该 Resident 的 viewer 检查。不经 Resident、直接按 Workspace 访问文件时，权限同样来自拥有或绑定该 Workspace 的存活 Resident：调用者对其中任一 Resident 达到 viewer 才能读取文件，达到 contributor 才能写入。在权限 R 树上，Resident 自有的 Workspace 挂在该 Resident 下，通过 Binding 使用的其他 Workspace 留在“共享”下（decision_600ae27b0538）；没有存活 Resident 拥有或绑定的 Workspace，只按调用者对该 Workspace 自身的权限判断。文件系统权限与路径边界仍需满足，Binding 不保存另一套 access 或 manage 权限。

## 设计点

### 绑定关系与路径的来源

`workspace.workspaces` 是 Workspace 身份、位置、生命周期的唯一真值，字段为 workspace_id、path、title、created_at、updated_at、deleted_at、deleted_by。活跃路径有唯一索引；同一真实目录重复登记返回同一个 ID。路径规范化后保存，拒绝相对路径及文件系统根目录。已删除身份不能复活；资源查询从表投影，搜索索引可以重建。

`resource_registry.registry_documents` 不再保存 Workspace 正文或来源副本。通用资源接口保留 workspace 类型与嵌套文件地址，直接适配专表。关系、权限及展示信息从各自的责任方读取。

### 名称更新与版本冲突

Workspace 的名称通过 `UpdateResource(resource_id=workspace_id, changes={title, expected_version})` 更新。`title` 必须为字符串，去除首尾空白后非空且不含 NUL；只接受 `title` 与 `expected_version`。ID、path、自有归属和使用绑定不由此入口修改。已有 Workspace 的 `ensure` 保留当前名称，不用登记或绑定时的旧值覆盖改名。

调用方先通过 `ResolveResource(address=workspace_id)` 取得 `result.resource.title` 与 `result.resource.version`。版本是当前 Workspace 行 `updated_at` 的不透明字符串，原样传入 `changes.expected_version` 或标准请求顶层 `expected_version`，不得由界面计算。两个位置同时提供时沿标准请求规则以顶层值为准。版本为必填非空字符串；成功结果的 `result.version` 与 `result.resource.version` 相同，资源记录直接由已提交行投影，不依赖搜索索引。`GetResourceType(resource_type="workspace")` 返回扁平 `result.updatable=true`；名称更新不开放正文、登记或删除能力。

WorkspaceStore 在同一事务内对当前行 `SELECT ... FOR UPDATE`，确认未删除、由资源提供器按真实 actor 检查权限，再比较版本并写入 title/updated_at。权限沿当前 permission_system 的 UpdateResource 端口；权限开关关闭仍用当前锁定行的真实路径保留个人 AMA 隐私，已有绑定和归属规则不变。移动、删除与改名竞争同一行；变更版本严格前进，避免时钟回拨或相同时间精度重用旧版本。名称未变且版本匹配时保持版本不变；已删除身份不能改名或复活。

正式 ISA 错误：非法名称、只读字段、缺少或非法版本为 `instruction.invalid_arguments`（400）；普通资源缺少更新权限为 `instruction.forbidden`（403）；缺失、已删除或因 AMA 隐私隐藏为 `instruction.not_found`（404）；旧版本为 `instruction.version_conflict`（409），details 保留 expected_version/current_version。冲突后重新读取并处理差异，不自动覆盖重试。索引未确认或提交后错误沿 ResourceService 既有合同处理，不能解释为领域写入已回滚。本入口只更新 Workspace 元信息，不改变文件正文写入的 CAS 协议。

`GetResident.result.resident.workspaces` 的每一项同时返回 `title` 与 `label`，两者都是 WorkspaceStore 当前名称。目录根投影按自有与额外绑定的全部 ID 一次批量读取当前行，不把名称存入 Resident 关系记录或跨请求缓存；名称更新后下一次读取即显示新名称。只读协议投影应保留这两个字段。路径、使用目的、自有引用与额外绑定保持原有语义；初始化尚未登记 Workspace 行时沿用目录 basename 展示，读取投影本身不登记或恢复身份。

### 自有工作区与默认启动目录

`lineage.residents.owned_workspace_id` 指向自有 Workspace；每个活跃 Resident 必须有一个引用，该引用唯一。自有 Workspace 固定在 `<lineage_folder>/residents/<resident_id>/workspace/`，默认 cwd 由它的当前路径推导。自有 Workspace 不再重复出现在额外绑定表中。

Resident Folder 与 Lineage Folder 是系统目录。文件浏览器可以展示它们，但不把它们冒充 Workspace。目录浏览使用实际 workspace_id，系统目录使用固定 location 标识；不生成 resident_workspace、development、work_1 之类的绑定别名。

### 引用其他目录

`lineage.resident_workspace_bindings` 只保存 resident_id、workspace_id、purpose，主键为两个 ID。路径通过 Workspace 专表批量查询得到。purpose 是用途说明，不参与权限或生命周期判断。

BindWorkspace 先验证存在的绝对目录，再按规范路径取得唯一 ID，并提交使用关系和文档更新任务。同一 Resident 重复绑定同一 Workspace 不产生新关系。绑定不增加角色、scope、managed、default_cwd、binding_origin 等字段。

### 解除绑定

UnbindWorkspace 通过 workspace_id 或明确路径选择额外绑定，只删除使用关系。自有 Workspace 不能被解除；独立 Workspace 即使失去最后一个使用者也保留，目录文件不受 Unbind 影响。

### 删除 Resident、Lineage 与关联 Workspace

**删除通过权限系统授权后，Workspace 的存在或被引用不会额外拒绝 Resident 或 Lineage 删除。** 删除集合中的所有 Resident 先在同一事务中标记删除，再移除它们的使用关系，按仍存活的 Resident 计算 Workspace 的去留。自有归属和额外绑定都计入引用；同批删除成员的相互引用不算存活引用。

**没有其他存活 Resident 引用的关联 Workspace 随删除清理。** 范围包含被删除 Resident 的自有 Workspace、额外绑定的 Workspace，以及待删除目录中的嵌套 Workspace。Workspace 注册标记删除，目录文件由持久清理任务删除。UnbindWorkspace 只解除关系，仍不触发这项回收。

**仍被其他 Resident 引用的 Workspace 保持可用。** 它的稳定 ID、路径、内容和存活使用者的绑定保持原样，即使原自有 Resident 已删除也是如此。若目录位于被删除的 Resident、Lineage 或 Project 目录内部，文件清理保留该工作区及其必要父目录，只清理其余内容；不会要求用户先迁出或注销。剩余父目录不恢复已经删除的 Resident、Lineage 或 Project 身份。若被回收的外部 Workspace 包含其他存活 Resident 的系统目录，其所属 Lineage 的目录一并保留，避免只留下 workspace/ 而丢失身份说明和运行资料。

**注册状态与文件清理分别提交并可恢复。** 关联 Workspace 的引用判断和退役跟随删除事务提交；文件删除作为持久生命周期步骤执行。清理步骤重新读取当前活跃 Workspace，保留删除提交后新登记的目录。Workspace 登记和位置修改与引用判断、文件清理串行化，避免检查结束后新增目录被旧任务误删。文件系统错误留下可重试任务，不复活已删除身份；系统不提供回收站。

Project 删除复用相同的 Workspace 引用清理规则（decision_4648f7fa3295）。授权始终由权限系统决定；引用数量只决定工作区保留或回收，不构成第二套删除权限。

关闭原生会话的持久化任务保存该会话的自有 Workspace ID、路径及启动目录快照，删除身份后仍能继续关闭会话；历史 Agent 轨迹继续保存。

### 为一次任务指定启动目录

默认执行从自有 Workspace 开始。接口中允许的单次 cwd 选择只影响该次执行。读取接口返回 default_cwd、owned_workspace、workspace_paths 等派生信息，数据库不再保存同义的路径副本或展示开关。

### Fork、迁移与工作区交接

Fork 创建自己的新 Workspace，并继承来源 Resident 的额外绑定；来源的自有 Workspace 不自动继承。重复保存不会更换已建立的自有 ID。

Resident 或 Lineage 移动保持 Workspace ID，在同一事务内更新位置与归属信息。Project 改名同时读取活跃 Resident 的自有引用和额外绑定，关闭并重定位受影响的 Provider 会话；Workspace 路径改变时推进 updated_at，使资源索引及时刷新。已删除 Workspace 的路径及删除事实保持不变。Workspace handoff 的来源使用 workspace_id 和相对范围；直接使用既有目录保留其 ID，复制目录形成新 Workspace。操作来源及交接结果保存在 handoff 记录中，绑定不再携带来源标签。

## 绑定记录的内容

例如 A 拥有 W1，同时使用共享目录 W2：Resident A 的 owned_workspace_id=W1，额外绑定只有 (A,W2,purpose)。B 可以拥有 W3 并使用同一个 W2。删除 A 后，A 的使用关系清除，W2 与 B 的关系继续存在。若 B 也使用 W1，W1 与该绑定一并保留；若 W1 没有其他使用者，则标记删除并清理文件。

## 实现说明

### 添加与解除的过程

WorkspaceStore 负责身份、路径、名称及其版本、删除状态。LineageDataStore 负责 Resident 的自有引用与额外使用关系。登记和绑定保存参与调用者事务；绑定按差异更新，不对未变化行执行删除重插。严格拒绝旧字段，运行路径不读取旧 JSON 或分类字段作为回退。

### 批量读取与投影

按 Workspace ID 批量查询目录，避免逐 Resident 调用 ResourceService。按路径查找使用活跃路径唯一索引。文件地址继续支持 `workspace_<id>/file/<relative-path>`，拒绝路径逃逸和符号链接替换。Workspace 索引直接读取专表，以记录数及最新 updated_at 检测新建、改名、移动和删除；不订阅旧 workspace registry 集合，也不读取它的修订号。Resident 与 Lineage 的描述记录仍按各自 registry 集合更新。删除资源不能通过陈旧索引重新暴露文件。

### Workspace 导航与文件预览

一个 Resident 的文件根有四类：Resident Folder、Lineage Folder、自有 Workspace 和全部额外绑定的外部目录。页面从 GetResident 的 `lineage_id` 与 `workspaces` 得到这些根，再用 ReadResource 读取 `<根 ID>/file/<相对路径>`：Resident Folder 的根 ID 是 `resident_<id>`，Lineage Folder 是 `lineage_<id>`，Workspace 是 `workspace_<id>`（decision_ad3e21094f14）。读目录返回直接子项，读文件返回预览或元数据。Resident Folder 与 Lineage Folder 的文件要求对该 Resident 或 Lineage 有 viewer，写在 Project 或“共享”等上级节点上的 viewer 同样适用；Workspace 文件按下文规则检查拥有或绑定它的 Resident。每次请求从当前登记与绑定解析根，不接受任意绝对目录，不跟随符号链接；解除绑定或删除对象后原地址失效。

Resident 工作台的目录、文本预览和媒体下载同样使用这组地址。原始字节由 `/api/resources/content?address=<文件地址>` 流式读取，每次请求重新检查权限，支持媒体分段读取，不放入指令结果。Resident 专用的读取指令、下载句柄以及旧的 node-workspace 与 agent-workbench Resident folder/file/file-raw 读取入口均已删除。

按 Workspace 访问文件的入口有两类：`workspace_<id>/file/<relative-path>` 地址（ReadResource、`/api/resources/content` 与资源引用，以及 UpdateWorkspaceFile），和绝对路径（UpdateWorkspaceFile 的绝对路径形式与 link-file 预览、保存）。这两类入口都按“基本概念”中的规则检查拥有或绑定该 Workspace 的 Resident：Workspace 地址由 Workspace provider 在解析文件时检查，绝对路径在匹配到所在 Workspace 后检查。检查只看调用者自身的权限：Resident 工作时按 Resident 自己的表项判定，不继承发消息者的权限（decision_b820faf42218）。被拒绝的读取与文件不存在返回相同结果，被拒绝的写入返回 `instruction.forbidden`。Portal 仓库这类共享 Workspace 可能被数百个 Resident 绑定，因此向权限端口分批询问等级，遇到满足要求的 Resident 即停止。`${share_root}/Skills` 与 `${share_root}/Agents` 属于共享协作目录，仍对已认证的 User 与 Resident 开放读写，但 `${share_root}/Agents/PersonalAgents` 除外。这个目录里的文件只按 Workspace 规则访问。

文件路径落在个人 AMA 的 Lineage 文件夹内时，只认这个 AMA 本身：只有登记所有者和代表所有者的 Resident 可以访问，由权限端口在询问权限系统之前回答，管理员、实例中增加的表项和临时凭证都不例外（decision_b820faf42218 第 4 节）。其他 Lineage 的 Resident 绑定这个目录或它的上级目录，不因此获得其中文件的权限；登记在 AMA 文件夹上、无人绑定的 Workspace 也不对他人开放。两类入口都按文件的实际路径（符号链接已解析）判断，资源搜索和 ResolveResource 也不向无权者列出这些 Workspace（decision_bd5217d08c09）。

Workspace 根地址统一导航到 `/lineage?workspace=<workspace_id>`。页面通过统一资源解析读取当前目录，在现有 Workspace 导航树中展示，点击文件使用共享 Preview 完成预览和编辑。仅含 Workspace 的入口保留 Chat 的未选择状态，不显示 Resident、Lineage 或聊天室访问错误。从资源链接打开的 Workspace 与当前 Resident 的绑定具有同一 workspace_id 时，侧栏只显示一棵目录树；保留资源入口的展开状态，切换 Resident 后仍可继续浏览。个人组织的文件夹分组独立保留。该临时导航不会创建个人工作区关联副本；刷新按 ID 重新取得目录位置，已删除资源显示不可用，不进入旧目录。Resident 创建表单的其他工作区路径以及 BindWorkspace/UnbindWorkspace 提供绑定操作。Preview 保存后可在干净的编辑模式下手动刷新，刷新后保持编辑模式并显示最新正文；如果读取期间产生了未保存修改，则保留编辑草稿，不以返回内容覆盖。

### 执行前的检查

校验 Resident 活跃、自有引用存在、路径符合 Lineage 布局。Workspace 不存在或已删除时明确失败。迁移独立执行，v35 到 v36 的预演与正式应用使用相同事务；合并重复活跃路径、迁移归属和绑定、清除旧 Workspace JSON 后校验完整 schema。启动只接受新 schema，不自动迁移或保留双写。

### 实现来源与入口定位

Workspace 绑定由 `lineage_workspace_binding.py#mount_reference`、`resident_workspace_domain.py#add_resident_reference_workspace` 和 `resident_workspace_storage.py#replace_bindings` 实现。`resident_domain.py` 是重导出这些操作的聚合入口；`workspace_contracts.py` 定义聊天附件缓存、大小与图片校验条件，不实现绑定关系。入口与附件合同作为关联说明保留，绑定的实现来源只列实际负责的文件。
