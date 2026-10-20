---
kb_id: decision_5bc3fdfc7eec
type: decision
project: AI员工
created_at: '2026-09-20'
version:
  number: 54
  created_at: '2026-09-20'
  created_by:
    user: ''
    agent: resident_8be5b285cc4b
  updated_at: '2026-10-19T19:49:31Z'
  updated_by:
    user: tfi-exported-user
    agent: resident_865c3c24f251
importance: 8
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/resources/action.py
  - ${code_root}/management_portal/static/portal_user_avatar.js
  - ${code_root}/management_portal/static/portal_resident_avatar.js
  - ${code_root}/management_portal/static/portal_avatar_editor.js
  - ${code_root}/management_portal/static/portal_resident_menu.css
  - ${code_root}/management_portal/avatar_store.py
  - ${code_root}/management_portal/static/settings.js
  - ${code_root}/management_portal/static/portal_management.js
  - ${code_root}/management_portal/static/portal_nav.js
  - ${code_root}/management_portal/static/portal_scope_menu.js
  - ${code_root}/management_portal/static/principal_backpack.css
  - ${code_root}/management_portal/static/principal_backpack.js
  - ${code_root}/management_portal/web_isa.py
  - ${code_root}/management_portal/http_account.py
  - ${code_root}/management_portal/user_avatar.py
  - ${code_root}/management_portal/resident_avatar.py
  - ${code_root}/management_portal/http_principal_backpack.py
  - ${code_root}/management_portal/principal_backpack.py
  related:
  - decision_b820faf42218
  - decision_22227d0ad075
  - decision_3e07c6d35cac
  - decision_600ae27b0538
  - decision_ea13b85216d0
  - decision_30528aff7035
  - decision_34158fdd4078
  - decision_741b04821171
  - decision_b3022c27d22f
  - decision_e486671af066
  supersedes: []
  derived_from: []
metaness: 2
---

# Principal 个人卡片、Package、头像与实例配置

Portal 中能够持有权限的参与者称为 **Principal**，包括人类用户（User）和 Resident。每个 Principal 有一张**个人卡片**：界面上的入口名为 “Menu”，里面显示这个 Principal 的身份资料和 **Package**（权限系统中它名下的权限表项与登记关联）。Project 与 Lineage 也有 Menu，它们的 Package 列出该资源上的表项，并提供写入、删除表项的入口。

本文说明个人卡片与 Package 的显示和操作、User、Resident、Project 与 Lineage 的头像，以及 Portal Management 中相关分栏的界面行为。权限系统的判定、表项与临时凭证见 decision_b820faf42218，默认模板的三棵树与表规则见 decision_600ae27b0538，权限指令见 decision_22227d0ad075；账号的创建与删除见 decision_30528aff7035。

## 基本概念

- **权限表项**：权限表中的一条记录 (P 节点, A 节点, R 节点)，效果为 permit 或 deny，可以停用；表项属于启用中权限方案的实例，不记来源，临时凭证另带签发者和到期时间；它覆盖 R 节点及其子树中的全部资源（decision_b820faf42218）。
- **登记关联**：Principal 与业务对象之间已经登记的关系。Resident 有所属项目、所属 Lineage 和工作区绑定。登记关联用于展示业务归属，不是权限表项，也不代表全部权限。
- **Principal 层级**：每个 Principal 位于 Portal Admin、User、Project、Lineage、Resident 五层中的一层（decision_3e07c6d35cac）。卡片显示这个层级；它不授予权限，和 viewer、contributor、admin 这些访问级别是两个不同的维度。

## 个人卡片

