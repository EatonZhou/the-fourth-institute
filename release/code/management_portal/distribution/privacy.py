"""Portable release text and source identities, separate from credential filtering.

The callers provide identities observed in selected domain rows. No account
store is read, and this transformation never provisions or grants an identity.
"""
from __future__ import annotations

import json
import re
import ast
import io
import tokenize
from pathlib import Path
from functools import lru_cache

ANONYMOUS_ID = 'tfi-exported-user'
LABEL = 'TFI portal'
IDENTITY_COLLECTIONS = {'scope_ids', 'failure_recipients', 'recipient_users', 'recipient_user_ids', 'owner_ids'}
_USER_FIELDS = {'actor', 'created_by', 'updated_by', 'deleted_by', 'username', 'user_id', 'author_id', 'owner_id', 'human_familizer_id'}
_ID = re.compile(r'^[a-z][a-z0-9_]*_[0-9a-f]{12}$')
_LAN = re.compile(r'(?<![\d.])(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})(?![\d.])')


def source_users(value):
    """Collect declared human actors/notification targets, never infer from prose."""
    from management_common.principal import SERVICE_IDS, NON_PRINCIPAL_SENDER_IDS
    result = set()
    def visit(item, field=''):
        if isinstance(item, dict):
            if item.get('kind', item.get('type')) == 'user':
                visit(item.get('id'), 'user_id')
            for key, child in item.items():
                visit(child, key)
        elif isinstance(item, (list, tuple)):
            for child in item: visit(child, field)
        elif isinstance(item, str):
            if field.endswith('_json'):
                try: visit(json.loads(item))
                except (ValueError, TypeError): pass
            elif field in _USER_FIELDS | {'failure_recipients', 'recipient_users', 'recipient_user_ids'}:
                if (re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{1,63}', item) and not _ID.fullmatch(item)
                    and item not in set(SERVICE_IDS) | set(NON_PRINCIPAL_SENDER_IDS) | {ANONYMOUS_ID, LABEL}):
                    result.add(item)
    visit(value)
    return sorted(result)


# These observed names also have ordinary language/code meanings. Their identity
# fields are always anonymized; prose requires a human-context rule.
def _short_user(user):
    return len(user) <= 2

def _human_text(text, users):
    if users:
        # Historical policy examples must describe roles, not reproduce a site's
        # real user/category assignments. Applies equally to escaped fixtures.
        text = re.sub(r'Portal 管理员分类由本站明确配置的管理员用户决定]*）',
                      'Portal 管理员分类由本站明确配置的管理员用户决定', text)
        text = re.sub(r'\d{4}-\d{2}-\d{2} (?:已按分类整理[。：:]?|起 )[^\n]*(?=\n|$)',
                      '用户分类示例：一般用户依默认规则访问；实习用户仅获得显式项目表项；管理员由本站明确配置。', text)
        text = re.sub(r'开发者名单默认是 `[^`]+`、`[^`]+`、`[^`]+`（环境变量 `PORTAL_ROUTER_DEV_USERS` 可修改）',
                      '开发者名单由环境变量 `PORTAL_ROUTER_DEV_USERS` 显式配置，发行副本默认不指定开发者', text)
        text = re.sub(r'名单 `PORTAL_ROUTER_DEV_USERS`（默认 `[^`]+`、`[^`]+`、`[^`]+`）',
                      '显式配置的名单 `PORTAL_ROUTER_DEV_USERS`（发行副本默认为空）', text)
        text = re.sub(r'A locally registered username is the canonical User ID',
                      'A locally registered username is the canonical User ID', text)
    for user in sorted(set(users), key=len, reverse=True):
        if not user: continue
        escaped = re.escape(user)
        if _short_user(user):
            # Short words can be code symbols: only human context is editable.
            text = re.sub(r'(?<![\w])'+escaped+r'(?=\s+(?:20\d{2}-)?\d{1,2}-\d{1,2})', LABEL, text)
            text = re.sub(r'((?:例如|e\.g\.|向|用户|账号|User|username|Ask|owner)\s*[`:]?\s*)'+escaped+r'(?![\w])',
                          lambda m:m[1]+LABEL, text, flags=re.I)
            continue
        pattern = re.compile(r'(?<![\w./-])'+escaped+r'(?![\w/-]|\.[\w])')
        text = '\n'.join(line if re.search(r'copyright|©',line,re.I) else pattern.sub(LABEL,line)
                         for line in text.split('\n'))
    return text


