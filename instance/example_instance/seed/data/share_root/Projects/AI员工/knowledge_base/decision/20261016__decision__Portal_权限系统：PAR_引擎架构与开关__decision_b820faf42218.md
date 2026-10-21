---
kb_id: decision_b820faf42218
type: decision
project: AI员工
created_at: '2026-10-16'
version:
  number: 70
  created_at: "2026-10-16T09:28:31Z"
  created_by:
    user: "weihao"
    agent: "resident_0d928ae486ba"
  updated_at: "2026-10-21T14:14:56Z"
  updated_by:
    user: ""
    agent: "resident_977d1a613d44"
importance: 9
metaness: 3
ai_generated: false
links:
  source:
  - /home/dirty-base/dirtybase-portal/management_portal/permission_system/__init__.py
  - /home/dirty-base/dirtybase-portal/management_portal/permission_system/inline.py
  - /home/dirty-base/dirtybase-portal/management_portal/permission_system/categories.py
  - /home/dirty-base/dirtybase-portal/management_portal/permission_system/par.py
  - /home/dirty-base/dirtybase-portal/management_portal/permission_system/engine/__init__.py
  - /home/dirty-base/dirtybase-portal/management_portal/permission_system/engine/model.py
  - /home/dirty-base/dirtybase-portal/management_portal/permission_system/engine/decision.py
  - /home/dirty-base/dirtybase-portal/management_portal/permission_system/engine/store.py
  - /home/dirty-base/dirtybase-portal/management_portal/permission_system/scheme/compiler.py
  - /home/dirty-base/dirtybase-portal/management_portal/permission_system/scheme/vocabulary.py
  - /home/dirty-base/dirtybase-portal/management_portal/permission_system/scheme/repository.py
  - /home/dirty-base/dirtybase-portal/management_portal/permission_system/scheme/builtin/default.json
  - /home/dirty-base/dirtybase-portal/management_portal/permission_system/facts.py
  - /home/dirty-base/dirtybase-portal/management_portal/permission_system/requests.py
  - /home/dirty-base/dirtybase-portal/management_portal/permission_system/temporary.py
  - /home/dirty-base/dirtybase-portal/management_portal/permission_system/management.py
  - /home/dirty-base/dirtybase-portal/management_portal/permission_system/schemes.py
  - /home/dirty-base/dirtybase-portal/management_portal/permission_system/authdiff.py
  - /home/dirty-base/dirtybase-portal/management_portal/hatg/catalog/permission.py
  - /home/dirty-base/dirtybase-portal/management_portal/society_service/isa/system_workflows.py
  - /home/dirty-base/dirtybase-portal/tests/cases/test_permission_engine.py
  - /home/dirty-base/dirtybase-portal/tests/cases/test_permission_system_switch.py
  - /home/dirty-base/dirtybase-portal/tests/cases/test_permission_schemes.py
  related:
  - thinking_3cbdd375ebf8
  - decision_600ae27b0538
  - decision_22227d0ad075
  - decision_ea13b85216d0
  - decision_3fe3cca8fd95
  - decision_30528aff7035
  - decision_4527448ad07f
  - decision_bd5217d08c09
  - decision_44cf587c1721
  supersedes:
  - decision_67b7e8951599
  - decision_707d2910d389
  - decision_0fbbd0448003
  - plan_ce8d3c73ac2d
  derived_from:
  - thinking_3cbdd375ebf8
---

# Portal 权限系统：PAR 引擎架构与开关

Portal 的权限系统回答一个问题：执行者 p 能否对资源 r 执行动作 a。它由一个与 Portal 业务无关的通用 PAR 引擎、把 Portal 对象翻译成 PAR 元素的适配层，以及可替换的权限方案组成，全部收在一个权限端口后面，并可以在执行设置中整体关闭。形式模型见 thinking_3cbdd375ebf8；Portal 的默认方案见 decision_600ae27b0538；权限指令见 decision_22227d0ad075。

## 1. 形式基础

- **三类元素**：主体 P（User、Resident）、动作 A（ISA 指令按参数拆出的要求）、资源 R（登记了资源 ID 的对象）。
- **权限树**：P、A、R 各有一棵树。节点是元素的集合，根是全集，子节点是父节点的子集，兄弟节点互不相交。每个元素在树上有唯一位置，属于它所在节点及其全部祖先。
- **权限表**：表项是三棵树上的节点三元组 (X, Y, Z)，带效果 permit 或 deny。
- **判定**：存在一个 permit 表项同时覆盖 (p, a, r)，并且没有任何 deny 表项覆盖它，结果为允许；其余情况拒绝。deny 优先于 permit，落在 deny 覆盖范围内的 permit 不起作用。
- **权限方案**：一套可供选择的权限，由**规则**和**实例**组成。规则写构造方式，例如“Lineage 挂在所属 Project 下”“创建者对新资源有 admin”；实例是具体的三棵树和一张权限表。判定只查实例。系统保存多套权限方案，同一时刻启用一套。规则、实例与生命周期见第 5 节。

## 2. 执行者自身权限

一次调用只按执行者自身的权限判定。Resident 处理 User 或其他 Resident 交来的任务时，用的是它自己在权限表中的权限，系统不合并、不借用发起者的权限，也不对调用链上的主体取交集。调用不携带任何授权主体：Resident 的命令行调用只带执行 ID（`PORTAL_EXECUTION_CONTEXT_FILE` 所指文件中只有 `execution_id`；旧键兼容规则见 decision_fb910c43ab07），用于记录这次调用属于哪一轮。

- **临时凭证**是一个主体把权限交给另一个主体的唯一途径，见 3.6。
- **可信调用链**仍随回合交给 Resident：回合的授权说明写明本轮执行者和消息来源。它不参与判定，只作为 Resident 判断“这件事该不该替对方做”的依据。
- **最坏情况**由“谁能调用谁”决定：假设每个 Resident 都会执行交给它的任何任务，主体 p 间接能做的事是 p 自身权限与它沿调用权限可达的全部主体的权限之并。分配给 Resident 的权限应以此为上限。

## 3. 架构

```text
Portal 业务层（ISA 内核、领域服务、页面入口、ResourceService）
  │ 判定请求：执行者、指令、参数           │ 对象变化：创建、移动、删除
  ▼                                        ▼
权限端口 permission_system（唯一入口；开关决定是否判定）
  ├─ 关闭：没有权限系统，端口对已登录主体直接放行，只保留“个人 AMA 归所有者”和 Portal 管理员保底；权限指令照常可用，经 par.py 不检查调用者
  ├─ 两种模式共用 administrators.py：Portal 管理员保底，在方案之前判定；categories.py：User 分类（Portal 管理员、一般用户、实习用户）；inline.py：随任务凭证的结束与 Project 改名改写树路径（只写 SQL，不加载引擎）
  └─ 开启 → par.py：PAR 引擎读取启用方案的实例
        ① 请求适配层 requests.py      指令 → 一组 (p, a, r)
        ② 事实适配层 facts.py         Portal 对象 → 实例中的位置与表项
        ③ 通用 PAR 引擎 engine/       实例存储、单次判定、反向查询、拒绝原因
        ④ 规则编译器 scheme/compiler.py    校验方案规则，输出引擎输入
        ⑤ 方案仓库 scheme/repository.py    内置方案、自定义方案、启用的方案
        ⑥ 临时凭证 temporary.py       签发、撤销、过期
        ⑦ 管理与分析 management.py    有效权限、试判定、临时凭证；解释与可达性审计只留接口
        ⑧ 方案与表项指令 schemes.py   方案的读取、创建、修改、删除与启用，实例节点与表项的读写；开关开或关都可用
```

依赖只朝下：业务层只调用端口；只有 ① 和 ② 了解 Portal 的对象和指令；③ 只认识 PAR 的树、表和元素 ID，可以单独测试；④ ⑤ 只处理方案规则；⑧ 读写方案和实例，经 ② 生成或补齐实例。

### 3.1 权限端口与开关

方案授权的业务调用只问指令，不另问抽象等级。viewer、contributor、admin 是默认方案 A 树上的普通节点；本节明定的 Portal 管理员保底和第 4 节的固定领域角色另按各自边界检查。业务代码要知道的每件事，都改写成“它保护的那条指令在这里能不能通过”：

