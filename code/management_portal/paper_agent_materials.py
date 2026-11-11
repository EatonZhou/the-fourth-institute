"""Paper-owned, job-retained immutable text snapshots, not an access grant."""
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import json
import os
import re

READ_FAILURE = 'PAPER_MATERIAL_READ_FAILED'

@dataclass(frozen=True)
class MaterialSnapshot:
    paper_id: str
    job_id: str
    text_path: str
    metadata_path: str
    sha256: str
    size_bytes: int

    def to_payload(self):
        from dataclasses import asdict
        return asdict(self)


def _identity(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}',value):
        raise ValueError('Invalid Paper material identity')
    return value


def _write_once(path: Path, data: bytes):
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o400)
    except FileExistsError:
        if path.is_symlink() or path.read_bytes()!=data:
            raise ValueError('Immutable Paper material conflict')
        return
    with os.fdopen(fd,'wb') as stream:
        stream.write(data);stream.flush();os.fsync(stream.fileno())


def _read(directory, paper_id, job_id):
    manifest=directory/'manifest.json'
    if manifest.is_symlink():raise PermissionError('Linked material manifest refused')
    data=json.loads(manifest.read_text())
    if data['paper_id']!=paper_id or data['job_id']!=job_id:
        raise ValueError('Material snapshot identity changed')
    digest=data['sha256']
    if not re.fullmatch('[0-9a-f]{64}',digest):raise ValueError('Invalid material digest')
    path=directory/(digest+'.txt')
    if path.is_symlink():raise PermissionError('Linked material refused')
    body=path.read_bytes()
    if sha256(body).hexdigest()!=digest or len(body)!=data['size_bytes']:
        raise ValueError('Material snapshot integrity failed')
    return MaterialSnapshot(paper_id,job_id,str(path),str(manifest),digest,len(body))


