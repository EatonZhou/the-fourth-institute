---
kb_id: decision_22227d0ad075
type: decision
project: AI员工
created_at: '2026-09-28'
version:
  number: 70
  created_at: "2026-09-28T10:45:00Z"
  created_by:
    user: "weihao"
    agent: "resident_2a1fc6cd272d"
  updated_at: "2026-11-26T03:02:04Z"
  updated_by:
    user: ""
    agent: "resident_83d75ad3a6c7"
importance: 8
metaness: 2
ai_generated: false
links:
  source:
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/hatg/catalog/permission.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/permission_system/schemes.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/permission_system/authdiff.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/permission_system/management.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/permission_system/temporary.py
  - /opt/tfi/the-fourth-institute-dev/code/management_portal/resources/isa.py
  related:
  - decision_b820faf42218
  - decision_600ae27b0538
  - decision_ea13b85216d0
  - decision_898b8911a2ff
  - decision_3fe3cca8fd95
  - decision_fb910c43ab07
  - decision_34158fdd4078
  - decision_5bc3fdfc7eec
  - decision_6eac174d85bf
  supersedes: []
  derived_from:
  - decision_898b8911a2ff
  - thinking_ca8f397a47de
---

# HATG ISA：权限指令

权限指令是 HATG ISA 中操作权限系统（decision_b820faf42218）本身的一组指令：读取开关和启用的权限方案，管理方案，读取和搜索方案实例中的树，增删改实例中的表项，查看有效权限和试判定，签发临时凭证。它们都由 ResourceService 处理。每条指令按什么判定写在它声明的判定槽里，每个判定槽要求什么写在启用方案的指令分类里，由 PAR 引擎判定（decision_b820faf42218 §3.2；默认方案见 decision_600ae27b0538）；少数取决于参数本身的检查（表项写在哪个资源上、查看谁的权限）也问引擎：代码询问调用者能否通过保护该动作的那条指令，不写死任何等级或管理员身份。

## 对象与原则

- 指令操作的对象与架构一一对应：开关与启用方案、方案（规则与随方案保存的实例）、实例中的树和表项、临时凭证、查询。
- 权限树上的位置由方案规则和领域事实派生（Resident 所在的 Lineage、资源所属的 Project 等），没有直接改树的指令。要改变位置，移动领域对象；要改变分类结构，修改方案规则。
- 实例的表就是表：表项只有 `entry_id`、效果、`p`、`a`、`r`、`disabled` 和 `version`，临时凭证另有签发者、签发时间、过期时间和随任务签发时的 `message_id`。表项不记录来源，规则生成的行与人工写入的行一样可以修改、停用和删除。写入只检查 p、a、r 是方案树上的节点或有效主体，同一方案中不能有两条内容相同的长期表项；表项之间是否矛盾不检查，落在 deny 内的 permit 按 deny 优先照常判定。
- 修改方案规则会按现有对象重新生成该方案的实例，此前对实例的修改不保留；只改标题、说明或指令分类时不重新生成。启用方案时把它保存的实例补齐到现有对象，保留实例中已有的每一行。
- 表项和节点指令带可选的 `scheme_id`，省略时作用于启用的方案。
- 开关由 `SetPermissionSystemEnabled` 管理（decision_4527448ad07f）。开关关闭时，全部权限指令照常工作，任何已登录主体都可以调用，不检查调用者在实例上的权限（关闭即没有权限系统，decision_b820faf42218 §3.1）；“启用的方案”指开启时将使用的方案，ActivatePermissionScheme 只记录这个选择，开启时补齐。读指令读的是这份实例，签发的临时凭证写入这份实例，开启后生效。方案还没有保存的实例时，CreatePermissionEntry 返回 `instruction.conflict`，因为之后生成实例会丢掉这行。
- 不检查方案是否会让管理员失去修改权限。退路是 Portal 管理员保底（weihao 2026-10-19，decision_b820faf42218 §3.1）：不论启用哪套方案、开关开或关，Portal 管理员永远可以读取方案列表（ListPermissionSchemes）、启用方案（ActivatePermissionScheme），也永远可以开关权限系统（`SetPermissionSystemEnabled`），所以不关开关也能切换方案。这三件事管理员做时不问方案，非管理员按方案判定。
- User 分类（Portal 管理员、一般用户、实习用户）是账号的事实，用用户指令 UpdateUser 的 `changes.category` 修改（decision_6eac174d85bf），不在这里授予。