@lru_cache(maxsize=1)
def source_environment():
    """Observed source directories, not one developer machine's path literals."""
    from management_common.portal_paths import paths
    configured = paths()
    roots = [(str(Path(__file__).resolve().parents[2]), '${code_root}'),
             (str(Path.home()), '${user_home}')]
    roots.extend((str(configured[key]), '${'+key+'}') for key in ('share_root','staging_root','instances_root'))
    return tuple(sorted(roots, key=lambda pair:len(pair[0]), reverse=True))


def portable_text(text, users=(), *, environment=True):
    """Replace source examples; callers keep runtime target configuration out."""
    if environment:
        for original, replacement in source_environment():
            text = re.sub(r'(?<![A-Za-z0-9_./-])' + re.escape(original) + r'(?=/|\b)', lambda _m: replacement, text)
        text = _LAN.sub('${lan_host}', text)
    return _human_text(text, users)


def redact(value, users=(), *, field='', environment=True):
    """Identity collections retain machine IDs; prose uses the public label."""
    if isinstance(value, dict):
        result = {_human_text(key, users): redact(item, users, field=key, environment=environment) for key,item in value.items()}
        if value.get('kind', value.get('type')) == 'user' and value.get('id') in users:
            result['id'] = ANONYMOUS_ID
        return result
    if isinstance(value, (list, tuple)):
        result = [redact(x, users, field=field, environment=environment) for x in value]
        if field in IDENTITY_COLLECTIONS or field == 'recipients':
            unique = []
            for item in result:
                if item not in unique: unique.append(item)
            result = unique
        return result
    if not isinstance(value, str): return value
    if field.endswith('_json'):
        try: decoded = json.loads(value)
        except ValueError: pass
        else:
            if isinstance(decoded, (dict,list)):
                return json.dumps(redact(decoded, users, environment=environment), ensure_ascii=False, separators=(',', ':'))
    if field in IDENTITY_COLLECTIONS | _USER_FIELDS and value in users:
        return ANONYMOUS_ID
    return portable_text(value, users, environment=environment)


def _standalone_test_identity(text, path, users):
    """Remove source-only test account defaults, retaining the TAU profile IDs."""
    py_actor = '__import__("os").environ.get("PORTAL_TEST_ACTOR", "").strip()'
    if '/test_framework/' in '/'+path:
        for variable in ('EXPECTED_ACTOR', 'TAU_ACTOR', 'ACCOUNT'):
            for user in users:
                literal = re.escape(json.dumps(user))
                if path.endswith('.py'):
                    text = re.sub(r'(?m)^'+variable+r' = '+literal+r'$', variable+' = '+py_actor, text)
                if path.endswith('.js'):
                    text = re.sub(r'const '+variable+r' = '+literal+r';',
                                  'const '+variable+' = (process.env.PORTAL_TEST_ACTOR || "").trim();', text)
        if path.endswith('/dev_target.py'):
            text = text.replace('if actor != EXPECTED_ACTOR:', 'if not EXPECTED_ACTOR or actor != EXPECTED_ACTOR:')
        if path.endswith('/tau_browser.js'):
            text = text.replace('if (actor !== expectedActor)', 'if (!expectedActor || actor !== expectedActor)')
        if path.endswith('/tau_residents.py'):
            text = text.replace('        clean_module = str(module_id or "").strip()',
                '        if not TAU_ACTOR:\n            raise ValueError("Configure PORTAL_TEST_ACTOR before assigning test Residents")\n        clean_module = str(module_id or "").strip()')
        if path.endswith('/tau_campaign.py'):
            for user in users:
                text = text.replace('"account": '+json.dumps(user), '"account": '+py_actor)
    if path.endswith(('/harness_evolution_offline.py','/harness_evolution_evaluation.py')):
        for user in users:
            text = text.replace('TEST_ACTORS = frozenset({'+json.dumps(user)+'})',
                'TEST_ACTORS = frozenset(filter(None, __import__("os").environ.get("PORTAL_TEST_ACTOR", "").split(",")))')
    return text


