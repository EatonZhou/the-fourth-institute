"""Formal resource identity, revisions and assets for presentation reports."""
import json
import re

from management_portal.home_report_store import ReportStore


def install_resolver(service):
    def resolve(address, *, actor='', **context):
        match = re.fullmatch(r'(report_[0-9a-f]{12})(?:/revision/([1-9][0-9]*)(?:/file/([a-z0-9.-]+))?)?', address)
        if not match:
            raise ValueError('无效汇报地址')
        identifier, revision, filename = match.groups()
        store = ReportStore()
        record, number, snapshot = store.get(identifier, actor, revision)
        result = {'address':address,'root_id':identifier,'resource_type':'report','target_kind':'report',
                  'record':store.metadata(record),'exists':True,'content_kind':'json',
                  'detail':store.response(identifier,actor,number),'version':snapshot['version'],
                  'filename':filename or 'report.json','portal_url':f'/home-report?kind=report&id={identifier}&revision={number}'}
        if filename:
            path, mime = store.file(identifier, number, filename, actor)
            result.update(path=str(path),content_type=mime,content_kind='file',size=path.stat().st_size)
        return result

    def read(resolved, *, actor='', max_bytes=4*1024*1024, **context):
        # Re-resolve for every read rather than trusting a cached permission grant.
        current = resolve(resolved['address'], actor=actor)
        if current.get('path'):
            from pathlib import Path
            path = Path(current['path'])
            if path.stat().st_size > max_bytes:
                raise ValueError('资产超过本次读取预算，请使用流式下载入口')
            return path.read_bytes(), current['content_type'], {'filename':current['filename']}
        return json.dumps(current['detail'],ensure_ascii=False).encode(), 'application/json', {'filename':'report.json'}
    service.register_resolver('report', resolve, read_content=read)


def install_mutation_provider(service):
    def register(payload, *, actor='', resource_id='', **context):
        return ReportStore().create(resource_id, payload, actor)
    def update(resource_id, payload, *, actor='', **context):
        if set(payload) != {'expected_revision'}:
            raise ValueError('汇报只支持显式刷新expected_revision；历史正文不可修改')
        return ReportStore().refresh(resource_id,actor,payload['expected_revision'])
    def unregister(resource_id, *, actor='', **context):
        return ReportStore().delete(resource_id,actor)
    service.register_mutation_provider('report',register=register,update=update,unregister=unregister,service_managed_registration=True)
