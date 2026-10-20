"""The Skills board entry: the Skill catalog is the resource catalog's Skill category."""

from urllib.parse import parse_qs, quote


class SkillsHttp:
    def home(self, request, parsed, actor):
        item_id = str(parse_qs(parsed.query).get("item", [""])[0]).strip()
        location = "/resource/skill"
        if item_id:
            location += f"?item={quote(item_id, safe='')}"
        request.send_response(302)
        request.send_header("Location", location)
        request.end_headers()
