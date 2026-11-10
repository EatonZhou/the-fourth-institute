const PATH = '/paper/api/reader/translation';

// Aborting observation never cancels a shared server task or resubmits it.
export async function translateSelection({selection, mode, operationId, principal, signal}) {
  const call = async (url, body) => {
    const response = await fetch(url, {method: body ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store', signal,
      headers: {'X-Reader-Principal': principal, ...(body ? {'Content-Type': 'application/json'} : {})},
      ...(body ? {body: JSON.stringify(body)} : {}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error?.message || `翻译请求失败 (${response.status})`);
    return result;
  };
  let result = await call(PATH, {operation_id: operationId, text: selection.text,
    source_language: 'auto', target_language: 'zh-CN', mode, document: selection.document});
  const cache = result.cache;
  const taskId = result.task_id;
  if (typeof taskId !== 'string' || !taskId) throw new Error('翻译服务未返回任务编号');
  while (['queued', 'running'].includes(result.status)) {
    await new Promise((resolve, reject) => {
      const abort = () => {clearTimeout(timer); reject(new DOMException('Aborted', 'AbortError'));};
      const timer = setTimeout(() => {signal.removeEventListener('abort', abort); resolve();}, 700);
      signal.addEventListener('abort', abort, {once: true});
      if (signal.aborted) abort();
    });
    result = await call(`${PATH}?task_id=${encodeURIComponent(taskId)}`);
    if (result.task_id !== taskId) throw new Error('翻译任务编号发生变化');
  }
  if (result.status !== 'succeeded') throw new Error(result.error?.message || '翻译已取消或失败');
  if (typeof result.text !== 'string' || !['google', 'deepseek'].includes(result.provider)) throw new Error('翻译服务返回了无效结果');
  return {...result, cache};
}
