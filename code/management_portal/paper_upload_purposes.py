"""Paper's upload purposes; shared upload storage enforces these declarations."""


def _pdf(data: bytes, media_type: str) -> str:
    if not data.startswith(b"%PDF-"):
        raise ValueError("Paper PDF 必须是 PDF 文件")
    return "application/pdf"


PURPOSES = {
    "paper_pdf": {"max_bytes": 512 * 1024 * 1024, "validate": _pdf},
    "figure_input": {"max_bytes": 512 * 1024 * 1024},
}
