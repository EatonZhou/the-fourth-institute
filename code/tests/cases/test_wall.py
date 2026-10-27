"""验证独立操作、固定 Resident、任务恢复、报告与周期调度。

:importance: P2
"""
from __future__ import annotations

import json

import pytest

from management_portal import wall_backend as wall
from management_portal.wall_documents import Source, apply_importance, frontmatter, index_sources, parse_points, point_markdown, replace_point, remove_index_reference, source_diagnostics


def test_wall_metaness_is_inherited_from_document_and_independent_of_importance(subtests, isolated_portal_case):
    """WALL 设计点继承文档 metaness 且与重要度独立"""
    for degree in [1, 3, 6, 10]:
        with subtests.test(degree=degree), isolated_portal_case() as case_path:
            text = f'---\nmetaness: {degree}\nimportance: 8\n---\n# Design\n## 设计点\n- **First**. First behavior.\n<!-- wall: {{"importance": 2, "metaness": 10}} -->\n- **Second**. Second behavior.\n'
            points = parse_points(Source("design.md", "Design", "Core", "design"), text)
            assert [point["metaness"] for point in points] == [degree, degree]
            assert [point["importance"] for point in points] == [8, 2]
            assert all(point["metaness_source"] == "document" for point in points)


def test_wall_invalid_metaness_keeps_content_visible_with_diagnostic(subtests, isolated_portal_case):
    """WALL 无效 metaness 回退默认值并保留内容与诊断"""
    for value in ["true", "false", "0", "11", "2.5", '"3"', "null"]:
        with subtests.test(value=value), isolated_portal_case() as case_path:
            source = Source("plan.md", "Plan", "Core", "improvement")
            text = f"---\nmetaness: {value}\n---\n# Complete change\n\nContext and intended benefit.\n"
            points = parse_points(source, text)
            assert len(points) == 1 and points[0]["metaness"] == 1
            assert source_diagnostics(source, text, points)[0]["code"] == "invalid_metaness"
            absent = text.split("---\n", 2)[2]
            assert not source_diagnostics(source, absent, parse_points(source, absent))


def test_wall_source_requires_current_index_and_authorized_root(wall_project):
    """WALL 来源读取要求当前索引记录与项目访问权限"""
    result = wall.handle_get("/api/wall/source", {"source": ["plan.md"]}, "owner")
    assert result["content"] == (wall_project / "plan.md").read_text()
    assert result["source_id"] == "plan.md"
    with pytest.raises(PermissionError):
        wall.source_document("portal", "plan.md", "outsider")
    (wall_project / "unlisted.md").write_text("Unlisted private file")
    with pytest.raises(KeyError):
        wall.source_document("portal", "unlisted.md", "owner")
    with pytest.raises(KeyError):
        wall.source_document("portal", "../outside.md", "owner")
    (wall_project / "IMPROVE.md").write_text("")
    with pytest.raises(KeyError):
        wall.source_document("portal", "plan.md", "owner")


def test_wall_plan_edit_preserves_complete_context_and_stable_identity(wall_project):
    """WALL 编辑完整方案保留原有上下文与稳定标识"""
    wall._scan(wall.project("portal", "owner"), "owner")
    original = wall.block("portal", "plan.md#plan", "owner")["block"]
    wall.edit({"block": original["id"], "title": "Complete user workflow", "importance": 7, "metaness": 4}, "owner")
    updated = wall.block("portal", original["id"], "owner")["block"]
    assert updated["id"] == original["id"] and updated["title"] == "Complete user workflow"
    assert updated["importance"] == 7 and updated["metaness"] == 4
    assert "Make it useful." in updated["content"] and "完整方案" in updated["content"]
    assert frontmatter((wall_project / "plan.md").read_text())[0]["metaness"] == 4


def test_wall_plan_accepts_anchor_before_leading_metadata():
    """WALL 方案首部锚点不影响标题、元数据与摘要解析"""
    text = '''<a id="plan_0123456789ab"></a>
<!-- wall: {"importance": 9, "resident_id": "resident_aaaaaaaaaaaa", "related": ["decision_bbbbbbbbbbbb#rule"]} -->
# Anchored plan

Explain the intended outcome.
'''

    point = parse_points(Source("plan_0123456789ab", "Plan", "Core", "improvement"), text)[0]

    assert point["title"] == "Anchored plan"
    assert point["importance"] == 9
    assert point["resident_id"] == "resident_aaaaaaaaaaaa"
    assert point["related"] == ["decision_bbbbbbbbbbbb#rule"]
    assert point["preview"] == "Explain the intended outcome."
    assert "<a" not in point["content"] and "wall:" not in point["content"]

    anchor_after_title = text.replace('<a id="plan_0123456789ab"></a>\n', '').replace(
        '# Anchored plan\n', '# Anchored plan\n\n<a id="plan"></a>\n',
    )
    point_after_title = parse_points(
        Source("plan_0123456789ab", "Plan", "Core", "improvement"),
        anchor_after_title,
    )[0]
    assert point_after_title["preview"] == "Explain the intended outcome."
    assert "<a" not in point_after_title["content"]


def test_wall_creates_one_indexed_file_per_improvement(wall_project):
    """WALL 为每个改进方案创建独立文件并加入索引"""
    created = [wall.create({"kind": "improvement", "title": title, "content": "A complete user benefit with context.", "metaness": 3}, "owner")["block"] for title in ("First significant change", "Second significant change")]
    assert created[0]["source_id"] != created[1]["source_id"]
    for point in created:
        assert point["id"] == point["source_id"] + "#plan"
        assert point["metaness"] == 3
        assert point["source_id"] in (wall_project / "IMPROVE.md").read_text()
        assert (wall_project / point["source_id"]).is_file()
    wall.action({"action": "reject", "block": created[0]["id"]}, "owner")
    assert wall.block("portal", created[1]["id"], "owner")["block"]["title"] == "Second significant change"


def test_wall_document_metaness_edit_refreshes_siblings_without_changing_review(wall_project):
    """WALL 修改文档 metaness 同步同源设计点且保留评审标记"""
    path = wall_project / "design.md"
    path.write_text(path.read_text() + "- **Sibling**. Shared source document.\n")
    wall._scan(wall.project("portal", "owner"), "owner")
    point = next(point for point in wall.board("portal", "owner")["blocks"] if point["title"] == "Existing")
    wall.set_reviewed({"block": point["id"], "read_revision": point["read_revision"], "reviewed": True}, "owner")
    wall.edit({"block": point["id"], "metaness": 5}, "owner")
    designs = [point for point in wall.board("portal", "owner")["blocks"] if point["source_id"] == "design.md"]
    assert len(designs) == 2 and all(point["metaness"] == 5 for point in designs)
    assert wall.block("portal", point["id"], "owner")["block"]["reviewed"]


def test_wall_plan_retirement_removes_only_matching_index_rows_contracts(subtests, isolated_portal_case):
    """WALL 方案退役仅移除匹配索引行并保留其他条目"""
    with subtests.test(contract='test_wall_plan_retirement_removes_only_matching_index_rows'), isolated_portal_case() as case_path:
        text = '# Improvements\n\n| Change | Plan |\n| --- | --- |\n| Retire | `plan_0123456789ab` |\n| Keep | `plan_aaaaaaaaaaaa` |\n\n- [Local](plans/change.md)\n  Details for local.\n- [Other](plans/other.md)\n'
        updated = remove_index_reference(text, 'plan_0123456789ab')
        updated = remove_index_reference(updated, 'plans/change.md')
        assert 'Retire' not in updated and 'Details for local' not in updated
        assert 'plan_aaaaaaaaaaaa' in updated and 'plans/other.md' in updated
    with subtests.test(contract='test_wall_points_keep_nested_evidence_and_exclude_code'), isolated_portal_case() as case_path:
        text = '---\nimportance: 8\n---\n# Example\n## 概念\n- Not a point.\n## 设计点\n- **First**. Explanation.\n  - Evidence belongs to First.\n  - More evidence.\n\n- **Second**. Another point.\n\n```md\n- **Not a point**.\n```\n## 使用记录\n- Not a design.\n'
        points = parse_points(Source('decision_0123456789ab', 'doc', '资源', 'design'), text)
        assert [p['title'] for p in points] == ['First', 'Second']
        assert 'Evidence belongs' in points[0]['content']
        assert points[0]['preview'].startswith('Explanation.')
        assert not points[0]['preview'].startswith('First')
        assert points[0]['importance'] == 8
        result = replace_point(text, points[0], '')
        assert 'First' not in result
        assert 'Second' in result and 'Not a design' in result
    with subtests.test(contract='test_wall_plans_are_complete_documents_and_designs_keep_point_boundaries'), isolated_portal_case() as case_path:
        text = '# Opportunities\n\n## 从现有代码核实的问题与机会\n\n1. **Fast loading**. Reduce work.\n\n2. **Cache**. Share results.\n'
        points = parse_points(Source('plan_0123456789ab', 'doc', '资源', 'improvement'), text)
        assert len(points) == 1
        assert points[0]['title'] == 'Opportunities'
        assert points[0]['id'] == 'plan_0123456789ab#plan'
        assert 'Fast loading' in points[0]['content'] and 'Cache' in points[0]['content']
        assert points[0]['document_scope'] is True
        summarized = parse_points(Source('plan.md', 'doc', 'Core', 'improvement'), '# Clear change\n\n**Clear change**：Explain why this matters before implementation details.\n')[0]
        assert summarized['preview'].startswith('Explain why this matters')
        chapter_plan = '# Independent improvements\n\n## Create hello\n\n- Create hello.txt.\n\n## Create world\n\n- Create world.txt.\n'
        chapter_points = parse_points(Source('plan.md', 'doc', 'WALL', 'improvement'), chapter_plan)
        assert [p['title'] for p in chapter_points] == ['Independent improvements']
        assert 'Create world.txt.' in chapter_points[0]['content']
        for kind in ('design',):
            paragraphs = parse_points(Source('doc.md', 'doc', 'WALL', kind), chapter_plan.replace('\n- ', '\n'))
            assert [p['title'] for p in paragraphs] == ['Create hello', 'Create world']
            assert paragraphs[0]['preview'] == 'Create hello.txt.'
            mixed = chapter_plan.replace('\n- Create world', '\nCreate world')
            source = Source('doc.md', 'doc', 'WALL', kind)
            mixed_points = parse_points(source, mixed)
            assert [p['title'] for p in mixed_points] == ['Create hello', 'Create world']
            remaining = replace_point(mixed, mixed_points[0], '')
            assert 'hello' not in remaining and 'Create world.txt.' in remaining
            assert parse_points(source, remaining)[0]['id'] == mixed_points[1]['id']
            background = '# Document\n## 背景\n### 说明\n- Context only.\n\n## Create hello\n\nCreate hello.txt.\n'
            assert [p['title'] for p in parse_points(source, background)] == ['Create hello']
            unrecognized = '# Document\n\nUnstructured request without a point boundary.\n'
            diagnostics = source_diagnostics(source, unrecognized, parse_points(source, unrecognized))
            assert diagnostics[0]['code'] == 'no_points'
            assert diagnostics[0]['source_id'] == 'doc.md'
            assert 'Unstructured request' in diagnostics[0]['preview']
            assert not source_diagnostics(source, '# Empty\n## 设计点与工作\n', [])
        text = '# WALL\n\n## 设计点\n\n' + point_markdown('Direction', 'Improve editing.\n\nMore detail.', {'key': 'chosen', 'kind': 'direction', 'importance': 9, 'related': [points[0]['id']]})
        point = parse_points(Source('.wall/directions.md', 'directions', 'WALL', 'direction'), text)[0]
        assert point['id'] == '.wall/directions.md#chosen'
        assert point['importance'] == 9 and point['related'] == [points[0]['id']]
        assert point['kind'] == 'direction' and 'More detail' in point['content']
        assert 'wall:' not in point['content']
    with subtests.test(contract='test_wall_resident_importance_is_per_point_and_explicit_scores_take_priority'), isolated_portal_case() as case_path:
        points = parse_points(Source('design.md', 'Design', 'Core', 'design'), '---\nimportance: 10\n---\n# Design\n## 设计点\n- **Permissions**. Reject unauthorized writes.\n- **Labels**. Display a name.\n<!-- wall: {"key":"manual", "importance":6} -->\n- **Manual**. Author\'s explicit choice.\n')
        table = '| 点 | 重要度 | 理由 |\n| --- | --- | --- |\n' + '\n'.join([f'| `{points[0]['id']}` | 9 | Shared authorization boundary |', f'| {points[1]['id']} | 3 | Local display only |', f'| {points[2]['id']} | 1 | Resident suggestion |', '| other.md#private | 10 | Not a visible point |'])
        apply_importance(points, table)
        assert [p['importance'] for p in points] == [9, 3, 6]
        assert points[0]['importance_source'] == 'resident'
        assert points[0]['recommendation'] == 'Shared authorization boundary'
        assert points[2]['importance_source'] == 'point'
        assert len(points) == 3
        apply_importance(points, table.replace('| 3 | Local', '| 4 | Local'))
        assert points[1]['importance'] == 4
        apply_importance(points, f'| 点 | 重要度 | 理由 |\n| --- | --- | --- |\n| {points[1]['id']} | NaN | Invalid |\n| {points[0]['id']} | 20 | Out of range |')
        assert [p['importance'] for p in points] == [9, 4, 6]
    with subtests.test(contract='test_wall_protocol_tables_exclude_column_headers'), isolated_portal_case() as case_path:
        text = '# Protocol\n\n## 公开接口\n| 接口 | 行为 |\n| -- | -- |\n| `run` | 开始工作 |\n| `stop` | 停止工作 |\n'
        points = parse_points(Source('decision_0123456789ab', 'doc', '协议', 'design'), text)
        assert [p['title'] for p in points] == ['run', 'stop']
    with subtests.test(contract='test_wall_standalone_table_is_not_attached_to_unrelated_list'), isolated_portal_case() as case_path:
        source = Source('design.md', 'Design', 'API', 'design')
        text = '# Design\n## 设计点\n- **Independent**. An unrelated rule.\n\n| Method | Behavior |\n| --- | --- |\n| run | Start |\n| stop | Stop |\n'
        assert [point['title'] for point in parse_points(source, text)] == ['Independent', 'run', 'stop']
        separated = text.replace('An unrelated rule.', 'A list introduction:').replace('\n\n| Method', '\n\n### 公开接口\n\n| Method')
        assert [point['title'] for point in parse_points(source, separated)] == ['Independent', 'run', 'stop']


