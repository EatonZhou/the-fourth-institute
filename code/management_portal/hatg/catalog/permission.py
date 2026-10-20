"""Permission instructions (decision_22227d0ad075): the switch, schemes, their instances and rows, checks and temporary credentials.

A permission scheme is its rules plus the instance saved with it.  Who may
call each instruction is written in the active scheme's instruction map and
decided by the PAR engine (decision_b820faf42218).
"""
from __future__ import annotations

from ..declarations import CURSOR, FIXED, NEXT_CURSOR, InstructionDeclaration as D, On, P, R

_DRY_RUN = "true 时只检查并报告结果，不写入"
_TREE = "哪一棵树：P（主体）、A（动作）或 R（资源）"
_EFFECT = "permit（允许，默认）或 deny（拒绝）"
_DISABLED = "true 时这一行留在表中但不参与判定"

_RULES = ("方案规则文档（tfi.permission-scheme.v2）：scheme_id、title、description、trees"
          "（规则节点用 each 指定对象类型，under 指定放在哪类相关对象之下，otherwise 指定没有该关系时放在哪个分类节点）、entries"
          "（节点规则含 id、effect、p、a、r、title；关系规则把 p 换成 relation）、instructions（A 元素到分类：a 指定 A 树分类节点，固定槽另可用 r 指定 R 树分类节点；"
          "A 元素与它判定的对象由指令声明的判定槽给出，见词汇 instructions[].elements）")
_ERRORS = "编译错误，每项含 path（规则中的 JSON 路径）与 message"
_UNMAPPED = "方案没有分类的 A 元素（未归类）：它们落在 A 树默认节点上，固定槽落在 R 树默认节点上，默认方案中只有管理员能通过"
_SCHEME_ID = "方案 ID；省略时为启用中的方案（权限系统关闭时为开启时将使用的方案）"
_ENTRY = "表项：entry_id、effect、p、a、r、disabled、version；临时凭证另有 issued_by、issued_at、expires_at，随消息签发的另有 message_id"
_CREDENTIAL = ("临时凭证：credential_id、principal_id（接收者）、a、resource_id、issued_by、issued_at、expires_at；"
               "随消息签发的另有 message_id")
_PRINCIPAL = "要查看的主体；查看自己时也写明；按方案中本指令对 principal_id 的要求判定"
_DIFF = ("重新生成的差异：added、removed、lost_edits 各含 total 与 items（最多 diff_limit 项）；added/removed 每项含 effect、p、a、r、disabled，"
         "lost_edits 每项含 kind（added 人工新增、deleted 人工删除、disabled 人工停用）与 entry")
_AUTH_DIFF = ("授权差报告（decision_b820faf42218 §5.12），只报告、不拦截：在现有对象上比较变化前后的实例给出的授权；"
              "改指令分类是移动 A 元素，实例不变时也报告它带来的得失。"
              "kind 为 neutral（中性）、tightening（收紧）、loosening（放宽）或 mixed（混合）；complete 为 false 表示计算超过时间上限，"
              "计数是下限；gained、lost 各含 triples（三元组数，A 按分类节点或移动的元素计）、principals、resources（涉及的主体数与资源数）；"
              "groups 含 total 与 items（最多 diff_limit 组，影响大的在前），每组含 kind、p 与 r（node 及其 title，"
              "kinds 表示只算这些对象类型，except 为除去的节点、except_total 为其总数）、before 与 after（变化前后允许的最高 A 节点，"
              "两边都有的不列）、gained 与 lost（得到、失去的 A 节点）、principals、resources；移动元素的组只涉及一个元素，"
              "另含 element、instruction、a（before、after 分类节点），固定槽还有 resource（before、after R 节点，r 即这个 R 节点）；"
              "remapped 为要求改变的指令：total 与 items（instruction、before、after，各为要求列表）")
_PREPARED = ("regenerate（实例缺失或由其他规则生成时重新生成）、added_nodes、moved_nodes、removed_nodes、added_entries、removed_entries")
_SCOPE = "exact（只匹配该节点，默认）、subtree（该节点及其下所有节点与元素）或 ancestors（该节点及其祖先）"


def _d(name, decides, description, parameters=(), result=(), *, mutates=False, target="", any_of=(), one_of=()):
    return D(name=name, category="permission", owner="resource", description=description,
             parameters=tuple(parameters), result=tuple(result), mutates=mutates, target=target,
             any_of=any_of, one_of=one_of, decides=decides)


