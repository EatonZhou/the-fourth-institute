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


def _read(directory: Path, data: dict, job_id: str):
    """Return the same bytes that passed integrity validation, without reopening."""
    if data['job_id']!=job_id:
        raise ValueError('Material snapshot identity changed')
    digest=data['sha256']
    if not re.fullmatch('[0-9a-f]{64}',digest):raise ValueError('Invalid material digest')
    path=directory/(digest+'.txt')
    if path.is_symlink():raise PermissionError('Linked material refused')
    body=path.read_bytes()
    if sha256(body).hexdigest()!=digest or len(body)!=data['size_bytes']:
        raise ValueError('Material snapshot integrity failed')
    snapshot = MaterialSnapshot(data['paper_id'],job_id,str(path),str(directory/'manifest.json'),digest,len(body))
    return snapshot, body.decode('utf-8')


def validate_path_result(content: str):
    """Additional artifact check; existing section/schema validation still runs."""
    if READ_FAILURE in content:
        raise ValueError('无法读取论文材料；未自动改发全文，请检查材料访问后显式重试')
    # Require an explicit location, not merely a generic Evidence heading.
    if not re.search(r'(?:第\s*[一二三四五六七八九十\d]+\s*[章节页]|\b(?:section|page|p\.)\s*\d|§\s*\d)',content,re.I):
        raise ValueError('Path-mode result lacks chapter/page evidence')


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
        if scope in {'paper:deep-read', 'paper:exp'}:
            full_prompt += '\n\n正文直接输出Markdown，禁止将正文包入JSON。末尾附<!-- PAPER_SOURCE_EVIDENCE -->标记，再附```json代码块，仅含files与locations字段记录实际读取文件和章节/页码依据。证据块不是正文。'
        else:
            full_prompt += '\n\n保持任务要求的原JSON字段，另增加source_evidence字符串字段，提供具体章节或页码依据；不在JSON外加说明。'
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
    manifest_path = directory/'manifest.json'
    if manifest_path.is_symlink():raise PermissionError('Linked material manifest refused')
    manifest=json.loads(manifest_path.read_text());meta=manifest['metadata']
    if meta['actor']!=actor or meta['target_resident_id']!=target_resident_id or meta['scope'] not in TASK_SCOPES:
        raise PermissionError('Task material actor or target changed')
    subject=manifest['paper_id'];_authorize_subject(actor,subject)
    snapshot,full=_read(directory,manifest,key)
    instruction=(f'完成{meta["scope"]}独立任务。业务对象：{subject}。先读取任务及论文材料快照：{snapshot.text_path}\n'
                 f'元数据：{snapshot.metadata_path}\nSHA256：{snapshot.sha256}\n'
                 '快照包含本次材料及完整输出协议；按该协议交付，最终结果由业务服务保存。'
                 f'无法读取只输出{READ_FAILURE}，不得凭标题或记忆生成。结论必须带具体章节或页码；'
                 '遵守快照中的输出协议；Markdown长文用正文加末尾PAPER_SOURCE_EVIDENCE证据块，小JSON任务保留其schema。')
    return {'type':'paper.task-material','version':1,'business_ref':meta['attempt'],'role_scope':meta['scope'],'full_prompt':full,
            'input_variants':{'version':1,'path_prompt':instruction,'materials':[{'identity':subject+'#'+key,'sha256':snapshot.sha256,'path':snapshot.text_path}]},
            'provenance':snapshot.to_payload()}


def variants_for_message(snapshot, prompt):
    """Keep workflow instructions around the single canonical task body."""
    full=snapshot['full_prompt']
    if not full or prompt.count(full)!=1:raise ValueError('Task material body boundary changed')
    value=dict(snapshot['input_variants'])
    value['path_prompt']=prompt.replace(full,value['path_prompt'],1)
    return value


def resident_input_variants(snapshot, prompt):
    """Contribute variants only for a verified Paper material snapshot."""
    if snapshot.get('type') != 'paper.task-material':
        return None
    return variants_for_message(snapshot, prompt)
