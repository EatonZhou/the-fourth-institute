---
kb_id: decision_600ae27b0538
type: decision
project: AI员工
created_at: 2026-10-16
version:
  number: 44
  created_at: "2026-10-16T16:03:37Z"
  created_by:
    user: "weihao"
    agent: "resident_9c94c8b365fe"
  updated_at: "2026-10-21T18:07:47Z"
  updated_by:
    user: ""
    agent: "resident_977d1a613d44"
importance: 8
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/permission_system/scheme/builtin/default.json
  - ${code_root}/management_portal/permission_system/scheme/vocabulary.py
  - ${code_root}/management_portal/permission_system/facts.py
  related:
  - decision_b820faf42218
  - decision_22227d0ad075
  - thinking_3cbdd375ebf8
  - decision_bd5217d08c09
  - decision_44cf587c1721
  supersedes: []
  derived_from:
  - thinking_3cbdd375ebf8
---

# Portal 权限默认方案：P、A、R 三棵树与表规则

默认方案是 Portal 权限系统（decision_b820faf42218）的内置方案，它的规则规定 Portal 的主体、动作和资源怎样放进三棵权限树，以及实例的权限表由哪些表规则生成。它的出发点是：树按 Portal 对象的自然归属分层，权限尽量写在类别节点上，少量例外用 deny 表项表达。规则文件是 `permission_system/scheme/builtin/default.json`。

## 1. A 树：免检与嵌套的分类节点

```text
A 根：全部动作（@actions）
├── 免检（@free）
└── 管理（@admin，A 树默认节点）
    └── 协作（@contributor）
        ├── 查看（@viewer）
        └── 给 Resident 发消息（@message）
```

这些节点只是默认方案的数据。Portal 代码不认识它们的名字，只认识指令：指令声明的每个判定槽是一个 A 元素，方案的指令分类把每个 A 元素挂到其中一个节点下，业务代码再问“这条指令在这里能不能通过”（decision_b820faf42218 §3.1、§3.2）。

