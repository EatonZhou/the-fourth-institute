"""Research map instruction declarations, executed by Portal Web.

The Projects feature's part of the instruction catalog (``isa.instructions``
in ``portal_features.py``); every service reads it, Portal Web runs the
handlers in ``research_map_web_isa.py``.  A Portal without the Projects page
has none of these instructions.
"""
from __future__ import annotations

from management_portal.hatg.declarations import CURSOR, NEXT_CURSOR, InstructionDeclaration as D, On, P, R

CATEGORIES = {"research": "研究地图"}

ON_PROJECT = (On("project_id", "project"),)
STANCES = ("candidate", "focus", "explore", "watch", "concluded", "dropped")
CHANGE = {"type": "object", "properties": {"op": {"type": "string", "enum": [
    "set_goal", "add_direction", "update_direction", "move_direction", "remove_direction",
    "add_relation", "remove_relation", "add_note"]}}, "required": ["op"]}


def _d(name, description, parameters=(), result=(), *, mutates=False):
    return D(name=name, category="research", owner="web", description=description,
             parameters=tuple(parameters), result=tuple(result), mutates=mutates, target="project_id",
             decides=ON_PROJECT)


MAP = (
    R("project_id", description="地图所属的 Project"),
    R("revision", "integer", "地图的修订号；还没有地图时为 0。ApplyResearchMapChanges 的 expected_revision 取它"),
    R("goal", description="项目目标，地图中心显示的文字"),
    R("directions", "array", "研究方向，同级按数组顺序排列：direction_id、parent_id（顶层为空）、title、question（要回答的问题）、"
      "approach（打法）、stance（candidate 候选、focus 主攻、explore 探索、watch 观望、concluded 已收束、dropped 放弃）、"
      "teams（负责的 Lineage ID）、expectation（人写给团队的期望）、conclusion（text、by、at，未收束为 null）、"
      "proposed_by、proposal_reason、created_at、created_by、updated_at、updated_by"),
    R("relations", "array", "方向之间的关系：from、to、kind（builds_on 建立在……之上、alternative 互为替代、depends_on 依赖）"),
    R("layout", "object", "direction_id → {x, y}：人在画布上排好的位置"),
    R("layout_revision", "integer", "布局的修订号"),
    R("notes", "array", "每个方向最近 20 条时间线：note_id、direction_id、kind（progress 进展、decision 决定、note 批注）、"
      "text、refs（资源地址）、noted_at、noted_by、revision"),
    R("outputs", "object", "direction_id → 产出：在 links.related 中引用这个方向、调用者能读的资源（resource_id、resource_type、"
      "title、url、modified_at），新的在前；读取历史修订时为空"),
    R("updated_at", description="这次修订的时间"), R("updated_by", description="这次修订的修改者"),
)

DECLARATIONS = (
    _d("GetResearchMap", "读取 Project 的研究地图：目标、方向、关系、布局、各方向最近的时间线与产出；带 revision 时读那次修订时的地图。", (
        R("project_id", description="要读取地图的 Project"),
        P("revision", "integer", "读取这一修订时的地图；省略时读当前地图", minimum=0),
    ), MAP),
    _d("ApplyResearchMapChanges", "以一组改动修改 Project 的研究地图，成为一次新修订：设定目标，增加、修改、移动、删除方向，"
       "增删方向之间的关系，给方向记一条时间线。Resident 增加方向时默认是候选（candidate），User 增加时默认是探索（explore）。", (
        R("project_id", description="要修改地图的 Project"),
        R("expected_revision", "integer", "提交所依据的地图修订号（GetResearchMap 的 revision）；地图已经变了时拒绝", minimum=0),
        R("changes", "array", "按顺序执行的改动，每项一个 op：set_goal{goal}；add_direction{title, question, approach, stance, parent_id, "
          "index, teams, expectation, proposal_reason, key}；update_direction{direction_id, fields:{title, question, approach, "
          "stance, teams, expectation, conclusion}}；move_direction{direction_id, parent_id, index}；remove_direction{direction_id}"
          "（子方向上移到它的位置）；add_relation / remove_relation{from, to, kind}；add_note{direction_id, kind, text, refs}。"
          "add_direction 带 key 时，后面的改动可以用 \"$<key>\" 指代这个新方向", items=CHANGE),
    ), (
        R("revision", "integer", "提交后的地图修订号"),
        R("created", "object", "key → 新方向的 direction_id"),
        R("applied", "array", "按执行结果记录的改动"),
    ), mutates=True),
    _d("ListResearchMapChanges", "按修订顺序列出研究地图的修改记录，以及每次修订后方向的样子，供回放地图的演变。", (
        R("project_id", description="要读取记录的 Project"),
        P("limit", "integer", "每页最多返回的条数，默认 100", minimum=1, maximum=500),
        CURSOR(),
    ), (
        R("changes", "array", "每项 revision、changed_at、changed_by、changes（这次的改动）与 directions（修订后各方向的 "
          "direction_id、parent_id、title、stance、teams）"),
        NEXT_CURSOR(),
    )),
    _d("UpdateResearchMapLayout", "保存研究地图画布上方向的位置；位置不是地图的修订。", (
        R("project_id", description="地图所属的 Project"),
        R("positions", "object", "direction_id → {x, y}；值为 null 时恢复自动排布"),
    ), (
        R("layout", "object", "保存后的全部位置"), R("layout_revision", "integer", "布局的修订号"),
    ), mutates=True),
)
