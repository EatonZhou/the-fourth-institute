"""Version-owned report media. Successful bundles are write-once and persistent."""
import hashlib
import json
import shutil
import time
import uuid
import fcntl
from pathlib import Path
from management_common.portal_paths import path as portal_path

from management_portal import business_tasks
from management_portal.home_report import digest, reauthorize
from management_portal.home_report_media import FILES, MediaStore, render, _SLOTS, bundle_files
from management_portal.home_report_quality import quality as normalize_quality


def _atomic(path, value):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex)
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def import_candidate(job, current, actor, *, read=None):
    legacy = MediaStore()
    directory = legacy.directory(actor, job)
    if directory.is_symlink() or directory.parent.is_symlink():
        raise PermissionError('缓存路径无效')
    state = json.loads((directory / 'state.json').read_text(encoding='utf-8'))
    reauthorize(state['report'], actor=actor, read=read)
    if state.get('status') != 'ready':
        raise FileNotFoundError('旧缓存未完成或已被清理，不能恢复')
    def comparable(report):
        import copy
        value = copy.deepcopy(report)
        for key in ('version','checked_at','read_seconds','source_version','author','format','markdown','authoring','origin_key'):
            value.pop(key, None)
        value.setdefault('quality', 'medium')
        for source in value['sources']:
            source.pop('snapshot', None)
        return value
    if comparable(state['report']) != comparable(current):
        raise ValueError('旧缓存来源快照与当前报告不一致，不能混用媒体')
    bundle_files(directory)
    return directory, state


def discover_candidate(current, actor, *, read=None):
    """Inspect only this principal's extant cache; never recover deleted bytes."""
    import re
    root=MediaStore().root / digest(actor)
    if not root.is_dir() or root.is_symlink():
        return None
    candidates=sorted((path for path in root.iterdir() if re.fullmatch(r'[a-f0-9]{64}',path.name)
                       and path.is_dir() and not path.is_symlink()),key=lambda path:path.stat().st_mtime,reverse=True)
    for directory in candidates:
        try:
            return import_candidate(directory.name,current,actor,read=read)
        except Exception:
            # A retired/unreadable cache is not a failure of the newly checked
            # report. Explicit import still surfaces its exact failure.
            continue
    return None


def install_import(store, identifier, number, candidate, *, version=None):
    directory, state = candidate
    parent=store.directory(identifier) / str(number)
    stage=parent / ('.import-'+uuid.uuid4().hex)
    stage.mkdir(mode=0o700)
    try:
        hashes = {}
        for name in bundle_files(directory):
            source=directory/name
            if source.is_symlink(): raise PermissionError('旧缓存文件被替换')
            before=hashlib.sha256(source.read_bytes()).hexdigest()
            shutil.copyfile(source,stage/name)
            hashes[name] = hashlib.sha256((stage/name).read_bytes()).hexdigest()
            if hashes[name]!=before: raise ValueError('旧缓存复制期间内容变化')
        state = {k:v for k,v in state.items() if k != 'report'}
        state.update(status='ready', assets=hashes, imported_from={'job_id':state['job_id'], 'report_version':state['version']})
        state.update(version=version or state['version'], quality='medium')
        _atomic(stage / 'state.json', state)
        stage.rename(parent/'media')
    finally:
        if stage.exists(): shutil.rmtree(stage)