- **个人卡片在当前页面上打开。** 入口有：页面右上角的账号菜单（打开自己的 User 卡片）、Resident 导航中的 Menu 命令、HATG 画布中双击 Resident 节点或节点三点菜单中的 Menu，以及 Chat Workspace 标题栏最右侧的 Menu 图标。旧地址 `/principal-backpack?principal=<ID>` 打开同一张卡片。打开卡片不改变当前页面和登录会话。
- **User 与 Resident 的外层卡片统一叫 Menu，身份区固定在上方，分栏导航在左侧。** 导航文字沿右侧细竖线对齐，无按钮背景，选中项用文字强调与下划线区分。细竖线从身份区贯穿至卡片底部；身份区与分栏内容之间不画横向分割线。User 从上到下为 Settings、Appearance、Package，各栏内容来自原账号设置组件（decision_e486671af066）。User Menu 复用 Resident Menu 的身份区与分栏样式（`portal_resident_menu.css`），标题、头像尺寸、细竖线位置和卡片高度与 Resident Menu 相同，标题只写 Menu，不加副标题；身份区显示头像和显示名，用户名与显示名相同时不重复显示。Resident 从上到下为 Settings、Harness、Package、Mailbox、Work Journal，默认打开 Settings；各栏仍使用原业务接口和权限判断，键盘导航顺序与视觉顺序一致。
- **有效登录用户可读取 User 卡片和名册。** Resident 卡片由 ResourceService 读取，入口确认真实身份与资源存在性。
- **名册搜索和跨 Principal 管理在 Portal Management 中进行。** 个人卡片只显示一个 Principal。
- **关闭卡片时保护未保存的修改。** 有未保存的编辑时先确认；保存等操作进行中时不能关闭；关闭后焦点回到打开卡片的控件，未完成的读取和监听被取消。
- **Resident Menu 由共享 `PortalResidentMenu` 管理。** Chat Workspace、导航入口与 HATG 调用同一组件；HATG 只响应改名、转移和删除后的图更新，不维护另一套 Menu 模板、样式或分栏状态。Harness、Package 和 Mailbox 仅在选中对应分栏后加载，打开 Menu 不改变当前聊天目标。
- **Resident 身份区显示头像、名称、Resident ID 与所属 Lineage 名称和 ID。** 名称旁的铅笔打开内联改名，保存成功后更新标题并保留 Menu；失败保留输入。接单状态直接显示 OPEN/CLOSED 并复用现有接单切换。Move 打开现有 Lineage 转移选择与确认，Leader 的转移限制保持不变。
- **右上角星标管理当前用户的收藏，叉号关闭卡片。** 星标用黄色标识，通过 GetUserNavigation 与 UpdateUserNavigation 读取、修改收藏，成功后同步左侧“我的收藏”；失败保留原状态并提示。收藏不修改 Resident、Lineage 或权限。
- **Mailbox 中每类消息均可展开全文。** 待处理请求、群体、info，以及处理中和最近已送达的请求及其随行 info，均以可点击、可键盘操作的正文预览提供“展开全文／收起全文”。第一次展开沿既有 GetMessage 读取，重复展开复用本次挂载的结果；群体分别读取成员消息。正文与附件名安全显示，读取失败可收起后重试，关闭后不渲染迟到结果。
- **Work Journal 展示当前 Resident 的职责与活动摘要。** 正文直接展示摘要，不重复显示 Work Journal 栏目标题。 第一次打开时通过 GetResidentSummaries 只读取当前 Resident，按职责范围、当前重点、近期成果、进行中的工作和近期变化组织摘要服务已有内容；显示摘要更新时间与待更新状态。没有摘要或读取失败时明确提示，失败可重试；不以运行列表替代职责，不自动启动摘要重建。内容按文本显示，同卡片内重复打开复用结果，迟到读取不覆盖关闭或切换对象后的卡片。
- **Resident Settings 以 Backend、Model、Effort、Fast 四行呈现当前运行设置。** Backend 打开现有 Session 切换界面，其余行使用共享运行设置编辑器与 Provider 能力目录；更改提交现有 Resident 设置接口，不创建第二套模型设置或绕过账号路由。运行设置下方以独立的紧凑小字信息行显示完整 Session ID，使用等宽字体、按宽度换行和专用复制按钮；尚未绑定时显示“尚未创建”并禁用复制，读取失败显示原因且不影响运行设置。Session 身份沿用共享 Resident 界面信息缓存，打开 Menu 时按需读取，完成 Backend 切换后重新读取；不新增轮询，迟到结果不覆盖关闭或切换对象后的菜单。Session ID 下方是同样排版的头像行，点击“编辑头像”展开头像编辑区（见“头像”）。删除节点保留在 Settings 底部，底栏滚动时盖住其下方内容。
- **Harness 分栏紧跟 Settings，集中 Resident 的 Harness 设置。** 分栏显示 Prompt 注入（点开进入预设、分类时机与预览子页面）、允许开启 sub agent（Provider 声明 `subagents` 能力时才出现）、失败后续跑一次、智能选择 Harness 和各项 Harness 功能；可用项由 Resident 投影中的 `runtime_capabilities` 推出。开关改动和 Prompt 注入改动（`changes.prompt_injection`）都通过 `UpdateResidentSettings` 保存为该 Resident 自己的设置，保存后重新读取 Resident 投影。Chat 扩展菜单中的“Harness”一行直接打开这个分栏。
- **删除提交成功后立即关闭对应的 Resident Menu。** 页面所属组件的后续刷新失败，也不能继续显示已删除 Resident 的设置或删除按钮；删除失败时保留当前菜单并显示错误。
- **卡片共用 Portal 的弹层、按钮、图标和焦点管理。** 切换分栏保持卡片尺寸，内容超长时内部滚动；长名称与 ID 可换行，窄屏仍保留身份区和左侧分栏，不使控件溢出视口。Package 在身份区下嵌入时省略重复头像资料。