DECLARATIONS = (
    _d("GetPermissionSystem", FIXED, "读取权限系统的开关状态、启用中的权限方案及其实例；关闭时 scheme 是开启时将使用的方案。", (), (
        R("enabled", "boolean", "权限系统是否开启"), R("mode", description="par（开启，PAR 引擎判定）或 open（关闭）"),
        R("scheme", "object", "启用中的方案：scheme_id、title、description、version、builtin"),
        R("instance", "object", "实例由哪版规则（rules_digest）在何时（generated_at）生成；尚未生成时为空"),
        R("needs_regenerate", "boolean", "实例缺失或由其他规则生成；开启权限系统时会重新生成。发布改变内置规则后，启动时内置方案的实例"
          "按新规则重新生成并写回实例中的编辑（见 GetPermissionScheme 的 last_merge），不动自定义方案"),
        R("allows", "object", "编辑器各按钮对应的指令 → 调用者能否通过：Create/Update/Delete/ActivatePermissionScheme "
          "按启用方案判定，关闭时已登录主体都可以；Create/Update/DeletePermissionEntry 恒为 true，"
          "它们按表项的 r 判定，看 ListPermissionEntries 的 allows"),
    )),
    _d("ListPermissionSchemes", FIXED, "列出权限方案：内置方案（默认方案）与自定义方案，标出启用中的方案（关闭时为开启时将使用的方案）。", (), (
        R("schemes", "array", "每项含 scheme_id、title、description、builtin、version、active、entry_count、"
          "source_scheme_id（复制来源，没有时为空）、outdated（来源方案的规则自复制或上次合并后已改变），自定义方案另有 updated_by、updated_at"),
    )),
    _d("GetPermissionScheme", FIXED, "读取一个方案的规则。", (
        P("scheme_id", description=_SCHEME_ID),
        P("vocabulary", "boolean", "同时返回规则可用的词汇：对象类型（object_types）、放置关系（placements，under 的取值）、"
          "关系规则的关系（relations）、资源类型（resource_types）、User 分类（user_categories，P 树节点 user_category 的取值）、"
          "按记录判定可用的记录字段（records，via 的取值，may_be_unowned 标出可能不属于任何对象的字段）、"
          "按落点判定可用的关系对象类型（place_related，place 的键）与全部 ISA 指令及参数（instructions，"
          "每条带 elements：它声明的判定槽，每项是一个 A 元素）"),
    ), (R("scheme_id", description="读取的方案"), R("rules", "object", _RULES), R("builtin", "boolean", "是否内置方案"),
        R("version", "integer", "方案规则的版本"),
        R("active", "boolean", "是否启用中的方案（关闭时为开启时将使用的方案）"), R("unmapped", "array", _UNMAPPED),
        P("vocabulary", "object", "请求 vocabulary 时：object_types、placements、relations、resource_types、user_categories、"
          "records、place_related 与 instructions"),
        P("source", "object", "复制品的来源：scheme_id（来源方案）、rules（复制或上次合并时来源方案的规则全文）、digest（它们的摘要）、"
          "current_digest（来源方案现在的摘要，来源已删除时为 null）、outdated、recorded_at；摘要不含 scheme_id 与 title。"
          "来源是内置方案时，发布改变内置规则后 ResourceService 启动时自动三方合并；合并过的复制品另有 last_merge（最近一次合并，"
          "自动或带 source_digest 保存）：source_scheme_id、from_digest、to_digest、merged_at、merged_by（自动合并为 portal）、"
          "conflicts（两边都改了、保留本方案版本的项，每项 key、was、now、own；保存的合并由人解决，为空）、"
          "unrestored（重新生成实例后节点已不存在、没有写回的实例中的编辑和撤销的临时凭证，每项带 missing）、"
          "deleted_again（人删掉、新规则又会生成、因此仍删掉的行：effect、p、a、r）、"
          "auth_diff（这次合并的授权差报告，格式同 UpdatePermissionScheme 的 auth_diff；报告上线前的合并为 null）、"
          "regenerated（实例是否按新规则重新生成；只有指令分类变化时为 false，实例不动，auth_diff 只报告移动的元素）。"
          "没有记录来源的方案（内置方案、按规则新建的方案）没有这一项"),
        P("last_merge", "object", "内置方案：最近一次发布改变它的规则后，启动时把新规则用到它自己的实例上、写回实例中的编辑的记录；"
          "发布只改指令分类时实例不动，有元素换了分类也记一条，regenerated 为 false。"
          "字段同 source.last_merge，source_scheme_id 是方案自己，from_digest、to_digest 是发布前后规则的摘要，conflicts 为空。"
          "没有过这样的发布时没有这一项"))),
    _d("CreatePermissionScheme", FIXED, "新建自定义方案：复制另一个方案的规则和实例（source_scheme_id），或按给出的规则从 Portal 现有对象生成实例（rules）。"
       "复制时同时记录来源方案和它此刻的规则（GetPermissionScheme 的 source），供来源改变后合并。"
       "dry_run 只编译并报告错误与未归类的 A 元素。", (
        R("scheme_id", description="新方案 ID：小写字母开头，只含小写字母、数字和下划线"),
        P("source_scheme_id", description="复制来源方案；与 rules 二选一"), P("rules", "object", _RULES + "；与 source_scheme_id 二选一"),
        P("title", description="方案标题；省略时用规则里的 title"), P("description", description="方案说明；省略时用规则里的 description"),
        P("dry_run", "boolean", _DRY_RUN),
    ), (P("scheme_id", description="新建的方案；dry_run 时没有"), P("version", "integer", "新方案的规则版本，为 1"),
        P("nodes", "integer", "新实例的节点数"), P("entries", "integer", "新实例的表项数"),
        P("valid", "boolean", "dry_run 时：规则是否编译通过"),
        P("errors", "array", _ERRORS), P("unmapped", "array", _UNMAPPED)), mutates=True, target="scheme_id"),
    _d("UpdatePermissionScheme", FIXED, "以整份规则替换一个自定义方案。树或规则表项变化时按 Portal 现有对象重新生成该方案的实例，实例中的编辑随之丢弃；"
       "只改标题、说明或指令分类时保留实例（改指令分类仍有授权差）。dry_run 只编译并报告重新生成的差异和授权差（auth_diff，保存的结果里也有）；"
       "差异要读取全部现有对象（数秒），"
       "编辑中的检查用 diff: false 只编译。内置方案的规则不可修改，要改先复制。调用信封的 expected_version 给 GetPermissionScheme.version 时按它比较。"
       "合并来源方案的改动后保存时带 source_digest：它必须等于来源方案现在的摘要，保存时来源记录换成来源方案现在的规则；不带时来源记录不变。"
       "带 source_digest 的保存和自动合并同一套写回：实例重新生成时实例中增加和停用的行带原 ID 写回，人删掉的行仍删掉，临时凭证保留，"
       "节点已不存在的不写回；结果和 dry_run 都报告 unrestored 与 deleted_again（含义同 last_merge），diff.lost_edits 只列不写回的行；"
       "保存后记为 last_merge。", (
        R("scheme_id", description="要修改的自定义方案"), R("rules", "object", _RULES),
        P("dry_run", "boolean", _DRY_RUN),
        P("diff", "boolean", "dry_run 时是否计算重新生成的差异，默认 true；false 时只编译并报告 regenerates，不返回 diff"),
        P("diff_limit", "integer", "差异每类最多列出的项数，也是授权差最多列出的组数，默认 50", minimum=1, maximum=500),
        P("source_digest", description="这次合并针对的来源规则摘要（GetPermissionScheme.source.current_digest）；"
          "与来源现在的摘要不同时冲突"),
    ), (P("scheme_id", description="被修改的方案；dry_run 时没有"), P("version", "integer", "保存后的规则版本"),
        P("diff", "object", _DIFF), P("auth_diff", "object", _AUTH_DIFF),
        P("source_digest", description="带 source_digest 保存时：这次合并针对的来源规则摘要"),
        P("regenerated", "object", "重新生成的实例：nodes、entries；未重新生成时为 null"),
        P("regenerates", "boolean", "dry_run 时：保存是否会重新生成实例"),
        P("unrestored", "array", "带 source_digest 时：不能写回的实例中的编辑与临时凭证，每项带 missing"),
        P("deleted_again", "array", "带 source_digest 时：人删掉、因此没有加回的规则行"),
        P("valid", "boolean", "dry_run 时：规则是否编译通过"), P("errors", "array", _ERRORS), P("unmapped", "array", _UNMAPPED)),
        mutates=True, target="scheme_id"),
    _d("DeletePermissionScheme", FIXED, "删除一个自定义方案及其实例；内置方案和启用中的方案（关闭时为开启时将使用的方案）不能删除。"
       "调用信封的 expected_version 给 GetPermissionScheme.version 时按它比较。", (
        R("scheme_id", description="要删除的自定义方案"),
    ), (R("deleted", description="被删除的方案 ID"),), mutates=True, target="scheme_id"),
    _d("ActivatePermissionScheme", FIXED, "启用一个方案：把它保存的实例按 Portal 现有对象补齐（新对象放入树并按规则写入表项，已删除对象连同其表项移出，移动的对象只改位置，"
       "已有表项保持不变；实例缺失或由其他规则生成时重新生成），然后切换。内置方案的实例由发布前的规则生成时，按新规则重新生成并写回实例中的编辑，"
       "与启动时跟随发布相同：写回记为方案的 last_merge，结果带 restored_entries、写不回的 unrestored 和保持删除的 deleted_again。"
       "权限系统关闭时只记录开启时使用这个方案，开启时补齐。dry_run 只报告影响。"
       "结果和 dry_run 都带授权差（auth_diff）：启用中方案现在的实例对比这个方案补齐后的实例；"
       "权限系统关闭时实际是全部放行，这份报告表示开启后相对旧方案的变化。"
       "内置方案和自定义方案都可以启用。"
       "Portal 管理员永远可以启用方案，不问启用中的方案；其他调用者按方案判定。", (
        R("scheme_id", description="要启用的方案，内置或自定义"), P("dry_run", "boolean", _DRY_RUN),
        P("diff_limit", "integer", "授权差最多列出的组数，默认 50", minimum=1, maximum=500),
    ), (R("scheme_id", description="要启用的方案"), R("title", description="方案标题"), R("unmapped", "array", _UNMAPPED),
        P("regenerate", "boolean", "实例是否重新生成；false 表示只补齐"),
        P("added_nodes", "integer", "放入树中的节点数"), P("moved_nodes", "integer", "移动位置的节点数"),
        P("removed_nodes", "integer", "移出的节点数；重新生成时为原实例的全部节点"),
        P("added_entries", "integer", "写入的表项数"), P("removed_entries", "integer", "移出的表项数"),
        P("restored_entries", "integer", "内置方案按新规则重新生成时写回的实例中的编辑数"),
        P("unrestored", "array", "写不回的实例中的编辑（节点已不在新实例中），格式同 last_merge.unrestored"),
        P("deleted_again", "array", "人删掉、新规则仍给出而保持删除的行"), R("auth_diff", "object", _AUTH_DIFF),
        R("activated", "boolean", "是否已切换到这个方案；dry_run 时为 false")),
        mutates=True, target="scheme_id"),
    _d("GetPermissionNode", FIXED, "读取一个方案实例中 P、A、R 树上的一个节点或元素：位置、祖先链和直接子节点（分页）。", (
        P("scheme_id", description=_SCHEME_ID),
        R("tree", enum=("P", "A", "R"), description=_TREE), R("node_id", description="分类节点（@ 开头）或元素 ID：主体、指令、资源"),
        P("limit", "integer", "每页最多列出的直接子节点数", minimum=1, maximum=500),
        CURSOR("上一页返回的 next_cursor，原样交回，继续列出子节点；不填时从第一个子节点读起"),
    ), (R("scheme_id", description="节点所在的方案"), R("tree", description="节点所在的树"),
        R("node", "object", "id、kind（class 或 element）、title、parent、path"),
        R("children", "array", "直接子节点：id、kind、title；实例中的元素另有 object_type"), NEXT_CURSOR("还有更多子节点时返回"))),
    _d("SearchPermissionNodes", FIXED, "在一个方案实例的一棵树中按 ID 或标题查找节点：先列声明的分类节点与指令元素，再列实例中的元素。", (
        P("scheme_id", description=_SCHEME_ID), R("tree", enum=("P", "A", "R"), description=_TREE),
        R("query", description="ID 或标题包含的文字，不区分大小写"),
        P("limit", "integer", "默认 20", minimum=1, maximum=100),
    ), (R("scheme_id", description="查找的方案"), R("tree", description="查找的树"),
        R("nodes", "array", "每项含 id、kind（class 或 element）、title、object_type、path"))),
    _d("ListPermissionEntries", (On("p", optional=True), On("r", optional=True)), "按 P、A、R 节点筛选并分页列出一个方案实例的表项，按 p、a、r、entry_id 排序。表项不区分来源：规则生成的与人写入的相同。"
       "p 和 r 至少给一个，各按方案中本指令对它的要求判定：给 p 时以这个主体或节点为对象，给 r 时以这个资源或节点为对象；"
       "列出整个实例用 r=@portal、r_scope=subtree。", (
        P("scheme_id", description=_SCHEME_ID),
        P("p", description="P 树节点；与 r 至少给一个"), P("a", description="A 树节点"), P("r", description="R 树节点；与 p 至少给一个"),
        P("p_scope", enum=("exact", "subtree", "ancestors"), description=_SCOPE),
        P("a_scope", enum=("exact", "subtree", "ancestors"), description=_SCOPE),
        P("r_scope", enum=("exact", "subtree", "ancestors"), description=_SCOPE),
        P("effect", enum=("permit", "deny"), description=_EFFECT), P("disabled", "boolean", _DISABLED), P("temporary", "boolean", "true 只列临时凭证，false 只列持久表项"),
        CURSOR(), P("limit", "integer", "默认 100", minimum=1, maximum=500),
    ), (R("scheme_id", description="表项所在的方案"), R("total", "integer", "符合筛选的表项总数"), R("entries", "array", _ENTRY), NEXT_CURSOR(),
        P("allows", "object", "给 r 时：CreatePermissionEntry、DeletePermissionEntry → 调用者能否在这个 r 上写入或删除表项；"
          "关闭时已登录主体都可以。每条表项另带 allows：UpdatePermissionEntry、DeletePermissionEntry "
          "→ 调用者能否修改、删除这一条（按它的 r）")), any_of=("p", "r")),
    _d("CreatePermissionEntry", (On("r"),), "在一个方案（内置或自定义）的实例中写入一条表项 (p, a, r, permit/deny)。只校验三个节点存在；与已有表项内容相同时冲突。"
       "按方案中本指令对 r 的要求判定。User 分类用 UpdateUser 的 changes.category 修改。", (
        P("scheme_id", description=_SCHEME_ID),
        R("p", description="P 树节点：主体 ID、Project 或 Lineage ID，或 @ 开头的分类节点"),
        R("a", description="A 树节点：@ 开头的分类节点或指令元素"),
        R("r", description="R 树节点：资源 ID 或 @ 开头的分类节点"),
        P("effect", enum=("permit", "deny"), description=_EFFECT), P("disabled", "boolean", _DISABLED),
    ), (R("scheme_id", description="写入表项的方案"), R("entry", "object", _ENTRY)), mutates=True, target="r"),
    _d("UpdatePermissionEntry", (On("entry_id", via="permission_entry.r"), On("r", optional=True)), "修改一条表项的 p、a、r、effect 或停用状态（disabled）；临时凭证不能修改。"
       "入口按表项已保存的 r 判定（声明经 permission_entry.r 取得，默认方案要求这个 r 上的 @admin）；"
       "改 r 时新的 r 也判定；表项不存在时返回不存在。"
       "调用信封的 expected_version 给表项的 version 时按它比较。", (
        P("scheme_id", description=_SCHEME_ID), R("entry_id", description="要修改的表项"),
        P("p", description="新的 P 树节点；省略时不变"), P("a", description="新的 A 树节点；省略时不变"),
        P("r", description="新的 R 树节点；省略时不变，给出时新的 r 也判定"),
        P("effect", enum=("permit", "deny"), description="新的效果；省略时不变"),
        P("disabled", "boolean", "true 停用这一行，false 恢复；省略时不变"),
    ), (R("scheme_id", description="表项所在的方案"), R("entry", "object", _ENTRY)), mutates=True, target="entry_id"),
    _d("DeletePermissionEntry", (On("entry_id", via="permission_entry.r", optional=True),
                                 On("entry_ids", via="permission_entry.r", optional=True)), "删除表项（同一事务）；临时凭证用 RevokeTemporaryCredential 撤销。"
       "入口按每条表项已保存的 r 判定（声明经 permission_entry.r 取得，默认方案要求 @admin），任何一条不通过或不存在时整批不删除。", (
        P("scheme_id", description=_SCHEME_ID),
        P("entry_id", description="要删除的一条表项；与 entry_ids 二选一"),
        P("entry_ids", "array", "1 至 1000 个表项 ID", items={"type": "string"}),
    ), (R("scheme_id", description="表项所在的方案"), R("deleted", "array", "被删除的表项 ID"),),
       mutates=True, one_of=(("entry_id",), ("entry_ids",))),
    _d("GetEffectivePermissions", (On("principal_id"),), "读取一个主体在 A 树各分类节点上被授权的 R 节点及其中的洞、它的全部有效表项和有效的临时凭证；持久凭证展示读取这里。", (
        R("principal_id", description=_PRINCIPAL),
    ), (R("principal_id", description="查看的主体"), R("scheme_id", description="判定所用的启用中方案"),
        R("path", "array", "主体在 P 树中的位置"),
        R("coverage", "object", "A 树每个分类节点的 permit 与 deny 节点"),
        P("user_category", description="User 的分类：portal_admin、member 或 intern；Resident 没有"),
        R("entries", "array", "主体的全部有效表项：entry_id、effect、p、a、r、disabled、version…"),
        R("temporary", "array", "主体签发的和收到的有效临时凭证，格式同 ListTemporaryCredentials 的 credentials")),
       target="principal_id"),
    _d("CheckPermission", (On("principal_id"),), "判定一个主体能否执行一条指令（instruction 与 arguments），或能否对一个资源执行 A 树节点（a 与 resource_id），返回结果、原因（permitted、no_permit、denied）和命中的表项。", (
        R("principal_id", description=_PRINCIPAL),
        P("instruction", description="要判定的指令名；与 arguments 一起给出，与 a、resource_id 二选一"),
        P("arguments", "object", "这次调用的参数，与真实调用相同"),
        P("a", description="A 树节点：@ 开头的分类节点或指令元素"),
        P("resource_id", description="与 a 一起给出：要判定的资源或 R 树节点"),
    ), (R("principal_id", description="被判定的主体"), R("scheme_id", description="判定所用的启用中方案"),
        R("allowed", "boolean", "所有请求都通过时为 true"),
        R("requests", "array", "每个判定请求：action、resource、allowed、reason，命中时带 permit、deny 表项"),
        P("portal_admin", "boolean", "true 表示这次调用由 Portal 管理员保底放行，不问方案（此时 requests 为空）"))),
    _d("IssueTemporaryCredential", FIXED, "把自己在一个资源上持有的 A 树节点临时交给另一个主体：签发时只核对签发者此刻确实持有，立即写入一行带签发者和过期时间的 permit。"
       "给出 message_id 时凭证随这条消息：接收 Resident 对它的输入完成、失败或取消时删除；否则按 expires_in_seconds 到期。两种都最迟在签发 7 天后失效。"
       "也可以在 DeliverMessage 中用 temporary_credentials 随消息签发。凭证写入启用中方案的实例。", (
        R("principal_id", description="接收者"), R("a", description="A 树节点：@ 开头的分类节点或指令元素"),
        R("resource_id", description="签发者在其上持有 a 的资源或 R 树节点"),
        P("message_id", description="接收 Resident 正在处理的、发给它的消息；凭证随这条消息的输入结束"),
        P("expires_in_seconds", "integer", "有效期；未给 message_id 时默认 86400；最长 604800", minimum=60, maximum=604800),
    ), (R("credential_id", description="新凭证的 ID"), R("principal_id", description="接收者"),
        R("a", description="交出的 A 树节点"), R("resource_id", description="凭证作用的资源或 R 树节点"),
        R("issued_by", description="签发者"), R("issued_at", "number", "签发时间，Unix 秒"),
        R("expires_at", "number", "过期时间，Unix 秒"), P("message_id", description="随消息签发时：凭证随这条消息的输入结束")),
       mutates=True, target="resource_id"),
    _d("RenewTemporaryCredential", FIXED, "签发者在按时长签发的临时凭证到期前续期：新的过期时间为此刻加 expires_in_seconds，不晚于签发后 7 天。随消息签发的凭证不能续期。", (
        R("credential_id", description="要续期的临时凭证"),
        R("expires_in_seconds", "integer", "从此刻起的有效期", minimum=60, maximum=604800),
    ), (R("credential_id", description="续期的凭证"), R("principal_id", description="接收者"),
        R("a", description="交出的 A 树节点"), R("resource_id", description="凭证作用的资源或 R 树节点"),
        R("issued_by", description="签发者"), R("issued_at", "number", "签发时间，Unix 秒"),
        R("expires_at", "number", "新的过期时间，Unix 秒")), mutates=True, target="credential_id"),
    _d("RevokeTemporaryCredential", FIXED, "撤销一张临时凭证：签发者撤回，或接收者交还。", (
        R("credential_id", description="要撤销的临时凭证"),
    ), (R("revoked", description="被撤销的凭证 ID"),), mutates=True, target="credential_id"),
    _d("ListTemporaryCredentials", (On("principal_id"),), "列出一个主体签发的和收到的有效临时凭证。", (
        R("principal_id", description=_PRINCIPAL),
    ), (R("credentials", "array", _CREDENTIAL),), target="principal_id"),
)
