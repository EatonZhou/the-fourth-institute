import pytest
from management_portal.paper_resident import normalize_task_result

@pytest.mark.parametrize('tail,status', [('', 'missing'), ('\n<!-- PAPER_SOURCE_EVIDENCE -->\n```json\nnot-json\n```','invalid'), ('\n<!-- PAPER_SOURCE_EVIDENCE -->\n```json\n{"files":["/paper.txt"],"locations":["Section 2"]}\n```','recorded')])
def test_evidence_never_blocks_paid_markdown(tail,status):
    body,usage=normalize_task_result('## Method\nActual findings'+tail,{'material_snapshot':{'sha256':'a'*64}},markdown=True)
    assert body=='## Method\nActual findings'
    assert usage['source_evidence_status']==status

def test_legacy_latex_bad_escape_is_recovered_without_damaging_valid_escapes():
    text=r'{"markdown":"## Method\nFormula \(x\) and \\pi", "source_evidence":"Section 2"}'
    body,usage=normalize_task_result(text,{},markdown=True)
    assert body.startswith('## Method\n') and '\\(x\\)' in body and '\\pi' in body

def test_small_schema_is_unchanged():
    text='{"score":4,"source_evidence":"Section 2"}'
    assert normalize_task_result(text,{},markdown=False)[0]==text