- 一次调用能否执行：`authorize_instruction`、`authorize_reads`、`readable_ids`。
- `permitted(actor, 指令, 参数, 资源列表)`：只判定该指令声明在这个参数上的判定槽，逐个资源给出能否通过；`permitted_ids` 返回能通过的资源集合。例如列表中每个 Lineage 的按钮问 UpdateLineage、DeleteLineage 等指令的 `lineage_id`；Workspace 文件按它所属的 Workspace 判定，读问 ReadResource、写问 UpdateWorkspaceFile 的 `address`（第 5.5 节）；Workbench 目录按 GetProject 过滤可读 Project。指令的固定槽（例如对“Portal 运维”的 `@admin`）先判定，不通过时所有资源都不通过；没有判定槽在这个参数上时按固定槽的结果给出。
- `allows(actor, 指令, 参数)`：按完整调用判定，供页面开关和设置项使用；`allows_each` 对同一组参数一次给出多条指令的结果。**页面上的一个按钮只问它自己那条指令**（weihao 2026-10-18）：对象的列表或详情结果带 `allows: {指令名: bool}`，按钮按点击时实际调用的指令显示或停用，不用一个 `can_manage` 代表多个按钮，也不借用另一条指令的结果。当前带 `allows` 的结果：GetPermissionSystem（方案的四条写指令按启用方案判定；表项的三条写指令按每行的 r 判定，这里恒为 true，按钮看 ListPermissionEntries 的 `allows`）、Lineage 列表（UpdateLineage、DeleteLineage、CreateResident、UpdateResident、CreateWorkspaceHandoff）、Resident 会话投影（DeliverMessage、UpdateResidentSettings、OpenIntake（打开与关闭接单共用）、RestartSession、CancelInput、UpdateResident、DeleteResident，以及在本 Lineage 中分支的 ForkResident；输入框能否发送也取 DeliverMessage）、定时与自动触发监控的每一行（UpdateComponent、PauseComponent（暂停与恢复共用）、TriggerEvent、CancelScriptRun、UpdateResidentSettings、CancelInput）、Workbench（UpdateResource）、给出 `r` 的 ListPermissionEntries（在这个 r 上的 CreatePermissionEntry、DeletePermissionEntry，供项目与 Lineage 菜单的授予和删除按钮）、Workspace 文件预览（保存按钮的 UpdateWorkspaceFile）。页面已改读 `allows`，`can_manage` 已从全部结果中删除；会话投影、Resident 文件夹和 Workspace 文件预览中的 `permissions`（`can_edit_resident_file`、`can_create_fork`、`can_edit_task_folder`）也已删除：预览保存只看 `allows.UpdateWorkspaceFile`，且文件必须有 Workspace 地址；消息菜单的分支入口要求后端支持分支（`backend_capabilities.portal_fork`）且 `allows.ForkResident` 为 true。`/api/me` 的 `access.allows` 报告 CreateUser、GetExecutionSettings、UpdateExecutionSettings（只问权限系统开关，即参数 `permission_system_enabled`）、ActivatePermissionScheme、UpdateProviderAccount、OpenTerminal、ListResourceTypes、UpdateResourceType、ListProviders、ListProviderAccounts（另有调用者自己的 `user_category`），管理页入口、执行设置、账号调度表单和 Society 页面的终端组件据此显示；对 Portal 管理员，UpdateExecutionSettings 与 ActivatePermissionScheme 两项永远为 true（Portal 管理员保底）；管理页用户表单（包括 User 分类）按该行的 `allows.UpdateUser` 判断，为 false 时只读，管理员名单相关字段仍须结合 `user_category` 限制，见本节保底规则；执行设置在服务端同样按 Get/UpdateExecutionSettings 判定；修改账号状态问 DeleteUser；把运行身份设为其他 User 问对该 User 的 UpdateUser；每个打开 Terminal 的入口问 OpenTerminal。
- `users_permitted(指令, 参数, 资源)`：启用方案实例中让这条指令在该资源上通过的、未停用且不过期的表项所点名的活跃 User，Lineage 自动失败通知按 UpdateLineage 取收件人；表项写在 User 分类节点上时，该分类的每个 User 都算在内，写在更上层节点（全部 User、全部主体）上的不点名任何人。关闭时没有权限系统可问，返回 None；失败通知模块此时把通知发给 Lineage 的创建 User，这是通知模块自己的回退。
- `user_categories()`、`user_category(用户名)`、`set_user_category(...)`：User 分类这项事实，保存在账号记录的 `category` 字段，开关开或关都可读写（UpdateUser 的 `changes.category`，第 3.3 节）。设为或取消 Portal 管理员只有 Portal 管理员能做（下文“Portal 管理员保底”）；一般用户与实习用户之间的调整关闭时已登录主体都能做，开启时按方案判定。用户列表和 `/api/me` 读取它。

对象变化时也只通知端口：`sync_resource`（创建或修改，携带记录；删除记录也走这里）、`retire_resources`、`sync_user`、`rename_element`（Project 改名时改写全部方案实例中的树路径）。权限指令通过 `manage` 进入。

能通过 UpdateExecutionSettings 的主体在“执行设置”的“权限”分组切换开关，对应该指令的 `permission_system_enabled`，保存在 `execution_runtime.service_settings`。每个进程最多 3 秒后读到新值，读取失败时沿用上次的值，从未读到时按开启处理。

**Portal 管理员保底**（weihao 2026-10-19，da10d535）。权限方案可以把任何主体挡在外面，包括写方案的人自己。开关因此可以直接打开：不做打开前的调用回放、不做推算，也不做打开后的观察机制（第 5.10 节仍是预留），保底是一条独立于方案的 Portal 管理员规则。它和“关闭时个人 AMA 归所有者”是方案之外仅有的两条判定；前者开关开或关、启用哪套方案都一样，由端口在方案之前判定，方案无法收回：

- Portal 管理员永远可以开关权限系统（UpdateExecutionSettings 只改 `permission_system_enabled`），永远可以读取方案列表（ListPermissionSchemes）和启用方案（ActivatePermissionScheme）。这是一条放行：管理员做这三件事时入口检查、`allows`、编辑器按钮、CheckPermission 和设置处理代码都不问引擎（CheckPermission 返回 `portal_admin: true`、`requests` 为空）。读取方案列表在保底内（9c94 按 weihao“无论方案怎么设置，管理员永远能切换方案”定，2026-10-19）：执行设置的方案下拉框靠它列出可选方案，读不到时启用的保底在页面上不成立；它只读，没有风险。只加这一条读指令，读取单个方案（GetPermissionScheme）等其余权限指令仍按方案判定。需要所有人都不受限时，出口仍是关闭开关。同一次调用里还有其他设置项时，整次调用按方案判定。非管理员照常按方案判定，开关关闭时照常放行。
- 管理员名单只有 Portal 管理员能改：把其他 User 设为或取消 Portal 管理员，停用、启用或删除管理员账号（删除或停用管理员等同于取消其管理员身份）；不能对自己做。开关关闭时同样只有管理员能改，这推翻了 10-18“关闭时谁都能改分类、本人能改自己的分类”中涉及管理员的部分。管理员做这类只改名单的调用（UpdateUser 的 `changes` 只含 `category`、`status`，或删除管理员）时也不问方案；非管理员一律以 `instruction.forbidden` 拒绝。一般用户与实习用户之间的调整不在名单之内，按方案判定，关闭时放行。管理员不能取消自己，但两位管理员可能同时互相停用；所以名单变更的检查和写入在同一个账号事务里进行，持有 identity 锁（`administrators.list_change`），并发的变更依次判定，后一个看到前一个的写入。取消管理员的写入完成后，如果已经没有启用中的 Portal 管理员，整次回滚。删除管理员时先在这个事务里停用账号，其余删除步骤（包括调用 SocietyService）在事务之后进行。管理员只能是 User，Resident 没有分类，永远不是管理员。
- 新部署时第一个创建的账号在同一事务中成为 Portal 管理员，不论它来自设置链接、普通邀请还是 CreateUser；已删除账号留下的记录也算账号。首个管理员设置链接只在 Portal 还没有任何账号时可用，自己不授予任何东西，管理员身份来自这条规则。
- 名单仍保存在账号记录的 `category = portal_admin`，方案的 `@portal_admins` 节点照旧从它收人；保底只决定谁能设、清 `portal_admin`，以及上面两条无条件放行。
- 已知边界：保底只管管理员名单、开关和启用方案。开关关闭时，管理员账号的密码、访问令牌和找回邮箱与其他账号一样，任何已登录主体都能修改（ResetUserPassword、CreateUserAccessToken、UpdateUser 改 `recovery_email`、RevokeUserSessions）。weihao 2026-10-19 确认不收紧，这些账号凭据和找回方式不并入保底。

规则集中在 `permission_system/administrators.py`，不加载引擎。GetInstructionSchema 的 `permission.portal_admin` 对 UpdateExecutionSettings、ListPermissionSchemes、ActivatePermissionScheme、UpdateUser、DeleteUser 写出这条保底，开关关闭时也写。

**关闭就是没有权限系统**（weihao 2026-10-18）。端口不导入引擎，只检查已登录（有效的 User、Resident 或 Portal 自身）、目标不是别人的个人 AMA，以及上面的管理员名单规则。其余一律放行，包括 OpenTerminal；指令声明中没有按 User 分类的例外，GetInstructionSchema 的 `permission` 报告不需要权限（Portal 管理员保底照样写出）。一般用户与实习用户之间的分类调整同样放行（weihao 2026-10-18），开启时由方案对 UpdateUser 的要求判定。`allows` 对已登录主体都为真；`permitted` 收到资源的登记所有者（`owners`）时，只有所有者、代表所有者的 Resident 和 Portal 自身通过（`acts_for`），其他资源对已登录主体都通过；`private_reaches(actor, 所有者)` 与 `hidden_private(actor, 记录)` 给领域代码回答同一条规则。个人 AMA 在关闭时仍归所有者，因为它装的是一个人的私人对话、文件和凭据：关闭开关是管理员为了修复方案或排障而做的操作，不应顺带把每个人的私人空间公开给所有已登录主体。开启时这条规则由方案的树表达（第 5.5 节），端口不再拦截，`owners` 参数不起作用；`private_reaches(actor, 所有者, 读指令, 参数, 资源)` 改为问这条读指令（Lineage 问 GetLineage，Resident 问 GetResident），所以列表、目录和监控按同一读判定隐去调用者读不到的个人 AMA，Portal 管理员的列表中也没有别人的 AMA。这类过滤调用 `readable_ids`，被过滤掉的对象不写“permission refused”日志。