- 每条 ISA 指令在声明中写判定槽：按哪个参数指向的对象判定，或用固定槽（decision_898b8911a2ff“声明格式”）。默认方案的 `instructions` 只写每个 A 元素挂在哪个节点下，固定槽另写对哪个 R 分类节点判定：`{"<A 元素>": {"a": "@contributor"}}`、`{"<A 元素>": {"a": "@admin", "r": "@shared"}}`。例如 ApplyLineageChanges 声明按 `lineage_id` 判定，默认方案把它放在 `@contributor` 下；AbsorbContext 声明源 Resident 与目标 Resident 两个槽，默认方案把源放在 `@viewer` 下、目标放在 `@contributor` 下。一个判定槽对应一个 (执行者, A 元素, 资源) 请求。下文说“对某参数要求某节点”，指这个参数上的判定槽被分类到该节点下。
- 节点按包含关系嵌套：“协作”包含挂在它下面的全部要求，所以对资源 r 持有 `@contributor` 的表项，同时能执行挂在 `@viewer` 下的动作。
- 指令分类沿用 TFI Portal 分级规则（原“模板 B”），另加权限指令自身的要求，给声明中的每个 A 元素分类（测试逐条核对）。所有人都能调用的指令显式放在“免检”下：写成 `{"a": "@free", "r": "@portal"}`，由“免检放行”表项覆盖。默认方案不设“未归类”节点；所有已安装指令的每个判定槽必须显式归入免检、查看、协作、管理或消息分类。A 树的结构默认节点为 `@admin`，但内置模板加载时必须核对正式 ISA 声明：缺少分类即报告模板错误及具体元素路径，不能以默认节点代替显式分类，也不能等到业务执行才报权限不足。新增指令或判定槽必须同时补齐模板并通过完整性检查（weihao 2026-10-20）。Project 内的主体继续通过 Project 的 `@admin` 表项覆盖同 Project 中的管理、协作、查看及消息动作；对其他 Project、共享运维或个人空间的访问仍按 R 树资源范围判定。自定义方案保留其自行指定的默认节点策略；GetPermissionScheme 和创建、修改、启用方案的 `dry_run` 仍用 `unmapped` 报告自定义方案遗漏的 A 元素，它不是默认模板的权限节点。GetProject 对 `project_id` 要求 `@viewer`（Workbench 目录按它过滤可读 Project）；ListPermissionEntries 对可选的 `r` 要求 `@viewer`，CreatePermissionEntry 对 `r` 要求 `@admin`，UpdatePermissionEntry 对表项已保存的 `r` 和可选的新 `r` 要求 `@admin`，DeletePermissionEntry 对每条表项已保存的 `r` 要求 `@admin`（`via` 槽位，见下表）。
- “给 Resident 发消息”是“协作”下的一个分类节点，收着 DeliverMessage、ForwardMessages 和 AnswerResidentRequest：这几条指令的 Resident 参数要求这个节点，而不是整个“协作”；DeliverMessage 发给 User 时对 `user_id` 同样要求这个节点，ForwardMessages 转发到聊天室时对 `target_chatroom_id` 要求 `@contributor`（聊天室成员满足）。同一指令有多个要求时，每个要求是一个 A 树元素，名为“指令:参数”，例如 `DeliverMessage:user_id`。持有 `@contributor` 的主体照样能发消息；“全员可给 Resident 发消息”规则只打开这一组指令，不同时打开修改 Resident 设置、队列等协作动作。
- 账号管理、Telepus 联系人绑定、权限方案的创建修改删除与启用、执行设置的读写（Get/UpdateExecutionSettings）等指令要求对“共享”节点通过 `@admin`；打开 Terminal（OpenTerminal）也是如此。修改 User 分类（UpdateUser 的 `changes.category`）在方案里没有单独的要求，和修改其他资料一样只要求对目标账号有 `@admin`；每个 User 对自己的账号持有 `@admin`，所以本人能在一般用户与实习用户之间改自己的分类。管理员名单不由方案决定（weihao 2026-10-19，decision_b820faf42218 §3.1“Portal 管理员保底”）：设为或取消 Portal 管理员、停用或删除管理员账号只有 Portal 管理员能做，不能对自己做；Portal 管理员永远可以开关权限系统和启用方案，即使方案把管理员挡在外面。Portal 管理员节点上的规则覆盖“共享”上的全部动作，所以 Portal 管理员在默认方案中也能执行它们。
- A 树不再按读、写、删、管理细分。“持有 `@contributor` 但不能删除”这类需求用 deny 表项表达。

2026-10-17 原先未映射、按免检放行的 110 条指令逐条归类如下：

