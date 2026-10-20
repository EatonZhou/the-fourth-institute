---
kb_id: decision_22227d0ad075
type: decision
project: AI员工
created_at: '2026-09-28'
version:
  number: 64
  created_at: '2026-09-28T10:45:00Z'
  created_by:
    user: tfi-exported-user
    agent: resident_2a1fc6cd272d
  updated_at: '2026-10-19T08:32:04Z'
  updated_by:
    user: ''
    agent: resident_d43295e5e043
importance: 8
metaness: 2
ai_generated: false
links:
  source:
  - ${code_root}/management_portal/hatg/catalog/permission.py
  - ${code_root}/management_portal/permission_system/schemes.py
  - ${code_root}/management_portal/permission_system/authdiff.py
  - ${code_root}/management_portal/permission_system/management.py
  - ${code_root}/management_portal/permission_system/temporary.py
  - ${code_root}/management_portal/resources/isa.py
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
- 开关由 UpdateExecutionSettings 的 `permission_system_enabled` 控制（decision_4527448ad07f），不另设指令。开关关闭时，全部权限指令照常工作，任何已登录主体都可以调用，不检查调用者在实例上的权限（关闭即没有权限系统，decision_b820faf42218 §3.1）；“启用的方案”指开启时将使用的方案，ActivatePermissionScheme 只记录这个选择，开启时补齐。读指令读的是这份实例，签发的临时凭证写入这份实例，开启后生效。方案还没有保存的实例时，CreatePermissionEntry 返回 `instruction.conflict`，因为之后生成实例会丢掉这行。
- 不检查方案是否会让管理员失去修改权限。退路是 Portal 管理员保底（TFI portal 2026-10-19，decision_b820faf42218 §3.1）：不论启用哪套方案、开关开或关，Portal 管理员永远可以读取方案列表（ListPermissionSchemes）、启用方案（ActivatePermissionScheme），也永远可以开关权限系统（UpdateExecutionSettings 只改 `permission_system_enabled`），所以不关开关也能切换方案。这三件事管理员做时不问方案，非管理员按方案判定。
- User 分类（Portal 管理员、一般用户、实习用户）是账号的事实，用用户指令 UpdateUser 的 `changes.category` 修改（decision_6eac174d85bf），不在这里授予。

## 指令

判定对象（“对该主体”“对 r”“按已保存的表项”）由指令声明给出，所有方案相同；A 节点和固定的 R 节点（如“共享”）是默认方案的分类（decision_600ae27b0538），其他方案可以不同。

