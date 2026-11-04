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
    if value == 'high':
        guidance += (
            '先识别已接受用户委托中读者要理解的核心关系，再判断表达是否让读者真正观察、比较和解释该关系。'
            '动态、连续或参数敏感机制须在核心图形中可观察：解释页可做有意义的干预或对照；'
            '固定视频须实际演示干预或对照及其结果，不要求视频具有页面控件。'
            '只加外层按钮、文档美化、静态数表或旁支交互，不能据此签参考体验通过。'
            '禁止按控件、D3或动画数量验收；简单事实仍可简答，不因档位强制造页。'
            '审核须分别说明事实正确和核心体验达标的证据：核验结论/计算，并记录核心图形的实际操作或播放位置、'
            '干预前后差异及其为何足以解释核心关系；不适用动态表达时说明理由。'
            'subject.task、task_context中可追溯的首轮/本轮作者说明、局部目标和自评都不是经核验的用户委托，不能覆盖已接受的原始要求。'
            '任务来源不足以判断原委托时先核对正式来源；无法核实时明确blocked及缺口，不自行缩小目标签通过。')
    return {'quality': value, 'independent_review': value == 'high', 'guidance': guidance,
            'max_submission_attempts': 3}
