"""Deterministic, escaped presentation exports of the same report snapshot."""
import html
import json
from zipfile import ZipFile, ZIP_DEFLATED
from xml.sax.saxutils import escape


def write_exports(report, directory):
    (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    e = lambda value: html.escape(str(value or ""))
    sections = [(report["title"], report["goal"])]
    sections += [(row["title"], "\n".join(str(row.get(k) or "") for k in ("status", "body", "detail", "conclusion", "next"))) for row in report["rows"]]
    sources = "\n".join(f'{s["label"]}: {s.get("address") or s["read"]} · {s["version"]}' for s in report["sources"])
    body = "".join(f"<section><h2>{e(title)}</h2><pre>{e(text)}</pre></section>" for title, text in sections)
    document = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
    document += '<title>'+e(report["title"])+'</title><style>body{font:17px system-ui;max-width:960px;margin:32px auto;padding:16px;color:#24344b}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}section{border-bottom:1px solid #dce2ef;padding:12px 0}</style>'
    document += f'<p>核对时间 {e(report["checked_at"])} · 快照 {e(report["version"])}</p>' + body
    document += '<h2>来源与版本</h2><pre>'+e(sources)+'</pre></html>'
    (directory / "report.html").write_text(document, encoding="utf-8")
    write_pptx(sections + [("来源与核对时间", report["checked_at"] + "\n" + sources)], directory / "slides.pptx")


def write_pptx(sections, path):
    """OOXML text deck. Split long passages into readable continuation slides."""
    pages = []
    for title, body in sections:
        text = str(body or "")
        for start in range(0, max(1, len(text)), 650):
            pages.append((str(title) + ("（续）" if start else ""), text[start:start + 650]))
    ns = 'http://schemas.openxmlformats.org/'
    package = ns + 'package/2006/relationships'
    office = ns + 'officeDocument/2006/relationships/'
    p = ns + 'presentationml/2006/main'
    a = ns + 'drawingml/2006/main'
    def rel(items):
        return f'<Relationships xmlns="{package}">' + ''.join(f'<Relationship Id="rId{i}" Type="{office}{kind}" Target="{target}"/>' for i, kind, target in items) + '</Relationships>'
    types = '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
    types += '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
    types += ''.join(f'<Override PartName="/ppt/slides/slide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>' for i in range(1,len(pages)+1)) + '</Types>'
    with ZipFile(path, 'w', ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', types)
        z.writestr('_rels/.rels', rel([(1,'officeDocument','ppt/presentation.xml')]))
        z.writestr('ppt/presentation.xml', f'<p:presentation xmlns:p="{p}" xmlns:r="{office[:-1]}"><p:sldIdLst>' + ''.join(f'<p:sldId id="{256+i}" r:id="rId{i}"/>' for i in range(1,len(pages)+1)) + '</p:sldIdLst><p:sldSz cx="12192000" cy="6858000"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>')
        z.writestr('ppt/_rels/presentation.xml.rels', rel([(i,'slide',f'slides/slide{i}.xml') for i in range(1,len(pages)+1)]))
        for i, (title, body) in enumerate(pages,1):
            paragraphs = ''.join(f'<a:p><a:r><a:rPr lang="zh-CN" sz="{size}"/><a:t>{escape(line)}</a:t></a:r></a:p>' for line,size in [(title,2800),*( (line,1600) for line in body.splitlines())])
            z.writestr(f'ppt/slides/slide{i}.xml', f'<p:sld xmlns:p="{p}" xmlns:a="{a}"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/><p:sp><p:nvSpPr><p:cNvPr id="2" name="Report"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="457200" y="457200"/><a:ext cx="11277600" cy="5943600"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/>{paragraphs}</p:txBody></p:sp></p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>')