## Project 与 Lineage Menu

- **Project 和 Lineage 也有 Menu，沿用 Resident Menu 的弹层、身份区和左侧分栏。** 共享组件 `PortalScopeMenu` 通过 `PortalNav.openScopeMenu({kind, id})` 打开。Project 详情标题栏的 Menu 按钮打开 Project Menu；Society 工作台标题旁的设置按钮打开 Lineage Menu。左侧导航中 Project 和 Lineage 卡片的三点菜单也提供 Menu，替代旧 Edit。入口按卡片的真实资源类型和 ID 打开：Project 不借用其 Project 层 Lineage 的身份，没有 Project 层 Lineage 的可读 Project 仍能打开自己的 Menu。身份区显示头像、名称、ID 与类型标签，Lineage 另显示所属项目；没有头像时显示名称首字。
- **Project 的分栏为 Settings、Package、Lineages、Work Journal；Lineage 的分栏为 Settings、Package、Residents、Work Journal。** 名称相同的分栏按层级取不同内容，各栏只在选中时读取。
- **Settings 修改本层的头像和登记字段。** 第一行是与个人 Menu 相同的头像行，“编辑头像”展开头像编辑区（见“头像”）。下方的字段网格中，Project 修改状态、类型和截止时间，提交 UpdateProject；Lineage 修改名称、Leader 和说明，提交 UpdateLineage。只提交与当前值不同的字段，“保存设置”和状态提示在网格下方；成功后通知打开 Menu 的页面更新标题、看板或画布。所属项目、导航可见性和删除仍在原 Lineage 设置对话框中，从 Society 工作台打开的 Lineage Menu 在 Settings 最后一行“所属项目、导航与删除”提供打开该对话框的按钮。任一入口保存 Lineage 名称、说明或 Leader 后，已挂载的同一 Lineage 画布同步登记字段，再打开原设置表单时使用新值；其他 Lineage 实例不受影响。
- **Package 展示本资源上的权限表项。** 内容来自 ListPermissionEntries（`r` 为本资源，`r_scope: "ancestors"`，最多 500 条，超出时注明只列出前若干条、完整的表在 Portal 管理的“权限方案 · 实例表”中）：本资源及其在 R 树上各级上级节点上的表项，每条显示主体节点、A 节点（deny 另标）、资源节点和状态（临时凭证、已停用）；临时凭证以外的表项可以逐条删除（DeletePermissionEntry）。下方的“写入表项”表单向一个主体写入本资源上某个 A 分类节点的 permit 表项（CreatePermissionEntry）。
- **成员分栏（Lineages / Residents）列出下一层成员，并提供批量授予。** Project 的成员是其 Lineage（SearchResources 按项目筛选），Lineage 的成员是其 Resident；每行显示成员头像，行尾“Menu ›”打开该成员自己的 Menu，列表上方有全选和已选数量。勾选成员后有两个方向：给一个主体写入对所选成员的表项；或给所选 Resident 写入对一个资源的表项，在 Project 中所选 Lineage 展开为其中全部 Resident。“批量删除同一 A 节点的表项”删除 P、A、R 与本次批量完全一致的持久表项：对每一对（主体, 资源）先用 ListPermissionEntries（`temporary: false`）读取匹配行，再用一次 DeletePermissionEntry（`entry_ids`）删除。每一项都是一次普通的 CreatePermissionEntry 或 DeletePermissionEntry，由权限系统判定，失败项（包括内容相同的表项已存在）连同原因列在结果中，不影响其他项。
- **权限系统关闭时不显示 Package 内容和批量授予。** 此时没有权限表项可以管理（decision_b820faf42218）。
- **Work Journal 按层级汇总。** Lineage 通过 GetResidentSummaries（每次至多 20 个）展示每个 Resident 的当前重点与近期成果；Project 展示 summary.md 正文和各 Lineage 的说明。

## Package