| 要求 | 指令 |
| --- | --- |
| 免检：调用者本人的数据 | Mailbox 条目（MarkMailboxItemRead、PinMailboxItem）、个人待办（Create/Update/Move/DeleteMailboxTodo）、撤销自己的访问凭据（RevokeUserAccessToken）、个人动态（PublishPulseItem，公开与撤回）、上传（CreateUpload、DeleteUpload）、作者对自己消息与回复的修改（UpdateChatroomMessage、RetractChatroomMessage、DeleteReply）、退出聊天室（LeaveChatroom） |
| 免检：处理器按固定角色核对调用者 | Family 简报与摘要材料（CheckResidentWorkSummary、GetFamilySummaryInputs、PublishFamilyProgress，只允许本 Family 门面或 Steward）、工作摘要（UpdateResidentWorkSummary，只允许本人或 Steward）、独立摘要队列（ClaimResidentSummaryJobs 只允许 resident-summary 职责承担者，SubmitResidentSummary、ReleaseResidentSummaryJob 只允许认领者）、临时凭证（IssueTemporaryCredential 由引擎核对签发者此刻持有，RenewTemporaryCredential 只允许签发者，RevokeTemporaryCredential 只允许签发者或接收者） |
| 对参数所指记录保存的对象有 `@admin` / `@viewer`，在入口判定（`via` 槽位，decision_22227d0ad075；828f133f 起处理代码不再另问引擎） | UpdatePermissionEntry（对 `entry_id` 所指表项已保存的 `r` 要求 `@admin`，改 `r` 时对新 `r` 也要求 `@admin`）、DeletePermissionEntry（对 `entry_id` 或 `entry_ids` 每条表项已保存的 `r` 要求 `@admin`）/ GetInstructionExecution（对 `instruction_id` 所指回执记录的 Lineage 要求 `@viewer`，回执没有 Lineage 时不产生请求；另有免检槽位，只看调用者自己回执的情况由处理代码按调用者过滤） |
| 免检：目录与创建 | GetInstructionSchema、ListInstructions、GetPermissionSystem、ListPermissionSchemes、GetPermissionScheme、GetPermissionNode、SearchPermissionNodes；CreateChatroom、CreatePost、CreateProject（创建后按创建者规则管理）；CompleteChat（使用已登记的模型接口，与运行 Resident 同级，不返回凭据） |
| 对“共享”有 `@viewer`（全员） | 运行状态与用量（GetPortalStatus、GetDeviceStatus、GetRuntimeCapacity、GetUsage、GetProviderUsageEvents、ListProviderAccounts、ListModelApis）、目录与列表（GetKnowledgeTags、GetResidentSummaryQueue、GetResourceType、ListComponentTypes、GetTemplate、ListTemplates、ListPosts、ListUsers、SearchResidents、SearchResources）、只给出对象 ID 的读取（GetMoveOperation、GetWorkspaceHandoff，处理器再核对所属 Lineage） |
| 对参数所指对象有 `@viewer` | 聊天室读取与已读（GetChatroom、ListChatroomMessages、MarkChatroomRead，以及给出 `chatroom_id` 的 SearchMessages、ListInputHistory）、帖子读取、已读与回复（GetPost、MarkPostRead、CreateReply）、Resident 读取（GetResident、ListResidentStates）、Function Block 读取（GetLineage 结果中的 `function_blocks`）、资源读取（ReadResource、ResolveResource）、GetUser（对 `user_id`，必填；读取本人由“账号本人”规则满足） |
| 对参数所指对象有 `@contributor` | UpdateChatroomReaction、CreateLineage（对必填的 `project_id`）、UpdateWorkspaceFile（对必填的 `address` 所在的 Workspace；只接受 Workspace 文件地址，不接受绝对路径，weihao 2026-10-18）、CheckFamilyProgress 与 CheckFamilyRecovery（对 `lineage_id`；本 Family 的门面与 Steward 作为 Lineage 中的 Resident 满足，2026-10-18 起不再免检） |
| 对参数所指主体有 `@admin` | GetEffectivePermissions、CheckPermission、ListTemporaryCredentials（对必填的 `principal_id`，查看自己也写明；它当作 R 树上的这个 User 或 Resident；账号本人、Project 中的 Resident 和个人 AMA 中的 Resident 都对自己满足）、ListPermissionEntries 的 `p`（`r` 要求 `@viewer`，两者至少给一个） |
| 对参数所指 User 有 `@admin`（对必填的 `user_id`；账号本人满足，处理自己的数据也写明自己的用户名） | Mailbox（GetMailbox、ClearMailbox 给出 `user_id` 时，WatchMailbox）、ListMailboxTodos、个人访问凭据（CreateUserAccessToken、ListUserAccessTokens）、收藏（GetUserNavigation、UpdateUserNavigation）、GetPulse、ListChatrooms、UpdateUser |
| 对“公共内容”有 `@contributor` / `@admin` | Lineage 模板的 CreateTemplate、UpdateTemplate、RestoreTemplate / DeleteTemplate：Lineage 模板与 skill 同属共享定义；CreateTemplate 从选区生成时还要求对来源 Lineage 有 `@viewer` |
| 对新资源将要落下的 R 树父节点有 `@contributor`（`place` 槽位，见第 2 节） | RegisterResource（device 落在“共享”，不带 Project 的资源落在公共内容，带 Project 的落在该 Project） |
| 对参数所指 Lineage 有 `@admin` | DeleteFamily（删除整个 Family 及其成员，与 DeleteLineage 同级）、RunScript（对运行所属的 Lineage；以服务所属操作系统账号在主机上运行，不受沙箱限制；weihao 2026-10-19 起从“Portal 运维”改为按 Lineage 判定） |
| 对“Portal 运维”有 `@admin` | PublishRelease、RestartPortal、CreateModelApi、UpdateModelApi、DeleteModelApi |
| 对“共享”有“给 Resident 发消息” | CreateNotification（向人类参与者发通知，与发消息同级） |

