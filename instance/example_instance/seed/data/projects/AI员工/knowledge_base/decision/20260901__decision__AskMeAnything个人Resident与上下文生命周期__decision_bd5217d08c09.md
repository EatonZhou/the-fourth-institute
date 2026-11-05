---
kb_id: decision_bd5217d08c09
type: decision
project: AI员工
created_at: '2026-09-01'
version:
  number: 33
  created_at: "2026-09-01"
  created_by:
    user: ""
    agent: ""
  updated_at: "2026-11-05T16:01:45Z"
  updated_by:
    user: ""
    agent: "resident_2e9ea0072551"
importance: 7
metaness: 1
ai_generated: true
links:
  source:
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/society_service/principal_hierarchy.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/portal_ui_state.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/personal_ama.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/permission_system/personal_ama.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/society_service/principal_hierarchy_domain.py
  - /opt/tfi/the-fourth-institute-dev/code/tests/cases/test_personal_ama_provisioning.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/resident_sidebar_backend.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/society_service/resident_domain.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/static/home.js
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/static/lineage_workbench.js
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/serve_management_portal.py
  - /opt/tfi/the-fourth-institute-dev/code/tests/cases/test_resident_sidebar_backend.py
  - /opt/tfi/the-fourth-institute-dev/code/tests/cases/test_resident_context_reset.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/society_service/personal_assistant.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/static/portal_agent_inbox.js
  related:
  - decision_328fb2548b8f
  - decision_44cf587c1721
  - decision_8fe87d812a4c
  - plan_7dddf00a1913
  - decision_3e07c6d35cac
  - decision_0f57656a2acd
  - decision_c47a13e8b962
  - decision_b1ab45c4b3ec
  - decision_152d5c046a23
  - decision_34158fdd4078
  - decision_b820faf42218
  supersedes: []
  derived_from: []
---
# Ask Me Anything 的个人 Resident 入口关联

Ask Me Anything 是每个用户自己的个人 Agent 关联。用户可以从 Home 的 Agent Access 或已有会话、直接地址回到自己长期使用的那个 Resident。Society 工作台导航不再提供固定的 Ask Me Anything 快捷卡片。关联保存在用户个人页面状态中，指向一个普通 Resident。

本篇说明这条关联怎样保存、Home 和 Society 工作台怎样显示入口，以及目标不可用时的表现。Resident 本身的身份与成员关系见 decision_0f57656a2acd。个人 Lineage 建成私人 Family 后，用户另有一个指向 Family 门面的个人助手入口，见下文“个人助手入口”；私人 Family 的路由、历史投影和 Steward 规则见 [私人 Family 与个人助手](/resource/knowledge?item=decision_328fb2548b8f)。

## 基本概念

| 名称 | 含义 |
| --- | --- |
| 个人入口关联 | 按用户保存的一条页面状态，说明这个用户的 Ask Me Anything 指向哪个 Resident。 |
| 目标 Resident | 关联指向的 Resident。它是普通 Resident，有自己的会话、工作区和运行设置。 |
| 页面状态存储 | Portal 为每个用户保存页面偏好和关联的存储（UI state store），按“命名空间 + 键”存取，每个用户只能读写自己的内容。 |
| Agent Access | Home 页面中列出常用 Agent 入口的面板。 |

## 设计点

### 入口只是一条个人关联

