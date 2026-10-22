from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from management_portal import paper_backend
from tests.cases.registry_support import write_projects





def _draft_store(tmp_path: Path) -> paper_backend.PaperStore:
    store = paper_backend.PaperStore(tmp_path / "papers")
    store.ensure()
    return store


@pytest.mark.parametrize("outside_allowed_root", [False, True])
def test_registration_rejects_unavailable_local_pdf_before_creating_paper(tmp_path, outside_allowed_root):
    """无效的本地 PDF 不创建论文，也不启动摘要或消费上传草稿。"""
    store = _draft_store(tmp_path)
    source = tmp_path / "input.pdf"
    if outside_allowed_root:
        source.write_bytes(b"%PDF-1.7\nfixture")
    with (
        mock.patch.object(paper_backend, "PAPER_STORE", store),
        mock.patch.object(paper_backend, "ALLOWED_FILE_ROOTS", [store.root] if outside_allowed_root else [tmp_path]),
        mock.patch.object(store, "register") as register,
        mock.patch.object(paper_backend, "_start_summary_task") as summary,
        mock.patch.object(paper_backend, "_start_assets_task") as assets,
    ):
        with pytest.raises(ValueError, match="registration PDF is unavailable"):
            paper_backend.register_paper({"title": "Unavailable source", "pdf_path": str(source)}, "alice")
    register.assert_not_called()
    summary.assert_not_called()
    assets.assert_not_called()


def test_empty_summary_material_records_failure_without_calling_model_or_writing_summary(tmp_path):
    """空材料使摘要任务明确失败，不请求模型，不留下可复用的摘要文件。"""
    path = tmp_path / "summary.md"
    paper = {"id": "paper_012345abcdef", "title": "No source", "abstract": ""}
    with (
        mock.patch.object(paper_backend, "_paper_by_id", return_value=paper),
        mock.patch.object(paper_backend, "_paper_store_file", return_value=path),
        mock.patch.object(paper_backend, "_analysis_text", return_value="  "),
        mock.patch.object(paper_backend, "_pdf_text_for_paper", return_value="\n"),
        mock.patch.object(paper_backend, "_resident_reply") as reply,
        mock.patch.object(paper_backend, "_update_paper_run_record") as record,
    ):
        with pytest.raises(ValueError, match="requires an abstract"):
            paper_backend._run_summary_job(paper["id"], actor="alice", job_id="summary-source-test")
    reply.assert_not_called()
    assert not path.exists()
    assert record.call_args.args[1]["status"] == "error"
    assert record.call_args.args[1]["job_id"] == "summary-source-test"


