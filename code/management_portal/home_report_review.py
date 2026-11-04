"""Report adapters for the shared, authenticated independent-review authority."""
from management_portal.home_explanation_review import ReviewService
from management_portal.home_report import digest


def subject(store, identifier, number, actor, *, media=None):
    record, number, snapshot = store.get(identifier, actor, number)
    base = f'{identifier}/revision/{number}/file/'
    assets = record['revisions'][str(number)]['assets']
    if media is None:
        if snapshot.get('quality') != 'high':
            return None
        hashes = {name: sha for name, sha in assets.items() if name in {'report.json', 'report.html', 'report.md'}}
        ref = base + 'report.json'
        version = number
        checksum = snapshot['version']
    else:
        if media.get('quality') != 'high':
            return None
        hashes = {'high-' + name: sha for name, sha in media.get('assets', {}).items()}
        ref = base + 'report.json'
        version = f'{number}/video/high'
        checksum = digest([snapshot['version'], 'high', hashes])
    return {'kind': 'report_video' if media else 'report', 'ref': ref, 'version': version,
            'digest': checksum, 'quality': 'high', 'author': snapshot.get('author') or record['created_by'],
            'assets': [{'address': base + name, 'sha256': sha} for name, sha in hashes.items()],
            'task': ('实际打开/home-report?kind=report&id='+identifier+'&revision='+str(number)+'操作固定版交互并核对来源。'
                     '修改通过UpdateResource changes={expected_revision, content:{title?,goal?,markdown?,presentation?}}追加新修订。'
                     'presentation支持question/assumption/rule/limit，nodes:[{id,title?,x?,y?}]改实际图形，'
                     'steps:[{title?,speech?,beats?:[{speech,edge?}]}]改实际分镜、音轨、字幕；保持原步骤数和固定来源边。'
                     '作者修改后POST /api/home-report/review {report_id,revision}送审；视频需要新revision重新生成。'
                     '视频需实际播放与听取音轨；本地机械配音的限制应如实评估，未达标必须否决。')}


def status(store, identifier, number, actor, *, media=None, service=None):
    value = subject(store, identifier, number, actor, media=media)
    if value is None:
        return {'status': 'not_required'}
    service = service or ReviewService()
    return service.read(service.identifier(value), actor) or {'status': 'not_requested'}


def submit(store, identifier, number, actor, *, media=None, service=None):
    store.authorize(actor, 'UpdateResource', identifier)
    value = subject(store, identifier, number, actor, media=media)
    if value is None:
        return {'status': 'not_required'}
    service = service or ReviewService()
    # Link a correction to the actual earlier rejection so the shared authority
    # can enforce its bounded review rounds. A GET never follows this write path.
    previous_number = int(number) - 1
    if previous_number > 0:
        previous_media = None
        if media is not None:
            from management_portal.home_report_persistent_media import PersistentMedia
            previous_media = PersistentMedia(store).status(identifier,previous_number,actor,quality='high',include_review=False)
        if media is None or previous_media and previous_media.get('status') == 'ready':
            previous = subject(store,identifier,previous_number,actor,media=previous_media)
            prior = service.read(service.identifier(previous),actor) if previous else None
            if prior and prior['status'] == 'changes_requested' and previous['author'] == value['author']:
                value['previous_review_id'] = prior['id']
    return service.submit(value, actor)