## 指令

判定对象（“对该主体”“对 r”“按已保存的表项”）由指令声明给出，所有方案相同；A 节点和固定的 R 节点（如“共享”）是默认方案的分类（decision_600ae27b0538），其他方案可以不同。

| 指令 | 改变状态 | 作用 | 默认方案要求 |
| --- | --- | --- | --- |
| `GetPermissionSystem` | 否 | 开关状态、启用方案及其版本（关闭时为开启时将使用的方案）、实例由哪版规则在何时生成、是否需要重新生成；`allows` 提供 Create/Update/Delete/ActivatePermissionScheme 的方案操作判定，并保留三条表项写指令的通用入口标记；它不替代对具体表项或目标 r 的授权。表项按钮应使用 ListPermissionEntries 返回的逐行或目标 r 的 `allows`（见该指令），服务端再次按实际对象判定；不返回过渡字段 `can_manage` | 免检 |
| `ListPermissionSchemes` | 否 | 内置方案（默认方案）与自定义方案，标出启用的方案和内置方案（`builtin`），报告每个方案实例中的表项数；复制而来的方案带来源方案 ID（`source_scheme_id`，没有来源时为空串）和来源是否已改变（`outdated`） | 免检 |
| `GetPermissionScheme` | 否 | 方案规则全文、版本与修改者、是否内置（`builtin`）；`unmapped` 列出规则没有分类的 A 元素；复制而来的方案带 `source`（见下文“来源记录”）；内置方案跟随发布写回过实例，或跟随只改指令分类、有元素换了分类的发布时，带顶层 `last_merge`，形状与 `source.last_merge` 相同，`source_scheme_id` 是方案自己（只改分类时实例不动，`auth_diff` 只报告移动元素）；可选同时返回词汇（对象类型、所属关系、主体选择器、资源类型、User 分类、全部指令及参数；每条指令带 `elements`，即它声明的判定槽，每项是一个 A 元素：`{element, argument, types?, optional?, via?, place?}` 或 `{element, fixed}`）。规则的 `instructions` 写 `{"<A 元素>": {"a": <A 树分类节点>, "r": <R 树分类节点，只用于固定槽>}}` | 免检 |
| `CreatePermissionScheme` | 是 | 创建方案并生成或复制实例，记录来源规则。 | 对“共享”有 @admin |
| `UpdatePermissionScheme` | 是 | 保存自定义规则；信封 expected_version 比较版本。来源合并保留实例编辑与临时凭证。 | 对“共享”有 @admin |
| `DeletePermissionScheme` | 是 | 删除自定义方案及其实例和来源记录；内置方案和启用的方案不能删除；调用信封给出 `expected_version` 时按方案版本比较 | 对“共享”有 @admin |
| `ActivatePermissionScheme` | 是 | 补齐并启用方案；权限关闭时只记录选择，管理员保底放行。 | 对“共享”有 @admin |
| `GetPermissionNode` | 否 | 方案实例中 P、A、R 树上的一个节点或元素：位置、祖先链、直接子节点（分页） | 免检 |
| `SearchPermissionNodes` | 否 | 在一棵树中按 ID 或标题查找节点：声明的分类节点与指令元素在前，实例中的元素在后 | 免检 |
| `ListPermissionEntries` | 否 | 按 p、a、r 筛选并分页列出表项（`cursor` + `limit`，结果带 `total`，还有下一页时带 `next_cursor`）；每个节点可按 `exact`（节点本身）、`ancestors`（节点及其祖先，即判定会读到的表项）或 `subtree`（节点及其下全部节点）匹配；还可按效果、停用、临时凭证筛选。`p` 与 `r` 至少给一个，没有“省略即列出自己”；列出整个实例用 `r=@portal`、`r_scope=subtree`（weihao 2026-10-18）。每条表项带 `allows`（UpdatePermissionEntry、DeletePermissionEntry），按它自己的 r 报告调用者能否修改、删除它；给出 r 时结果另带 `allows`（CreatePermissionEntry、DeletePermissionEntry），报告能否在这个 r 上写入、删除表项 | 给出 p 时对 p 所指的主体或节点有 @admin，给出 r 时对 r 有 @viewer |
| `CreatePermissionEntry` | 是 | 在实例中写入一条表项 (p, a, r, permit/deny)，可直接写为停用 | 对 r 有 @admin |
| `UpdatePermissionEntry` | 是 | 修改一条表项的效果、p、a、r 或停用标记；调用信封给出 `expected_version` 时按表项版本比较，版本已变化返回 `instruction.version_conflict` | 入口按已保存的表项判定（声明 `via="permission_entry.r"`）：调用者在表项已保存的 r 上要有 @admin，改 r 时在新的 r 上也要有；表项不存在返回 `instruction.not_found` |
| `DeletePermissionEntry` | 是 | 按各表项原资源授权，同事务批量删除；任何一条拒权或不存在时不删除。 | 入口按每条已保存的表项判定（声明 `via="permission_entry.r"`）：调用者在每条表项已保存的 r 上要有 @admin，任何一条不通过或不存在时整批不删除；`entry_id` 与 `entry_ids` 恰好给一个 |
| `GetEffectivePermissions` | 否 | 读取一个主体在 A 树各分类节点上被授权的 R 节点及其中的洞、它的全部有效表项和有效的临时凭证；持久凭证展示读取这里。 | 对该主体有 @admin |
| `CheckPermission` | 否 | 检查主体对资源的 A 节点权限。 | 对该主体有 @admin |
| `AcceptUserAuthorization` | 是 | Resident 理解当前执行中真实 User 的自然语言授权后，提交源 message_id、原文 evidence 与准确 credentials；Society 核验来源与执行关系，权限 owner 检查 User 持有并原子签发、审计。凭证只给调用 Resident，默认随当前任务，明确时长可给 expires_in_seconds；重试不重签，附件不阻止授权 | 免检；处理器证明当前 User 来源及执行归属，引擎核验签发者持有 |
| `CreateTemporaryCredential` | 是 | 交出本人当前持有的指定 A 节点。给出 `message_id` 时，凭证绑定本人尚未受理的准备输入，待受理才启用；省略时按时长立即生效（默认一天）。最长七天。 | 门户根固定槽为 @free。任务型由处理器核对准备记录与实际签发者，权限 owner 核验签发者当前持有待授出的权限 |
| `UpdateTemporaryCredential` | 是 | 签发者在按时长签发的凭证到期前续期：新的过期时间为此刻加 `expires_in_seconds`，不晚于签发后 7 天；随任务签发的凭证不能续期 | 免检；只有签发者 |
| `RevokeTemporaryCredential` | 是 | 撤销一张临时凭证：签发者撤回，接收者做完事后交还 | 免检；处理器只允许签发者或接收者（固定角色，开关关闭时同样生效） |
| `GetPermissionSchemeCreatePreview` | 否 | 只读编译拟创建的方案，返回 valid、errors 与 unmapped。可读取来源规则；不初始化存储、不执行迁移、不生成或复制实例。 | 对“共享”有 @admin |
| `GetPermissionSchemeUpdatePreview` | 否 | 只读校验规则并计算拟保存的实例差异及授权差。diff=false 仅编译；source_digest 比较来源摘要。不会保存规则、更新来源、补齐实例或写入后回滚。 | 对“共享”有 @admin |
| `GetPermissionSchemeActivationPreview` | 否 | 只读计算启用方案后的实例与授权变化。读取已存规则、实例及当前对象；不切换方案、不初始化或迁移存储、不补齐实例。 | 对“共享”有 @admin |
| `CheckInstructionPermission` | 否 | 检查主体的一次指令调用，保留全部目标与管理员保底判断。 | 对 `principal_id` 有 @admin |
| `SetPermissionSystemEnabled` | 是 | 切换权限系统；Portal 管理员保底放行，开启前按原 owner 生命周期补齐实例。 | 对“Portal”有 @admin |