def test_wall_index_uses_ids_and_project_relative_links_contracts(tmp_path, subtests, isolated_portal_case):
    """WALL 索引支持统一标识与项目相对链接并保留模块归属"""
    with subtests.test(contract='catalog_table_owns_design_despite_earlier_cross_reference'), isolated_portal_case() as case_path:
        (case_path / 'SPEC.md').write_text(
            '# Spec\n## Earlier\nSee `decision_0123456789ab`.\n'
            '## Owner\n| Design | ID |\n| -- | -- |\n| Target | `decision_0123456789ab` |\n',
            encoding='utf-8',
        )
        (case_path / 'IMPROVE.md').write_text(
            '# Plans\n| Design | Plan |\n| -- | -- |\n| `decision_0123456789ab` | `plan_abcdef012345` |\n',
            encoding='utf-8',
        )
        sources = index_sources(case_path)
        assert [source.address for source in sources] == ['decision_0123456789ab', 'plan_abcdef012345']
        assert all(source.module_path == ('Owner',) for source in sources)
    with subtests.test(contract='test_wall_index_uses_ids_and_project_relative_links'), isolated_portal_case() as case_path:
        tmp_path = case_path
        (tmp_path / 'SPEC.md').write_text('# Spec\n## Resources\n| Name | ID |\n| -- | -- |\n| First | `decision_0123456789ab` |\n\n[local](design.md)\n', encoding='utf-8')
        (tmp_path / 'IMPROVE.md').write_text('# Plans\n| Design | Plan |\n| -- | -- |\n| `decision_0123456789ab` | `plan_abcdef012345` |\n', encoding='utf-8')
        sources = index_sources(tmp_path)
        assert [s.address for s in sources] == ['decision_0123456789ab', 'design.md', 'plan_abcdef012345']
        assert sources[0].domain == 'Resources'
        assert sources[2].module_path == ('Resources',)
    with subtests.test(contract='test_wall_index_uses_only_the_primary_link_in_each_prose_entry'), isolated_portal_case() as case_path:
        tmp_path = case_path
        (tmp_path / 'SPEC.md').write_text('# Spec\n', encoding='utf-8')
        (tmp_path / 'IMPROVE.md').write_text(
            '# Plans\n\n- [Primary plan](plans/change.md#plan)：see [review evidence](review.md).\n',
            encoding='utf-8',
        )
        (tmp_path / 'plans').mkdir()
        (tmp_path / 'plans/change.md').write_text('# Change\n\nA complete plan.\n', encoding='utf-8')
        (tmp_path / 'review.md').write_text('# Review\n\nSupporting evidence only.\n', encoding='utf-8')
        sources = index_sources(tmp_path)
        assert [source.address for source in sources] == ['plans/change.md']
    with subtests.test(contract='test_wall_index_rejects_a_standalone_report_as_an_implementable_plan'), isolated_portal_case() as case_path:
        tmp_path = case_path
        (tmp_path / 'SPEC.md').write_text('# Spec\n', encoding='utf-8')
        (tmp_path / 'IMPROVE.md').write_text(
            '# Plans\n\n- [Primary plan](.wall/plans/change.md)\n- [Static review](.wall/review.md)\n',
            encoding='utf-8',
        )
        (tmp_path / '.wall/plans').mkdir(parents=True)
        (tmp_path / '.wall/plans/change.md').write_text(
            '<!-- wall: {"key":"plan","kind":"plan"} -->\n# Change\n\nA complete plan.\n',
            encoding='utf-8',
        )
        (tmp_path / '.wall/review.md').write_text(
            '# Static review\n\nEvidence and validation boundaries only.\n',
            encoding='utf-8',
        )
        sources = index_sources(tmp_path)
        assert [source.address for source in sources] == ['.wall/plans/change.md']
    with subtests.test(contract='test_wall_spec_modules_follow_heading_levels_and_plan_design_links'), isolated_portal_case() as case_path:
        tmp_path = case_path
        (tmp_path / 'Spec.md').write_text('# Spec\n## 2. Identity\n### Settings\n`decision_0123456789ab`\n## 8. Frontend\n### Home\n`decision_111111111111`\n#### Nested detail\n[Local](local.md)\n### Settings\n`decision_222222222222`\n```markdown\n## Not a module\n`decision_999999999999`\n```\n## 10. Operations\n`decision_333333333333`\n', encoding='utf-8')
        (tmp_path / 'Improve.md').write_text('# Improve\n## Different plan heading\n| Design | Plan |\n| --- | --- |\n| `decision_111111111111` | `plan_aaaaaaaaaaaa` |\n| `decision_0123456789ab` | `plan_bbbbbbbbbbbb` |\n| [Local](local.md) | [Plan](plan.md) |\n\n`plan_cccccccccccc`\n', encoding='utf-8')
        sources = {source.address: source for source in index_sources(tmp_path)}
        assert sources['decision_0123456789ab'].module_path == ('2. Identity', 'Settings')
        assert sources['decision_222222222222'].module_path == ('8. Frontend', 'Settings')
        assert sources['decision_333333333333'].module_path == ('10. Operations',)
        assert 'decision_999999999999' not in sources
        for address in ('plan_aaaaaaaaaaaa', 'local.md', 'plan.md'):
            assert sources[address].module_path == ('8. Frontend', 'Home')
        assert sources['plan_bbbbbbbbbbbb'].domain == '2. Identity'
        assert sources['plan_cccccccccccc'].module_path == ('Different plan heading',)
        source = sources['plan_aaaaaaaaaaaa']
        point = parse_points(source, '# Plan\n## 改进点\n- **Improve**. More useful.\n')[0]
        assert point['module_path'] == ['8. Frontend', 'Home']
        assert point['id'] == parse_points(Source(source.address, 'Plan', 'Old label', 'improvement'), '# Plan\n## 改进点\n- **Improve**. More useful.\n')[0]['id']


def test_wall_local_source_addresses_match_markdown_relations_on_all_platforms(tmp_path):
    """原生文件路径不得改变墙面地址、分类及 Markdown 关联。"""
    directory = tmp_path / ".wall"
    (directory / "plans").mkdir(parents=True)
    (tmp_path / "SPEC.md").write_text("# Spec\n\n[Design](.wall/design.md)\n", encoding="utf-8")
    (tmp_path / "IMPROVE.md").write_text(
        "# Improve\n\n[Plan](.wall/plans/fix.md)\n\n[Issue](.wall/inconsistencies.md)\n", encoding="utf-8")
    (directory / "design.md").write_text(
        '# Design\n## 设计点\n<!-- wall: {"key":"rule"} -->\n- **Rule**. Exact result.\n', encoding="utf-8")
    (directory / "inconsistencies.md").write_text(
        '# Issues\n## 不一致点\n<!-- wall: {"key":"precision"} -->\n- **Precision**. Result differs.\n', encoding="utf-8")
    # This file is discovered without an index link, covering the fallback scan.
    (directory / "directions.md").write_text(
        '# Directions\n## 演化方向\n- **Exact arithmetic**. Preserve precision.\n', encoding="utf-8")
    (directory / "plans/fix.md").write_text(
        '<!-- wall: {"related":[".wall/design.md#rule",".wall/inconsistencies.md#precision"]} -->\n'
        '# Fix precision\n\nPreserve the exact result.\n', encoding="utf-8")
    sources = index_sources(tmp_path)
    assert {source.address: source.kind for source in sources} == {
        ".wall/design.md": "design", ".wall/plans/fix.md": "improvement",
        ".wall/inconsistencies.md": "inconsistency", ".wall/directions.md": "direction",
    }
    points = {point["id"]: point for source in sources
              for point in parse_points(source, (tmp_path / source.address).read_text(encoding="utf-8"))}
    plan = points[".wall/plans/fix.md#plan"]
    assert all(reference in points for reference in plan["related"])
    assert points[plan["related"][1]]["kind"] == "inconsistency"


def test_wall_mutually_linked_indexes_are_not_executable_points(tmp_path, subtests, isolated_portal_case):
    """WALL 相互链接的导航索引不被识别为可执行设计点"""
    for spec_name in ["SPEC.md", "Spec.md"]:
        with subtests.test(spec_name=spec_name), isolated_portal_case() as case_path:
            tmp_path = case_path
            wall_dir = tmp_path / ".wall"
            wall_dir.mkdir()
            (tmp_path / spec_name).write_text("# Spec\n## Core\n[Design](design.md)\n[Plans](IMPROVE.md)\n", encoding="utf-8")
            (tmp_path / "IMPROVE.md").write_text("# Plans\n\nNavigation only.\n\n- [Differences](.wall/inconsistencies.md)\n- [Ideas](.wall/improvements.md)\n- [Directions](.wall/directions.md)\n- [Index](.wall/WALL.md)\n", encoding="utf-8")
            (wall_dir / "WALL.md").write_text(f"# WALL index\n\n- [Differences](inconsistencies.md)\n- [Dev](dev.md)\n- [Directions](directions.md)\n- [Ideas](improvements.md)\n- [Plans](../IMPROVE.md)\n- [Design index](../{spec_name})\n", encoding="utf-8")
            bodies = {"design.md": ("design", 2), ".wall/dev.md": ("improvement", 3),
                      ".wall/directions.md": ("direction", 1), ".wall/improvements.md": ("improvement", 3),
                      ".wall/inconsistencies.md": ("inconsistency", 1)}
            for address, (kind, count) in bodies.items():
                (tmp_path / address).write_text("# Work\n## 设计点与工作\n" + "".join(
                    f"- **{kind} {i}**. Concrete behavior.\n" for i in range(count)
                ), encoding="utf-8")
            sources = index_sources(tmp_path)
            assert {source.address for source in sources} == bodies.keys()
            points = []
            for source in sources:
                assert source.kind == bodies[source.address][0]
                body = (tmp_path / source.address).read_text(encoding="utf-8")
                parsed = parse_points(source, body)
                assert not source_diagnostics(source, body, parsed)
                points.extend(parsed)
            assert len(points) == 8


@pytest.mark.parametrize("instance", ["dev", "release", "standalone"])
def test_wall_instance_dispatches_its_own_jobs(monkeypatch, instance):
    """独立实例按稳定业务键派发自己的 WALL 工作，不依赖旧站标签。"""
    monkeypatch.setenv("PORTAL_INSTANCE", instance)
    calls = []
    monkeypatch.setattr(wall.business_tasks, "start", lambda *args: calls.append(args))
    wall._start_job("wall-existing")
    assert calls == [("wall-job:wall-existing", wall._run_job, "wall-existing")]


@pytest.mark.parametrize("kind", ["design", "improvement"])
@pytest.mark.parametrize("section", ["设计点", "分类接口"])
@pytest.mark.parametrize("indent", ["", "  "])
@pytest.mark.parametrize("annotated", [False, True])
def test_wall_table_belongs_to_its_introducing_point(kind, section, indent, annotated):
    """WALL 表格归属引导设计点且编辑后保持稳定身份"""
    source = Source("notice.md", "Notice", "Home", kind)
    table = "| 标识 | 页面标签 |\n| --- | --- |\n| `bug` | Bug |\n| `request` | 需求 |\n| `release` | Portal Release News |\n| `chat` | 闲谈 |\n| `other` | 其他 |\n"
    annotation = '<!-- wall: {"key":"category-legend","importance":4} -->\n' if annotated else ""
    prefix = f"---\nimportance: 10\n---\n# Notice\n\n## {section}\n\n"
    lead = annotation + "- 五个分类的接口标识与页面标签如下：\n\n"
    following = "\n- **下一条规则**。用户发布时选择的分类立即生效。\n"
    original_id = parse_points(source, prefix + lead + following)[0]["id"]
    text = prefix + lead + "\n".join(indent + line for line in table.splitlines()) + "\n" + following
    points = parse_points(source, text)
    if kind == "improvement":
        assert len(points) == 1 and points[0]["title"] == "Notice"
        assert table.strip() in "\n".join(line.strip() for line in points[0]["content"].splitlines())
        assert "下一条规则" in points[0]["content"]
        return
    assert [point["title"] for point in points] == ["五个分类的接口标识与页面标签", "下一条规则"]
    assert points[0]["id"] == original_id
    assert points[0]["importance"] == (4 if annotated else 10)
    assert [line.strip() for line in points[0]["content"].splitlines() if line.strip().startswith("|")] == table.splitlines()
    assert points[0]["_end"] <= points[1]["_start"]
    remaining = replace_point(text, points[0], "")
    assert "五个分类" not in remaining and "|" not in remaining
    assert [point["title"] for point in parse_points(source, remaining)] == ["下一条规则"]
    replacement = point_markdown(points[0]["title"], points[0]["content"], {"key": points[0]["id"].rpartition("#")[2], "importance": 4})
    edited = parse_points(source, replace_point(text, points[0], replacement))
    assert [point["id"] for point in edited] == [point["id"] for point in points]
    assert edited[0]["importance"] == 4
    assert sum(line.strip().startswith("|") for line in edited[0]["content"].splitlines()) == 7


