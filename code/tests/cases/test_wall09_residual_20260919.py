from pathlib import Path
from contextlib import contextmanager
import json
import re
from unittest.mock import Mock
from types import SimpleNamespace

import pytest
import yaml

from management_portal.society_service import project_domain
from tests.cases.test_project_deletion import _configure_project
from tests.cases.test_paper_figure_transactions import figure_store
from management_portal import paper_backend, paper_state_store
from management_portal.resources.providers import project
from management_portal.resources.registry_store import RegistryWriter
from tests.cases.registry_support import ProjectRegistryYaml


def test_project_deletion_rejects_registered_symlink_without_touching_neighbor(monkeypatch, tmp_path):
    """注册路径是符号链接的 Project 拒绝删除，目录、注册表和归档都不变"""
    project_root, registry, archive, deleted = _configure_project(
        monkeypatch, tmp_path, lineages=(["lineage_aaaaaaaaaaaa"], []),
    )
    alias = project_root.parent / "Alias"
    alias.symlink_to(project_root, target_is_directory=True)
    document = yaml.safe_load(registry.read_text())
    document["projects"][0]["path"] = str(alias)
    registry.write_text(yaml.safe_dump(document))
    before = registry.read_bytes()
    seed_before = (project_root / "summary.md").read_bytes()
    with pytest.raises(ValueError, match="symbolic"):
        project_domain.delete_project("Example", "alice")
    assert alias.is_symlink() and project_root.is_dir()
    assert (project_root / "summary.md").read_bytes() == seed_before
    assert registry.read_bytes() == before
    assert not archive.exists()
    assert deleted == []


@pytest.mark.parametrize("variant", ["root", "outside"])
def test_project_deletion_rejects_unsafe_targets_before_side_effects(monkeypatch, tmp_path, variant):
    """Project 路径指向 Projects 根目录或其外部时，删除在任何改动前被拒绝"""
    project_root, registry, archive, deleted = _configure_project(
        monkeypatch, tmp_path, lineages=(["lineage_aaaaaaaaaaaa"], []),
    )
    target = project_root.parent if variant == "root" else tmp_path / "outside"
    target.mkdir(exist_ok=True)
    document = yaml.safe_load(registry.read_text())
    document["projects"][0]["path"] = str(target)
    registry.write_text(yaml.safe_dump(document))
    before = registry.read_bytes()
    with pytest.raises(PermissionError, match="之外"):
        project_domain.delete_project("Example", "alice")
    assert project_root.is_dir() and target.is_dir()
    assert registry.read_bytes() == before
    assert not archive.exists()
    assert deleted == []


@pytest.mark.parametrize("project_type", ["academic", "company", "personal", "other"])
def test_project_seed_is_written_under_the_registry_lock(monkeypatch, tmp_path, project_type):
    """创建 Project 时在注册表锁内写种子目录，academic 类型另建 article 与 references"""
    share = tmp_path / "share"
    projects = share / "Projects"
    projects.mkdir(parents=True)
    registry = ProjectRegistryYaml()
    before = yaml.safe_dump({
        "schema_version": 1,
        "projects": [],
        "status_vocab": ["active"],
        "tag_vocab": [{"folder": "design"}],
    })
    registry.write_text(before)
    monkeypatch.setattr(project, "SHARE_ROOT", share)
    monkeypatch.setattr(project, "PROJECTS_ROOT", projects)
    held = []
    original_seed = project._write_project_seed_files
    original_mutate = project.project_registry.mutate

    @contextmanager
    def observe_lock(**options):
        with original_mutate(**options) as document:
            held.append(document)
            yield document
            held.clear()

    def observe_seed(*args):
        assert held
        assert not (share / ".history").exists()
        return original_seed(*args)

    monkeypatch.setattr(project.project_registry, "mutate", observe_lock)
    monkeypatch.setattr(project, "_write_project_seed_files", observe_seed)
    result = project.create_project({"id": "Fixture", "project_type": project_type})
    assert result["resource_id"] == "Fixture"
    assert not held
    root = projects / "Fixture"
    for relative in ["agent_port/skills", "agent_port/tools", "agents", "knowledge_base/design", "src", "to_archive"]:
        assert (root / relative).is_dir()
    assert (root / "article").exists() == (project_type == "academic")
    assert (root / "references").exists() == (project_type == "academic")
    assert yaml.safe_load(registry.read_text())["projects"][0]["project_type"] == project_type


