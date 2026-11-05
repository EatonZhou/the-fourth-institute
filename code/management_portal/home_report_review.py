"""Report adapters for the shared, authenticated independent-review authority."""
from management_portal.home_explanation_review import ReviewService, AuthoringService
from management_portal.home_report import digest
from management_portal.home_report_origin import recheck


def responsible(store, snapshot, actor):
    binding=recheck(actor,snapshot.get('authoring'),snapshot.get('origin_key'),snapshot['target'],read=store.read)
    return binding.get('resident_id','') if binding and binding.get('status')=='verified' else ''


def subject(store, identifier, number, actor, *, media=None):
    record, number, snapshot = store.get(identifier, actor, number)
    store.require_template(snapshot)
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
            'responsible_resident_id': responsible(store,snapshot,actor),
            'assets': [{'address': base + name, 'sha256': sha} for name, sha in hashes.items()],
            'task': ('实际打开/home-report?kind=report&id='+identifier+'&revision='+str(number)+'操作固定版交互并核对来源。'
                     '修改通过UpdateResource changes={expected_revision, content:{title?,goal?,markdown?,presentation?}}追加新修订。'
                     'presentation支持question/assumption/rule/limit，nodes:[{id,title?,x?,y?}]改实际图形，'
                     'steps:[{title?,speech?,beats?:[{speech,edge?}]}]改实际分镜和字幕；保持原步骤数和固定来源边。'
                     '作者修改后POST /api/home-report/review {report_id,revision}送审；视频需要新revision重新生成。'
                     '视频需实际播放并核对画面、字幕与节奏；本地报告视频为无配音字幕视频，未达标必须否决。')}


def status(store, identifier, number, actor, *, media=None, service=None):
    value = subject(store, identifier, number, actor, media=media)
    if value is None:
        return {'status': 'not_required'}
    service = service or ReviewService()
    return service.current(service.identifier(value), actor) or {'status': 'not_requested'}


def submit(store, identifier, number, actor, *, media=None, service=None):
    store.authorize(actor, 'UpdateResource', identifier)
    value = subject(store, identifier, number, actor, media=media)
    if value is None:
        return {'status': 'not_required'}
    service = service or ReviewService()
    # Link a correction to the actual earlier rejection in the shared authority.
    # Review rounds do not impose a quality cutoff. GET never submits work.
    previous_number = int(number) - 1
    if previous_number > 0:
        previous_media = None
        if media is not None:
            from management_portal.home_report_persistent_media import PersistentMedia
            previous_media = PersistentMedia(store).status(identifier,previous_number,actor,quality='high',include_review=False)
        if media is None or previous_media and previous_media.get('status') == 'ready':
            previous = subject(store,identifier,previous_number,actor,media=previous_media)
            prior = service.current(service.identifier(previous),actor) if previous else None
            if prior and prior['status'] == 'changes_requested' and previous['author'] == value['author']:
                value['previous_review_id'] = prior['id']
    return service.submit(value, actor)


def authoring_subject(store, identifier, number, actor, quality):
    record,number,snapshot=store.get(identifier,actor,number)
    store.require_template(snapshot)
    ref=f'{identifier}/revision/{number}/file/report.json'
    return {'kind':'report_video','ref':ref,'version':number,'digest':snapshot['version'],
            'quality':quality,'author':snapshot.get('author') or record['created_by'],
            'responsible_resident_id':responsible(store,snapshot,actor),
            'assets':[{'address':ref,'sha256':record['revisions'][str(number)]['assets']['report.json']}],
            'task':('这是固定报告的实际分镜修改任务。读取report.json和原图后按policy组织问题、具体对象、递进变换与视觉焦点。'
                    '本域通过UpdateResource追加新版本：changes={expected_revision:'+str(number)+',content:{authoring_id:本任务authoring_id,'
                    'presentation:{question?,assumption?,rule?,limit?,nodes:[{id,title?,x?,y?}],steps:[{title?,speech?,beats?:[{speech,edge?}]}]}}}。'
                    '节点/边使用固定来源，steps保留原数量；至少实际修改图形或分镜，不能只更新quality/policy标签。'
                    'authoring_id由服务端核验本人的负责者身份、固定源摘要及取消状态。'
                    '保存后使用返回的新revision明确POST /api/home-report/media {report_id:"'+identifier+'",revision:新版本,quality:"'+quality+'"}。'
                    '媒体结果与新报告固定绑定，原引用不改；high成片由共享独立审核接收，不自审。')}


def authoring_status(store, identifier, number, actor, quality, *, service=None):
    service=service or AuthoringService()
    value=authoring_subject(store,identifier,number,actor,quality)
    record=service.read(service.identifier(value),actor)
    if not record:
        return None
    result={'status':'authoring','quality':quality,'report_id':identifier,'revision':int(number),'authoring':record,'error':record.get('error','')}
    if record['status']=='cancelled':
        result['status']='cancelled'
        return result
    for revision,entry in store.record(identifier)['revisions'].items():
        snapshot=entry['report']
        if snapshot.get('video_authoring',{}).get('id')==record['id']:
            result.update(status='authored',reference=store.reference(identifier,int(revision),snapshot))
            break
    return result


def submit_authoring(store, identifier, number, actor, quality, *, service=None):
    store.authorize(actor,'UpdateResource',identifier)
    service=service or AuthoringService()
    value=authoring_subject(store,identifier,number,actor,quality)
    service.submit(value,actor)
    return authoring_status(store,identifier,number,actor,quality,service=service)


def accept_authoring(store, identifier, number, actor, previous, snapshot, authoring_id, service):
    record=service.read(authoring_id,actor)
    if not record or record['status']!='submitted':
        raise ValueError('创作任务未投递或已取消，不能发布改稿')
    value=record['subject']
    if value['responsible_resident_id']!=actor or value['ref']!=f'{identifier}/revision/{number}/file/report.json' or value['digest']!=previous['version']:
        raise PermissionError('修改者或固定来源与正式创作任务不一致')
    if responsible(store,previous,actor)!=actor:
        raise PermissionError('报告负责者关联已失效')
    if digest(previous.get('reasoning'))==digest(snapshot.get('reasoning')):
        raise ValueError('必须实际修改分镜或图形，不能只提交质量标签')
    return {'id':record['id'],'quality':value['quality'],'resident_id':actor,
            'source_revision':int(number),'source_digest':previous['version'],'plan_digest':digest(snapshot['reasoning'])}


def require_authored(store, snapshot, actor, quality, *, service=None):
    proof=snapshot.get('video_authoring') or {}
    if proof.get('quality')!=quality:
        return False
    record=(service or AuthoringService()).read(proof.get('id'),actor)
    if not record or record['status']!='submitted':
        raise ValueError('分镜创作任务已取消或不可继续')
    if proof.get('resident_id')!=record['subject']['responsible_resident_id'] or proof.get('plan_digest')!=digest(snapshot.get('reasoning')):
        raise ValueError('分镜与已核验作者修改不一致')
    return True
