// Diagnostics must not serialize Playwright call logs: they can contain cookies.
const fs = require('fs');
const path = require('path');
const { createHash } = require('crypto');

const names = new Set(['Error', 'TypeError', 'ReferenceError', 'SyntaxError', 'RangeError', 'URIError', 'EvalError', 'AggregateError', 'TimeoutError', 'AbortError']);
const networkCodes = ['ECONNREFUSED', 'ECONNRESET', 'ETIMEDOUT', 'ENOTFOUND', 'EAI_AGAIN', 'EPIPE'];
const staticRoot = path.resolve(__dirname, '../../management_portal/static');
const sources = new Set();
function indexSources(dir, prefix = '/static/') {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.isDirectory()) indexSources(path.join(dir, entry.name), `${prefix}${entry.name}/`);
    else if (entry.isFile() && entry.name.endsWith('.js')) sources.add(prefix + entry.name);
  }
}
indexSources(staticRoot);

function safeError(error) {
  const message = typeof error?.message === 'string' ? error.message : '';
  const stack = typeof error?.stack === 'string' ? error.stack : '';
  const frames = [];
  // Only V8 location frames, never the message, function names or call log.
  for (const line of stack.split('\n').slice(1, 33)) {
    if (!/^\s*at\s/.test(line)) continue;
    const anonymous = line.match(/<anonymous>:(\d+):(\d+)\)?$/);
    if (anonymous) {
      frames.push({ source: '[anonymous]', source_hash: null, line: Number(anonymous[1]), column: Number(anonymous[2]) });
      continue;
    }
    const match = line.match(/(https?:\/\/[^\s)]+):(\d+):(\d+)\)?$/);
    if (!match) continue;
    try {
      const pathname = new URL(match[1]).pathname;
      frames.push({ source: sources.has(pathname) ? pathname : '[source]',
        source_hash: createHash('sha256').update(pathname).digest('hex'),
        line: Number(match[2]), column: Number(match[3]) });
    } catch { /* No verifiable URL location. */ }
  }
  return { name: names.has(error?.name) ? error.name : 'Error',
    network_code: networkCodes.find(code => new RegExp(`\\b${code}\\b`).test(message)) || null,
    stack_frames: frames, details_omitted: true };
}

async function safeTransport(operation, invoke) {
  try { return await invoke(); }
  catch (error) {
    const diagnostic = safeError(error);
    // A new Error deliberately has no original cause or library-owned fields.
    throw Object.assign(new Error(`TAU ${operation} transport failed: ${diagnostic.network_code || diagnostic.name}`),
      { code: 'tau.transport_failed', diagnostic });
  }
}

module.exports = { safeError, safeTransport, errorSourcePaths: [...sources] };
