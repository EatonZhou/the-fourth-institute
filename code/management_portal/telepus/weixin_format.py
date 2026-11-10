"""Weixin Markdown filtering and channel presentation."""
from __future__ import annotations

import re

from .channel import ChannelCapabilities, split_markdown_text
from .telegram_presentation import TelegramPresentation


def _link(match):
    label, target = match.group(1), match.group(2).strip()
    # The Weixin client renders Markdown links; a Portal-relative address cannot be opened on the phone.
    return match.group(0) if re.match(r'(?i)https?://', target) else label


def filter_markdown(text):
    # Protect both fenced and inline code from prose transformations.
    parts = re.split(r'(```[\s\S]*?```|`[^`\n]+`)', text)
    for index in range(0, len(parts), 2):
        value = re.sub(r'(?m)^#{5,6}\s+', '', parts[index])
        value = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', value)
        value = re.sub(r'\[([^\]]+)\]\(([^)\s]+)\)', _link, value)
        parts[index] = re.sub(r'~~(.*?)~~', r'\1', value)
    return ''.join(parts)


_LIST = re.compile(r'^\s{0,3}([-*+]|\d{1,3}[.)])\s')
_TABLE = re.compile(r'^\s*\|')
_TABLE_RULE = re.compile(r'^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$')


def _cells(line):
    return [cell.strip() for cell in line.strip().strip('|').split('|')]


def _table_as_list(rows):
    """A table of three or more columns as one block per row; wide tables are hard to read on a phone."""
    header = _cells(rows[0])
    if len(header) < 3:
        return rows
    out = []
    for number, row in enumerate(rows[2:], 1):
        cells = _cells(row)
        if out:
            out.append('')
        out.append(f'**{cells[0]}**' if cells and cells[0] else f'**第 {number} 行**')
        out.extend(f'- {name}：{value}' for name, value in zip(header[1:], cells[1:]) if value)
    return out


def _kind(line, in_list):
    if _LIST.match(line) or (in_list and re.match(r'^(\s{2,}|\t)\S', line)):
        return 'list'
    if _TABLE.match(line):
        return 'table'
    if line.lstrip().startswith('>'):
        return 'quote'
    return 'text'


def _layout(lines):
    """Separate blocks with a blank line.

    The Weixin client renders Markdown, where a single line break joins lines into
    one paragraph: a title runs into the first sentence, and a sentence after a
    list or quote is drawn inside it.  Lines of one list, table or quote stay together.
    """
    out, previous, in_list = [], None, False
    for line in lines:
        if not line.strip():
            if out and out[-1] != '':
                out.append('')
            previous = None
            continue
        kind = _kind(line, in_list)
        in_list = kind == 'list'
        if previous and not (kind == previous and kind in {'list', 'table', 'quote'}) and out[-1] != '':
            out.append('')
        out.append(line)
        previous = kind
    return out


def format_text(text):
    """Weixin layout of Markdown text; fenced code is kept verbatim."""
    blocks = re.split(r'(?m)(^```[^\n]*\n[\s\S]*?^```[ \t]*$)', filter_markdown(str(text or '')))
    out = []
    for index, block in enumerate(blocks):
        if index % 2:
            if out and out[-1] != '':
                out.append('')
            out.extend(block.split('\n'))
            out.append('')
            continue
        lines, prose = block.split('\n'), []
        position = 0
        while position < len(lines):
            line = lines[position]
            if _TABLE.match(line) and position + 1 < len(lines) and _TABLE_RULE.match(lines[position + 1]):
                end = position + 2
                while end < len(lines) and _TABLE.match(lines[end]):
                    end += 1
                table = _table_as_list(lines[position:end])
                prose.extend(table if table is lines[position:end] else ['', *table, ''])
                position = end
                continue
            prose.append(line)
            position += 1
        out.extend(_layout(prose))
    while out and out[-1] == '':
        out.pop()
    while out and out[0] == '':
        out.pop(0)
    return '\n'.join(out)


def split_text(text):
    return split_markdown_text(format_text(text), limit=WeixinPresentation.capabilities.text_limit - 100)