关闭期间方案和实例照常可以查看和编辑，只是不在当前系统中生效。全部权限指令（第 3.7 节）在两种模式下由同一份代码处理，区别只在调用者校验：开启时按指令的判定槽与启用方案的分类判定，关闭时任何已登录主体都可以调用，不检查调用者在实例上的权限。方案的创建、修改、删除和启用与开启时相同，编译错误和版本冲突的返回也相同；ActivatePermissionScheme 只记录开启时使用哪套方案，并报告开启时补齐将新增、移动、删除多少节点和表项；GetPermissionSystem 返回 `enabled: false`、`mode: open` 和这套方案，方案列表与读取也把它标为启用。表项写入启用方案（或指定方案）保存的实例，开启时随实例生效；GetPermissionNode、SearchPermissionNodes、ListPermissionEntries 读取的也是这份实例。关闭期间对象变化不维护实例（只记录创建者），所以实例中缺少关闭期间新建的对象，开启时补齐。理由是关闭期间任何已登录主体都能修改并启用方案，方案能改变全部规则，单独收紧表项不增加保护；开启开关的人可以用 ListPermissionEntries 和 ActivatePermissionScheme 的 dry_run 查看将生效的实例。临时凭证照常签发、续期和撤销，写入这份实例，开启后生效；有效权限、试判定和临时凭证列表按这份实例作答。

开启前，在开关仍关闭时由 ResourceService 把启用方案的实例补齐到全部现有对象（RPC `permission.prepare`），然后写入开关；方案从未生成实例、或实例由另一版规则生成时，改为按现有对象生成。

开关保持开启（weihao 2026-10-19，2575e9af 走查时打开）。WALL 与 Test 归在 AI员工 Project 下，开启后其他 Project 的 Resident 对它们只有 viewer：不为此加实例中的编辑，也不把这些 Resident 加入 AI员工；需要写入时，由有权限的主体签发临时凭证（第 3.6 节）。

### 3.2 请求适配层

一条 ISA 指令按它声明的判定槽拆成一组请求（weihao 2026-10-19）。判定槽写在指令声明的 `decides` 中（decision_898b8911a2ff“声明格式”），随代码发布：参数槽按参数所指的对象判定，可带类型限定、`optional`、`via`（按已保存记录的字段判定）或 `place`（按新对象将要落下的位置判定）；固定槽不指向对象。每个判定槽是一个 A 元素：指令只有一个槽时，元素就是指令名；有多个槽时，元素是 `指令:参数` 或 `指令:固定槽名`。方案只给 A 元素分类：把它放在一个 A 树分类节点下，固定槽另指定一个 R 树分类节点（例如默认方案中 CreateUser 在“管理”下、对“共享”判定）。参数槽所指的每个资源（地址取根 ID，列表取每一项）是一个 r，资源类型不符或必填参数缺失时返回 `instruction.invalid_arguments`。拥有服务可以在判定前补上调用者能省略、由服务推导的参数（`OwnerBinding.authorization_arguments`），记录的调用和处理代码仍用调用者给出的参数：目前只有 RunScript，按运行所属的 Lineage 判定，这个 Lineage 与处理代码使用的由同一个函数确定（decision_8b510cbd9c58）。方案没有分类的 A 元素放在 A 树的默认节点下，没有分类的固定槽按 R 树的默认节点判定，参数槽仍按声明拆出对象：默认方案为“未归类”和“共享”，只有管理员表项覆盖，新增指令忘记分类时只有管理员能用。所有人都能调用的指令必须显式放在免检节点下（默认方案写成 `{"a": "@free", "r": "@portal"}`）。编译后的方案对有未归类元素的指令，`describe` 带 `unmapped: true`；GetPermissionScheme 与方案指令的 `dry_run` 列出未归类的 A 元素。一组请求在一次引擎调用中判定，全部允许才放行；读指令被拒时报告 `instruction.not_found`，写指令报告 `instruction.forbidden`，details 带目标、动作、要求挂靠的 A 节点（`a`）和原因（`no_permit` 或 `denied`）。CancelInput 要求对目标 Resident 有 `@contributor`，输入的发起人不另外放行。

### 3.3 事实适配层

事实适配层把一个 Portal 对象读成一个事实：类型、所属对象（Lineage 的 Project、Resident 的 Lineage、Resident 自有的 Workspace 等）、创建者、Family 的 steward、聊天室成员。它维护启用方案的实例（`facts.Instance`），规则只在对象第一次出现时作用到这个对象本身：

- **新对象**：按方案规则放到 R 树和 P 树上，写入规则给这个对象的表项，例如 Project 内管理 (Project, @admin, Project)、Project 创建人 (创建者, @admin, Project)、steward (steward, @admin, Family 所在 Lineage)。实例在 `scheme_objects` 中记下它认识的每个对象及其 steward、成员关系；不在其中的对象就是新对象。
- **对象移动**：只改它在树上的位置，子树随之移动；表项引用节点，不受影响，人改过的表项保持原样。
- **关系变化**：Family 换 steward、聊天室成员变化时，按旧关系生成的表项按内容删除，写入新关系的表项。
- **对象删除**：离开两棵树，指名它的节点的表项一并删除，不论表项最初怎样写入；它的关系表项（例如 steward 在 Lineage 上的那行）按内容删除。

个人 AMA 的 Lineage 按方案规则放置，事实层没有特殊处理：个人 Lineage 的事实以所有者的个人空间和所有者 User 为关系对象（不属于任何 Project），方案的规则节点用 `under` 指定按哪种关系放置（第 5.9 节）。一类对象在一棵树中可以有多个规则节点，每个节点按一种关系放置，事实按自己有的关系选用第一个匹配的节点。默认方案在 P 树“User”下放“个人 AMA（在所有者名下）”（`each: lineage`、`under: user`），在 R 树“个人空间”下放“个人 AMA”（`under: personal_space`）；其中的 Resident 随 Lineage 放置，因此与所有者权限相同（decision_600ae27b0538 §2、§3）。关系对象尚未放上树时，对象按规则节点“没有关系时”的去处放置，不为关系对象预留位置。没有放进 P 树的主体落在 P 树默认节点下，默认方案为不带分类的“User”节点。

- 写入时机（第 5.7 节）：Lineage、Resident、Family 的描述投影在 ResourceService 保存描述的同一数据库事务中更新实例；Project 在登记记录的事务中（`project_registry.mutate` 的 `before_commit`）、聊天室在房间记录的事务中、帖子在帖子记录的事务中更新。知识条目、论文、skill、repo、article、experiment、Workbench、device 等经 ResourceService 登记的资源由文件或各自的存储写入，没有可共享的数据库事务，仍在提交后紧接着更新；User 在创建或删除账号时更新。
- 事实从对象记录读取：创建者是记录的 `created_by`（帖子是作者），Resident 处理某位 User 的输入时创建的对象另有 `created_for`，两者都是创建者。权限端口的 `creation_fields` 给创建对象的存储计算这两项：`on_behalf_of` 从调用上下文的执行 ID 找到这一轮（必须是该 Resident 正在运行的执行），取其输入消息经认证的发起 User（decision_600ae27b0538 §4）。只有现存的 User 或 Resident 作为创建者得到表项，导入脚本这类创建者不得到。Family 的 steward 和聊天室成员同样每次从对象记录读取。User 分类保存在账号记录的 `category` 字段，开关关闭时也读写这里；开启时，方案中声明了 `user_category` 的分类节点收下该分类的 User，改分类就是在 P 树中移动；没有记录的 User 是一般用户。
- **生成实例**（`generate`）：ResourceService 读取全部现有对象——账号、Project 登记、协作描述、不在资源索引中的功能对象（聊天室、帖子，由各功能经功能登记的 `permission.records` 接入点提供，decision_15aab18c0ab3）和资源索引中的全部记录——在一个事务内写出两棵树和规则给出的全部表项，记录实例由哪版规则生成（`rules_digest`，规则中树和表项的摘要，不含标题）以及这版规则的全文（`rules`）。实例中的编辑不保留，临时凭证保留。创建方案（不复制时）、修改方案规则时生成；ResourceService 启动并完成索引预热后，只让发布改变了规则的内置方案自己的实例跟随新规则（实例的 `rules_digest` 与当前内置规则不同），按新规则重新生成并写回实例中的编辑（第 5.11 节）；从未生成过实例的内置方案只在它正被启用时生成，开关开或关都一样。随后对来源内置规则已变化的复制品做三方合并，只有树或表规则变化时才重新生成复制品的实例，同样写回实例中的编辑；其他自定义方案的实例在启动时不动。开启时，对象增量同步读取当前启用的方案；关闭期间不维护实例，切换方案后仍须在再次开启时补齐（第 3.1 节）。
- **补齐**（`complete`）：把已保存的实例带到现有对象的状态，保留实例中已有的每一行：新对象按规则加入，移动的对象改位置，删除的对象连同指名它的表项离开，关系变化换掉关系表项。启用方案和开启开关时补齐；ResourceService 完成索引预热后对启用方案补齐一次，之后每小时一次，修复提交后同步遗漏的资源（第 5.7 节）。启动和定时补齐无人值守，将移出的对象超过 100 个时整次不写入并记日志，因为这多半是事实读少了；补齐只在启用方案已有实例时运行，从不重新生成。`dry_run` 只报告数量。生成和补齐都在开始时锁住实例表，增量变化等它们提交后再写。

### 3.4 通用 PAR 引擎

