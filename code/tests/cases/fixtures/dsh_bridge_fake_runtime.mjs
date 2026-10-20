// A stand-in for the DSH services the Portal bridge (management_portal/dsh_runtime/bridge.mjs)
// uses, so a test drives the bridge over its socket without a model.
//
//   node dsh_bridge_fake_runtime.mjs <bridge.mjs> <config.json>
//
// config: {socketPath, sessionId, cwd, route, identityFingerprint, earlier?: {turn_id, end}}
// ``earlier`` puts an attempt of ``turn_id`` in the session log first, ended
// "completed", "aborted", or not at all ("none": the process stopped inside it).
//
// The agent answers every input with "done:<text>:<inputs this process took>";
// an input "use tool" first calls the turn's first Portal tool and waits for it;
// "use subagent" first delegates to a subagent, which runs a command in a
// session of its own inside the ``subagent`` call (as dsh-tool-subagent does);
// "use bash" runs a bash call whose result DSH settled before the command's
// output reached the shell's scrollback; "use slow subagent" delegates to a
// subagent that works until it is cancelled (the call then fails as
// dsh-tool-subagent's does).
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';

const [bridgePath, configPath] = process.argv.slice(2);
const config = JSON.parse(readFileSync(configPath, 'utf8'));
const { apply } = await import(bridgePath);

const listeners = {};
const on = (name, handler) => { (listeners[name] ??= []).push(handler); };
const fire = (name, ...args) => { for (const handler of listeners[name] ?? []) handler(...args); };
// A cordis waterfall: each handler gets the next one's decision from ``next``.
const waterfall = (name, ...args) => {
  const handlers = listeners[name] ?? [];
  const run = index => index < handlers.length ? handlers[index](...args, () => run(index + 1)) : Promise.resolve({ kind: 'accept' });
  return run(0);
};

function makeSession(id, header = {}) {
  const log = [];
  const session = {
    id,
    header: { id, ...header },
    log,
    get seq() { return log.length; },
    snapshotEvents: () => log.slice(),
    deriveMessages: () => [],
    surface: { nodes: [] },
    eventAt: seq => log.find(event => event.seq === seq),
    deriveEventMessage: () => null,
    append(type, data) {
      const event = { seq: log.length, type, data };
      log.push(event);
      fire('session/event', session, event);
      return event;
    },
  };
  return session;
}
const session = makeSession(config.sessionId);
const log = session.log;
const prefixOf = id => `portal_${createHash('sha256').update(JSON.stringify(id)).digest('hex').slice(0, 24)}_`;
if (config.earlier) {
  const { turn_id: turnId, end } = config.earlier;
  log.push({ seq: log.length, type: 'agent/inbox/spliced', data: { inserted: [{ id: `${prefixOf(turnId)}earlier_input`, role: 'user' }] } });
  if (end === 'completed') {
    log.push({ seq: log.length, type: 'assistant/message', data: { message: { content: [{ type: 'text', text: 'earlier answer' }] }, usage: {} } });
  }
  if (end !== 'none') log.push({ seq: log.length, type: 'turn/end', data: { reason: { kind: end } } });
}