def test_wall_nested_table_is_not_split_without_an_introducing_colon_contracts(subtests, isolated_portal_case):
    """WALL 缩进表格无引导冒号时仍归属前一设计点"""
    with subtests.test(contract='test_wall_nested_table_is_not_split_without_an_introducing_colon'), isolated_portal_case() as case_path:
        text = '# Design\n## 设计点\n- **Categories**. Mapping for clients.\n\n  | Code | Label |\n  | --- | --- |\n  | bug | Bug |\n  | chat | Chat |\n\n- **Other**. Separate point.\n'
        points = parse_points(Source('design.md', 'Design', 'Home', 'design'), text)
        assert [point['title'] for point in points] == ['Categories', 'Other']
        assert '| Code | Label |' in points[0]['content']
        assert '| chat | Chat |' in points[0]['content']
    with subtests.test(contract='test_wall_recent_completed_requires_explicit_result_links'), isolated_portal_case() as case_path:
        old = dict(id='old', action='implement', title='Old work', status='completed', created_at=1, updated_at=3)
        missing = dict(old, id='unlinked', updated_at=2)
        fresh = dict(old, id='fresh', completed_at=4, result_block_ids=['.wall/dev.md#custom'])
        point = {'id': '.wall/dev.md#old', 'title': 'Current result', 'kind': 'improvement', 'status': 'dev'}
        result = wall._recent_completed([old, missing, fresh], [point])
        assert [job['id'] for job in result] == ['fresh', 'old', 'unlinked']
        assert result[0]['result_blocks'][0]['id'] == '.wall/dev.md#custom'
        assert result[1]['result_blocks'] == []
        assert result[2]['result_blocks'] == []


def test_wall_protocol_heading_rename_preserves_point(wall_project):
    """WALL 协议标题重命名后保留原设计点与正文"""
    path = wall_project / "design.md"
    path.write_text("# API\n## RPC\n### `execute`\nSubmit the work.\n", encoding="utf-8")
    point = parse_points(Source("design.md", "API", "RPC", "design"), path.read_text())[0]
    wall.edit({"block": point["id"], "title": "Execute work", "importance": 8, "content": point["content"]}, "owner")
    updated = wall.block("portal", point["id"], "owner")["block"]
    assert updated["title"] == "Execute work" and "Submit the work" in updated["content"]


def test_wall_table_importance_edit_keeps_table(wall_project):
    """WALL 修改表格设计点重要度时保留表格结构"""
    path = wall_project / "design.md"
    path.write_text("# API\n## 接口\n| 方法 | 行为 |\n| -- | -- |\n| `run` | 开始工作 |\n", encoding="utf-8")
    point = parse_points(Source("design.md", "doc", "协议", "design"), path.read_text())[0]
    wall.edit({"block": point["id"], "importance": 9, "content": point["content"], "title": point["title"]}, "owner")
    updated = wall.block("portal", point["id"], "owner")["block"]
    assert updated["importance"] == 9
    assert "| 方法 | 行为 |" in path.read_text()