- **存储**（PostgreSQL schema `permission`）：每套方案的实例按 `scheme_id` 分开保存。`scheme_nodes` 每个元素一行，保存父节点和根到元素的物化路径（`path` 数组，GIN 索引），一行就能取得全部祖先，`path && 节点集合` 就能取出整棵子树；没有行的元素位于所在树的默认父节点下。`scheme_entries` 保存表项：`entry_id`、效果、P/A/R 节点、`disabled`、`version`，临时凭证另有签发者、签发时间、过期时间和随任务签发时的 `message_id`；同一方案中内容相同的长期表项只有一行。`state` 只有一行，保存实例版本号（第 5.6 节）：表项增删改、停用启用、临时凭证签发、续期、撤销和结束、P 树中主体位置变化、生成与补齐、启用另一套方案，都在同一事务中让它加一。`scheme_instances` 记录每份实例由哪版规则生成；`scheme_objects` 记录实例认识的对象及其关系；`schemes` 保存自定义方案的规则，`active_scheme` 记录启用的方案（没有记录时为默认方案）。数据库是唯一真相，进程内缓存都可以重建。
- **单次判定**：一次判定只读一条 SQL，同时取回实例版本号、启用的方案及其规则版本、主体的路径和所问资源的路径。每个进程按主体缓存“适用于该主体的未停用、未过期表项”，并记下建立缓存时的版本号；读到的版本号不同，就清空本进程的全部缓存再重算。缓存中每个 permit 表项挂着可能与它相交的 deny 表项（洞），这是由两张表派生的索引。判定沿动作路径和资源路径做哈希查找，找到覆盖的 permit 后只检查它的洞，大多数 permit 没有洞，判定即结束。代价只与树深有关，与主体、动作和资源的数量无关。
- **反向查询**：给出主体和一个 A 节点，引擎从缓存算出被授权的 R 节点集合及其中的洞，把它们写成 `path && 授权节点 AND NOT path && 洞节点` 的条件在数据库中过滤，不逐个判定。
- **拒绝原因**：判定结果区分“没有 permit 表项覆盖”和“命中 deny 表项”，并给出命中的表项。
- **方案切换**：另一个进程启用了另一套方案或修改了启用方案的规则时，同一条 SQL 读到的方案或规则版本与本进程持有的不同；实现加载新的方案，按它的指令分类重新生成请求，再读一次路径后判定，没有异常重试。

### 3.5 规则编译器与方案仓库

方案规则是一份 JSON 数据（新写入 `tfi.permission-scheme.v2`，读取仅接受该精确格式），顶层只有 `schema`、`scheme_id`、`title`、`description`、`trees`、`entries`、`instructions`：三棵树的声明节点、表规则、A 元素的分类。树上的节点分两类：**分类节点**是固定集合，例如“共享”“管理（admin）”；**规则节点**带 `each`，表示“每个某类对象”，并写一条放置规则（第 5.9 节）：`under` 写放在哪类关系对象之下（例如 R 树中“每个 Project”下的“每个 Lineage”写 `under: project`），`otherwise` 写没有这个关系时放到本树哪个分类节点。挂在规则节点下的规则节点，`under` 必须是父节点的对象类型，`otherwise` 省略时为树的兜底节点（`default_parent`）；挂在分类节点下的规则节点没有关系时就放在这个分类节点下，不写 `otherwise`。方案没有覆盖的对象类型放在兜底节点下。P 树的分类节点可以带 `user_category`（`portal_admin`、`member` 或 `intern`），表示该分类的 User 放在这个节点下，每个分类最多一个节点。表规则分两种（第 5.8 节）：**节点规则** `{p, a, r}` 三者都是分类节点，生成实例中的一行固定表项；**关系规则** `{relation, a, r}` 的 `r` 是 R 树的规则节点，事实适配层对其中每个对象按关系找到主体，各写一行。可用的对象类型、放置关系（`placements`）、关系（`relations`）、资源类型和 User 分类由 `scheme/vocabulary.py` 给出（GetPermissionScheme 的 `vocabulary` 另附全部 ISA 指令及参数，每条带 `elements`：它声明的判定槽，每项是一个 A 元素）。`instructions` 写 `{"<A 元素>": {"a": <A 树分类节点>, "r": <R 树分类节点，只用于固定槽>}}`，编译后该元素挂在 `a` 下，覆盖该节点的表项就打开这个元素；方案不能写 `argument`、`types`、`optional`、`via`、`place`，判定对象由声明给出（weihao 2026-10-19）。参数槽的参数是已保存记录的 ID 时，声明写 `via="<记录>.<字段>"`、不写类型：入口按 ID 读出记录，用记录在该字段里保存的对象作为判定对象；记录不存在时整次调用返回 `instruction.not_found`，记录在该字段里没有对象时这个槽不产生请求。可用的记录字段由 `vocabulary.RECORDS` 给出（编辑器读 `vocabulary.records`），目前有 `permission_entry.r`（表项已保存的 r）和 `instruction_execution.lineage`（回执所属的 Lineage，调用者作用域的回执没有）。创建对象的指令按新对象将要落下的位置判定时，声明写 `place={<关系对象类型>: (参数路径…)}`，槽的参数是新对象的类型参数，不写 `via` 和类型：入口取每种关系对象第一个给出的 ID，按放置规则算出新对象在 R 树中的父节点（关系对象，没有时为 `otherwise`，类型没有放置规则时为兜底节点），和事实适配层登记后放置它的计算相同（`CompiledScheme.placement`）；`@public` 这类分类节点不是对象，按没有关系对象处理。`place` 的键由 `vocabulary.place_related` 给出（目前默认方案只用于 RegisterResource，decision_600ae27b0538）。编译器不认识任何节点名；例如默认方案把发消息的几条指令放在“协作（contributor）”下的“给 Resident 发消息”节点，一条表项就能只打开这一组指令。

编译器检查结构：三棵树各有一个根、父节点都已声明、没有环；分类节点不挂在规则节点下；`user_category` 只写在 P 树分类节点上，取值有效且不重复；每类对象在一棵树里按同一关系只有一个规则节点，并且确实有它声明的放置关系；`otherwise` 是本树分类节点；节点规则只引用分类节点，关系规则的关系取自固定种类并只用于它适用的对象（“所属”还要求方案在 P 树放置这类对象）；一条表项不能同时有 `p` 和 `relation`；`instructions` 中的每个 A 元素都由某条指令声明，`a` 是 A 树分类节点，`r` 只写在固定槽上且是 R 树分类节点。判定槽本身的结构（参数已声明、via 与类型互斥、place 的参数路径已声明）在指令声明构造时检查，登记情况由声明规则测试核对。错误逐条返回，每条带规则中的 JSON 路径。这项检查只针对新建和修改的规则：读取已有方案（内置或已保存）时，分类中本 Portal 没有声明的 A 元素被跳过并在日志中告警一次，规则对其余元素照常生效，读出的规则也不含这些元素，所以复制或修改后保存时它们随之去掉。打包时摘掉一个功能，它的指令随之消失，方案不因此无法编译（decision_15aab18c0ab3）。编译器不检查表规则之间的含义，例如 deny 覆盖范围内的 permit 照常编译。

方案仓库：内置方案的规则以文件随代码发布，永不原地修改；自定义方案的规则保存在 `permission.schemes`，带版本号，修改按版本比较后写入。启用的方案记录在 `permission.active_scheme`，开关开或关含义相同；启用的方案不能删除，删除自定义方案时连同它的实例一起删除。内置方案的规则只读，方案本身可以直接使用：可以启用，它的实例照常增删表项、停用行、签发临时凭证；要改规则，先用 CreatePermissionScheme 复制（连同实例）为自定义方案（第 5.11 节）。线上启用的是默认方案。内置方案只有**默认方案**：Portal 的分级权限规则，见 decision_600ae27b0538。需要所有人都能做所有事时，由管理员关闭开关（weihao 2026-10-19，此前的内置“全开方案”已删除）。

管理页“权限方案”分栏的编辑器见 decision_ea13b85216d0；“执行设置”的“权限”分组可以直接选择启用的方案。

### 3.6 临时凭证

临时凭证把签发者此刻持有的权限交给接收者，保存在方案实例的 permit 行中。签发、随任务结束、按时长到期、续期、撤销以及重新生成时的处理统一见第 5.6 节；正式指令参数与返回见 decision_22227d0ad075。开放模式下的实例与凭证含义仍遵循第 3.1 节。

### 3.7 管理与分析

权限指令的作用和参数见 decision_22227d0ad075：读取开关与启用方案、方案的增删改与启用、读取和搜索树上的节点、按三棵树的节点和范围列出表项、增改删实例表项、查看主体的有效权限、试判定、临时凭证；User 分类由用户指令 UpdateUser 的 `changes.category` 修改（decision_6eac174d85bf）。方案与表项指令在 `schemes.py` 中，两种模式共用；写表项只按指令的判定槽与方案的分类判定：启用方案实例中的一行，调用者要能在这行的 r 上通过 CreatePermissionEntry；其他方案实例中的行，调用者要能通过 UpdatePermissionScheme。写入只检查 p、a、r 是方案树上的节点或有效主体，不检查表项之间的含义。查看主体的有效权限、试判定和临时凭证列表，由指令对 `principal_id` 声明要求，把这个 User 或 Resident 当作 R 树上的资源判定；ListPermissionEntries 的 `p` 与 `r` 至少给一个，各按自己的声明要求判定，不再有“省略即列出自己”。GetEffectivePermissions 按 A 树每个分类节点给出覆盖范围（`coverage`），User 另报 `user_category`，CheckPermission 用 `a` 与 `resource_id` 试判定一个节点。拒绝解释的展示和可达性审计（Reach）只在管理模块中保留接口，暂不实现。

User 与 Resident 卡片的 Package 读取 GetEffectivePermissions，以奖章形式展示主体的表项（Portal 管理员分类节点上的表项显示为一枚独立的奖章），奖章标出表项的 A 节点，颜色区分 permit 与 deny。Project 与 Lineage Menu 的 Package 写入表项时，A 节点从启用方案的 A 树读取。

## 4. 权限与领域功能的划分

**方案表达的授权由 PAR 引擎判定。** 引擎拿到权限树和权限表后作答；业务代码和指令处理器不另写与方案竞争的授权。权限端口保留第 3.1 节明定的管理员保底与关闭时个人 AMA 边界，本节的领域规则也继续有效。A 树、R 树上的节点名（如“免检”“共享”）对代码没有语义，只是集合节点：一条指令落在“免检”下，所有人都能调用，完全因为表里有 (P 根, 免检, R 根) permit。

