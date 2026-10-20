"""Registration keeps an author list only when it reads as person names.

A TAU run uploaded a synthetic paper without a byline; the front matter
parser accepted body sentences split on double spaces as authors, because
2-6 words with letters was its only check, and the paper was registered with
"extracted text, structured experimental evidence, ..." as its authors.
"""
from __future__ import annotations

from management_portal import paper_backend

PROSE = (
    "TAU Synthetic Paper\n\n"
    "This synthetic paper tests extracted text  structured experimental evidence  "
    "and a simple visual explanation. The research  is evaluated on accuracy\n\n"
    "Abstract\nA short abstract.\n"
)
BYLINE = (
    "Dataflow Runtimes for Embodied Agents\n\n"
    "Ada Lovelace1  Charles Babbage*  and Jean-Pierre van der Berg\n\n"
    "Abstract\nA short abstract.\n"
)


def test_front_matter_takes_a_byline_but_not_body_prose():
    """论文登记从 PDF 首页取署名作者，不把正文句段当作作者"""
    assert paper_backend._paper_front_matter_metadata(PROSE)["authors"] == ""
    assert paper_backend._paper_front_matter_metadata(BYLINE)["authors"] == (
        "Ada Lovelace1, Charles Babbage*, Jean-Pierre van der Berg"
    )


def test_model_authors_that_are_not_names_are_dropped():
    """模型返回的作者不是人名形态时不写入论文作者"""
    prose = paper_backend._coerce_paper_registration_metadata({
        "authors": ["extracted text", "structured experimental evidence", "The research is evaluated on accuracy"],
    })
    assert prose["authors"] == ""
    labels = paper_backend._coerce_paper_registration_metadata({"authors": ["Method A", "Method B"]})
    assert labels["authors"] == ""
    assert paper_backend._paper_front_matter_metadata(
        "Synthetic Comparison Paper\n\nMethod A  Method B\n\nAbstract\nInvented scores."
    )["authors"] == ""
    names = paper_backend._coerce_paper_registration_metadata({"authors": ["Ada Lovelace", "J.-P. Sartre", "张三"]})
    assert names["authors"] == "Ada Lovelace, J.-P. Sartre, 张三"