@pytest.fixture
def wall_project(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from management_portal import society_service
    from management_portal.workbench_projections import native
    def binding(name, options, backend, *, create_role=False):
        assert name == "wall"
        assert create_role is True
        scope = options["resident_scope"]
        assert scope.startswith(("wall-discussion:portal:", "wall-work:portal:"))
        if scope.startswith("wall-discussion:"):
            return {"resident_id": "resident_111111111111", "lineage_id": "lineage_111111111111"}
        return {"resident_id": "resident_0123456789ab", "lineage_id": "lineage_0123456789ab"}
    monkeypatch.setattr(native, "binding", binding)
    from management_portal.lineage_workflows import WorkflowBindings
    monkeypatch.setattr(WorkflowBindings, "install", lambda self, **kwargs: {key: kwargs[key] for key in ("lineage_id", "resident_id")})

    root = tmp_path / "repo"
    root.mkdir()
    (root / "SPEC.md").write_text("# Spec\n[Design](design.md)\n", encoding="utf-8")
    (root / "IMPROVE.md").write_text("# Improve\n[Plan](plan.md)\n\n[Keep](keep.md)\n", encoding="utf-8")
    (root / "design.md").write_text("# Design\n## 设计点\n- **Existing**. The feature.\n", encoding="utf-8")
    (root / "plan.md").write_text("# Improve\n\nMake it useful.\n\n## 完整方案\n- Keep the context with this improvement.\n", encoding="utf-8")
    (root / "keep.md").write_text("# Keep\n\nAnother significant change.\n", encoding="utf-8")
    monkeypatch.setattr(wall, "PORTAL_ROOT", root)
    monkeypatch.setattr(wall, "WALL_LOCK_ROOT", tmp_path / "wall-locks")
    wall._CACHE.clear()
    class Access:
        def user(self, actor):
            return {"portal_admin": actor == "owner"}
        def is_active(self, actor):
            return bool(actor and actor != "outsider")
        def project_ids(self):
            return ["AI员工"]
    monkeypatch.setattr(wall, "get_portal_access_store", lambda: Access())
    monkeypatch.setattr(wall, "_start_job", lambda *_: None)
    monkeypatch.setattr(society_service, "get_society_service", lambda: SimpleNamespace(resident=SimpleNamespace(
        get_resident_states=lambda resident_ids, actor: {"items": []},
    )))
    return root


def test_wall_incremental_scan_reuses_unchanged_documents(wall_project, monkeypatch):
    """WALL 增量扫描复用未变文档且仅重解析正文变更"""
    from unittest.mock import Mock
    from management_portal import wall_documents as documents
    read = Mock(wraps=wall.read_source)
    parse = Mock(wraps=wall.parse_points)
    index = Mock(wraps=documents._index_entries)
    monkeypatch.setattr(wall, "read_source", read)
    monkeypatch.setattr(wall, "parse_points", parse)
    monkeypatch.setattr(documents, "_index_entries", index)
    record = wall.project("portal", "owner")
    wall._scan(record, "owner")
    initial = wall.board("portal", "owner")
    assert read.call_count == parse.call_count == 3
    assert index.call_count == 2
    read.reset_mock()
    parse.reset_mock()
    index.reset_mock()
    wall._scan(record, "owner")
    assert read.call_count == parse.call_count == index.call_count == 0
    assert wall.board("portal", "owner", known_signature=initial["signature"])["unchanged"]
    # An explicit refresh also checks versions rather than bypassing the document cache.
    def start(_name, fn, *args):
        fn(*args)
        return True
    monkeypatch.setattr(wall.business_tasks, "start", start)
    wall.board("portal", "owner", refresh=True)
    assert read.call_count == parse.call_count == 0
    path = wall_project / "plan.md"
    path.write_text(path.read_text().replace("Make it useful", "Make it faster"))
    wall._scan(record, "owner")
    assert [call.args[1] for call in read.call_args_list] == ["plan.md"]
    assert parse.call_count == 1
    read.reset_mock()
    parse.reset_mock()
    # Metadata-only writes do not require Markdown parsing.
    path.write_text(path.read_text())
    wall._scan(record, "owner")
    assert read.call_count == 1 and parse.call_count == 0
    (wall_project / "SPEC.md").write_text("# Spec\n## New module\n[Design](design.md)\n")
    wall._scan(record, "owner")
    assert wall.board("portal", "owner")["modules"] == [("New module",)]
    path.unlink()
    wall._scan(record, "owner")
    board = wall.board("portal", "owner")
    assert all(point["source_id"] != "plan.md" for point in board["blocks"])
    assert board["errors"][0]["source_id"] == "plan.md"


def test_wall_cached_resource_is_reauthorized_and_reresolved(wall_project, monkeypatch):
    """WALL 缓存资源每次扫描重新授权并解析当前位置"""
    from types import SimpleNamespace
    from unittest.mock import Mock
    from management_portal import wall_documents as documents
    address = "decision_0123456789ab"
    (wall_project / "SPEC.md").write_text(f"# Spec\n`{address}`\n")
    path = wall_project / "design.md"
    denied = False
    def resolve(resource, *, actor):
        assert resource == address and actor == "viewer"
        if denied:
            raise PermissionError("Access revoked")
        return {"resolved_path": str(path), "exists": True}
    def read_content(resource, *, actor):
        resolved = service.resolve(resource, actor=actor)
        return path.read_bytes(), "text/markdown", resolved
    service = SimpleNamespace(resolve=Mock(side_effect=resolve), read_content=Mock(side_effect=read_content))
    monkeypatch.setattr(documents, "get_resource_service", lambda: service)
    record = wall.project("portal", "viewer")
    wall._scan(record, "viewer")
    wall._scan(record, "viewer")
    assert service.read_content.call_count == 1
    assert service.resolve.call_count == 2
    denied = True
    wall._scan(record, "viewer")
    board = wall.board("portal", "viewer")
    assert not board["errors"]
    assert all(point["source_id"] != address for point in board["blocks"])
    denied = False
    path = wall_project / "moved.md"
    path.write_text("# Moved\n## 设计点\n- **Moved point**. Now here.\n")
    wall._scan(record, "viewer")
    assert any(point["title"] == "Moved point" for point in wall.board("portal", "viewer")["blocks"])
    assert service.read_content.call_count == 2


def test_wall_background_checks_keep_cached_wall_visible(wall_project, monkeypatch):
    """WALL 后台校验期间继续显示已有缓存内容"""
    wall._scan(wall.project("portal", "owner"), "owner")
    initial = wall.board("portal", "owner")
    monkeypatch.setattr(wall.business_tasks, "start", lambda *_: True)
    wall.invalidate("portal")
    assert wall.board("portal", "owner", known_signature=initial["signature"])["unchanged"]
    assert not wall.board("portal", "owner")["loading"]


def test_wall_initial_scan_status_skips_full_state(wall_project, monkeypatch):
    """WALL 初始扫描状态查询跳过完整业务状态读取"""
    monkeypatch.setattr(wall, "_cache", lambda *_args, **_kwargs: {"loading": True})
    monkeypatch.setattr(wall, "_read_state", lambda: pytest.fail("scan poll read all WALL state"))
    board = wall.board("portal", "owner")
    assert board["loading"] and board["blocks"] == [] and board["signature"] == ""


def test_wall_importance_cache_is_incremental_and_does_not_mutate_document_points(wall_project, monkeypatch):
    """WALL 重要度缓存增量更新且不修改原文设计点"""
    from unittest.mock import Mock
    record = wall.project("portal", "owner")
    wall._scan(record, "owner")
    original = wall.board("portal", "owner")["blocks"][0]
    path = wall_project / ".wall" / "importance.md"
    path.parent.mkdir()
    path.write_text(f"| Point | Importance | Reason |\n| --- | --- | --- |\n| {original['id']} | 9 | Shared behavior |\n")
    wall._scan(record, "owner")
    read = Mock(wraps=wall.read_source)
    monkeypatch.setattr(wall, "read_source", read)
    wall._scan(record, "owner")
    assert read.call_count == 0
    scored = wall.board("portal", "owner")["blocks"][0]
    assert scored["importance"] == 9 and not scored["unread"]
    path.unlink()
    wall._scan(record, "owner")
    assert wall.board("portal", "owner")["blocks"][0]["importance"] == original["importance"]
    assert read.call_count == 0


def test_wall_unread_baseline_changes_receipts_and_user_isolation(wall_project, monkeypatch):
    """WALL 未读按内容版本与用户隔离且拒绝过期阅读回执"""
    for actor in ("owner", "viewer"):
        wall._scan(wall.project("portal", actor), actor)
        assert not any(point["unread"] for point in wall.board("portal", actor)["blocks"])
    path = wall_project / "plan.md"
    path.write_text(path.read_text() + "- **New work**. Newly suggested.\n")
    for actor in ("owner", "viewer"):
        wall._scan(wall.project("portal", actor), actor)
    board = wall.board("portal", "viewer")
    unread = [point for point in board["blocks"] if point["unread"]]
    assert [point["title"] for point in unread] == ["Improve"]
    assert "New work" in unread[0]["content"]
    point = unread[0]
    payload = {"project": "portal", "block": point["id"], "read_revision": point["read_revision"]}
    with pytest.raises(PermissionError):
        wall.mark_read(payload, "outsider")
    assert wall.mark_read({**payload, "read_revision": "old"}, "viewer")["read"] is False
    # Read permission alone allows acknowledgement, but stale versions do not.
    assert wall.handle_post("/api/wall/read", payload, "viewer")["read"]
    changed = wall.board("portal", "viewer", known_signature=board["signature"])
    assert not changed.get("unchanged")
    assert not any(point["unread"] for point in changed["blocks"])
    assert next(p for p in wall.board("portal", "owner")["blocks"] if p["id"] == point["id"])["unread"]
    wall._CACHE.clear()
    wall._scan(wall.project("portal", "viewer"), "viewer")
    assert not any(point["unread"] for point in wall.board("portal", "viewer")["blocks"])
    # Metadata and running status are not new reading content.
    wall.edit({**payload, "importance": 9}, "owner")
    wall._scan(wall.project("portal", "viewer"), "viewer")
    assert not wall.block("portal", point["id"], "viewer")["block"]["unread"]
    path.write_text(path.read_text().replace("Newly suggested.", "Updated result."))
    wall._scan(wall.project("portal", "viewer"), "viewer")
    assert not wall.mark_read(payload, "viewer")["read"]
    updated = wall.block("portal", point["id"], "viewer")["block"]
    assert updated["unread"] and updated["read_revision"] != point["read_revision"]


def test_wall_unread_completion_and_one_to_many_results(wall_project):
    """WALL 完成结果与新增多个设计点分别标记未读"""
    wall._scan(wall.project("portal", "owner"), "owner")
    initial = wall.board("portal", "owner")
    point = next(p for p in initial["blocks"] if p["kind"] == "improvement")
    record = wall.project("portal", "owner")
    job = wall.action({"action": "implement", "block": point["id"]}, "owner")["job"]
    wall._finish(record, job, {"status": "completed", "summary": "Implemented the complete Plan."})
    wall._scan(record, "owner")
    changed = next(p for p in wall.board("portal", "owner")["blocks"] if p["status"] == "dev")
    assert changed["unread"] and changed["status"] == "dev"
    wall.mark_read({"block": changed["id"], "read_revision": changed["read_revision"]}, "owner")
    path = wall_project / "design.md"
    path.write_text(path.read_text() + "- **Result one**. First design.\n- **Result two**. Second design.\n")
    wall._scan(wall.project("portal", "owner"), "owner")
    unread = [p["title"] for p in wall.board("portal", "owner")["blocks"] if p["unread"]]
    assert unread == ["Result one", "Result two"]


def test_wall_review_marks_are_personal_persistent_and_independent_of_read_state(wall_project, monkeypatch):
    """WALL 评审标记按用户持久保存且独立于未读状态"""
    for kind in ("direction", "inconsistency", "improvement"):
        wall.create({"kind": kind, "title": f"Review {kind}", "content": "Saved content."}, "owner")
    wall._scan(wall.project("portal", "viewer"), "viewer")
    before = wall.board("portal", "viewer")
    assert all(point["reviewed"] is False for point in before["blocks"])
    originals = {path: path.read_bytes() for path in wall_project.rglob("*.md")}
    monkeypatch.setattr(wall, "_scan", lambda *_: pytest.fail("review started a document scan"))
    monkeypatch.setattr(wall.resident_handoff, "submit_invocation", lambda **_: pytest.fail("review started AI"))
    for point in before["blocks"]:
        payload = {"project": "portal", "block": point["id"], "read_revision": point["read_revision"], "reviewed": True}
        assert wall.handle_post("/api/wall/review", payload, "viewer")["reviewed"] is True
        assert wall.block("portal", point["id"], "viewer")["block"]["reviewed"] is True
        assert wall.block("portal", point["id"], "owner")["block"]["reviewed"] is False
    after = wall.board("portal", "viewer", known_signature=before["signature"])
    assert all(point["reviewed"] for point in after["blocks"])
    assert [point["unread"] for point in after["blocks"]] == [point["unread"] for point in before["blocks"]]
    assert originals == {path: path.read_bytes() for path in wall_project.rglob("*.md")}
    assert not wall._read_state()["jobs"] and not wall._read_state()["discussions"]
    point = after["blocks"][0]
    wall._CACHE.clear()
    assert wall.block("portal", point["id"], "viewer")["block"]["reviewed"] is True
    wall._change(lambda data: data["projects"].update(other={**data["projects"]["portal"], "id": "other"}))
    assert wall.block("other", point["id"], "viewer")["block"]["reviewed"] is False
    wall.set_reviewed({"block": point["id"], "read_revision": point["read_revision"], "reviewed": False}, "viewer")
    assert wall.block("portal", point["id"], "viewer")["block"]["reviewed"] is False


def test_wall_review_versions_reject_stale_content_and_preserve_score_only_edits(wall_project):
    """WALL 评审拒绝过期正文版本且仅评分变更保留标记"""
    wall._scan(wall.project("portal", "owner"), "owner")
    initial = wall.board("portal", "owner")
    point = initial["blocks"][0]
    payload = {"block": point["id"], "reviewed": True, "read_revision": point["read_revision"]}
    wall.mark_read(payload, "owner")
    assert not wall.block("portal", point["id"], "owner")["block"]["reviewed"]
    wall.set_reviewed(payload, "owner")
    edited = wall.edit({"block": point["id"], "importance": 10}, "owner")["block"]
    assert edited["reviewed"] and edited["read_revision"] == point["read_revision"]
    for change in ({"title": "New title"}, {"content": "New body"}):
        changed = wall.edit({"block": point["id"], **change}, "owner")["block"]
        assert not changed["reviewed"]
        assert changed["read_revision"] != payload["read_revision"]
        with pytest.raises(ValueError, match="内容已变化"):
            wall.set_reviewed(payload, "owner")
        payload["read_revision"] = changed["read_revision"]
        wall.set_reviewed(payload, "owner")
    _, internal, text, _ = wall._point("portal", point["id"], "owner")
    path = wall_project / internal["source_id"]
    path.write_text(replace_point(text, internal, point_markdown(internal["title"], internal["content"], {
        **internal["_meta"], "key": internal["id"].rpartition("#")[2], "kind": "improvement", "status": "dev",
    })))
    dev = wall.block("portal", point["id"], "owner")["block"]
    assert dev["status"] == "dev" and not dev["reviewed"]
    wall.set_reviewed({**payload, "read_revision": dev["read_revision"]}, "owner")
    with pytest.raises(ValueError, match="内容已变化"):
        wall.set_reviewed({**payload, "reviewed": False}, "owner")
    assert wall.block("portal", point["id"], "owner")["block"]["reviewed"]


def test_wall_review_requires_access_and_an_explicit_boolean(wall_project):
    """WALL 设置评审标记要求访问权限和显式布尔值"""
    point = wall.create({"kind": "design", "title": "New design", "content": "Current behavior."}, "owner")["block"]
    assert point["read_revision"] and not point["reviewed"]
    payload = {"block": point["id"], "read_revision": point["read_revision"], "reviewed": True, "actor": "owner"}
    with pytest.raises(PermissionError):
        wall.set_reviewed(payload, "outsider")
    for value in (None, "true", "false", 1, 0):
        with pytest.raises(ValueError, match="布尔值"):
            wall.set_reviewed({**payload, "reviewed": value}, "viewer")
    wall.set_reviewed(payload, "viewer")
    assert wall.block("portal", point["id"], "viewer")["block"]["reviewed"]
    assert not wall.block("portal", point["id"], "owner")["block"]["reviewed"]


def test_wall_historical_job_keeps_outcome_but_projects_unavailable_resident(wall_project, monkeypatch):
    """WALL 历史任务保留结果并标记 Resident 已不可用"""
    job = {"id": "historical-job", "project": "portal", "actor": "owner", "action": "analyze",
           "created_at": 1791417600.0, "status": "error", "error": "Original failure",
           "resident_id": "resident_111111111111"}
    wall._change(lambda data: data["jobs"].__setitem__(job["id"], job))
    wall._scan(wall.project("portal", "owner"), "owner")
    result = wall.board("portal", "owner")
    projected = next(item for item in result["jobs"] if item["id"] == job["id"])
    assert projected["resident_available"] is False
    assert projected["resident_id"] == job["resident_id"]
    assert projected["error"] == "Original failure"
    assert wall._read_state()["jobs"][job["id"]] == job


def test_wall_permission_and_no_model_calls_on_board(wall_project, monkeypatch):
    """WALL 看板读取不调用模型且按项目权限开放操作"""
    monkeypatch.setattr(wall.resident_handoff, "submit_invocation", lambda **_: pytest.fail("board launched AI"))
    wall._scan(wall.project("portal", "viewer"), "viewer")
    result = wall.board("portal", "viewer")
    assert len(result["blocks"]) == 3
    assert result["capabilities"]["write"]
    wall.action({"action": "implement", "block": result["blocks"][1]["id"]}, "viewer")
    assert wall.projects("outsider")["projects"] == []
    wall.register({"root": str(wall_project), "project_id": "AI员工"}, "viewer")


@pytest.mark.parametrize("root", ["", ".", "repo", "../repo"])
def test_wall_registration_rejects_relative_directory(wall_project, monkeypatch, root):
    """空目录和相对目录不能意外登记服务进程的工作目录。"""
    monkeypatch.chdir(wall_project)
    with pytest.raises(ValueError, match="绝对路径"):
        wall.register({"root": root, "project_id": "AI员工"}, "owner")


def test_wall_registration_rejects_filesystem_root(wall_project):
    """文件系统根目录不能被当作工程，兼容 Windows 盘符根目录。"""
    with pytest.raises(ValueError, match="软件工程目录"):
        wall.register({"root": wall_project.anchor, "project_id": "AI员工"}, "owner")


def test_wall_registration_asks_update_project_on_its_project(wall_project, tmp_path, monkeypatch):
    """登记与取消登记工程按 UpdateProject 对所属 Project 判定，不再只看是否登录

    登记用请求中的 project_id，取消登记用登记记录中的 project_id；不能修改该 Project 的主体被拒绝，记录不变。
    """
    from management_portal import permission_system

    wall_project = tmp_path / "engineering"
    wall_project.mkdir()
    (wall_project / "SPEC.md").write_text("# Spec\n", encoding="utf-8")
    asked = []
    allowed = {"owner"}

    def allows(actor, instruction, arguments=None):
        asked.append((actor, instruction, (arguments or {}).get("project_id")))
        return actor in allowed

    monkeypatch.setattr(permission_system, "allows", allows)
    with pytest.raises(PermissionError, match="UpdateProject"):
        wall.register({"root": str(wall_project), "project_id": "AI员工"}, "viewer")
    key = wall.register({"root": str(wall_project), "project_id": "AI员工"}, "owner")["project"]["id"]
    with pytest.raises(PermissionError, match="UpdateProject"):
        wall.unregister({"project": key}, "viewer")
    assert key in wall._read_projects()
    assert {item[1:] for item in asked} == {("UpdateProject", "AI员工")}


def test_wall_board_modules_keep_spec_order_and_only_include_readable_points(wall_project, monkeypatch):
    """WALL 模块按规范顺序展示且仅包含可读设计点"""
    sections = ["## 2. Identity\n[Identity](design.md)\n", "## 8. Frontend\n### Home\n[Home](home.md)\n", "## 10. Operations\n[Operations](ops.md)\n", "## 7. Private\n[Private](private.md)\n"]
    (wall_project / "SPEC.md").write_text("# Spec\n" + "\n".join(sections), encoding="utf-8")
    (wall_project / "private.md").write_text("# Private\n", encoding="utf-8")
    for name in ("home", "ops"):
        (wall_project / f"{name}.md").write_text(f"# {name}\n## 设计点\n- **Point**. Description.\n", encoding="utf-8")
    (wall_project / "IMPROVE.md").write_text("# Plans\n| Design | Plan |\n| --- | --- |\n| [Home](home.md) | [Improve](plan.md) |\n", encoding="utf-8")
    original_read = wall.read_source
    def read(root, address, actor):
        if address == "private.md":
            raise PermissionError("private")
        return original_read(root, address, actor)
    monkeypatch.setattr(wall, "read_source", read)
    monkeypatch.setattr(wall.resident_handoff, "submit_invocation", lambda **_: pytest.fail("module scan launched AI"))
    wall._scan(wall.project("portal", "viewer"), "viewer")
    result = wall.board("portal", "viewer")
    assert result["modules"] == [("2. Identity",), ("8. Frontend", "Home"), ("10. Operations",)]
    assert not result["errors"]
    plan = next(point for point in result["blocks"] if point["title"] == "Improve")
    assert plan["module_path"] == ["8. Frontend", "Home"]
    assert wall.block("portal", plan["id"], "viewer")["block"]["module_path"] == plan["module_path"]
    assert wall.board("portal", "viewer", known_signature=result["signature"])["unchanged"]
    (wall_project / "SPEC.md").write_text("# Spec\n" + "\n".join(reversed(sections)), encoding="utf-8")
    wall._scan(wall.project("portal", "viewer"), "viewer")
    changed = wall.board("portal", "viewer", known_signature=result["signature"])
    assert not changed.get("unchanged")
    assert changed["modules"] == list(reversed(result["modules"]))


@pytest.mark.parametrize("access", ["allowed", "denied", "missing"])
def test_wall_document_resident_is_checked_only_when_opening_detail(wall_project, monkeypatch, access):
    """WALL 仅在打开详情时检查文档 Resident 的可访问性"""
    from types import SimpleNamespace
    from management_portal import society_service
    resident_id = "resident_0123456789ab"
    path = wall_project / "design.md"
    path.write_text('# Design\n## 设计点\n<!-- wall: {"key":"shared","resident_id":"' + resident_id + '"} -->\n- **Shared**. The feature.\n', encoding="utf-8")
    calls = []
    def get_resident(identifier, actor):
        calls.append((identifier, actor))
        if access == "denied":
            raise PermissionError("denied")
        if access == "missing":
            raise KeyError(identifier)
        return {"resident_id": identifier, "lineage_id": "lineage_0123456789ab"}
    monkeypatch.setattr(society_service, "get_society_service", lambda: SimpleNamespace(resident=SimpleNamespace(
        get_resident=get_resident,
        get_resident_states=lambda identifiers, actor: {"items": [
            {"resident_id": identifier, "state": {"work_state": "running"}} for identifier in identifiers
        ]},
    )))
    wall._scan(wall.project("portal", "viewer"), "viewer")
    board = wall.board("portal", "viewer")
    point = next(point for point in board["blocks"] if point["title"] == "Shared")
    assert point["resident_id"] == ""
    assert calls == []
    detail = wall.block("portal", point["id"], "viewer")["block"]
    assert detail["resident_id"] == (resident_id if access == "allowed" else "")
    assert detail["lineage_id"] == ("lineage_0123456789ab" if access == "allowed" else "")
    assert detail["resident_work_state"] == ("running" if access == "allowed" else "")
    assert calls == [(resident_id, "viewer")]
    assert resident_id in path.read_text(encoding="utf-8")


def test_wall_discussion_identity_stays_separate_from_implementation(wall_project):
    """WALL 个人讨论身份与实现任务的 Resident 分别保存"""
    point = parse_points(Source("plan.md", "Plan", "Work", "improvement"), (wall_project / "plan.md").read_text())[0]
    discussion = {"resident_id": "resident_111111111111", "lineage_id": "lineage_111111111111"}
    worker = {"resident_id": "resident_222222222222", "lineage_id": "lineage_222222222222"}
    job = wall.action({"action": "implement", "block": point["id"]}, "owner")["job"]
    wall._patch_job(job["id"], status="running", **worker)
    wall._change(lambda state: state["discussions"].__setitem__(wall._discussion_key("portal", point["id"], "owner"), discussion))
    wall._scan(wall.project("portal", "owner"), "owner")
    board_point = next(item for item in wall.board("portal", "owner")["blocks"] if item["id"] == point["id"])
    for result in (board_point, wall.block("portal", point["id"], "owner")["block"]):
        assert result["discussion"] == discussion
        assert result["resident_id"] == discussion["resident_id"]
        assert result["job"]["resident_id"] == worker["resident_id"]
    private = wall.block("portal", point["id"], "viewer")["block"]
    assert private["discussion"]["resident_id"] == ""
    assert private["job"] == {}


def test_wall_discussion_scope_does_not_reuse_a_worker_and_keeps_existing_discussions(wall_project, monkeypatch):
    """WALL 讨论使用独立作用域并沿用已有讨论绑定"""
    from types import SimpleNamespace
    from management_portal import society_service
    point = parse_points(Source("plan.md", "Plan", "Work", "improvement"), (wall_project / "plan.md").read_text())[0]
    identity = {"resident_id": "resident_111111111111", "lineage_id": "lineage_111111111111"}
    calls = []
    def ensure(**payload):
        calls.append(payload)
        return identity
    monkeypatch.setattr(wall.resident_handoff, "ensure_invocation_resident", ensure)
    monkeypatch.setattr(society_service, "get_society_service", lambda: SimpleNamespace(resident=SimpleNamespace(get_resident=lambda *_: identity)))
    for _ in range(2):
        assert wall.discuss("portal", point["id"], "owner", backend_route_id="three")["resident_id"] == identity["resident_id"]
    assert len(calls) == 2
    assert calls[0]["resident_binding"] == calls[1]["resident_binding"]
    assert calls[0]["resident_scope"].startswith("wall-discussion:portal:")
    assert calls[0]["resident_binding"] == identity
    assert calls[0]["backend_route_id"] == "three"
    with pytest.raises(PermissionError):
        wall.discuss("portal", point["id"], "outsider")


def test_wall_personal_discussion_precedes_document_resident_in_board_and_detail(wall_project, monkeypatch):
    """WALL 看板与详情优先显示个人讨论 Resident"""
    from types import SimpleNamespace
    from management_portal import society_service
    path = wall_project / "design.md"
    path.write_text('# Design\n## 设计点\n<!-- wall: {"key":"shared","resident_id":"resident_author"} -->\n- **Shared**. The feature.\n', encoding="utf-8")
    def states(identifiers, actor):
        assert identifiers == ["resident_personal"]
        assert actor == "owner"
        return {"items": [{"resident_id": "resident_personal", "state": {"work_state": "running"}}]}
    monkeypatch.setattr(society_service, "get_society_service", lambda: SimpleNamespace(resident=SimpleNamespace(
        get_resident=lambda *_: pytest.fail("personal discussion should not resolve document author"),
        get_resident_states=states,
    )))
    wall._scan(wall.project("portal", "owner"), "owner")
    block_id = "design.md#shared"
    identity = {"resident_id": "resident_personal", "lineage_id": "lineage_personal"}
    wall._change(lambda state: state["discussions"].__setitem__(wall._discussion_key("portal", block_id, "owner"), identity))
    board = wall.board("portal", "owner")
    point = next(point for point in board["blocks"] if point["id"] == block_id)
    detail = wall.block("portal", block_id, "owner")["block"]
    for result in (point, detail):
        assert result["resident_id"] == identity["resident_id"]
        assert result["lineage_id"] == identity["lineage_id"]
        assert result["resident_work_state"] == "running"
    assert wall.block("portal", block_id, "owner")["block"]["job"] == {}


def test_wall_chat_states_are_batched_visible_and_invalidate_only_runtime_signature(wall_project, monkeypatch):
    """WALL 批量读取可见会话状态且仅改变运行签名"""
    from types import SimpleNamespace
    from management_portal import society_service

    wall._scan(wall.project("portal", "owner"), "owner")
    initial = wall.board("portal", "owner")
    resident_id = "resident_0123456789ab"
    hidden_id = "resident_abcdef012345"
    unrelated_id = "resident_fedcba543210"
    calls = []
    snapshot = {"resident_id": resident_id, "state": {"work_state": "idle"}, "cursor": 1}
    def states(identifiers, actor):
        calls.append((identifiers, actor))
        return {"items": [snapshot]}
    monkeypatch.setattr(society_service, "get_society_service", lambda: SimpleNamespace(resident=SimpleNamespace(get_resident_states=states)))
    monkeypatch.setattr(wall.resident_handoff, "ensure_invocation_resident", lambda **_: pytest.fail("state read created Resident"))
    monkeypatch.setattr(wall.resident_handoff, "submit_invocation", lambda **_: pytest.fail("state read launched AI"))
    for index, point in enumerate(initial["blocks"]):
        identity = {"resident_id": resident_id if index < 2 else hidden_id, "lineage_id": "lineage_0123456789ab"}
        wall._change(lambda data: data["discussions"].__setitem__(wall._discussion_key("portal", point["id"], "owner"), identity))
        wall._change(lambda data: data["discussions"].__setitem__(wall._discussion_key("portal", point["id"], "viewer"), {"resident_id": unrelated_id}))
    previous = initial
    for status in ("idle", "queued", "running", "stopping", "idle"):
        snapshot["state"]["work_state"] = status
        current = wall.board("portal", "owner", known_signature=previous["signature"])
        assert current["signature"] != previous["signature"]
        assert [point["resident_work_state"] for point in current["blocks"]] == [status, status, ""]
        assert all(not point["unread"] and point["job"] == {} for point in current["blocks"])
        assert [point["read_revision"] for point in current["blocks"]] == [point["read_revision"] for point in initial["blocks"]]
        assert calls[-1] == ([resident_id, hidden_id], "owner")
        snapshot["cursor"] += 1
        assert wall.board("portal", "owner", known_signature=current["signature"])["unchanged"]
        previous = current
    assert len(calls) == 10
    wall._change(lambda data: data["discussions"].__setitem__("unrelated", {"resident_id": unrelated_id}))
    assert wall.board("portal", "owner", known_signature=previous["signature"])["unchanged"]


def test_wall_chat_state_outage_keeps_content_and_recovers(wall_project, monkeypatch):
    """WALL 会话状态服务中断时保留内容并支持恢复"""
    from types import SimpleNamespace
    from management_portal import society_service

    wall._scan(wall.project("portal", "owner"), "owner")
    point = wall.board("portal", "owner")["blocks"][0]
    resident_id = "resident_0123456789ab"
    wall._change(lambda data: data["discussions"].__setitem__(wall._discussion_key("portal", point["id"], "owner"), {"resident_id": resident_id}))
    unavailable = True
    def states(identifiers, actor):
        if unavailable:
            raise RuntimeError("offline")
        return {"items": [{"resident_id": resident_id, "state": {"work_state": "running"}}]}
    monkeypatch.setattr(society_service, "get_society_service", lambda: SimpleNamespace(resident=SimpleNamespace(get_resident_states=states)))
    board = wall.board("portal", "owner")
    assert len(board["blocks"]) == 3 and not board["errors"]
    assert board["warnings"][-1]["code"] == "resident_state_unavailable"
    detail = wall.block("portal", point["id"], "owner")
    assert detail["block"]["resident_work_state"] == "" and detail["block"]["content"] == point["content"]
    assert detail["warnings"] == board["warnings"]
    unavailable = False
    recovered = wall.board("portal", "owner", known_signature=board["signature"])
    assert not recovered.get("unchanged") and not recovered["warnings"]
    assert recovered["blocks"][0]["resident_work_state"] == "running"


def test_wall_board_and_detail_share_resident_scores_without_overwriting_manual_edits(wall_project, monkeypatch):
    """WALL 看板与详情共用 Resident 评分且手工评分优先"""
    monkeypatch.setattr(wall.resident_handoff, "submit_invocation", lambda **_: pytest.fail("reading scores launched AI"))
    wall._scan(wall.project("portal", "owner"), "owner")
    point = next(p for p in wall.board("portal", "owner")["blocks"] if p["title"] == "Existing")
    (wall_project / ".wall").mkdir()
    scores = wall_project / ".wall/importance.md"
    scores.write_text(f"# Recommendations\n\n| 点 | 重要度 | 理由 |\n| --- | --- | --- |\n| {point['id']} | 9 | Affects every request |\n", encoding="utf-8")
    wall._scan(wall.project("portal", "owner"), "owner")
    cached = next(p for p in wall.board("portal", "owner")["blocks"] if p["id"] == point["id"])
    assert cached["importance"] == 9
    assert wall.block("portal", point["id"], "owner")["block"]["importance"] == 9
    assert cached["recommendation"] == "Affects every request"
    wall.edit({"block": point["id"], "importance": 4}, "owner")
    wall._scan(wall.project("portal", "owner"), "owner")
    edited = next(p for p in wall.board("portal", "owner")["blocks"] if p["id"] == point["id"])
    assert edited["importance"] == 4
    assert wall.block("portal", point["id"], "owner")["block"]["importance_source"] == "point"


def test_wall_reject_retires_whole_plan_and_keeps_other_plans(wall_project):
    """WALL 拒绝方案将整篇退役并保留其他方案文件"""
    wall._scan(wall.project("portal", "owner"), "owner")
    point = next(p for p in wall.board("portal", "owner")["blocks"] if p["title"] == "Improve")
    wall.action({"action": "reject", "block": point["id"]}, "owner")
    text = (wall_project / "plan.md").read_text()
    assert "status: obsolete" in text and "# Improve" in text
    assert "plan.md" not in (wall_project / "IMPROVE.md").read_text()
    assert "keep.md" in (wall_project / "IMPROVE.md").read_text()
    assert "# Keep" in (wall_project / "keep.md").read_text()
    assert all(p["id"] != point["id"] for p in wall.board("portal", "owner")["blocks"])


@pytest.mark.parametrize("second_action", ["edit", "remove"])
def test_wall_different_points_in_one_document_do_not_overwrite_each_other(wall_project, monkeypatch, second_action):
    """WALL 同文档不同设计点并发修改不会互相覆盖"""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    (wall_project / "SPEC.md").write_text("# Spec\n[Design](design.md)\n")
    (wall_project / "IMPROVE.md").write_text("# Improve\n")
    (wall_project / "design.md").write_text("# Design\n## 设计点\n- **Improve**. First rule.\n- **Keep**. Another rule.\n")
    wall._scan(wall.project("portal", "owner"), "owner")
    points = {point["title"]: point["id"] for point in wall.board("portal", "owner")["blocks"]}
    writing, release, second_done = Event(), Event(), Event()
    write = wall.write_source
    def delayed_write(root, address, text, actor, **kwargs):
        if not writing.is_set():
            writing.set()
            assert release.wait(5)
        return write(root, address, text, actor, **kwargs)
    monkeypatch.setattr(wall, "write_source", delayed_write)
    def second():
        try:
            if second_action == "edit":
                wall.edit({"block": points["Keep"], "title": "Updated second"}, "owner")
            else:
                wall._remove_point(wall.project("portal", "owner"), points["Keep"], "owner")
        finally:
            second_done.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(wall.edit, {"block": points["Improve"], "title": "Updated first"}, "owner")
        assert writing.wait(5)
        other = pool.submit(second)
        second_done.wait(.15)
        release.set()
        first.result(timeout=5)
        other.result(timeout=5)
    text = (wall_project / "design.md").read_text()
    assert "Updated first" in text
    assert ("Updated second" in text) if second_action == "edit" else ("**Keep**" not in text)


@pytest.mark.parametrize("kind", ["design", "direction", "inconsistency"])
@pytest.mark.parametrize("existing_format", ["list", "heading"])
def test_wall_create_preserves_points_after_nested_evidence_headings(wall_project, kind, existing_format):
    """正文含嵌套标题或分节点时仍可新增独立 WALL 点

    验证设计、方向和不一致点正文中的嵌套标题不改变文档章节；分节点后的显式新点保留独立身份，原正文不被合并或覆盖，重读后仍可访问。

    :importance: P1
    :design: decision_06defc47e960
    """
    address = {"design": ".wall/design.md", "direction": ".wall/directions.md",
               "inconsistency": ".wall/inconsistencies.md"}[kind]
    path = wall_project / address
    path.parent.mkdir(exist_ok=True)
    evidence = "Context.\n\n## Evidence heading\n\nA detail.\n\n> ## Quoted heading\n> Quoted detail.\n\nSetext heading\n--------------\n\nLast detail."
    original = "# WALL\n\n## 设计点与工作\n\n" + point_markdown(
        "Existing point", evidence, {"key": "existing", "kind": kind},
    )
    if existing_format == "heading":
        original = '# WALL\n\n<!-- wall: {"key":"existing"} -->\n## Existing point\n\nLast detail.\n'
    path.write_text(original)

    created = wall.create({"kind": kind, "title": "New point", "content": "New detail."}, "owner")["block"]

    assert path.read_text().startswith(original.rstrip())
    points = parse_points(Source(address, "WALL", "WALL", kind), path.read_text())
    assert [point["id"] for point in points] == [address + "#existing", created["id"]]
    assert [point["title"] for point in points] == ["Existing point", "New point"]
    assert "Last detail." in points[0]["content"]
    assert "New detail." not in points[0]["content"]
    wall._scan(wall.project("portal", "owner"), "owner")
    assert wall.block("portal", created["id"], "owner")["block"]["title"] == "New point"


def test_wall_create_edit_direction_roundtrip(wall_project, monkeypatch):
    """WALL 方向创建编辑后保留正文、关联和稳定标识"""
    monkeypatch.setattr(wall.business_tasks, "start", lambda *_: False)
    wall._scan(wall.project("portal", "owner"), "owner")
    created = wall.create({"kind": "direction", "title": "Better editing", "content": "Fast and simple.", "importance": 8}, "owner")
    point_id = created["block"]["id"]
    assert any(p["id"] == point_id for p in wall.board("portal", "owner")["blocks"])
    wall.edit({"block": point_id, "importance": 10, "related": ["design.md#abc"]}, "owner")
    point = wall.block("portal", point_id, "owner")["block"]
    assert point["importance"] == 10
    assert point["related"] == ["design.md#abc"]
    assert "Fast and simple" in point["content"]
    assert "directions.md" in (wall_project / ".wall/WALL.md").read_text()
    wall.edit({"block": point_id, "title": "Clear editing", "content": point["content"]}, "owner")
    updated = wall.block("portal", point_id, "owner")["block"]
    assert updated["title"] == "Clear editing"
    assert "Better editing" not in updated["content"]
    cached = next(p for p in wall.board("portal", "owner")["blocks"] if p["id"] == point_id)
    assert cached["title"] == "Clear editing" and cached["importance"] == 10
    (wall_project / "IMPROVE.md").write_text("# Improve\n## Appendix\n[Local](.wall/improvements.md)\n")
    improvement = wall.create({"kind": "improvement", "title": "Immediate", "content": "New work."}, "owner")["block"]
    wall._scan(wall.project("portal", "owner"), "owner")
    assert wall.block("portal", improvement["id"], "owner")["block"]["domain"] == "Appendix"


def test_wall_success_removes_plan_and_creates_blue_markdown(wall_project):
    """WALL 实现成功退役原方案并生成关联的待发布结果"""
    wall._scan(wall.project("portal", "owner"), "owner")
    point = next(p for p in wall.board("portal", "owner")["blocks"] if p["title"] == "Improve")
    job = wall.action({"action": "implement", "block": point["id"]}, "owner")["job"]
    again = wall.action({"action": "implement", "block": point["id"]}, "owner")["job"]
    assert job["id"] == again["id"]
    wall._finish(wall.project("portal", "owner"), job, {"status": "completed", "summary": "Implemented and tested."})
    assert "status: obsolete" in (wall_project / "plan.md").read_text()
    assert "plan.md" not in (wall_project / "IMPROVE.md").read_text()
    wall._scan(wall.project("portal", "owner"), "owner")
    blue = [p for p in wall.board("portal", "owner")["blocks"] if p["status"] == "dev"]
    assert len(blue) == 1 and "Implemented and tested" in blue[0]["content"]
    assert blue[0]["related"] == []
    completed = wall.board("portal", "owner")["recent_completed"]
    assert len(completed) == 1 and completed[0]["id"] == job["id"]
    assert completed[0]["title"] == "Improve"
    assert completed[0]["completed_at"] >= job["created_at"]
    assert completed[0]["result_blocks"][0]["id"] == blue[0]["id"]
    stored = wall._read_state()["jobs"][job["id"]]
    assert stored["result_block_ids"] == [blue[0]["id"]]
    assert stored["completed_at"] == completed[0]["completed_at"]
    wall._CACHE.clear()
    wall._scan(wall.project("portal", "owner"), "owner")
    assert wall.board("portal", "owner")["recent_completed"] == completed


def test_wall_recent_completed_are_personal_sorted_by_completion_and_bounded(wall_project, monkeypatch):
    """WALL 最近完成列表按用户隔离且按完成时间限量排序"""
    wall._scan(wall.project("portal", "owner"), "owner")
    jobs = [dict(id=f"done-{index}", project="portal", actor="owner", action="implement",
                 status="completed", title=f"Work {index}", created_at=100 - index,
                 updated_at=index + 1, completed_at=index + 1, summary="Done.") for index in range(25)]
    jobs.extend(dict(id=f"queued-{index}", project="portal", actor="owner", action="implement",
                     status="queued", created_at=200 + index) for index in range(90))
    jobs.extend([
        dict(jobs[0], id="viewer-work", actor="viewer", completed_at=1000),
        dict(jobs[0], id="other-project", project="other", completed_at=2000),
        dict(jobs[0], id="failed-work", status="failed", completed_at=3000),
    ])
    wall._change(lambda data: data["jobs"].update({job["id"]: job for job in jobs}))
    monkeypatch.setattr(wall, "_scan", lambda *_: pytest.fail("Opening recent work should not scan documents"))
    result = wall.board("portal", "owner")
    assert [job["id"] for job in result["recent_completed"]] == [f"done-{index}" for index in range(24, 4, -1)]
    assert all(job["status"] == "queued" or job["id"] == "failed-work" for job in result["jobs"])
    assert wall.board("portal", "owner", known_signature=result["signature"])["unchanged"]
    with pytest.raises(PermissionError):
        wall.board("portal", "outsider")
    # Another reader receives only their own work, even with access to the same project.
    record = wall.project("portal", "viewer")
    wall._CACHE[(record["id"], "viewer")] = wall._CACHE[(record["id"], "owner")]
    assert [job["id"] for job in wall.board("portal", "viewer")["recent_completed"]] == ["viewer-work"]


def test_wall_failed_and_blocked_do_not_remove_plan(wall_project):
    """WALL 实现阻塞时保留原方案且拒绝无法解析的结果"""
    wall._scan(wall.project("portal", "owner"), "owner")
    point = next(p for p in wall.board("portal", "owner")["blocks"] if p["title"] == "Improve")
    job = wall.action({"action": "implement", "block": point["id"]}, "owner")["job"]
    wall._finish(wall.project("portal", "owner"), job, {"status": "blocked", "summary": "Need input."})
    assert "Improve" in (wall_project / "plan.md").read_text()
    assert wall._read_state()["jobs"][job["id"]]["status"] == "blocked"
    with pytest.raises(ValueError):
        wall._result("Done maybe")


def test_wall_reuses_the_dev_result_written_by_the_resident(wall_project):
    """WALL 实现完成时复用 Resident 已写入的开发结果"""
    wall._scan(wall.project("portal", "owner"), "owner")
    point = next(p for p in wall.board("portal", "owner")["blocks"] if p["title"] == "Improve")
    job = wall.action({"action": "implement", "block": point["id"]}, "owner")["job"]
    path = wall_project / ".wall/dev.md"
    path.parent.mkdir()
    path.write_text("# Dev\n## 待发布\n" + point_markdown("Improved", "Implemented and tested.", {
        "key": "resident-result", "kind": "improvement", "status": "dev",
    }), encoding="utf-8")
    wall._finish(wall.project("portal", "owner"), job, {
        "status": "completed", "summary": "Done.", "dev_block_id": ".wall/dev.md#resident-result",
    })
    points = parse_points(Source(".wall/dev.md", "Dev", "WALL", "improvement"), path.read_text())
    assert len(points) == 1 and points[0]["title"] == "Improved"
    assert "status: obsolete" in (wall_project / "plan.md").read_text()
    assert "plan.md" not in (wall_project / "IMPROVE.md").read_text()
    stored = wall._read_state()["jobs"][job["id"]]
    assert stored["result_block_ids"] == [".wall/dev.md#resident-result"]
    assert stored["completed_at"] >= job["created_at"]


def test_wall_low_confidence_routing_creates_an_independent_worker(wall_project, monkeypatch):
    """WALL 低置信度推荐仍使用独立工作绑定派发任务"""
    from management_portal import resident_recommendation_backend
    point = parse_points(Source("plan.md", "Plan", "Work", "improvement"), (wall_project / "plan.md").read_text())[0]
    monkeypatch.setattr(resident_recommendation_backend, "recommend_residents", lambda **_: {
        "recommendations": [{"resident_id": "resident_unrelated", "confidence": "low"}],
    })
    submitted = []
    def submit(**kwargs):
        submitted.append(kwargs)
        return {"resident_id": "resident_0123456789ab", "lineage_id": "lineage_0123456789ab", "message_id": "msg_work",
                "event_id": "event_work", "event_source_id": "event_source_0123456789ab",
                "effect_id": "effect_0123456789ab", "trigger_path_id": "trigger_path_0123456789ab"}
    monkeypatch.setattr(wall.resident_handoff, "submit_invocation", submit)
    monkeypatch.setattr(wall.resident_handoff, "resume_and_wait", lambda **_: {"message": 'WALL_RESULT: {"status":"completed","summary":"Done."}'})
    job = wall.action({"action": "implement", "block": point["id"]}, "owner")["job"]
    wall._run_job(job["id"])
    assert len(submitted) == 1
    assert submitted[0]["cwd"] == str(wall_project)
    assert submitted[0]["resident_scope"].startswith("wall-work:portal:")
    assert submitted[0]["resident_binding"] == {"resident_id": "resident_0123456789ab", "lineage_id": "lineage_0123456789ab"}
    assert submitted[0]["workflow_binding"] == submitted[0]["resident_binding"]
    stored = wall._read_state()["jobs"][job["id"]]
    assert stored["status"] == "completed"
    assert stored["trigger_path_id"] == "trigger_path_0123456789ab"
    assert stored["message_id"] == "msg_work"
    assert "invocation_id" not in stored


@pytest.mark.parametrize("ambiguous", [False, True])
def test_wall_receipt_cutover_preserves_business_results_and_exact_event_links(wall_project, ambiguous):
    """WALL 回执迁移保留业务结果并按事件匹配唯一触发路径"""
    from management_common.portal_database import _database_schema
    from management_portal.wall_store import WallState, database
    from scripts.migrate_wall_trigger_paths import migrate

    jobs = {
        "matched": {"id": "matched", "lineage_id": "team", "event_id": "event-one", "message_id": "input-one",
                    "status": "completed", "summary": "Original result", "trigger_path_id": "old-invocation-one"},
        "unavailable": {"id": "unavailable", "lineage_id": "team", "event_id": "event-two", "message_id": "input-two",
                        "status": "failed", "error": "Original error", "trigger_path_id": "old-invocation-two"},
    }
    WallState().replace({"jobs": jobs})
    with database().transaction() as connection, connection.raw.transaction(force_rollback=True):
        connection.execute("ALTER TABLE wall_jobs RENAME COLUMN trigger_path_id TO invocation_id")
        connection.execute("CREATE TABLE trigger_paths (lineage_id TEXT,occurrence_id TEXT,trigger_path_id TEXT)")
        connection.execute("INSERT INTO trigger_paths VALUES ('team','event-one','path-one')")
        if ambiguous:
            connection.execute("INSERT INTO trigger_paths VALUES ('team','event-one','path-two')")
        schema = _database_schema("portal_wall")
        if ambiguous:
            with pytest.raises(ValueError, match="identify one Trigger Path"):
                migrate(connection.raw, wall_schema=schema, lineage_schema=schema)
            assert connection.execute("SELECT invocation_id FROM wall_jobs WHERE id='matched'").fetchone()["invocation_id"] == "old-invocation-one"
        else:
            assert migrate(connection.raw, wall_schema=schema, lineage_schema=schema) == {
                "jobs": 2, "trigger_path_links": 1, "events_without_path": 1}
            restored = {row["id"]: {key: value for key, value in dict(row).items() if value is not None}
                        for row in connection.execute("SELECT * FROM wall_jobs")}
            assert restored["matched"] == {**jobs["matched"], "trigger_path_id": "path-one"}
            assert restored["unavailable"] == {key: value for key, value in jobs["unavailable"].items() if key != "trigger_path_id"}


def test_wall_expert_recommendation_is_context_for_bound_team(wall_project, monkeypatch):
    """WALL 将专家推荐作为已绑定团队的任务上下文"""
    from management_portal import resident_recommendation_backend
    point = parse_points(Source("plan.md", "Plan", "Work", "improvement"), (wall_project / "plan.md").read_text())[0]
    monkeypatch.setattr(resident_recommendation_backend, "recommend_residents", lambda **_: {"recommendations": [{"resident_id": "resident_expert", "confidence": "high"}]})
    submitted = []
    monkeypatch.setattr(wall.resident_handoff, "submit_invocation", lambda **payload: submitted.append(payload) or {"resident_id": "resident_0123456789ab", "lineage_id": "lineage_0123456789ab", "message_id": "msg_work"})
    monkeypatch.setattr(wall.resident_handoff, "resume_and_wait", lambda **_: {"message": 'WALL_RESULT: {"status":"completed","summary":"Done"}'})
    job = wall.action({"action": "implement", "block": point["id"]}, "owner")["job"]
    wall._run_job(job["id"])
    assert "resident_expert" in submitted[0]["message"]
    assert submitted[0]["resident_binding"]["resident_id"] == "resident_0123456789ab"


@pytest.mark.parametrize("cancel_before_delivery", [False, True])
def test_wall_preparation_failure_keeps_identity_and_can_retry(wall_project, monkeypatch, cancel_before_delivery):
    """WALL 准备失败保留已分配身份和方案并允许重试"""
    from management_portal import resident_recommendation_backend
    monkeypatch.setattr(resident_recommendation_backend, "recommend_residents", lambda **_: {"recommendations": []})
    point = parse_points(Source("plan.md", "Plan", "Work", "improvement"), (wall_project / "plan.md").read_text())[0]
    identity = {"resident_id": "resident_0123456789ab", "lineage_id": "lineage_0123456789ab"}
    submissions = []
    fail = True

    def submit(**payload):
        submissions.append(payload)
        assert payload["resident_binding"] == identity
        assert payload["workflow_binding"] == identity
        if cancel_before_delivery:
            wall._patch_job(job["id"], cancel_requested=True)
        payload["on_assigned"](identity)
        assigned = wall._read_state()["jobs"][job["id"]]
        assert assigned["status"] == "preparing"
        assert assigned["resident_id"] == identity["resident_id"]
        assert not assigned.get("message_id")
        if fail:
            raise ValueError("当前 Resident 正在等待 Workspace Handoff ready")
        return {**identity, "message_id": "msg_ready"}

    monkeypatch.setattr(wall.resident_handoff, "submit_invocation", submit)
    monkeypatch.setattr(wall.resident_handoff, "resume_and_wait", lambda **_: {"message": 'WALL_RESULT: {"status":"completed","summary":"Ready and verified."}'})
    job = wall.action({"action": "implement", "block": point["id"]}, "owner")["job"]
    wall._run_job(job["id"])
    failed = wall._read_state()["jobs"][job["id"]]
    assert failed["status"] == ("cancelled" if cancel_before_delivery else "failed")
    assert failed["resident_id"] == identity["resident_id"] and not failed.get("message_id")
    assert "Improve" in (wall_project / "plan.md").read_text()
    if not cancel_before_delivery:
        assert "任务尚未投递" in failed["error"] and "确认就绪后重试" in failed["error"]
        fail = False
        job = wall.action({"action": "implement", "block": point["id"]}, "owner")["job"]
        wall._run_job(job["id"])
        assert wall._read_state()["jobs"][job["id"]]["status"] == "completed"
        assert submissions[0]["resident_scope"] == submissions[1]["resident_scope"]
        assert submissions[0]["business_ref"] != submissions[1]["business_ref"]


def test_wall_action_receipt_does_not_expose_another_actors_job(wall_project):
    """WALL 操作回执按用户隔离且不暴露他人任务"""
    wall._scan(wall.project("portal", "owner"), "owner")
    point = next(p for p in wall.board("portal", "owner")["blocks"] if p["title"] == "Improve")
    first = wall.action({"action": "implement", "block": point["id"]}, "owner")["job"]
    second = wall.action({"action": "implement", "block": point["id"]}, "contributor")["job"]
    assert second["id"] != first["id"] and second["actor"] == "contributor"


def test_wall_restart_resumes_existing_message(wall_project, monkeypatch):
    """WALL 重启后恢复已有任务消息且不重复提交"""
    job = {"id": "test-job", "project": "portal", "block": "", "actor": "owner", "action": "analyse", "title": "Analyse", "status": "running", "resident_id": "resident_0123456789ab", "message_id": "msg_existing", "created_at": 1}
    wall._change(lambda data: data["jobs"].__setitem__(job["id"], job))
    monkeypatch.setattr(wall.resident_handoff, "submit_invocation", lambda **_: pytest.fail("resubmitted"))
    calls = []
    def resume(**kwargs):
        calls.append(kwargs)
        return {"message": 'WALL_RESULT: {"status":"completed","summary":"Reviewed."}'}
    monkeypatch.setattr(wall.resident_handoff, "resume_and_wait", resume)
    wall._run_job(job["id"])
    assert calls[0]["message_id"] == "msg_existing"
    assert wall._read_state()["jobs"][job["id"]]["status"] == "completed"


def test_wall_local_source_cannot_escape_registered_root(wall_project):
    """WALL 本地来源路径禁止越过已登记项目根目录"""
    (wall_project / "SPEC.md").write_text("[escape](../secrets.md)", encoding="utf-8")
    with pytest.raises(PermissionError):
        index_sources(wall_project)


def test_wall_symlinked_index_and_output_cannot_escape_root(wall_project):
    """WALL 符号链接索引与输出目录禁止越过项目根目录"""
    outside = wall_project.parent / "outside"
    outside.mkdir()
    secret = outside / "SPEC.md"
    secret.write_text("Private document", encoding="utf-8")
    index = wall_project / "SPEC.md"
    index.unlink()
    index.symlink_to(secret)
    with pytest.raises(PermissionError):
        index_sources(wall_project)
    index.unlink()
    (wall_project / ".wall").symlink_to(outside, target_is_directory=True)
    with pytest.raises(PermissionError):
        wall.create({"kind": "direction", "title": "Escape"}, "owner")
    assert list(outside.iterdir()) == [secret]


def test_wall_scan_completion_race_does_not_leave_loading(wall_project, monkeypatch):
    """WALL 扫描完成竞态不遗留加载状态且保留较新编辑"""
    monkeypatch.setattr(wall.business_tasks, "start", lambda *_: False)
    result = wall.board("portal", "owner", refresh=True)
    assert result["loading"] is False
    wall._scan(wall.project("portal", "owner"), "owner")
    original_read = wall.read_source
    def read_and_edit(root, address, actor):
        text = original_read(root, address, actor)
        if address == "design.md":
            point = parse_points(Source(address, "Design", "Design", "design"), text)[0]
            wall._cache_point("portal", {**point, "title": "Newer edit"}, actor)
        return text
    monkeypatch.setattr(wall, "read_source", read_and_edit)
    (wall_project / "design.md").write_text("# Design\n## 设计点\n- **Existing**. Changed feature.\n", encoding="utf-8")
    wall._scan(wall.project("portal", "owner"), "owner")
    result = wall.board("portal", "owner")
    assert result["loading"] is False
    assert next(p for p in result["blocks"] if p["source_id"] == "design.md")["title"] == "Newer edit"


def test_wall_runtime_stops_and_can_restart_without_leaving_a_timer(wall_project, monkeypatch):
    """WALL 运行线程可停止并重新启动且不遗留定时线程"""
    import threading
    ticked = threading.Event()
    monkeypatch.setenv("PORTAL_INSTANCE", "dev")
    monkeypatch.setattr(wall, "_tick", ticked.set)
    for _ in range(2):
        wall.start_runtime()
        thread = wall._RUNTIME_THREAD
        assert ticked.wait(2)
        wall.stop_runtime()
        assert not thread.is_alive() and not wall._RUNTIME_STARTED
        ticked.clear()


def test_wall_unchanged_poll_has_no_document_payload(wall_project):
    """WALL 未变化轮询仅返回签名且诊断变化更新响应"""
    wall._scan(wall.project("portal", "owner"), "owner")
    first = wall.board("portal", "owner")
    second = wall.board("portal", "owner", known_signature=first["signature"])
    assert second == {"unchanged": True, "signature": first["signature"]}
    (wall_project / "design.md").write_text("# Design\n\nA request without a point boundary.\n", encoding="utf-8")
    wall._scan(wall.project("portal", "owner"), "owner")
    warning = wall.board("portal", "owner", known_signature=first["signature"])
    assert len(warning["blocks"]) == 2 and warning["errors"] == []
    assert warning["warnings"][0]["source_id"] == "design.md"
    assert "一级列表" in warning["format_hint"]
    (wall_project / "design.md").write_text("# Design\n\nAnother unsupported request.\n", encoding="utf-8")
    wall._scan(wall.project("portal", "owner"), "owner")
    changed = wall.board("portal", "owner", known_signature=warning["signature"])
    assert changed["signature"] != warning["signature"]
    assert "Another unsupported" in changed["warnings"][0]["preview"]
    (wall_project / "design.md").write_text("# Design\n## 设计点与工作\n", encoding="utf-8")
    wall._scan(wall.project("portal", "owner"), "owner")
    repaired = wall.board("portal", "owner", known_signature=changed["signature"])
    assert repaired["warnings"] == [] and len(repaired["blocks"]) == 2


def test_wall_unregister_does_not_delete_project_files(wall_project):
    """WALL 注销项目登记时保留项目文件"""
    item = wall.register({"root": str(wall_project), "project_id": "AI员工"}, "owner")["project"]
    assert item["id"] == "portal"
    key = "fixture-project"
    wall._change(lambda data: data["projects"].__setitem__(key, {**item, "id": key}))
    wall.unregister({"project": key}, "owner")
    assert (wall_project / "SPEC.md").exists()
    assert key not in wall._read_state()["projects"]


def test_wall_other_project_publish_never_borrows_portal_entrypoint(wall_project):
    """WALL 其他项目发布提示不借用 Portal 发布入口"""
    record = {**wall.project("portal", "owner"), "id": "other"}
    prompt = wall._task_prompt(record, {"action": "publish", "actor": "owner"}, None)
    assert "publish_portal_release" not in prompt
    assert "没有发布入口" in prompt
    prompt = wall._task_prompt(record, {"action": "publish-all", "actor": "owner"}, None)
    assert "publish_portal_release" not in prompt
    assert "没有全量发布能力" in prompt
    for action in ("publish", "publish-all"):
        prompt = wall._task_prompt(record, {"action": action, "actor": "owner"}, None)
        assert "不因缺少发布入口返回blocked" not in prompt
        assert "未发布或无法验证时必须返回blocked" in prompt


@pytest.mark.parametrize("action", ["publish", "publish-all"])
def test_other_project_publication_without_commit_preserves_dev(wall_project, monkeypatch, action):
    """缺少实际发布结果时不能因模型返回 completed 移除 Dev 成果。"""
    record = {**wall.project("portal", "owner"), "id": "other"}
    job = {"id": "wall-unpublished", "action": action, "actor": "owner", "block": ".wall/dev.md#kept"}
    wall._change(lambda data: data["jobs"].__setitem__(job["id"], job))
    removed = []
    monkeypatch.setattr(wall, "_remove_point", lambda *args: removed.append(args))
    wall._finish(record, job, {"status": "completed", "summary": "没有发布入口，未同步 Release。"})
    assert removed == []
    finished = wall._read_state()["jobs"][job["id"]]
    assert finished["status"] == "blocked"
    assert "publication_commit" in finished["error"]


def test_an_analysis_that_records_a_defect_it_found_is_completed(wall_project):
    """WALL 分析把发现的不一致如实记录后即为完成，不因被分析工程未通过验证而阻塞

    分析的成果是 WALL 文档；被分析工程本身的缺陷写入记录与 summary，只有分析本身未完成时才返回 blocked。

    A full TAU run saw an analysis record a sample's return-value mismatch
    in .wall/inconsistencies.md and then report the job blocked, because the
    analysed code failed its check.  Finding that is the analysis's result."""
    prompt = wall._task_prompt(wall.project("portal", "owner"), {"action": "analyse", "actor": "owner"}, None)
    assert "如实记录后即为完成（completed）" in prompt
    assert "不因被分析的工程本身未通过验证而返回blocked" in prompt


@pytest.mark.parametrize("outage", ["legacy", "instruction"])
def test_wall_verify_uses_real_framework_boundary(wall_project, monkeypatch, outage):
    """WALL 回归通过实际框架派发且服务故障后沿用任务恢复"""
    from management_portal.workbench_projections import native
    calls = []
    def submit(name, **options):
        import inspect
        inspect.signature(wall.resident_handoff.submit_invocation).bind(**{
            key: value for key, value in options.items() if key not in {"workflow", "create_role"}
        })
        calls.append((name, options))
        return {"resident_id": "resident_0123456789ab", "lineage_id": "lineage_0123456789ab", "message_id": "msg_tests"}
    monkeypatch.setattr(native, "submit", submit)
    run_id = "testrun_0123456789abcdef"
    monkeypatch.setattr(wall.resident_handoff, "resume_and_wait", lambda **_: {
        "message": 'WALL_RESULT: ' + json.dumps({"status": "completed", "summary": "Tests repaired and passed", "test_run_ids": [run_id]})})
    with pytest.raises(ValueError, match="unknown Codex account"):
        wall.action({"action": "verify", "backend_route_id": "unknown-account"}, "owner")
    assert not wall._read_state()["jobs"]
    job = wall.action({"action": "verify", "backend_route_id": "three", "test_case_ids": ["test_73ce003e7c1f"]}, "owner")["job"]
    assert wall.action({"action": "verify"}, "owner")["job"]["id"] == job["id"]
    wall._run_job(job["id"])
    assert calls[0][0] == "wall"
    options = calls[0][1]
    assert options["resident_scope"] == "wall-work:portal:regression"
    assert options["workflow"]["name"] == "wall:work"
    assert options["backend_route_id"] == "three"
    assert job["backend_route_id"] == "three"
    assert options["create_role"] is True
    assert "test_73ce003e7c1f" in options["message"]
    assert "错误或过时的测试断言" in options["message"]
    assert "不自动调用全量发布" in options["message"]
    finished = wall._read_state()["jobs"][job["id"]]
    assert finished["status"] == "completed" and finished["test_run_ids"] == [run_id]
    next_job = wall.action({"action": "verify", "backend_route_id": "three"}, "owner")["job"]
    wall._run_job(next_job["id"])
    assert calls[-1][1]["resident_scope"] == options["resident_scope"]
    # A service restart during preparation keeps the same durable job, role
    # and business reference, then resumes through the normal scheduler.
    next_job = wall.action({"action": "verify", "backend_route_id": "three"}, "owner")["job"]
    def unavailable(*_a, **_k):
        if outage == "instruction":
            raise wall.OperationError("service restarting", code="instruction.owner_unavailable",
                                      http_status=503, retryable=True, commit_state="not_committed",
                                      details={"owner": "lineage"})
        raise wall.SocietyServiceUnavailable("service restarting")
    monkeypatch.setattr(native, "submit", unavailable)
    wall._run_job(next_job["id"])
    assert wall._read_state()["jobs"][next_job["id"]]["status"] == "queued"
    monkeypatch.setattr(native, "submit", submit)
    wall._run_job(next_job["id"])
    assert calls[-1][1]["business_ref"] == next_job["id"]
    assert calls[-1][1]["backend_route_id"] == "three"
    assert calls[-1][1]["resident_scope"] == options["resident_scope"]


@pytest.mark.parametrize("commit_state,retryable,cancelled,expected", [
    ("not_committed", True, False, "queued"),
    ("not_committed", True, True, "cancelled"),
    ("not_committed", False, False, "failed"),
    ("unknown", True, False, "blocked"),
    ("committed", True, False, "blocked"),
])
def test_wall_service_recovery_respects_commit_receipt_and_original_input(
        wall_project, monkeypatch, commit_state, retryable, cancelled, expected):
    """WALL 按提交回执恢复 Lineage 临时故障并沿用原输入

    Lineage 暂时不可用且明确未提交、允许重试时，原任务重新排队并继续等待原输入；取消优先，提交状态未知或已提交时阻塞，不重复投递。

    :importance: P1
    """
    from management_portal.workbench_projections import native
    job = {"id": "recover-service", "project": "portal", "block": "", "actor": "owner", "action": "verify",
           "status": "running", "resident_id": "resident_0123456789ab", "message_id": "msg_original", "created_at": 1}
    wall._change(lambda data: data["jobs"].__setitem__(job["id"], job))
    monkeypatch.setattr(native, "submit", lambda *_a, **_k: pytest.fail("original input must not be resubmitted"))
    def unavailable(**_):
        if cancelled:
            wall._patch_job(job["id"], cancel_requested=True)
        raise wall.OperationError("service restarting", code="instruction.owner_unavailable", http_status=503,
                                  retryable=retryable, commit_state=commit_state, details={"owner": "lineage"})
    monkeypatch.setattr(wall.resident_handoff, "resume_and_wait", unavailable)
    wall._run_job(job["id"])
    saved = wall._read_state()["jobs"][job["id"]]
    assert saved["status"] == expected and saved["message_id"] == "msg_original"
    if expected == "queued":
        calls = []
        def restored(**options):
            calls.append(options)
            return {"message": 'WALL_RESULT: {"status":"completed","summary":"Recovered",'
                    '"test_run_ids":["testrun_0123456789abcdef"]}'}
        monkeypatch.setattr(wall.resident_handoff, "resume_and_wait", restored)
        wall._run_job(job["id"])
        assert calls[0]["message_id"] == "msg_original"
        assert wall._read_state()["jobs"][job["id"]]["status"] == "completed"


def test_wall_repairs_and_reruns_failed_cases_without_repeating_passed_work(wall_project, monkeypatch):
    """WALL 回归恢复沿用原消息并保留阻塞原因与运行记录"""
    # The bound Resident owns test selection and repair; recovery observes its
    # existing message rather than starting another test campaign or Resident.
    job = {"id": "regression", "project": "portal", "block": "", "actor": "owner", "action": "verify",
           "status": "running", "resident_id": "resident_0123456789ab", "message_id": "msg_repair", "created_at": 1}
    wall._change(lambda data: data["jobs"].__setitem__(job["id"], job))
    from management_portal.workbench_projections import native
    monkeypatch.setattr(native, "submit", lambda *_a, **_k: pytest.fail("recovery resubmitted"))
    calls = []
    def resume(**options):
        calls.append(options)
        return {"message": 'WALL_RESULT: {"status":"blocked","summary":"Unresolved failure","test_run_ids":["testrun_0123456789abcdef"]}'}
    monkeypatch.setattr(wall.resident_handoff, "resume_and_wait", resume)
    wall._run_job(job["id"])
    assert calls[0]["message_id"] == "msg_repair"
    finished = wall._read_state()["jobs"][job["id"]]
    assert finished["status"] == "blocked" and finished["error"] == "Unresolved failure"
    assert finished["test_run_ids"] == ["testrun_0123456789abcdef"]


def test_wall_publication_validates_commit_and_keeps_other_cards(wall_project, monkeypatch):
    """WALL 发布校验提交已进入 Release 且仅移除选中卡片"""
    from scripts import portal_release
    path = wall_project / '.wall/dev.md'
    path.parent.mkdir(exist_ok=True)
    path.write_text('# Dev\n## 待发布\n' + ''.join(
        point_markdown(key, 'ready', {'key': key, 'kind': 'improvement', 'status': 'dev'})
        for key in ('selected', 'unrelated')))
    wall._ensure_local_index(wall_project, '.wall/dev.md', 'direction')
    wall.invalidate('portal')
    wall._scan(wall.project('portal', 'owner'), 'owner')
    sha='a'*40
    checked=[]
    def require_commit(commit):
        checked.append(commit)
        if commit != sha:
            raise ValueError('publication_commit 尚未进入 Release 分支')
        return commit
    monkeypatch.setattr(portal_release,'require_released_commit',require_commit)
    updates=[]
    monkeypatch.setattr(wall,'_patch_job',lambda *a,**k:updates.append(k))
    job={'id':'wall-publish','action':'publish','actor':'owner','block':'.wall/dev.md#selected'}
    with pytest.raises(ValueError,match='尚未进入'):
        wall._finish(wall.project('portal','owner'),job,{'status':'completed','publication_commit':'b'*40})
    assert not updates and '**selected**' in path.read_text()
    wall._finish(wall.project('portal','owner'),job,{'status':'completed','publication_commit':sha,'blocks':['.wall/dev.md#unrelated']})
    assert '**selected**' not in path.read_text() and '**unrelated**' in path.read_text()
    assert updates[0]['publication_commit']==sha
    assert checked == ['b'*40,sha]
    wall._finish(wall.project('portal','owner'),job,{'status':'completed','publication_commit':sha})
    assert '**unrelated**' in path.read_text()
    prompt=wall._task_prompt({'id':'portal','title':'Portal','root':str(wall_project)},
                            {**job,'message':''},None)
    assert '--path' in prompt and 'tfi_dev.py publish' in prompt and job['id'] in prompt and '全量测试' in prompt


def test_wall_full_publish_is_independent_and_does_not_retire_cards(wall_project, monkeypatch):
    """WALL 发布与 Resident 回归工作流验证

    :design: decision_06defc47e960, decision_08bb2d9141f9
    """
    from scripts import portal_release
    verify = wall.action({'action': 'verify'}, 'owner')['job']
    publish = wall.action({'action': 'publish-all'}, 'owner')['job']
    assert verify['id'] != publish['id']
    calls = []
    monkeypatch.setattr(wall, '_remove_point', lambda *a: calls.append(a))
    monkeypatch.setattr(portal_release, 'require_released_commit', lambda commit: commit)
    wall._finish(wall.project('portal', 'owner'), publish, {'status': 'completed', 'publication_commit': 'a'*40})
    assert not calls
    assert wall._read_state()['jobs'][verify['id']]['status'] == 'queued'
    assert wall._read_state()['jobs'][publish['id']]['publication_commit'] == 'a'*40
    prompt = wall._task_prompt(wall.project('portal', 'owner'), publish, None)
    assert '--all' in prompt and 'tfi_dev.py publish' in prompt and publish['id'] in prompt


def test_wall_project_jobs_stop_only_selected_job(wall_project, monkeypatch):
    """WALL 发布与 Resident 回归工作流验证

    :design: decision_06defc47e960, decision_08bb2d9141f9
    """
    verify = wall.action({'action': 'verify'}, 'owner')['job']
    publish = wall.action({'action': 'publish-all'}, 'owner')['job']
    wall.action({'action': 'stop', 'job_id': verify['id']}, 'owner')
    jobs = wall._read_state()['jobs']
    assert jobs[verify['id']]['cancel_requested'] is True
    assert jobs[publish['id']]['cancel_requested'] is False
    # Another actor cannot cancel the owner's job even knowing its ID.
    wall.action({'action': 'stop', 'job_id': publish['id']}, 'other')
    assert wall._read_state()['jobs'][publish['id']]['cancel_requested'] is False


def test_wall_independent_schedules_persist_and_trigger_without_analysis(wall_project, monkeypatch):
    """WALL 发布与 Resident 回归工作流验证

    :design: decision_06defc47e960, decision_08bb2d9141f9
    """
    monkeypatch.setattr(wall, 'start_runtime', lambda: None)
    config = {'enabled': False, 'interval_minutes': 60, 'auto_fix': False, 'backend_route_id': 'three',
              'schedules': {'verify': {'enabled': True, 'interval_minutes': 30},
                            'publish-all': {'enabled': True, 'interval_minutes': 60}}}
    wall.automation(config, 'owner')
    calls = []
    monkeypatch.setattr(wall, 'action', lambda payload, actor: calls.append((payload, actor)))
    wall._tick()
    assert {item[0]['action'] for item in calls} == {'verify', 'publish-all'}
    assert all(item[1] == 'owner' and item[0]['backend_route_id'] == 'three' for item in calls)
    wall._tick()
    assert len(calls) == 2
    saved = wall._read_state()['projects']['portal']['schedules']
    wall.automation(config, 'owner')
    assert wall._read_state()['projects']['portal']['schedules']['verify']['last_run'] == saved['verify']['last_run']
    with pytest.raises(PermissionError):
        wall.automation(config, 'outsider')


def test_wall_global_scheduling_switch_preserves_plans_and_manual_actions(wall_project, monkeypatch):
    monkeypatch.setattr(wall, 'start_runtime', lambda: None)
    wall.automation({'enabled': True, 'interval_minutes': 60, 'auto_fix': False,
                     'schedules': {'verify': {'enabled': True, 'interval_minutes': 60}}}, 'owner')
    scheduled = []
    monkeypatch.setattr(wall, 'action', lambda payload, actor: scheduled.append((payload, actor)))
    assert wall.set_scheduling_enabled(False) == {'enabled': False}
    assert wall.scheduling_status() == {'enabled': False}
    wall._tick()
    assert scheduled == []
    assert wall._read_state()['projects']['portal']['automation']['enabled'] is True
    assert wall._read_state()['projects']['portal']['schedules']['verify']['enabled'] is True
    assert wall.set_scheduling_enabled(True) == {'enabled': True}
    wall._tick()
    assert {request['action'] for request, _actor in scheduled} == {'analyse', 'verify'}
    with pytest.raises(ValueError):
        wall.set_scheduling_enabled('false')


def test_wall_scheduling_admin_endpoint_checks_identity_and_payload(monkeypatch):
    from management_portal.http_admin import AdminHttp

    actor = ['viewer']
    payload = [{'enabled': False}]
    monkeypatch.setattr(wall, 'scheduling_status', lambda: {'enabled': True})
    changed = []
    monkeypatch.setattr(wall, 'set_scheduling_enabled', lambda enabled: changed.append(enabled) or {'enabled': enabled})
    admin = AdminHttp(respond=lambda _request, status, result: (status, result),
                      session_actor=lambda _request: actor[0], access_payload=lambda _actor: {},
                      read_json=lambda _request: payload[0], is_admin=lambda value: value == 'admin')
    assert admin.wall_scheduling_get(None, None, None)[0] == 403
    assert admin.wall_scheduling_post(None, None, None)[0] == 403
    actor[0] = 'admin'
    assert admin.wall_scheduling_get(None, None, None) == (200, {'enabled': True})
    payload[0] = {'enabled': False, 'unexpected': 1}
    assert admin.wall_scheduling_post(None, None, None)[0] == 400
    payload[0] = {'enabled': False}
    assert admin.wall_scheduling_post(None, None, None) == (200, {'enabled': False})
    assert changed == [False]


@pytest.mark.parametrize("report, supporting, status", [
    ("# 不一致报告\n\n问候语与设计不一致，需要对齐。\n", False, "blocked"),
    ("# 不一致\n\n## 不一致点\n- **问候语与设计不一致**。实现输出 hi，设计要求 hello。\n", False, "completed"),
    # A verification note linked beside the points has no card of its own and does not void them.
    ("# 不一致\n\n## 不一致点\n- **问候语与设计不一致**。实现输出 hi，设计要求 hello。\n", True, "completed"),
    ("# 不一致报告\n\n问候语与设计不一致，需要对齐。\n", True, "blocked"),
])
def test_an_analysis_counts_as_completed_only_when_wall_reads_its_output(wall_project, monkeypatch, report, supporting, status):
    """WALL 分析只有在产出可识别的点时才算完成，否则阻塞并说明规定形态

    分析任务返回 completed 时，本次写入但没有可识别点的 WALL 文档使任务变为 blocked，并指明设计、Plan、不一致、方向的规定位置与一级列表形态。
    """
    monkeypatch.setattr(wall.resident_handoff, "submit_invocation", lambda **_: {
        "resident_id": "resident_0123456789ab", "lineage_id": "lineage_0123456789ab", "message_id": "msg_analyse"})

    def resume(**_):
        (wall_project / ".wall").mkdir(exist_ok=True)
        (wall_project / ".wall/inconsistencies.md").write_text(report, encoding="utf-8")
        if supporting:
            (wall_project / ".wall/verification.md").write_text("# 验证记录\n\n逐项核对了问候语输出。\n", encoding="utf-8")
            (wall_project / ".wall/WALL.md").write_text("# WALL\n\n- [验证记录](verification.md)\n", encoding="utf-8")
        return {"message": 'WALL_RESULT: {"status":"completed","summary":"Analysed."}'}

    monkeypatch.setattr(wall.resident_handoff, "resume_and_wait", resume)
    job = wall.action({"action": "analyse"}, "owner")["job"]
    wall._run_job(job["id"])
    finished = wall._read_state()["jobs"][job["id"]]
    assert finished["status"] == status
    if status == "blocked":
        assert ".wall/inconsistencies.md" in finished["error"]
    if supporting:
        assert ".wall/verification.md" in {source.address for source in index_sources(wall_project)}


def test_analysis_rejects_points_written_in_navigation_even_when_plan_is_visible(wall_project):
    """WALL 分析拒绝误写在导航索引中的设计点

    即使 Plan 已正常回显，显式设计点误写导航索引仍阻塞；移到实际来源文档后通过。

    :design: decision_06defc47e960
    :design-point:
    """
    import time

    job = {"created_at": time.time() - 1, "action": "analyse"}
    directory = wall_project / ".wall"
    directory.mkdir(exist_ok=True)
    report = '# Analysis\n\n## 不一致点\n<!-- wall: {"key":"empty-input","kind":"inconsistency"} -->\n- **Empty input fails**. Implementation divides by zero.\n'
    (directory / "WALL.md").write_text(report)
    (directory / "plans").mkdir()
    (directory / "plans/fix.md").write_text("# Handle empty input\n\nReturn zero when no entries exist.\n")
    (wall_project / "IMPROVE.md").write_text("# Plans\n\n- [Empty input](.wall/plans/fix.md)\n")
    record = wall.project("portal", "owner")
    assert wall._unrecognized_outputs(record, job) == [".wall/WALL.md"]
    (directory / "inconsistencies.md").write_text(report)
    (directory / "WALL.md").write_text("# WALL\n\n- [Differences](inconsistencies.md)\n")
    assert wall._unrecognized_outputs(record, job) == []
    sources = index_sources(wall_project)
    source = next(item for item in sources if item.address == ".wall/inconsistencies.md")
    assert parse_points(source, report)[0]["id"] == ".wall/inconsistencies.md#empty-input"


def test_work_without_a_publication_entry_still_completes(wall_project):
    """工程没有发布入口时，已完成并验证的 WALL 工作仍返回 completed

    WALL 任务提示说明缺少发布入口只在结果中注明未发布，不因此把已完成并验证的实现返回 blocked；只有发布类任务在缺少入口时返回 blocked。
    """
    # Only the publish actions report blocked without a publication entry; finished and verified work does not.
    point = parse_points(Source("plan.md", "Plan", "Work", "improvement"), (wall_project / "plan.md").read_text())[0]
    job = wall.action({"action": "implement", "block": point["id"]}, "owner")["job"]
    record = wall.project("portal", "owner")
    prompt = wall._task_prompt(record, wall._read_state()["jobs"][job["id"]], point)
    assert "工程没有发布入口时只在结果中说明未发布" in prompt
    assert "不因缺少发布入口返回blocked" in prompt