@pytest.mark.parametrize("failure", ["seed", "registry"])
def test_project_creation_compensates_partial_directory_and_registry(monkeypatch, tmp_path, failure):
    """创建 Project 时种子文件或注册表写入失败会删除半成品目录并恢复注册表"""
    share = tmp_path / "share"
    projects = share / "Projects"
    projects.mkdir(parents=True)
    registry = ProjectRegistryYaml()
    before = yaml.safe_dump({
        "schema_version": 1,
        "projects": [],
        "status_vocab": ["active"],
    })
    registry.write_text(before)
    before = registry.read_text()
    monkeypatch.setattr(project, "SHARE_ROOT", share)
    monkeypatch.setattr(project, "PROJECTS_ROOT", projects)
    if failure == "seed":
        def fail_seed(root, *_args):
            root.mkdir()
            (root / "partial").write_text("fixture")
            raise OSError("synthetic seed failure")
        monkeypatch.setattr(project, "_write_project_seed_files", fail_seed)
    else:
        def fail_registry(*_args):
            raise OSError("synthetic registry failure")
        monkeypatch.setattr(RegistryWriter, "put", fail_registry)
    with pytest.raises(OSError, match=f"synthetic {failure}"):
        project.create_project({"id": "Fixture", "project_type": "company"})
    assert not (projects / "Fixture").exists()
    assert registry.read_text() == before






def test_figure_delete_cannot_unlink_prefix_sibling(figure_store, tmp_path):
    """删除生成图时不会删到目录名前缀相同的相邻目录中的文件"""
    paper, metadata = figure_store
    sibling = tmp_path.parent / (tmp_path.name + "-neighbor")
    sibling.mkdir()
    protected = sibling / "keep.png"
    protected.write_bytes(b"fixture-neighbor")
    metadata.write_text(json.dumps([{"figure_id": "fig-001", "generated": [{"generated_id": "gen-one", "image_rel_path": "../" + sibling.name + "/keep.png"}]}]))
    paper_backend.delete_generated_figure({"paper_id": paper["id"], "figure_id": "fig-001", "generated_id": "gen-one"}, "alice")
    assert protected.read_bytes() == b"fixture-neighbor"


def test_figure_dedupe_stale_metadata_and_scoped_delete(figure_store, monkeypatch, tmp_path):
    """相同的图表重绘请求复用生成项，过期项标为错误后重建，删除只影响指定生成项"""
    paper, metadata = figure_store
    launch = Mock(return_value="")
    monkeypatch.setattr(paper_backend, "_start_figure_draft_task", launch)
    payload = {"paper_id": paper["id"], "figure_id": "fig-001", "request": "fixture", "source_generated_id": "base"}
    first = paper_backend.start_figure_draft(payload, "alice")
    second = paper_backend.start_figure_draft(payload, "alice")
    assert first["generated"]["generated_id"] == second["generated"]["generated_id"]
    assert launch.call_count == 1
    figures = json.loads(metadata.read_text())
    figures[0]["generated"][0]["updated_at"] = 1
    metadata.write_text(json.dumps(figures))
    third = paper_backend.start_figure_draft(payload, "alice")
    assert third["generated"]["generated_id"] != first["generated"]["generated_id"]
    figures = json.loads(metadata.read_text())
    old = next(entry for entry in figures[0]["generated"] if entry["generated_id"] == first["generated"]["generated_id"])
    assert old["status"] == "error" and "stale" in old["error"]
    assert third["generated"]["source_generated_id"] == "base"
    before_neighbor = dict(old)
    image = tmp_path / "generated.png"
    image.write_bytes(b"fixture")
    paper_backend._set_figure_generated_item(paper["id"], "fig-001", third["generated"]["generated_id"], {"image_rel_path": image.name})
    paper_backend.delete_generated_figure({**payload, "generated_id": third["generated"]["generated_id"]}, "alice")
    assert not image.exists()
    assert next(entry for entry in json.loads(metadata.read_text())[0]["generated"] if entry["generated_id"] == old["generated_id"]) == before_neighbor
    with pytest.raises(KeyError):
        paper_backend.delete_generated_figure({**payload, "generated_id": "missing"}, "alice")