Lineage Effect 的执行身份、事件归属和自动运行边界以 decision_ae422b92c6f3 为唯一权威；本方案按已认证的调用者进行对应指令判定，不在此另设一套 Effect 身份或免检规则。开关开启后，开发团队的 Resident 要发布或重启 Portal，需要一条“Portal 运维”上的表项，例如 (开发团队所在 Project, `@admin`, 运维)。RunScript 按运行所属的 Lineage 要求 `@admin`（Effect 内为激活的 Lineage，Resident 省略时为其所在 Lineage，decision_8b510cbd9c58）：Lineage 挂在所属 Project 下，Project 的 admin（包括该 Project 的 Resident）能在本 Project 的 Lineage 中运行脚本，其他 Project 的主体不能。weihao 2026-10-19 接受项目 admin 因此得到无沙箱的主机脚本能力。

## 2. R 树：按对象归属分层

```text
R 根（Portal）
├── 共享
│   ├── 每个 User 账号
│   ├── 每个聊天室
│   ├── 每个帖子
│   ├── 不属于 Project 的非个人 Lineage（及其 Resident、Workspace、Family）、
│   │   通过 Binding 加入的 Workspace、dataset、model、document、search、test、device 等
│   ├── Portal 运维：发布、重启、模型接口登记（只有管理员）
│   ├── 公共内容：所有主体都有 admin
│   │   ├── 不属于任何 Project 的知识条目、skill、todo、repo、review
│   │   ├── 既不属于 Project 也不属于 Lineage 的 Workbench
│   │   └── 论文库：不属于任何 Project 的论文
│   └── 全部 Project
│       └── 每个 Project
│           ├── Project 内的论文
│           ├── Project 内的知识条目、article、skill、todo、repo
│           ├── Project 内的 Workbench
│           └── 每个 Lineage
│               ├── 每个 Resident
│               │   └── Resident 自有的 Workspace
│               ├── 不属于 Project 的 Workbench
│               └── 每个 Family
└── 个人
    └── 每个 User 的个人空间
        └── 该 User 的个人 AMA Lineage（及其 Resident、Workspace、Family）
```

