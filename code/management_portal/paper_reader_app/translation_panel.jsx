import React, {useEffect, useLayoutEffect, useRef, useState} from 'react';

const SOURCES = {google: 'Google', deepseek: 'DeepSeek Flash', kimi: 'Moonshot Kimi API'};

const t = value => window.PortalI18n?.t(value) || value;
const seconds = ms => `${(ms / 1000).toFixed(1)} ${t('秒')}`;
export function translationDiagnostics(result, elapsedMs) {
  const lines = [`${t('本次等待')}: ${seconds(elapsedMs)}`];
  if (result.task_id) lines.push(`task_id: ${result.task_id}`);
  const cache = {hit:'缓存命中', inflight:'复用进行中任务', operation:'原任务', miss:'本次请求'}[result.cache];
  if (cache) lines.push(t(cache));
  if (result.cache === 'inflight') lines.push(t('任务耗时从原任务开始计算；本次等待从本次操作开始计算。'));
  for (const [key,label] of [['google_request_ms','Google 请求'],['task_total_ms','任务总耗时'],['total_ms','最后一次接口处理耗时']]) {
    if (Number.isFinite(result[key])) lines.push(`${t(label)}: ${seconds(result[key])}`);
  }
  for (const [key,label] of [['accepted_at','受理'],['created_at','任务创建'],['dispatch_wait_started_at','派发等待'],['upstream_started_at','上游请求开始'],['upstream_finished_at','上游请求结束'],['published_at','发布']]) {
    if (typeof result[key] === 'string' && result[key]) lines.push(`${t(label)}: ${result[key]}`);
  }
  return lines.join('\n');
}

export function TranslationMode({mode, onChange}) {
  return <label className="paperTranslationMode">{t('翻译模式')} <select aria-label={t('翻译模式')} value={mode} onChange={event => onChange(event.target.value)}>
    <option value="auto">{t('自动（Google → DeepSeek → Kimi）')}</option>
    <option value="google">Google</option>
    <option value="deepseek">DeepSeek Flash</option>
    <option value="kimi">Moonshot Kimi API</option>
  </select></label>;
}

export function useTranslation({selection, mode, isCurrent, request}) {
  const [view, setView] = useState({state: 'idle'});
  const generation = useRef(0), pending = useRef(null);
  const run = async (input, selectedMode) => {
    pending.current?.abort();
    const controller = new AbortController();
    pending.current = controller;
    const own = ++generation.current;
    if (!input || !isCurrent()) return;
    const started = performance.now();
    setView({state: 'loading'});
    try {
      const result = await request(input, selectedMode, crypto.randomUUID(), controller.signal);
      if (own !== generation.current || controller.signal.aborted || !isCurrent()) return;
      setView({state: 'success', result, started, elapsedMs: performance.now() - started});
    } catch (error) {
      if (own !== generation.current || controller.signal.aborted || !isCurrent()) return;
      setView({state: 'error', error: error.message});
    }
  };
  useEffect(() => {
    if (selection) void run(selection, mode);
    return () => {generation.current++; pending.current?.abort();};
  }, [selection, mode]);
  // Measure through the DOM commit that makes the successful translation visible.
  useLayoutEffect(() => {
    if (view.state === 'success') setView(current => ({...current, elapsedMs: performance.now() - current.started}));
  }, [view.state, view.result]);
  return {view, retry: () => run(selection, mode)};
}

export function TranslationPanel(props) {
  const model = useTranslation(props);
  return <TranslationResult {...props} model={model} />;
}

export function TranslationResult({selection, mode, setMode, model, expand, full = false}) {
  const {view, retry} = model;
  const [copyError, setCopyError] = useState('');
  if (!selection) return null;
  return <section className="paperTranslation" aria-label={t('选区翻译')}>
    {full && setMode && <TranslationMode mode={mode} onChange={setMode} />}
    {selection && <div className="paperTranslationBody" aria-live="polite">
      {full && <details open><summary>{t('所选原文')}</summary><p>{selection.text}</p></details>}
      {view.state === 'loading' && <p role="status">{t('正在翻译…')}</p>}
      {view.state === 'error' && <p role="alert">{window.PortalI18n?.message(view.error)||view.error}</p>}
      {view.state === 'success' && <>
        <p className="paperTranslationText">{view.result.text}</p>
        <small title={translationDiagnostics(view.result, view.elapsedMs)} tabIndex={0}>
          {t('来源')} {SOURCES[view.result.provider] || view.result.provider} · {t('用时')} {seconds(view.elapsedMs)}
        </small>
      </>}
      <div className="paperTranslationActions">
      {view.state === 'success' && <button type="button" className="toolbar-button wide-button portalButton is-compact" onClick={async () => {
        try {await navigator.clipboard.writeText(view.result.text); setCopyError('');}
        catch (_) {setCopyError('复制失败，请手动选择译文复制');}
      }}>{t('复制')}</button>}
      {expand && <button type="button" className="toolbar-button wide-button portalButton is-compact" onClick={expand}>{t('展开')}</button>}
      </div>
      {copyError && <p role="alert">{t(copyError)}</p>}
      {full && <button onClick={retry}>{t('重试翻译')}</button>}
    </div>}
  </section>;
}
