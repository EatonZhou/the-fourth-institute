"""Paper owns business identities and their read authorization, not messages."""
from hashlib import sha256
import json


def figure_version(figure, images):
    """Called only after the figure owner has authorized its selected files."""
    from pathlib import Path
    version = [str(figure.get('figure_id') or ''), str(figure.get('caption') or '')]
    version.extend(sha256(Path(path).read_bytes()).hexdigest() for path in images or [])
    return sha256(json.dumps(version, ensure_ascii=False).encode()).hexdigest()


def build(*, scope, actor, metadata, message_id, business_ref='', material=None):
    subject = metadata.get('material_subject') or metadata.get('paper_id')
    if not subject and metadata.get('collector_job_id'):
        subject = 'collector:' + metadata['collector_job_id']
    if not subject:
        source = metadata.get('source_identity')
        if not source:
            raise ValueError('Paper independent task requires a business subject')
        # Public catalogue enrichment has no registered Paper yet. This is the
        # caller's request, not a synthetic Paper or a grant to a shared cache.
        subject = 'request:' + actor + ':' + sha256(str(source).encode()).hexdigest()
    version = (material or {}).get('provenance', {}).get('sha256', '') or metadata.get('material_version', '')
    group = sha256(json.dumps([scope, subject, version], ensure_ascii=False).encode()).hexdigest()
    return {'application': scope.replace(':', '.'), 'subject_ref': subject,
            'group_ref': 'paper-group:' + group, 'attempt_ref': business_ref or message_id}


def resolve(*, actor, target_resident_id, reference):
    # Instruction admission (the target Resident capability) is checked before
    # this handler. A denied subject is a row visibility result; a denied
    # resolver capability or unavailable owner remains an explicit error.
    from .hatg.contracts import InstructionError
    try:
        return _resolve(actor=actor, target_resident_id=target_resident_id, reference=reference)
    except (PermissionError, KeyError):
        return {'type': 'business-history', 'version': 1, 'visible': False}
    except InstructionError as error:
        if error.code in {'instruction.forbidden', 'instruction.not_found'}:
            return {'type': 'business-history', 'version': 1, 'visible': False}
        raise


def _resolve(*, actor, target_resident_id, reference):
    from .business_context import message_metadata
    if set(reference) != {'type', 'version', 'business_context'} or reference.get('version') != 1:
        raise ValueError('Invalid business history reference')
    context = message_metadata({'business_context': reference['business_context']})['business_context']
    from .paper_projection import ROLES
    if context['application'].replace('.', ':', 1) not in ROLES:
        raise ValueError('Unknown Paper business application')
    subject = context['subject_ref']
    if subject.startswith('draft:'):
        from .paper_registration import _paper_registration_draft
        draft = _paper_registration_draft(subject[6:], actor=actor)
        if draft.get('actor') != actor:
            raise PermissionError('Registration draft belongs to another caller')
        label = draft.get('filename') or subject
    elif subject.startswith('collector:'):
        from .paper_collection_jobs import _paper_collection_job_by_id
        job = _paper_collection_job_by_id(subject[10:])
        if not job or job.get('deleted'):
            raise KeyError('Collection history not found')
        if job.get('created_by') != actor:
            raise PermissionError('Collection history belongs to another caller')
        label = job.get('direction') or job.get('query') or subject
    elif subject.startswith('request:'):
        if not subject.startswith('request:' + actor + ':'):
            raise PermissionError('Catalogue request belongs to another caller')
        label = context['application'] + ' · ' + subject.rsplit(':', 1)[-1][:12]
    else:
        from .resources import get_resource_service
        resource = get_resource_service().get(subject, actor=actor)
        label = getattr(resource, 'title', None) or (resource.get('title') if isinstance(resource, dict) else None) or subject
    return {'type': 'business-history', 'version': 1, 'label': str(label)[:512]}
