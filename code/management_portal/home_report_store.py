"""Persistent report presentations. Registry owns identity; assets are never cache.

All entry points reauthorize both the report and its original source reads.
No caller-supplied file path, HTML or source snapshot is accepted for writes.
"""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import re
import shutil
import uuid
from io import BytesIO
from zipfile import ZipFile, BadZipFile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from management_common.portal_paths import path as portal_path
from management_portal.home_report import build_report, reauthorize, normalize_target
from management_portal.resources.errors import ResourceVersionConflict
from management_portal.resources.registry_store import get_registry_store

COLLECTION = "report"
ORIGINS = "report_origin"
IDENTITY = re.compile(r"report_[0-9a-f]{12}")


def now():
    return datetime.now(timezone.utc).isoformat()


def require_permission(actor, instruction, resource_id):
    from management_portal import permission_system
    parameter = "address" if instruction == "ReadResource" else "resource_id"
    if not actor or resource_id not in permission_system.permitted_ids(actor, instruction, parameter, [resource_id]):
        raise PermissionError("无权访问此汇报")


def revision_number(value):
    if isinstance(value, bool) or not re.fullmatch(r"[1-9][0-9]{0,8}", str(value)):
        raise ValueError("汇报版本必须是正整数")
    return int(value)


class ReportStore:
    def __init__(self, *, registry=None, root=None, authorize=None, read=None):
        self.registry = registry or get_registry_store()
        self.root = Path(root) if root is not None else portal_path("features") / "home-reports"
        self.authorize = authorize or require_permission
        self.read = read

    @staticmethod
    def origin_id(actor, key):
        if not actor or not isinstance(key, str) or not re.fullmatch(r"home-message/v1:[a-f0-9]{64}|home-request/v1:[a-f0-9-]{32,36}", key):
            raise ValueError("需要稳定的origin_key")
        return hashlib.sha256(json.dumps([actor, key], ensure_ascii=False).encode()).hexdigest()

    def origin(self, actor, key):
        binding = self.registry.get(ORIGINS, self.origin_id(actor, key))
        if not binding:
            raise FileNotFoundError("此请求尚未生成持久汇报")
        if self.registry.get(COLLECTION,binding['report_id']) is None:
            from management_portal.resources.errors import ResourceOperationError
            raise ResourceOperationError('report.deleted','此请求的汇报已删除；新汇报需要新的请求',410)
        return self.response(binding["report_id"], actor, binding["revision"])

    def materialize(self, payload, actor, register):
        """The source is consulted only for an origin's very first materialization."""
        origin = self.origin_id(actor, payload.get("origin_key"))
        locks = self.root / ".locks"
        locks.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (locks / ("origin-" + origin)).open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            if self.registry.get(ORIGINS, origin):
                return self.origin(actor, payload["origin_key"])
            result = register(payload)
            identifier = result.get("resource_id") or result["resource"]["resource_id"]
            return self.response(identifier, actor, 1)

    @contextmanager
    def writer(self, identifier, *, creating=False):
        """Short directory/registry transaction; no long media work in this lock."""
        from management_portal.resources.directory_registrations import directory_writer, is_deleted
        from management_common.directory_cleanup_store import get_directory_cleanup_store
        with directory_writer(COLLECTION, ORIGINS) as writer:
            if is_deleted(identifier, connection=writer.connection):
                raise FileNotFoundError('汇报已删除')
            get_directory_cleanup_store().assert_available(self.directory(identifier), connection=writer.connection)
            if not creating and writer.get(COLLECTION,identifier) is None:
                raise FileNotFoundError('汇报已删除')
            yield writer

    def directory(self, identifier):
        if not IDENTITY.fullmatch(str(identifier)):
            raise ValueError("汇报ID无效")
        path = self.root / identifier
        if path.is_symlink() or self.root.is_symlink():
            raise PermissionError("汇报目录不能是符号链接")
        return path

    @contextmanager
    def lock(self, identifier):
        # Cross-process lock outside the asset subtree also serializes deletion.
        self.directory(identifier)
        locks = self.root / ".locks"
        locks.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (locks / identifier).open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield

    def record(self, identifier):
        self.directory(identifier)
        record = self.registry.get(COLLECTION, identifier)
        if not record or record.get("deleted_at"):
            raise FileNotFoundError("汇报不存在或已删除")
        return record

    def get(self, identifier, actor, revision=None):
        record = self.record(identifier)
        self.authorize(actor, "ReadResource", identifier)
        number = revision_number(record["revision"] if revision is None else revision)
        stored = record["revisions"].get(str(number))
        if stored is None:
            raise FileNotFoundError("汇报版本不存在")
        snapshot = copy.deepcopy(stored["report"])
        reauthorize(snapshot, actor=actor, read=self.read)
        return record, number, snapshot

    def reference(self, identifier, number, snapshot):
        return {"view": "report", "target": {"kind": "report", "id": identifier, "revision": number},
                "title": str(snapshot['title']),
                "address": f"{identifier}/revision/{number}", "version": snapshot["version"],
                "checked_at": snapshot["checked_at"]}

    def response(self, identifier, actor, revision=None):
        record, number, snapshot = self.get(identifier, actor, revision)
        from management_portal.home_report_persistent_media import PersistentMedia
        return {**snapshot, "report_id": identifier, "revision": number, "latest_revision": record["revision"],
                "reference": self.reference(identifier, number, snapshot),
                "media": PersistentMedia(self).status(identifier, number, actor),
                "exports": self.exports(identifier, number, snapshot)}

    @staticmethod
    def exports(identifier, number, snapshot):
        base = f"/api/home-report/reports/{identifier}/{number}/file/"
        names = ['report.html','report.json','slides.pptx'] + [a['filename'] for a in snapshot.get('attachments',[])]
        return [{"name": name, "url": base + name} for name in names]

    def _save_revision(self, identifier, number, snapshot, attachments=None):
        from management_portal.home_report_exports import write_exports
        parent = self.directory(identifier)
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination = parent / str(number)
        if destination.exists():
            previous = json.loads((destination / 'report.json').read_text(encoding='utf-8'))
            if previous.get('version') != snapshot.get('version'):
                raise FileExistsError('未完成版本与当前快照不符，需要恢复原创建请求')
            names = ['report.json','report.html','slides.pptx'] + [item['filename'] for item in previous.get('attachments',[])]
            return {'report':previous,'assets':{name:hashlib.sha256((destination/name).read_bytes()).hexdigest()
                                               for name in names}}
        stage = parent / (".revision-" + uuid.uuid4().hex)
        stage.mkdir(mode=0o700)
        try:
            write_exports(snapshot, stage)
            for name, content in (attachments or {}).items():
                if not re.fullmatch(r'attachment-[1-9][0-9]*\.pptx',name):
                    raise ValueError('无效PPT资产名')
                (stage/name).write_bytes(content)
            hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in stage.iterdir() if p.is_file()}
            if destination.exists():
                # A crashed uncommitted revision cannot be silently overwritten.
                raise FileExistsError("汇报版本资产已存在，需要核对未完成写入")
            stage.rename(destination)
            return {"report": snapshot, "assets": hashes}
        finally:
            if stage.exists():
                shutil.rmtree(stage)

    def _append_revision(self, identifier, actor, expected_revision, snapshot, attachments=None):
        """Publish a new bundle; only registered revisions are immutable.

        A process or registry transaction can fail after the atomic filesystem
        rename. Under the same writer lock, an unregistered next revision is a
        disposable prepared bundle, not a version readers have been promised.
        Creation uses its separate reserved-ID/first-snapshot recovery contract.
        """
        expected = revision_number(expected_revision)
        with self.lock(identifier), self.writer(identifier) as writer:
            record = self.record(identifier)
            self.authorize(actor, 'UpdateResource', identifier)
            if expected != record['revision']:
                raise ResourceVersionConflict('汇报已有新版本，请重新读取',
                                              expected_version=expected, current_version=record['revision'])
            reauthorize(snapshot, actor=actor, read=self.read)
            number = expected + 1
            if str(number) in record['revisions']:
                raise ValueError('汇报版本登记不一致，不能覆盖已有版本')
            destination = self.directory(identifier) / str(number)
            if destination.is_symlink():
                raise PermissionError('汇报版本目录不能是符号链接')
            if destination.exists():
                shutil.rmtree(destination)
            stored = self._save_revision(identifier, number, snapshot, attachments)
            record['revisions'][str(number)] = stored
            record.update(revision=number, title=snapshot['title'], updated_at=now())
            writer.put(COLLECTION, identifier, record)
        return record

    def create(self, identifier, payload, actor):
        allowed = {"target", "version", "import_job", "origin_key"}
        if set(payload) - allowed:
            raise ValueError("汇报创建仅接受target/version/import_job")
        # RegisterResource reserves this identity for its idempotent operation.
        # A transaction may roll back after exports have been atomically written;
        # retry the exact first snapshot even if the live source has since changed.
        with self.lock(identifier):
            existing = self.registry.get(COLLECTION, identifier)
            if existing:
                self.get(identifier, actor)
                return self.metadata(existing)
            frozen = self.directory(identifier) / '1/report.json'
            if frozen.exists():
                snapshot = json.loads(frozen.read_text(encoding='utf-8'))
                if snapshot['target'] != normalize_target(payload.get('target')):
                    raise ValueError('未完成创建的目标与重试不一致')
                reauthorize(snapshot, actor=actor, read=self.read)
            else:
                snapshot = build_report(payload.get("target"), actor=actor, read=self.read)
            return self._create_locked(identifier, payload, actor, snapshot)

    def _create_locked(self, identifier, payload, actor, snapshot):
        if payload.get("version") is not None and payload["version"] != snapshot["version"]:
            raise ResourceVersionConflict("来源已改变，请重新核对后保存")
        imported = None
        if payload.get("import_job"):
            from management_portal.home_report_persistent_media import import_candidate
            imported = import_candidate(payload["import_job"], snapshot, actor, read=self.read)
        else:
            from management_portal.home_report_persistent_media import discover_candidate
            imported = discover_candidate(snapshot,actor,read=self.read)
        with self.writer(identifier, creating=True) as writer:
            existing = self.registry.get(COLLECTION, identifier)
            if existing:
                if existing.get("deleted_at"):
                    raise FileNotFoundError("已删除身份不能复活")
                self.get(identifier, actor)
                return self.metadata(existing)
            stored = self._save_revision(identifier, 1, snapshot)
            snapshot = stored['report']
            from management_portal import permission_system
            record = {"resource_id": identifier, "resource_type": "report", "title": snapshot["title"],
                      **permission_system.creation_fields(actor), "created_at": now(), "updated_at": now(), "revision": 1,
                      "revisions": {"1": stored}}
            from management_common.instance_locations import stored_location
            record['directories'] = [{'path':stored_location(self.directory(identifier)), 'owner_id':identifier}]
            record['directory'] = {'owner_id':identifier}
            if imported:
                from management_portal.home_report_persistent_media import install_import
                try:
                    install_import(self, identifier, 1, imported)
                except (OSError,ValueError):
                    if payload.get('import_job'):
                        raise
                    # Cache eviction may win the copy race. Text still commits;
                    # no ready media is advertised unless the full import commits.
                    pass
            writer.put(COLLECTION, identifier, record)
            permission_system.sync_resource(self.metadata(record), resource_type='report', connection=writer.connection)
            if payload.get('origin_key'):
                origin = self.origin_id(actor,payload['origin_key'])
                if writer.get(ORIGINS,origin):
                    raise FileExistsError('此来源已绑定报告')
                writer.put(ORIGINS,origin,{'report_id':identifier,'revision':1,'created_by':actor})
        return self.metadata(record)

    def attach(self, identifier, actor, expected_revision, address):
        """Explicitly import a readable PPT resource into a new report revision."""
        if not isinstance(address,str) or not address or address.startswith('/') or len(address)>2048:
            raise ValueError('请提供正式PPT资源地址')
        record, old_number, snapshot = self.get(identifier,actor)
        self.authorize(actor,'UpdateResource',identifier)
        from management_portal.resources import get_resource_service
        content, mime, metadata = get_resource_service().read_content(address,actor=actor,max_bytes=32*1024*1024)
        if len(content)>32*1024*1024:
            raise ValueError('PPT超过32MiB限制')
        try:
            with ZipFile(BytesIO(content)) as archive:
                if 'ppt/presentation.xml' not in archive.namelist() or '[Content_Types].xml' not in archive.namelist():
                    raise ValueError('资源不是有效的PPTX')
                if sum(item.file_size for item in archive.infolist())>128*1024*1024:
                    raise ValueError('PPT解压内容过大')
        except BadZipFile as exc:
            raise ValueError('资源不是有效的PPTX') from exc
        from management_portal.home_report import digest
        sha=hashlib.sha256(content).hexdigest()
        name=f'attachment-{len(snapshot.get("attachments",[]))+1}.pptx'
        assets={item['filename']:self.file(identifier,old_number,item['filename'],actor)[0].read_bytes()
                for item in snapshot.get('attachments',[])}
        assets[name]=content
        snapshot.setdefault('source_version',snapshot['version'])
        snapshot.setdefault('attachments',[]).append({'address':address,'filename':name,'sha256':sha,'imported_at':now()})
        snapshot['sources'].append({'id':digest(['ppt',address,sha])[:20],'label':'关联PPT','address':address,
            'read':{'instruction':'ResolveResource','arguments':{'address':address}},'version':sha,
            'version_kind':'content_sha256','snapshot':{'sha256':sha,'bytes':len(content)}})
        snapshot.pop('version',None)
        snapshot['checked_at']=now()
        snapshot['version']=digest(snapshot)
        record = self._append_revision(identifier, actor, expected_revision, snapshot, assets)
        return self.response(identifier, actor, record['revision'])

    def refresh(self, identifier, actor, expected_revision):
        record, _, previous = self.get(identifier, actor)
        self.authorize(actor, "UpdateResource", identifier)
        snapshot = build_report(previous["target"], actor=actor, read=self.read)
        record = self._append_revision(identifier, actor, expected_revision, snapshot)
        return self.metadata(record)

    def delete(self, identifier, actor, expected_revision=None):
        # Deletion needs the report admin, not access to sources already revoked.
        with self.lock(identifier):
            record = self.record(identifier)
            self.authorize(actor, "UnregisterResource", identifier)
            if expected_revision is not None and revision_number(expected_revision) != record["revision"]:
                raise ResourceVersionConflict("汇报已有新版本")
            from management_portal.resources.directory_registrations import unregister_registry
            document, cleanup_ids = unregister_registry(COLLECTION,identifier,actor=actor)
        return {**self.metadata(document),'deleted':True,'registration_status':'unregistered',
                'directory_cleanup':{'status':'pending','cleanup_ids':cleanup_ids}}

    def file(self, identifier, number, name, actor):
        record, number, snapshot = self.get(identifier, actor, number)
        from management_portal.home_report_media import FILES
        types = {"report.json": "application/json", "report.html": "text/html; charset=utf-8",
                 "slides.pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}
        for item in snapshot.get('attachments',[]):
            types[item['filename']]=types['slides.pptx']
        if name in types:
            path = self.directory(identifier) / str(number) / name
            expected = record["revisions"][str(number)]["assets"][name]
            if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise FileNotFoundError("汇报资产不存在或摘要不符")
            return path, types[name]
        if name not in FILES:
            raise FileNotFoundError("汇报资产不存在")
        from management_portal.home_report_persistent_media import PersistentMedia
        return PersistentMedia(self).file(identifier, number, name, actor)

    @staticmethod
    def metadata(record):
        return {key: value for key, value in record.items() if key != "revisions"}


def permission_records():
    return [ReportStore.metadata(r) for r in get_registry_store().documents(COLLECTION).values() if not r.get("deleted_at")]


class RegistryProjection:
    resource_type = "report"
    collection = COLLECTION

    def validate(self, resource_id, raw):
        if raw.get("resource_id") != resource_id or not IDENTITY.fullmatch(resource_id):
            raise ValueError("Invalid report registry identity")
        return raw

    def project(self, item, *, adapter):
        from management_portal.resource_index_records import resource_record
        # Discovery exposes identity only. Source-derived titles/body are served
        # by the resolver after its source checks, including after revocation.
        return resource_record(resource_id=item["resource_id"], resource_type="report", title="汇报",
                           summary="持久汇报", adapter=adapter, modified_at=item["updated_at"],
                           attributes={key:item[key] for key in ('created_by','created_for','created_at','updated_at','revision') if key in item}, facets=["report"])


REGISTRY_PROJECTION = RegistryProjection()
