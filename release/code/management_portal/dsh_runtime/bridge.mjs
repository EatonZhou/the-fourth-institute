/** Portal control plugin. Only public dsh services; the native loop owns execution. */
import net from 'node:net';
import localIpc from './local_ipc.cjs';
import { chmodSync, closeSync, fstatSync, openSync, readFileSync, statSync, unlinkSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { createHash } from 'node:crypto';
import { freezeMessage } from '@deepseek-ai/dsh-llm';
import { observeModelRequests } from './request_timing.mjs';

const dshVersion = JSON.parse(readFileSync(new URL('./node_modules/@deepseek-ai/dsh/package.json', import.meta.url), 'utf8')).version;

export const name = 'portal-control';
export const inject = ['agents', 'sessions', 'sessionPersistence', 'systemPrompt', 'tools', 'tokenMeter', 'compaction', 'terminals'];

const textOf = blocks => (blocks ?? []).filter(b => b.type === 'text').map(b => b.text).join('\n');
const operationFingerprint = params => fingerprint([params.prompt, params.model, params.effort, params.tools]);
const fingerprint = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
// A turn whose Worker left (a Worker handover) keeps running; its Portal tool
// calls wait this long for the next Worker before they fail.
const DETACHED_TOOL_WAIT_MS = 180_000;
// The delegation tool (``dsh-tool-subagent``) when the Resident allows subagents.
const SUBAGENT_TOOL = 'subagent';

// DSH's persistent ``bash`` can take a command for done from /proc (the shell
// is back reading its terminal) before it has read the command's last output:
// that result has no "[Command finished with exit code N]" line and lacks the
// output's tail, often all of it.  The shell's scrollback has the whole output
// a moment later, between the command's start and end marks.
const BASH_SETTLED = /\[Command finished with exit code \d+\]$|\[shell (?:exited|killed by signal)|^Your command timed out/;
const BASH_START = /__DSH_PERSISTENT_BASH_START_([0-9a-f-]{36})__/g;
const BASH_RECOVERY_WAIT_MS = 500;
const SCROLLBACK_PAGE_LINES = 1000;
// As much output as DSH's bash returns (its ``maxOutputChars``).
const BASH_MAX_OUTPUT_CHARS = 16000;

/**
 * The whole output of the last command in a persistent shell's scrollback, as
 * DSH renders a finished command; undefined while the command has not ended
 * or when the output DSH already returned is not this command's beginning.
 */
export function finishedBashOutput(scrollback, partial) {
  const start = [...scrollback.matchAll(BASH_START)].at(-1);
  if (!start) return undefined;
  const endMark = `__DSH_PERSISTENT_BASH_END_${start[1]}:`;
  const end = scrollback.indexOf(endMark, start.index);
  const status = end < 0 ? undefined : /^(\d+)\r?\n/.exec(scrollback.slice(end + endMark.length))?.[1];
  if (status === undefined) return undefined;
  const text = scrollback.slice(start.index + start[0].length, end).replace(/^\r?\n/, '').replace(/(?:\r?\n)+$/, '');
  if (!text.startsWith(partial)) return undefined;
  const marker = `[Command finished with exit code ${status}]`;
  if (!text) return marker;
  let shown = text;
  if (text.length > BASH_MAX_OUTPUT_CHARS) {
    const cut = /[\uD800-\uDBFF]/.test(text[BASH_MAX_OUTPUT_CHARS - 1]) ? BASH_MAX_OUTPUT_CHARS - 1 : BASH_MAX_OUTPUT_CHARS;
    shown = `${text.slice(0, cut)}<response clipped><NOTE>Only the beginning of this output is shown; search it with grep -n to read the rest.</NOTE>`;
  }
  return `${shown}\n${marker}`;
}

export function apply(ctx, config) {
  let handle, current, loading, admitted, compacting = false, toolDisposers = [], prompt = '', lastUsed = Date.now();
  const sessionId = config.sessionId;
  const pending = new Map();
  const contextWindows = new Map();
  const requestStarts = new Map();
  const messageTimings = new Map();
  const stopObservingRequests = observeModelRequests((id, startedAt) => requestStarts.set(id, startedAt));
  const connections = new Set();
  const events = () => handle.agent.session.snapshotEvents();
  const flush = () => ctx.sessions.flush(handle.agent.session);
  function notify(event) { current?.emit?.({type: 'event', event}); }
  // The Worker that follows the turn: its send function and socket.  Without
  // one the turn runs on; the next Worker attaches and gets what it missed.
  function attach(state, emit, socket) {
    clearTimeout(state.detachTimer);
    state.detachTimer = null;
    state.emit = emit;
    state.owner = socket;
    for (const event of events().filter(e => e.seq > state.sentSeq)) {
      const translated = translate(event, state.id);
      for (const item of translated) emit({type: 'event', event: item});
      state.sentSeq = event.seq;
    }
    for (const child of state.children.values()) sendChild(state, child);
    for (const call of pending.values()) emit({type: 'tool_call', ...call.call});
  }
  function detach(state) {
    state.emit = null;
    state.owner = null;
    clearTimeout(state.detachTimer);
    state.detachTimer = setTimeout(() => {
      if (state.owner || current !== state) return;
      for (const call of [...pending.values()]) call.fail('No Portal Worker took the tool call; outcome unknown');
    }, DETACHED_TOOL_WAIT_MS);
    state.detachTimer.unref?.();
  }
  // ``session`` is the turn's own session or a subagent's: a subagent's
  // system prompt is DSH's own, not Portal's input.
  function translate(event, turnId, session = handle.agent.session) {
    const data = event.data;
    const child = session.id !== sessionId;
    const params = {threadId: session.id, turnId};
    const item = {id: `${session.id}:${event.seq}`};
    const completed = item => ({method: 'item/completed', params: {...params, item}});
    if (event.type === 'request/context') {
      contextWindows.set(session.id, Number(data.contextWindow ?? 0));
      return [];
    }
    if (event.type === 'assistant/message') {
      if (data.interrupted) return [];
      const timing = messageTimings.get(item.id);
      if (timing) params.request_timing = timing;
      const result = [];
      if (!child && data.usage) {
        const input = (data.usage.inputTokens ?? 0) + (data.usage.cacheReadTokens ?? 0) + (data.usage.cacheWriteTokens ?? 0);
        result.push({method: 'thread/tokenUsage/updated', params: {...params, tokenUsage: {
          context: {inputTokens: input}, modelContextWindow: contextWindows.get(session.id) ?? 0,
        }}});
      }
      for (const [index, block] of (data.message.content ?? []).entries()) {
        if (block.type === 'reasoning') result.push(completed({
          id: `${item.id}:reasoning:${index}`, type: 'reasoning', content: [block.text ?? ''],
        }));
      }
      const text = textOf(data.message.content);
      if (text) result.push(completed({...item, type: 'agentMessage', text}));
      return result;
    }
    if (child && ['system/message', 'developer/message'].includes(event.type)) return [];
    if (['system/message', 'developer/message', 'user/message'].includes(event.type)) {
      const message = data.message ?? data;
      // Portal already records its own submissions and steering at acceptance.
      if (event.type === 'user/message' && String(message.id ?? '').startsWith('portal_')) return [];
      return [completed({...item, type: 'providerInput', role: message.role ?? event.type.split('/')[0], content: message.content})];
    }
    if (event.type === 'tool/call') {
      return [{method: 'item/started', params: {...params, item: {...item, id: data.callId, type: 'dynamicToolCall', tool: data.name, arguments: data.arguments}}}];
    }
    if (event.type === 'tool/result') {
      // Session format v4: the tool-role message is the result of one call.  A
      // replacement (a pruned or rewritten earlier result) is no new result.
      const result = data.message ?? {};
      if (!result.toolCallId || event.surfaceOp?.op === 'replace') return [];
      const call = session.snapshotEvents().find(e => e.type === 'tool/call' && e.data.callId === result.toolCallId);
      return [completed({...item, id: result.toolCallId, type: 'dynamicToolCall',
        tool: call?.data.name ?? '', arguments: call?.data.arguments ?? '{}', status: result.isError ? 'failed' : 'completed',
        success: !result.isError, output: result.content})];
    }
    return [];
  }
  // DSH's write tool publishes a new file from its private 0600 staging file.
  // A file the agent creates gets the mode an ordinary create gets in its
  // directory (umask or default ACL), as with every other Provider: the
  // workspaces are shared.
  function shareCreatedFile(event, session) {
    const data = event.data ?? {};
    if (data.meta?.operation !== 'create' || data.message?.isError) return;
    const call = session.snapshotEvents().find(e => e.type === 'tool/call' && e.data.callId === data.message?.toolCallId);
    if (call?.data.name !== 'write') return;
    try {
      const args = typeof call.data.arguments === 'string' ? JSON.parse(call.data.arguments) : call.data.arguments;
      const path = resolve(config.cwd, String(args?.file_path ?? ''));
      if ((statSync(path).mode & 0o777) !== 0o600) return;
      const probe = join(dirname(path), `.portal-create-mode-${process.pid}-${event.seq}`);
      const fd = openSync(probe, 'wx', 0o666);
      let mode;
      try { mode = fstatSync(fd).mode & 0o777; } finally { closeSync(fd); unlinkSync(probe); }
      if (mode !== 0o600) chmodSync(path, mode);
    } catch { /* The file is gone or its directory is not writable: it stays as written. */ }
  }
  // DSH runs a subagent (its ``subagent`` tool) in a session of its own,
  // inside the tool call.  The child's items go to the Worker as
  // ``subagent/event`` under that call.  The child names its task
  // (``subagent/descriptor`` label) with the call's ``description``: that
  // links it to the call; its items before the link follow it then.
  function subagentCall(event) {
    if (event.type !== 'tool/call' || event.data.name !== SUBAGENT_TOOL) return null;
    let args = event.data.arguments;
    try { if (typeof args === 'string') args = JSON.parse(args); } catch { args = {}; }
    return {callId: event.data.callId, label: String(args?.description ?? '')};
  }
  function followChild(state, session, event) {
    let child = state.children.get(session.id);
    if (!child) state.children.set(session.id, child = {session, callId: '', sentSeq: -1, model: ''});
    if (!child.callId && event.type === 'subagent/descriptor') {
      const label = String(event.data?.label ?? '');
      const index = state.subagentCalls.findIndex(call => call.label === label);
      const call = state.subagentCalls.splice(index >= 0 ? index : 0, 1)[0];
      if (call) child.callId = call.callId;
      // Asked to stop before its session named its task.
      if (call && state.stopping.has(call.callId)) queueMicrotask(() => ctx.agents.get(session.id)?.cancel({kind: 'parent'}));
    }
    sendChild(state, child);
  }
  // One subagent stops as its parent would stop it: the call returns
  // "cancelled" with the child's partial answer and the turn goes on.  The
  // Worker hears ``subagent/stopping`` first, so the call's card ends stopped.
  function stopSubagent(state, callId) {
    const child = [...state.children.values()].find(each => each.callId === callId);
    if (!child && !state.subagentCalls.some(call => call.callId === callId)) {
      return {stopped: false, reason: state.endedCalls.has(callId) ? 'subagent_finished' : 'unknown_subagent'};
    }
    if (state.endedCalls.has(callId)) return {stopped: false, reason: 'subagent_finished'};
    if (child) {
      const agent = ctx.agents.get(child.session.id);
      if (agent?.status !== 'running') return {stopped: false, reason: 'subagent_finished'};
      agent.cancel({kind: 'parent'});
    } else state.stopping.add(callId);
    notify({method: 'subagent/stopping', params: {threadId: sessionId, turnId: state.id, toolUseId: callId}});
    return {stopped: true};
  }
  function sendChild(state, child) {
    if (!child.callId || !state.emit) return;
    let log;
    try { log = child.session.snapshotEvents(); } catch { return; /* The child session was disposed. */ }
    for (const event of log.filter(e => e.seq > child.sentSeq)) {
      if (event.type === 'request/context') child.model = String(event.data?.model ?? child.model);
      const items = translate(event, state.id, child.session);
      // Each request's token use, for the subagent's card (the turn's usage
      // is read from the session logs, the child's included).
      const usage = event.type === 'assistant/message' && !event.data?.interrupted ? event.data?.usage : null;
      if (usage) {
        const cached = usage.cacheReadTokens ?? 0;
        const input = (usage.inputTokens ?? 0) + cached + (usage.cacheWriteTokens ?? 0);
        const output = usage.outputTokens ?? 0;
        items.push({method: 'thread/tokenUsage/updated', params: {threadId: child.session.id, turnId: state.id, tokenUsage: {last: {
          inputTokens: input, cachedInputTokens: cached, outputTokens: output,
          reasoningOutputTokens: usage.reasoningTokens ?? 0, totalTokens: input + output}}}});
      }
      for (const item of items) state.emit({type: 'event', event: {method: 'subagent/event', params: {
        traceKey: child.session.id, agentId: child.session.id, parentToolUseId: child.callId, ancestorTraceKeys: [],
        ...(child.model ? {model: child.model} : {}), event: item}}});
      child.sentSeq = event.seq;
    }
  }
  ctx.on('session/event', (session, event) => {
    if (!current) return;
    if (event.type === 'assistant/message') {
      const startedAt = requestStarts.get(session.id);
      requestStarts.delete(session.id);
      if (startedAt && !event.data.interrupted) {
        messageTimings.set(`${session.id}:${event.seq}`, {
          started_at: startedAt, finished_at: new Date().toISOString(),
        });
      }
    }
    if (session.id !== sessionId) {
      if (session.header?.parentSession !== sessionId || session.header?.origin !== 'subagent') return;
      if (event.type === 'tool/result') shareCreatedFile(event, session);
      followChild(current, session, event);
      return;
    }
    // Input that arrives after the turn closed would start a turn of its own.
    if (event.type === 'turn/end') current.closed = true;
    if (event.type === 'tool/result') shareCreatedFile(event, session);
    const call = subagentCall(event);
    if (call) current.subagentCalls.push(call);
    if (event.type === 'tool/result' && event.data.message?.toolCallId) current.endedCalls.add(event.data.message.toolCallId);
    if (!current.emit) return;
    const translated = translate(event, current.id, session);
    for (const item of translated) notify(item);
    current.sentSeq = event.seq;
  });
  // A bash result that ended without its exit code is read again from the shell (see finishedBashOutput).
  ctx.on('tools/post-execute', async (exec, result, next) => {
    const decision = await next();
    if (exec.name !== 'bash' || exec.parent !== undefined || exec.signal?.aborted || result.isError
      || decision.kind !== 'accept' || Object.hasOwn(decision, 'value')) return decision;
    const partial = textOf(decision.content ?? result.content);
    if (BASH_SETTLED.test(partial)) return decision;
    try {
      const output = await recoverBashOutput(exec.agent, partial);
      return output === undefined ? decision : {...decision, content: [{type: 'text', text: output}]};
    } catch (error) {
      console.error(`bash output recovery skipped: ${error.message}`);
      return decision;
    }
  });
  async function recoverBashOutput(owner, partial) {
    for (let waited = 0; waited < BASH_RECOVERY_WAIT_MS; waited += 25) {
      await new Promise(done => setTimeout(done, 25));
      for (const terminal of ctx.terminals.list(owner)) {
        if (terminal.status?.kind === 'exited') continue;
        const output = finishedBashOutput(scrollbackOf(owner, terminal.sessionId), partial);
        if (output !== undefined) return output;
      }
    }
    return undefined;
  }
  // A shell's scrollback back to the last command's start mark (newest page first, as DSH reads it).
  function scrollbackOf(owner, id) {
    let page = ctx.terminals.read(owner, id, {offset: 0, count: SCROLLBACK_PAGE_LINES});
    const pages = [page.text];
    while (!pages[0].includes('__DSH_PERSISTENT_BASH_START_') && page.lineEnd < page.totalLines && pages.length < 50) {
      page = ctx.terminals.read(owner, id, {offset: page.lineEnd, count: SCROLLBACK_PAGE_LINES});
      pages.unshift(page.text);
    }
    return pages.filter(Boolean).join('\n');
  }
  ctx.on('agent/assistant-stream', ({agent, frame}) => {
    if (agent.session.id !== sessionId || frame.type !== 'chunk' || !current) return;
    const chunk = frame.chunk;
    if (chunk.type === 'text-delta') notify({method: 'item/agentMessage/delta', params: {
      threadId: sessionId, turnId: current.id, itemId: frame.attemptId, delta: chunk.text ?? chunk.delta ?? ''}});
  });
  async function load(options) {
    if (handle) return handle;
    if (loading) return loading;
    loading = (async () => {
      const setup = (agentCtx) => {
        agentCtx.on('agent/pre-step', (_payload, next) => {
          if (!current) return {kind: 'reject'};
          return next();
        });
        agentCtx.systemPrompt.variable('portal_instructions', () => prompt);
        agentCtx.systemPrompt.section({name: 'portal', order: 0, complete: true, text: '{{portal_instructions}}'});
      };
      const agentOptions = {provider: 'deepseek-official', model: options.model,
        ...(options.effort ? {reasoningEffort: options.effort} : {})};
      const exists = await ctx.sessionPersistence.stat(sessionId);
      handle = exists
        ? await ctx.agents.resume({resumeSessionId: sessionId, agentOptions, setup})
        : await ctx.agents.create({sessionId, meta: {cwd: config.cwd}, agentOptions, setup});
      prompt = textOf(handle.agent.session.deriveMessages().find(message => message.role === 'system')?.content);
      return handle;
    })();
    try { return await loading; } finally { loading = null; }
  }
  function registerTools(tools) {
    for (const dispose of toolDisposers) dispose();
    toolDisposers = [];
    for (const tool of tools) {
      toolDisposers.push(handle.agent.ctx.tools.register({
        name: tool.name, description: tool.description, parameters: tool.inputSchema,
        output: {schema: {type: 'object'}, render: (_args, result) => result.content},
        async execute(args, exec) {
          const callId = exec.callId;
          const result = await new Promise((resolve, reject) => {
            const fail = message => { exec.signal.removeEventListener('abort', abort); pending.delete(callId); reject(new Error(message)); };
            const abort = () => fail('Portal tool callback interrupted; outcome may be unknown');
            if (exec.signal.aborted) return abort();
            exec.signal.addEventListener('abort', abort, {once: true});
            const call = {call_id: callId, tool: tool.name, arguments: args};
            pending.set(callId, {call, fail, resolve: value => {exec.signal.removeEventListener('abort', abort); resolve(value);}});
            // Without a Worker the call waits for the next one (``attach``).
            current.emit?.({type: 'tool_call', ...call});
          });
          if (result.isError) throw new Error(textOf(result.content));
          return result;
        },
      }));
    }
  }
  // The events of the Portal operation that starts after `start`: up to the
  // next operation's prompt.  Steering continues the running turn; only one
  // that races the turn's close starts another turn, which belongs to the
  // same operation, so its result is the last turn's.
  function operationEvents(start, prefix) {
    const interval = events().filter(e => e.seq > start.seq);
    const next = interval.find(e => e.type === 'agent/inbox/spliced'
      && e.data.inserted?.some(m => /^portal_[0-9a-f]{24}_/.test(m.id) && !m.id.startsWith(prefix)));
    return next ? interval.filter(e => e.seq < next.seq) : interval;
  }
  function resultFrom(start, id) {
    const interval = operationEvents(start, `portal_${fingerprint(id).slice(0, 24)}_`);
    const end = interval.filter(e => e.type === 'turn/end').at(-1);
    if (!end || end.data.reason.kind !== 'completed') {
      const detail = end?.data.reason.error?.message ?? end?.data.reason.kind ?? 'outcome unknown';
      throw new Error(`DSH operation ${id} did not complete: ${detail}. It will not be resubmitted automatically.`);
    }
    const messages = interval.filter(e => e.type === 'assistant/message' && e.seq < end.seq);
    const usage = {input_tokens: 0, output_tokens: 0, total_tokens: 0, cached_input_tokens: 0};
    for (const event of messages) {
      const u = event.data.usage ?? {};
      usage.input_tokens += (u.inputTokens ?? 0) + (u.cacheReadTokens ?? 0);
      usage.output_tokens += u.outputTokens ?? 0;
      usage.total_tokens += u.totalTokens ?? (u.inputTokens ?? 0) + (u.cacheReadTokens ?? 0) + (u.outputTokens ?? 0);
      usage.cached_input_tokens += u.cacheReadTokens ?? 0;
    }
    return {session_id: sessionId, turn_id: id, response: textOf(messages.at(-1)?.data.message.content), usage};
  }
  function assertSession(params) {
    if (params.session_id && params.session_id !== sessionId) throw new Error('Session identity mismatch');
    if (params.cwd !== config.cwd || params.provider_profile !== config.route) throw new Error('Session route/cwd changes require migration');
    if (fingerprint(params.resident_environment ?? {}) !== config.identityFingerprint) throw new Error('Resident identity mismatch');
  }
  // ``admission`` names the Worker that follows the operation (``emit``, ``socket``).
  async function run(params, admission) {
    assertSession(params);
    if (!params.is_owner || !['owner', 'danger-full-access'].includes(params.permission_profile)) throw new Error('DSH profile requires an authorized full-access execution');
    const id = params.turn_id;
    if (!id) throw new Error('Stable turn ID is required');
    if (current) {
      if (current.id !== id) throw new Error('Session has another active operation');
      attach(current, admission.emit, admission.socket);
      return current.promise;
    }
    await load(params);
    const signature = operationFingerprint(params);
    const prefix = `portal_${fingerprint(id).slice(0, 24)}_`;
    const messageId = `${prefix}${signature}_${fingerprint(params.system_prompt_append || prompt)}`;
    // The messages of this operation: its input, then the continuations of
    // an attempt that stopped before its turn completed.
    const attempts = events().filter(e => e.type === 'agent/inbox/spliced' && e.data.inserted?.some(m => m.id.startsWith(prefix)));
    let text = params.prompt, inputId = messageId;
    // A turn an earlier attempt started (``continue_prompt``) that this
    // process never took: its input goes out for the first time.
    let recovered = params.continue_prompt ? {delivery: 'first_delivery'} : null;
    if (attempts.length) {
      const start = {seq: attempts[0].seq};
      const end = operationEvents({seq: attempts.at(-1).seq}, prefix).filter(e => e.type === 'turn/end').at(-1);
      if (end?.data.reason.kind === 'completed') {
        // The operation completed while no Worker followed it: return its result.
        const original = attempts[0].data.inserted.find(m => m.id.startsWith(prefix));
        if (!params.continue_prompt && (!original.id.startsWith(`${prefix}${signature}_`)
          || (params.system_prompt_append && original.id !== messageId))) throw new Error('Operation ID reused with different input');
        const result = resultFrom(start, id);
        const emit = admission.emit;
        emit({type: 'event', event: {method: 'turn/started', params: {threadId: sessionId, turn: {id}}}});
        for (const event of events().filter(e => e.seq > start.seq && e.seq <= end.seq)) {
          const translated = translate(event, id);
          for (const item of translated) emit({type: 'event', event: item});
        }
        emit({type: 'event', event: {method: 'turn/completed', params: {threadId: sessionId, turn: {id, status: 'completed'}}}});
        return result;
      }
      // Interrupted, or the process stopped inside the turn: the agent has the
      // input already, so the turn is continued; the input is never sent twice.
      text = params.continue_prompt || 'continue';
      inputId = `${prefix}continue${attempts.length}`;
      recovered = {delivery: 'continued'};
    }
    if (params.system_prompt_append) prompt = params.system_prompt_append;
    // Options are Agent-owned mutable runtime settings in the pinned public API.
    Object.assign(handle.agent.options, {model: params.model, reasoningEffort: params.effort || 'high'});
    registerTools(params.tools ?? []);
    const start = {seq: handle.agent.session.seq - 1};
    requestStarts.clear();
    messageTimings.clear();
    const state = {id, emit: admission.emit, owner: admission.socket, sentSeq: start.seq, detachTimer: null, startSeq: start.seq,
      steered: new Set(), closed: false, subagentCalls: [], children: new Map(), stopping: new Set(), endedCalls: new Set()};
    current = state;
    // The Worker may have left while the session loaded.
    if (admission.socket?.destroyed) detach(state);
    state.promise = (async () => {
      notify({method: 'turn/started', params: {threadId: sessionId, turn: {id}}});
      if (recovered) notify({method: 'turn/recovered', params: {threadId: sessionId, turnId: id, ...recovered, prompt: text}});
      handle.agent.followup(freezeMessage({id: inputId, role: 'user', source: {kind: 'user'}, content: [{type: 'text', text}]}));
      await handle.agent.whenIdle();
      state.closed = true;
      await flush();
      const result = resultFrom(start, id);
      notify({method: 'turn/completed', params: {threadId: sessionId, turn: {id, status: 'completed'}}});
      return result;
    })();
    try { return await state.promise; } finally { clearTimeout(state.detachTimer); current = null; lastUsed = Date.now(); }
  }
  const server = net.createServer(socket => {
    connections.add(socket);
    let buffer = '';
    socket.on('error', () => {});
    socket.on('close', () => {
      connections.delete(socket);
      // A Worker that leaves (a handover) leaves the turn running.
      if (current?.owner === socket) detach(current);
    });
    socket.on('data', data => {
      buffer += data.toString();
      if (buffer.length > 8 * 1024 * 1024) return socket.destroy();
      let position;
      while ((position = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, position); buffer = buffer.slice(position + 1);
        let message;
        try { message = JSON.parse(line); } catch { socket.destroy(); return; }
        const send = body => {if (!socket.destroyed) socket.write(JSON.stringify({id: message.id, ...body}) + '\n');};
        if (message.type === 'tool_result') {
          const call = pending.get(message.call_id); pending.delete(message.call_id); call?.resolve(message.result); continue;
        }
        (async () => {
          const p = message.params ?? {};
          if (message.method === 'health') return {ready: true, protocol: 'tfi.native-agent.v1', version: dshVersion, capabilities: {turn_steer: true, session_compact: true, turn_reattach: true, turn_continue: true}};
          if (message.method === 'turn/run') {
            if (compacting) throw new Error('Session is compacting');
            if (admitted) {
              // The next Worker of a running turn attaches to it under its turn ID.
              if (admitted.id !== p.turn_id) throw new Error('Session has another admitted request');
              if (current?.id === p.turn_id) attach(current, send, socket);
              else Object.assign(admitted, {emit: send, socket});
              return await admitted.promise;
            }
            const admission = {id: p.turn_id, emit: send, socket};
            admitted = admission;
            admission.promise = run(p, admission);
            try {return await admission.promise;} finally {admitted = null;}
          }
          if (message.method === 'turn/steer') {
            // Input for the running turn: the agent takes it at its next step.
            const active = current && current.id === p.turn_id && (!p.session_id || p.session_id === sessionId);
            if (!active) return {steered: false, reason: 'no_active_turn'};
            if (current.closed || handle?.agent.status !== 'running') return {steered: false, reason: 'turn_closing'};
            const text = typeof p.text === 'string' ? p.text : '';
            if (!text.trim()) throw new Error('Steering needs text');
            const steerId = `portal_steer_${fingerprint([p.turn_id, p.control_id || text]).slice(0, 32)}`;
            if (current.steered.has(steerId)) return {steered: true, duplicate: true};
            current.steered.add(steerId);
            handle.agent.steer(freezeMessage({id: steerId, role: 'user', source: {kind: 'user'}, content: [{type: 'text', text}]}));
            return {steered: true};
          }
          if (message.method === 'subagent/stop') {
            const active = current && !current.closed && (!p.session_id || p.session_id === sessionId);
            if (!active) return {stopped: false, reason: 'no_active_turn'};
            return stopSubagent(current, String(p.tool_use_id ?? ''));
          }
          if (message.method === 'turn/interrupt') {
            const matches = current && current.id === p.turn_id && (!p.session_id || p.session_id === sessionId);
            if (matches) handle.agent.cancel({kind: 'user'});
            return {interrupted: Boolean(matches)};
          }
          if (message.method === 'session/compact') {
            // DSH's own on-demand compaction (what its /compact command runs):
            // an older span becomes one summary, the recent history stays.
            assertSession(p);
            if (current || admitted || compacting) return {compacted: false, reason: 'busy'};
            if (!(await ctx.sessionPersistence.stat(sessionId))) return {compacted: false, reason: 'no_session'};
            compacting = true;
            try {
              await load(p);
              let result;
              try {
                result = await ctx.compaction.compactNow(handle.agent, AbortSignal.timeout(570_000));
              } catch (error) {
                if (error?.name !== 'ManualCompactionError') throw error;
                if (error.code === 'busy') return {compacted: false, reason: 'busy'};
                // A short history whose summary would not be smaller stays as it is.
                if (error.code === 'summary' && /not smaller/.test(String(error.cause?.message ?? ''))) {
                  return {compacted: false, reason: 'summary_not_smaller', session_id: sessionId};
                }
                throw new Error(`${error.message}: ${error.cause?.message ?? error.code}`);
              }
              const contextTokens = ctx.tokenMeter.measure(handle.agent.session).totalTokens;
              if (!result) return {compacted: false, reason: 'nothing_to_compact', session_id: sessionId, context_tokens: contextTokens};
              return {compacted: true, session_id: sessionId, shadowed_items: result.shadowedSeqs.length,
                shadowed_tokens: result.shadowedTokenCount, summary_seq: result.summarySeq, context_tokens: contextTokens};
            } finally { compacting = false; lastUsed = Date.now(); }
          }
          if (message.method === 'session/close') {
            if (current || compacting) throw new Error('Cannot release an active session');
            if (handle) {await flush(); await handle.dispose(); handle = null;}
            setTimeout(() => process.exit(0), 100).unref();
            return {closed: true};
          }
          throw new Error(`Unsupported native operation: ${message.method}`);
        })().then(result => send({type: 'result', result}), error => send({type: 'error', error: {message: error.message}}));
      }
    });
  });
  server.on('error', error => {console.error(error.message); process.exit(1);});
  localIpc.listenLocal(server, config.socketPath, {python:config.pythonExecutable});
  const idle = setInterval(() => {if (!current && !compacting && Date.now() - lastUsed > 300_000) process.exit(0);}, 15_000);
  ctx.on('dispose', () => {stopObservingRequests(); clearInterval(idle); server.close(); for (const socket of connections) socket.destroy();});
}