def test_note_creation_persists_markdown_deletion_preserves_neighbors(monkeypatch, tmp_path):
    """论文笔记创建时写入 Markdown 文件，删除一条笔记不影响其他笔记"""
    paper = {"id": "paper_0123456789ab", "title": "fixture"}
    monkeypatch.setattr(paper_backend, "_ensure_data_root", lambda: None)
    monkeypatch.setattr(paper_backend, "_all_papers", lambda: [paper])
    monkeypatch.setattr(paper_backend, "_paper_by_id", lambda *_args: paper)
    monkeypatch.setattr(paper_backend, "_paper_store_file", lambda *_args: tmp_path / "notes")
    first = paper_backend.create_note({"paper_id": paper["id"], "body": "first"}, "alice")
    second = paper_backend.create_note({"paper_id": paper["id"], "body": "second"}, "alice")
    assert "first" in (tmp_path / "notes" / (first["note_id"] + ".md")).read_text()
    before = (tmp_path / "notes" / (second["note_id"] + ".md")).read_bytes()
    paper_backend.delete_note({"note_id": first["note_id"]}, "alice")
    assert paper_state_store.records("notes") == [second]
    assert (tmp_path / "notes" / (second["note_id"] + ".md")).read_bytes() == before


@pytest.mark.parametrize("mode", ["missing", "done", "partial_error"])
def test_assets_worker_records_skip_counts_errors_and_stable_paper(monkeypatch, tmp_path, mode):
    """论文资源提取任务在 PDF 缺失时记为跳过、部分失败时记为错误，运行记录归属同一论文"""
    paper_id = "paper_0123456789ab"
    pdf = tmp_path / "fixture.pdf"
    if mode != "missing":
        pdf.write_bytes(b"%PDF-fixture")
    records = []
    monkeypatch.setattr(paper_backend, "_paper_run_record_by_job_id", lambda *_args: None)
    monkeypatch.setattr(paper_backend, "_write_paper_run_record", lambda identity, patch: records.append((identity, patch)))
    monkeypatch.setattr(paper_backend, "_update_paper_run_record", lambda identity, patch: records.append((identity, patch)))
    monkeypatch.setattr(paper_backend, "_paper_asset_lock_path", lambda: tmp_path / "assets.lock")
    monkeypatch.setattr(paper_backend, "_paper_by_id", lambda *_args: {"id": paper_id})
    monkeypatch.setattr(paper_backend, "_local_pdf_for_paper", lambda *_args: pdf)
    refs = Mock(side_effect=OSError("synthetic refs failure")) if mode == "partial_error" else Mock(return_value={"count": 2})
    figures = Mock(return_value={"count": 3})
    monkeypatch.setattr(paper_backend, "extract_refs", refs)
    monkeypatch.setattr(paper_backend, "extract_figures", figures)
    result = paper_backend._run_assets_job(paper_id, actor="fixture", job_id="fixture-assets")
    assert all(identity == paper_id for identity, _patch in records)
    assert [patch["status"] for _identity, patch in records] == ["queued", "running", {"missing": "skipped", "done": "done", "partial_error": "error"}[mode]]
    if mode == "missing":
        refs.assert_not_called()
        figures.assert_not_called()
    else:
        assert result["figures"] == 3
        assert bool(result["errors"]) == (mode == "partial_error")




