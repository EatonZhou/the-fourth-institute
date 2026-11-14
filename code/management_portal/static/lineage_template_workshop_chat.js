(() => {
  'use strict';
  const t = text => window.PortalI18n.t(text);
  const DUTY_DESCRIPTION = '模板工坊助手：校验并交回草稿，由用户保存';
  const DUTY_PROMPT = `你是当前 Lineage 的工坊助手，协助用户编辑模板。当前模板 ID 与本轮草稿路径由对话附带的上下文给出。
已保存的模板先用 GetTemplate {template_id} 读取，按其 kind 和 editor.fields 编辑原模块，不转换 kind。prompt 编辑 body.message，script 编辑 body.script 与 endpoints；explore、familize 遵循 GetTemplate 返回的字段说明。新模板尚未保存，先按用户说明创建 structure 草稿，默认包含 {key:"target",from:"target"} 空位。
structure 的 body 包含 roles（key、from:target/lineage_leader/new/fork；新角色可设 title、settings，Fork 设 fork_of）、components（key、kind、config）、wires（key、kind、source、target、端口及 config）、match（已有元素的查找条件及 keep/update/delete）和 layout（key 到 {x,y} 的位置）。完整结构语义先 ReadResource decision_b04cac4188bb，阅读其中 structure 一节。不要凭空编造原语字段。
工作结果写为自己工作区中本轮指定路径的 UTF-8 JSON 完整对象：{kind,title,description,parameters,body}；layout 放在 body.layout。structure 若有 from:target，targets 为 ["resident"]；否则有 match 为 ["selection"]；否则为 ["canvas"]；category 为 structure。其他 kind 保留原 targets 和 category。
先调用 CreateTemplate {template:完整对象,dry_run:true}，通过后再用已有工作区写文件能力将完整对象写入指定文件。不要写部分补丁。完成后告诉用户草稿已交回画布，尚未保存。
禁止调用 UpdateTemplate、ApplyTemplate，禁止非 dry_run 的 CreateTemplate；不得直接修改正式模板。`;

  function findAssistant(lineage, templateId) {
    const block = (lineage.function_blocks || []).find(item => item.template_id === templateId);
    return (block?.elements || []).find(item => item.kind === 'resident')?.id || '';
  }
  function mount({root, workshop, execute = (name,args,options) => window.PortalInstructionClient.execute(name,args,options)}) {
    let assistant = '', assistantTemplate = '', workspaceId = '', pending = null, panel = null;
    let reading = false, disposed = false, prepared = false, sentDraft = null;
    const versions = new Map();
    const checkedTurns = new Set();
    const pageKey = workshop.sessionId;
    const id = () => workshop.template()?.template_id || `new-${pageKey}`;
    const relativePath = () => `workshop/${id()}.json`;
    const fileAddress = () => `${workspaceId}/file/${relativePath()}`;
    const report = error => { root.querySelector('#workshopChatStatus').textContent = window.PortalI18n.message(error.message || error); };
    async function discover() {
      if (!workshop.lineageId) return '';
      if (!assistantTemplate) {
        assistantTemplate = (await execute('ListTemplates', {})).templates.find(item => item.builtin_key === 'workshop_assistant')?.template_id || '';
        if (!assistantTemplate) throw new Error(t('工坊助手模板尚未安装'));
      }
      const {lineage} = await execute('GetLineage', {lineage_id:workshop.lineageId});
      assistant = findAssistant(lineage, assistantTemplate);
      return assistant;
    }
    async function prepare() {
      if (pending) return pending;
      pending = (async () => {
        if (!workshop.lineageId) throw new Error(t('请从 Lineage 工具箱打开工坊以使用助手'));
        await discover();
        if (!assistant) {
          if (!await window.PortalUI.confirm({title:t('工坊助手'), message:t('将在本 Lineage 中创建工坊助手'), confirmLabel:t('创建')})) return null;
          const args = {lineage_id:workshop.lineageId,template_id:assistantTemplate,target:{kind:'canvas'}};
          await execute('ApplyTemplate', {...args,dry_run:true});
          await execute('ApplyTemplate', args, {idempotencyKey:`workshop-assistant:${workshop.lineageId}`});
          await discover();
          if (!assistant) throw new Error(t('工坊助手创建结果尚未确认，请重试'));
        }
        const {lineage} = await execute('GetLineage', {lineage_id:workshop.lineageId});
        if (!(lineage.function_blocks || []).some(block => block.duty?.holder_id === assistant && block.duty?.description === DUTY_DESCRIPTION)) {
          await execute('CreateDuty', {lineage_id:workshop.lineageId,holder_id:assistant,
            description:DUTY_DESCRIPTION,category:'fixed',memory_level:'session',prompt:DUTY_PROMPT},
          {idempotencyKey:`workshop-assistant-duty:${assistant}`});
        }
        const {resident} = await execute('GetResident', {resident_id:assistant});
        workspaceId = (resident.workspaces || []).find(workspace => workspace.is_owned)?.workspace_id || '';
        if (!workspaceId) throw new Error(t('工坊助手没有可读取的工作区'));
        // Establish a baseline before this turn; an old draft must not replace this page on opening.
        try { const address = fileAddress(), previous = await execute('ReadResource', {address}); versions.set(address,previous.version || previous.text); }
        catch (error) { if (error.code !== 'instruction.not_found') throw error; }
        prepared = true;
        return {resident_id:assistant,lineage_id:workshop.lineageId};
      })();
      try { return await pending; } finally { pending = null; }
    }
    async function readDraft() {
      if (reading || disposed || !workspaceId) return;
      reading = true;
      const target = sentDraft || {address:fileAddress(), documentKey:workshop.documentKey()};
      const {address} = target;
      if (target.documentKey !== workshop.documentKey()) { reading = false; return; }
      try {
        const result = await execute('ReadResource', {address});
        if (disposed || target.documentKey !== workshop.documentKey()) return;
        const version = result.version || result.text;
        if (versions.get(address) === version) return;
        const value = JSON.parse(result.text);
        if (!value || !value.body || !value.kind || !value.title || !Array.isArray(value.parameters)) throw new Error(t('助手草稿不是完整模板对象'));
        versions.set(address,version);
        await workshop.acceptDraft(value);
      } catch (error) { if (error.code !== 'instruction.not_found') report(error); }
      finally { reading = false; }
    }
    function completed(detail) {
      for (const input of detail?.snapshot?.input_states || []) {
        const key = input.message_id;
        if (!key || checkedTurns.has(key) || !['completed','failed','cancelled'].includes(input.state)) continue;
        checkedTurns.add(key); void readDraft();
      }
    }
    function context() {
      const templateId = workshop.template()?.template_id;
      return { ...(templateId ? {resources:[{address:templateId}]} : {}),
        context_text:`${templateId ? `模板：${templateId}` : '新模板，尚未保存'}\n草稿文件：${relativePath()}\n工作区文件地址：${fileAddress()}` };
    }
    function updateWarning() { root.querySelector('#workshopUnsaved').hidden = !workshop.dirty(); }
    root.addEventListener('workshop:draft-changed', updateWarning);
    root.querySelector('#workshopChatSave').addEventListener('click', () => workshop.save().catch(report));
    async function load() {
      const found = await discover();
      const emptyText = workshop.lineageId ? t('和工坊助手一起编辑模板') : t('请从 Lineage 工具箱打开工坊以使用助手');
      panel = window.PortalWorkbench.createPanel('chat', {
        root:root.querySelector('#workshopChat'),id:'templateWorkshopChat',autoLoad:false,
        refsScope:'templateWorkshopChat',draftOwner:() => window.PortalIdentity.username(),
        resolveResident:async () => {
          if (!found) return {binding_state:'unbound',chat_workspace:{schema:'portal.chat_workspace.v3',target:{kind:'workshop',id:workshop.lineageId},
            messages:[],runtime:{live:false,details:[]},composer:{can_send:!!workshop.lineageId,placeholder:emptyText}}};
          // Do not create anything merely by opening the page.
          const {resident} = await execute('GetResident', {resident_id:found});
          workspaceId = (resident.workspaces || []).find(workspace => workspace.is_owned)?.workspace_id || '';
          if (workspaceId) {
            try { const address = fileAddress(), previous = await execute('ReadResource', {address}); versions.set(address,previous.version || previous.text); }
            catch (error) { if (error.code !== 'instruction.not_found') throw error; }
          }
          return {resident_id:found,lineage_id:workshop.lineageId};
        },
        resolveBeforeSend:prepare,submissionContext:async () => {
          if (!prepared) await prepare();
          sentDraft = {address:fileAddress(),documentKey:workshop.documentKey()};
          return context();
        },onDetail:completed,
        emptyText,onError:report,
      });
      await panel.openSurface({lineage_id:workshop.lineageId});
      const composer = root.querySelector('#workshopChat .composer');
      if (composer) composer.before(root.querySelector('#workshopUnsaved'));
    }
    return {load,prepare,readDraft,context,findAssistant,
      destroy() { disposed = true; root.removeEventListener('workshop:draft-changed',updateWarning); panel?.destroy(); },
    };
  }
  window.LineageTemplateWorkshopChat = Object.freeze({mount,findAssistant,DUTY_PROMPT,DUTY_DESCRIPTION});
})();
