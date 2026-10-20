from __future__ import annotations

import re
from typing import Any


MAX_RECALL_QUERY_CHARS = 500
MAX_RECALL_HITS = 20
MAX_RECALL_CONTEXT_CHARS = 4_000
MAX_RECALL_READ_CHARS = 32_000
DEFAULT_RECALL_HITS = 5
DEFAULT_RECALL_CONTEXT_CHARS = 600
DEFAULT_RECALL_READ_CHARS = 16_000

_QUERY_TOKEN_RE = re.compile(r"[\w@./:+-]{2,}", re.UNICODE)
_CJK_RE = re.compile(r"[\u3400-\u9fff]{3,}")


def _bounded_int(
    value: object,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def read_text_slice(
    content: str,
    *,
    offset: object = 0,
    limit: object = DEFAULT_RECALL_READ_CHARS,
) -> dict[str, Any]:
    text = str(content)
    try:
        clean_offset = int(offset)
    except (TypeError, ValueError) as exc:
        raise ValueError("Recall offset must be an integer") from exc
    if clean_offset < 0 or clean_offset > len(text):
        raise ValueError("Recall offset is outside the content range")
    clean_limit = _bounded_int(
        limit,
        default=DEFAULT_RECALL_READ_CHARS,
        minimum=1,
        maximum=MAX_RECALL_READ_CHARS,
    )
    next_offset = min(len(text), clean_offset + clean_limit)
    selected = text[clean_offset:next_offset]
    line_start = text.count("\n", 0, clean_offset) + 1
    line_end = line_start + selected.count("\n")
    return {
        "offset": clean_offset,
        "next_offset": next_offset,
        "total_chars": len(text),
        "line_start": line_start,
        "line_end": line_end,
        "eof": next_offset >= len(text),
        "content": selected,
    }


def _query_terms(query: str) -> tuple[str, ...]:
    folded = query.casefold()
    candidates = list(_QUERY_TOKEN_RE.findall(folded))
    for cjk_match in _CJK_RE.finditer(folded):
        token = cjk_match.group()
        candidates.extend(
            token[index:index + 2]
            for index in range(min(len(token) - 1, 16))
        )
    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        clean = candidate.strip("./:+-")
        if len(clean) < 2 or clean in seen:
            continue
        seen.add(clean)
        unique.append(clean)
        if len(unique) >= 24:
            break
    return tuple(unique)


def _occurrences(
    content: str,
    needle: str,
    *,
    limit: int,
) -> list[int]:
    positions: list[int] = []
    cursor = 0
    step = max(1, len(needle))
    while len(positions) < limit:
        position = content.find(needle, cursor)
        if position < 0:
            break
        positions.append(position)
        cursor = position + step
    return positions


def _snippet_bounds(
    content: str,
    *,
    position: int,
    match_length: int,
    context_chars: int,
) -> tuple[int, int]:
    radius = max(32, context_chars // 2)
    start = max(0, position - radius)
    end = min(len(content), position + max(1, match_length) + radius)
    nearby_line_start = content.rfind("\n", max(0, start - 160), start)
    if nearby_line_start >= 0:
        start = nearby_line_start + 1
    nearby_line_end = content.find("\n", end, min(len(content), end + 160))
    if nearby_line_end >= 0:
        end = nearby_line_end
    return start, end


def search_text(
    content: str,
    query: object,
    *,
    max_hits: object = DEFAULT_RECALL_HITS,
    context_chars: object = DEFAULT_RECALL_CONTEXT_CHARS,
) -> dict[str, Any]:
    """Search local text and return bounded, offset-addressable snippets."""

    text = str(content)
    clean_query = " ".join(str(query or "").split())
    if not clean_query:
        raise ValueError("Recall query required")
    if len(clean_query) > MAX_RECALL_QUERY_CHARS:
        raise ValueError(
            f"Recall query exceeds {MAX_RECALL_QUERY_CHARS} characters"
        )
    clean_hits = _bounded_int(
        max_hits,
        default=DEFAULT_RECALL_HITS,
        minimum=1,
        maximum=MAX_RECALL_HITS,
    )
    clean_context = _bounded_int(
        context_chars,
        default=DEFAULT_RECALL_CONTEXT_CHARS,
        minimum=64,
        maximum=MAX_RECALL_CONTEXT_CHARS,
    )
    folded_text = text.casefold()
    folded_query = clean_query.casefold()
    exact_positions = _occurrences(
        folded_text,
        folded_query,
        limit=max(clean_hits * 8, 32),
    )
    candidates: list[tuple[tuple[int, int, int], int, int, tuple[str, ...]]] = []
    match_mode = "exact_phrase"
    if exact_positions:
        candidates.extend(
            ((1, len(folded_query), -position), position, len(folded_query), (clean_query,))
            for position in exact_positions
        )
    else:
        match_mode = "query_terms"
        terms = _query_terms(clean_query)
        for term in terms:
            for position in _occurrences(
                folded_text,
                term,
                limit=max(clean_hits * 12, 48),
            ):
                scoring_radius = max(32, clean_context // 2)
                window_start = max(0, position - scoring_radius)
                window_end = min(
                    len(folded_text),
                    position + len(term) + scoring_radius,
                )
                window = folded_text[window_start:window_end]
                matched = tuple(
                    candidate for candidate in terms if candidate in window
                )
                occurrence_count = sum(window.count(item) for item in matched)
                candidates.append((
                    (len(matched), occurrence_count, len(term)),
                    position,
                    len(term),
                    matched,
                ))
        candidates.sort(key=lambda item: item[0], reverse=True)

    hits: list[dict[str, Any]] = []
    intervals: list[tuple[int, int]] = []
    for score, position, match_length, matched_terms in candidates:
        start, end = _snippet_bounds(
            text,
            position=position,
            match_length=match_length,
            context_chars=clean_context,
        )
        if any(start < prior_end and end > prior_start for prior_start, prior_end in intervals):
            continue
        intervals.append((start, end))
        snippet = text[start:end]
        line_start = text.count("\n", 0, start) + 1
        hits.append({
            "offset": start,
            "next_offset": end,
            "match_offset": position,
            "line_start": line_start,
            "line_end": line_start + snippet.count("\n"),
            "matched_terms": list(matched_terms),
            "score": {
                "matched_term_count": int(score[0]),
                "occurrence_count": int(score[1]),
            },
            "content": snippet,
        })
        if len(hits) >= clean_hits:
            break

    return {
        "query": clean_query,
        "match_mode": match_mode if hits else "none",
        "hit_count": len(hits),
        "max_hits": clean_hits,
        "context_chars": clean_context,
        "total_chars": len(text),
        "hits": hits,
    }