@pytest.mark.parametrize("mode", ["done", "write_error", "extract_error"])
def test_deep_read_real_analysis_persistence_and_error_terminal(monkeypatch, tmp_path, mode):
    """精读任务成功时写入分析文件并标记 analysis_ready，写入或文本提取失败时以错误结束"""
    from tests.cases.test_paper_deep_read_execution_boundary import _install_job, _result
    original_writer = paper_backend._write_analysis_file
    state = {"job_id": "fixture-deep", "paper_id": "paper_0123456789ab", "status": "queued", "created_by": "fixture"}
    _install_job(monkeypatch, tmp_path, state)
    path = tmp_path / "analysis.md"
    monkeypatch.setattr(paper_backend, "_write_analysis_file", original_writer)
    monkeypatch.setattr(paper_backend, "_paper_store_file", lambda *_args: path)
    fields = Mock(return_value={})
    monkeypatch.setattr(paper_backend, "_update_paper_fields", fields)
    completion = Mock(return_value=_result("# Synthetic Analysis\n" + paper_backend.DEEP_READ_HEADINGS + "\nfixture"))
    monkeypatch.setattr("management_portal.paper_projection.turn", completion)
    if mode == "write_error":
        original_write = Path.write_text
        def fail_analysis(target, *args, **kwargs):
            if target == path:
                raise OSError("synthetic analysis write failure")
            return original_write(target, *args, **kwargs)
        monkeypatch.setattr(Path, "write_text", fail_analysis)
    if mode == "extract_error":
        monkeypatch.setattr(paper_backend, "_extract_pdf_text", Mock(side_effect=OSError("synthetic extraction failure")))
    paper_backend._run_deep_read_job(state["job_id"])
    assert state["status"] == ("done" if mode == "done" else "error")
    if mode == "done":
        assert "Synthetic Analysis" in path.read_text()
        assert path.with_suffix(".usage.json").exists()
        assert fields.call_args.args[1]["status"] == "analysis_ready"
    else:
        fields.assert_not_called()
        assert "synthetic" in state["error"]
    if mode == "extract_error":
        completion.assert_not_called()


@pytest.mark.parametrize("stale", [False, True])
def test_exp_start_deduplicates_or_retires_stale_job(monkeypatch, tmp_path, stale):
    """启动 Exp 时复用运行中的任务，旧任务已失效则标为错误并新建任务"""
    paper = {"id": "paper_0123456789ab", "title": "fixture"}
    old = {"job_id": "old", "paper_id": paper["id"], "status": "running", "created_at": 1}
    paper_state_store.append("exp_jobs", old)
    monkeypatch.setattr(paper_backend, "_all_papers", lambda: [paper])
    monkeypatch.setattr(paper_backend, "_is_stale_exp_job", lambda *_args: stale)
    start = Mock(side_effect=lambda job: job)
    monkeypatch.setattr(paper_backend, "_start_exp_task", start)
    result = paper_backend.start_exp({"paper_id": paper["id"]}, "fixture")
    jobs = paper_state_store.records("exp_jobs")
    if not stale:
        assert result["job_id"] == "old" and jobs == [old]
        start.assert_not_called()
    else:
        assert result["job_id"] != "old"
        assert jobs[0]["status"] == "error" and jobs[0]["step"] == "stale"
        assert [job["job_id"] for job in jobs] == ["old", result["job_id"]]
        assert jobs[1]["status"] == "queued"
        start.assert_called_once()