`AcceptUserAuthorization.message_id` 可以是当前执行已送达的正式 Family 转交副本、中间副本或原始 User 消息 ID。Society 逐跳校验持久化 business_ref 与 family_relays 的源消息、目标 Resident、目标消息一致，抵达原始消息后核验真实 User 来源和直接正文 evidence。回执以原始消息 ID 和正文摘要去重，仍绑定接收者当前执行；换用副本 ID 重试不会重复签发，撤销或过期后不能换 ID 重签。普通转发、引用、附件自述及没有完整正式转交记录的消息不能授权。

表中“免检”是默认方案的显式分类（放在“免检”节点下，固定槽对 Portal 节点判定）；默认方案给全部 A 元素分类，没有分类的 A 元素只有 Portal 管理员能调用（见 decision_600ae27b0538）。写表项的要求按表项所在的方案区分：启用方案实例中的行按上表；其他方案实例中的行，调用者要能通过 UpdatePermissionScheme。查看主体的指令（GetEffectivePermissions、CheckPermission、CheckInstructionPermission）都要求写明 `principal_id`，查看自己也写自己的 ID，不再有“省略即自己”（weihao 2026-10-18）；它们把 `principal_id` 当作 R 树上的这个 User 或 Resident 判定，处理器不再另问其他指令：默认方案中账号本人对自己的账号有 @admin，Project 中的 Resident 对本 Project 有 @admin，个人 AMA 中的 Resident 与所有者相同，所以查看自己总能通过。默认方案把每个 User 按分类放在 P 树的 Portal 管理员、一般用户或实习用户节点下，并把“共享”上的全部动作交给 Portal 管理员节点，所以方案管理等指令默认只有 Portal 管理员满足。