def test_registration_pdf_upload_creates_owned_background_draft_contracts(tmp_path, subtests, isolated_portal_case):
    """上传 PDF 创建后台草稿并从正文和文件名提取元数据"""
    with subtests.test(contract='test_registration_pdf_upload_creates_owned_background_draft'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = _draft_store(tmp_path)
        with mock.patch.object(paper_backend, 'PAPER_STORE', store), mock.patch('management_portal.business_tasks.start', return_value=True) as start:
            draft = paper_backend.create_paper_registration_draft({'filename': 'example.pdf', 'data_bytes': b'%PDF-1.7\nregistration upload'}, 'alice')
            assert draft['status'] == 'queued'
            assert draft['upload_id'].startswith('paper-upload-')
            assert 'actor' not in draft
            assert paper_backend._paper_registration_draft_pdf(draft['upload_id']).read_bytes().startswith(b'%PDF')
            start.assert_called_once()
            # ISA admission governs access; the draft retains its original identity.
            assert paper_backend.get_paper_registration_draft(draft['upload_id'], 'bob') == draft
    with subtests.test(contract='test_registration_metadata_uses_body_title_and_filename_arxiv_id'), isolated_portal_case() as case_path:
        tmp_path = case_path
        pdf_path = tmp_path / 'paper.pdf'
        pdf_path.write_bytes(b'%PDF-1.7\nfixture')
        pdf_text = '\nRedwood: A Scalable Architecture for Modern AI Workloads\n\nAda Author        Ben Builder\n\nAbstract\nThis paper presents a scalable accelerator architecture for modern AI workloads.\n\n1 Introduction\nPaper body.\n'
        with mock.patch.object(paper_backend, '_pdf_document_properties', return_value={'title': '2608.26418v2.pdf'}):
            metadata = paper_backend._deterministic_paper_registration_metadata(pdf_path, filename='2608.26418v2.pdf', pdf_text=pdf_text)
            metadata_without_text = paper_backend._deterministic_paper_registration_metadata(pdf_path, filename='2608.26418v2.pdf', pdf_text='')
        assert metadata['title'] == 'Redwood: A Scalable Architecture for Modern AI Workloads'
        assert metadata['arxiv'] == '2608.26418v2'
        assert metadata_without_text['title'] == ''
        assert metadata_without_text['arxiv'] == '2608.26418v2'
    with subtests.test(contract='test_registration_metadata_prefers_pdf_arxiv_id_over_filename'), isolated_portal_case() as case_path:
        tmp_path = case_path
        pdf_path = tmp_path / 'paper.pdf'
        pdf_path.write_bytes(b'%PDF-1.7\nfixture')
        with mock.patch.object(paper_backend, '_pdf_document_properties', return_value={}):
            metadata = paper_backend._deterministic_paper_registration_metadata(pdf_path, filename='2608.26418v2.pdf', pdf_text='arXiv:2608.12345v3\n\nA Paper Title from the PDF Body')
        assert metadata['arxiv'] == '2608.12345v3'


def test_registration_draft_extracts_editable_metadata(
    tmp_path: Path,
) -> None:
    """论文登记草稿提取可编辑元数据并记录用量"""
    store = _draft_store(tmp_path)
    upload_id = "paper-upload-012345abcdef"
    draft_dir = store.root / "_incoming" / "registration-drafts" / upload_id
    draft_dir.mkdir(parents=True)
    (draft_dir / "paper.pdf").write_bytes(b"%PDF-1.7\nfixture")
    (draft_dir / "draft.json").write_text(
        json.dumps({
            "upload_id": upload_id,
            "filename": "fixture.pdf",
            "actor": "alice",
            "status": "queued",
            "created_at": 1,
            "updated_at": 1,
            "metadata": {},
        }),
        encoding="utf-8",
    )
    ai_metadata = {
        "title": "Extracted Paper",
        "authors": "A. Author, B. Author",
        "venue": "ISCA",
        "year": "2026",
        "abstract": "Extracted abstract.",
        "tags": ["accelerator", "simulation"],
        "workload": ["DNN inference"],
        "platform": ["many-core"],
        "architecture": ["dataflow"],
        "optimization": ["mapping"],
        "doi": "10.1000/example",
        "arxiv": "2607.01088",
    }
    with (
        mock.patch.object(paper_backend, "PAPER_STORE", store),
        mock.patch.object(
            paper_backend,
            "_extract_pdf_text_file",
            return_value="Paper body",
        ),
        mock.patch.object(
            paper_backend,
            "_deterministic_paper_registration_metadata",
            return_value={key: [] if isinstance(value, list) else "" for key, value in ai_metadata.items()},
        ),
        mock.patch.object(
            paper_backend,
            "_ai_paper_registration_metadata",
            return_value=(ai_metadata, {"model": "fixture"}),
        ) as extract,
    ):
        result = paper_backend._run_paper_registration_draft(upload_id)

    assert result["status"] == "done"
    assert result["metadata"] == ai_metadata
    assert result["usage"] == {"model": "fixture"}
    assert extract.call_args.kwargs["actor"] == "alice"


def test_register_consumes_uploaded_pdf_and_draft_metadata(tmp_path, subtests, isolated_portal_case):
    """登记论文消费上传草稿并保留显式清空的元数据"""
    variants = [(status, value) for status in ("done", "running") for value in ("omitted", "", [], None)]
    for draft_status, author_value in variants:
        with subtests.test(draft_status=draft_status, author_value=author_value), isolated_portal_case() as case_path:
            tmp_path = case_path
            store = _draft_store(tmp_path)
            upload_id = "paper-upload-fedcba654321"
            draft_dir = store.root / "_incoming" / "registration-drafts" / upload_id
            draft_dir.mkdir(parents=True)
            (draft_dir / "paper.pdf").write_bytes(b"%PDF-1.7\nfixture paper")
            (draft_dir / "draft.json").write_text(
                json.dumps({
                    "upload_id": upload_id,
                    "filename": "fixture.pdf",
                    "actor": "alice",
                    "status": draft_status,
                    "created_at": 1,
                    "updated_at": 1,
                    "metadata": {
                        "title": "Uploaded Paper",
                        "authors": "A. Author",
                        "venue": "MICRO",
                        "year": "2025",
                        "abstract": "A paper uploaded directly.",
                        "tags": ["architecture"],
                        "doi": "10.1000/uploaded",
                    },
                }),
                encoding="utf-8",
            )
            resource_service = mock.Mock()
            resource_service.new_id.return_value = "paper_012345abcdef"
            resource_service.validate_id.return_value = None
            with (
                mock.patch.object(paper_backend, "PAPER_STORE", store),
                mock.patch.object(
                    paper_backend,
                    "ALLOWED_FILE_ROOTS",
                    [tmp_path, store.root],
                ),
                mock.patch.object(
                    paper_backend,
                    "get_resource_service",
                    return_value=resource_service,
                ),
                mock.patch.object(paper_backend, "_start_summary_task") as summary,
                mock.patch.object(paper_backend, "_start_assets_task") as assets,
            ):
                payload = {"registration_upload_id": upload_id}
                if author_value != "omitted":
                    payload.update(authors=author_value, tags=[], doi="", abstract="")
                detail = paper_backend.register_paper(payload, "alice")

            paper = detail["paper"]
            assert paper["id"] == "paper_012345abcdef"
            assert paper["title"] == "Uploaded Paper"
            assert paper["authors"] == ("A. Author" if author_value == "omitted" else "")
            assert paper["abstract"] == ("A paper uploaded directly." if author_value == "omitted" else "")
            assert paper["tags"] == (["architecture"] if author_value == "omitted" else [])
            assert paper["has_pdf"] is True
            assert not draft_dir.exists()
            manifest_path, manifest = store.manifest_for_paper_id(paper["id"])
            assert (manifest_path.parent / manifest["files"]["pdf"]).read_bytes().startswith(
                b"%PDF"
            )
            assert manifest["identifiers"].get("doi", "") == ("10.1000/uploaded" if author_value == "omitted" else "")
            summary.assert_called_once_with(paper["id"], actor="alice")
            assets.assert_called_once_with(paper["id"], actor="alice")


def test_registration_upload_reuses_pdf_validation_contracts(subtests, isolated_portal_case):
    """论文上传拒绝非 PDF 且元数据与分类经登记流程补全"""
    with subtests.test(contract='test_registration_upload_reuses_pdf_validation'), isolated_portal_case() as case_path:
        with pytest.raises(ValueError, match='look like a PDF'):
            paper_backend._validated_pdf_upload({'filename': 'bad.pdf', 'data_bytes': b'not a pdf'})
    with subtests.test(contract='test_front_matter_fallback_ignores_arxiv_header'), isolated_portal_case() as case_path:
        metadata = paper_backend._paper_front_matter_metadata('\n            Sequence to Sequence Learning\n                with Neural Networks\n\n        Ilya Sutskever        Oriol Vinyals        Quoc V. Le\narXiv:1409.3215v3 [cs.CL] 14 Dec 2014\n\nGoogle                 Google                 Google\n\nAbstract\nPaper abstract.\n')
        assert metadata == {'title': 'Sequence to Sequence Learning with Neural Networks', 'authors': 'Ilya Sutskever, Oriol Vinyals, Quoc V. Le'}
    with subtests.test(contract='test_local_registration_classification_covers_embodied_systems'), isolated_portal_case() as case_path:
        result = paper_backend._heuristic_paper_registration_classification({'title': 'Zetta: An Efficient Closed-Loop Embodied Harness', 'abstract': 'A self-evolving embodied agent for physical robot execution with online adaptation, a frozen base policy, and runtime recovery skills.'}, 'The robotic system improves policy inference speed on physical tasks.', project_catalog=[{'id': 'OSForAgent', 'description': 'Agentic operating systems'}, {'id': 'RobotWorker', 'description': 'Robot manipulation'}])
        assert result['session'] == 'OSForAgent'
        assert result['tags'][:2] == ['embodied intelligence', 'robotics']
        assert 'robot control' in result['workload']
        assert result['platform'] == ['robot']
        assert 'closed-loop agent' in result['architecture']
        assert 'agent harness' in result['architecture']
        assert 'online self-improvement' in result['optimization']
        assert 'runtime recovery' in result['optimization']
    with subtests.test(contract='test_registration_metadata_is_one_task_for_its_role_resident'), isolated_portal_case() as case_path:
        resident_result = {'message': json.dumps({'title': 'Fixture Paper', 'authors': ['A. Author'], 'venue': 'arXiv', 'year': '2026', 'session': 'OSForAgent', 'abstract': 'Fixture abstract.', 'tags': ['agents'], 'workload': ['agent inference'], 'platform': [], 'architecture': ['agent runtime'], 'optimization': [], 'doi': '', 'arxiv': '2601.00001'}), 'usage': {'total_tokens': 123}}
        with mock.patch.object(paper_backend, '_paper_registration_project_catalog', return_value=[{'id': 'OSForAgent', 'description': 'Agent OS'}]), mock.patch('management_portal.paper_projection.turn', return_value=resident_result) as run_resident:
            metadata, usage = paper_backend._ai_paper_registration_metadata('Fixture PDF text', actor='alice')
        assert metadata['session'] == 'OSForAgent'
        assert metadata['authors'] == 'A. Author'
        # The usage is the role Resident's own; it runs with its own Provider and model.
        assert usage == {'total_tokens': 123}
        run_resident.assert_called_once()
        kwargs = run_resident.call_args.kwargs
        assert kwargs['actor'] == 'alice'
        assert kwargs['resident_scope'] == 'paper:registration-metadata'
        assert 'Fixture PDF text' in kwargs['prompt']
        assert not {'provider', 'runtime_options', 'lineage_scope'} & set(kwargs)
    with subtests.test(contract='test_ccf_registration_preserves_related_projects'), isolated_portal_case() as case_path:
        with mock.patch.object(paper_backend, 'register_paper', return_value={'paper': {'id': 'paper_fixture'}}) as register:
            paper_backend.register_ccf_dblp_paper({'paper': {'title': 'Fixture Paper'}, 'project_ids': ['OSForAgent'], 'session': 'OSForAgent'}, 'alice')
        registered_payload = register.call_args.args[0]
        assert registered_payload['project_ids'] == ['OSForAgent']
        assert registered_payload['session'] == 'OSForAgent'


def test_identifier_metadata_is_preserved_while_model_enriches_classification(
    tmp_path: Path,
) -> None:
    """模型补充论文分类时保留标识来源的权威元数据"""
    store = _draft_store(tmp_path)
    upload_id = "paper-upload-abcdef012345"
    draft_dir = store.root / "_incoming" / "registration-drafts" / upload_id
    draft_dir.mkdir(parents=True)
    (draft_dir / "paper.pdf").write_bytes(b"%PDF-1.7\nfixture")
    (draft_dir / "draft.json").write_text(
        json.dumps({
            "upload_id": upload_id,
            "filename": "fixture.pdf",
            "actor": "alice",
            "status": "queued",
            "created_at": 1,
            "updated_at": 1,
            "metadata": {},
        }),
        encoding="utf-8",
    )
    base = {
        "title": "Fallback title",
        "authors": "Fallback author",
        "venue": "",
        "year": "2025",
        "session": "",
        "abstract": "Fallback abstract.",
        "tags": [],
        "workload": [],
        "platform": [],
        "architecture": [],
        "optimization": [],
        "doi": "",
        "arxiv": "2501.01234",
    }
    authoritative = {
        "title": "Authoritative title",
        "authors": "A. Author",
        "venue": "arXiv",
        "year": "2025",
        "abstract": "Authoritative abstract.",
        "arxiv": "2501.01234",
    }
    with (
        mock.patch.object(paper_backend, "PAPER_STORE", store),
        mock.patch.object(
            paper_backend,
            "_extract_pdf_text_file",
            return_value="Paper body",
        ),
        mock.patch.object(
            paper_backend,
            "_deterministic_paper_registration_metadata",
            return_value=base,
        ),
        mock.patch.object(
            paper_backend,
            "_identifier_paper_registration_metadata",
            return_value=authoritative,
        ),
        mock.patch.object(
            paper_backend,
            "_ai_paper_registration_metadata",
            return_value=({
                "title": "Model should not replace this title",
                "session": "OSForAgent",
                "tags": ["embodied intelligence"],
                "workload": ["robot learning"],
                "platform": ["robot"],
                "architecture": ["closed-loop agent"],
                "optimization": ["self-improvement"],
            }, {"total_tokens": 42}),
        ) as ai,
    ):
        result = paper_backend._run_paper_registration_draft(upload_id)

    assert result["status"] == "done"
    assert result["metadata"]["title"] == "Authoritative title"
    assert result["metadata"]["session"] == "OSForAgent"
    assert result["metadata"]["tags"] == ["embodied intelligence"]
    assert result["metadata"]["workload"] == ["robot learning"]
    assert result["metadata"]["architecture"] == ["closed-loop agent"]
    assert result["usage"] == {"total_tokens": 42}
    assert result["warnings"] == []
    ai.assert_called_once()
    assert "Known authoritative metadata" in ai.call_args.args[0]


def test_registering_a_paper_already_held_relates_it_to_the_project_without_rerunning_its_assets(
    tmp_path, isolated_portal_case,
):
    """重复登记已有论文仅补充项目关联且不重跑产物"""
    with isolated_portal_case() as case_path:
        write_projects({"projects": [{"id": "AI员工"}, {"id": "RobotCompany"}]})
        store = _draft_store(case_path)
        resource_service = mock.Mock()
        resource_service.new_id.return_value = "paper_0123456789ab"
        resource_service.validate_id.return_value = None
        with (
            mock.patch.object(paper_backend, "PAPER_STORE", store),
            mock.patch.object(paper_backend, "ALLOWED_FILE_ROOTS", [case_path, store.root]),
            mock.patch.object(paper_backend, "get_resource_service", return_value=resource_service),
            mock.patch.object(paper_backend, "_start_summary_task") as summary,
            mock.patch.object(paper_backend, "_start_assets_task") as assets,
        ):
            first = paper_backend.register_paper(
                {"title": "Held Paper", "doi": "10.1000/held", "project_ids": ["AI员工"]}, "alice",
            )["paper"]
            summary.reset_mock()
            assets.reset_mock()
            again = paper_backend.register_paper(
                {"title": "Held Paper", "doi": "10.1000/held", "project_ids": ["RobotCompany"]}, "alice",
            )["paper"]
        assert again["id"] == first["id"]
        assert {"AI员工", "RobotCompany"} <= set(again["project_ids"])
        summary.assert_not_called()
        assets.assert_not_called()


def test_a_paper_is_related_only_to_registered_projects(tmp_path, isolated_portal_case):
    """论文只关联已登记的 Project

    按会议与 project_ids 推导关联时，只保留已登记的 Project；update_paper 写回时去掉指向未登记 Project 的关联。

    :importance: P1
    :design: decision_528bf9985705
    """
    # Collection jobs passed journals and topics such as "Science Robotics" as the
    # paper's project; 74 papers were related to Projects that never existed (sample_admin 2026-10-13).
    with isolated_portal_case() as case_path:
        write_projects({"projects": [{"id": "AI员工"}]})
        store = _draft_store(case_path)
        resource_service = mock.Mock()
        resource_service.new_id.return_value = "paper_0123456789ab"
        resource_service.validate_id.return_value = None
        with (
            mock.patch.object(paper_backend, "PAPER_STORE", store),
            mock.patch.object(paper_backend, "ALLOWED_FILE_ROOTS", [case_path, store.root]),
            mock.patch.object(paper_backend, "get_resource_service", return_value=resource_service),
            mock.patch.object(paper_backend, "_start_summary_task"),
            mock.patch.object(paper_backend, "_start_assets_task"),
        ):
            paper = paper_backend.register_paper({
                "title": "Journal Paper", "doi": "10.1000/journal", "session": "Science Robotics",
                "project_ids": ["AI员工", "ROSProxy"],
            }, "alice")["paper"]
            manifest_path, manifest = store.manifest_for_paper_id(paper["id"])
            assert [item["project_id"] for item in manifest["relations"]["projects"]] == ["AI员工"]

            # A relation written before the check is dropped by the next write.
            manifest["relations"]["projects"].append({"project_id": "ACLinkBoard", "relation": "relevant"})
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            updated = store.update_paper(paper["id"], {"title": "Journal Paper (revised)"})
        assert updated["project_ids"] == ["AI员工"]
