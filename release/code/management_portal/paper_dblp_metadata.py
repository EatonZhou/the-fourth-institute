import json
import re
import urllib.error
import urllib.parse
import urllib.request


ENDPOINT = "https://sparql.dblp.org/sparql"
PREFIX = "PREFIX dblp: <https://dblp.org/rdf/schema#>\n"
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_ENTRIES = 1000
MAX_PAPERS = 10000
MAX_AUTHORS = 100000


class DblpMetadataError(RuntimeError):
    pass


def _value(row, name):
    binding = row.get(name)
    return str(binding.get("value") or "") if isinstance(binding, dict) else ""


def _query(query, limit):
    request = urllib.request.Request(
        ENDPOINT + "?" + urllib.parse.urlencode({"query": PREFIX + query + f"\nLIMIT {limit + 1}"}),
        headers={"Accept": "application/sparql-results+json", "User-Agent": "TFIPortalPaper/0.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            content = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        raise DblpMetadataError(f"DBLP 结构化数据接口返回 HTTP {error.code}，本地缓存未改动。") from error
    except OSError as error:
        raise DblpMetadataError("DBLP 结构化数据接口暂不可用，本地缓存未改动，请稍后重试。") from error
    if len(content) > MAX_RESPONSE_BYTES:
        raise DblpMetadataError("DBLP 目录数据超过单次读取上限，未缓存不完整结果。")
    try:
        payload = json.loads(content)
    except (ValueError, UnicodeDecodeError) as error:
        if b"anubis_challenge" in content:
            raise DblpMetadataError("DBLP 返回了访问验证页面，未获取到论文目录。本地缓存未改动，请稍后重试。") from error
        raise DblpMetadataError("DBLP 结构化数据接口返回了无效数据，本地缓存未改动。") from error
    results = payload.get("results") if isinstance(payload, dict) else None
    rows = results.get("bindings") if isinstance(results, dict) else None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise DblpMetadataError("DBLP 结构化数据接口缺少结果列表，本地缓存未改动。")
    if len(rows) > limit:
        raise DblpMetadataError("DBLP 目录条目超过单次读取上限，未缓存不完整结果。")
    return rows


def _venue_identity(venue):
    key = str(venue.get("dblp_conf_key") or "").strip()
    section = str(venue.get("dblp_section") or "conf").strip()
    if section not in {"conf", "journals"} or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", key):
        raise ValueError("invalid DBLP venue identity")
    return section, key


def _toc(venue, value):
    section, key = _venue_identity(venue)
    parsed = urllib.parse.urlsplit(str(value or ""))
    prefix = f"/db/{section}/{key}/"
    if parsed.scheme != "https" or parsed.netloc != "dblp.org" or parsed.query or parsed.fragment:
        raise DblpMetadataError("DBLP 返回了无效目录地址，本地缓存未改动。")
    filename = parsed.path.removeprefix(prefix)
    if not parsed.path.startswith(prefix) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", filename):
        raise DblpMetadataError("DBLP 目录不属于当前会议或期刊，本地缓存未改动。")
    stem = filename.removesuffix(".html")
    return f"https://dblp.org{prefix}{stem}", stem


def catalog_entries(venue):
    section, key = _venue_identity(venue)
    rows = _query(f"""SELECT ?toc ?year ?volume (COUNT(DISTINCT ?paper) AS ?count) WHERE {{
  ?paper dblp:publishedInStream <https://dblp.org/streams/{section}/{key}>;
         dblp:yearOfPublication ?year; dblp:listedOnTocPage ?toc; a ?kind.
  VALUES ?kind {{ dblp:Inproceedings dblp:Article }}
  OPTIONAL {{ ?paper dblp:publishedInJournalVolume ?volume }}
}} GROUP BY ?toc ?year ?volume ORDER BY DESC(?year) ?toc""", MAX_ENTRIES)
    entries = {}
    ranks = {}
    for row in rows:
        address, stem = _toc(venue, _value(row, "toc"))
        year_text = _value(row, "year")
        if not re.fullmatch(r"\d{4}", year_text):
            raise DblpMetadataError("DBLP 返回了无效目录年份，本地缓存未改动。")
        entry_id = year_text if section == "conf" else stem
        count_text = _value(row, "count")
        if not count_text.isdigit():
            raise DblpMetadataError("DBLP 返回了无效论文数量，本地缓存未改动。")
        entry = {
            "entry_id": entry_id, "year": int(year_text), "html_url": address,
            "file": stem, "count": int(count_text), "metadata_source": "dblp-sparql",
        }
        if section == "conf":
            main = f"{key}{year_text}".casefold()
            rank = (0 if stem.casefold() == main else 1 if stem.casefold().startswith(main) else 2, len(stem), stem)
            if entry_id in ranks and ranks[entry_id] <= rank:
                continue
            ranks[entry_id] = rank
        else:
            volume = _value(row, "volume")
            entry.update(issue=volume, label=f"Volume {volume}" if volume else stem)
            previous = entries.get(entry_id)
            if previous:
                entry["count"] += previous["count"]
                entry["year"] = max(entry["year"], previous["year"])
        entries[entry_id] = entry
    if not entries:
        raise DblpMetadataError("DBLP 未返回可识别的年份或期次目录，本地缓存未改动。")
    return sorted(entries.values(), key=lambda entry: (entry["year"], entry["entry_id"]), reverse=True)


def papers_for_entry(venue, entry):
    section, key = _venue_identity(venue)
    address, _ = _toc(venue, entry.get("html_url"))
    selector = f"""?paper dblp:publishedInStream <https://dblp.org/streams/{section}/{key}>;
    dblp:listedOnTocPage <{address}>; a ?kind.
  VALUES ?kind {{ dblp:Inproceedings dblp:Article }}"""
    rows = _query(f"""SELECT DISTINCT ?paper ?title ?year ?doi ?document ?pages ?booktitle WHERE {{
  {selector}
  ?paper dblp:title ?title; dblp:yearOfPublication ?year.
  OPTIONAL {{ ?paper dblp:doi ?doi }}
  OPTIONAL {{ ?paper dblp:primaryDocumentPage ?document }}
  OPTIONAL {{ ?paper dblp:pagination ?pages }}
  OPTIONAL {{ ?paper dblp:publishedIn ?booktitle }}
}} ORDER BY ?paper ?doi ?document""", MAX_PAPERS)
    if not rows:
        raise DblpMetadataError("DBLP 未返回此目录的论文列表，本地缓存未改动。")
    papers = {}
    for row in rows:
        record = _value(row, "paper")
        record_prefix = f"https://dblp.org/rec/{section}/{key}/"
        if not record.startswith(record_prefix) or re.search(r'[<>"\s]', record):
            raise DblpMetadataError("DBLP 论文标识与当前目录不一致，本地缓存未改动。")
        title = _value(row, "title").strip().rstrip(".")
        year_text = _value(row, "year")
        if not title or not re.fullmatch(r"\d{4}", year_text):
            raise DblpMetadataError("DBLP 论文缺少有效标题或年份，本地缓存未改动。")
        if record in papers:
            continue
        doi = _value(row, "doi")
        document = _value(row, "document")
        document = document if urllib.parse.urlsplit(document).scheme in {"https", "http"} else ""
        doi = doi.removeprefix("https://doi.org/").removeprefix("http://doi.org/")
        papers[record] = {
            "dblp_key": record.removeprefix("https://dblp.org/rec/"), "title": title,
            "authors": "", "year": int(year_text), "venue": str(venue.get("abbr") or ""),
            "booktitle": _value(row, "booktitle"), "pages": _value(row, "pages"),
            "doi": doi, "ee": document, "dblp_url": record,
            "ccf_rank": str(venue.get("rank") or ""), "ccf_category": str(venue.get("category") or ""),
            "ccf_source": str(venue.get("source") or ""), "metadata_source": "dblp-sparql",
        }
    signatures = _query(f"""SELECT DISTINCT ?paper ?ordinal ?name WHERE {{
  {selector}
  ?paper dblp:hasSignature ?signature.
  ?signature a dblp:AuthorSignature; dblp:signatureOrdinal ?ordinal; dblp:signatureDblpName ?name.
}} ORDER BY ?paper ?ordinal""", MAX_AUTHORS)
    authors = {}
    for row in signatures:
        record, ordinal, name = _value(row, "paper"), _value(row, "ordinal"), _value(row, "name")
        if record not in papers or not name or not ordinal.isdigit():
            raise DblpMetadataError("DBLP 作者列表与论文目录不一致，本地缓存未改动。")
        authors.setdefault(record, {})[int(ordinal)] = name
    for record, paper in papers.items():
        names = authors.get(record, {})
        paper["authors"] = ", ".join(names[position] for position in sorted(names))
    return list(papers.values())