表中只写默认方案的要求。ListPermissionSchemes 和 ActivatePermissionScheme 另有方案之外的 Portal 管理员保底（decision_b820faf42218 §3.1）：Portal 管理员调用时永远通过，不问启用的是哪套方案，与 `SetPermissionSystemEnabled` 时相同（见上文）。

## 判定和预览

`CheckInstructionPermission` 检查主体能否以给定参数执行一条指令。`CheckPermission` 检查主体对特定资源拥有的 A 节点权限。两条指令都只读。

`GetPermissionSchemeActivationPreview`、`GetPermissionSchemeCreatePreview`、`GetPermissionSchemeUpdatePreview` 分别核对启用、创建与修改方案的请求。预览只计算结果，正式提交重新校验目标和权限。

## 任务临时凭证

以实际 User 或 Resident 身份依次调用 `PrepareResidentInput`、`CreateTemporaryCredential`、原参数 `DeliverMessage`。 每一项 `a` 和 `resource_id` 单独签发，`principal_id`、`message_id` 使用准备记录绑定的接收方和输入。门面权限回答使用准备回执中的最终提问成员和回答 ID（decision_1e2eb869bc10）。任务型签发省略 `expires_in_seconds`。

权限 owner 校验真实签发者、接收者和准备状态，按当前方案检查签发者实际可授出的权限。签发成功先保存禁用 permit 和可追踪回执。待受理的 `pending` 凭证不能用于访问；同事务受理成功后才启用。Family 结构化审批还要复核持久问题批准的精确集合与当前授出能力，全部匹配才整批启用。受理拒绝不留下本次激活；取消准备或输入完成、失败、取消时在原事务回收。该流程保留 User 经当前执行中的真实授权消息调用 `AcceptUserAuthorization` 的既有来源核验。

同一签发者、接收方、消息、A 节点和资源的重试复用原有效凭证和截止时间。已受理的准备不得新签，凭证过期、回收或方案不再匹配时不得借重试恢复。

