---
kb_id: decision_bd5217d08c09
type: decision
project: AI员工
created_at: '2026-09-01'
version:
  number: 25
  created_at: '2026-09-01'
  created_by:
    user: ''
    agent: ''
  updated_at: '2026-10-19T13:19:28Z'
  updated_by:
    user: ''
    agent: resident_511e229bc8c3
importance: 7
metaness: 1
ai_generated: true
links:
  source:
  - ${code_root}/management_portal/society_service/principal_hierarchy.py
  - ${code_root}/management_portal/portal_ui_state.py
  - ${code_root}/management_portal/personal_ama.py
  - ${code_root}/management_portal/society_service/principal_hierarchy_domain.py
  - ${code_root}/tests/cases/test_personal_ama_provisioning.py
  - ${code_root}/management_portal/resident_sidebar_backend.py
  - ${code_root}/management_portal/society_service/resident_domain.py
  - ${code_root}/management_portal/static/home.js
  - ${code_root}/management_portal/static/lineage_workbench.js
  - ${code_root}/management_portal/serve_management_portal.py
  - ${code_root}/tests/cases/test_resident_sidebar_backend.py
  - ${code_root}/tests/cases/test_resident_context_reset.py
  - ${code_root}/management_portal/society_service/personal_assistant.py
  - ${code_root}/management_portal/static/portal_agent_inbox.js
  related:
  - decision_44cf587c1721
  - decision_8fe87d812a4c
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

本篇说明这条关联怎样保存、Home 和 Society 工作台怎样显示入口，以及目标不可用时的表现。Resident 本身的身份与成员关系见 decision_0f57656a2acd。个人 Lineage 建成私人 Family 后，用户另有一个指向 Family 门面的个人助手入口，见下文“个人助手入口”；私人 Family 的路由、历史投影和 Steward 规则见 私人 Family 与个人助手。

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
- **个人 AMA 只属于它的所有者，管理员也看不到。** 每个用户只有一个 AMA Lineage（`lineage_membership=personal`）。这个 Lineage、带 `personal_owner_id` 的 Resident 与 Family，以及个人 Lineage 目录中的 Workspace，只对登记所有者和代表所有者的 Resident 开放：权限端口在询问权限系统之前回答，它们得到 admin，其他任何人得到 none，管理员、实例中增加的表项和临时凭证都不例外；权限系统关闭时同样如此（TFI portal 2026-10-12，decision_b820faf42218 第 4 节）。Lineage 列表、Resident 目录、资源搜索和按 ID 读取都按这条规则隐藏；登记所有者本人直接可见，登记尚未同步完成时也能使用自己的 AMA。带 `personal_owner_id` 的分支被移到别的 Lineage 后仍按所有者私有。个人 Lineage 中新建的 Resident（私人 Family 的门面、Router、steward 和后加入的成员）同样登记 `personal_owner_id` 为所有者，受同一保护，并且不进入任何人的找人候选。
- **入口失效时只影响入口本身。** 关联缺失、目标已删除、用户失去查看权限或关联读取失败时，入口显示为不可用，其他入口和 Resident 目录照常工作。

## 登记所有者与代表权限

个人 AMA 及其所有分支代表登记所有者。登记时保存 `personal_owner_id`，Fork 逐代继承同一字段，ResourceService 从 Resident 描述符中的该字段读取代表关系；调用者、创建者、名称、页面关联和旧 `record_type` 不替代登记所有者。代表关系只用于上述私有保护：代表所有者的 Resident 对所有者的个人 AMA 资源有 admin。对其他资源，这些 Resident 与任何 Resident 一样只按自己的权限判定，不继承所有者或发起者的权限，也不向分支复制或签发所有者的权限（decision_b820faf42218）。

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
