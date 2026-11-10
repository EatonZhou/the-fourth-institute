"""Weixin Markdown filtering and channel presentation."""
from __future__ import annotations

import re

from .channel import ChannelCapabilities, split_markdown_text
from .telegram_presentation import TelegramPresentation


def filter_markdown(text):
    # Protect both fenced and inline code from prose transformations.
    parts = re.split(r'(```[\s\S]*?```|`[^`\n]+`)', text)
    for index in range(0, len(parts), 2):
        value = re.sub(r'(?m)^#{5,6}\s+', '', parts[index])
        value = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', value)
        value = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1（\2）', value)
        parts[index] = re.sub(r'~~(.*?)~~', r'\1', value)
    return ''.join(parts)


def split_text(text):
    return split_markdown_text(filter_markdown(text), limit=WeixinPresentation.capabilities.text_limit - 100)


class WeixinPresentation(TelegramPresentation):
    capabilities = ChannelCapabilities(buttons=False, edit_messages=False, unlimited_sends=False, command_menu=False, text_limit=3800)
    title = '微信'
    receipts = False
    help_text = '\n'.join([
        '**Telepus 微信使用说明**',
        '在 Portal 的 Telepus 联系人区域扫码绑定后，文字、图片和文件交给你的个人助手。正在输入表示消息已进入 Portal。',
        '引用一条回复继续讨论，助手会联系原回复成员。问题逐题作答：回复编号或直接写答案。',
        '门面上的成员权限请求只可选择“不交出”；授权请直接 /route 联系成员或在网页作答。',
        '微信发送额度有限；看到暂停提示时，发任意消息可继续接收。',
        '/help 本说明；/home 返回个人助手；/route <resident_id> 精准路由；/where 当前目标',
        '/history [n] 最近 1–10 轮；/full <编号> 全文；/earlier 更早',
        '/more 长回复下一段；/file <编号> 下载附件',
        '/back 上一题；/skip 关闭问题；/queue 队列；/action <编号> 队列操作',
        '/stop 停止；/stop current 精准路由只停止当前；/now <内容> 插入当前；/refresh 拉取回复',
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
