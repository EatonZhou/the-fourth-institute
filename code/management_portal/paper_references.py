"""Reference lists in PDF text: finding the block, splitting entries and guessing each title."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable, Pattern


def _looks_like_bad_ref_title(title: str) -> bool:
    value = str(title or "").strip()
    if not value:
        return True
    if re.fullmatch(r"\d+", value):
        return True
    if re.fullmatch(r"(?:pp\.?\s*)?\d+\s*[-–]\s*\d+", value, re.I):
        return True
    if re.fullmatch(r"10\.\S+", value, re.I):
        return True
    if re.search(r"\bdoi\b|https?\s*:\s*//|www\.", value, re.I):
        return True
    if re.fullmatch(r"(?:19|20)\d{2}[a-z]?", value):
        return True
    if re.match(r"^(?:doi|URL|https?://|//)\b", value, re.I):
        return True
    if re.match(r"^(?:IEEE|ACM|Springer|PMLR),?\s+(?:19|20)\d{2}\b", value, re.I):
        return True
    if re.fullmatch(r"(?:The\s+)?(?:VLSI\s+journal|Journal|Proceedings|Conference|Transactions|Lecture Notes|Science|Nature)", value, re.I):
        return True
    if re.match(r"^(?:Proceedings of|Conference on|International Conference|IEEE(?:/CVF)?|ACM|Springer|PMLR|Journal of|Transactions on)\b", value, re.I):
        return True
    if re.match(r"^(?:ISBN|ISSN)\b", value, re.I):
        return True
    return False


def _parse_references_from_text(text: str, limit: int = 2000) -> list[str]:
    block = _reference_block_from_text(text or "")
    # An explicit, scored bibliography heading establishes the section even in
    # short conference papers. The heading-free heuristic still requires five.
    minimum_entries = 1 if block else 5
    if not block:
        source = text or ""
        tail_start = int(len(source) * 0.45)
        tail = source[tail_start:]
        for marker_match in re.finditer(r"\n\s*(?:\[1\]|1\.|1\])\s+", tail):
            start = tail_start + marker_match.start()
            probe = source[start : start + 5000]
            if (
                re.search(r"\n\s*(?:\[2\]|2\.|2\])\s+", probe)
                and re.search(r"\n\s*(?:\[3\]|3\.|3\])\s+", probe)
                and re.search(r"\b(?:Preprint|arXiv|Proceedings|Conference|Journal|Nature|Science|Neural Information Processing Systems|doi|https?://)\b", probe, re.I)
            ):
                block = source[start:]
                break
    if not block:
        return []
    block = _cut_reference_block(block)
    lines = _decolumnize_reference_lines(list(block.splitlines()))
    broad_marker = re.compile(r"^(?:\[\d{1,3}\]|\d{1,2}\.|\d{1,3}\])\s+")
    first_marker = next((str(line or "").strip() for line in lines[:40] if broad_marker.match(str(line or "").strip())), "")
    first_marker_idx = next((idx for idx, line in enumerate(lines[:40]) if broad_marker.match(str(line or "").strip())), None)
    first_author_idx = next((idx for idx, line in enumerate(lines[:40]) if _looks_like_unnumbered_ref_start(str(line or "").strip())), None)
    entries = []
    current = []
    if first_marker_idx is None or (first_author_idx is not None and first_author_idx < first_marker_idx):
        fallback = _parse_unnumbered_reference_lines(lines)
        return fallback[:limit] if len(fallback) >= minimum_entries else []
    if first_marker.startswith("["):
        marker = re.compile(r"^\[\d{1,3}\]\s+")
        marker_token = re.compile(r"^\[\d{1,3}\](?:\s|$)")
    elif re.match(r"^\d{1,3}\]", first_marker):
        marker = re.compile(r"^\d{1,3}\]\s+")
        marker_token = re.compile(r"^\d{1,3}\](?:\s|$)")
    else:
        marker = re.compile(r"^\d{1,3}\.\s+")
        marker_token = re.compile(r"^\d{1,3}\.(?:\s|$)")
    # A long bibliography with detached labels is not a short bibliography.
    # Keep its existing unnumbered fallback when too few complete entries parse.
    if sum(bool(marker_token.match(str(line).strip())) for line in lines) >= 5:
        minimum_entries = max(minimum_entries, 5)
    for line in lines:
        line = line.replace("\x0c", " ").strip()
        if not line:
            continue
        if _is_reference_page_noise(line):
            continue
        if re.search(r"\bQuestion:\s+Does the paper\b", line, re.I):
            break
        if marker.match(line) and not _looks_like_numbered_reference_line(line):
            continue
        if marker.match(line) and current:
            entries.append(" ".join(current).strip())
            current = [line]
        elif marker.match(line):
            current = [line]
        elif not current:
            continue
        elif _reference_entry_ended(" ".join(current)) and not _looks_like_reference_continuation(line):
            continue
        else:
            current.append(line)
    if current:
        entries.append(" ".join(current).strip())
    entries = _sort_numbered_reference_entries(entries)
    cleaned = [_trim_reference_entry(item) for item in entries if len(item) > 20]
    cleaned = [item for item in cleaned if len(item) > 20 and not _looks_like_reference_fragment(item)][:limit]
    if len(cleaned) >= minimum_entries:
        return cleaned
    fallback = _parse_unnumbered_reference_lines(lines)
    return fallback[:limit] if len(fallback) >= minimum_entries else []


def _sort_numbered_reference_entries(entries: list[str]) -> list[str]:
    numbered: list[tuple[int, int, str]] = []
    for idx, entry in enumerate(entries):
        match = re.match(r"^\s*(?:\[(\d{1,4})\]|(\d{1,4})[.\]])\s+", str(entry or ""))
        if not match:
            return entries
        numbered.append((int(match.group(1) or match.group(2) or 0), idx, entry))
    if len(numbered) < 3:
        return entries
    ordered = sorted(numbered, key=lambda item: (item[0], item[1]))
    numbers = [item[0] for item in ordered]
    if len(set(numbers)) != len(numbers):
        return entries
    return [item[2] for item in ordered]


def _reference_block_from_text(text: str) -> str:
    source = str(text or "")
    if not source.strip():
        return ""
    line_re = re.compile(r"(?m)^.*$")
    heading_re = re.compile(
        r"(?i)(?:(?:\d+(?:\.\d+)*\.?\s+)?(?:References|Bibliography|R\s*E\s*F\s*E\s*R\s*E\s*N\s*C\s*E\s*S))[:：]?"
    )
    candidates: list[tuple[int, int, str]] = []
    for line_match in line_re.finditer(source):
        heading_line = line_match.group(0)
        if not heading_line.strip():
            continue
        heading_match = None
        for candidate in heading_re.finditer(heading_line):
            prefix = heading_line[: candidate.start()]
            suffix = heading_line[candidate.end():]
            if prefix.strip() and not re.search(r"\s{6,}$", prefix):
                continue
            if suffix.strip() and not re.match(r"\s{6,}", suffix):
                continue
            heading_match = candidate
            break
        if not heading_match:
            continue
        line_end = source.find("\n", line_match.start())
        if line_end < 0:
            continue
        inline_right = ""
        inline_match = re.search(r"\s{3,}((?:\[\d{1,3}\]|\d{1,3}[.\]])\s+\S.*)$", heading_line[heading_match.end():])
        if inline_match:
            inline_right = inline_match.group(1).strip() + "\n"
        after_heading = source[line_end + 1 :]
        blocks_to_score = [inline_right + after_heading]
        if heading_match.start() >= 40 and heading_line[: heading_match.start()].strip():
            right_only = _right_column_block_until_page_break(after_heading)
            if right_only.strip():
                blocks_to_score.append(inline_right + right_only)
        for raw_block in blocks_to_score:
            block = _cut_reference_block(raw_block)
            score = _score_reference_block(block[:24000])
            if score >= 7:
                candidates.append((score, line_match.start() + heading_match.start(), block))
    if not candidates:
        return ""
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return candidates[0][2]


def _right_column_block_until_page_break(text: str) -> str:
    first_page, sep, rest = str(text or "").partition("\f")
    right_lines: list[str] = []
    for line in first_page.splitlines():
        raw = str(line or "").rstrip()
        if not raw.strip():
            right_lines.append("")
            continue
        split = re.match(r"^(.*?\S)\s{6,}(\S.*)$", raw)
        if split:
            right_lines.append(split.group(2).strip())
        elif re.match(r"^\s{32,}\S", raw):
            right_lines.append(raw.strip())
    joined = "\n".join(right_lines).strip()
    if sep:
        return f"{joined}\n{sep}{rest}" if joined else f"{sep}{rest}"
    return joined


def _cut_reference_block(block: str) -> str:
    section_cut = re.search(
        r"\n\s*(?:(?:[A-Z]|\d+(?:\.\d+)*)\.\s+)?(?:Supplementary\s+Related\s+Work|Appendix|APPENDIX|Acknowledg(?:e)?ments?|Author\s+Contributions?)\b",
        block,
    )
    page_appendix_cut = re.search(
        r"\n\s*\x0c?[^\n]{0,120}\s+\d+\s*\n\s*[A-Z]\s+(?:Detailed|Additional|Ablation|Experimental|Implementation|Proof|Reproducibility|Limitations|Attention|Hyperparameter|More|Benchmark|Dataset|Training|Evaluation)\b[^\n]{0,120}\n",
        block,
    )
    if page_appendix_cut and (not section_cut or page_appendix_cut.start() < section_cut.start()):
        section_cut = page_appendix_cut
    return block[: section_cut.start()] if section_cut else block


def _score_reference_block(block: str) -> int:
    lines = [line.strip() for line in str(block or "").splitlines() if line.strip()]
    if not lines:
        return 0
    numbered = sum(1 for line in lines[:220] if re.match(r"^(?:\[\d{1,3}\]|\d{1,3}[.\]])\s+\S", line))
    unnumbered = sum(1 for line in lines[:220] if _looks_like_unnumbered_ref_start(line) or _looks_like_reference_first_line(line))
    signals = len(re.findall(r"\b(?:arXiv|doi|URL|Proceedings|Conference|Journal|Transactions|Advances|Science|Nature|IEEE|ACM|PMLR|Springer)\b", "\n".join(lines[:220]), re.I))
    first_ref_idx = next(
        (
            idx
            for idx, line in enumerate(lines[:35])
            if re.match(r"^(?:\[\d{1,3}\]|\d{1,3}[.\]])\s+\S", line)
            or _looks_like_unnumbered_ref_start(line)
            or _looks_like_reference_first_line(line)
        ),
        None,
    )
    if first_ref_idx is None:
        return 0
    score = numbered * 7 + unnumbered * 3 + min(signals, 80)
    if first_ref_idx > 10:
        score -= 80
    if re.search(r"\b(?:thank|acknowledg|insightful comments|helpful discussion)\b", "\n".join(lines[:20]), re.I):
        score -= 30
    return score


def _decolumnize_reference_lines(lines: list[str]) -> list[str]:
    left: list[str] = []
    right: list[str] = []
    split_count = 0
    for line in lines:
        raw_line = str(line or "").rstrip()
        if re.match(r"^\s{32,}\S", raw_line):
            left.append("")
            right.append(raw_line.strip())
            split_count += 1
            continue
        text = raw_line.strip()
        if not text:
            left.append("")
            right.append("")
            continue
        marker_split = re.search(r"\s{3,}((?:\[\d{1,3}\]|\d{1,3}[.\]])\s+\S.*)$", text)
        if marker_split and marker_split.start() >= 24:
            left.append(text[: marker_split.start()].strip())
            right.append(marker_split.group(1).strip())
            split_count += 1
            continue
        match = re.match(r"^(.*?\S)\s{6,}(\S.*)$", text)
        if match:
            left.append(match.group(1).strip())
            right.append(match.group(2).strip())
            split_count += 1
        else:
            left.append(text)
            right.append("")
    if split_count < max(8, len([line for line in lines if str(line or "").strip()]) // 5):
        return lines
    return [line for line in left if line.strip()] + [line for line in right if line.strip()]


def _parse_unnumbered_reference_lines(lines: list[str]) -> list[str]:
    entries: list[str] = []
    current: list[str] = []
    expanded_lines: list[str] = []
    for line in lines:
        expanded_lines.extend(_split_inline_unnumbered_reference_starts(str(line or "")))
    for line in expanded_lines:
        text = str(line or "").replace("\x0c", " ").strip()
        if not text:
            continue
        if _is_reference_page_noise(text):
            if current and _reference_entry_ended(" ".join(current)):
                entries.append(" ".join(current).strip())
                current = []
            continue
        if re.search(r"\bQuestion:\s+Does the paper\b", text, re.I):
            break
        starts_entry = _looks_like_unnumbered_ref_start(text)
        current_text = " ".join(current)
        if current and starts_entry and (_reference_entry_ended(current_text) or _looks_like_completed_reference_for_split(current_text)):
            entries.append(" ".join(current).strip())
            current = [text]
        elif not current:
            if starts_entry or _looks_like_reference_first_line(text):
                current = [text]
        elif _reference_entry_ended(" ".join(current)) and not _looks_like_reference_continuation(text):
            entries.append(" ".join(current).strip())
            current = [text] if starts_entry or _looks_like_reference_first_line(text) else []
        else:
            current.append(text)
    if current:
        entries.append(" ".join(current).strip())
    return [entry for entry in (_trim_reference_entry(item) for item in entries) if len(entry) > 20 and not _looks_like_reference_fragment(entry)]


def _split_inline_unnumbered_reference_starts(line: str) -> list[str]:
    text = re.sub(r"\s+", " ", str(line or "")).strip()
    if len(text) < 220:
        return [line]
    starts = [0]
    author_start = (
        r"(?:"
        r"[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+,\s+(?:[A-Z]\.|[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+,)"
        r"|[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+(?:\s+[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+){0,3}\s+(?:Team|Collaboration|AI)(?:,|\s)"
        r")"
    )
    pattern = re.compile(
        rf"(?<![A-Z]\.)(?<=\.)\s+(?={author_start}.{{0,260}}\((?:19|20)\d{{2}}[a-z]?\))"
    )
    for match in pattern.finditer(text):
        pos = match.end()
        if pos - starts[-1] >= 80:
            starts.append(pos)
    if len(starts) == 1:
        return [line]
    starts.append(len(text))
    return [text[starts[idx] : starts[idx + 1]].strip() for idx in range(len(starts) - 1) if text[starts[idx] : starts[idx + 1]].strip()]


def _trim_reference_entry(raw: str) -> str:
    value = re.sub(r"\s+", " ", str(raw or "")).strip()
    body_cut = _reference_body_cut_index(value)
    if body_cut is not None:
        return value[:body_cut].strip(" .") + "."
    if len(value) <= 1400:
        return value
    end_patterns = [
        r"^(.{160,}?(?:arXiv:\d{4}\.\d{4,5}(?:v\d+)?(?:\s+\[[^\]]+\])?\.?))",
        r"^(.{160,}?(?:19|20)\d{2}[a-z]?\.)",
        r"^(.{160,}?\bpp\.\s*\d+[-–]\d+\.)",
        r"^(.{160,}?\b\d+[-–]\d+\.)",
    ]
    for pattern in end_patterns:
        match = re.search(pattern, value, re.I)
        if match:
            return match.group(1).strip()
    return value[:1400].rstrip(" ,;") + "..."


def _reference_body_cut_index(value: str) -> int | None:
    stop_patterns = [
        r"\s+(?:Appendix|APPENDIX)\s+[A-Z0-9]\b",
        r"\s+[A-Z]\s+(?:Contributions|Simulation|Real-World|Experiment|Dataset|Additional|Implementation)\b",
        r"\s+(?:Figure|Fig\.|Table|Tab\.)\s+\d+\b",
        r"\s+(?:We calculate|We compute|This task|The robot|The bulk of this data)\b",
        r"\s+A\s+Broader Impact\b",
        r"\s+(?:implemented|datasets|elapsed|below|bag)\.\s+(?:[A-Z]|Task\b)",
        r"\s+Dataset Model Subset\b",
        r"\s+Safety Data Pct\.\b",
        r"\s+maintenance\.\s+[A-Z]",
        r"\s+[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+(?:\s+[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+){1,4}\s+is\s+(?:a|an|the)\s+(?:full\s+)?(?:professor|assistant\s+professor|researcher|phd\s+candidate|senior\s+algorithm\s+engineer)\b",
        r"\s+(?:Publisher[’']s\s+note|Open\s+Access\s+This\s+article|©\s+The\s+Author)",
    ]
    for pattern in stop_patterns:
        match = re.search(pattern, value[160:], re.I)
        if match:
            return 160 + match.start()
    return None


def _looks_like_reference_fragment(raw: str) -> bool:
    value = re.sub(r"\s+", " ", str(raw or "")).strip()
    if re.match(
        r"^(?:Proof\.|Safety Data|Ms\. Pac-Man|Objects\.|Vision\.|Layer Selection Protocol|Video Benchmark Implementation|Evaluation Methodology\.|Intended Use|Published as a conference paper|A\.\s+The|A\.\s+Appendix|C\.\d+\s+|Rel\.\s+Avg\.|Avg\.\s+Success|Tab\.\s+\d+\s+reports|For\s+(?:Pick|Move|Clean|Open|Close|Insert))(?:\b|\s|$)",
        value,
        re.I,
    ):
        return True
    if re.search(r"•\s+Network Architecture\b", value):
        return True
    if re.match(r"^(?:Results\.|instruction of the current goal\b|Split A Split B\b)", value, re.I):
        return True
    if re.search(r"\b(?:We conduct comprehensive experiments|The task sequence starts|Split A Split B|Success Rate)\b", value, re.I):
        return True
    if re.match(r"^\d+\s+https?://", value, re.I):
        return True
    if re.match(r"^(?:preprint|arXiv:|org/|abs/|//|https?://)", value, re.I):
        return True
    if re.match(r"^(?:PMLR\b|Springer International Publishing\b|In:\s+Proceedings\b|Proceedings\b|IEEE/?CVF\b|IEEE\s+International\b|Conference on\b|Journal of\b)", value, re.I):
        return True
    if re.match(r"^(?:ARXIV\.\d|Nov\s+(?:19|20)\d{2}\.|IEEE/?CVF|IEEE\s+International|ACM\s+Conference|Proceedings|Conference on|Journal of|formers\.)", value, re.I):
        return True
    if re.match(r"^(?:in|models|policies|policy|gans)\b", value) and re.search(r"\barXiv\s*:", value, re.I):
        return True
    if re.match(r"^(?:Robosuite|Franka Kitchen|Diffusion Policy,|A\. Contributions\b)", value, re.I):
        return True
    return False


def _looks_like_numbered_reference_line(line: str) -> bool:
    import unicodedata

    text = re.sub(r"^(?:\[\d+\]|\d+\.|\d+\])\s+", "", str(line or "").strip())
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"([A-Z])\.\s*-\s*([A-Z])\.", r"\1.-\2.", text)
    if not text:
        return False
    if re.match(r"^(?:and|or|the|this|that|while|where|when|we|it)\b", text, re.I):
        return False
    # Unicode letters include names such as Michał and decomposed PDF accents.
    # Preserve the same capitalized author-start check without a Latin subset.
    if text[0].isupper() and re.match(r"^[^\W\d_][\w’'`.-]+(?:,|\s+[^\W\d_])", text):
        return True
    if re.match(r"^[A-Z]\.\s+[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+", text):
        return True
    if re.match(r"^[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+(?:,|\s+[A-Z])", text):
        return True
    if re.match(r"^[A-Z][A-Za-z0-9& -]{2,60}\.", text):
        return True
    return False


def _looks_like_unnumbered_ref_start(line: str) -> bool:
    text = str(line or "").strip()
    if re.match(r"^[A-Z](?:\.-[A-Z])?\.\s+\S", text):
        return True
    if re.match(r"^[A-Z]\.\s+[A-Z]\.\s+\S", text):
        return True
    if re.match(r"^[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+,\s+(?:[A-Z]\.\s*){1,4}", text):
        return True
    if re.match(r"^[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+(?:\s+[A-Z]\.?){1,3},\s+[A-Z]", text):
        return True
    if re.match(r"^[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+\s+[A-Z]{1,3},\s+[A-Z]", text):
        return True
    if re.match(r"^[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+\s+[A-Z]{1,4}\s+\((?:19|20)\d{2}[a-z]?\)", text):
        return True
    if re.match(r"^[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+(?:\s+[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+){0,4}\.\s+\S", text):
        return True
    if _looks_like_person_author_line(text):
        return True
    if re.match(r"^[A-Z][A-Za-z-]+(?:\s+[A-Z][A-Za-z-]+){0,3}\s+Collaboration,", text):
        return True
    return False


def _looks_like_reference_first_line(line: str) -> bool:
    text = str(line or "").strip()
    if not text:
        return False
    if re.match(
        r"^(?:IEEE/?CVF|ACM|Proceedings|Conference|International Conference|Journal|Transactions|Advances)\b",
        text,
        re.I,
    ):
        return False
    if re.match(r"^https?://github\.com/[-\w.]+/[-\w.]+/?$", text, re.I):
        return False
    if re.search(r"https?://|arXiv\s*:", text, re.I):
        return True
    if re.search(r"\b(?:preprint|Proceedings|Conference|Journal|Transactions|Advances)\b", text, re.I):
        return True
    if "—" in text and "." in text:
        return True
    return _looks_like_unnumbered_ref_start(text)


def _looks_like_person_author_line(line: str) -> bool:
    text = str(line or "").strip()
    person = r"[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+(?:\s+(?:[A-Z]\.?|[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+)){0,3}"
    if re.match(rf"^{person},\s+{person},\s+{person}", text):
        return True
    if re.match(rf"^{person}(?:,\s+{person}){{1,}}(?:,?\s+(?:and|et al\.))?", text):
        return True
    if re.match(rf"^{person}\s+(?:and|&)\s+{person}\.", text):
        return True
    return False


def _is_reference_page_noise(line: str) -> bool:
    text = str(line or "").strip()
    if not text:
        return True
    if re.match(
        r"^(?:A\.\s+Appendix|Appendix|Contributors|Authors|Contributor Policy|Optimization|Layer Selection Protocol|Video Benchmark Implementation|Evaluation Methodology|Intended Use|Pct\.\s+Response|Dataset Model Subset|Rel\.\s+Avg\.|Avg\.\s+Success|Tab\.\s+\d+\s+reports|For\s+(?:Pick|Move|Clean|Open|Close|Insert))\b",
        text,
        re.I,
    ):
        return True
    if re.match(r"^\d{1,4}$", text):
        return True
    if re.match(r"^\d+\s+[A-Z][A-Za-z. ]+\s+et al\.$", text):
        return True
    if re.match(r"^(?:Preprint|Published in Transactions on Machine Learning Research.*)$", text, re.I):
        return True
    if re.match(r"^(?:Figure|Fig\.|Table)\s+\d+\b", text, re.I):
        return True
    return False


def _looks_like_reference_continuation(line: str) -> bool:
    text = str(line or "").strip()
    if not text or _is_reference_page_noise(text):
        return False
    if re.match(r"^(?:https?://|//|org/|abs/|preprint|pp\.|volume|doi:|URL\b)", text, re.I):
        return True
    if re.match(r"^[a-z0-9][A-Za-z0-9/_.:#?=&%-]*\.?$", text):
        return True
    return False


def _reference_entry_ended(text: str) -> bool:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    value = re.sub(r"\s+([.,;:])", r"\1", value)
    if re.search(r"\bIn:?\s+(?:19|20)\d{2}[a-z]?\s*$", value, re.I):
        return False
    if re.search(r"\[Accessed\s+\d{1,2}-\d{1,2}-\d{4}\]\.\s*$", value, re.I):
        return True
    if re.search(r"(?:19|20)\d{2}[a-z]?\.\s*(?:URL\s+\S+\.?)?$", value):
        return True
    if re.search(r"(?:19|20)\d{2}[a-z]?[.;]?\s*(?:doi:\s*\S+\.?)?$", value, re.I):
        return True
    if re.search(r"(?:19|20)\d{2}[a-z]?\.\s+(?:[A-Z]\.)?\d+(?:,\s*(?:[A-Z]\.)?\d+)*\s*$", value):
        return True
    if re.search(r"https?://\S+\.?\s*$", value, re.I) and re.search(r"(?:19|20)\d{2}", value[-180:]):
        return True
    if re.search(r"\bURL\s+https?://", value, re.I) and re.search(r"(?:19|20)\d{2}", value[-220:]) and value.endswith("."):
        return True
    if re.search(r"\bCoRR,\s*abs/\d{4}\.\d{4,5}(?:v\d+)?\.?\s*$", value, re.I):
        return True
    if re.search(r"arXiv:\d{4}\.\d{4,5}(?:v\d+)?(?:\s+\[[^\]]+\])?\.?\s*$", value, re.I):
        return True
    if re.search(r"\b(?:pp\.\s*)?\d+[-–]\d+\.?\s*$", value, re.I):
        return True
    if re.search(r"\b(?:Conference on Robot Learning|Annual Conference on Robot Learning|Proceedings of Robotics: Science and Systems \(RSS\)|International Conference on Machine Learning)\.?\s*$", value, re.I):
        return True
    return False


def _looks_like_completed_reference_for_split(text: str) -> bool:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    value = re.sub(r"\s+([.,;:])", r"\1", value)
    if not value.endswith(".") or len(value) < 45:
        return False
    if re.search(r"\((?:19|20)\d{2}[a-z]?\)", value):
        return bool(
            re.search(
                r"\b(?:arXiv|preprint|University|Systems|Letters|Journal|Conference|Proceedings|PMLR|IEEE|Ieee|RSS|ICML|CoRL|CVPR|NeurIPS)\b",
                value[-260:],
                re.I,
            )
        )
    return False


def _guess_ref_title(raw: str) -> str:
    normalized = _normalize_reference_text(raw)
    for rule in _REFERENCE_TITLE_RULES:
        candidate = rule.extract(normalized)
        title = _validated_ref_title_candidate(
            candidate,
            allow_short=rule.allow_short,
            allow_identifier=rule.allow_identifier,
        )
        if title:
            return title
    return ""


def _ref_sentence_parts(raw: str) -> list[str]:
    protected = re.sub(r"(?<![A-Za-z])([A-Z])\.", r"\1<prd>", raw or "")
    parts = re.split(r"\.\s+", protected)
    return [part.replace("<prd>", ".").strip() for part in parts if part.strip()]


def _looks_like_ref_author_list(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    lowered = value.casefold()
    if " et al" in lowered:
        return True
    if value.count(",") >= 2:
        return True
    if "," in value and re.search(r"\b(and|&)\b", lowered):
        return True
    person = r"[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+(?:\s+[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+){0,3}"
    if re.fullmatch(rf"{person}\s+(?:and|&)\s+{person}", value):
        return True
    if re.fullmatch(r"(?:[A-Z]\.\s+){1,4}[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+", value):
        return True
    return False


def _looks_like_compact_ref_author_list(text: str) -> bool:
    value = str(text or "").strip()
    if not value or len(value) > 520:
        return False
    person = r"[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+(?:\s+[A-Z]){1,4}"
    if re.fullmatch(rf"{person}(?:,\s+{person}){{0,20}}(?:,\s+et\s+al\.?)?", value):
        return True
    return False


def _clean_ref_title_candidate(value: str) -> str:
    candidate = re.sub(r"\s+", " ", str(value or "")).strip()
    candidate = candidate.strip(" .,:;\"“”")
    arxiv_url = re.search(r"(?:https?:)?//arxiv\.org/abs/(\d{4}\.\d{4,5})(?:v\d+)?", candidate, re.I)
    if arxiv_url and re.match(r"^(?:https?:)?//arxiv\.org/abs/", candidate, re.I):
        return f"arXiv:{arxiv_url.group(1)}"
    candidate = re.sub(r"^(?:et al\.?\s*)+", "", candidate, flags=re.I).strip()
    candidate = re.sub(r"\b(arXiv|CoRR)\s+preprint\b.*$", "", candidate, flags=re.I)
    candidate = re.sub(r"\bPreprint\s+at\s+https?://.*$", "", candidate, flags=re.I)
    candidate = re.sub(r"\bPreprint\s+at\s+https?\s*:\s*//.*$", "", candidate, flags=re.I)
    candidate = re.sub(r"\bURL\s+https?://.*$", "", candidate, flags=re.I)
    candidate = re.sub(r"\bURL\s+https?\s*:\s*//.*$", "", candidate, flags=re.I)
    candidate = re.sub(r"\s+https?://.*$", "", candidate, flags=re.I)
    candidate = re.sub(r"\s+https?\s*:\s*//.*$", "", candidate, flags=re.I)
    candidate = re.sub(r"\s+www\.\s*.*$", "", candidate, flags=re.I)
    candidate = re.sub(r",?\s+DOI\b.*$", "", candidate)
    candidate = re.sub(r"\s+In\s+(?:Proceedings|Conference|International|European|The)\b.*$", "", candidate, flags=re.I)
    candidate = re.sub(r",?\s+pp\.\s*\d+\s*[-–]\s*\d+.*$", "", candidate, flags=re.I)
    candidate = re.sub(r",?\s+(?:19|20)\d{2}\.?\s*$", "", candidate)
    return candidate.strip(" .,:;\"“”")


def _looks_like_ref_venue_tail(value: str) -> bool:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return False
    if re.match(r"^(?:[A-Z][A-Za-z0-9&'.:-]+(?:\s+[A-Z][A-Za-z0-9&'.:-]+){0,5}|[A-Z]{2,})\s*,\s*\d", text):
        return True
    if re.match(r"^(?:In\s+)?(?:Proceedings|Conference|Journal|Transactions|Science|Nature|Springer|IEEE|ACM|PMLR|F1000Research|Technologies)\b", text, re.I):
        return True
    if re.search(r"\b(?:ISBN|ISSN)\b", text, re.I):
        return True
    return False


@dataclass(frozen=True, slots=True)
class _ReferenceTitlePattern:
    name: str
    pattern: Pattern[str]
    title_group: int = 2
    prefix_group: int | None = 1
    prefix_mode: str = "author"


@dataclass(frozen=True, slots=True)
class _ReferenceTitleRule:
    name: str
    extract: Callable[[str], str]
    allow_short: bool = False
    allow_identifier: bool = False


_REFERENCE_VENUE_BOUNDARY = (
    r"(?:In:?|arXiv\b|CoRR\b|doi:|URL\b|https?://|Journal\b|Proceedings\b|"
    r"Conference\b|IEEE\b|ACM\b|PMLR\b|Nature\b|Science\b|Springer\b|"
    r"Advances\b|Transactions\b|NeurIPS\b|ICLR\b|ICML\b|CVPR\b|ECCV\b|"
    r"[A-Z][A-Za-z -]{3,90}(?:[,.;]|\s+\d|\s+\())"
)
_REFERENCE_TITLE_PATTERNS: tuple[_ReferenceTitlePattern, ...] = (
    _ReferenceTitlePattern(
        "author-colon-title",
        re.compile(
            rf"^(.{{5,1200}}?):\s+(.+?)(?:\.\s+{_REFERENCE_VENUE_BOUNDARY}|\s+\((?:19|20)\d{{2}}\)|$)",
            re.I,
        ),
        prefix_mode="author-colon",
    ),
    _ReferenceTitlePattern(
        "author-dot-year-title",
        re.compile(
            rf"^(.{{4,1400}}?)\.\s+(?:19|20)\d{{2}}[a-z]?\.\s+(.+?)(?:\.\s+{_REFERENCE_VENUE_BOUNDARY}|$)",
            re.I,
        ),
        prefix_mode="author-loose",
    ),
    _ReferenceTitlePattern(
        "author-parenthesized-year-title",
        re.compile(
            rf"^(.{{4,520}}?)\s+\((?:19|20)\d{{2}}(?:,\s*[A-Za-z]+)?[a-z]?\)\.?\s+(.+?)(?:\.\s+{_REFERENCE_VENUE_BOUNDARY}|$)",
            re.I,
        ),
        prefix_mode="author-loose",
    ),
    _ReferenceTitlePattern(
        "initials-title",
        re.compile(
            rf"^(.{{5,900}}\b[A-Z][A-Za-zÀ-ÖØ-öø-ÿ’'`.-]+,\s+(?:[A-Z]\.\s*){{1,4}})\s+(.+?)(?:\.\s+{_REFERENCE_VENUE_BOUNDARY}|$)",
            re.I,
        ),
        prefix_mode="initials",
    ),
    _ReferenceTitlePattern(
        "compact-authors",
        re.compile(
            rf"^(.{{4,1400}}?)\.\s+(.+?)(?:\.\s+{_REFERENCE_VENUE_BOUNDARY}|\s+\((?:19|20)\d{{2}}\)|$)",
            re.I,
        ),
        prefix_mode="author-loose",
    ),
    _ReferenceTitlePattern(
        "et-al-title",
        re.compile(
            rf"^(.+?\bet\s+al)\.\s+(.+?)(?:\.\s+{_REFERENCE_VENUE_BOUNDARY}|$)",
            re.I,
        ),
        prefix_mode="et-al",
    ),
    _ReferenceTitlePattern(
        "typed-reference",
        re.compile(r"^(.{6,420}?)\.\s+(.+?)\s*\[[A-Z]\](?://)?(?:\s*In:|\s*$)", re.I),
    ),
    _ReferenceTitlePattern(
        "initial-author-comma-title",
        re.compile(
            r"^(?:(?:[A-Z]\.\s*)+[A-Z][\w’'`-]+(?:\s+[A-Z][\w’'`-]+)*,\s*)+"
            r"(.+?)(?:,\s+(?:19|20)\d{2}\b|,\s+(?:arXiv|IEEE|ACM|Nature|Science)\b|$)"
        ),
        title_group=1,
        prefix_group=None,
        prefix_mode="initials",
    ),
    _ReferenceTitlePattern(
        "comma-journal",
        re.compile(
            r"^(.{4,360}?),\s+(.+?),\s+(?:(?:Phys\.|Rev\.|J\.|Proc\.|Annu\.|IEEE|ACM|Nature|Science)(?:\s+[A-Za-z.]+){0,4}\b|[A-Z][A-Za-z -]{3,80},)"
        ),
        prefix_mode="author-loose",
    ),
)


def _normalize_reference_text(raw: str) -> str:
    import unicodedata

    value = re.sub(r"(\w)-\s+(\w)", r"\1\2", unicodedata.normalize("NFC", raw or ""))
    value = re.sub(r"(\d)\.\s+(\d)", r"\1.\2", value)
    return re.sub(r"^(?:\[\d+\]|\d+\.|\d+\])\s+", "", value).strip()


def _validated_ref_title_candidate(
    value: object,
    *,
    allow_short: bool = False,
    allow_identifier: bool = False,
) -> str:
    candidate = _clean_ref_title_candidate(str(value or ""))[:220]
    if not candidate:
        return ""
    if allow_identifier and re.fullmatch(r"arXiv:\d{4}\.\d{4,5}", candidate, re.I):
        return candidate
    alphanumeric = re.sub(r"[^A-Za-z0-9]+", "", candidate)
    if len(alphanumeric) < (4 if allow_short else 8):
        return ""
    if _looks_like_bad_ref_title(candidate) or _looks_like_ref_venue_tail(candidate):
        return ""
    if re.fullmatch(
        r"(?:doi:?\s*)?10\.\S+|(?:URL\s+)?https?://\S+|(?:19|20)\d{2}[a-z]?",
        candidate,
        re.I,
    ):
        return ""
    return candidate


def _reference_prefix_matches(prefix: str, mode: str) -> bool:
    value = str(prefix or "").strip()
    lowered = value.casefold()
    if mode == "et-al":
        return bool(re.search(r"\bet\s+al\.?$", value, re.I))
    if mode == "initials":
        return not re.match(r"^[A-Z]\.\s+", value) and (
            "," in value or " and " in lowered
        )
    if mode == "author-colon":
        return (
            ". " not in value
            and not re.search(r"\.\s+(?:19|20)\d{2}[a-z]?\.\s+", value)
            and _reference_prefix_matches(value, "author-loose")
        )
    if _looks_like_ref_author_list(value) or _looks_like_compact_ref_author_list(value):
        return True
    words = value.split()
    person_name = 1 <= len(words) <= 4 and all(
        word[0].isupper() and re.fullmatch(r"[^\W\d_][\w’'`.-]*", word)
        for word in words
    )
    return mode == "author-loose" and (
        value.count(",") >= 1
        or bool(re.search(r"\b(?:and|et\s+al)\b", lowered))
        or person_name
    )


def _extract_title_before_doi(raw: str) -> str:
    doi = re.search(r"\bdoi\s*:?\s*10\.\S+", raw, re.I)
    if not doi:
        return ""
    before_doi = raw[:doi.start()].strip(" .,:;")
    parts = _ref_sentence_parts(before_doi)
    candidate = parts[-1] if parts else before_doi
    if len(parts) >= 2 and _looks_like_ref_venue_tail(candidate):
        candidate = parts[-2]
    return candidate


def _extract_open_x_title(raw: str) -> str:
    if re.match(r"^(?:Open\s+X-Embodiment\s+Collaboration|Collaboration,\s+O\.\s*X\.-E\.)", raw, re.I):
        return "Open X-Embodiment: Robotic learning datasets and RT-X models"
    return ""


def _first_quoted_title(raw: str) -> tuple[str, int]:
    # Apostrophes in authors (O'Brien, d'Autume) are not title delimiters.
    match = re.search(r'"([^"\n]+)"|“([^”\n]+)”|(?<!\w)[\'‘]([^\'’\n]+)[\'’](?!\w)', raw)
    if match and raw[match.start()] == "“":
        depth = 1
        for index in range(match.start() + 1, len(raw)):
            if raw[index] == "“":
                depth += 1
            elif raw[index] == "”":
                depth -= 1
                if depth == 0:
                    return raw[match.start() + 1:index], match.start()
    return (next(group for group in match.groups() if group is not None), match.start()) if match else ("", -1)


def _extract_author_quoted_title(raw: str) -> str:
    candidate, start = _first_quoted_title(raw)
    if start < 0 or start >= 900:
        return ""
    prefix = raw[:start]
    if _reference_prefix_matches(prefix, "author-loose"):
        return candidate
    return ""


def _extract_pattern_title(raw: str) -> str:
    protected = re.sub(r"(?<![A-Za-z])([A-Z])\.", r"\1<prd>", raw)
    for rule in _REFERENCE_TITLE_PATTERNS:
        # Broad author/title boundaries must not split at a middle initial.
        # The dedicated initials rule deliberately consumes those periods.
        match = rule.pattern.match(raw if rule.prefix_mode == "initials" else protected)
        if not match:
            continue
        if rule.prefix_group is not None and not _reference_prefix_matches(
            match.group(rule.prefix_group).replace("<prd>", "."), rule.prefix_mode
        ):
            continue
        candidate = match.group(rule.title_group).replace("<prd>", ".")
        if _validated_ref_title_candidate(candidate):
            return candidate
    return ""


def _extract_sentence_title(raw: str) -> str:
    parts = _ref_sentence_parts(raw)
    if len(parts) < 2:
        return ""
    if _reference_prefix_matches(parts[0], "author-loose"):
        title_index = 2 if len(parts) >= 3 and re.fullmatch(r"(?:19|20)\d{2}[a-z]?", parts[1]) else 1
        if title_index < len(parts):
            return parts[title_index]
    if len(parts) >= 3 and _reference_prefix_matches(parts[0], "author") and _looks_like_ref_venue_tail(parts[2]):
        return parts[1]
    if re.match(r"^(?:URL|https?://)", parts[1], re.I) and not _looks_like_ref_author_list(parts[0]):
        return parts[0]
    if len(parts[0].split()) <= 4 and re.fullmatch(r"[A-Z][A-Za-z0-9& -]{1,60}", parts[0]):
        return parts[1]
    return ""


def _extract_typed_marker_title(raw: str) -> str:
    marker = re.search(r"\s\[[A-Z]\](?://)?(?:\.?\s*In:|\s*$)", raw, re.I)
    if not marker:
        return ""
    parts = _ref_sentence_parts(raw[:marker.start()])
    return parts[-1] if len(parts) >= 2 and _reference_prefix_matches(parts[0], "author") else ""


def _extract_colon_title(raw: str) -> str:
    match = re.search(
        rf":\s+(?!https?://)(.+?)(?:\.\s+{_REFERENCE_VENUE_BOUNDARY}|\s+\(\d{{4}}\)|$)",
        raw,
        re.I,
    )
    if not match:
        return ""
    prefix = raw[:match.start()]
    return match.group(1) if _reference_prefix_matches(prefix, "author-loose") else ""


def _extract_year_title(raw: str) -> str:
    match = re.search(
        rf"\((?:19|20)\d{{2}}[a-z]?\)\.?\s+(.+?)(?:\.\s+{_REFERENCE_VENUE_BOUNDARY}|$)",
        raw,
        re.I,
    )
    return match.group(1) if match else ""


def _extract_open_quote_title(raw: str) -> str:
    match = re.search(r"[\"“]([^\"”]{8,260}?)(?:,?\s+arXiv|\.\s+(?:19|20)\d{2}|$)", raw, re.I)
    return match.group(1) if match and match.start() < 900 else ""


def _extract_any_quoted_title(raw: str) -> str:
    candidate, _ = _first_quoted_title(raw)
    return candidate


def _extract_sentence_fallback_title(raw: str) -> str:
    for part in _ref_sentence_parts(raw)[:4]:
        if not re.search(r"\b(?:arXiv|doi|https?|pages?|pp|Tech\.?\s+rep)\b", part, re.I):
            return part
    return ""


_REFERENCE_TITLE_RULES: tuple[_ReferenceTitleRule, ...] = (
    _ReferenceTitleRule("open-x-canonical", _extract_open_x_title),
    _ReferenceTitleRule("author-quoted", _extract_author_quoted_title),
    _ReferenceTitleRule("doi-tail", _extract_title_before_doi, allow_identifier=True),
    _ReferenceTitleRule("structured-pattern", _extract_pattern_title),
    _ReferenceTitleRule("sentence-structure", _extract_sentence_title),
    _ReferenceTitleRule("typed-marker", _extract_typed_marker_title),
    _ReferenceTitleRule("author-colon", _extract_colon_title),
    _ReferenceTitleRule("year-boundary", _extract_year_title),
    _ReferenceTitleRule("open-quote", _extract_open_quote_title),
    _ReferenceTitleRule("quoted-fallback", _extract_any_quoted_title),
    _ReferenceTitleRule("sentence-fallback", _extract_sentence_fallback_title),
)


def _match_registered_paper(title: str, raw: str, papers: list[dict]) -> dict | None:
    hay = f"{title} {raw}".casefold()
    for paper in papers:
        ptitle = str(paper.get("title") or "").casefold()
        if ptitle and (ptitle in hay or (title and title.casefold() in ptitle)):
            return paper
    return None