- **Ask Me Anything 不是一种特殊的 Resident。** 关联只记录“这个用户的个人入口指向哪个 Resident”。它不复制会话，不设置 owner，不授予查看权限，不改变工作区、运行设置或 Lineage 成员关系。
- **关联只属于一个用户。** 它保存在该用户的页面状态中，其他用户知道命名空间或 Resident ID 也读不到。
- **Resident 的变化不影响关联。** 目标 Resident 重启会话、清空上下文或改名之后，关联仍指向原来的 `resident_id`；Fork 出的新 Resident 不会自动成为个人入口。
- **账号创建时一并建好。** 管理员创建账号或用户领取邀请后，服务以新用户本人的名义建好它的 Ask Me Anything：一个标题为 Ask Me Anything 的个人 Lineage（`lineage_membership=personal`，不属于任何 Project，组织层级为用户层），其中一个 Codex 新会话的 Leader Resident（组织类别 `personal_ama`，登记 `personal_owner_id` 为该用户），并在关联缺失时写入指向它的关联。已有个人 Lineage 时不再新建；已有关联时保持不变。这一步失败不撤销账号。下次成功登录时，Portal 内部重试同一个幂等创建函数；仍失败时记录错误并继续登录，下次再重试。创建和补建均复用登记的个人所有者，已有关联保持不变（decision_6eac174d85bf）。
- **同一流程把个人 Lineage 建成私人 Family。** AMA 和入口关联就绪后，服务以用户本人身份以 AMA 为唯一成员 Familize 这个 Lineage，门面接替 AMA 成为 Lineage leader；Lineage 标题仍为「Ask Me Anything」时改为「Ask Me Anything | My Lineage」，用户自己改过的标题保持不变。AMA 的身份、Session、`resident.md`、历史和原入口关联都不变。已有用户在下次登录或已登录状态下打开页面时于后台补做（页面读取 `/api/me` 时触发，每个 Portal Web 进程每位用户检查一次），失败只记录错误，10 分钟后打开页面再试。
- **页面不自动创建。** 打开页面时关联缺失，Portal 不创建 Resident，也不猜测另一个 Resident；Portal 页面没有设置个人入口的操作。

### 只有能看到的 Resident 才显示为入口

- **入口可用的前提是目标出现在当前用户可见的 Resident 目录中。** 关联本身不授予访问权；用户打开、发消息、清空上下文、重启或 Fork 时，服务端照常检查权限。
- **个人 AMA 只属于它的所有者，管理员也看不到。** 每个用户只有一个 AMA Lineage（`lineage_membership=personal`）。这个 Lineage、带 `personal_owner_id` 的 Resident 与 Family，以及个人 Lineage 目录中的 Workspace，只对登记所有者、代表所有者的 Resident 和 Portal 自身存在（weihao 2026-10-12）。这是 Society 对个人 AMA 的规定，不是权限规则（weihao 2026-10-26）：共用的登记所有者查询和私有性判定函数位于 `permission_system/personal_ama.py`，Society 在进入权限端口前执行私有性判定，权限实现复用登记所有者查询来判定主体代表关系。私有性判定在权限系统开或关时都生效；管理员和普通表项不能越过它。权限系统开启时，所有者本人或其既有 AMA 可用正式临时凭证委托精确资源与动作，判定读取活跃临时行，详见本文“对话中的临时授权与个人 AMA 委托”。对其他主体，这些对象不存在：读、改、搜索、列出得到 not found，不提示“无权”或需要什么权限。权限系统开启时，规定放过的再由方案判定。Lineage 列表、Resident 目录、资源搜索和按 ID 读取都按这条规定隐藏；登记所有者本人直接可见，登记尚未同步完成时也能使用自己的 AMA。带 `personal_owner_id` 的分支被移到别的 Lineage 后仍按所有者私有。个人 Lineage 中新建的 Resident（私人 Family 的门面、Router、steward 和后加入的成员）同样登记 `personal_owner_id` 为所有者，受同一保护，并且不进入任何人的找人候选。
- **给 AMA 发消息对所有人开放**（weihao 2026-10-26）。投递例外不要求先取得临时委托：任何已登录的 User 和 Resident 都可以 DeliverMessage 或 ForwardMessages 到任何人的个人 AMA Resident，Resident 做完别人通过 AMA 交办的任务后可以直接回传。只开放投递，读对话、搜索、查看状态和管理仍按私有规定处理。不能读该 AMA 的发送方只得到自己那条消息的回执（消息 ID、消息本身、输入状态、是否接收与排期），不带 AMA 的状态、队列、游标或对话控制，也看不到主人的消息；引用回复仍要求能读被引用的对话。权限系统开启时，投递还要通过方案：默认方案“全员可给 Resident 发消息”覆盖个人空间中的 AMA（decision_600ae27b0538）。
- **入口失效时只影响入口本身。** 关联缺失、目标已删除、用户失去查看权限或关联读取失败时，入口显示为不可用，其他入口和 Resident 目录照常工作。