- R 根下先分成“共享”和“个人”两部分。面向全员和管理员的授权写在“共享”节点上，个人空间因此天然不被覆盖，不需要 deny 表项。
- 所有 Project 都在“全部 Project”节点下。授权写在这个节点上，就覆盖现有的和以后新建的每个 Project 及其中的全部资源。
- 每个资源只有一个父节点。所属对象不止一个时（例如一篇论文关联多个 Project）取记录中的第一个；缺少所属对象时不在“全部 Project”下。每个规则节点写一条放置规则（decision_b820faf42218 §5.9）：`under` 写放在哪类关系对象之下，`otherwise` 写没有这个关系时放到本树哪个分类节点。“Project 内的知识条目”、“Project 内的 Workbench”和“每个 Workbench”写 `otherwise: @public`（公共内容），“Project 内的论文”写 `otherwise: @papers`（公共内容下的论文库），“每个 Lineage”写 `otherwise: @shared`；挂在 Lineage 下的 Resident、Family 和挂在 Resident 下的 Workspace 不写 `otherwise`，没有关系时放到树的兜底节点“共享”。不为尚未登记的所属对象预留位置；关系对象未在实例中时按引擎的缺省放置处理，后续同步或补齐恢复自然归属。具体事务与补齐边界见 decision_b820faf42218 §3.3、§5.7，文件型资源不承诺与权限实例共享事务。
- 登记新资源时按它将要落下的位置判定（weihao 2026-10-19，ee804aeb）：调用者要在新资源的 R 树父节点上有 `@contributor`。父节点和登记后入树时的计算相同：按资源类型找放置规则，调用给出了所属对象时是那个对象（`fields.project_id`，没有时取 `fields.project_ids` 的第一项），没有时是规则的 `otherwise` 节点，类型不在任何放置规则里时是“共享”。`@public` 这类分类节点不是对象，按没有所属对象处理。所以登记 device 落在“共享”上，只有 Portal 管理员能登记；不带 Project 的知识条目、repo 落在公共内容上，所有人都能登记；带 Project 的落在那个 Project 上，Workbench 也是这样（例如带 `project_ids: [P1, P2]` 的 Workbench 落在 P1 上，要求 P1 的 `@contributor`）；不带 Project 的 Workbench 落在公共内容上。RegisterResource 声明按落点判定的槽：`On("resource_type", place={"project": ("fields.project_id", "fields.project_ids")})`，默认方案把它放在 `@contributor` 下；`place` 的键必须是放置规则中出现的关系对象类型（词汇 `place_related`），不与 `via`、类型同写。
- 公共内容是不归属任何 Project 的共享内容，所有 User 和 Resident 都能修改、删除（weihao 2026-10-17）。账号、聊天室、帖子、全部 Project 不在其中；个人 AMA 在所有者的个人空间下，也不在其中；方案规则中没有出现的对象类型落在“共享”下，只有全员可读。
- Resident 挂在它当前所在的 Lineage 下；移动到其他 Lineage 时随之移动，它的子树一起移动。
- Workbench 挂在它记录的第一个 Project 下（weihao 2026-10-19，9d7dd4c1）。Workbench 登记时记下所绑定 Lineage 所属的 Project（`project_ids`），所以它通常和这些 Lineage 在同一个 Project 中。方案里“Project 内的 Workbench”排在“每个 Workbench”之前，同时有 Project 和 Lineage 时按 Project 放置；没有 Project 时挂在它登记的主 Lineage 下，两者都没有时在公共内容下。被其他 Lineage 绑定只是使用关系，不改变它在树上的位置。
- Resident 自有的 Workspace 跟随该 Resident；通过 Binding 加入的其他 Workspace 不改变位置，留在“共享”下。
- User 账号是共享资源，管理员可以管理它，本人也可以修改它。每个 User 另有一个个人空间节点（`personal_space:<用户名>`），在“个人”下，用于承载属于个人的资源。device 是机器与 GPU，不是个人资源，留在“共享”下。
- 个人 AMA 属于它的所有者，AMA Lineage 下的全部 Resident 与所有者权限相同（weihao 2026-10-18）。R 树中，个人 AMA 的 Lineage（登记为 `lineage_membership: personal`，所有者是它的创建者）放在所有者的个人空间下，Resident、Workspace、Family 随 Lineage；“个人空间”规则因此让所有者管理整个 AMA。放置是方案中的一个普通规则节点：“个人空间”下的“个人 AMA”（`each: lineage`）。个人 Lineage 的事实以个人空间和所有者 User 为所属对象、不属于任何 Project，所以落在这里而不是“每个 Project”下；方案删掉这个节点时，个人 Lineage 按没有 Project 的 Lineage 放置。管理员表项写在“共享”上，覆盖不到个人空间，管理员因此也不能访问。开关关闭时没有方案，权限端口保留“个人 AMA 归所有者”这一条（第 6 节）。