@pytest.mark.parametrize("failure", [False, True])
def test_exp_real_markdown_commit_and_write_failure(monkeypatch, tmp_path, failure):
    """Exp 任务成功时写入 exp.md 并记录路径，写入失败时以错误结束"""
    paper = {"id": "paper_0123456789ab", "title": "fixture", "_store_root": str(tmp_path)}
    state = {"job_id": "fixture-exp", "paper_id": paper["id"], "created_by": "fixture"}
    path = tmp_path / "exp.md"
    monkeypatch.setattr(paper_backend, "_set_exp_job", lambda _identity, patch: state.update(patch) or dict(state))
    monkeypatch.setattr(paper_backend, "get_paper_detail", lambda *_args: {"paper": paper})
    extraction = "# Synthetic Experiment\n" + "\n".join(f"## {name}" for name in paper_backend.EXP_SECTIONS)
    monkeypatch.setattr(paper_backend, "_generate_exp_extraction", lambda *_args, **_kwargs: (extraction, {"model": "fixture"}))
    monkeypatch.setattr(paper_backend, "_paper_store_file", lambda *_args: path)
    monkeypatch.setattr(paper_backend, "PAPER_STORE", SimpleNamespace(update_paper=Mock(return_value={})))
    if failure:
        path.mkdir()
    paper_backend._run_exp_job("fixture-exp")
    assert state["status"] == ("error" if failure else "done")
    if not failure:
        assert state["exp_path"] == str(path)
        assert "Synthetic Experiment" in path.read_text()
    else:
        assert state["error"]


def test_exp_that_is_not_an_extraction_is_an_error_and_is_not_saved(monkeypatch, tmp_path):
    """Paper Exp 返回的不是实验拆解时任务失败且不保存

    Exp 回复缺少规定小节（少于三分之一）时任务以错误结束，不写入 exp.md。

    :module: workflow.paper
    :importance: P1
    :aspects: functional, behavior
    """
    paper = {"id": "paper_0123456789ab", "title": "fixture", "_store_root": str(tmp_path)}
    state = {"job_id": "fixture-exp", "paper_id": paper["id"], "created_by": "fixture"}
    path = tmp_path / "exp.md"
    monkeypatch.setattr(paper_backend, "_set_exp_job", lambda _identity, patch: state.update(patch) or dict(state))
    monkeypatch.setattr(paper_backend, "get_paper_detail", lambda *_args: {"paper": paper})
    narration = "我先读取环境配置与注册信息，确认产物位置。```bash\ncat agent_profile.yaml\n```"
    monkeypatch.setattr(paper_backend, "_generate_exp_extraction", lambda *_args, **_kwargs: (narration, {"model": "fixture"}))
    monkeypatch.setattr(paper_backend, "_paper_store_file", lambda *_args: path)
    paper_backend._run_exp_job("fixture-exp")
    assert state["status"] == "error"
    assert "Exp 返回的不是规定产物" in state["error"]
    assert not path.exists()


@pytest.mark.parametrize("action,error,status", [("entries", FileNotFoundError("missing"), 404), ("entries", FileExistsError("exists"), 409), ("file", ValueError("invalid"), 400), ("file", PermissionError("denied"), 403), ("upload", "too_large", 413)])
def test_article_actual_http_branch_roles_and_error_mapping(monkeypatch, action, error, status):
    """Article 工作区写入先校验资源权限，并把后端错误映射为 404、409、400、403、413"""
    # The Article workspace writes go through ArticlesHttp.workspace (the route table's handler).
    from management_portal.http_articles import ArticlesHttp
    from management_portal.serve_management_portal import RequestBodyTooLarge

    require = Mock()
    if error == "too_large":
        error = RequestBodyTooLarge("fixture upload too large")
    backend = SimpleNamespace(**{name: Mock(side_effect=error) for name in ["create_article_entry", "write_article_file", "upload_article_file"]},
                              ARTICLE_UPLOAD_MAX_BYTES=1024)
    http = ArticlesHttp(
        respond=lambda _request, code, payload: (code, payload),
        exception_response=lambda _request, exc: (500, {"error": str(exc)}),
        read_json=Mock(side_effect=error) if action == "upload" else (lambda _request: {"path": "main.tex", "content": "fixture"}),
        read_multipart=Mock(), multipart_limit=lambda size: size, body_too_large=RequestBodyTooLarge,
        session_actor=lambda _request: "fixture", require_resource=require, notify_index=Mock(),
        identity_backend=lambda: backend, texlab_backend=Mock(), resource_service=Mock(),
        serve_media=Mock(), serve_download=Mock(), media_content_type=Mock(),
    )
    response = http.workspace(SimpleNamespace(headers={}), None, "fixture",
                              {"article_id": "article_0123456789ab", "action": action})
    assert response[0] == status
    require.assert_called_once_with("article_0123456789ab", "fixture")


