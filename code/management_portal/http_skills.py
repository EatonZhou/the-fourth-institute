"""The Skills board entry: the Skill catalog is the resource catalog's Skill category."""

from urllib.parse import parse_qs, quote

from management_portal.http_routes import RouteDefinition


class SkillsHttp:
    def home(self, request, parsed, actor):
        item_id = str(parse_qs(parsed.query).get("item", [""])[0]).strip()
        location = "/resource/skill"
        if item_id:
            location += f"?item={quote(item_id, safe='')}"
        request.send_response(302)
        request.send_header("Location", location)
        request.end_headers()

    def routes(self):
        """The Skills board entry."""
        return (
            RouteDefinition("GET", "/skills", self.home, "skills", page=True),
            RouteDefinition("GET", "/skills/", self.home, "skills", page=True),
        )