### 显示内容

- **表项与关联资源分开显示。** 表项来自 GetEffectivePermissions，以图标格列出该主体及其在 P 树上各级上级节点名下的全部启用表项和有效的临时凭证，写在分类节点上的表项（如免检、全员可读）也一样列出。关联资源只说明登记关系。表项覆盖其 R 节点及子树中的资源，子树中的资源不另列。
- **Package 展示被查看主体自己的表项。** 不从角色或内置账号名单虚构权限，也不借用查看者的权限。
- **查看他人的 Package 需要是该主体本人或管理员**（GetEffectivePermissions 的要求，decision_22227d0ad075）。读取失败可重试；空清单只说明该主体名下没有表项或临时凭证。
- **个人 Menu 的 Package 只读。** 写入和删除表项在 Project 与 Lineage Menu 的 Package 或管理页的“权限方案 · 实例表”中进行，临时凭证由持有者用 IssueTemporaryCredential 签发。权限系统关闭时，Package 说明权限系统已关闭，不显示表项。
- **Package 不包含秘密。** 页面不显示密码、认证密钥、检查点或令牌文件内容。
- **Resident 的层级未登记时显示“未登记”。** 页面不从名称推测层级。

### 分类与图标

- **个人 Menu 内的 Package 直接展示类别页签和物品格。** 不叠加标题、刷新按钮或围住整个 Package 的外框；必要的加载、空清单、错误提示与重试入口保留。
- **顶部固定五个页签：All、Project & Lineage、Device、Resident、Others，默认 All。** All 显示全部表项。Project & Lineage 包含 Project、Lineage、Workbench 和知识库内容类资源（论文、决策、想法、待办等）；Device、Resident 各自一类；其余类型、分类节点和未识别的类型归入 Others。分类只影响显示，不改变授权，也不表示资源在 R 树上的位置。
- **页签保持单行。** 选中的页签以下划线标出；窄屏时在页签栏内横向滚动。切换页签只切换图标区，不重新读取资料；没有表项的类别也可以选择。
- **每条表项是一个 36 像素的图标格（奖章）。** 图标形状表示资源类型；边框颜色表示效果。R、A 分类节点（ID 以 `@` 开头）的名称取自启用方案规则中该节点的 `title`，每套方案用 GetPermissionScheme 读取一次，读取失败时显示节点 ID。悬停与键盘聚焦提示显示资源名称、资源类型、A 节点名称、deny 标记和来源（权限表项或临时凭证，有签发者或到期时间的是临时凭证），无权读取名称时以资源 ID 回退。点击格子、触屏点选或键盘 Enter/Space 打开表项详情卡片，展示资源名称、资源 ID、A 节点、效果、来源、主体节点、签发者、到期时间和表项 ID。关闭返回原格子，刷新、切换主体、宿主销毁时清理详情卡片。名称读取沿用现有鉴权与按需缓存，迟到响应不覆盖新的主体。
- **资源名称按需读取。** 显示提示时，页面以当前登录者的权限读取资源名称；无权读取或资源不存在时显示资源 ID。
- **Package 的展示字段不授予权限。** 判定只看执行者自身的表项（decision_b820faf42218）。

## 头像

User、Resident、Project 和 Lineage 都可以有头像。头像有两种：42 个海洋生物图案中的一个（`{"kind": "sprite", "sprite_index", "sprite_rarity"}`），或上传的图片（`{"kind": "upload", "version", "url"}`，`version` 是图片内容的 SHA-256）。

### 默认头像

- **Resident 在第一次显示时抽取一个海洋图案。** 图案分为 R、SR、SSR、SSSR 四种稀有度。抽取时先以 1% 的概率得到 SSSR；否则按 75%、20%、5% 的概率得到 R、SR、SSR，再在该稀有度的图案中随机选一个。不同 Resident 可以重复。抽到的图案按 `resident_id` 保存，不随消息、会话、观看者或排序变化，只有修改头像时才改变。
- **User 没有设置时使用第一个海洋图案；Project 和 Lineage 没有设置时显示名称首字。**
- **页面只收到图案序号和稀有度，或图片地址。** 共享组件 `PortalResidentAvatar` 绘制图案和上传图片；Resident 头像文件损坏或数据无效时，页面复用默认普通海洋图案，不重新抽取或覆盖原绑定。Home 对调用者无权读取的 Resident 隐去身份，使用匿名活动的海洋图案，不使用其真实头像或生成身份链接。

### 修改头像

