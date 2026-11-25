(() => {
  'use strict';
  const t = text => window.PortalI18n.t(text);
  const DUTY_DESCRIPTION = '模板工坊助手：校验并交回草稿，由用户保存';
  const DUTY_PROMPT = `你是用户自己的工坊助手，住在用户的 My Lineage，协助编辑各个工作台中的模板。当前模板 ID 与本轮草稿路径由对话附带的上下文给出；不要使用所在 Lineage 代替模板目标。
若本轮上下文明确指定职责实例，遵循上下文中的职责草稿协议：读取目标 Lineage，限定职责边界，通过 dry_run 校验后交回草稿；禁止正式提交。下列模板协议仅适用于编辑模板。
已保存的模板先用 GetTemplate {template_id} 读取，按其 kind 和 editor.fields 编辑原模块，不转换 kind。prompt 编辑 body.message，script 编辑 body.script 与 endpoints；explore、familize 遵循 GetTemplate 返回的字段说明。新模板从空白 structure 草稿开始，roles、components、wires、match 均为空；目标空位仅在用户需要时显式加入。
structure 的 body 包含 roles、components、wires、match、layout、function_block。所有元素的 key 必须唯一。roles 每项 {key,from,title?,settings?}，from 为 target（施加目标 Resident）、lineage_leader（目标 Lineage 的 Leader）、new（新建 Resident，可省略 provider 使用默认值）、fork（复制已有角色，须给 fork_of:角色key、fork_mode:full/compacted/recent-5/recent-10（省略时 full））。components 每项 {key,kind,title?,config}，kind 为 event_source/effect/loop/filter/selector/reducer/seed；先 ListComponentTypes 查原语配置定义，不猜字段。wires 每项 {key,kind,source,target,source_port?,target_port?,config?}，kind 为 route（激活）、target（对象引用）、watch（Event 观察）；两端使用角色或组件 key，watch 也可观察连线 key。角色引用使用 {{role.key.id}}、{{role.key.ids}}、{{role.key.title}}，参数用 {{param.name}}。
match 用来在施加目标附近查找已有元素，不会匹配 Function Block 内部元素。每项 {key,kind,where?,op?,count?,config?}；op 是 keep/delete/update，count 是 one/all，已有连线另带 source/target 及可选端口，引用其他角色或 match key。op:update 的 config 变化放 update:{config:{字段:值}}，null 清除字段，保留未变字段；config 是查找时的样例配置，不是对已有元素的全量覆盖。已有元素引用 {{match.key.id}}、{{match.key.title}}。layout 是 key 到 {x,y} 的位置；不写时自动布局。function_block:true 把新建元素封装为一个职责结构，不改变已有实例。
工作结果写为自己工作区中本轮指定路径的 UTF-8 JSON 完整对象：{kind,title,description,parameters,body}；layout 放在 body.layout。structure 若有 from:target，targets 为 ["resident"]；否则有 match 为 ["selection"]；否则为 ["canvas"]；category 为 structure。其他 kind 保留原 targets 和 category。
先调用 CreateTemplate {template:完整对象,dry_run:true}，通过后再用已有工作区写文件能力将完整对象写入指定文件。不要写部分补丁。完成后告诉用户草稿已交回画布，尚未保存。
Effect 描述与确认协议：读取组件 config.script_draft.description，查询 ListInstructions / GetInstructionSchema 后编写候选 HATG DSL，填到 script_draft.script 和 script_draft.endpoints；原 config.script / endpoints 是运行版本，必须保持不变。保留 requested_by / confirm_by，不替用户确认或换确认人。没有 script_draft 时，把新编写或改写脚本作为 script_draft:{description,script,endpoints} 交回草稿；新 Effect 的运行 script 为空。模板草稿可使用 CreateTemplate dry_run 校验，职责草稿使用带版本的 ApplyLineageChanges dry_run。用户在工坊保存草稿、确认此版本之后才可生效和上架。禁止调用 ReviewTemplateScript、ReviewComponentScript。
禁止调用 UpdateTemplate、ApplyTemplate，禁止非 dry_run 的 CreateTemplate；不得直接修改正式模板。`;

  function findAssistant(lineage, templateId) {
    const block = (lineage.function_blocks || []).find(item => item.template_id === templateId);
    return (block?.elements || []).find(item => item.kind === 'resident')?.id || '';
  }
  function mount({root, workshop, execute = (name,args,options) => window.PortalInstructionClient.execute(name,args,options),
    listLineages = () => window.PortalAPI.get('/lineage/api/lineages',{cache:'no-store'})}) {
    const element = id => root.querySelector(`[data-workshop-id="${id}"],#${id}`);
    const owner = window.PortalIdentity.username();
    let personalLineageId = '';
    let assistant = '', assistantTemplate = '', workspaceId = '', pending = null, panel = null;
    let reading = false, disposed = false, prepared = false, sentDraft = null;
    const versions = new Map();
    const checkedTurns = new Set();
    const pageKey = workshop.sessionId;
    const id = () => workshop.draftFileKey?.() || workshop.template()?.template_id || `new-${pageKey}`;
    const relativePath = () => `workshop/${id()}.json`;
    const fileAddress = () => `${workspaceId}/file/${relativePath()}`;
    const report = error => { element('workshopChatStatus').textContent = window.PortalI18n.message(error.message || error); };
    async function discover() {
      const listing = await listLineages();
      personalLineageId = (listing.lineages || []).find(lineage => lineage.lineage_membership === 'personal' && String(lineage.created_by || '').toLowerCase() === owner.toLowerCase())?.lineage_id || '';
      if (!personalLineageId) { assistant = ''; return ''; }
      if (!assistantTemplate) {
        assistantTemplate = (await execute('ListTemplates', {})).templates.find(item => item.builtin_key === 'workshop_assistant')?.template_id || '';
        if (!assistantTemplate) throw new Error(t('工坊助手模板尚未安装'));
      }
      const {lineage} = await execute('GetLineage', {lineage_id:personalLineageId});
      assistant = findAssistant(lineage, assistantTemplate);
      return assistant;
    }
    async function prepare() {
      if (pending) return pending;
      pending = (async () => {
        await discover();
        if (!personalLineageId) throw new Error(t('当前用户没有 My Lineage，无法使用工坊助手'));
        if (!assistant) {
          const args = {lineage_id:personalLineageId,template_id:assistantTemplate,target:{kind:'canvas'}};
          await execute('ApplyTemplate', {...args,dry_run:true});
          await execute('ApplyTemplate', args, {idempotencyKey:`workshop-assistant:${personalLineageId}`});
          await discover();
          if (!assistant) throw new Error(t('工坊助手创建结果尚未确认，请重试'));
        }
        const {lineage} = await execute('GetLineage', {lineage_id:personalLineageId});
        const duty = (lineage.function_blocks || []).find(block => block.duty?.holder_id === assistant && block.duty?.description === DUTY_DESCRIPTION);
        if (!duty) {
          await execute('CreateDuty', {lineage_id:personalLineageId,holder_id:assistant,
            description:DUTY_DESCRIPTION,category:'fixed',memory_level:'session',prompt:DUTY_PROMPT},
          {idempotencyKey:`workshop-assistant-duty:${assistant}`});
        } else if (duty.duty.memory?.prompt !== DUTY_PROMPT) {
          await execute('UpdateDuty',{lineage_id:personalLineageId,function_block_id:duty.id,prompt:DUTY_PROMPT,memory_level:'session'});
        }
        const {resident} = await execute('GetResident', {resident_id:assistant});
        workspaceId = (resident.workspaces || []).find(workspace => workspace.is_owned)?.workspace_id || '';
        if (!workspaceId) throw new Error(t('工坊助手没有可读取的工作区'));
        // Establish a baseline before this turn; an old draft must not replace this page on opening.
        try { const address = fileAddress(), previous = await execute('ReadResource', {address}); versions.set(address,previous.version || previous.text); }
        catch (error) { if (error.code !== 'instruction.not_found') throw error; }
        prepared = true;
        return {resident_id:assistant,lineage_id:personalLineageId};
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
        if (!workshop.assistantContext && (!value || !value.body || !value.kind || !value.title || !Array.isArray(value.parameters))) throw new Error(t('助手草稿不是完整模板对象'));
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
        context_text:`${workshop.assistantContext?.() || (templateId ? `模板：${templateId}` : '新模板，尚未保存')}\n草稿文件：${relativePath()}\n工作区文件地址：${fileAddress()}` };
    }
    function updateWarning() { element('workshopUnsaved').hidden = !workshop.dirty(); }
    root.addEventListener('workshop:draft-changed', updateWarning);
    element('workshopChatSave').addEventListener('click', () => workshop.save().catch(report));
    async function load() {
      const found = await discover();
      const emptyText = personalLineageId ? t('和工坊助手一起编辑模板') : t('当前用户没有 My Lineage，无法使用工坊助手');
      panel = window.PortalWorkbench.createPanel('chat', {
        root:element('workshopChat'),id:`templateWorkshopChat-${pageKey}`,autoLoad:false,
        refsScope:`templateWorkshopChat-${pageKey}`,draftOwner:() => window.PortalIdentity.username(),
        resolveResident:async () => {
          if (!found) return {binding_state:'unbound',chat_workspace:{schema:'portal.chat_workspace.v3',target:{kind:'workshop',id:owner},
            messages:[],runtime:{live:false,details:[]},composer:{can_send:!!personalLineageId,placeholder:emptyText}}};
          // Do not create anything merely by opening the page.
          const {resident} = await execute('GetResident', {resident_id:found});
          workspaceId = (resident.workspaces || []).find(workspace => workspace.is_owned)?.workspace_id || '';
          if (workspaceId) {
            try { const address = fileAddress(), previous = await execute('ReadResource', {address}); versions.set(address,previous.version || previous.text); }
            catch (error) { if (error.code !== 'instruction.not_found') throw error; }
          }
          return {resident_id:found,lineage_id:personalLineageId};
        },
        resolveBeforeSend:prepare,submissionContext:async () => {
          if (!prepared) await prepare();
          sentDraft = {address:fileAddress(),documentKey:workshop.documentKey()};
          return context();
        },onDetail:completed,
        emptyText,onError:report,
        onInteractionChange(open,source) {root.dispatchEvent(new CustomEvent('workshop:assistant-interaction',{detail:{open,source}}));},
      });
      await panel.openSurface({owner});
      const composer = element('workshopChat').querySelector('.composer');
      if (composer) composer.before(element('workshopUnsaved'));
    }
    return {load,prepare,readDraft,context,findAssistant,
      destroy() { disposed = true; root.removeEventListener('workshop:draft-changed',updateWarning); panel?.destroy(); },
    };
  }
  window.LineageTemplateWorkshopChat = Object.freeze({mount,findAssistant,DUTY_PROMPT,DUTY_DESCRIPTION});
})();
