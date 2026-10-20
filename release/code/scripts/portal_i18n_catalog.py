"""Extract literal presentation text and annotate static HTML ownership."""
from __future__ import annotations

import argparse
import ast
import html
import json
import re
import string
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = {"script", "style", "pre", "code", "textarea"}
ATTRIBUTES = {"title", "aria-label", "placeholder", "alt", "data-portal-tooltip"}


class PresentationParser(HTMLParser):
    def __init__(self, source: str, records: set[str], *, annotate: bool = False):
        super().__init__(convert_charrefs=True)
        self.source, self.records, self.annotate = source, records, annotate
        self.stack = []
        self.edits = []
        self.lines = [0]
        for match in re.finditer("\n", source):
            self.lines.append(match.end())

    def position_offset(self):
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag, attrs):
        attr_map = dict(attrs)
        excluded = bool(tag in SKIP or attr_map.get("translate") == "no" or (self.stack and self.stack[-1][1]))
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append((tag, excluded))
        if excluded:
            return
        strings = {key: value for key, value in attrs if key in ATTRIBUTES and value and useful(value)}
        self.records.update(value.strip() for value in strings.values())
        if strings and self.annotate and "data-portal-i18n-attrs" not in attr_map:
            original = self.get_starttag_text()
            offset = self.position_offset() + len(original) - (2 if original.endswith("/>") else 1)
            metadata = html.escape(json.dumps(strings, ensure_ascii=False), quote=True)
            self.edits.append((offset, offset, f' data-portal-i18n-attrs="{metadata}"'))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if (self.stack and self.stack[-1][1]) or not useful(data):
            return
        self.records.add(data.strip())
        if self.annotate:
            start = self.position_offset()
            if self.stack and self.stack[-1][0] == "title":
                opening = self.source.rfind("<title", 0, start)
                closing = self.source.index(">", opening)
                self.edits.append((closing, closing, ' data-portal-i18n-text="' + html.escape(json.dumps(data, ensure_ascii=False), quote=True) + '"'))
                return
            # Catalog text is encoded as JSON, with HTML comment terminators escaped.
            marker = json.dumps(data, ensure_ascii=False).replace("--", "\\u002d\\u002d").replace(">", "\\u003e")
            self.edits.append((start, start, "<!--portal-i18n:" + marker + "-->"))

    def output(self):
        result = self.source
        for start, end, value in sorted(self.edits, reverse=True):
            result = result[:start] + value + result[end:]
        return result


def useful(value):
    value = value.strip()
    return bool(value and (re.search(r"[\u3400-\u9fff]", value) or re.search(r"[A-Za-z]{2}", value))) and not value.startswith(("http://", "https://", "/static/"))


def backend_messages(records):
    # Errors are catalogued at their defining boundary; operational records are untouched.
    for directory in [ROOT / "management_portal", ROOT / "knowledge_board_lan"]:
        for path in directory.rglob("*.py"):
            if "dsh_runtime" in path.parts:
                continue
            # Instruction declarations: the HATG catalog and the features' instruction modules.
            declares = "catalog" in path.parts or path.name.endswith("_instructions.py")
            try:
                tree = ast.parse(path.read_text())
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                message = None
                function = node.func.id if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) else node.func.attr if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) else ""
                if isinstance(node, ast.Call) and function.endswith(("Error", "Exception")) and node.args:
                    message = node.args[0]
                elif isinstance(node, ast.Dict):
                    for key, value in zip(node.keys, node.values):
                        fields = {"error", "message", "label", "help", "description", "category_title", "owner_title"} if declares else {"error", "message"}
                        if isinstance(key, ast.Constant) and key.value in fields:
                            collect_message(value, records)
                elif declares and isinstance(node, ast.keyword) and node.arg in {"description", "category_title", "owner_title", "label", "help"}:
                    collect_message(node.value, records)
                if message is not None:
                    collect_message(message, records)


def collect_message(node, records):
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and useful(node.value):
        records.add(node.value.strip())
    elif isinstance(node, ast.JoinedStr):
        parts, index = [], 0
        for part in node.values:
            if isinstance(part, ast.Constant):
                parts.append(str(part.value))
            else:
                parts.append("{p" + str(index) + "}")
                index += 1
        value = "".join(parts)
        if useful(value):
            records.add(value.strip())
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format" and isinstance(node.func.value, ast.Constant) and isinstance(node.func.value.value, str):
        try:
            fields, parts = {}, []
            for literal, field, _spec, _conversion in string.Formatter().parse(node.func.value.value):
                parts.append(literal)
                if field is not None:
                    fields.setdefault(field, len(fields))
                    parts.append("{p" + str(fields[field]) + "}")
            if useful("".join(parts)):
                records.add("".join(parts).strip())
        except ValueError:
            pass
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod) and isinstance(node.left, ast.Constant) and isinstance(node.left.value, str):
        index = 0
        def placeholder(match):
            nonlocal index
            result = "{p" + str(index) + "}"
            index += 1
            return result
        value = re.sub(r"%(?:\([^)]+\))?[#0 +\-]?[\d.*]*(?:[srdifge])", placeholder, node.left.value)
        if useful(value):
            records.add(value.strip())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", default="/tmp/portal-i18n-sources.json")
    parser.add_argument("--output", default="/tmp/portal-i18n-messages.json")
    parser.add_argument("--annotate", action="store_true")
    args = parser.parse_args()
    source = json.loads(Path(args.sources).read_text())
    records = set(source["records"])
    for markup in source["htmlSources"]:
        reader = PresentationParser(markup, records)
        reader.feed(markup)
    backend_messages(records)
    manifest = json.loads((ROOT / "management_portal/static/lineage_primitive_definitions.json").read_text())
    def collect_definition(value):
        if isinstance(value, list):
            for item in value:
                collect_definition(item)
        elif isinstance(value, dict):
            for key, child in value.items():
                if key in {"title", "description", "label", "help", "text", "placeholder", "enabled_label", "disabled_label", "category_title", "owner_title", "purpose"} and isinstance(child, str) and useful(child):
                    records.add(child.strip())
                else:
                    collect_definition(child)
    collect_definition(manifest)
    if args.annotate:
        for directory in ["management_portal", "knowledge_board_lan", "project_board_lan"]:
            for path in (ROOT / directory).glob("*.html"):
                raw = path.read_text()
                if "portal-i18n:" in raw:
                    continue
                reader = PresentationParser(raw, records, annotate=True)
                reader.feed(raw)
                result = reader.output()
                # Every page loads the same catalog and language owner before its app scripts.
                if "/static/portal_i18n.js" in result:
                    if "/static/portal_text.js" not in result:
                        result = result.replace('<script src="/static/portal_translations.js">', '<script src="/static/portal_text.js"></script>\n    <script src="/static/portal_translations.js">', 1)
                elif "/static/portal_state.js" in result:
                    result = re.sub(r'(<script\b[^>]*src="/static/portal_state.js"[^>]*></script>)', r'\1\n    <script src="/static/portal_text.js"></script>\n    <script src="/static/portal_translations.js"></script>\n    <script src="/static/portal_i18n.js"></script>', result, count=1)
                elif "<head>" in result:
                    result = result.replace("<head>", '<head>\n    <script src="/static/portal_text.js"></script>\n    <script src="/static/portal_translations.js"></script>\n    <script src="/static/portal_i18n.js"></script>', 1)
                path.write_text(result)
    Path(args.output).write_text(json.dumps(sorted(records), ensure_ascii=False, indent=2))
    print(json.dumps({"messages": len(records), "output": args.output}))


if __name__ == "__main__":
    main()
