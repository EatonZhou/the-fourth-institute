"""Reproducible, hash-pinned patch for the 2.4.1 native prompt contract."""
import hashlib
import shutil
from pathlib import Path

PATCH_ID = 'portal-deny-all-v1'
BASE_SHA256 = 'fd15d5c9b48892bb4f1d5fe82df9f790c8c19a44ca9a81054cc592b6fb9e2c90'
MARKER = '/* portal-deny-all-v1 */'

def transform(source):
    if hashlib.sha256(source.encode()).hexdigest() != BASE_SHA256:
        raise ValueError('Unknown Kimi 2.4.1 bundle; patch requires review')
    replacements = [
        ('listToolsResponseSchema = object$1({ tools: array(toolDescriptorSchema) });',
         'listToolsResponseSchema = object$1({ tools: array(toolDescriptorSchema), portal_deny_all_version: number$1().optional() });'),
        ('okEnvelope({ tools }, req.id)', 'okEnvelope({ tools, portal_deny_all_version: 1 }, req.id)'),
        ('function isToolActiveComposed(layers, name, source = "builtin") {',
         'function isToolActiveComposed(layers, name, source = "builtin") {\n\tif (layers.sessionDisabledTools?.includes("*")) return false;'),
        ('\t\tasync replace(names) {\n\t\t\tawait this.ready;',
         '\t\tasync replace(names) {\n\t\t\tawait this.ready;\n\t\t\tif (this.state.disabledTools.includes("*") && !names.includes("*")) throw new Error("Restricted session policy cannot be relaxed");'),
        ('\t\tdisabled_tools: array(string()).optional(),',
         '\t\tdeny_all_tools: boolean$1().optional(),\n\t\tdisabled_tools: array(string()).optional(),'),
        ('if (req.body.disabled_tools !== void 0) try {\n\t\t\t\t\tawait resolved.toolPolicy.setSessionDisabledTools(req.body.disabled_tools);',
         'if (req.body.deny_all_tools === true || req.body.disabled_tools !== void 0) try {\n\t\t\t\t\tawait resolved.toolPolicy.setSessionDisabledTools(req.body.deny_all_tools === true ? ["*"] : req.body.disabled_tools);'),
        ('const clientMetadata = req.body.metadata === void 0 ? void 0 : [structuredClone(req.body.metadata)];',
         '''const portalMetadata = structuredClone(req.body.metadata ?? {});
                delete portalMetadata.portal_tool_policy;
                if (req.body.deny_all_tools === true) {
                    if (!JSON.parse(resolved.toolPolicy.getPolicyFingerprint()).at(-1)?.includes("*")) throw new Error("Restricted policy was not applied");
                    portalMetadata.portal_tool_policy = {version:1,policy:"deny_all",session_id,prompt_id:reservation.id,effective_tools:[],source:"kimi.native.prompt"};
                }
                const clientMetadata = Object.keys(portalMetadata).length ? [portalMetadata] : void 0;'''),
    ]
    for old, new in replacements:
        if source.count(old) != 1: raise ValueError('Native patch anchor is not unique')
        source = source.replace(old, new)
    return source.replace('\n', '\n' + MARKER + '\n', 1)

def apply(root):
    bundle = Path(root)/'lib/node_modules/@moonshot-ai/kimi-code/dist/main.mjs'
    original = bundle.read_text()
    backup = bundle.with_suffix('.portal-upstream')
    if MARKER in original[:100]:
        if not backup.is_file() or original != transform(backup.read_text()):
            raise ValueError('Patched Kimi bundle integrity mismatch')
        return
    patched = transform(original)
    backup.write_text(original)
    temp = bundle.with_suffix('.portal.tmp')
    temp.write_text(patched)
    shutil.copymode(bundle, temp)
    temp.replace(bundle)
