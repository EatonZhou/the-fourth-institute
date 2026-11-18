"""Read an explicit user's decision through Society's existing message owner."""
from __future__ import annotations

from .article_restore_preview import digest

APPROVE = '同意按该项目当前共享规则恢复'


def proposal_text(scope):
    """Exact inspectable scope, not a token conferring authority."""
    return (f"恢复对象：{scope['article_id']}（{scope['title']}）。\n"
            '恢复内容：已找回的旧 PDF、历史源文件与可证修改；main.md 明确标注部分恢复，最终稿未齐。\n'
            f"候选文件：{scope['source_address']}。\n"
            f"目标目录：{scope['project_id']}/{scope['target_relative_path']}。\n"
            '旧 ACL 未知；按目标项目当前共享规则继承，适用拒绝仍生效；不是仅申请者私有。\n'
            '范围校验：' + digest(scope))


def verify(arguments, actor, scope):
    from management_portal.society_service.resident_protocol import get_resident_message
    from management_portal import permission_system

    rid = arguments['decision_resident_id']
    proposal = get_resident_message(rid, actor, arguments['proposal_message_id'])
    answer = get_resident_message(rid, actor, arguments['decision_message_id'])
    parts = proposal.get('content_parts') or []
    text = '\n'.join(p.get('text', '') for p in parts if p.get('type') == 'text')
    if len(text.encode()) > 65536 or proposal_text(scope) not in text:
        raise ValueError('Recovery proposal does not match the exact current scope')
    sender = (answer.get('origin') or {}).get('sender') or {}
    user = sender.get('id')
    if (answer.get('role') != 'user' or sender.get('kind') != 'user'
            or not user or answer.get('author_id') != user):
        raise PermissionError('Recovery decision must be a genuine direct User message')
    answers = answer.get('content_parts') or []
    sets = [p for p in parts if p.get('type') == 'async_questions']
    fixed = [p for p in answers if p.get('type') == 'async_question_answer']
    approved = False
    if len(sets) == 1 and len(fixed) == 1:
        questions = sets[0].get('questions') or []
        values = fixed[0].get('answers') or []
        approved = (sets[0].get('answer_message_id') == arguments['decision_message_id']
            and fixed[0].get('source_message_id') == arguments['proposal_message_id']
            and len(questions) == len(values) == 1
            and APPROVE in questions[0].get('options', [])
            and values[0].get('question_index') == 0 and values[0].get('answer') == APPROVE)
    direct = '\n'.join(p.get('text', '') for p in answers if p.get('type') == 'text').strip()
    if len(direct.encode()) > 65536:
        raise ValueError('Recovery decision text exceeds the bounded review budget')
    if (answer.get('quote') or {}).get('message_id') == arguments['proposal_message_id']:
        approved = approved or direct in {APPROVE, '同意', '同意该共享范围'}
    if not approved:
        raise ValueError('Current project sharing has not been explicitly approved for this proposal')
    # A historical answer alone cannot keep revoked authority alive.
    checks = {**arguments, 'source_address': arguments['source_address'].split('/')[0]}
    if not permission_system.allows(user, 'RestoreArticle', checks):
        raise PermissionError('The deciding User no longer holds the required recovery permissions')
    return {'user': user, 'proposal_message_id': arguments['proposal_message_id'],
            'decision_message_id': arguments['decision_message_id'], 'scope_digest': digest(scope)}