def release_code_text(text, path, users=()):
    """Sanitize documentation without inventing a runtime identity fallback.

    Source-only convenience defaults become explicit caller configuration in a
    standalone package. Other executable identity literals require review.
    """
    path = str(path).replace('\\', '/')
    if path.endswith('/scripts/dev_resident_lifecycle_smoke.py') or path == 'scripts/dev_resident_lifecycle_smoke.py':
        for user in users:
            text = text.replace('parser.add_argument("--actor", default=' + json.dumps(user) + ')',
                                'parser.add_argument("--actor", required=True)')
    if path.endswith('management_portal/portal_deployment_router.py'):
        text = re.sub(r'os.getenv\("PORTAL_ROUTER_DEV_USERS", "[^"]*"\)',
                      'os.getenv("PORTAL_ROUTER_DEV_USERS", "")', text)
    text = _standalone_test_identity(text, path, users)
    fixture = '/tests/' in '/' + path or path.endswith('legacy-definitions.json')
    if fixture and path.endswith('.fixture.mjs'):
        # These capture readers require an explicitly supplied local fixture;
        # a source author's private capture directory is not a portable default.
        text = re.sub(r"process\.env\.([A-Z_]+)\s*\|\|\s*'/(?:share|home)/[^']+'",
                      lambda m: '(process.env.' + m[1] + ' || (() => { throw new Error("Set ' + m[1] + ' to a local fixture"); })())', text)
    if fixture and path.endswith('/test_build.py'):
        text = re.sub(r"(monkeypatch\.setenv\('PORTAL_PROJECTION_NPM_CACHE',)\s*'/home/[^']+'",
                      r"\1 str(tmp_path / 'npm-cache')", text)
    if path.endswith('.json'):
        return json.dumps(redact(json.loads(text), users, environment=fixture), ensure_ascii=False, indent=2) + '\n'
    if path.endswith('.py'):
        if not users:
            return portable_text(text, (), environment=fixture)
        tree = ast.parse(text)
        docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                      if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                      and node.body and isinstance(node.body[0], ast.Expr)
                      and isinstance(node.body[0].value, ast.Constant)
                      and isinstance(node.body[0].value.value, str)}
        identity_literals = set()
        def identity_name(name):
            return name.lower() in _USER_FIELDS or name.lower().endswith(('_actor','_user','_username','_owner'))
        for node in ast.walk(tree):
            values = []
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(isinstance(t, ast.Name) and identity_name(t.id) for t in targets): values.append(node.value)
            elif isinstance(node, ast.Dict):
                values += [v for k,v in zip(node.keys,node.values) if isinstance(k, ast.Constant)
                           and isinstance(k.value,str) and (identity_name(k.value) or k.value in IDENTITY_COLLECTIONS)]
            elif isinstance(node, ast.keyword) and node.arg and identity_name(node.arg):
                values.append(node.value)
            for value in values:
                if value is not None:
                    identity_literals.update(id(x) for x in ast.walk(value) if isinstance(x, ast.Constant))
        for node in ast.walk(tree):
            if (not fixture and isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) in identity_literals and node.value in users):
                raise ValueError('Unclassified executable source identity: '+path+':'+str(node.lineno))
        # Edit original spans rather than untokenize: Python 3.12 f-string
        # tokenization normalizes doubled braces, so round-tripping can corrupt it.
        lines=text.splitlines(keepends=True);offsets=[0]
        for line in lines: offsets.append(offsets[-1]+len(line))
        edits=[]
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type in {tokenize.COMMENT, tokenize.STRING, getattr(tokenize, 'FSTRING_MIDDLE', -1)}:
                replacement=portable_text(token.string, users, environment=fixture)
                if replacement!=token.string:
                    edits.append((offsets[token.start[0]-1]+token.start[1], offsets[token.end[0]-1]+token.end[1], replacement))
        output=text
        for start,end,replacement in reversed(edits): output=output[:start]+replacement+output[end:]
        ast.parse(output)
        return output
    if path.endswith(('.js', '.mjs', '.css', '.html')):
        # Transform only comments/string/template spans, never bare identifiers.
        spans = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`|/\*.*?\*/|//[^\n]*', re.S)
        return spans.sub(lambda m: portable_text(m[0], users, environment=fixture), text)
    return portable_text(text, users, environment=fixture)