const tools = [];
// The live agents besides the turn's own (``ctx.agents.get``): its subagents.
const liveAgents = new Map();
// The agent's persistent shell (dsh-terminal), as the bash tool leaves it.
const shell = { sessionId: 'pty-1', status: { kind: 'running' }, text: '' };
let inputs = 0;
let running = Promise.resolve();
let controller = null;
const agent = {
  session,
  options: {},
  status: 'idle',
  ctx: { tools: { register(tool) { tools.push(tool); return () => tools.splice(tools.indexOf(tool), 1); } } },
  followup(message) {
    inputs += 1;
    const taken = inputs;
    controller = new AbortController();
    const signal = controller.signal;
    agent.status = 'running';
    session.append('agent/inbox/spliced', { inserted: [message] });
    const text = message.content[0].text;
    running = (async () => {
      try {
        if (text === 'use tool') {
          const callId = `call-${taken}`;
          session.append('tool/call', { callId, name: tools[0].name, arguments: '{}' });
          const result = await tools[0].execute({}, { callId, signal });
          // Session format v4, as the pinned runtime writes it: the tool-role message is the result.
          session.append('tool/result', { message: { role: 'tool', toolCallId: callId, isError: false, content: result.content } });
        }
        if (text === 'use bash') {
          const callId = `call-${taken}-bash`;
          session.append('tool/call', { callId, name: 'bash', arguments: JSON.stringify({ command: 'sleep 1; date +%T' }) });
          const nonce = '0b5e5a4c-1d2e-4f3a-9b8c-7d6e5f4a3b2c';
          shell.text = `dsh> __DSH_PERSISTENT_BASH_START_${nonce}__\n`;
          setTimeout(() => { shell.text += `12:34:56\n__DSH_PERSISTENT_BASH_END_${nonce}:0\ndsh> `; }, 60);
          const result = { content: [{ type: 'text', text: '' }], isError: false };
          const decision = await waterfall('tools/post-execute', { name: 'bash', callId, agent, signal }, result);
          session.append('tool/result', { message: { role: 'tool', toolCallId: callId, isError: false, content: decision.content ?? result.content } });
        }
        if (text === 'use subagent') {
          const callId = `call-${taken}-subagent`;
          session.append('tool/call', { callId, name: 'subagent', arguments: JSON.stringify({ description: 'Count files', prompt: 'How many files?' }) });
          const child = makeSession('5f0c-child', { parentSession: config.sessionId, origin: 'subagent', delegationDepth: 1 });
          child.append('agent/inbox/spliced', { inserted: [{ id: 'child-input', role: 'user' }] });
          child.append('subagent/descriptor', { version: 3, mode: 'one-shot', provider: 'spawn', label: 'Count files' });
          session.append('subagent/catalog', { version: 0, childId: child.id, mode: 'one-shot', label: 'Count files' });
          child.append('system/message', { message: { role: 'system', content: [{ type: 'text', text: 'DSH rules' }] } });
          child.append('user/message', { message: { role: 'user', id: 'child-input', content: [{ type: 'text', text: 'How many files?' }] } });
          child.append('request/context', { provider: 'deepseek-official', model: 'deepseek-v4-flash', contextWindow: 1000000 });
          child.append('assistant/message', { message: { content: [{ type: 'text', text: 'Counting.' }] }, usage: { inputTokens: 10, outputTokens: 2 } });
          child.append('tool/call', { callId: 'child-call-1', name: 'bash', arguments: JSON.stringify({ command: 'ls | wc -l' }) });
          child.append('tool/result', { message: { role: 'tool', toolCallId: 'child-call-1', isError: false, content: [{ type: 'text', text: '3' }] } });
          child.append('assistant/message', { message: { content: [{ type: 'text', text: 'There are 3 files.' }] }, usage: { inputTokens: 20, cacheReadTokens: 30, outputTokens: 5 } });
          child.append('turn/end', { reason: { kind: 'completed' } });
          session.append('tool/result', { message: { role: 'tool', toolCallId: callId, isError: false, content: [{ type: 'text', text: 'There are 3 files.' }] } });
        }
        if (text === 'use slow subagent') {
          const callId = `call-${taken}-subagent`;
          session.append('tool/call', { callId, name: 'subagent', arguments: JSON.stringify({ description: 'Watch the log', prompt: 'Tail the log.' }) });
          const child = makeSession('7a1d-child', { parentSession: config.sessionId, origin: 'subagent', delegationDepth: 1 });
          let cancelled;
          const stop = new Promise(resolve => { cancelled = resolve; });
          liveAgents.set(child.id, { session: child, status: 'running', cancel(reason) { this.status = 'idle'; cancelled(reason); } });
          child.append('agent/inbox/spliced', { inserted: [{ id: 'child-input', role: 'user' }] });
          child.append('subagent/descriptor', { version: 3, mode: 'one-shot', provider: 'spawn', label: 'Watch the log' });
          child.append('user/message', { message: { role: 'user', id: 'child-input', content: [{ type: 'text', text: 'Tail the log.' }] } });
          child.append('assistant/message', { message: { content: [{ type: 'text', text: 'Watching.' }] }, usage: { inputTokens: 10, outputTokens: 2 } });
          const reason = await stop;
          child.append('turn/end', { reason: { kind: 'aborted', by: reason } });
          liveAgents.delete(child.id);
          session.append('tool/result', { message: { role: 'tool', toolCallId: callId, isError: true, content: [
            { type: 'text', text: 'subagent run was cancelled\nPartial output before the run ended:\nWatching.' }] } });
          // A step the stopped child settles after its call returned.
          child.append('assistant/message', { message: { content: [{ type: 'text', text: 'Late words.' }] }, usage: { inputTokens: 5, outputTokens: 1 } });
        }
        session.append('request/context', { contextWindow: 1000000 });
        session.append('assistant/message', { message: { content: [{ type: 'text', text: `done:${text}:${taken}` }] }, usage: { inputTokens: 20, cacheReadTokens: 30, cacheWriteTokens: 5, outputTokens: 10 } });
        session.append('turn/end', { reason: { kind: 'completed' } });
      } catch (error) {
        session.append('turn/end', { reason: { kind: 'aborted', error: { message: error.message } } });
      } finally {
        agent.status = 'idle';
      }
    })();
  },
  whenIdle: () => running,
  cancel() { controller?.abort(); },
  steer() {},
};
const handle = { agent, dispose: async () => {} };
const lines = text => text.split('\n').length;
const ctx = {
  on,
  terminals: {
    list: owner => (owner === agent ? [shell] : []),
    read: (_owner, _id, { offset = 0 } = {}) => ({ text: offset === 0 ? shell.text : '', lineEnd: lines(shell.text), totalLines: lines(shell.text) }),
  },
  sessions: { flush: async () => {} },
  sessionPersistence: { stat: async () => log.length > 0 },
  agents: {
    get: id => liveAgents.get(id),
    create: async ({ setup }) => { setup({ on() {}, systemPrompt: { variable() {}, section() {} } }); return handle; },
    resume: async ({ setup }) => { setup({ on() {}, systemPrompt: { variable() {}, section() {} } }); return handle; },
  },
};
apply(ctx, config);
console.log('ready');
