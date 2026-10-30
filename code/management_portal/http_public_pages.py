"""Public Portal pages and allowlisted static assets."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from urllib.parse import parse_qs

from management_portal.http_routes import RouteDefinition
from management_portal import script_groups


_APPLICATION_JS = {
    "portal_projections.js", "projection_host.js", "projection_sdk.js",
    "workbench_studio.js",
}
# These reviewed host dependencies have nested paths. This is deliberately an
# exact map, not a public mount of the projection library registry.
PUBLIC_SHARED_STATIC_ASSETS = {
    "/static/projection_libraries/portal-graph/1.0.0/geometry.js":
        "projection_libraries/portal-graph/1.0.0/geometry.js",
}


class PublicPagesHttp:
    def __init__(self, *, root, check_session, serve_bytes, serve_static,
                 send_empty, prepare_login, prepare_verify, request_cookie,
                 challenge_cookie, challenge_username, clear_challenge,
                 route_script, respond, media_content_type):
        self.root = root
        self.check_session = check_session
        self.serve_bytes = serve_bytes
        self.serve_static = serve_static
        self.send_empty = send_empty
        self.prepare_login = prepare_login
        self.prepare_verify = prepare_verify
        self.request_cookie = request_cookie
        self.challenge_cookie = challenge_cookie
        self.challenge_username = challenge_username
        self.clear_challenge = clear_challenge
        self.route_script = route_script
        self.respond = respond
        self.media_content_type = media_content_type

    @staticmethod
    def _redirect(request, location):
        request.send_response(302)
        request.send_header("Location", location)
        request.end_headers()

    def login(self, request, parsed, _actor):
        if self.check_session(request):
            return self._redirect(request, "/")
        return self.serve_bytes(request, 200, self.prepare_login("error" in parse_qs(parsed.query)), "text/html; charset=utf-8")

    def login_verify(self, request, parsed, _actor):
        if self.check_session(request):
            return self._redirect(request, "/")
        token = self.request_cookie(request, self.challenge_cookie)
        if not self.challenge_username(token):
            request.send_response(302)
            self.clear_challenge(request)
            request.send_header("Location", "/login")
            request.end_headers()
            return
        return self.serve_bytes(request, 200, self.prepare_verify("error" in parse_qs(parsed.query)), "text/html; charset=utf-8")

    def forgot_password(self, request, _parsed, _actor):
        return self.serve_bytes(request, 200, (self.root / "forgot_password.html").read_bytes(),
                                "text/html; charset=utf-8", extra_headers={"Referrer-Policy": "no-referrer"})

    def reset_password(self, request, _parsed, _actor):
        return self.serve_bytes(request, 200, (self.root / "reset_password.html").read_bytes(),
                                "text/html; charset=utf-8", extra_headers={"Referrer-Policy": "no-referrer"})

    def resource_routes(self, request, _parsed, _actor):
        return self.serve_bytes(request, 200, self.route_script(), "text/javascript; charset=utf-8")

    def static(self, request, parsed, _actor):
        filename = Path(parsed.path).name
        if parsed.path in PUBLIC_SHARED_STATIC_ASSETS:
            path = self.root / "static" / PUBLIC_SHARED_STATIC_ASSETS[parsed.path]
        else:
            path = self.root / "static" / filename
        if filename.endswith(".css"):
            content_type = "text/css; charset=utf-8"
        elif filename.endswith(".json"):
            content_type = "application/json; charset=utf-8"
        elif filename in _APPLICATION_JS:
            content_type = "application/javascript; charset=utf-8"
        else:
            content_type = "text/javascript; charset=utf-8"
        return self.serve_static(request, path, content_type)

    def vendor(self, request, parsed, _actor):
        rel = parsed.path.removeprefix("/static/vendor/").strip("/")
        if not rel or "\\" in rel:
            return self.send_empty(request, 404)
        root = (self.root / "static" / "vendor").resolve(strict=False)
        path = (root / rel).resolve(strict=False)
        if root not in path.parents or not path.is_file():
            return self.send_empty(request, 404)
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        if path.suffix in {".js", ".mjs"}:
            content_type = "text/javascript; charset=utf-8"
        elif path.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif path.suffix in {".woff2", ".woff", ".ttf"}:
            content_type = {".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf"}[path.suffix]
        return self.serve_static(request, path, content_type)

    def asset(self, request, parsed, _actor):
        rel = parsed.path.removeprefix("/static/assets/").strip("/")
        if not rel or "/" in rel or "\\" in rel:
            return self.send_empty(request, 404)
        root = (self.root / "static" / "assets").resolve(strict=False)
        path = (root / rel).resolve(strict=False)
        if root not in path.parents or not path.is_file():
            return self.send_empty(request, 404)
        content_type = self.media_content_type(path)
        if not content_type:
            return self.send_empty(request, 404)
        return self.serve_static(request, path, content_type)

    def routes(self):
        """The sign-in pages and the static files anyone may load."""
        return (
            RouteDefinition("GET", "/forgot-password", self.forgot_password, "portal-public-get", phase="public"),
            RouteDefinition("GET", "/login", self.login, "portal-public-get", phase="public"),
            RouteDefinition("GET", "/login/verify", self.login_verify, "portal-public-get", phase="public"),
            RouteDefinition("GET", "/reset-password", self.reset_password, "portal-public-get", phase="public"),
            RouteDefinition("GET", "/static/portal_resource_routes.js", self.resource_routes, "portal-public-get", phase="public"),
            *(
                RouteDefinition("GET", path, self.static, "portal-public-get", phase="public")
                for path in (
                    '/static/hatg_mailbox_panel.js',
                    '/static/hatg_graph_model.js',
                    '/static/hatg_lineage_state.js',
                    '/static/hatg_program_edits.js',
                    '/static/hatg_family_blocks.js',
                    '/static/hatg_node_status.js',
                    '/static/hatg_canvas_selection.js',
                    '/static/hatg_function_block_menu.js',
                    '/static/hatg_program_inspector_references.js',
                    '/static/hatg_program_inspector_primitive_fields.js',
                    '/static/hatg_program_inspector_delivery.js',
                    '/static/hatg_program_inspector_observation.js',
                    '/static/hatg_program_inspector_manual_events.js',
                    '/static/hatg_program_inspector_saving.js',
                    '/static/hatg_program_inspector_render.js',
                    '/static/hatg_program_inspector.js',
                    '/static/hatg_relations.js',
                    '/static/hatg_layout.js',
                    '/static/hatg_toolbar.js',
                    '/static/hatg_graph_projection.js',
                    '/static/hatg_graph_view.js',
                    '/static/hatg_resident_runtime.js',
                    '/static/hatg_workspace_results.js',
                    '/static/hatg_resident_dialogs.js',
                    '/static/hatg_lineage_dialogs.js',
                    '/static/hatg_seeds.js',
                    '/static/hatg_leader.js',
                    '/static/hatg_graph_search.js',
                    '/static/hatg_panel.css',
                    '/static/hatg_panel_edges.css',
                    '/static/hatg_panel_nodes.css',
                    '/static/hatg_panel_navigation.css',
                    '/static/hatg_panel_library.css',
                    '/static/hatg_panel_inspector.css',
                    '/static/hatg_panel_relations.css',
                    '/static/hatg_panel_dialogs.css',
                    '/static/hatg_panel_responsive.css',
                    '/static/hatg_panel_effect_scripts.css',
                    '/static/hatg_panel_lineage_loading.js',
                    '/static/hatg_panel_template_shelf.js',
                    '/static/hatg_panel_bindings.js',
                    '/static/hatg_panel_mount.js',
                    '/static/hatg_panel.js',
                    '/static/portal_script_runs.js',
                    '/static/hatg_panel_features.js',
                    '/static/hatg_panel_state.js',
                    '/static/hatg_workspace_projection.js',
                    '/static/home.css',
                    '/static/home.js',
                    '/static/home_agent_access.js',
                    '/static/home_pulse.js',
                    '/static/home_system_resources.js',
                    '/static/home_accounts.js',
                    '/static/home_usage.js',
                    '/static/home_scheduled_triggered.js',
                    '/static/home_activity.js',
                    '/static/home_workbench.css',
                    '/static/lineage_element_fields.js',
                    '/static/lineage_function_blocks.css',
                    '/static/lineage_graph.js',
                    '/static/lineage_graph_camera.js',
                    '/static/lineage_graph_canvas_events.js',
                    '/static/lineage_graph_cards.js',
                    '/static/lineage_graph_drag.js',
                    '/static/lineage_graph_edge_routes.js',
                    '/static/lineage_graph_edges.js',
                    '/static/lineage_graph_element_index.js',
                    '/static/lineage_graph_function_blocks.js',
                    '/static/lineage_graph_hover.js',
                    '/static/lineage_graph_layout.js',
                    '/static/lineage_graph_minimap.js',
                    '/static/lineage_graph_nodes.js',
                    '/static/lineage_graph_ports.js',
                    '/static/lineage_graph_quick_fork.js',
                    '/static/lineage_graph_render.js',
                    '/static/lineage_graph_selection.js',
                    '/static/lineage_graph_viewport.js',
                    '/static/lineage_graph_wiring.js',
                    '/static/lineage_invocation_inspector.js',
                    '/static/lineage_primitive_definitions.json',
                    '/static/lineage_primitive_inspector.js',
                    '/static/lineage_program.js',
                    '/static/lineage_template_graph.js',
                    '/static/lineage_templates.js',
                    '/static/lineage_templates_application.js',
                    '/static/lineage_templates_dialogs.js',
                    '/static/lineage_templates_editing.js',
                    '/static/lineage_templates_editor_actions.js',
                    '/static/lineage_templates_editor_draft.js',
                    '/static/lineage_templates_editor_elements.js',
                    '/static/lineage_templates_editor_inspector.js',
                    '/static/lineage_templates_editor_pages.js',
                    '/static/lineage_templates_resident_settings.js',
                    '/static/lineage_templates_shelf.js',
                    '/static/lineage_workbench.css',
                    '/static/lineage_workbench.js',
                    '/static/lineage_workbench_controller.js',
                    '/static/lineage_workbench_preview.js',
                    '/static/lineage_workbench_preview_chat_links.js',
                    '/static/lineage_workbench_preview_documents.js',
                    '/static/lineage_workbench_preview_editing.js',
                    '/static/lineage_workbench_preview_files.js',
                    '/static/lineage_workbench_preview_links.js',
                    '/static/lineage_workbench_preview_refresh.js',
                    '/static/lineage_workbench_preview_rendering.js',
                    '/static/lineage_workbench_recent.js',
                    '/static/lineage_workbench_resident_dialogs.js',
                    '/static/lineage_workbench_sidebar.js',
                    '/static/lineage_workbench_sidebar_agent_list.js',
                    '/static/lineage_workbench_sidebar_favorites.js',
                    '/static/lineage_workbench_sidebar_filters.js',
                    '/static/lineage_workbench_sidebar_reveal.js',
                    '/static/lineage_workbench_sidebar_snapshot.js',
                    '/static/lineage_workbench_sidebar_targets.js',
                    '/static/lineage_workbench_state.js',
                    '/static/lineage_workbench_card_menus.js',
                    '/static/lineage_workbench_chatrooms.js',
                    '/static/lineage_workbench_location.js',
                    '/static/lineage_workbench_chat_targets.js',
                    '/static/lineage_workbench_selection.js',
                    '/static/lineage_workbench_chat_detail.js',
                    '/static/lineage_workbench_layout.js',
                    '/static/lineage_workbench_editors.js',
                    '/static/lineage_workbench_dialog_controls.js',
                    '/static/lineage_workbench_watchboard.js',
                    '/static/lineage_workbench_which_resident.js',
                    '/static/markdown.js',
                    '/static/portal_agent_inbox.js',
                    '/static/portal_mailbox_todos.js',
                    '/static/portal_api.js',
                    '/static/portal_board_shell.js',
                    '/static/portal_button_tooltips.js',
                    '/static/portal_model_api_settings.js',
                    '/static/portal_resident_runtime_controller.js',
                    *script_groups.scripts("chat-workspace"),
                    '/static/portal_resident_avatar.css',
                    '/static/portal_resident_avatar.js',
                    '/static/portal_resident_menu.js',
                    '/static/portal_resident_menu_events.js',
                    '/static/portal_resident_menu_family.js',
                    '/static/portal_resident_menu_group.js',
                    '/static/portal_resident_menu_identity.js',
                    '/static/portal_resident_menu_journal.js',
                    '/static/portal_resident_menu_resident.js',
                    '/static/portal_resident_menu_runtime.js',
                    '/static/portal_resident_menu_tabs.js',
                    '/static/portal_resident_menu_transfer.js',
                    '/static/portal_resident_menu.css',
                    '/static/portal_workspace_menu.js',
                    '/static/portal_workspace_menu.css',
                    '/static/portal_chatroom_view.js',
                    '/static/portal_command_palette.js',
                    '/static/portal_component_recovery.js',
                    '/static/portal_components.css',
                    '/static/portal_components_markdown.css',
                    '/static/portal_components_chat_frame.css',
                    '/static/portal_components_messages.css',
                    '/static/portal_components_composer.css',
                    '/static/portal_components_queue.css',
                    '/static/portal_components_resource_chips.css',
                    '/static/portal_components_agent_inbox.css',
                    '/static/portal_components_turn_cards.css',
                    '/static/portal_components_conversation.css',
                    '/static/portal_conversation_store.js',
                    '/static/portal_demo_frame.js',
                    '/static/portal_demo_player.js',
                    '/static/portal_design_system.css',
                    '/static/portal_detail_surface.js',
                    '/static/portal_editor_open_service.js',
                    '/static/portal_events.js',
                    '/static/portal_file_links.js',
                    '/static/portal_form_controls.js',
                    '/static/portal_hatg_loader.js',
                    '/static/portal_home_components.js',
                    '/static/portal_text.js',
                    '/static/portal_i18n.js',
                    '/static/portal_identity.js',
                    '/static/portal_image_editor.css',
                    '/static/portal_image_editor.js',
                    '/static/portal_instruction_client.js',
                    '/static/portal_user_enrollment.js',
                    '/static/portal_sha256.js',
                    '/static/portal_user_enrollment.css',
                    '/static/portal_lineage_catalogs.js',
                    '/static/portal_society_service.js',
                    '/static/portal_live_tour.css',
                    '/static/portal_live_tour.js',
                    '/static/portal_management.js',
                    '/static/portal_management_accounts.js',
                    '/static/portal_management_execution.js',
                    '/static/portal_management_guards.js',
                    '/static/portal_management_layout.js',
                    '/static/portal_management_paths.js',
                    '/static/portal_management_permission_system.js',
                    '/static/portal_management_sections.js',
                    '/static/portal_management_services.js',
                    '/static/portal_management_users.js',
                    '/static/portal_mermaid.mjs',
                    '/static/portal_mermaid_realm.mjs',
                    '/static/portal_nav.js',
                    '/static/portal_overlay.js',
                    '/static/portal_page_state.js',
                    '/static/portal_personal_workspaces.js',
                    '/static/portal_passkeys.js',
                    '/static/portal_pdf_preview.js',
                    '/static/portal_preview_contract.js',
                    '/static/portal_preview_renderers.js',
                    '/static/portal_preview_runtime.js',
                    '/static/portal_projections.js',
                    '/static/portal_refresh_coordinator.js',
                    '/static/portal_resident_discovery_service.js',
                    '/static/portal_resource_chips.js',
                    '/static/portal_resource_header.js',
                    '/static/portal_state.js',
                    '/static/portal_theme.js',
                    '/static/portal_themes.css',
                    '/static/portal_translations.js',
                    '/static/portal_ui.js',
                    '/static/portal_ui_primitives.js',
                    '/static/portal_workbench.css',
                    *script_groups.scripts("portal-workbench"),
                    '/static/portal_document_statistics.js',
                    *script_groups.scripts("portal-workspace-surface"),
                    '/static/projection_host.js',
                    '/static/projection_sdk.js',
                    '/static/resident_projection.js',
                    '/static/resident_protocol.js',
                    '/static/settings.css',
                    '/static/settings.js',
                    '/static/development_workspace.js',
                    '/static/workbench_studio.css',
                    '/static/workbench_studio.js',
                    '/static/portal_workbench_catalog.css',
                    '/static/portal_workbench_catalog.js',
                )
            ),
            *(RouteDefinition("GET", path, self.static, "portal-public-get", phase="public")
              for path in PUBLIC_SHARED_STATIC_ASSETS),
            RouteDefinition("GET", "/static/assets/", self.asset, "portal-public-get", match="prefix", phase="public"),
            RouteDefinition("GET", "/static/vendor/", self.vendor, "portal-public-get", match="prefix", phase="public"),
        )