- **四种 Menu 的 Settings 都有同样的头像行。** User Menu、Resident Menu（Session ID 下方；Family 门面没有运行设置表单，头像行在 Family 设置上方）、Project Menu 和 Lineage Menu 都由共享组件 `PortalAvatarEditor` 渲染：平时只显示“编辑头像”按钮，点击后展开图案网格和“上传图片”。选择或上传后，身份区的头像即时预览；点击“保存头像”后才提交，未修改或保存中时按钮不可用；“取消”撤销预览并收起编辑区，保存成功后编辑区也收起。Project 和 Lineage 的编辑区另有“恢复默认”，保存后回到名称首字。头像保存中不能切换分栏或关闭 Menu；切换分栏会收起编辑区并放弃未保存的选择。User Menu 关闭前确认未保存的头像选择，其他 Menu 关闭时直接放弃。
- **上传的图片需要满足限制。** 最大 2 MiB，只接受 PNG、JPEG 和 WebP，不接受动画；宽和高都在 16–2048 像素之间。服务端解码后去除元数据，统一保存为 PNG。
- **保存通过 ISA 指令完成。** 上传图片时先用 `CreateUpload`（用途 `avatar`）上传，得到 `upload_id`；选择图案时直接使用 `{"sprite_index": 0–41}`。四种对象分别提交：

  | 对象 | 指令与字段 | 判定 |
  | --- | --- | --- |
  | User | `UpdateUser` 的 `changes.avatar` | 默认模板要求对目标 User 账号有 admin，账号本人满足 |
  | Resident | `UpdateResident` 的 `avatar`（与 `title` 至少提供一项） | 按 UpdateResident 对该 Resident 的要求 |
  | Project | `UpdateProject` 的 `changes.avatar` | 按 UpdateProject 的要求 |
  | Lineage | `UpdateLineage` 的 `changes.avatar` | 按 UpdateLineage 的要求 |

  `avatar` 的值为 `{"sprite_index": …}` 或 `{"upload_id": …}`，两者只能取一个；上传用途必须为 `avatar` 且已经完成；权限系统让上传只归上传者时（PAR 的 `owners_only`），只能使用调用者自己的上传，因为头像对所有登录者可见。Project 和 Lineage 的 `avatar` 可以为 `null`，表示回到名称首字。指令返回对象时带上保存后的 `avatar`。
- **保存成功后同一页面的其他位置随之更新。** User 头像保存后导航和 Package 立即更新；Resident、Project 和 Lineage 头像保存后，同一页面中读过该对象头像的位置使用新头像。保存头像只清除头像的未保存状态；已经打开的密码、Passkey、两步验证编辑和 Package 草稿继续受各自的保护。保存失败时保留所选内容。

### 存储

- **头像记录按对象保存为 JSON 文件，上传的图片以 PNG 保存在同一目录。** 保存新头像后，同一对象的旧图片随之删除。

  | 对象 | 目录（环境变量） | 文件名 | 格式 |
  | --- | --- | --- | --- |
  | User | `~/.tfi/portal/user_avatars`（`PORTAL_USER_AVATAR_ROOT`） | 用户名的 SHA-256 | `tfi.user-avatar.v1` |
  | Resident | `~/.tfi/portal/resident_avatars`（`PORTAL_RESIDENT_AVATAR_ROOT`） | `resident_id` | `tfi.resident-avatar.v1` |
  | Project、Lineage | `~/.tfi/portal/scope_avatars`（`PORTAL_SCOPE_AVATAR_ROOT`） | `project:<Project ID>` 或 `lineage:<lineage_id>` 的 SHA-256 | `tfi.scope-avatar.v1` |

  表中的 `~` 由服务进程的 `Path.home()` 确定，显式环境变量优先；未配置时使用表中的规范目录。站点若仍使用 `~/.dirtybase/portal/<kind>_avatars` 等实际目录，应由对应的 `PORTAL_*_AVATAR_ROOT` 显式指定，不根据目录是否存在自动回退。新写入使用表中 `tfi` 标签，读取仅接受与规范完整名称及版本精确相同的格式，不因读取改写记录。

- **写入在文件锁内完成，并用原子替换写入。** Resident 的首次抽取也在文件锁内完成，多个进程同时显示同一 Resident 只抽取一次。各服务进程按文件的修改时间和大小缓存读取结果，任一进程修改头像后，其他进程下次读取就拿到新值。
- **头像随对象一起维护。** DeleteProject 和 DeleteLineage 删除对应头像；RenameProject 把 Project 头像移到新 ID 下。

