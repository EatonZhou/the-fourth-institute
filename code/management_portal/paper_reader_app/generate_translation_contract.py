"""Build a static client enum from the service's request admission declaration.

No runtime import, credentials or service restart. Fail the build if that declaration
changes shape, rather than silently shipping a separately maintained allowlist.
"""
import ast
import json
from pathlib import Path


def generate():
    here = Path(__file__).resolve().parent
    tree = ast.parse((here.parent / 'paper_translation_service.py').read_text())
    matches = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1 or not isinstance(node.ops[0], ast.NotIn):
            continue
        if ast.unparse(node.left) == "request['mode']" and isinstance(node.comparators[0], ast.Tuple):
            matches.append(ast.literal_eval(node.comparators[0]))
    if len(matches) != 1 or 'auto' not in matches[0] or not all(isinstance(v, str) for v in matches[0]):
        raise ValueError('Translation service mode declaration changed; update contract generator')
    providers = [v for v in matches[0] if v != 'auto']
    (here / 'translation_contract.js').write_text(
        '// Generated from paper_translation_service.py admission; do not edit.\n'
        + 'export const TRANSLATION_PROVIDERS = Object.freeze(' + json.dumps(providers) + ');\n')


if __name__ == '__main__':
    generate()
