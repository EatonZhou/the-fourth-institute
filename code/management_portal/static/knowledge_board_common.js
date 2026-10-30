// Knowledge board: helpers every part of the page uses.
//
// The board's parts are classic scripts that share the page's global scope.
// They only declare functions and constants; knowledge_board.js, loaded last,
// creates the page state, binds the events and starts the page.

// ── markdown renderer (shared) ────────────────────────────────────────
const renderMarkdown = (src, options = {}) => window.MD.render(src, options) || "<p></p>";
const renderInlineMarkdown = (text) => window.MD.renderInline(text);
const escapeHtml = (value) => window.PortalUIPrimitives.escapeHtml(value);
const portalIcon = (name, options = {}) => window.PortalUIPrimitives.iconHtml(name, options);
const knowledgeStateHtml = (message, options = {}) => window.PortalUIPrimitives.asyncStateHtml({
  state: options.state || "empty",
  title: options.title || "",
  message,
  density: options.density || "compact",
  icon: options.icon,
});
const verticalDisclosureIcon = (expanded) => portalIcon(expanded ? "chevron-up" : "chevron-down");
const horizontalDisclosureIcon = (expanded) => portalIcon(expanded ? "chevron-left" : "chevron-right");
const MESSAGE_ATTACHMENT_REF_RE = /\[(image|file):\s*([^\]]+)\]/g;

function byId(id) {
  return state.raw?.items?.find((x) => x.kb_id === id) || null;
}

function alertError(err) {
  window.PortalUI.toast(window.PortalUI.errorMessage(window.PortalI18n.t("操作"), err), "error");
}