**划分标准**（weihao 2026-10-18）：

- 规则只关心**调用者在这个对象上是不是那个固定角色**（作者、领取人、自己的信箱、门面、steward），属于 Portal 的领域功能，写在领域代码中，不经过权限系统。就像聊天软件里只能撤回自己的消息。
- 规则在回答**谁可以操作别人的对象**，就是权限，必须由方案的树和表表达，经引擎判定。

按这个标准，以下属于领域功能，开关关闭时依然生效：

- 身份认证与防冒充：登录、Session、令牌、两步验证与 Passkey 照常检查；执行者由会话或令牌确定，不能自报（decision_3fe3cca8fd95）。
- 只属于本人的数据：个人秘密（Git 凭据、个人访问令牌）、Mailbox、个人待办、导航、上传；编辑或撤回自己的消息，删除自己的回复，退出聊天室。
- 工作流角色：Family 的门面与 steward、Leader、摘要任务的领取与提交（decision_44cf587c1721）。
- 安全护栏与数据完整性：对象状态、版本、路径约束和破坏性操作的确认。

以下属于权限，由引擎判定，代码中不保留对应的判断：

- 个人 AMA 的私有性：只靠树上的位置表达（第 5.5 节）。
- Workspace 文件的读写，包括按绝对路径访问（第 5.5 节）。
- 查看他人的表项、有效权限和临时凭证：指令对 `principal_id` 声明要求，由表判定。
- Family 进度类指令中“Lineage 管理者”这一分支：指令对 `lineage_id` 声明要求，由表判定；门面与 steward 仍作为领域角色核对。
- 权限指令本身：谁能编辑方案、写表项，按指令的判定槽与方案的分类判定。修改和删除表项的判定对象来自已保存的表项（decision_898b8911a2ff“判定对象可以来自已保存的记录”）：UpdatePermissionEntry 声明 `On("entry_id", via="permission_entry.r")` 加可选的 `On("r", optional=True)`，DeletePermissionEntry 的 entry_id、entry_ids 都按 `via="permission_entry.r"` 判定，默认方案把这些元素都放在 @admin 下；入口读出表项已保存的 r 后和其他请求一起判定，批量删除时任何一条不通过整批拒绝，处理代码不再问引擎。只看入口参数里的 entry_id 时，表项本身不被判定，任何人都能删除或改写管理员表项。

临时凭证还须执行自身定义的检查（第 5.6 节）：签发者此刻必须在该资源上持有所交出的 A 节点。这是临时凭证的定义（“把自己有的权限临时交给别人”），不属于另加的规则。

消息中的资源引用不做权限检查：发送方可以引用任何地址，收件方读取时按自己的权限判定（weihao 2026-10-18）。

开关关闭时，除第 3.1 节的管理员保底外，权限端口保留“个人 AMA 归所有者”：领域代码的 `can_manage_resident`、`actor_can_manage_lineage`、`authorized_resident_ids` 等经端口的 `private_reaches` 回答它；开启时个人 AMA 由方案的树表达，这些函数对个人 AMA 改问对应的读指令（GetLineage、GetResident），对其他对象只检查身份。

**代码中保留的检查**（2026-10-18 全仓审计，对象是 `management_portal` 中全部 `PermissionError` 与显式身份比较）：

- 身份：登录、Session、令牌、两步验证、Passkey 与再认证；执行者不能自报，RPC 调用者必须与声明的 actor 一致；“只有 User 能做”“只有 Resident 能发给用户”这类按主体种类的规则（例如 Telepus 看板上登录的 User 维护自己的联系人，打开 Terminal 不给 Resident）。
- 本人的数据：Git 凭据、个人访问令牌、Pulse 条目、Mailbox、待办、导航、上传；聊天室中编辑或撤回自己的消息、删除自己的回复；论文与 WALL 任务按发起人列出。
- 工作流角色：Family 的门面、steward 与 Router，Leader，工作摘要只由 Resident 本人或 steward 写入，摘要任务的领取人，输入的认证发起人，Execution 的提交者与 Worker 领取。
- 护栏：路径、根目录与符号链接约束，对象状态与版本，至少保留一位启用中的 Portal 管理员，首位管理员设置链接只在还没有任何账号时可用。
- 个人 AMA 在开关关闭时归所有者，以及 Portal 管理员保底（第 3.1 节）：方案之外仅有的两条判定。

审计找到的只按身份放行、没有询问引擎的入口，都归入已有指令判定（weihao 2026-10-18）：

- 账号接单汇总只统计调用者 GetResident 通过的 Resident；“全部打开”“全部关闭”把其中的 Resident 列表交给 OpenIntake（`open: true` 或 `false`）逐个判定，按 `account_id` 批量的写法已删除。
- 资源备份与校验的按钮询问资源备份 Lineage 上的 TriggerEvent，取消询问 CancelScriptRun；后端触发 Event 时由 TriggerEvent 自身判定，不再单独检查身份。
- WALL 项目登记与注销询问所在 Project 的 UpdateProject。
- 按 `job_id` 读取论文评审状态已改为按论文判定。
- Watchboard 的运行按钮询问所在 Lineage 的 TriggerEvent，每个配置的编辑询问该文件地址的 UpdateWorkspaceFile，看板管理询问 ApplyLineageChanges。Telepus 看板上维护自己的联系人是固定角色规则（登录的 User 只改本人），BindTelepusContactForUser 与 UnbindTelepusContactForUser 仍只给管理员。
- `/api/admin/access-control` 读取全部用户与访问审计，不另设判定（weihao 2026-10-18）：开关关闭时所有登录主体拥有全部权限，没有管理员之分；除 Portal 管理员保底（第 3.1 节）外，代码不按 Portal 管理员之类的分类放行，一切按权限系统处理。用户列表逐行带 `allows.UpdateUser`。

## 5. 权限方案：规则、实例与生命周期

weihao 2026-10-18 定稿。5.1 至 5.3、5.5 与 5.6 已实现，第 3 节按实现描述；5.4 的编辑界面由 decision_ea13b85216d0 跟进。5.7 至 5.9 已实现（2026-10-18），各节末尾写明实现状态；5.10 的调用回放检查仍为预留，不因已有分析辅助函数而视为实现；5.7 中没有可共享事务的资源按 weihao 2026-10-18 的决定写入后同步，由启动和定时补齐修复遗漏（已实现，2026-10-18）。5.11 已实现：复制品的来源记录与合并（2026-10-18），内置方案直接启用与跟随发布的写回（2026-10-19）。

### 5.1 组成

一个权限方案包含：

- **规则**：树规则、表规则、指令分类（原“模板”的内容）。内置的“默认方案”的规则随代码发布，不原地修改；复制后得到可编辑的方案。
- **实例**：这套方案自己的三棵树和权限表，随方案保存。下次启用时加载的就是上次保存的实例。

### 5.2 实例的表就是表

- 表项只有 (P 节点, A 节点, R 节点, permit/deny) 和停用标记；临时凭证另带签发者和过期时间。表项不记录来源，不区分它是规则产生的还是人写入的。
- 人可以直接编辑实例的表：新增、删除、修改表项，停用或启用一行。编辑的结果就是表的样子，代码不再按规则校正它，也不推导其他表项。
- 实例相对规则生成结果增加、删除和停用的行，统称**实例中的编辑**（weihao 2026-10-19）。表里不标记它们；只有拿实例和规则对现有对象生成的结果比较，才知道哪些行是编辑。规则变化后的写回（第 5.11 节）就是这样找出编辑的。
- 关系规则（创建者、steward 等）对每个对象写一行，不判断它是否已被其他表项覆盖。代码中没有“哪条表项被哪条更一般的表项覆盖”的记录。
- 写入表项时不做语义检查。permit 落在 deny 之内时，按 deny 优先照常判定，这条 permit 不起作用；编辑器可以给出不阻止保存的提示。
- 不做防锁死检查。编辑权交给了人，表的样子由人负责；退路是执行设置里的权限系统开关（第 3.1 节），关闭后再修复方案。
- 谁能编辑一套方案的规则和实例，仍由方案的指令分类判定。

### 5.3 生命周期

| 事件 | 处理 |
|---|---|
| 新对象出现 | 按启用方案的规则求出它在树上的位置和以它为对象的表项，写入实例；可共享数据库事务的对象与创建同事务完成，其他资源按第 5.7 节提交后同步并补齐；其他表项不动 |
| 对象删除 | 删除它的节点和所有引用该节点的表项，不论表项是谁写的 |
| 对象移动 | 只改它在树上的位置；表项引用的是节点，不受影响 |
| 编辑表项 | 只改这一行 |
| 修改方案规则 | 编译规则，按全部现有对象重新生成这套方案的实例；此前对实例的编辑不保留。保存前的 dry_run 报告新旧实例的差异：增删的表项和将丢失的编辑 |
| 发布改变内置方案的规则 | 内置方案的实例按新规则重新生成，并写回实例中的编辑（第 5.11 节）；从它复制出的方案按来源合并 |
| 启用另一套方案 | 加载它保存的实例并补齐：停用期间新增的对象按规则加入，停用期间删除的对象及引用它们的表项删除；人编辑过的部分保持原样 |
| 打开权限系统开关 | 与“启用”相同：所选方案补齐后生效 |

- 停用的方案不随对象变化维护，启用时补齐。
- 创建者、steward、User 分类是跨方案的事实（第 3.3 节），规则作用到新对象时读取；开关关闭时照常记录。
- 判定缓存：实例的表每改一次，版本号加一。各进程发现版本变化后，让缓存失效，下次查询时重算。版本号只是“表改过几次”的计数，不记录任何表项内容。

### 5.4 编辑界面