## 临时凭证回执

AcceptUserAuthorization 的 credentials、CreateTemporaryCredential、UpdateTemporaryCredential，以及 GetEffectivePermissions 返回的 temporary 使用同一生命周期说明：

- `state`（`CreateTemporaryCredential`）：`pending` 表示待正式受理且不授予权限；`active` 表示已启用。
- `lifetime=task`：凭证绑定 `message_id` 的输入；该输入完成、失败或取消即失效。`expires_at=null` 表示结束时刻未知，不能解释为永不过期。默认接受用户授权属于此类，后台脚本不延长其生命周期。
- `lifetime=duration`：按明确时长签发，`expires_at` 是计划到期的 Unix 秒。AcceptUserAuthorization 仅在用户明确授权时长时接受 expires_in_seconds；直接 CreateTemporaryCredential 未绑定消息且省略时长时仍默认一天。
- `deadline_at`：两种凭证的最迟截止 Unix 秒。任务型凭证可在此前结束；按时长凭证的 deadline_at 与 expires_at 相等。续期不超过最初签发后七天，任务型凭证不能续期。
- 撤回、交还或当前权限变化仍可使调用提前拒绝；回执不保证后续调用一定获权。任务结束自动删除、已撤回或到期后已清理的凭证再次调用 RevokeTemporaryCredential 返回 `instruction.not_found`。同一条已失效授权消息不能重签。

这些字段说明签发当时的生命周期，不推测任务结束时间，也不改变持久表项的 expires_at 截止判定。

## 方案预览的数据读取

`GetPermissionSchemeActivationPreview` 和 `GetPermissionSchemeUpdatePreview(diff=true)` 读取当前领域事实的预览只走各 owner 的独立只读快照，直接读取既有数据，不初始化或迁移存储、不刷新索引或写缓存。缺失必要来源或读取失败即报错。

## 来源记录

内置方案的规则跟随代码发布更新，只读；方案可以直接启用，实例照常编辑（decision_b820faf42218 §5.11）。要改规则，先复制。复制品记录来源，编辑器据此提示“来源方案已更新”，并做三方对比：复制（或上次合并）时的来源规则、来源现在的规则、复制品自己的规则。

- CreatePermissionScheme 带 `source_scheme_id` 复制时，记录来源方案 ID、来源当时的规则全文和摘要。按 `rules` 新建的方案没有来源记录。
- 摘要是规则文档去掉 `scheme_id` 和 `title` 后的 SHA-256 前 16 位十六进制，覆盖说明、树、表规则和指令分类。来源只改标题时不算改变。
- GetPermissionScheme 的 `source` 为 `{scheme_id, rules, digest, recorded_at, current_digest, outdated}`：`rules` 与 `digest` 是记录时的来源规则，`recorded_at` 是记录时间（Unix 秒），`current_digest` 是来源现在的摘要（来源已删除时为 null），`outdated` 在两个摘要都存在且不同时为 true。ListPermissionSchemes 的每项带 `source_scheme_id` 与 `outdated`。
- 来源是内置方案时，发布改变内置规则后 ResourceService 启动时自动三方合并：复制品没改过的项换成来源的新版本，两边都改了的项保留复制品的版本并记为冲突，来源记录推进到来源现在的规则；树或表规则变化时重新生成实例并写回实例中的编辑（decision_b820faf42218 §5.11）。合并过的复制品，`source` 另有 `last_merge`（最近一次合并，自动或带 `source_digest` 保存）：`{source_scheme_id, from_digest, to_digest, merged_at, merged_by, conflicts, unrestored, deleted_again}`。`merged_by` 自动合并为 `portal`，手动合并为保存者；`conflicts` 每项 `{key, was, now, own}`，`key` 与编辑器的对比单位相同（`field|…`、`tree|…`、`node|…|…`、`entry|…`、`instruction|…`），手动合并为空；`unrestored` 是节点已不存在、没有写回的编辑和撤销的临时凭证，每项带 `missing`（缺失节点所在的树）；`deleted_again` 是新规则仍给出、因为人删过而保持删除的行，每项 `{effect, p, a, r}`；`auth_diff` 是这次合并的授权差（见“授权差”），报告上线前的合并为 null；`regenerated` 表示实例是否按新规则重新生成，只有指令分类变化时为 false（实例不动，`auth_diff` 只报告移动的元素）。
- 编辑器里的合并（来源是内置方案时也可以手动合并）通过 UpdatePermissionScheme 保存，另带 `source_digest`，取合并所依据的来源规则摘要，也就是读到的 `current_digest`。它必须等于来源现在的摘要，否则返回 `instruction.conflict`，提示重新读取来源；相等时在同一事务中把来源记录换成来源现在的规则。树或表规则变化时与自动合并走同一套写回人的编辑，更新预览报告将写不回的表项和保持删除的行。不带 `source_digest` 的保存不改变来源记录，规则变化时照常整套重新生成实例。
- 实例中的表项不参与合并：合并只处理规则，实例按规则是否变化决定是否重新生成。
- 删除复制品时一并删除它的来源记录；删除来源方案不影响复制品，复制品的 `current_digest` 变为 null。

