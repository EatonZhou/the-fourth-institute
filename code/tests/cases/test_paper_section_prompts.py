"""Section-first prompts remain compatible with saved documents and readers."""
import re
from management_portal import paper_reading_jobs as jobs
from management_portal.paper_analysis_view import analysis_view


def test_section_first_prompts_and_document_round_trip(monkeypatch, tmp_path):
    paper = {'id': 'paper_test', 'title': 'Fixture title', 'abstract': 'Fixture abstract'}
    analysis = '\n\n'.join('## ' + name + '\nEvidence.' for name in jobs.DEEP_READ_SECTIONS)
    exp = '\n\n'.join('## ' + name + '\nEvidence.' for name in jobs.EXP_SECTIONS)
    prompt = jobs._deep_read_prompt(paper, 'Full text')
    assert not re.search(r'^# ', prompt, re.M)
    assert '正文直接从第一个二级小节开始' in prompt
    assert '- Title: Fixture title' in prompt
    jobs._require_artifact_sections(analysis, jobs.DEEP_READ_SECTIONS, 'Deep Read')
    document = jobs._deep_read_analysis_document(paper, analysis, {})
    viewed = analysis_view(document, None, None)
    assert viewed['body'].startswith('## ')
    for name in jobs.DEEP_READ_SECTIONS:
        assert name in viewed['body']
    prompts = []
    monkeypatch.setattr(jobs, '_analysis_text', lambda *a, **kw: analysis)
    monkeypatch.setattr(jobs, '_pdf_text_for_paper', lambda *a: 'Full text')
    monkeypatch.setattr(jobs, '_resident_reply', lambda prompt, **kw: (prompts.append(prompt) or exp, {}))
    body, usage = jobs._generate_exp_extraction(paper, actor='fixture')
    assert not re.search(r'^# ', prompts[0], re.M)
    assert '- Title: Fixture title' in prompts[0]
    jobs._require_artifact_sections(body, jobs.EXP_SECTIONS, 'Exp')
    monkeypatch.setattr(jobs, '_paper_store_file', lambda *a: tmp_path / 'exp.md')
    path = jobs._write_exp_markdown(paper, body, usage, actor='fixture')
    # The existing detail reader reads the document, not a required first H1.
    from management_portal.paper_store import _read_text_preview
    text = _read_text_preview(path, limit=120000)
    assert '## 实验目标' in text and not re.search(r'^# ', text, re.M)