def test_texlab_diagnostics_are_per_session_and_not_restored(tmp_path):
    """TexLab 诊断按会话保存且返回副本，重新创建会话时不恢复旧诊断"""
    from management_portal.article_texlab_backend import TexLabSession
    first = TexLabSession("article_0123456789ab", tmp_path)
    first._diagnostics["file:///fixture.tex"] = [{"message": "fixture"}]
    snapshot = first.diagnostics()
    snapshot["file:///fixture.tex"][0]["message"] = "changed"
    assert first.diagnostics()["file:///fixture.tex"][0]["message"] == "fixture"
    restarted = TexLabSession("article_0123456789ab", tmp_path)
    assert restarted.diagnostics() == {} and restarted.process is None


@pytest.mark.parametrize("kind", ["review", "note"])
@pytest.mark.parametrize("unlink_fails", [False, True])
def test_paper_delete_removes_markdown_and_index_or_restores_index(monkeypatch, tmp_path, kind, unlink_fails):
    """删除论文评审或笔记时一并删除 Markdown 与索引，文件删除失败时索引保持原状"""
    identity_key = kind + "_id"
    identity = "review_0123456789ab" if kind == "review" else "note-fixture"
    neighbor_id = "review_abcdef012345" if kind == "review" else "note-neighbor"
    records = [{identity_key: identity, "paper_id": "paper_0123456789ab"}, {identity_key: neighbor_id, "paper_id": "paper_0123456789ab"}]
    for record in records:
        paper_state_store.append(kind + "s", record)
    directory = tmp_path / (kind + "s")
    directory.mkdir()
    target = directory / (identity + ".md")
    target.write_text("target")
    neighbor = directory / (neighbor_id + ".md")
    neighbor.write_text("neighbor")
    monkeypatch.setattr(paper_backend, "_ensure_data_root", lambda: None)
    monkeypatch.setattr(paper_backend, "_paper_by_id", lambda *_args: {"id": "paper_0123456789ab"})
    monkeypatch.setattr(paper_backend, "_paper_store_file", lambda *_args: directory)
    remove = getattr(paper_backend, "delete_" + kind)
    if unlink_fails:
        original_unlink = Path.unlink
        def failed_unlink(path, *args, **kwargs):
            if path == target:
                raise OSError("synthetic unlink failure")
            return original_unlink(path, *args, **kwargs)
        monkeypatch.setattr(Path, "unlink", failed_unlink)
        with pytest.raises(OSError, match="synthetic unlink"):
            remove({identity_key: identity}, "fixture")
        assert paper_state_store.records(kind + "s") == records
        assert target.read_text() == "target"
    else:
        remove({identity_key: identity}, "fixture")
        assert not target.exists()
        assert paper_state_store.records(kind + "s") == [records[1]]
    assert neighbor.read_text() == "neighbor"