## 登记所有者与代表权限

个人 AMA 及其所有分支代表登记所有者。登记时保存 `personal_owner_id`，Fork 逐代继承同一字段，ResourceService 从 Resident 描述符中的该字段读取代表关系；调用者、创建者、名称、页面关联和旧 `record_type` 不替代登记所有者。代表关系有两个用处：

- 私有规定把代表所有者的 Resident 当作所有者，它们能访问和管理所有者的个人 AMA。
- **AMA 等同于主人**（weihao 2026-10-26）。在权限判定中，这些 Resident 与所有者 User 拥有同样的权限：用户能做的事，AMA 自动能做。权限引擎判定它们时加上主人在权限树上的位置，主人的表项、分类和收到的临时凭证都对它们生效，与方案怎样放置 AMA 无关；Portal 管理员保底也按主人判定，但它们不能对主人本人做管理员名单变更。临时凭证中 AMA Resident 与主人算同一方，两者之间不签发凭证，一方可以续期、收回或交还另一方的凭证（decision_b820faf42218 第 3.1、5.5、5.6 节）。

这只对登记了 `personal_owner_id` 的 Resident 成立；其他 Resident 仍只按自己的权限判定，不继承发起者的权限。

页面入口关联只决定导航。更换入口目标不会改变任何 Resident 的 `personal_owner_id`。分支的创建者可以与登记所有者不同，代表关系仍随来源 Resident 继承。

## 关联的数据

| 项目 | 值 |
| --- | --- |
| 命名空间 | `lineage-workbench.personal-ama` |
| 键 | 优先读取 `primary`；没有 `primary` 时取该命名空间中的第一条 |
| 值 | 必含 `resident_id`；还可以带 `lineage_id` 和 `presentation`（展示用的标题等） |

页面通过 `/api/ui-state/associations` 读取当前用户的关联；`/api/ui-state/associations/put` 和 `/api/ui-state/associations/remove` 可以写入和删除。每个值最大 1 MiB，每个命名空间最多 2,000 条。

## 在 Home 上

Agent Access 面板的第一项是 Ask Me Anything 卡片：

- **可用时**是一个链接，标题固定为“Ask Me Anything”（不使用目标 Resident 自己的标题），说明为“打开个人 Agent”，前面可能带目标的运行状态，右侧有箭头图标。点击打开 Society 工作台并选中目标 Resident（`/lineage?resident=<ID>&lineage=<Lineage ID>`）。
- **不可用时**仍显示同名卡片，说明为“当前暂不可用”，不能点击。

Home 同时读取关联和 Resident 目录；目录还没准备好时按服务给出的节奏重试（间隔从 0.25 秒逐步加到 2 秒），确认目录后再判断目标是否可见。关联读取失败时按“没有关联”处理。

Agent Access 是 Home 中可以隐藏的面板，例如“每日概览”布局隐藏了它。隐藏面板只影响 Home 的显示，不删除关联，也不影响已有会话或直接地址访问。

## 在 Society 工作台侧栏

- **导航不显示固定的 Ask Me Anything 快捷卡片。** 桌面与移动端的 Lineages、My Favorite 面板使用同一规则，顶部依次保留 Which Resident、个人助手卡片，之后是各自的列表。此规则只移除固定入口，不删除个人关联、Resident、历史会话或私人 Family，也不阻止通过已有会话、Home 或直接地址访问 AMA。
- **页面不提供删除入口。** 侧栏菜单中不显示删除这个 Resident 的操作；删除它所在的 Lineage 时提示“Personal AMA 的 Leader Resident 不能删除。”。这只是页面上的保护，服务端没有对应的限制。
- **目标不在当前目录中时，侧栏服务尝试补上它。** 侧栏数据由 `resident_sidebar_backend.snapshot` 生成：读取关联后，如果目标 Resident 不在当前用户的 Resident 目录中，服务以当前用户的身份读取目标的资料和运行状态，成功时把它追加到列表。补上的条目带有 `is_lineage_leader: true`，只用于侧栏布局，不改变真实的 Leader。读取失败（不存在、无权限或数据无效）时跳过补充，其余侧栏照常返回。
- **侧栏数据按用户缓存 15 秒。** 数据变化时 Portal 清除缓存。