管理页权限编辑器（decision_ea13b85216d0）分两部分：方案规则的编辑沿用现有树状图和表规则界面，去掉表规则上的“启用”勾选；实例表的编辑视图按主体、动作、资源过滤并分页，支持新增、删除、修改和停用/启用表项。修改规则时，保存前展示 dry_run 差异。

### 5.5 去掉引擎之外的授权逻辑

- **权限指令**：删除“permit 不超过授予者自己的权限”“permit 不落在 deny 之内”、RevokePermission 处理器中的二次判定，以及防锁死检查。写表项、编辑方案只按指令的判定槽与方案的分类判定。
- **个人 AMA**：方案规则写明“个人 Lineage 在 R 树中挂在所有者的个人空间下，在 P 树中挂在所有者 User 下”，其中的 Resident 随 Lineage 放置。个人空间只有所有者本人的 admin 表项覆盖，管理员表项写在“共享”上，覆盖不到这里；AMA 中的 Resident 因此与所有者权限相同。删除权限端口中的 `acts_for` 拦截、`lineage_access_policy.py` 中的 AMA 判断和事实层对 AMA 的强制放置；搜索与解析中隐藏 AMA 的效果由可读过滤得到。开关关闭时没有方案可以表达这条规则，端口保留“个人 AMA 归所有者”（weihao 2026-10-18，第 3.1 节）。
- **Workspace**：按绝对路径访问时，先按已登记 Workspace 的路径做最长前缀匹配，得到所属的 Workspace 资源 ID，再由引擎按这个 Workspace 判定；不属于任何已登记 Workspace 的路径按 R 树默认节点判定。删除独立的路径授权。按绝对路径只做读取（链接文件预览）；写入只有 UpdateWorkspaceFile 一条路，它只接受 `<workspace_id>/file/<相对路径>`，预览结果里的 `file_address` 给出这个地址（weihao 2026-10-18）。
- **免检指令**：处理器中凡属“操作别人的对象”的检查改为指令声明的要求（第 4 节列出的几类）；属于领域角色的检查保留在领域代码中。

### 5.6 判定缓存与临时凭证（weihao 2026-10-18）

**判定缓存只用一个版本号。**

- 实例只有一个版本号，保存在 `permission.state`。表项增删改、停用启用、临时凭证签发或撤销、P 树中主体的位置变化、启用另一套方案，都在同一事务里让它加一。R 树的变化（新资源、资源移动）不加版本号，因为资源路径不进缓存，每次判定时现读。
- 每个进程按主体缓存“适用于这个主体的表项”，以及每个 permit 的洞，并记下缓存建立时的版本号。判定用一条 SQL 同时读当前版本号和所问资源的路径。版本号不同，就清空本进程的全部缓存再重算。
- 删除按 P 节点计数的 `revisions`、实例的模板标记，以及换模板时抛出的 `InstanceChanged` 重试机制。

**临时凭证就是表里一行带过期时间的 permit。**

- A 签发给 B 时，只检查一件事：A 此刻在该资源上持有所交出的 A 节点（第 4 节）。检查通过后，在实例表中写入一行 (B, A 节点, 资源, permit)，带上 `expires_at` 和签发者，立即生效，不需要重建。
- 不复制 A 的 deny，有效期也不受 A 自己那条表项的约束。A 之后失去权限不影响已签发的凭证。重新生成实例时临时凭证按第 3.3 节保留；规则合并后节点消失的处理见第 5.11 节。
- 判定时，过期的行视为不存在；日常维护删除这些行。只有签发者和接收者可以撤销；接收者做完事后可以主动交还。

**有效期以任务为主，时间兜底。**

| 方式 | 做法 | 结束条件 |
|---|---|---|
| 随任务 | A 在发给 B 的消息上附带凭证（DeliverMessage 的凭证参数，或签发时给出 `message_id`）；凭证行记录这条消息 | B 对这条消息的输入进入完成、失败或取消状态时，在同一事务中删除这一行（`inline.end_with_input`） |
| 按时长 | 按 `expires_in_seconds` 签发，默认一天 | 到期；到期前签发者可以用 RenewTemporaryCredential 续期 |

- 两种方式都有 7 天的兜底上限：随任务的凭证在消息长时间未处理完时，最迟 7 天后失效；续期后的总时长也不超过从签发起 7 天。
- 随任务的凭证可能在事情真正做完前失效，例如 B 把后续工作安排到以后的轮次。这时 B 被拒绝，再向 A 申请或请 A 按时长签发；提前失效不会多给出权限。

### 5.7 事实的归属与写入时机（weihao 2026-10-18）

方案规则生成表项时要读对象的事实，例如“Project 创建者对该 Project 有 admin”要知道创建者，“按 User 分类放置 User”要知道分类。

**事实属于对象自己，权限系统只读。**

- 创建者写在对象自己的记录里。Project 的登记记录增加创建者；删除权限 schema 中的 `creators` 表。开关关闭时创建的对象因此不会丢失创建者。
- User 分类是账号的属性，和用户名、状态保存在账号记录中；删除权限 schema 中的 `user_categories` 表。UpdateUser 的 `changes.category` 改写账号记录。
- steward、聊天室成员等关系同样从对象记录读取。权限系统中只保留方案、实例（树和表），以及补齐所需的对象快照（`scheme_objects`）。

**对象和它在树上的位置同一事务提交；没有可共享事务的对象写入后同步，并定时补齐（weihao 2026-10-18）。**

- 保存在 PostgreSQL 中的对象，创建、移动、删除都在改动对象的同一事务中同步实例：Lineage、Resident、Family、Project、聊天室、帖子、User 账号。这些对象不存在“已存在但不在树上”的窗口。
- 经文件或各自存储写入的资源（知识条目、论文、skill、repo、article、experiment、Workbench、device）没有可与实例共享的事务，写入成功后紧接着同步实例。同步可以重复执行，结果不变。
- 提交后的同步可能因为进程中断或出错而没有执行。补齐（`complete`）在服务启动时和定时运行，把缺位置、位置过时或已删除的对象按规则修正，修正不了的对象记入日志。这样一次同步失败，最迟在下一次补齐时修复，不会长期放错位置。
- 启用方案、开启开关和修改规则时一次性收集全部事实（约 4.5 秒）的做法不变，这些操作很少发生。

实现状态（2026-10-18）：Project 登记记录已有 `created_by`/`created_for`，`permission.creators` 中的 3 个 Project 创建者迁入登记记录后删除该表；Project、聊天室、帖子已在各自记录的事务中同步实例（第 3.3 节）。经文件或各自存储写入的资源（知识条目、论文、skill、repo、article、experiment、Workbench、device）没有可共享的事务，按 weihao 的决定保持写入后同步；提交后的同步是幂等的，重复执行不改变实例。ResourceService 启动后和每小时对启用方案补齐一次，保留全部表项（包括手工行），绝不重新生成；修正不了的情况（补齐出错，或将移出的对象超过 100 个）记入 ResourceService 日志，整次不写入。补齐先在锁外算出目标位置，只在写入时锁住实例表。User 分类迁入账号记录分两次发布：账号表的读写先改为按列名读取，所有进程换上新代码后再加列、迁移并删除 `permission.user_categories`，避免加列打断旧进程中 `SELECT *` 的预备语句。两次发布均已完成：分类现在是 `portal_identity.user_accounts.category`，改分类与 P 树中的移动、创建账号与放置、删除账号与移出都在账号表的同一事务中提交；`permission.user_categories` 已删除。

### 5.8 关系规则（weihao 2026-10-18）

表规则有两种：节点规则直接写一行 (P 节点, A 节点, R 节点, 效果)；**关系规则**写成 (关系, A 节点, R 上的对象类别, 效果)，对类别中的每个对象，按关系找到对应的主体，写一行 (主体, A 节点, 该对象, 效果)。例如 (创建者, admin, 每个 Project) 给每个 Project 的创建者写一行 admin；(所属 Project, admin, 每个 Project) 给每个 Project 中 Resident 所在的 P 节点写一行 admin。

- 原来的选择器（`select`）和 P、R 两侧成对的“每个”节点配对，统一改为关系规则，规则文档和编辑器中只有这一种“按关系展开”的写法。P 树中“每个 Project 的 Resident”这类模式节点仍用于放置主体。
- 关系的种类由代码固定，方案只能选用：本人（账号就是这个 User、Resident 就是它自己）、所有者（个人空间属于这个 User）、所属（Resident 属于这个 Project 或 Lineage）、创建者、steward、成员（聊天室成员）。每种关系由事实层从对象自己的属性读出（第 5.7 节）。新增关系需要改代码并更新本节；方案不能自定义关系。
- 关系变化（换 steward、成员增减）时，删掉旧关系生成的行，写入新关系的行，其他行不动。
- 规则只是生成器：判定只看实例中的表项，表项仍是节点三元组（thinking_3cbdd375ebf8 第 14 章 Gen(Π, F)）。
- steward 作为 Family 管家的职能（第 4 节）留在代码；“steward 对所在 Lineage 有 admin”是一条可以删改的关系规则，两者互不依赖。

实现状态（2026-10-18）：规则文档升为 `dirtybase.permission-scheme.v2`，关系规则写成 `{"relation", "a", "r"}`，关系取值为 `self`、`owner`、`belongs`、`creator`、`steward`、`members`（`scheme/vocabulary.py` 的 `RELATIONS`）。用新旧代码对线上全部事实各生成一次默认方案实例，节点与表项完全相同。

### 5.9 对象的放置与兜底节点（weihao 2026-10-18）

**每种对象的放置规则写成“放在它的某个关系对象之下；没有这个关系时，放到某个节点”，两部分都写在方案里。**