## 3. P 树：User 按分类，Resident 按归属分层

```text
P 根
├── User
│   ├── Portal 管理员
│   ├── 一般用户
│   └── 实习用户
│       （每个分类节点下是该分类的 User；User 之下是它的个人 AMA Lineage，再下是其中的 Resident）
└── Resident
    └── 每个 Project
        └── 每个 Lineage
            └── 每个 Resident
```

三个规则节点的元素分别是 Project、Lineage 和 Resident 本身：每个 Project 是“Resident”下的一个元素，每个 Lineage 挂在所属 Project 的元素下，每个 Resident 挂在所属 Lineage 的元素下。主体树里授权给一个元素，就授权给它下面的全部 Resident，所以授权给某个 Project 的元素，就是授权给这个 Project 的全部 Resident。

Resident 在 P 树中的层级与它在 R 树中的位置一致，这样“同一 Lineage 的成员”“同一 Project 的成员”都是 P 树上的现成节点，权限可以直接写在这些节点上。所在 Lineage 不属于任何 Project 时，Lineage 节点直接挂在“Resident”下。个人 AMA 的 Lineage 由“User”下的规则节点“个人 AMA（在所有者名下）”放置（`each: lineage`、`under: user`，按所有者关系放在所有者 User 之下）；R 树中“个人 AMA”写 `under: personal_space`、`otherwise: @personal`。同一类对象可以有多个规则节点，每个按一种关系放置，对象按自己有的关系选用第一个匹配的节点（decision_b820faf42218 §3.5）。个人 Lineage 以所有者 User 为所属对象，所以挂在所有者 User 之下，其中的 Resident 继承所有者 P 树路径上的全部表项（所有者分类节点上的表项和所有者名下的表项），权限与所有者相同；所有者改分类时 AMA 子树随之移动。没有放进 P 树的主体（例如描述尚未同步的 Resident）落在 P 树默认节点“User”下，不属于任何分类，只有对 P 根的表项覆盖它。

每个 User 属于一个分类：Portal 管理员、一般用户或实习用户（weihao 2026-10-17）。分类是账号的事实，权限系统关闭时同样保留和生效；没有记录的 User 是一般用户。分类保存在账号记录的 `category` 字段中，改分类与 P 树中的移动在同一事务提交（decision_b820faf42218 §5.7）。方案规则用 P 树分类节点上的 `user_category` 字段声明哪个节点代表哪个分类，每个分类最多一个节点；User 放在自己分类的节点下，改分类（UpdateUser 的 `changes.category`）就是在 P 树中移动。写在分类节点上的表项对该分类的全部 User 生效，例如 Portal 管理员的权限就是 Portal 管理员节点上的一条固定表项。分类只属于 User，Resident 没有分类；Resident 需要 Project 之外的权限时，由 Portal 管理员写表项或由持有者签发临时凭证。

一般用户管理全部 Project：一般用户节点上有一条固定表项 (一般用户, @admin, 全部 Project)（weihao 2026-10-18）。实习用户没有这条表项，对 Project 只有全员可读；需要参与某个 Project 时，由管理员写表项，例如 (实习用户, @contributor, 该 Project)。

## 4. 权限表规则

| 规则 | 表项 |
|---|---|
| 免检放行 | (P 根, 免检, R 根) permit |
| 全员可读 | (P 根, @viewer, 共享) permit |
| 全员可给 Resident 发消息 | (P 根, @message, 共享) permit |
| 全员可管理公共内容 | (P 根, @admin, 公共内容) permit |
| Portal 管理员 | (Portal 管理员节点, A 根, 共享) permit |
| 一般用户管理全部 Project | (一般用户节点, @admin, 全部 Project) permit |
| Project 创建人 | 每个 Project X 的每个创建人：(创建人, @admin, X) permit |
| 聊天室创建者 | 每个聊天室 c 的创建者：(创建者, @admin, c) permit |
| 帖子作者 | 每个帖子 n 的作者：(作者, @admin, n) permit |
| 账号本人 | 每个 User u：(u, @admin, u 的账号) permit |
| 个人空间 | 每个 User u：(u, @admin, u 的个人空间) permit |
| Project 内管理 | 每个 Project X：(X 的 Resident 节点, @admin, X) permit |
| Family steward | 每个 Lineage L 中每个 Family 的 steward s：(s, @admin, L) permit |
| 聊天室成员 | 每个聊天室 c 的每位成员 m：(m, @contributor, c) permit |