工作台把关联和项目、收藏等次要数据一起并行读取，用于识别个人 AMA 和保留页面删除保护，不再将其置顶为快捷卡片。每次读取带有序号，迟到的旧响应不会覆盖新结果。

## 个人助手入口

个人 Lineage 建成私人 Family 后，Family 门面是用户的个人助手：用户从这里和自己的成员对话，原 AMA 是默认成员（decision_328fb2548b8f）。个人助手有自己的入口关联，与 AMA 关联并存：

| 项目 | 值 |
| --- | --- |
| 命名空间 | `lineage-workbench.personal-assistant` |
| 键 | `primary` |
| 值 | `resident_id`（门面）、`lineage_id`、`family_id`（`family_…` 资源 ID）、`presentation: {kind: "personal-assistant"}` |

关联由初始化流程写入，值与 Family 当前角色不一致时更新；页面不创建、不修改它。页面用 `PortalNav.personalAssistantTarget()` 读取关联，经 `navigationTarget` 确认门面可读且属于关联中的 Lineage，再用 `GetLineage` 读取 Lineage 当前标题作为显示名称。关联缺失、ID 不合法或目标不可读时不显示个人助手入口，AMA 入口照常工作。

- **导航卡片。** Lineages 与 My Favorite 两个导航面板顶部，在 Which Resident 之后显示个人助手卡片，标题是个人 Lineage 的当前标题（默认「Ask Me Anything | My Lineage」），点击打开门面对话。用户给 Lineage 改名后，Lineage 变更事件触发重新读取，卡片标题随之变化。
- **全局悬浮章鱼。** 全局悬浮的「我的 Mailbox」按钮下方紧贴着章鱼按钮，两者拼成一个不留缝隙的悬浮标签：共用尺寸、外框、阴影、拖动位置和整组避让，一起上下拖动，靠底色区分（章鱼按钮带主题强调色的浅底）；两个面板互斥。每个页面打开时，章鱼按钮按浏览器为当前用户记住的上次确认结果与 Mailbox 按钮同一帧画出，随后按服务端读到的入口更新或隐藏，并记住新结果；同一浏览器的其他用户看不到它。鼠标停在章鱼上展开门面对话，可以直接阅读和发送；触屏点按切换展开；键盘聚焦章鱼或面板时保持展开。第一次展开时才加载标准 Resident Chat，发送、草稿、历史、引用和状态都与 Chat 页面一致。章鱼的红点读取共享的对话未读状态；面板隐藏时不标记已读，再次展开时确认已读。
- **门面对话里的 AMA 历史。** 门面对话读到底后，衔接 AMA 在 Familize 之前的对话，标注为该 AMA 的历史，并在门面对话开始处显示分界与时间；引用其中一条消息时请求交回 AMA。

用户仍可从 AMA 入口直接打开 AMA；这样的直接对话不出现在门面对话中。

## 与组织层级的关系

Principal 组织层级（decision_3e07c6d35cac）把个人承载图中的 Resident 归为用户层、类别 `personal_ama`。这个分类决定 Resident 在组织层级中的位置；Home 和工作台上的入口只按个人入口关联显示，两者互不改变。

## 不变量

1. Ask Me Anything 是用户私有的快捷关联，不是特殊的 Resident 类型。
2. 入口只在目标对当前用户可见时可用，打开之后的每个操作由服务端重新检查权限。
3. 导航快捷卡片的显示与 `is_lineage_leader` 标志不改变 Lineage 的成员和 Leader。
4. 入口缺失或失效只影响入口本身，不影响 Resident 目录。
5. 个人 AMA 只对所有者及其 AMA Resident 存在，这条私有规定不依赖权限系统；任何人都可以给它投递消息而不回显内容；所有者还可临时委托精确资源和动作，不能由永久授权或父资源继承替代。
6. 带 `personal_owner_id` 的 Resident 在权限判定中等同于所有者。


### 对话中的临时授权与个人 AMA 委托