## 授权差

UpdatePermissionScheme 和 ActivatePermissionScheme 的结果带 `auth_diff`，报告这次变化在现有对象上让主体得到和失去了什么（decision_b820faf42218 §5.12）。对应预览和正式结果都有；修改规则时还要读取全部现有对象，所以 `diff: false` 的编辑中检查不带这一项。启动时的自动合并把同样的报告写进 `last_merge.auth_diff`。报告只给人看，不拦截操作。

- 比较的两侧：修改规则时，是方案现在保存的实例和新实例（按新规则生成，带 `source_digest` 时再写回人的编辑）；启用时，是启用中方案现在的实例和目标方案补齐后的实例。只改标题或说明时报告为中性。改指令分类就是移动 A 元素：实例不变时也按现有实例比较移动的元素，报告它们带来的得失。
- `kind`：整份报告的类别，`neutral`（中性）、`tightening`（收紧，只失去）、`loosening`（放宽，只得到）或 `mixed`（混合）。
- `complete`：计算是否在 2 秒的上限内做完。为 false 时，计数是下限，`groups` 只含已经算出的组。
- `gained`、`lost`：各为 `{triples, principals, resources}`。`triples` 是得到或失去的 (主体, A 节点, 资源) 三元组数，A 节点是分类节点或移动的元素；`principals` 和 `resources` 是涉及的主体数与资源数（固定槽要求的 R 节点不算资源）。
- `groups`：`{total, items}`，`items` 最多 `diff_limit` 组（默认 50，最多 500），实例变化的组和移动元素的组一起排序，影响大的在前。每组的内容：
  - `kind`：这一组的类别。
  - `p`、`r`：主体和资源，各为 `{node, title?, kinds?, except?, except_total?}`。`node` 是新树里容纳它们的最低节点，`kinds` 表示只计这些对象类型，`except` 列出除去的节点（最多 10 个），`except_total` 是除去节点的总数。固定槽元素的组，`r` 是它要求的 R 节点。
  - `before`、`after`：变化前后允许的最高 A 节点，两侧都有的不列，例如 `["@admin"]` → `["@contributor"]`。
  - `gained`、`lost`：得到和失去的 A 节点。实例变化的组 A 只取分类节点，某条表项直接点名、没有移动的指令元素也算一项。
  - `principals`、`resources`：这一组的主体数和资源数，固定槽元素的组资源数为 1。
  - 移动元素的组另有 `element`、`instruction` 和分类的变化 `a: {before, after}`，固定槽还有 R 节点的变化 `resource: {before, after}`。这种组只涉及一个元素，`before`/`after`/`gained`/`lost` 中出现的就是这个元素，例如放宽时 `before: []`、`after: ["DeleteLineage"]`。参数槽只计声明类型的对象。
