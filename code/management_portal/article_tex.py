"""Article names for the shared TeX source primitives."""
from management_common.latex_source import (
    _read_text,
    _strip_tex_comments,
    _balanced_argument,
    _tex_macros,
    _plain_tex,
    extract_title as extract_article_title,
    title_fingerprint,
    _safe_relative_path,
)