def segment_text(segments, total):
    """One segment to send; a reply sent in several parts says which part this is."""
    if total <= 1:
        return segments[0]
    number = total - len(segments) + 1
    hint = f'（第 {number}/{total} 段，回复 /more 继续）' if len(segments) > 1 else f'（第 {number}/{total} 段，完）'
    return segments[0].rstrip() + '\n\n' + hint


class WeixinPresentation(TelegramPresentation):
    capabilities = ChannelCapabilities(buttons=False, edit_messages=False, unlimited_sends=False, command_menu=False, text_limit=3800)
    title = '微信'
    receipts = False
    help_text = '\n'.join([
        '**Telepus 微信使用说明**',
        '',
        '**发消息**',
        '- 扫码绑定后，文字、图片和文件交给你的个人助手；显示“正在输入”表示消息已进入 Portal。',
        '- 等待超过 5 分钟会收到一次“仍在处理”。',
        '- 引用一条回复继续讨论，助手会联系写那条回复的成员。',
        '',
        '**问答**',
        '- 问题逐题出现：回复编号选择，或直接写答案。',
        '- 门面上的成员权限请求只能选“不交出”；要授权请 /route 直接联系成员，或在网页作答。',
        '',
        '**发送额度**',
        '- 微信限制机器人连续发送的条数；看到暂停提示时，发任意消息继续接收。',
        '',
        '**命令**',
        '- /help 本说明',
        '- /home 回到个人助手',
        '- /route <resident_id> 直接联系一个 Resident',
        '- /where 当前目标',
        '- /history [n] 最近 1–10 轮；/full <编号> 看全文；/earlier 更早',
        '- /more 长回复的下一段',
        '- /file <编号> 发送回复中的附件',
        '- /back 上一题；/skip 不再回答',
        '- /queue 未完成的消息；/action <编号> 对它操作',
        '- /stop 停止；/stop current 精准路由时只停当前',
        '- /now <内容> 插入当前工作；/refresh 立即拉取回复',
    ])

    def _question_prompt(self, core, session):
        question = session['questions'][session['step']]
        options = self.options(session)
        lines = [f"❓ **问题 {session['step'] + 1}/{len(session['questions'])}**：{question['title']}"]
        if question.get('credentials'):
            lines.append('请求交出的权限：' + '、'.join(f"{c.get('a')} · {c.get('resource_id')}" for c in question['credentials']))
            if not session['lendable']:
                lines.append('授权请 /route 直接联系成员，或在网页作答。')
        lines.extend(f'{i}. {option}' for i, option in enumerate(options, 1))
        lines.append('回复编号选择，或直接写你的答案；/back 上一题，/skip 不再回答')
        return {'text': '\n'.join(lines), 'question_session': session['session_id']}

    @staticmethod
    def options(session):
        question = session['questions'][session['step']]
        if question.get('credentials'):
            return ['同意交出这些权限', '不交出'] if session['lendable'] else ['不交出']
        return [str(v) for v in question.get('options') or []]

    def incoming(self, core, state, message):
        chat = state['chat_id']
        session = core.store.active_question(chat)
        command, _, argument = message.partition(' ')
        if session and command in {'/back', '/skip'}:
            if command == '/back':
                session['step'] = max(0, session['step'] - 1)
                core.store.update_question_session(session['session_id'], step=session['step'])
                return {'messages': [self._question_prompt(core, session)]}
            core.portal.close_questions(state['portal_actor'], session['resident_id'], session['source_message_id'])
            core.store.update_question_session(session['session_id'], status='closed')
            return core._reply(['这组问题已关闭，不再回答。'])
        if command in {'/file', '/full', '/earlier', '/action'}:
            key = 'weixin-control:' + chat + ':' + command + ':' + (argument.strip() if command != '/earlier' else '')
            callback = core.store.get_meta(key, '')
            if not callback:
                return core._reply(['这个编号已失效，请重新读取回复。'])
            return core.dispatch_callback(chat_id=chat, telegram_user_id=state['telegram_user_id'],
                                         portal_actor=state['portal_actor'], data=callback)
        if session and not message.startswith('/'):
            options = self.options(session)
            choice = int(message) if message.isascii() and message.isdigit() else 0
            selected = 1 <= choice <= len(options)
            return core._answer_question(state, session, session['step'], options[choice - 1] if selected else message, custom=not selected)
        return None