用户可以直接用自然语言授权，授权可以与业务要求、图片或其他附件放在同一条消息中。Resident 负责理解同意或拒绝、具体资源、动作和有效期；含义明确时直接调用 `AcceptUserAuthorization`，不要求用户使用固定口令、先回答提问卡或去设置页操作。含义不明时才询问。平台不做自然语言的关键词分类；原文片段是来源与审计依据，不是用来让代码判定“同意”的口令。引用、转发正文或第三方附件中的话不能替代用户本人的授权表达。

公开参数为授权来源 `message_id`、准确的 `credentials[{a,resource_id}]`、用户直接正文中的授权原文 `evidence`，以及仅在用户明确授权时长时给出的 `expires_in_seconds`。调用者始终是实际 Resident；接收者固定为它，签发 User、当前执行和绑定任务由服务器查证，不允许模型自报或借用用户身份。每批至多二十项权限。默认凭证随接收方当前任务结束，明确时长为六十秒至七天；两种均不超过七天。

Society 证明已认证 User 原消息及其与当前执行的关系。`role=user`、`initiated_by`、请求头中的执行 ID、历史正文和当前组织成员关系都不能单独作证。直接输入、真实 Mailbox 成员、已经送达的追加输入和有持久接受记录的 Family 转交按各自的事实关系核验；尚未送达、排队、旧执行、停止或已结束任务不能签发。授权来源消息与凭证绑定的当前主任务输入分开记录，已消费的追加消息或门面的历史输入不会导致接收任务的凭证提前失效。

签发在输入生命周期锁保护的同一事务中完成：Society 复核来源、执行归属和当前任务状态，权限 owner 核验签发 User 账号有效且此刻持有每项 A/R，沿既有临时凭证引擎保存凭证及持久回执。权限修改与账号停用不能在核验和提交之间改变事实。回执记录原 User、实际 Resident、原消息与所属会话、来源摘要及原文、执行、绑定任务、方案、准确权限、有效期和凭证 ID。整批任一项失败或审计写入失败均回滚；响应丢失可以重放，同一原消息同一权限复用原凭证而不延长期限，后续独立权限保留独立不可变记录。换执行、换来源或改有效期产生冲突；撤销、到期、输入终结或重建方案使原凭证消失后，不得凭旧授权重签或扩签。默认随任务凭证在任务完成、失败、取消的同一事务中删除。

自然语言消息按原样入会话，不改造成 `async_question_answer`，不从旧问题推导权限。结构化 `portal-question.credentials` 卡片仍可在需要询问时使用：用户明确选择后沿既有 `MessageLending` 原子投递与签发。它是可选交互，不是自然语言授权的前置条件。

个人 AMA 的所有者可据此向其他主体临时委托精确资源与动作。私有边界只接受登记所有者或其既有 AMA 实际签发给当前主体（或其原有代表的 User）、仍活跃且未停用的临时 permit；永久 grant、管理员身份、其他签发者、group、父资源继承和再次转借均不能打开 AMA。父子在同批检查中也逐个核对精确 `r`。登记所有者、其 AMA 与 Portal 原有路径不变。

`personal_ama.reaches(actor, owner, resource_id='', *, action='ReadResource')` 和 `hidden(actor, owners, *, action='ReadResource')` 只回答私有边界是否可达。省略资源 ID 保持原所有者规则。Society 对受委托对象传精确资源及本次实际 A 元素，多槽指令传该资源的 `slot.element`；调用上下文结束立即复位，模糊的 manage 默认不能推定具体操作。Resource 列表使用读动作；查看一个精确 Resident 不因此打开它的父 Lineage、兄弟、Workspace 或文件。权限系统开启时，私有边界核对当前临时凭证的精确资源、动作范围及 deny，随后继续正常动作授权；不能把 viewer 扩成管理权限。到期逐次看时钟，撤销和停用随实例版本立即失效。OPEN 下只执行独立的所有者隔离：所有者、代表所有者的既有 AMA 和 Portal 自身可达，其他主体保持隐藏；不加载权限引擎或临时凭证，已保存的委托不会在 OPEN 模式开放他人的私人 AMA。