| 指令 | 改变状态 | 作用 | 默认方案要求 |
| --- | --- | --- | --- |
| `GetPermissionSystem` | 否 | 开关状态、启用方案及其版本（关闭时为开启时将使用的方案）、实例由哪版规则在何时生成、是否需要重新生成；`allows` 提供 Create/Update/Delete/ActivatePermissionScheme 的方案操作判定，并保留三条表项写指令的通用入口标记；它不替代对具体表项或目标 r 的授权。表项按钮应使用 ListPermissionEntries 返回的逐行或目标 r 的 `allows`（见该指令），服务端再次按实际对象判定；不返回过渡字段 `can_manage` | 免检 |
| `ListPermissionSchemes` | 否 | 内置方案（默认方案）与自定义方案，标出启用的方案和内置方案（`builtin`），报告每个方案实例中的表项数；复制而来的方案带来源方案 ID（`source_scheme_id`，没有来源时为空串）和来源是否已改变（`outdated`） | 免检 |
| `GetPermissionScheme` | 否 | 方案规则全文、版本与修改者、是否内置（`builtin`）；`unmapped` 列出规则没有分类的 A 元素；复制而来的方案带 `source`（见下文“来源记录”）；内置方案跟随发布写回过实例，或跟随只改指令分类、有元素换了分类的发布时，带顶层 `last_merge`，形状与 `source.last_merge` 相同，`source_scheme_id` 是方案自己（只改分类时实例不动，`auth_diff` 只报告移动元素）；可选同时返回词汇（对象类型、所属关系、主体选择器、资源类型、User 分类、全部指令及参数；每条指令带 `elements`，即它声明的判定槽，每项是一个 A 元素：`{element, argument, types?, optional?, via?, place?}` 或 `{element, fixed}`）。规则的 `instructions` 写 `{"<A 元素>": {"a": <A 树分类节点>, "r": <R 树分类节点，只用于固定槽>}}` | 免检 |
| `CreatePermissionScheme` | 是 | 新建自定义方案：复制另一个方案的规则和实例（`source_scheme_id`，标题缺省为“原标题 副本”，同时记录来源），或按给出的规则从现有对象生成实例（`rules`，没有来源）；`dry_run` 只编译，报告错误和未归类的 A 元素（编译失败时也给出） | 对“共享”有 @admin |
| `UpdatePermissionScheme` | 是 | 以整份规则替换自定义方案；调用信封给出 `expected_version` 时按方案版本比较；树或表规则变化时重新生成实例（`regenerated` 报告节点数和表项数，未重新生成时为 null）；合并来源的改变时带 `source_digest`，保存后来源记录推进到来源现在的规则，结果回带 `source_digest`，重新生成时与自动合并同样写回人的编辑，结果和 dry_run 带 `unrestored`、`deleted_again`；`dry_run` 报告编译结果、是否重新生成（`regenerates`）和差异（`diff`：新增、删除的表项与将丢失的修改；带 `source_digest` 时将丢失的修改只有写不回的表项）；结果和 dry_run 都带授权差 `auth_diff`（见“授权差”） | 对“共享”有 @admin |
| `DeletePermissionScheme` | 是 | 删除自定义方案及其实例和来源记录；内置方案和启用的方案不能删除；调用信封给出 `expected_version` 时按方案版本比较 | 对“共享”有 @admin |
| `ActivatePermissionScheme` | 是 | 补齐方案保存的实例后切换（实例缺失或由其他规则生成时重新生成；内置方案的实例由发布前的规则生成时，按新规则重新生成并写回实例中的编辑，与启动时跟随发布相同，记为 `last_merge`），报告新增、移动、删除的节点与表项数，写回时另报 `restored_entries`、写不回的 `unrestored` 和保持删除的 `deleted_again`；内置方案和自定义方案都可以启用；开关关闭时只记录开启时使用这个方案；`dry_run` 只报告；结果带这个方案的 `unmapped` 清单，以及启用中方案现在的实例对比这个方案补齐后实例的授权差 `auth_diff`（`diff_limit` 限制列出的组数） | 对“共享”有 @admin |
| `GetPermissionNode` | 否 | 方案实例中 P、A、R 树上的一个节点或元素：位置、祖先链、直接子节点（分页） | 免检 |
| `SearchPermissionNodes` | 否 | 在一棵树中按 ID 或标题查找节点：声明的分类节点与指令元素在前，实例中的元素在后 | 免检 |
| `ListPermissionEntries` | 否 | 按 p、a、r 筛选并分页列出表项（`cursor` + `limit`，结果带 `total`，还有下一页时带 `next_cursor`）；每个节点可按 `exact`（节点本身）、`ancestors`（节点及其祖先，即判定会读到的表项）或 `subtree`（节点及其下全部节点）匹配；还可按效果、停用、临时凭证筛选。`p` 与 `r` 至少给一个，没有“省略即列出自己”；列出整个实例用 `r=@portal`、`r_scope=subtree`（TFI portal 2026-10-18）。每条表项带 `allows`（UpdatePermissionEntry、DeletePermissionEntry），按它自己的 r 报告调用者能否修改、删除它；给出 r 时结果另带 `allows`（CreatePermissionEntry、DeletePermissionEntry），报告能否在这个 r 上写入、删除表项 | 给出 p 时对 p 所指的主体或节点有 @admin，给出 r 时对 r 有 @viewer |
| `CreatePermissionEntry` | 是 | 在实例中写入一条表项 (p, a, r, permit/deny)，可直接写为停用 | 对 r 有 @admin |
| `UpdatePermissionEntry` | 是 | 修改一条表项的效果、p、a、r 或停用标记；调用信封给出 `expected_version` 时按表项版本比较，版本已变化返回 `instruction.version_conflict` | 入口按已保存的表项判定（声明 `via="permission_entry.r"`）：调用者在表项已保存的 r 上要有 @admin，改 r 时在新的 r 上也要有；表项不存在返回 `instruction.not_found` |
| `DeletePermissionEntry` | 是 | 删除一条或多条表项（同一事务）；临时凭证用 RevokeTemporaryCredential | 入口按每条已保存的表项判定（声明 `via="permission_entry.r"`）：调用者在每条表项已保存的 r 上要有 @admin，任何一条不通过或不存在时整批不删除；`entry_id` 与 `entry_ids` 恰好给一个 |
| `GetEffectivePermissions` | 否 | 主体在各 A 分类节点上被授权的 R 节点与洞（`coverage`）、全部表项、有效的临时凭证，User 另有 `user_category`；Package 的奖章读取这里；`principal_id` 必填 | 对该主体有 @admin |
| `CheckPermission` | 否 | 判定一个主体能否执行一条指令，或在一个资源上是否持有某个 A 节点；返回每个请求的结果、原因（permitted、no_permit、denied）与命中的表项；Portal 管理员保底放行的调用返回 `portal_admin: true`、`requests` 为空；`principal_id` 必填 | 对该主体有 @admin |
| `IssueTemporaryCredential` | 是 | 把自己在一个资源上持有的一个 A 节点临时交给另一个主体：只核对签发者此刻持有，随即写入一行 (接收者, A 节点, 资源, permit)，带签发者和过期时间。给出 `message_id`（接收 Resident 正在处理的、发给它的消息）时凭证随任务：这条消息的输入进入完成、失败或取消时在同一事务中删除；否则按 `expires_in_seconds` 到期（默认一天）。两种都最迟在签发 7 天后失效。DeliverMessage 的 `temporary_credentials` 在投递时以同样方式签发（decision_2fe6fd5367fb） | 免检；由引擎核对签发者持有 |
| `RenewTemporaryCredential` | 是 | 签发者在按时长签发的凭证到期前续期：新的过期时间为此刻加 `expires_in_seconds`，不晚于签发后 7 天；随任务签发的凭证不能续期 | 免检；只有签发者 |
| `RevokeTemporaryCredential` | 是 | 撤销一张临时凭证：签发者撤回，接收者做完事后交还 | 免检；处理器只允许签发者或接收者（固定角色，开关关闭时同样生效） |
| `ListTemporaryCredentials` | 否 | 一个主体签发的和收到的有效临时凭证；`principal_id` 必填 | 对该主体有 @admin |