- 关系取自第 5.8 节的固定种类。例如知识条目放在所属 Project 之下，没有 Project 时放到“公共内容”；论文没有 Project 时放到“论文库”；个人 AMA 的 Lineage 放在所有者的个人空间之下（P 树中放在所有者 User 之下）。个人 AMA 因此是一条普通放置规则，事实层不再强制放置（第 5.5 节）。
- 每棵树有一个由方案设定的兜底节点：方案没有覆盖的对象类型，以及缺少关系又没写去处的对象，都放在兜底节点下。默认方案中 R 树的兜底节点是“共享”，A 树是“未归类”，两者都只有管理员能操作。
- 删除“沿模式链向上找最近分类节点”的隐式规则，以及 `unowned` 这一单独写法（并入放置规则的“没有关系时”）。
- 删除“所属对象尚未放上树时先临时放置它”的机制：对象与它的位置同一事务提交（第 5.7 节），所属对象总是先存在。
- 同一对象（Resident 等）在 P 树和 R 树中的位置由各自的放置规则决定，可以不同：P 树位置决定它能做什么，R 树位置决定别人能对它做什么。

实现状态（2026-10-18）：规则节点写 `under`（关系对象类型）和 `otherwise`（没有关系时的分类节点），`unowned`、`via`、模式链上溯和临时放置已删除，个人 AMA 由 `under: user`/`under: personal_space` 放置。线上全部对象的位置与改动前一致，没有对象移动。

### 5.10 预留：调用回放检查（weihao 2026-10-18，暂不实现）

将来可以在开启开关或修改方案之前，取最近一段时间实际执行过的指令调用（调用者、指令、参数，来自指令执行记录），不执行，逐条用启用方案的实例试判定，列出开启后会被拒绝的调用，按调用者和指令分组，供人判断是方案漏了表项还是本该拒绝。

现在只保留它需要的接口，不建回放、记录或汇报功能：

- CheckPermission 按 (主体, 指令, 参数) 试判定，返回结果、原因和命中的表项；开关关闭时也按启用方案的实例判定，不受关闭模式影响。
- 指令执行记录保留调用者、指令名和参数，回放可以直接读取。

实现状态（2026-10-18）：CheckPermission 一直按启用方案的实例判定，与开关无关，返回 `allowed`、每个请求的 `reason` 和命中的 `permit`/`deny` 表项；开关关闭时的这一行为已有测试固定。

### 5.11 内置方案规则只读、可直接启用（weihao 2026-10-18，2026-10-19 修订）

- 内置方案（默认方案）的规则只读，跟随代码发布；要改规则，先复制为自定义方案。方案本身可以直接使用（weihao 2026-10-19）：可以启用；它的实例和自定义方案的实例一样，可以增加、删除、停用表项，可以签发、续期、撤销临时凭证，谁能做这些都按指令分类判定。
- 发布改变内置规则时，内置方案的实例按新规则重新生成，同时写回实例中的编辑，和复制品的合并用同一套写回（见下文“实例”）：增加的行、停用的行带原 ID 写回，人删掉、新规则仍给出的行保持删除，临时凭证保留。写不回的表项和保持删除的行记为这套内置方案最近一次的写回记录，编辑器可以查看。为了知道哪些行是编辑，实例保存生成它的规则全文，作为下次发布的比较基准。其他方案的实例不受影响。
- 内置规则更新后，改动自动带进从它复制出的方案（weihao 2026-10-18）。发布后 ResourceService 启动时，对来源是内置方案、来源规则已经变化的每套复制品做三方合并：比较复制或上次合并时的来源规则、现在的来源规则和复制品的规则，按项（树节点、各树的 root 与 default_parent、表规则、A 元素的分类、说明）处理：
  - 复制品没有改过的项，换成来源的新版本，包括新增和删除；
  - 复制品自己改过、来源也改了的项，保留复制品的版本，记为冲突写进日志，并在编辑器中提示；
  - 合并后更新来源记录。
- 合并不丢人的编辑。只有指令分类或说明变化时，实例不变。树或表规则变化时按新规则重新生成实例，然后把实例中的编辑写回：增加和停用的行，P、A、R 节点在新实例中都还存在的照原样保留，找不到节点的不写回，逐条记进日志（thinking_3cbdd375ebf8 §14.8）。规则生成、但被人删掉的行，合并后新规则仍然给出时也保持删除（weihao 2026-10-19），这些行记进最近一次合并的记录，编辑器据此显示哪些行因为人删了才没加回。
- 合并就是合并，结果不因谁触发而不同（weihao 2026-10-19）：发布后的自动合并和编辑器里带 `source_digest` 保存的手动合并走同一套写回，手动合并的 dry_run 报告将写不回的表项和保持删除的行。人在编辑器里直接改规则保存（不带 `source_digest`）仍按第 5.3 节重新生成实例，此前的编辑不保留。
实现状态（2026-10-19，c6eb4deb）：ActivatePermissionScheme 可以启用内置方案；UpdatePermissionScheme 仍拒绝修改内置方案的规则（“内置方案不可修改；请复制为新方案”）；表项指令和临时凭证指令对内置方案与自定义方案相同。实例生成时把规则全文存进 `permission.scheme_instances.rules`（JSONB）；这一列上线前生成的内置方案实例，ResourceService 启动时在规则未变的前提下补存。启动时对规则有变化的内置方案调用 `PARPermissions.follow_release`：有基准时按基准规则补齐，再走 `Instance.regenerate_keeping_edits`，结果连同授权差记进 `permission.scheme_merges`（`source_scheme_id` 是方案自己，`merged_by` 为 `portal`），由 GetPermissionScheme 的顶层 `last_merge` 返回；没有基准时整套重新生成。发布只改了指令分类（树和表项不变，基准与当前规则仍有差别）时，实例不动，只把基准推进到当前规则；有元素换了分类节点时，同样记一条 `last_merge`（`merged_by` 为 `portal`，`conflicts`、`unrestored`、`deleted_again` 为空，`auth_diff` 是移动元素的授权差），只改说明的发布不记录（9ca737a3，ET 2026-10-19）。复制品合并这类发布时实例同样不动，`last_merge.auth_diff` 同样报告移动元素的得失。`last_merge.regenerated` 标明实例是否重新生成，实例不动的记录为 false，编辑器据此不再写“实例已按新规则重新生成”（ccce47f4，`permission.scheme_merges.regenerated`，此前的记录为 true）。启动跟随之前（ResourceService 预热中，或同库的另一个 ResourceService 刚启动）或跟随失败之后，启用内置方案或开启开关同样写回：`schemes.prepare` 在自己的事务中调用同一个 `schemes.follow_release`，dry_run 在回滚的事务里写回并报告 `restored_entries`、`unrestored`、`deleted_again`（b623122e，7a4a 审查 c6eb4deb 时复现了原先整套重新生成丢掉实例中编辑的问题）。prepare 读实例时还没拿跟随锁；如果拿到锁时另一个进程已经跟随完，实例已在新规则上，启用改为补齐，报告 `regenerate: false`（6479785c，7a4a 审查 b623122e 时复现了原先的 TypeError）；启动时每套内置方案各自跟随，一套失败只记日志，不影响其他。对象同步每次读取当前启用的方案，关闭时切换方案后其他进程不再写入旧方案的实例；停用的方案缺少的新对象，在启用（开关开启时）或开启开关时由补齐带上。复制品在 `permission.scheme_sources` 记录来源方案以及复制或上次合并时的来源规则全文和摘要，编辑器据此提示来源已更新并做三方对比；合并通过带 `source_digest` 的 UpdatePermissionScheme 保存（接口见 decision_22227d0ad075“来源记录”）。发布删除某条指令后，已保存的自定义方案仍可能给它的 A 元素分类：加载已保存的方案时跳过这类分类（没有人能调用已删除的指令，判定不变），保存规则时仍严格编译并报告它的路径，所以启用中的方案不会因发布整份失效（de8cd826）；复制品合并来源后这类分类随之清掉。

判定槽移进指令声明（2026-10-19，b3dd4c7f）：方案的 `instructions` 从“指令到判定槽数组”改为“A 元素到分类”。一次性迁移把已保存的自定义方案、复制品的来源规则（连同摘要）和实例基准（`scheme_instances.rules`）转为新格式；线上只有默认方案的基准，转换后与新的 default.json 相同，没有与声明不同的槽结构。迁移前后的编译器与请求适配层在每条指令的每种合法参数组合上逐项比较，请求相同；迁移脚本和比较测试在切换后删除（f6f4e0ad）。

线上切换（2026-10-19）：此前启用的是默认方案的复制品 `dirtybase`，规则与默认方案逐项相同。默认方案的实例补齐到与 `dirtybase` 相同的对象；`dirtybase` 实例中的编辑是增加的两行（AI员工 对 @operations 的 @admin、hk 对 AI员工 的 @contributor），没有停用的行、人删的行和临时凭证，两行带原 ID 搬入默认方案的实例。搬完后两份实例的节点、对象关系和表项完全相同，双向授权差都为中性；随后启用默认方案（启用时对现有对象的补齐为 0 变化），删除 `dirtybase`。

自动合并的实现（0afce3ff，2026-10-18）：

