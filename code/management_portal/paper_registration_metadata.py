"""Registration metadata of a PDF or a reference: PDF properties, front matter, Crossref, arXiv, heuristics and paper:registration-metadata."""

from __future__ import annotations

import html as html_lib
import http.client
import json
import logging
from pathlib import Path
import re
import urllib.error
from urllib.parse import quote, urlencode
import urllib.request
import xml.etree.ElementTree as ET

from management_portal.paper_pdf import _pdf_document_properties
from management_portal.paper_records import (
    _arxiv_id_from_text, _is_project_relation_id, _normalize_list, _safe_year,
)
from management_portal.paper_references import _guess_ref_title
from management_portal.paper_resident import _parse_json_object_from_text, _resident_reply
from management_portal.paper_store import WORKSPACE_ROOT

LOGGER = logging.getLogger(__name__)


def _deterministic_paper_registration_metadata(
    pdf_path: Path,
    *,
    filename: str,
    pdf_text: str,
) -> dict:
    properties = _pdf_document_properties(pdf_path)
    title = str(properties.get("title") or "").strip()
    filename_stem = Path(filename).stem.strip()
    property_title_stem = Path(title).stem.strip() if title.casefold().endswith(".pdf") else title
    filename_arxiv_match = re.search(
        r"(?<!\d)((?:\d{4}\.\d{4,5}|[a-z-]+/\d{7})(?:v\d+)?)(?!\d)",
        filename_stem,
        re.I,
    )
    filename_arxiv = filename_arxiv_match.group(1) if filename_arxiv_match else ""
    if (
        title.casefold() in {"untitled", "paper", "document", "microsoft word"}
        or re.match(r"^arxiv\s*:", title, re.I)
        or property_title_stem.casefold() == filename_stem.casefold()
        or bool(filename_arxiv and title.casefold() == filename_arxiv.casefold())
    ):
        title = ""
    authors = _plausible_author_list(properties.get("author"))
    front_matter = _paper_front_matter_metadata(pdf_text)
    title = title or str(front_matter.get("title") or "")
    authors = authors or str(front_matter.get("authors") or "")
    date_text = " ".join(
        str(properties.get(key) or "") for key in ("creationdate", "moddate")
    )
    year = _safe_year(date_text)
    if not year:
        year_match = re.search(r"\b(?:19|20)\d{2}\b", str(pdf_text or "")[:12_000])
        year = year_match.group(0) if year_match else ""
    abstract_match = re.search(
        r"(?ims)^\s*abstract\s*[-—:]?\s*(.{80,5000}?)"
        r"(?=^\s*(?:keywords?|index\s+terms?|(?:1|i)[.\s]+introduction)\b)",
        str(pdf_text or "")[:40_000],
    )
    abstract = re.sub(r"\s+", " ", abstract_match.group(1)).strip() if abstract_match else ""
    identifier_text = "\n".join((str(pdf_text or "")[:100_000], str(properties)))
    doi_match = re.search(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", identifier_text, re.I)
    doi = doi_match.group(0).rstrip(".,;:)") if doi_match else ""
    arxiv_match = re.search(
        r"(?:arxiv\s*[:/]\s*)?\b((?:\d{4}\.\d{4,5}|[a-z-]+/\d{7})(?:v\d+)?)\b",
        identifier_text,
        re.I,
    )
    arxiv = (
        arxiv_match.group(1)
        if arxiv_match and "arxiv" in arxiv_match.group(0).casefold()
        else ""
    ) or filename_arxiv
    if not title:
        stem = re.sub(r"[_-]+", " ", filename_stem).strip()
        if (
            len(stem) >= 12
            and stem.casefold() not in {"main", "paper", "document"}
            and not filename_arxiv
        ):
            title = stem
    return {
        "title": title[:320],
        "authors": authors[:600],
        "venue": "",
        "year": year,
        "session": "",
        "abstract": abstract[:5000],
        "tags": [],
        "workload": [],
        "platform": [],
        "architecture": [],
        "optimization": [],
        "doi": doi,
        "arxiv": arxiv,
    }


_AUTHOR_NAME_PARTICLES = frozenset({
    "de", "da", "di", "del", "della", "der", "den", "van", "von", "la", "le", "du", "bin", "binti",
    "al", "el", "dos", "das", "ter", "ten", "y", "zu",
})


def _looks_like_person_name(value: object) -> bool:
    """A person's name as it appears in a byline, not a fragment of prose.

    A synthetic paper without a byline had body sentences split on double
    spaces registered as its authors ("extracted text, structured experimental
    evidence, ..."): every fragment had 2-6 words, which was the only check.
    """
    text = re.sub(r"\s+", " ", str(value or "")).strip(" ,;")
    text = re.sub(r"[\d*†‡§¶✉]+$", "", text).strip()
    if not text or len(text) > 80:
        return False
    if re.fullmatch(r"[\u4e00-\u9fff·•]{2,8}", text):
        return True
    # Experimental labels can look title-cased without being a byline.
    if re.fullmatch(r"(?:method|model|baseline|variant|group)\s+[A-Z]", text, re.I):
        return False
    words = text.split(" ")
    if not 2 <= len(words) <= 6:
        return False
    for word in words:
        bare = re.sub(r"[\d*†‡§¶✉,;]+$", "", word)
        if not bare:
            return False
        if bare.casefold() in _AUTHOR_NAME_PARTICLES:
            continue
        if re.fullmatch(r"[A-Z]\.(?:-?[A-Z]\.)*", bare):
            continue
        if not (bare[0].isalpha() and bare[0].isupper()) or re.search(r"[.!?:]$", bare):
            return False
    return True


def _plausible_author_list(value: object) -> str:
    """The author list when it reads as person names, otherwise empty."""
    if isinstance(value, list):
        value = ", ".join(str(item or "").strip() for item in value if str(item or "").strip())
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    parts = [part for part in re.split(r"\s*(?:,|;|，|、|\band\b)\s*", text) if part.strip()]
    names = [re.sub(r"[\d*†‡§¶✉]+$", "", part.strip()).strip() for part in parts if _looks_like_person_name(part)]
    # A byline may carry "et al." or "Jr."; prose fails most of its pieces.
    return ", ".join(names) if names and len(names) >= 0.8 * len(parts) else ""


def _paper_front_matter_metadata(pdf_text: str) -> dict[str, str]:
    blocks: list[list[str]] = []
    current: list[str] = []
    for raw_line in str(pdf_text or "")[:16_000].splitlines()[:120]:
        stripped = raw_line.strip()
        if re.fullmatch(r"abstract", stripped, re.I):
            break
        if not stripped:
            if current:
                blocks.append(current)
                current = []
            continue
        current.append(raw_line.rstrip())
    if current:
        blocks.append(current)

    def ignored(line: str) -> bool:
        text = line.strip()
        return bool(
            not text
            or re.search(r"\barxiv\s*:", text, re.I)
            or re.search(r"\bdoi\s*:", text, re.I)
            or "@" in text
            or re.fullmatch(r"(?:19|20)\d{2}", text)
            or re.match(r"^(?:proceedings|copyright|preprint|submitted|accepted)\b", text, re.I)
        )

    title = ""
    title_block_index = -1
    for index, block in enumerate(blocks[:6]):
        lines = [re.sub(r"\s+", " ", line).strip() for line in block if not ignored(line)]
        candidate = " ".join(lines).strip()
        if 12 <= len(candidate) <= 320 and len(re.findall(r"[A-Za-z]", candidate)) >= 8:
            title = candidate
            title_block_index = index
            break

    authors: list[str] = []
    for block in blocks[title_block_index + 1:title_block_index + 4] if title_block_index >= 0 else []:
        for line in block:
            if ignored(line):
                continue
            for candidate in re.split(r"\s{2,}|\s+and\s+", line.strip(), flags=re.I):
                name = re.sub(r"^(?:and|&)\s+", "", re.sub(r"\s+", " ", candidate).strip(" ,;"), flags=re.I)
                words = name.split()
                if (
                    2 <= len(words) <= 6
                    and len(name) <= 100
                    and not re.search(r"\b(?:university|institute|laboratory|department|google|microsoft|meta|nvidia)\b", name, re.I)
                    and all(re.search(r"[A-Za-z]", word) for word in words)
                    and _looks_like_person_name(name)
                ):
                    authors.append(name)
        if authors:
            break
    return {
        "title": title,
        "authors": ", ".join(authors[:30]),
    }


def _crossref_registration_metadata(doi: str) -> dict:
    clean_doi = str(doi or "").strip().removeprefix("https://doi.org/")
    if not clean_doi:
        return {}
    request = urllib.request.Request(
        "https://api.crossref.org/works/" + quote(clean_doi, safe=""),
        headers={"User-Agent": "TFIPortal/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            payload = json.loads(response.read(2_000_000))
    except (OSError, http.client.HTTPException, ValueError) as exc:
        # The connection or HTTP status (URLError, HTTPError), a broken response, a body that is not JSON.
        LOGGER.warning("Crossref lookup of %s failed: %s: %s", clean_doi, type(exc).__name__, exc)
        return {}
    message = payload.get("message") if isinstance(payload, dict) else {}
    if not isinstance(message, dict):
        return {}
    titles = message.get("title") if isinstance(message.get("title"), list) else []
    venues = message.get("container-title") if isinstance(message.get("container-title"), list) else []
    authors = []
    for author in message.get("author") if isinstance(message.get("author"), list) else []:
        if not isinstance(author, dict):
            continue
        name = " ".join(
            part for part in (
                str(author.get("given") or "").strip(),
                str(author.get("family") or "").strip(),
            )
            if part
        )
        if name:
            authors.append(name)
    year = ""
    for date_key in ("published-print", "published-online", "issued", "created"):
        date_value = message.get(date_key) if isinstance(message.get(date_key), dict) else {}
        date_parts = date_value.get("date-parts") if isinstance(date_value.get("date-parts"), list) else []
        if date_parts and isinstance(date_parts[0], list) and date_parts[0]:
            year = _safe_year(date_parts[0][0])
            if year:
                break
    abstract = html_lib.unescape(
        re.sub(r"<[^>]+>", " ", str(message.get("abstract") or ""))
    )
    return {
        "title": str(titles[0] if titles else "").strip(),
        "authors": ", ".join(authors),
        "venue": str(venues[0] if venues else "").strip(),
        "year": year,
        "abstract": re.sub(r"\s+", " ", abstract).strip(),
        "doi": clean_doi,
    }


def _identifier_paper_registration_metadata(metadata: dict) -> dict:
    arxiv = str(metadata.get("arxiv") or "").strip()
    if arxiv:
        fetched = _fetch_arxiv_metadata(id_list=re.sub(r"v\d+$", "", arxiv, flags=re.I))
        if fetched:
            return {
                "title": fetched.get("title") or "",
                "authors": fetched.get("authors") or "",
                "venue": "arXiv",
                "year": fetched.get("year") or "",
                "abstract": fetched.get("abstract") or "",
                # Keep the exact uploaded identifier (including a version suffix)
                # while using the versionless id for the metadata lookup above.
                "arxiv": arxiv,
            }
    doi = str(metadata.get("doi") or "").strip()
    return _crossref_registration_metadata(doi) if doi else {}


def _coerce_paper_registration_metadata(value: dict) -> dict:
    authors = _plausible_author_list(value.get("authors"))
    return {
        "title": str(value.get("title") or "").strip()[:320],
        "authors": str(authors or "").strip()[:600],
        "venue": str(value.get("venue") or "").strip()[:80],
        "year": _safe_year(value.get("year")),
        "session": str(value.get("session") or "").strip()[:120],
        "abstract": str(value.get("abstract") or "").strip()[:5000],
        "tags": _normalize_list(value.get("tags"))[:12],
        "workload": _normalize_list(value.get("workload"))[:8],
        "platform": _normalize_list(value.get("platform"))[:8],
        "architecture": _normalize_list(value.get("architecture"))[:8],
        "optimization": _normalize_list(value.get("optimization"))[:8],
        "doi": str(value.get("doi") or "").strip()[:200],
        "arxiv": str(value.get("arxiv") or "").strip()[:80],
    }


def _paper_registration_project_catalog() -> list[dict[str, str]]:
    try:
        from management_portal import project_registry

        projects = project_registry.projects()
    except Exception:
        LOGGER.warning("Project catalog unreadable; the registration draft offers no projects", exc_info=True)
        return []
    result: list[dict[str, str]] = []
    for item in projects or []:
        if not isinstance(item, dict):
            continue
        project_id = str(item.get("id") or "").strip()
        if not _is_project_relation_id(project_id):
            continue
        result.append({
            "id": project_id,
            "description": str(item.get("description") or "").strip(),
            "status": str(item.get("status") or "").strip(),
        })
    return result


def _heuristic_paper_registration_project(
    source: str,
    project_catalog: list[dict[str, str]],
) -> str:
    available = {str(item.get("id") or "").strip(): item for item in project_catalog}
    hints = (
        ("GPUDataflowRuntime", ("megakernel", "persistent kernel", "split-join dataflow", "gpu dataflow runtime")),
        ("HardwareExplorer", ("hardware exploration", "architecture exploration", "accelerator design", "manycore architecture")),
        ("ACLink-Co-Kernel", ("co-kernel", "distributed os kernel", "event-triggered kernel")),
        ("ACLink", ("network-on-chip", "network-on-package", "event-triggered interconnect", "distributed scheduling protocol")),
        ("SemanticPaging", ("semantic paging", "semantic virtual memory", "kv cache management")),
        ("SparseTriggerArch", ("sparse trigger", "event-triggered architecture", "fine-grained sparse")),
        ("FlowTango", ("streaming language", "streaming runtime", "dataflow language")),
        ("DRAMStacking", ("dram stacking", "stacked dram", "3d-stacked memory")),
        ("CATFlow", ("distributed training", "parallel plan", "strategy discovery")),
        ("OSForAgent", ("embodied agent", "agent harness", "agentic os", "operating system for agent", "runtime critic", "physical intelligence")),
        ("RobotWorker", ("robot manipulation", "robot navigation", "vision-language-action", "vla policy", "learning-based robot control")),
        ("RoboLab", ("cloud robot laboratory", "remote robot experiment", "robot lab automation")),
        ("Simulator_Asset_Library", ("simulator asset", "simulation asset", "scene generation", "sim-to-real")),
        ("ScienceRoboticsViewpoints", ("science robotics viewpoint", "robotics perspective", "future of robotics")),
    )
    scored: list[tuple[int, str]] = []
    for project_id, needles in hints:
        if project_id not in available:
            continue
        score = sum(4 for needle in needles if needle in source)
        if score:
            scored.append((score, project_id))
    if scored:
        return max(scored, key=lambda item: item[0])[1]

    stopwords = {
        "about", "agent", "based", "company", "from", "paper", "project",
        "research", "system", "that", "this", "with",
    }
    source_tokens = {
        token for token in re.findall(r"[a-z][a-z0-9-]{3,}", source)
        if token not in stopwords
    }
    lexical_scores: list[tuple[int, str]] = []
    for project_id, item in available.items():
        description = " ".join((project_id, item.get("description") or "")).casefold()
        description_tokens = {
            token for token in re.findall(r"[a-z][a-z0-9-]{3,}", description)
            if token not in stopwords
        }
        overlap = len(source_tokens & description_tokens)
        if overlap >= 2:
            lexical_scores.append((overlap, project_id))
    return max(lexical_scores, default=(0, ""), key=lambda item: item[0])[1]


def _heuristic_paper_registration_classification(
    metadata: dict,
    pdf_text: str,
    *,
    project_catalog: list[dict[str, str]] | None = None,
) -> dict:
    """Derive conservative search facets when the model pool is unavailable."""
    source = " ".join((
        str(metadata.get("title") or ""),
        str(metadata.get("abstract") or ""),
        str(pdf_text or "")[:30_000],
    )).casefold()

    def matches(rules: tuple[tuple[tuple[str, ...], str], ...], limit: int) -> list[str]:
        values: list[str] = []
        for needles, label in rules:
            if any(needle in source for needle in needles) and label not in values:
                values.append(label)
            if len(values) >= limit:
                break
        return values

    catalog = project_catalog if project_catalog is not None else _paper_registration_project_catalog()
    return {
        "session": _heuristic_paper_registration_project(source, catalog),
        "tags": matches((
            (("embodied intelligence", "embodied agent"), "embodied intelligence"),
            (("robot", "robotic"), "robotics"),
            (("self-evolving", "self evolution", "self-improvement"), "self-evolving agents"),
            (("closed-loop", "closed loop"), "closed-loop control"),
            (("world model",), "world models"),
            (("reinforcement learning",), "reinforcement learning"),
            (("imitation learning",), "imitation learning"),
            (("large language model", " llm "), "large language models"),
            (("computer vision", "visual recognition"), "computer vision"),
            (("natural language processing",), "natural language processing"),
            (("memory system", "memory hierarchy"), "memory systems"),
            (("accelerator", "processing-in-memory", "compute-in-memory"), "computer architecture"),
        ), 8),
        "workload": matches((
            (("robot manipulation", "manipulation task"), "robot manipulation"),
            (("robot navigation", "navigation task"), "robot navigation"),
            (("robot", "robotic", "embodied agent"), "robot control"),
            (("policy inference", "inference speedup"), "policy inference"),
            (("model training", "training workload"), "model training"),
            (("machine translation",), "machine translation"),
            (("image generation",), "image generation"),
            (("graph processing", "graph neural network"), "graph processing"),
        ), 6),
        "platform": matches((
            (("physical robot", "robot-environment", "robotic"), "robot"),
            ((" gpu", "gpu ", "cuda"), "GPU"),
            ((" cpu", "cpu "), "CPU"),
            (("fpga",), "FPGA"),
            (("edge device", "edge platform"), "edge device"),
            (("cloud platform", "cloud cluster"), "cloud"),
            (("ros ", "robot operating system"), "ROS"),
        ), 6),
        "architecture": matches((
            (("closed-loop", "closed loop"), "closed-loop agent"),
            (("agentic harness", "embodied harness", "agent harness"), "agent harness"),
            (("world model",), "world model"),
            (("actor-critic", "actor critic"), "actor-critic"),
            (("transformer",), "Transformer"),
            (("diffusion model", "diffusion policy"), "diffusion model"),
            (("mixture of experts", "mixture-of-experts"), "mixture-of-experts"),
            (("encoder-decoder", "encoder decoder"), "encoder-decoder"),
            (("graph neural network",), "graph neural network"),
            (("systolic array",), "systolic array"),
            (("chiplet",), "chiplet"),
        ), 6),
        "optimization": matches((
            (("self-evolving", "self evolution", "self-improvement"), "online self-improvement"),
            (("online adaptation", "online while", "online update"), "online adaptation"),
            (("recovery skill", "runtime recovery"), "runtime recovery"),
            (("frozen base policy", "policy frozen"), "frozen-policy adaptation"),
            (("quantization",), "quantization"),
            (("knowledge distillation", "distillation"), "knowledge distillation"),
            (("pruning",), "pruning"),
            (("parallelization", "parallel execution"), "parallelization"),
            (("scheduling",), "scheduling"),
            (("caching", "cache reuse"), "caching"),
        ), 6),
    }


def _ai_paper_registration_metadata(pdf_text: str, *, actor: str = "") -> tuple[dict, dict]:
    system_prompt = (
        "你是 TFI Portal 的论文元数据提取器。只根据给定 PDF 文本提取信息；"
        "书目信息缺失时使用空字符串，不得猜测；分类字段应根据论文语义归纳，"
        "只有确实不适用时才使用空数组。只返回 JSON 对象。"
    )
    project_catalog = _paper_registration_project_catalog()
    project_options = "\n".join(
        f"- {item['id']}: {item.get('description') or ''}"
        for item in project_catalog
    ) or "- (no project options available)"
    user_prompt = f"""请从下面的论文 PDF 文本中提取注册信息。

JSON schema:
{{
  "title": "论文完整标题",
  "authors": ["作者姓名"],
  "venue": "正式会议、期刊名称或 arXiv；无法确定则为空",
  "year": "四位年份",
  "session": "最相关的现有项目 ID；无法可靠判断则为空",
  "abstract": "论文原始摘要，保持原语言",
  "tags": ["最多 8 个检索主题"],
  "workload": ["论文处理的工作负载"],
  "platform": ["硬件或软件平台"],
  "architecture": ["关键架构"],
  "optimization": ["关键优化技术"],
  "doi": "DOI，不含 https://doi.org/",
  "arxiv": "arXiv ID"
}}

规则：
- title、authors、venue、year、abstract 必须来自正文可见内容。
- authors 只提取明确署名的作者；没有署名时返回空数组，不把方法、模型、实验组或表格标签当作人名。
- tags 和分类字段使用简短英文术语，避免宽泛词和重复词。
- session 只能从下面的现有项目中选择一个，必须原样返回项目 ID，禁止创造项目：
{project_options}
- 不要输出说明、Markdown 或代码围栏。

PDF text:
{str(pdf_text or '')[:60_000]}
"""
    content, usage = _resident_reply(
        f"{system_prompt}\n\n{user_prompt}",
        actor=actor,
        resident_scope="paper:registration-metadata",
        cwd=str(WORKSPACE_ROOT),
        metadata={"business_type": "paper_registration_metadata"},
    )
    extracted = _coerce_paper_registration_metadata(_parse_json_object_from_text(content))
    if extracted.get("session") not in {item["id"] for item in project_catalog}:
        extracted["session"] = ""
    return extracted, usage


def _lookup_ref_metadata(ref: dict) -> dict:
    raw = str(ref.get("raw") or "")
    title = str(ref.get("title") or "").strip()
    arxiv_id = _arxiv_id_from_text(f"{title}\n{raw}")
    if arxiv_id:
        return _fetch_arxiv_metadata(id_list=arxiv_id)
    if title:
        return _fetch_arxiv_metadata(search_title=title)
    guessed = _guess_ref_title(raw)
    if guessed:
        return _fetch_arxiv_metadata(search_title=guessed)
    return {}


def _fetch_arxiv_metadata(*, id_list: str = "", search_title: str = "") -> dict:
    params = {"start": "0", "max_results": "1"}
    if id_list:
        params["id_list"] = id_list
    elif search_title:
        params["search_query"] = f'ti:"{search_title[:180]}"'
    else:
        return {}
    url = f"https://export.arxiv.org/api/query?{urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": "TFIPortal/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            body = response.read(1_500_000)
    except (urllib.error.URLError, TimeoutError, OSError):
        return {}
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return {}
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    entry = root.find("atom:entry", ns)
    if entry is None:
        return {}
    return _metadata_from_arxiv_entry(entry, ns)


def _metadata_from_arxiv_entry(entry, ns: dict[str, str]) -> dict:
    def text(path: str) -> str:
        node = entry.find(path, ns)
        return re.sub(r"\s+", " ", node.text or "").strip() if node is not None else ""

    entry_id = text("atom:id")
    arxiv_id = _arxiv_id_from_text(entry_id)
    pdf_url = ""
    for link in entry.findall("atom:link", ns):
        if link.attrib.get("title") == "pdf" or link.attrib.get("type") == "application/pdf":
            pdf_url = link.attrib.get("href", "")
            break
    if not pdf_url and arxiv_id:
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    authors = []
    for author in entry.findall("atom:author", ns):
        name = author.find("atom:name", ns)
        if name is not None and str(name.text or "").strip():
            authors.append(re.sub(r"\s+", " ", name.text or "").strip())
    published = text("atom:published")
    return {
        "title": text("atom:title"),
        "authors": ", ".join(authors),
        "year": _safe_year(published),
        "abstract": text("atom:summary"),
        "arxiv_id": arxiv_id,
        "pdf_url": pdf_url,
        "source_url": entry_id or (f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else ""),
    }