def prepare_snapshot(*, actor: str, paper_id: str, job_id: str, text: str, metadata: dict):
    """Materialize once after authorization; recovery reuses and verifies it.

    Files belong to the business job and have no cache lease/automatic cleanup.
    Caller records to_payload() in the existing job JSON provenance. This does
    not prove adapter readability. Draft registration needs its own authority.
    """
    from management_portal.resources import get_resource_service
    from management_portal.paper_store import DATA_ROOT
    if not actor:raise PermissionError('Authenticated material actor required')
    _identity(paper_id);_identity(job_id)
    get_resource_service().get(paper_id,actor=actor)
    root=(DATA_ROOT/'job_materials').resolve()
    root.mkdir(parents=True,exist_ok=True)
    directory=root/job_id
    if directory.is_symlink():raise PermissionError('Linked job directory refused')
    directory.mkdir(exist_ok=True)
    if (directory/'manifest.json').exists():return _read(directory,paper_id,job_id)
    if not isinstance(text,str) or not text.strip():raise ValueError('Extracted material text required')
    body=text.encode('utf-8');digest=sha256(body).hexdigest()
    manifest={'schema':'tfi.paper-job-material.v1','paper_id':paper_id,'job_id':job_id,
              'sha256':digest,'size_bytes':len(body),'metadata':metadata}
    encoded=(json.dumps(manifest,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode()
    _write_once(directory/(digest+'.txt'),body)
    _write_once(directory/'manifest.json',encoded)
    return _read(directory,paper_id,job_id)


def validate_path_result(content: str):
    """Additional artifact check; existing section/schema validation still runs."""
    if READ_FAILURE in content:
        raise ValueError('无法读取论文材料；未自动改发全文，请检查材料访问后显式重试')
    # Require an explicit location, not merely a generic Evidence heading.
    if not re.search(r'(?:第\s*[一二三四五六七八九十\d]+\s*[章节页]|\b(?:section|page|p\.)\s*\d|§\s*\d)',content,re.I):
        raise ValueError('Path-mode result lacks chapter/page evidence')


def path_instruction(snapshot: MaterialSnapshot, *, title: str, task: str, output_protocol: str):
    return (f'{task}\n论文ID：{snapshot.paper_id}\n标题：{title}\n'
            f'请先用文件读取工具读取完整提取文本：{snapshot.text_path}\n'
            f'元数据：{snapshot.metadata_path}\n文本SHA256：{snapshot.sha256}\n'
            f'无法读取时仅输出 {READ_FAILURE}，不得依据标题或记忆生成。'
            '结论必须注明材料中的具体章节或页码。结果由业务服务校验保存，不自行写业务产物。\n'
            f'输出协议：\n{output_protocol}')

TASK_SCOPES = frozenset({'paper:deep-read','paper:review','paper:exp','paper:registration-metadata'})

def _authorize_subject(actor, subject):
    if subject.startswith('draft:'):
        from management_portal.paper_registration import _paper_registration_draft
        _paper_registration_draft(subject[6:],actor=actor)
    else:
        from management_portal.resources import get_resource_service
        get_resource_service().get(subject,actor=actor)


def prepare_task_input(*,actor,scope,attempt,subject,full_prompt):
    """Business-owned immutable task snapshot; reference carries no file path."""
    from management_portal.paper_projection import native
    from management_portal.paper_store import DATA_ROOT
    _authorize_subject(actor,subject)
    identity=native.lookup('paper',actor=actor,resident_scope=scope)
    target=identity.get('resident_id')
    if not target:raise ValueError('Paper material role is not bound')
    key='task-'+sha256((scope+'\0'+attempt).encode()).hexdigest()
    root=(DATA_ROOT/'job_materials').resolve();root.mkdir(parents=True,exist_ok=True)
    directory=root/key
    if directory.is_symlink():raise PermissionError('Linked task snapshot refused')
    directory.mkdir(exist_ok=True)
    if not (directory/'manifest.json').exists():
        full_prompt += '\n\n必须提供具体章节或页码依据；JSON输出增加source_evidence字符串字段，不在JSON外加说明。'
        body=full_prompt.encode();digest=sha256(body).hexdigest()
        manifest={'schema':'tfi.paper-task-material.v1','paper_id':subject,'job_id':key,'sha256':digest,'size_bytes':len(body),
                  'metadata':{'actor':actor,'scope':scope,'attempt':attempt,'target_resident_id':target}}
        _write_once(directory/(digest+'.txt'),body)
        _write_once(directory/'manifest.json',(json.dumps(manifest,ensure_ascii=False,sort_keys=True)+'\n').encode())
    ref={'type':'paper.task-material','version':1,'key':key}
    verified=resolve_task_reference(actor=actor,target_resident_id=target,reference=ref)
    # Recovery uses the admitted immutable task even if current library text changed.
    return verified['full_prompt'],ref,verified


def resolve_task_reference(*,actor,target_resident_id,reference):
    from management_portal.paper_store import DATA_ROOT
    if set(reference)!={'type','version','key'} or reference['version']!=1 or reference['type']!='paper.task-material':
        raise ValueError('Invalid task material reference')
    key=_identity(reference['key']);directory=(DATA_ROOT/'job_materials').resolve()/key
    if directory.is_symlink():raise PermissionError('Linked task directory refused')
    manifest=json.loads((directory/'manifest.json').read_text());meta=manifest['metadata']
    if meta['actor']!=actor or meta['target_resident_id']!=target_resident_id or meta['scope'] not in TASK_SCOPES:
        raise PermissionError('Task material actor or target changed')
    subject=manifest['paper_id'];_authorize_subject(actor,subject)
    snapshot=_read(directory,subject,key)
    full=Path(snapshot.text_path).read_text()
    instruction=(f'完成{meta["scope"]}独立任务。业务对象：{subject}。先读取任务及论文材料快照：{snapshot.text_path}\n'
                 f'元数据：{snapshot.metadata_path}\nSHA256：{snapshot.sha256}\n'
                 '快照包含本次材料及完整输出协议；按该协议交付，最终结果由业务服务保存。'
                 f'无法读取只输出{READ_FAILURE}，不得凭标题或记忆生成。结论必须带具体章节或页码；'
                 'JSON任务增加source_evidence字符串字段列明出处，不要输出JSON外说明。')
    return {'type':'paper.task-material','version':1,'full_prompt':full,
            'input_variants':{'version':1,'path_prompt':instruction,'materials':[{'identity':subject+'#'+key,'sha256':snapshot.sha256,'path':snapshot.text_path}]},
            'provenance':snapshot.to_payload()}


def variants_for_message(snapshot, prompt):
    """Keep workflow instructions around the single canonical task body."""
    full=snapshot['full_prompt']
    if not full or prompt.count(full)!=1:raise ValueError('Task material body boundary changed')
    value=dict(snapshot['input_variants'])
    value['path_prompt']=prompt.replace(full,value['path_prompt'],1)
    return value