- **时机和顺序。** ResourceService 启动、索引预热后，先让规则有变化的内置方案的实例跟随发布（写回实例中的编辑），再合并复制品，然后才做第一次补齐。合并和每小时补齐在同一个线程中依次运行。合并一套复制品时锁住它的来源记录（`scheme_sources` 行），另一个进程已经合并过的就跳过；需要重新生成时锁住实例表。别的进程的补齐在锁外按读到的实例规则算好位置，拿到锁时如果实例已按别的规则生成，整次不写入（`InstanceReplaced`），下一次补齐再来。
- **合并单位。** 和编辑器的三方对比相同（`scheme/merge.py`）：每棵树的设置（root、default_parent 等）、每个树节点、每条表规则（按 id，没有 id 时按位置）、每个 A 元素的分类（`instruction|<A 元素>`），以及 description 等其他顶层字段；scheme_id 和 title 属于复制品，不合并。复制品的某项和复制时的来源相同，就换成来源的新版本，包括新增和删除；复制品也改了、而且改得和来源现在的不同，保留复制品的版本，记为冲突。合并后的规则严格编译，编译不过整套不写入、记进日志。规则有变化时以 `portal` 身份按版本保存，然后把来源记录推进到来源现在的规则。
- **实例。** 只有指令分类或说明变化时（合并后的 `rules_digest` 与实例的相同），实例不动，表项的 ID 和版本都不变。树或表规则变化时，在同一事务中：
  1. 先按旧规则补齐，使旧规则对现有对象给出的每一行都已知；
  2. 旧规则不给的行（人写的）和停用的行算作人的编辑；
  3. 按新规则重新生成实例；
  4. 人的编辑在 P、A、R 节点都还存在时带原 ID、版本和停用标记写回，同内容的规则行让位给它；
  5. 旧规则给出、但已被人删掉的行，新规则也给出时仍然删掉；
  6. 临时凭证节点还在的保留，不在的撤销。
  
  节点已不存在、写不回的编辑和撤销的临时凭证逐条进日志。合并前的补齐将移出超过 100 个对象时，整套不写入。
- **日志与编辑器。** ResourceService 的日志对每套复制品写一行合并结果，每个冲突一行（was、now、own），每条没写回的表项一行（带缺失的树）。最近一次合并（自动或手动）的结果保存在 `permission.scheme_merges`，由 GetPermissionScheme 的 `source.last_merge` 返回：`merged_by`（自动合并为 `portal`，手动合并为保存者）；`conflicts` 每项 key、was、now、own；`unrestored` 是没写回的表项，每项带 `missing`；`deleted_again` 是新规则仍给出、因为人删过而保持删除的行（effect、p、a、r）。来源记录推进后编辑器不再显示“来源已更新”，冲突要从 `last_merge` 读取。内置方案跟随发布的写回记录形状相同，由 GetPermissionScheme 的顶层 `last_merge` 返回（`source_scheme_id` 是方案自己，`conflicts` 为空）。
- **手动合并（69f53dba，2026-10-19）。** 带 `source_digest` 的 UpdatePermissionScheme 树或表规则变化时，在同一事务中走上面的同一套写回（`Instance.regenerate_keeping_edits`）。dry_run 在回滚的事务中实际跑一遍，报告 `unrestored`、`deleted_again`，`diff.lost_edits` 只列写不回的表项（`kind: unrestored`，带 `missing`）。保存后同样记为 `last_merge`，`conflicts` 为空（冲突由编辑器在保存前让人决定）。不带 `source_digest` 的规则修改仍整套重新生成。

### 5.12 授权差报告（weihao 2026-10-19）

动态性（thinking_3cbdd375ebf8 §14）的工程部分目前只做授权差报告：一次操作要重新生成或替换实例时，报告哪些主体在哪些资源上得到或失去了哪些动作，供人判断这次变化能否接受。报告不拦截任何操作。以下三项暂不做：分级审批（按变化类别要求不同的批准）、节点跨版本身份（新旧实例里同一个节点的对应关系），以及不生成实例、直接从规则推出授权差。意图变化（§14.5 第 2 层：一项授权从跟随具体元素变成跟随类别，或反过来）也不报告。

- **何时报告。** UpdatePermissionScheme 修改规则（包括带 `source_digest` 的手动合并）和 ActivatePermissionScheme 换方案，dry_run 结果和正式保存结果都带 `auth_diff`。启动时复制品的自动合并和内置方案跟随发布的写回，把同样的报告写进各自 `last_merge.auth_diff`。单条表项的增删改不报告，它的影响就是那一行本身。
- **比较什么。** 两个实例都在现有对象上比较：
  - 修改规则时，用方案现在保存的实例对比新实例：按新规则生成，合并时再写回人的编辑。
  - 启用方案时，用启用中方案现在的实例对比目标方案补齐后的实例。权限系统关闭时实际生效的是全部放行，报告仍比较两个实例，回答的是“打开开关后与旧方案相比会怎样”。
  
  只改标题或说明时报告为中性。指令元素是 A 树上的元素（thinking_3cbdd375ebf8 §14），改指令分类就是移动 A 元素，授权随之改变：实例不变时也读取现有实例比较移动的元素（见“报什么”）。要求改变了的指令另列在 `remapped`，每项的 before/after 列出各 A 元素（`element`）的判定槽与分类。临时凭证不参与比较。
- **报什么。** ΔAuth⁺（新得到的权限）和 ΔAuth⁻（失去的权限）按 §14.4 在节点层面汇总成盒子。组分两种：
  - **实例变化的组。** 每组是一个主体集合、一个资源集合和一次 A 层级变化。A 取分类节点（@viewer、@contributor、@admin、@message 等），以及被某条表项直接点名的 A 节点（例如一条只授予某条指令的表项，这条指令的 ID 会出现在得到或失去的节点里）。其余指令不逐条列出：没有被点名、也没有移动的指令，判定等于它路径上最近的被点名节点，所以不会漏掉变化。每组给出变化前后允许的最高分类节点（例如 `@admin` → `@contributor`）、得到和失去的分类节点、主体数、资源数和变化类别。
  - **移动元素的组。** 一个 A 元素换了分类节点，或固定槽换了 R 节点，就按元素单独比较：旧侧取它在旧实例中原来的位置，新侧取它在新实例中的新位置，对同样的主体类和资源类各判定一次，得出（主体节点，该元素，资源节点）的得失盒子。参数槽只计声明类型的对象（例如 DeleteLineage 只计 Lineage）；place 槽判定新对象将落下的位置，只计它的所属对象类型（RegisterResource 为 Project）和没有所属对象时落下的分类节点；via 槽判定记录字段指向的对象，字段声明了类型时只计这些类型（回执的 lineage 只计 Lineage），没有声明的（表项的 r）计全部节点（cee9336b，7a4a 审查 4bea1966 时指出）；固定槽的资源是它要求的 R 节点，组里资源数记 1，不计入整份报告的资源数。移动的元素不再参与实例变化的组。每组另带 `element`、`instruction` 和分类的变化 `a: {before, after}`，固定槽还有 `resource: {before, after}`；`before`/`after`/`gained`/`lost` 中出现的是这个元素本身。例如把 DeleteLineage 从 @admin 改到 @viewer，能查看某个 Lineage 但不能管理它的主体在该 Lineage 上得到 DeleteLineage，报告放宽。
  
  主体集合和资源集合用新树里容纳它们的最低节点描述。需要时再注明只计哪些对象类型（kinds），以及除去哪些节点（except）。两种组按涉及的三元组数一起排序，一起受 `diff_limit` 和时间上限限制，一起计入顶层计数和类别。整份报告的类别：两个集合都为空是中性，只失去是收紧，只得到是放宽，两者都有是混合。
- **例子。** 9d7dd4c1 把属于 Project 的 Workbench 从“公共内容”移到所属 Project 下。在线上实例上，这次变化的报告是收紧，共四组：
  - hk（连同其个人 AMA 中的 Resident）在 AI员工 的 5 个 Workbench 上从 @admin 降为 @contributor，因为 hk 有一条对 AI员工 的 @contributor 表项，是实例中增加的行。
  - 在 AI员工、OSForAgent 和元胞自动机研究这三个 Project 中，各自 Workbench 上不属于该 Project 的主体都从 @admin 降为 @viewer 与 @message，分别涉及 509、937、1055 个主体。Portal 管理员和一般用户不受影响。
- **怎样算。** 一个主体的判定只取决于它路径上哪些节点被某条表项点名。前后两侧点名节点都相同的主体归为一类，资源也这样分类。每对类在每一侧只判定一次，得到 A 分类节点上的位集合。两侧表项相同的主体类，只需检查路径变了的资源类。前后结果不同的类对，按相同的变化合并成组。
- **边界。** 列出的组数受 `diff_limit` 限制（默认 50，最多 500），`groups.total` 是总组数。计算超过 2 秒即停止，报告 `complete: false`，这时各计数是下限。线上实例约有 1 万个节点、109 条表项，读取节点、算出新实例的位置和比较两个实例合计约 0.13 秒，UpdatePermissionScheme 的 dry_run 本身约 2 秒。

实现状态（2026-10-19，8cd80811；移动元素的组 4bea1966）：实现在 `permission_system/authdiff.py`，结果字段见 decision_22227d0ad075“授权差”。实例不变的修改和合并经 `authdiff.kept` 读取现有实例比较；没有移动元素时直接报中性。线上实例上同时移动 4 个元素的比较约 0.2 秒。内置方案跟随只改指令分类的发布时，实例不写回，`last_merge.auth_diff` 只有移动元素的组（9ca737a3，见 5.11）。`permission.scheme_merges` 新增 `auth_diff` 列（JSONB，可空）；报告上线前的合并，该列为 null。

权限存储启动时只创建缺失的表，不修改已有的表（避免在线上表上加 DDL 锁），所以后加的列只写在建表语句里：`scheme_instances.rules`（c6eb4deb）、`scheme_merges.auth_diff`（8cd80811），以及更早的 `scheme_merges.deleted_again`、`merged_by`。新建的库直接带这些列；线上库已手动补列（2026-10-19）。其他已有的库需要先补列，否则启动跟随和合并会报 `UndefinedColumn`。已安装站点的升级步骤随开源部署一起设计。

## 资源发现与直接读取

资源列表、搜索联想和项目导航按各对象的读取权限过滤；项目拒绝覆盖其资源、Lineage 与 Resident，直接读取遵循同一判定。
