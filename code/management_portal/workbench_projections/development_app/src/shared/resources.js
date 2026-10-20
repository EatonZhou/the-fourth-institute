export const resourceAddress = ref => String(ref).replace(/#L\d+(?:-L\d+)?$/, '');

// Preserve the server's reason. Unreadable alone does not prove a denial.
export function requireReadable(result, ref = result?.ref || '') {
  if (result?.readable === true) return result;
  const code = result?.error?.code || 'resource_unavailable';
  const title = ({ permission_denied: '无权读取来源', file_not_found: '来源文件不存在',
    resource_not_found: '来源资源不存在', 'instruction.not_found': '来源资源不存在' })[code] || '来源暂不可用';
  throw Object.assign(new Error(ref ? `${title}：${ref}` : title), {
    code, reason: code, address: ref, status: result?.status, cause: result?.error,
  });
}

export function createResources(sdk, { signal } = {}) {
  // One region owns its transport budget. Only unfinished identical requests
  // are shared: settled permissions are never a cache for a later operation.
  const pending = new Map(), queue = [];
  let running = 0, scheduled = false;
  const aborted = () => new DOMException('Region disposed', 'AbortError');
  const unavailable = code => ['permission_denied', 'file_not_found', 'resource_not_found'].includes(code);
  function schedule() {
    if (scheduled || signal?.aborted) return;
    scheduled = true; queueMicrotask(() => { scheduled = false; pump(); });
  }
  const itemError = item => Object.assign(new Error(item.error?.error || item.error?.message || 'Resource batch item failed'), item.error, { status: item.status });
  function pump() {
    while (!signal?.aborted && running < 4 && queue.length) {
      const first = queue.shift(), tasks = [first];
      if (!first.single) for (let i = 0; i < queue.length && tasks.length < 128;) {
        const task = queue[i];
        if (!task.single && task.operation === first.operation && task.args.format === first.args.format) tasks.push(...queue.splice(i, 1));
        else i++;
      }
      running++;
      Promise.resolve().then(async () => {
        if (signal?.aborted) throw aborted();
        if (tasks.length === 1) return [{ address: first.args.address, ok: true, result: await sdk.resource(first.operation, first.args) }];
        const response = await sdk.resource(first.operation + '_many', {
          addresses: tasks.map(task => task.args.address), ...(first.args.format ? { format: first.args.format } : {}),
        });
        const results = response?.results;
        if (!Array.isArray(results) || results.length !== tasks.length || results.some((item, i) =>
          item?.address !== tasks[i].args.address || typeof item.ok !== 'boolean'
          || (item.ok ? !item.result || typeof item.result !== 'object' : !item.error || typeof item.error.code !== 'string'))) {
          throw Object.assign(new Error('Invalid resource batch response'), { code: 'projection.resource_batch_response' });
        }
        return results;
      }).then(results => finish(tasks, null, results), error => finish(tasks, error));
    }
  }
  function finish(tasks, error, results) {
    running--;
    for (const [i, task] of tasks.entries()) {
      const item = results?.[i], failure = signal?.aborted ? aborted() : error || (item?.ok === false ? itemError(item) : null);
      // A body can fit the single-read limit but exceed the batch's remaining
      // budget. Retry only that item once alone, never an unsupported operation
      // or an authorization failure, and never loop for an oversized body.
      if (!signal?.aborted && tasks.length > 1 && !task.single && task.operation === 'read'
        && ['projection.resource_size', 'projection.resource_budget'].includes(failure?.code)) {
        task.single = true; queue.push(task); continue;
      }
      pending.delete(task.key);
      if (failure && task.operation === 'resolve' && unavailable(failure.code)) task.resolve({ readable: false, resource_type: '', title: '', portal_url: '', error: { code: failure.code, message: failure.message }, status: failure.status });
      else if (failure) task.reject(failure); else task.resolve(item.result);
    }
    schedule();
  }
  signal?.addEventListener('abort', () => {
    for (const task of queue.splice(0)) { pending.delete(task.key); task.reject(aborted()); }
  }, { once: true });
  function request(operation, args) {
    if (signal?.aborted) return Promise.reject(aborted());
    const key = JSON.stringify([operation, args.address, args.format || 'text']);
    if (!pending.has(key)) {
      const promise = new Promise((resolve, reject) => queue.push({ key, operation, args, resolve, reject }));
      pending.set(key, promise); schedule();
    }
    return pending.get(key);
  }
  async function resolve(ref) {
    // Authorization is checked afresh; permissions and evidence may change while open.
    const result = await request('resolve', { address: resourceAddress(ref) });
    return { ...result, ref };
  }
  async function read(ref, format = 'text') {
    let result;
    try {
      // The read endpoint authenticates the workbench and exact resource as
      // the current actor itself; a separate resolve adds no permission gate.
      result = await request('read', { address: resourceAddress(ref), ...(format === 'yaml' ? { format } : {}) });
    } catch (error) {
      // Access can be revoked or the file deleted between any two reads.
      if (unavailable(error?.code)) {
        return { readable: false, ref, data: null, error: { code: error.code, message: error.message }, status: error.status };
      }
      throw error;
    }
    if (format === 'yaml') return { readable: true, ref, data: result.data, metadata: result.metadata };
    const encoded = result.content ?? result.text ?? result.base64 ?? '';
    const text = result.encoding === 'base64' || result.base64 !== undefined
      ? new TextDecoder().decode(Uint8Array.from(atob(encoded), char => char.charCodeAt(0))) : encoded;
    return { readable: true, ref, data: format === 'json' ? JSON.parse(text) : text, metadata: result.metadata };
  }
  async function readable(items, getRef = item => item.ref) {
    const checked = await Promise.all(items.map(async item => ({ item, access: await resolve(getRef(item)) })));
    // Callers must construct counts and links only from this result. Preserve a
    // missing prerequisite as unknown; filtering cannot prove executability.
    return checked.filter(value => value.access.readable).map(value => value.item);
  }
  return Object.freeze({ resolve, read, readable });
}
