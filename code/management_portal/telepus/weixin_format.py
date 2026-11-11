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


def without_links(text):
    text = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', text)
    return re.sub(r'(?i)https?://[^\s<>\u3000]+', '[链接]', text)


def notification_text(items, *, total=None, summary=True):
    """Visible source excerpts exist only in this in-memory outgoing message."""
    from .quote_text import quote_text

    total = len(items) if total is None else total
    lines = [f'🔔 Portal 有 {total} 件事需要你查看'] if total > 1 else []
    for item in items[:5]:
        title = ' '.join(str(item['title']).split())[:100].replace('*', '')
        if item['category'] == 'mention':
            author = ' '.join(str(item.get('author') or '').split()).replace('*', '')[:100]
            lines.append(f'🔔 **{author}** 在 **{title}** 里 @了你')
            lines.append(item.get('excerpt') or '')
            if not summary:
                lines.append('引用这条消息回复，会以你的名义发到聊天室')
        else:
            lines.append(f'❓ **{title}** 在等你回答')
            questions = item['questions']
            for index, question in enumerate(questions[:3], 1):
                visible = ' '.join(without_links(quote_text(question['title'])).split())
                excerpt = visible[:120] + ('…' if len(visible) > 120 else '')
                lines.append(f'{index}. ' + ('待批准：' if item['kind'] == 'approval' else '') + excerpt)
                if question.get('options'):
                    lines.append('选项：' + '；'.join(f'{number}. {option}' for number, option in enumerate(question['options'], 1)))
            if len(questions) > 3:
                lines.append(f'另有 {len(questions) - 3} 个问题')
            if item.get('requires_authorization'):
                lines.append('这个问题要授权，请回 Portal 回答。')
            elif summary or len(questions) > 3:
                lines.append('问题较多，请回 Portal 回答。')
            elif item['kind'] == 'approval':
                lines.append('引用这条消息回答：回复“同意”或“拒绝”。')
            elif len(questions) > 1:
                lines.append('引用这条消息回答：每行一个“问题序号 答案”，例如“1 选项文字”。')
            else:
                lines.append('引用这条消息回答：回复选项序号或文字，例如“1”。')
    if total > len(items):
        lines.append(f'另有 {total - len(items)} 项待发送，将继续分批提醒。')
    if summary:
        lines.append('这是汇总提醒，请回 Portal 回复，或引用对应的单条 @ 提醒。')
    # Source excerpts and display names may themselves contain links. Keep their
    # labels, but reminders never include a clickable URL (ordinary replies do).
    return format_text(without_links('\n'.join(lines)))


class WeixinPresentation(TelegramPresentation):
    capabilities = ChannelCapabilities(buttons=False, edit_messages=False, unlimited_sends=False, command_menu=False, text_limit=3800)
    title = '微信'
    receipts = False
    help_text = '\n'.join([
        '**Telepus 微信使用说明**',
        '',
        '**发消息**',
        '- 扫码绑定后，文字、图片和文件由个人助手里的手机助手直接处理；显示“正在输入”表示消息已进入 Portal。',
        '- 等待超过 5 分钟会收到一次“仍在处理”。',
        '- 引用一条回复继续讨论，助手会联系写那条回复的成员。',
        '- 引用单条 @ 提醒回复，会以你的名义发到对应聊天室；汇总提醒请回 Portal 回复。',
        '- 引用问题提醒可直接回答：单题回复选项序号或文字，多题每行“题号 答案”，批准请求回复“同意”或“拒绝”。问题未列全或需要授权时请回 Portal 回答。',
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
        '- /notify 查看提醒；/notify mention on|off 开关聊天室 @ 我；/notify question on|off 开关待我回答',
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

    def help_messages(self, where):
        return [self.help_text + '\n\n' + where]

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
        if command == '/notify':
            from .notifications import Notifications
            notifications = Notifications(core.store)
            args = argument.split()
            if args and (len(args) != 2 or args[0] not in ('mention', 'question') or args[1] not in ('on', 'off')):
                return core._reply(['用法：/notify；/notify mention on|off；/notify question on|off'])
            # Inbound identity and per-user lock are already established by the adapter.
            actor = state['portal_actor']
            prefs = notifications.update(actor, **{args[0]: args[1] == 'on'}) if args else notifications.preferences(actor)
            return core._reply([f"微信提醒：\n聊天室 @ 我：{'开启' if prefs['mention'] else '关闭'}\n"
                                f"待我回答：{'开启' if prefs['question'] else '关闭'}\n开启后只提醒新出现的事项。"])
        if session and command in {'/back', '/skip'}:
            if command == '/back':
                session['step'] = max(0, session['step'] - 1)
                core.store.update_question_session(session['session_id'], step=session['step'])
                return {'messages': [core._question_prompt(session)]}
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