表中“免检”是默认方案的显式分类（放在“免检”节点下，固定槽对 Portal 节点判定）；默认方案给全部 A 元素分类，没有分类的 A 元素只有 Portal 管理员能调用（见 decision_600ae27b0538）。写表项的要求按表项所在的方案区分：启用方案实例中的行按上表；其他方案实例中的行，调用者要能通过 UpdatePermissionScheme。查看主体的指令（GetEffectivePermissions、CheckPermission、ListTemporaryCredentials）都要求写明 `principal_id`，查看自己也写自己的 ID，不再有“省略即自己”（TFI portal 2026-10-18）；它们把 `principal_id` 当作 R 树上的这个 User 或 Resident 判定，处理器不再另问其他指令：默认方案中账号本人对自己的账号有 @admin，Project 中的 Resident 对本 Project 有 @admin，个人 AMA 中的 Resident 与所有者相同，所以查看自己总能通过。默认方案把每个 User 按分类放在 P 树的 Portal 管理员、一般用户或实习用户节点下，并把“共享”上的全部动作交给 Portal 管理员节点，所以方案管理等指令默认只有 Portal 管理员满足。

表中只写默认方案的要求。ListPermissionSchemes 和 ActivatePermissionScheme 另有方案之外的 Portal 管理员保底（decision_b820faf42218 §3.1）：Portal 管理员调用时永远通过，不问启用的是哪套方案，与 UpdateExecutionSettings 只改 `permission_system_enabled` 时相同（见上文）。

## 来源记录

内置方案的规则跟随代码发布更新，只读；方案可以直接启用，实例照常编辑（decision_b820faf42218 §5.11）。要改规则，先复制。复制品记录来源，编辑器据此提示“来源方案已更新”，并做三方对比：复制（或上次合并）时的来源规则、来源现在的规则、复制品自己的规则。

