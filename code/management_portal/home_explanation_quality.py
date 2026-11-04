"""Quality policy shared by authored explanations and fixed report media."""
LEVELS = ('low', 'medium', 'high')


def prepare_plan(plan, value='medium'):
    value = quality(value)
    if value != 'low' and any(not scene.get('visual', {}).get('events') for scene in plan['scenes']):
        raise ValueError('medium/high需要作者提供与真实字幕对齐的visual.events分镜；现有循环连线计划可选low，或修改为新固定解释页后生成。')
    return {**plan, '_quality': value}


def quality(value='medium'):
    if value not in LEVELS:
        raise ValueError('质量必须是low、medium或high')
    return value


def policy(value='medium', *, video=True):
    value = quality(value)
    if video:
        guidance = {
            'low': '自由选择适合内容的讲解和画面，不强制参照风格；保持事实、字幕和音轨真实。',
            'medium': '以3Blue1Brown式直观解释加强分镜：先给问题和具体对象，再逐步变换；保持颜色与对象身份、同尺度数值、真实音画同步，避免逐页念稿。',
            'high': '按3Blue1Brown级别检查概念递进、数学与因果正确性、连续图形叙事、视觉焦点、节奏与留白、音画同步和窄屏可读性。独立Resident须实际播放固定成片，否决后修改为新固定版再审，未通过不得称达标。',
        }[value]
    else:
        guidance = {'low': '直接Markdown，准确清楚即可。',
                    'medium': '使用现有交互网页模板，组织读者可操作的解释。',
                    'high': '按Distill/Transformer Circuits式解释标准组织真实证据、概念递进和交互；由独立Resident实际操作固定版并审核，修改后重新送审。'}[value]
    return {'quality': value, 'independent_review': value == 'high', 'guidance': guidance,
            'max_submission_attempts': 3}