- `remapped`：`{total, items}`，列出要求改变了的指令，每项 `{instruction, before, after}`，两侧各为这条指令每个 A 元素的判定槽与分类（`{element, a, argument?…}` 或 `{element, a, resource}`），指令不存在时为空列表。

## 错误

- 方案规则不能编译：`instruction.invalid_arguments`，`details.errors` 逐条列出 `path`（规则中的 JSON 路径）与 `message`；预览时同样的错误放在结果的 `errors` 中。
- 方案或表项的 `expected_version` 不一致：`instruction.version_conflict`，`details.current_version` 为当前版本。内置方案规则的修改和内置方案的删除，启用方案的删除、内容相同的表项、方案还没有实例时写入表项、用表项指令删除临时凭证，`source_digest` 与来源方案现在的摘要不同，也返回 `instruction.conflict`。
- 方案没有来源记录却带 `source_digest`：`instruction.invalid_arguments`。
- p、a、r 不是方案树上的节点：参数错误，消息指出哪棵树。
- 在不能写表项的资源上写入、修改或删除表项，列出他人的有效权限或临时凭证：`instruction.forbidden`。

## 参数名契约

下表列出 arguments 内的顶层参数名；必填条件、类型和枚举以 GetInstructionSchema 为准。调用信封中的 expected_version、idempotency_key 与 wait_seconds 不列入 arguments。

| 指令名 | arguments 参数名 |
| --- | --- |
| `GetPermissionSystem` | 无 |
| `ListPermissionSchemes` | 无 |
| `GetPermissionScheme` | `scheme_id`、`vocabulary` |
| `CreatePermissionScheme` | `scheme_id`、`source_scheme_id`、`rules`、`title`、`description` |
| `UpdatePermissionScheme` | `scheme_id`、`rules`、`diff_limit`、`source_digest` |
| `DeletePermissionScheme` | `scheme_id` |
| `ActivatePermissionScheme` | `scheme_id`、`diff_limit` |
| `GetPermissionNode` | `scheme_id`、`tree`、`node_id`、`limit`、`cursor` |
| `SearchPermissionNodes` | `scheme_id`、`tree`、`query`、`limit` |
| `ListPermissionEntries` | `scheme_id`、`p`、`a`、`r`、`p_scope`、`a_scope`、`r_scope`、`effect`、`disabled`、`temporary`、`cursor`、`limit` |
| `CreatePermissionEntry` | `scheme_id`、`p`、`a`、`r`、`effect`、`disabled` |
| `UpdatePermissionEntry` | `scheme_id`、`entry_id`、`p`、`a`、`r`、`effect`、`disabled` |
| `DeletePermissionEntry` | `scheme_id`、`entry_ids` |
| `GetEffectivePermissions` | `principal_id` |
| `CheckPermission` | `principal_id`、`a`、`resource_id` |
| `AcceptUserAuthorization` | `message_id`、`evidence`、`credentials`、`expires_in_seconds` |
| `CreateTemporaryCredential` | `principal_id`、`a`、`resource_id`、`message_id`、`expires_in_seconds` |
| `UpdateTemporaryCredential` | `credential_id`、`expires_in_seconds` |
| `RevokeTemporaryCredential` | `credential_id` |
| `GetPermissionSchemeCreatePreview` | `scheme_id`、`source_scheme_id`、`rules`、`title`、`description` |
| `GetPermissionSchemeUpdatePreview` | `scheme_id`、`rules`、`diff`、`diff_limit`、`source_digest` |
| `GetPermissionSchemeActivationPreview` | `scheme_id`、`diff_limit` |
| `CheckInstructionPermission` | `principal_id`、`instruction`、`arguments` |
| `SetPermissionSystemEnabled` | `enabled` |

`a` 是 A 树节点：`@` 开头的分类节点（默认方案有 @admin、@contributor、@viewer、@message 等）或指令元素；`p` 是主体 ID、Project 或 Lineage ID（表示其中的 Resident）或 `@` 开头的分类节点；`r` 是资源 ID 或 `@` 开头的分类节点。