def test_figure_extract_real_synthetic_pdf_rebuilds_metadata(figure_store, monkeypatch, tmp_path):
    """从 PDF 提取图表时重建元数据，图表获得不透明 ID 与序号且不留临时页面文件"""
    from tests.cases.test_paper_figure_contract import write_synthetic_axes_pdf
    paper, metadata = figure_store
    source = tmp_path / "synthetic.pdf"
    write_synthetic_axes_pdf(source)
    monkeypatch.setattr(paper_backend, "_local_pdf_for_paper", lambda *_args: source)
    monkeypatch.setattr(paper_backend, "_figures_dir", lambda *_args: tmp_path / "figures")
    result = paper_backend.extract_figures({"paper_id": paper["id"]}, "fixture")
    stored = json.loads(metadata.read_text())
    assert result["count"] == len(stored) == 1
    # A newly extracted figure gets an opaque identity that survives reordering; its position is the index.
    assert re.fullmatch(r"fig-[0-9a-f]{24}", stored[0]["figure_id"]) and stored[0]["index"] == 1
    assert (tmp_path / stored[0]["image_rel_path"]).is_file()
    assert not list((tmp_path / "figures/extracted").glob("_page*"))


def test_reference_registration_reuses_or_registers_and_returns_detail(monkeypatch):
    """登记参考文献时复用已登记论文，否则查元数据后登记，两种情况都返回论文详情"""
    paper = {"id": "paper_0123456789ab", "title": "Existing"}
    ref = {"ref_id": "fixture", "paper_id": "paper_abcdef012345", "title": "Reference", "registered_paper_id": paper["id"]}
    papers = [paper]
    monkeypatch.setattr(paper_backend, "_ref_context", lambda *_args: ({"id": ref["paper_id"]}, ref, [ref]))
    monkeypatch.setattr(paper_backend, "_all_papers", lambda: papers)
    monkeypatch.setattr(paper_backend, "_paper_public", lambda value: dict(value))
    monkeypatch.setattr(paper_backend, "get_paper_detail", lambda identity: {"id": identity})
    lookup = Mock(return_value={"title": "Resolved Reference", "source_url": "https://fixture.invalid"})
    monkeypatch.setattr(paper_backend, "_lookup_ref_metadata", lookup)
    register = Mock(return_value=paper)
    monkeypatch.setattr(paper_backend, "PAPER_STORE", SimpleNamespace(register=register))
    writes = Mock()
    monkeypatch.setattr(paper_backend, "_write_store_refs", writes)
    first = paper_backend.register_ref({"ref_id": "fixture"}, "fixture")
    assert first["paper"]["id"] == paper["id"] and first["detail"]["id"] == paper["id"]
    lookup.assert_not_called()
    register.assert_not_called()
    papers.clear()
    second = paper_backend.register_ref({"ref_id": "fixture", "paper_id": ref["paper_id"]}, "fixture")
    lookup.assert_called_once()
    register.assert_called_once()
    assert second["ref"]["registered_paper_id"] == paper["id"]
    assert writes.call_args.args[0] == ref["paper_id"]


def test_collector_job_listed_and_deleted_by_other_users_keeps_runtime_binding(monkeypatch):
    """其他用户可以查看和删除论文收集任务，创建者与 Resident、Lineage 绑定保持不变"""
    state = {"job_id": "fixture", "created_by": "alice", "status": "done", "resident_id": "resident_fixture", "lineage_id": "lineage_fixture"}
    monkeypatch.setattr(paper_backend, "_paper_collection_job_by_id", lambda *_args: dict(state))
    monkeypatch.setattr(paper_backend, "_all_paper_collection_jobs", lambda: [dict(state)])
    monkeypatch.setattr(paper_backend, "_set_paper_collection_job", lambda _identity, patch: state.update(patch) or dict(state))
    assert len(paper_backend.list_paper_collection_jobs("bob")["jobs"]) == 1
    assert state["resident_id"] == "resident_fixture" and state["lineage_id"] == "lineage_fixture"
    paper_backend.delete_paper_collection_job({"job_id": "fixture"}, "bob")
    assert state["deleted"] and state["status"] == "deleted"
    assert paper_backend.list_paper_collection_jobs("bob")["jobs"] == []