### 读取头像

- **读取头像不经过任何权限判断。** 已登录的观看者可以读取任何 User、Resident、Project 和 Lineage 已有的头像，不看观看者是谁、账号状态如何、能否读取那个对象。`GET /api/account/avatar?principal=<用户名或 resident_id>` 返回 User 或 Resident 的头像；`?project=<Project ID>` 和 `?lineage=<lineage_id>` 返回 `{"avatar": …}`，没有设置时为 `null`。上传的图片通过 `/api/account/avatar/image?<user|resident|project|lineage>=<ID>&v=<版本>` 读取，地址带图片内容的哈希，图片换了地址就换，所以浏览器可以长期缓存（`private, max-age=31536000, immutable`）。只有不带参数读取自己的头像设置（含可选图案列表）需要知道调用者是谁，修改头像需要有效账号。
- **Resident 只返回已经确定的头像。** 读取不会替还没显示过的 Resident 抽取头像，这时返回 404，页面显示名称首字母。
- **Chat 消息头显示发送者的头像。** User 和 Resident 发送的消息在名称前显示头像；Portal 等服务发送的消息只显示首字母，不读取头像。同一页面对同一发送者只读取一次（5 分钟内）。读取失败（如 Portal 重启）时先显示首字母，并在 3、10、30、60 秒后重试，直到成功；图片加载失败同样重试。已经知道的头像在消息重新渲染时直接画出，消息节点保留，不会闪回首字母，也不会重新下载图片。

头像只是外观，与权限和身份配置无关。

## Portal Management 中的权限分栏

Portal Management 的页面整体与分栏入口见 decision_30528aff7035；本篇只定义其中与 Principal 和 Package 有关的界面行为。“权限表项”使用本文的组件；“权限方案”是独立的方案编辑器（decision_ea13b85216d0），同样经 `PortalNav.mountPermissionManagement`（view `entries` 或 `schemes`）挂载。

- **权限表项分栏：** 左侧名册列出 User 和 Resident（User 名册向有效登录用户显示；Resident 名册分页读取，每页默认 60 个、最多 100 个），选中后显示该 Principal 的 Package（GetEffectivePermissions 返回的表项），带刷新按钮。权限系统关闭时这个分栏隐藏。
- **执行设置的“权限”分组：** 管理员在这里开关权限系统（`permission_system_enabled`），并从内置方案与自定义方案中选择启用的方案；换用其他方案前先以 ActivatePermissionScheme 的 `dry_run` 报告补齐或生成实例的节点和表项数，确认后切换，各方案实例中的修改原样保留（decision_b820faf42218）。

### 分栏的加载与刷新

- **每个分栏第一次打开时挂载，之后保留。** 页面通过 `PortalNav.mountPermissionManagement({container, principalId, view})` 挂载权限组件，组件提供 `destroy`、`hasUnsavedChanges`、`isBusy`、`getPrincipalId`、`setPrincipalId` 和 `refresh`。切换分栏保留选中的 Principal、滚动位置和草稿。
- **权限变化只刷新受影响的视图。** 写入、删除表项等操作发出变化事件，说明受影响的视图和 Principal；回到该视图时局部刷新。视图中有草稿或提交进行中时，刷新推迟，输入保留。
- **切换 Principal 需要组件同意。** 组件有未保存的草稿时可以拒绝切换，页面不会清除草稿来强行切换。
- **关闭后迟到的结果被丢弃。** 卡片或分栏销毁后返回的读取结果不再显示。

## 接口

个人卡片和“权限表项”分栏的名册与资料通过 `/api/principal-backpack/` 下的只读接口取得。它们只接受浏览器登录会话，操作者从会话取得，请求中不能包含 `actor`、`subject_ref` 或 `context` 这类替换身份的字段。

| 接口 | 用途 |
| --- | --- |
| `GET /catalog?q=&offset=&limit=` | 名册：User 与 Resident |
| `GET /detail?principal=` | 一个 Principal 的资料与登记关联 |

错误：参数错误 400，无权 403，Principal 不存在或不可读取 404，资源服务暂不可用 503；这些接口不接受写入。

表项的读取与修改都使用权限指令（decision_22227d0ad075）：个人 Package 读取 `GetEffectivePermissions`；Project 与 Lineage 的 Package 用 `ListPermissionEntries` 读取，用 `CreatePermissionEntry`、`DeletePermissionEntry` 写入和删除表项。
