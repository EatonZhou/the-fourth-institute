"""Immutable, explicitly selected bytes exchanged within one quality review.

This is not a resource credential or a proxy for the source Workspace. Source
reads occur only during the authenticated submit operation; readers subsequently
receive independent copies, guarded by the review owner's live authorization.
"""
import hashlib
import json
import os
import re
import shutil
import uuid
from pathlib import Path

from management_common import secure_paths

MAX_FILE = 128 * 1024 * 1024
MAX_PACKAGE = 256 * 1024 * 1024


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def selection(assets):
    result, addresses, names = [], set(), set()
    if not isinstance(assets, list) or len(assets) > 40:
        raise ValueError('固定交接清单最多40个文件')
    for asset in assets:
        address, checksum = asset.get('address'), asset.get('sha256')
        if not isinstance(address, str) or not re.fullmatch('[0-9a-f]{64}', str(checksum)):
            raise ValueError('交接文件须有正式地址和SHA256')
        # Names are metadata, never paths in the server's blob store.
        name = address.split('/file/', 1)[-1]
        secure_paths.relative_parts(name)
        if '\\' in name or ':' in name or address in addresses or name in names:
            raise ValueError('交接清单包含重复或不安全成员名')
        addresses.add(address); names.add(name)
        result.append({'address': address, 'sha256': checksum, 'name': name})
    return sorted(result, key=lambda item: item['address'])


class TransferStore:
    def __init__(self, root, resources):
        self.root, self.resources = Path(root), resources

    def freeze(self, review_id, assets, actor, *, group='subject'):
        selected = selection(assets)
        folder = self.root / (review_id + '-' + group)
        stage = self.root / ('.transfer-' + uuid.uuid4().hex)
        stage.mkdir(mode=0o700)
        entries, total = [], 0
        try:
            for asset in selected:
                resolved = self.resources.resolve(asset['address'], actor=actor)
                size = resolved.get('size')
                hasher, count, mime = hashlib.sha256(), 0, 'application/octet-stream'
                with (stage / asset['sha256']).open('wb') as target:
                    if type(size) is int and size > 4 * 1024 * 1024:
                        if size > MAX_FILE:
                            raise ValueError('交接单文件超过128MiB')
                        for start in range(0, size, 256 * 1024):
                            end = min(start + 256 * 1024, size) - 1
                            data, mime, _ = self.resources.read_content_range(
                                asset['address'], actor=actor, start=start, end=end)
                            if len(data) != end - start + 1:
                                raise ValueError('交接文件读取期间发生变化')
                            target.write(data); hasher.update(data); count += len(data)
                    else:
                        data, mime, _ = self.resources.read_content(asset['address'], actor=actor, max_bytes=MAX_FILE + 1)
                        data = data.encode() if isinstance(data, str) else data
                        count = len(data)
                        if count > MAX_FILE:
                            raise ValueError('交接单文件超过128MiB')
                        target.write(data); hasher.update(data)
                    target.flush(); os.fsync(target.fileno())
                if hasher.hexdigest() != asset['sha256']:
                    raise ValueError('审核引用内容已变化')
                total += count
                if total > MAX_PACKAGE:
                    raise ValueError('交接包超过256MiB')
                entries.append({**asset, 'size': count, 'content_type': mime})
            manifest = {'entries': entries, 'digest': digest(entries)}
            (stage / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
            if folder.exists():
                with secure_paths.open_target(folder, ('manifest.json',)) as (fd, _):
                    existing = json.loads(os.read(fd, 131073))
                if existing != manifest:
                    raise ValueError('固定交接不可扩展或替换；请提交新修订')
                self.verify(review_id, existing, group=group)
            else:
                os.replace(stage, folder)
            return manifest
        finally:
            if stage.exists():
                shutil.rmtree(stage)

    def read(self, review_id, manifest, checksum, *, group='subject', start=0, end=None):
        entry = next((e for e in manifest['entries'] if e['sha256'] == checksum), None)
        if entry is None:
            raise FileNotFoundError('该文件不在本次固定交接清单')
        if digest(manifest['entries']) != manifest['digest']:
            raise ValueError('固定交接清单摘要不符')
        with secure_paths.open_target(self.root / (review_id + '-' + group), (checksum,)) as (fd, info):
            if info.st_size != entry['size']:
                raise ValueError('固定交接文件大小不符')
            # Verify the same pinned handle used for delivery, never a source path.
            hasher = hashlib.sha256()
            while chunk := os.read(fd, 256 * 1024):
                hasher.update(chunk)
            if hasher.hexdigest() != checksum:
                raise ValueError('固定交接文件摘要不符')
            os.lseek(fd, start, os.SEEK_SET)
            length = max(0, min(entry['size'] - start, (end - start + 1) if end is not None else entry['size']))
            chunks = []
            while length:
                data = os.read(fd, min(length, 256 * 1024))
                if not data:
                    raise ValueError('固定交接文件被截断')
                chunks.append(data); length -= len(data)
            return b''.join(chunks), entry

    def verify(self, review_id, manifest, *, group='subject'):
        for entry in manifest['entries']:
            self.read(review_id, manifest, entry['sha256'], group=group, end=-1)