默认方案没有 deny 表项。deny 留给运行中出现的例外，例如在某个 Project 内挖掉一个敏感资源，或临时冻结某个主体。

前六条是节点规则，在规则文件中写成 `{"p", "a", "r"}`，三者都是分类节点，各生成一行。后八条是关系规则（decision_b820faf42218 §5.8），写成 `{"relation", "a", "r"}`，`r` 是 R 树的规则节点，对其中每个对象按关系找到主体各写一行：Project 创建人、聊天室创建者、帖子作者用 `creator`（创建者），账号本人用 `self`（本人），个人空间用 `owner`（所有者），Project 内管理用 `belongs`（所属：主体是该 Project 在 P 树中的节点，即其中全部 Resident），Family steward 用 `steward`，聊天室成员用 `members`（成员）。

- 资源按它在树上的位置管理，创建者对自己创建的知识条目、论文、Lineage、Resident 等没有额外权限（weihao 2026-10-17）：Project 内的资源由该 Project 的 Resident 和持有该 Project 权限的 User 管理，公共内容由所有人管理。只保留三个例外：实习用户创建 Project 后要能管理它（管理员和一般用户本来就管理全部 Project，他们的创建人表项不改变判定），聊天室只有创建者能删除，帖子只有作者能修改和删除。“Project 创建人”对 Resident 创建的 Project 同样生效。
- 创建者在资源创建时记录，此后不随修改改变；Project、聊天室、帖子的创建者是创建它的认证调用者。Resident 处理某位 User 的输入时创建的对象，这位 User 也是创建者（weihao 2026-10-18）：User 本可以自己直接创建并成为创建者，交给 Resident 代办不应因此失去这份权限。发起的 User 取自这次调用所属、仍在运行的执行的输入消息来源（经过认证，由投递入口写入）；只认直接输入，输入来自另一个 Resident 时不沿转交链追溯，取不到就只记 Resident。权限端口的 `creation_fields` 计算这两项，创建对象的存储把它们写进对象记录的 `created_by`、`created_for`；创建者有什么权限仍由方案的创建者规则决定。目前写入 `created_for` 的只有 Project 登记记录；帖子表没有这一列，只记作者；聊天室只由 User 创建。已删除的 Resident 不再是主体，不因创建者规则得到表项。三条例外规则以及 steward、聊天室成员规则合称关系规则，只有现存的 User 或 Resident 因它们得到表项；导入脚本这类不是主体的创建者、已删除的 Resident 不生成表项。
- 关系规则对每个对象写一行，不看其他表项是否已经覆盖（decision_b820faf42218 §5.2）。规则只在对象第一次出现在实例中时应用到这个对象：之后对这行表项的修改、停用或删除保持原样，对象移动也不改动它。关系本身变化时（Family 换 steward、聊天室成员变化），旧关系的行按内容删除，写入新关系的行；对象删除时，指名它的行一并删除。
- 要收回创建人对某个 Project 的管理权，删除或停用实例中的这行表项；修改方案规则会重新生成实例，这类修改随之丢失。
- 创建者写在对象自己的记录里，权限系统只读（decision_b820faf42218 §5.7）：Project 登记记录的 `created_by`/`created_for`、聊天室记录的 `created_by`、帖子的作者；steward 每次从 Family 记录读取。开关关闭时创建的对象因此不丢创建者，开启补齐或生成实例时“Project 创建人”从登记记录展开。原 `permission.creators` 表中 3 个 Project 的创建者（MoEChiplet、AgentHarnessOverhead、元胞自动机研究）在 2026-10-18 迁入登记记录后删除这张表；其余 Project 早于审计记录，查不到创建者，没有“Project 创建人”表项，由一般用户规则与表项管理。
- Portal 管理员分类由 Portal 管理员用 UpdateUser 的 `changes.category` 修改（见第 1 节）；新部署时第一个创建的账号自动成为 Portal 管理员。管理员不能取消自己，Portal 因此始终至少有一位启用中的 Portal 管理员。Resident 通过“Project 内管理”管理所在 Project；需要 Project 之外的权限时，由 Portal 管理员写表项或由持有者签发临时凭证。
- steward 来自 Family 的登记记录：steward 换人时旧 steward 的行换成新 steward 的行，Family 解散时这行删除。steward 与 Family 在同一 Project 中、或个人 AMA 中的 steward 已被“Project 内管理”或“个人空间”覆盖，同样有自己的一行，判定结果不变。
- 聊天室成员规则让非创建者成员也能在聊天室发言（CreateChatroomMessage 的要求挂在 `@contributor` 下）。
- “Project 内管理”让 Project 中任一 Lineage 的 Resident 对这个 Project 下的全部资源持有 `@admin`：登记、修改和删除本 Project 的知识条目、论文、skill、todo、repo，管理同一 Project 中的 Lineage 和 Resident，也包括它自己（例如清空自己的上下文、读取自己发出的 prompt）。原先的“Lineage 内协作”（Lineage 的 Resident 对本 Lineage 有 `@contributor`）已删除：Project 内的 Lineage 已被“Project 内管理”覆盖，不属于 Project 的 Lineage 只有个人 AMA，由 AMA 私有规则保护。
- 删除 User 时，它的个人 AMA Lineage 及其 Resident 随账号删除；其他 Lineage、Project 和对话历史保留。
- User 不属于 Project。一般用户由“一般用户管理全部 Project”覆盖现有和以后新建的每个 Project，不另写表项；实习用户参与某个 Project 时写 (User, @contributor 或 @admin, 该 Project)。2026-10-18 起 Lyra、huanyu、lukai、songchen 是一般用户，原先各自的“全部 Project”表项已删除；hk 是实习用户，有一条 (hk, @contributor, AI员工) 表项；weihao、ET 是管理员，管理员规则已覆盖全部共享内容，不另写表项。默认方案的实例另有一条 (AI员工, @admin, Portal 运维)，让开发团队发布 Portal。
- “全员可给 Resident 发消息”让 User、Telepus 用户、个人助手和其他 Lineage 的 Resident 都能给共享区的 Resident 投递、转发消息和回答它的提问。要让某个主体不能给某个 Resident 发消息，写一条 (主体, 给 Resident 发消息, 该 Resident) deny。

## 5. 与执行者自身权限的配合

判定只看执行者自身权限（decision_b820faf42218 第 2 节）。按上述规则，Resident 默认能管理所在 Project 下的全部资源和全部公共内容、读取全部共享资源、给任何共享区的 Resident 发消息；修改其他 Project 的资源时，需要持有权限的主体签发临时凭证，或由管理员写入表项。

## 6. 不在方案中的规则

个人秘密、只能撤回自己的消息、个人待办只属于本人，以及 Family steward、Leader 等工作流角色的领域规则，属于权限系统之外的固定规则（decision_b820faf42218 第 4 节）。它们在开关关闭时同样生效，默认方案不重复表达。

个人 AMA 的私有性在开关开启时由本方案的放置表达（第 2、3 节），删掉相应节点就不再保留；开关关闭时由权限端口保留“个人 AMA 归所有者”这一条（decision_b820faf42218 §3.1）。