class PersistentMedia:
    def __init__(self, store, renderer=render):
        self.store = store
        self.renderer = renderer

    @staticmethod
    def task(identifier, revision, quality='medium'):
        return f'persistent-home-report:{identifier}:{revision}:{quality}'

    def names(self, quality):
        quality = normalize_quality(quality)
        return ('media', 'media-state.json') if quality == 'medium' else (f'media-{quality}', f'media-{quality}-state.json')

    def lease_path(self,identifier,number,quality='medium'):
        suffix = '' if quality == 'medium' else '-' + normalize_quality(quality)
        return self.store.root/'.locks'/f'media-{identifier}-{number}{suffix}'

    def active(self,identifier,number,quality='medium'):
        path=self.lease_path(identifier,number,quality)
        if not path.exists(): return False
        with path.open('r') as stream:
            try:
                fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                return True
        return False

    def status(self, identifier, number, actor, *, quality='medium', include_review=True):
        quality = normalize_quality(quality)
        _, number, snapshot = self.store.get(identifier, actor, number)
        directory = self.store.directory(identifier) / str(number)
        folder, pending_name = self.names(quality)
        complete = directory / folder / 'state.json'
        pending = directory / pending_name
        state = json.loads(complete.read_text()) if complete.exists() else json.loads(pending.read_text()) if pending.exists() else None
        if state is None:
            if quality != 'low':
                from management_portal.home_report_review import authoring_status
                return authoring_status(self.store,identifier,number,actor,quality)
            return None
        if state.get('quality', 'medium') != quality:
            raise ValueError('媒体质量与固定产物不一致')
        if state['status'] == 'pending' and not self.active(identifier,number,quality):
            # A worker can finish between the first read and the lease check.
            state = json.loads(complete.read_text()) if complete.exists() else json.loads(pending.read_text())
            if state['status'] == 'pending':
                state = {**state,'status':'failed','error':'上次生成已中断，可以重试。'}
        result = {**state, 'report_id':identifier, 'revision':number, 'quality':quality,
                  'base_url':f'/api/home-report/reports/{identifier}/{number}/media/{quality}/file'}
        if quality == 'high' and result['status'] == 'ready' and include_review:
            from management_portal.home_report_review import status
            result['review'] = status(self.store, identifier, number, actor, media=result)
        return result

    def start(self, identifier, number, actor, *, retry=False, quality='medium'):
        quality = normalize_quality(quality)
        _, number, snapshot = self.store.get(identifier, actor, number)
        self.store.require_template(snapshot)
        self.store.authorize(actor, 'UpdateResource', identifier)
        if quality != 'low':
            from management_portal.home_report_review import require_authored, submit_authoring
            existing=self.status(identifier,number,actor,quality=quality)
            if existing and existing['status'] in {'ready','cancelled','pending'}:
                return existing
            if not require_authored(self.store,snapshot,actor,quality):
                return submit_authoring(self.store,identifier,number,actor,quality)
            if quality == 'high':
                from management_portal.home_report_review import ReviewService, responsible
                ReviewService().ensure_reviewers({'kind': 'report_video',
                    'author': snapshot.get('author'),
                    'responsible_resident_id': responsible(self.store, snapshot, actor)}, actor)
        task = self.task(identifier, number, quality)
        with self.store.lock(identifier):
            previous = self.status(identifier, number, actor, quality=quality)
            if previous and (previous['status'] in {'ready','cancelled'} or self.active(identifier,number,quality) or not retry):
                return previous
            if not _SLOTS.acquire(blocking=False):
                raise RuntimeError('视频生成繁忙，请稍后重试')
            directory = self.store.directory(identifier) / str(number)
            folder, pending_name = self.names(quality)
            lease=None
            state = {'status':'pending','version':snapshot['version'], 'quality':quality}
            try:
                lease=self.lease_path(identifier,number,quality).open('a')
                fcntl.flock(lease,fcntl.LOCK_EX)
                with self.store.writer(identifier):
                    _atomic(directory / pending_name, state)
            except BaseException:
                if lease is not None: lease.close()
                _SLOTS.release()
                raise
            def generate():
                started = time.monotonic()
                stage = None
                retention = None
                temporary_owner=f'home-report-render:{identifier}:{number}:{quality}'
                try:
                    self.store.get(identifier, actor, number)
                    self.store.authorize(actor, 'UpdateResource', identifier)
                    # Render outside the registered object tree. A deletion can
                    # commit immediately; only the short final commit writes it.
                    temporary_root=portal_path('runtime')/'tmp'/'home-report'
                    temporary_root.mkdir(parents=True,exist_ok=True,mode=0o700)
                    stage = temporary_root / ('render-'+uuid.uuid4().hex)
                    from management_common.directory_retention import RetentionStore
                    retention=RetentionStore()
                    # The OS lease proves the prior process has stopped. Release
                    # its registered temporary trees through the shared queue.
                    retention.release_owner(temporary_owner)
                    retention.declare(stage,category='runtime_tmp',owner_id=temporary_owner)
                    stage.mkdir(mode=0o700)
                    result = self.renderer({**snapshot, 'media_quality':quality}, stage)
                    with self.store.lock(identifier), self.store.writer(identifier):
                        # Access may change during the long render. Do not commit revoked work.
                        self.store.get(identifier, actor, number)
                        self.store.authorize(actor, 'UpdateResource', identifier)
                        if quality != 'low':
                            require_authored(self.store,snapshot,actor,quality)
                        if json.loads((directory / pending_name).read_text()).get('status') == 'cancelled':
                            return
                        hashes = {name: hashlib.sha256((stage / name).read_bytes()).hexdigest() for name in bundle_files(stage)}
                        _atomic(stage / 'state.json', {**state, **result, 'status':'ready', 'assets':hashes})
                        commit = directory / ('.media-' + uuid.uuid4().hex)
                        shutil.copytree(stage,commit)
                        commit.rename(directory / folder)
                    if quality == 'high':
                        from management_portal.home_report_review import submit
                        submit(self.store, identifier, number, actor, media={**state, 'assets':hashes})
                except Exception as exc:
                    try:
                        with self.store.writer(identifier):
                            if (directory / pending_name).exists() and json.loads((directory / pending_name).read_text()).get('status') == 'cancelled':
                                return
                            _atomic(directory / pending_name, {**state, 'status':'failed','error':str(exc)[:800],
                                    'generation_seconds':round(time.monotonic()-started,3)})
                    except FileNotFoundError:
                        pass  # A deleted report is never recreated by a late worker.
                finally:
                    try:
                        if stage and stage.exists(): shutil.rmtree(stage)
                    finally:
                        try:
                            if retention is not None:
                                retention.finish(temporary_owner)
                                retention.release_owner(temporary_owner)
                        finally:
                            lease.close()
                            _SLOTS.release()
            try:
                if not business_tasks.start(task, generate):
                    lease.close()
                    _SLOTS.release()
            except BaseException:
                lease.close()
                _SLOTS.release()
                raise
        return {**state,'report_id':identifier,'revision':number}

    def cancel(self, identifier, number, actor, *, quality='medium'):
        quality = normalize_quality(quality)
        _, number, _ = self.store.get(identifier, actor, number)
        self.store.authorize(actor, 'UpdateResource', identifier)
        state=self.status(identifier,number,actor,quality=quality)
        if state and state.get('authoring') and state['status']=='authoring':
            from management_portal.home_report_review import AuthoringService
            AuthoringService().cancel(state['authoring']['id'],actor)
            return self.status(identifier,number,actor,quality=quality)
        with self.store.lock(identifier):
            state = self.status(identifier, number, actor, quality=quality)
            if state is None:
                raise FileNotFoundError('尚未生成视频')
            if state['status'] != 'pending':
                return state
            state = {**state, 'status':'cancelled','cancelled_by':actor}
            with self.store.writer(identifier):
                _atomic(self.store.directory(identifier) / str(number) / self.names(quality)[1], state)
            return state

    def file(self, identifier, number, name, actor, *, quality='medium'):
        quality = normalize_quality(quality)
        state = self.status(identifier, number, actor, quality=quality, include_review=False)
        if name not in FILES or not state or state['status'] != 'ready':
            raise FileNotFoundError('媒体尚未生成')
        path = self.store.directory(identifier) / str(number) / self.names(quality)[0] / name
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError('媒体资产不存在')
        # The saved digest binds this file to the immutable report revision.
        # Check in bounded chunks: a damaged video is an explicit failure,
        # never an invitation to replace the published bundle by rendering.
        expected = (state.get('assets') or {}).get(name)
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise FileNotFoundError('媒体资产摘要缺失或不符')
        return path, FILES[name]