- CreatePermissionScheme 带 `source_scheme_id` 复制时，记录来源方案 ID、来源当时的规则全文和摘要。按 `rules` 新建的方案没有来源记录。
- 摘要是规则文档去掉 `scheme_id` 和 `title` 后的 SHA-256 前 16 位十六进制，覆盖说明、树、表规则和指令分类。来源只改标题时不算改变。
- GetPermissionScheme 的 `source` 为 `{scheme_id, rules, digest, recorded_at, current_digest, outdated}`：`rules` 与 `digest` 是记录时的来源规则，`recorded_at` 是记录时间（Unix 秒），`current_digest` 是来源现在的摘要（来源已删除时为 null），`outdated` 在两个摘要都存在且不同时为 true。ListPermissionSchemes 的每项带 `source_scheme_id` 与 `outdated`。
- 来源是内置方案时，发布改变内置规则后 ResourceService 启动时自动三方合并：复制品没改过的项换成来源的新版本，两边都改了的项保留复制品的版本并记为冲突，来源记录推进到来源现在的规则；树或表规则变化时重新生成实例并写回实例中的编辑（decision_b820faf42218 §5.11）。合并过的复制品，`source` 另有 `last_merge`（最近一次合并，自动或带 `source_digest` 保存）：`{source_scheme_id, from_digest, to_digest, merged_at, merged_by, conflicts, unrestored, deleted_again}`。`merged_by` 自动合并为 `portal`，手动合并为保存者；`conflicts` 每项 `{key, was, now, own}`，`key` 与编辑器的对比单位相同（`field|…`、`tree|…`、`node|…|…`、`entry|…`、`instruction|…`），手动合并为空；`unrestored` 是节点已不存在、没有写回的编辑和撤销的临时凭证，每项带 `missing`（缺失节点所在的树）；`deleted_again` 是新规则仍给出、因为人删过而保持删除的行，每项 `{effect, p, a, r}`；`auth_diff` 是这次合并的授权差（见“授权差”），报告上线前的合并为 null；`regenerated` 表示实例是否按新规则重新生成，只有指令分类变化时为 false（实例不动，`auth_diff` 只报告移动的元素）。
- 编辑器里的合并（来源是内置方案时也可以手动合并）通过 UpdatePermissionScheme 保存，另带 `source_digest`，取合并所依据的来源规则摘要，也就是读到的 `current_digest`。它必须等于来源现在的摘要，否则返回 `instruction.conflict`，提示重新读取来源；相等时在同一事务中把来源记录换成来源现在的规则。树或表规则变化时与自动合并走同一套写回人的编辑，dry_run 报告将写不回的表项和保持删除的行。不带 `source_digest` 的保存不改变来源记录，规则变化时照常整套重新生成实例。
- 实例中的表项不参与合并：合并只处理规则，实例按规则是否变化决定是否重新生成。
- 删除复制品时一并删除它的来源记录；删除来源方案不影响复制品，复制品的 `current_digest` 变为 null。

## 授权差

UpdatePermissionScheme 和 ActivatePermissionScheme 的结果带 `auth_diff`，报告这次变化在现有对象上让主体得到和失去了什么（decision_b820faf42218 §5.12）。dry_run 结果和正式结果都有；修改规则时还要读取全部现有对象，所以 `diff: false` 的编辑中检查不带这一项。启动时的自动合并把同样的报告写进 `last_merge.auth_diff`。报告只给人看，不拦截操作。

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

- 方案规则不能编译：`instruction.invalid_arguments`，`details.errors` 逐条列出 `path`（规则中的 JSON 路径）与 `message`；`dry_run` 时同样的错误放在结果的 `errors` 中。
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
| `CreatePermissionScheme` | `scheme_id`、`source_scheme_id`、`rules`、`title`、`description`、`dry_run` |
| `UpdatePermissionScheme` | `scheme_id`、`rules`、`dry_run`、`diff`、`diff_limit`、`source_digest` |
| `DeletePermissionScheme` | `scheme_id` |
| `ActivatePermissionScheme` | `scheme_id`、`dry_run`、`diff_limit` |
| `GetPermissionNode` | `scheme_id`、`tree`、`node_id`、`limit`、`cursor` |
| `SearchPermissionNodes` | `scheme_id`、`tree`、`query`、`limit` |
| `ListPermissionEntries` | `scheme_id`、`p`、`a`、`r`、`p_scope`、`a_scope`、`r_scope`、`effect`、`disabled`、`temporary`、`cursor`、`limit` |
| `CreatePermissionEntry` | `scheme_id`、`p`、`a`、`r`、`effect`、`disabled` |
| `UpdatePermissionEntry` | `scheme_id`、`entry_id`、`p`、`a`、`r`、`effect`、`disabled` |
| `DeletePermissionEntry` | `scheme_id`、`entry_id`、`entry_ids` |
| `GetEffectivePermissions` | `principal_id` |
| `CheckPermission` | `principal_id`、`instruction`、`arguments`、`a`、`resource_id` |
| `IssueTemporaryCredential` | `principal_id`、`a`、`resource_id`、`message_id`、`expires_in_seconds` |
| `RenewTemporaryCredential` | `credential_id`、`expires_in_seconds` |
| `RevokeTemporaryCredential` | `credential_id` |
| `ListTemporaryCredentials` | `principal_id` |

`a` 是 A 树节点：`@` 开头的分类节点（默认方案有 @admin、@contributor、@viewer、@message 等）或指令元素；`p` 是主体 ID、Project 或 Lineage ID（表示其中的 Resident）或 `@` 开头的分类节点；`r` 是资源 ID 或 `@` 开头的分类节点。
