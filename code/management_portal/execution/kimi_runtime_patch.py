"""Reproducible, hash-pinned patch for the managed native prompt contract."""
import hashlib
import shutil
import sys
from functools import lru_cache
from pathlib import Path

PATCH_ID = 'portal-deny-all-v1'
V253_SHA256 = '07019fce6f568b642d4422e1daa81f975de6a21c578c82a924925c2e667aa11a'
BASE_SHA256 = 'fd15d5c9b48892bb4f1d5fe82df9f790c8c19a44ca9a81054cc592b6fb9e2c90'
MARKER = '/* portal-deny-all-v1 */'

def package_directory(root):
    prefix = Path(root) if sys.platform == "win32" else Path(root) / "lib"
    return prefix / "node_modules/@moonshot-ai/kimi-code"


def verified(root):
    """Verify the managed revision without mutating a running installation."""
    bundle = package_directory(root) / "dist/main.mjs"
    backup = bundle.with_suffix('.portal-upstream')
    try:
        stamps = tuple((p.stat().st_mtime_ns, p.stat().st_size) for p in (bundle, backup))
        return _verified(str(bundle), stamps)
    except (OSError, ValueError):
        return False

@lru_cache(maxsize=8)
def _verified(bundle, stamps):
    path = Path(bundle)
    return path.read_text() == transform(path.with_suffix('.portal-upstream').read_text())

def transform(source):
    digest = hashlib.sha256(source.encode()).hexdigest()
    if digest not in {BASE_SHA256, V253_SHA256}:
        raise ValueError('Unknown Kimi bundle; patch requires review')
    boolean_schema = "boolean" if digest == V253_SHA256 else "boolean$1"
    replacements = [
        ('listToolsResponseSchema = object$1({ tools: array(toolDescriptorSchema) });',
         'listToolsResponseSchema = object$1({ tools: array(toolDescriptorSchema), portal_deny_all_version: number$1().optional() });'),
        ('okEnvelope({ tools }, req.id)', 'okEnvelope({ tools, portal_deny_all_version: 1 }, req.id)'),
        ('function isToolActiveComposed(layers, name, source = "builtin") {',
         'function isToolActiveComposed(layers, name, source = "builtin") {\n\tif (layers.sessionDisabledTools?.includes("*")) return false;'),
        ('\t\tasync replace(names) {\n\t\t\tawait this.ready;',
         '\t\tasync replace(names) {\n\t\t\tawait this.ready;\n\t\t\tif (this.state.disabledTools.includes("*") && !names.includes("*")) throw new Error("Restricted session policy cannot be relaxed");'),
        ('\t\tdisabled_tools: array(string()).optional(),',
         f'\t\tdeny_all_tools: {boolean_schema}().optional(),\n\t\tdisabled_tools: array(string()).optional(),'),
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
    bundle = package_directory(root) / "dist/main.mjs"
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
