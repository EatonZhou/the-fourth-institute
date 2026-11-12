"""Temporary download records and their durable expiry, owned by ResourceService."""
import json
import re
from datetime import datetime

from management_common.configuration_files import atomic_json
from management_common.directory_links import is_directory_link
from management_common.directory_retention import RetentionStore
from management_portal.hatg.contracts import not_found

TOKEN = re.compile(r'[A-Za-z0-9_-]{32}')


def _path(root, token):
    if not TOKEN.fullmatch(token):
        raise not_found('下载地址不存在或已经失效')
    return root / 'downloads' / (token + '.json')


def _expiry(record):
    expiry = datetime.fromisoformat(record['expires_at'].replace('Z', '+00:00'))
    if expiry.tzinfo is None:
        raise ValueError('Download expiry must include a timezone')
    return expiry


def _register(path, token, record):
    # The grant's runtime lifetime ends at its expiry. The shared maintenance
    # queue owns physical removal, including Windows occupied-file retries.
    RetentionStore().declare(path, category='runtime_tmp',
                             owner_id='resource-download:' + token,
                             finished_at=_expiry(record))


def write(root, token, record):
    path = _path(root, token)
    path.parent.mkdir(parents=True, exist_ok=True)
    _register(path, token, record)
    atomic_json(path, record)


def read(root, token, caller, now):
    path = _path(root, token)
    from management_common.directory_cleanup_store import get_directory_cleanup_store
    if get_directory_cleanup_store().pending_at(path):
        raise not_found('下载地址不存在或已经失效')
    try:
        record = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise not_found('下载地址不存在或已经失效') from None
    if _expiry(record) <= now or record.get('caller') != caller:
        raise not_found('下载地址不存在或已经失效')
    return record


def reconcile(root):
    """Adopt pre-registration grant files into the same expiry owner.

    Existing records remain valid until their original deadline; retries never
    extend it. This is also safe after a writer stops before publishing its file.
    """
    folder = root / 'downloads'
    from management_common.directory_cleanup_store import get_directory_cleanup_store
    queue = get_directory_cleanup_store()
    report = {'registered': 0, 'errors': []}
    if is_directory_link(folder):
        raise ValueError('Download grant directory must not be a link')
    for path in folder.glob('*.json'):
        if not TOKEN.fullmatch(path.stem) or is_directory_link(path) or not path.is_file():
            continue
        try:
            if queue.pending_at(path):
                continue
            if path.stat().st_size > 65536:
                raise ValueError('Download grant is too large')
            record = json.loads(path.read_text(encoding='utf-8'))
            if not all(key in record for key in ('caller', 'address', 'expires_at')):
                raise ValueError('Invalid download grant record')
            _register(path, path.stem, record)
            report['registered'] += 1
        except (OSError, ValueError, KeyError) as error:
            report['errors'].append(f'{path.name}: {error}')
    return report
