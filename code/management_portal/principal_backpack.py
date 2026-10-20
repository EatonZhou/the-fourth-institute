from __future__ import annotations

from management_portal.resident_avatar import get_avatar
from management_common.principal import actor_principal


class PrincipalBackpack:
    def __init__(self, *, access, resources, credential_usernames=()):
        self.access = access
        self.resources = resources
        self.usernames = tuple(credential_usernames)

    def _principal(self, principal):
        identity = actor_principal(principal)
        if identity.kind != "user" or not self.access.is_active(identity.id):
            raise PermissionError("需要有效的 Portal 登录身份")
        return identity

    @staticmethod
    def _user(record):
        return {"id": record["username"], "kind": "user", "title": record.get("display_name") or record["username"], "status": record.get("status", ""), "level": record.get("category") or "member"}

    @staticmethod
    def _resident(record):
        attributes = record.get("attributes") or {}
        return {"id": record["resource_id"], "kind": "resident", "title": record.get("title") or record["resource_id"], "status": record.get("status") or attributes.get("status", ""), "level": (attributes.get("principal_hierarchy") or {}).get("level", "unassigned")}

    def catalog(self, principal, *, query="", offset=0, limit=60):
        from management_common.resource_identity import get_resource_type_registry

        self._principal(principal)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("分页范围不正确")
        query = str(query or "").strip()[:160]
        users = self.access.list_users(self.usernames)
        users = [self._user(row) for row in users if row.get("username") and query.casefold() in (str(row.get("display_name", "")) + row["username"]).casefold()]
        page = self.resources.query(resource_types=["resident"], actor=principal, q=query, offset=offset, limit=limit, include_attributes=True)
        residents = [self._resident(row) for row in page.get("resources", [])]
        return {"principal": principal, "resource_types": sorted(get_resource_type_registry().types), "users": users, "residents": residents, "offset": offset, "limit": limit, "resident_total": page.get("total", len(residents)), "has_more": bool(page.get("has_more", offset + len(residents) < page.get("total", 0)))}

    def detail(self, principal, target):
        self._principal(principal)
        identity = actor_principal(target)
        possessions = []
        if identity.kind == "user":
            matches = [row for row in self.access.list_users(self.usernames) if row.get("username") == target]
            if not matches:
                raise KeyError("User 不存在")
            record = matches[0]
            summary = self._user(record)
            from management_portal.user_avatar import get_user_avatar
            summary["avatar"] = get_user_avatar(target)
            settings_url = "/settings"
        elif identity.kind == "resident":
            record = self.resources.get(target, actor=principal)
            if not record or record.get("resource_type") != "resident":
                raise KeyError("Resident 不存在或不可读取")
            summary = self._resident(record)
            summary["avatar"] = get_avatar(target)
            attributes = record.get("attributes") or {}
            for project in record.get("project_ids") or attributes.get("project_ids") or []:
                possessions.append({"kind": "project", "id": project, "title": project, "level": "", "source": "项目关联"})
            for relation in attributes.get("relations") or record.get("relations") or []:
                if relation.get("kind") in {"workspace", "lineage"} and relation.get("target_id"):
                    possessions.append({"kind": relation["kind"], "id": relation["target_id"], "title": relation["target_id"], "level": "", "source": "已登记绑定"})
            settings_url = "/lineage?resident=" + target
        else:
            raise ValueError("背包只支持 User 与 Resident")
        result = {"principal": summary, "possessions": possessions, "settings_url": settings_url, "note": "角色与关联不代表权限凭证；生效权限单独读取并校验。"}
        return result
