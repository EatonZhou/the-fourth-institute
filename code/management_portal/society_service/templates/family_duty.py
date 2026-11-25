"""Reusable Family duty recipes; binding is resolved from the target Steward."""
from string import Template
from .familize import reconcile_in_body
from .registry import result, TemplateError
from .. import family_review_duty, lineage_graph_edits


def install_review(body, steward, actor, template):
    return family_review_duty.install(body, steward, actor, template, reconcile=reconcile_in_body)


class FamilyDutyKind:
    key = 'family_duty'
    title = 'Family 职责'
    targets = ('resident',)

    def normalize(self, template):
        body = template['body']
        if set(body) != {'interval_seconds','guide','prompt'}:
            raise TemplateError('回顾职责需要周期、指南和触发提示词')
        if type(body['interval_seconds']) is not int or body['interval_seconds'] < 1:
            raise TemplateError('回顾间隔须为正整数')
        if any(not isinstance(body[k], str) or not body[k].strip() or len(body[k]) > 20000 for k in ('guide','prompt')):
            raise TemplateError('指南与提示词须为 1–20000 字文本')
        try:
            Template(body['prompt']).substitute(facts='',guide_path='',guide_version='')
            assert 'facts' in Template(body['prompt']).get_identifiers()
        except (ValueError, KeyError, AssertionError):
            raise TemplateError('提示词须保留 ${facts}，仅支持 facts、guide_path、guide_version') from None
        return template

    def describe(self, template):
        return {'persistent':True, 'steps':['给目标 Steward 安装可独立撤下的周期组织回顾职责；自动绑定所属 Family。']}

    def editor(self):
        return {'fields':[{'name':'interval_seconds','type':'integer','label':'回顾间隔（秒）','min':1},
                          {'name':'guide','type':'text','label':'职责指南','multiline':True},
                          {'name':'prompt','type':'text','label':'触发提示词','multiline':True}]}

    def editor_graph(self, template):
        from .family_graph import project
        return project(template)

    def _apply(self, ctx, dry_run):
        if len(ctx.target['resident_ids']) != 1:
            raise TemplateError('请选择一个 Family Steward')
        def edit(body):
            block = install_review(body, ctx.target['resident_ids'][0], ctx.actor, ctx.template)
            if ctx.title:
                block['title'] = ctx.title
            return block['id']
        _, bid = lineage_graph_edits.commit_lineage_edit(ctx.lineage_id, ctx.actor, edit,
            reason='安装周期组织回顾职责', dry_run=dry_run)
        return result('预览安装周期组织回顾职责' if dry_run else '已安装周期组织回顾职责',
                      function_block_ids=[] if dry_run else [bid], preview=[{'title':ctx.template['title'],'summary':'周期与指南随职责保存，撤下后不自动重建'}] if dry_run else [])

    def preview(self, ctx):
        return self._apply(ctx, True)

    def apply(self, ctx):
        return self._apply(ctx, False)


KIND = FamilyDutyKind()
