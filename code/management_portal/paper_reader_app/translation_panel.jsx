import React, {useEffect, useRef, useState} from 'react';

const SOURCES = {google: 'Google', deepseek: 'DeepSeek Flash', kimi: 'Moonshot Kimi API'};

export function TranslationMode({mode, onChange}) {
  return <label className="paperTranslationMode">翻译模式 <select aria-label="翻译模式" value={mode} onChange={event => onChange(event.target.value)}>
    <option value="auto">自动（Google 优先）</option>
    <option value="google">Google</option>
    <option value="deepseek">DeepSeek Flash</option>
    <option value="kimi">Moonshot Kimi API</option>
  </select></label>;
}

export function TranslationPanel({selection, mode, isCurrent, request}) {
  const [view, setView] = useState({state: 'idle'});
  const generation = useRef(0), pending = useRef(null);
  const run = async (input, selectedMode) => {
    pending.current?.abort();
    const controller = new AbortController();
    pending.current = controller;
    const own = ++generation.current;
    if (!input || !isCurrent()) return;
    setView({state: 'loading'});
    try {
      const result = await request(input, selectedMode, crypto.randomUUID(), controller.signal);
      if (own !== generation.current || controller.signal.aborted || !isCurrent()) return;
      setView({state: 'success', result});
    } catch (error) {
      if (own !== generation.current || controller.signal.aborted || !isCurrent()) return;
      setView({state: 'error', error: error.message});
    }
  };
  useEffect(() => {
    if (selection) void run(selection, mode);
    return () => {generation.current++; pending.current?.abort();};
  }, [selection, mode]);
  if (!selection) return null;
  return <section className="paperTranslation" aria-label="选区翻译">
    {selection && <div className="paperTranslationBody" aria-live="polite">
      <details><summary>所选原文</summary><p>{selection.text}</p></details>
      {view.state === 'loading' && <p role="status">正在翻译…</p>}
      {view.state === 'error' && <p role="alert">{view.error}</p>}
      {view.state === 'success' && <>
        <p className="paperTranslationText">{view.result.text}</p>
        <small>{SOURCES[view.result.provider]} · {view.result.model || '未报告模型'} ·
          {{hit: '缓存命中', inflight: '复用进行中任务', operation: '原任务', miss: '本次请求'}[view.result.cache]}
          {view.result.google_request_ms != null && ` · Google 请求 ${view.result.google_request_ms} ms`}
          {view.result.task_total_ms != null && ` · 任务总耗时 ${view.result.task_total_ms} ms`}
          {view.result.total_ms != null && ` · 本次接口耗时 ${view.result.total_ms} ms`}
        </small>
      </>}
      {view.state !== 'loading' && <button onClick={() => run(selection, mode)}>重试翻译</button>}
    </div>}
  </section>;
}
