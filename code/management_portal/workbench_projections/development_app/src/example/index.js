import { select, scaleLinear } from 'd3';

export async function mount(root, { sdk, onDispose, signal }) {
  const text = document.createElement('p');
  text.textContent = '平台示例：Worker 计算布局，D3 绘制本地模拟数据。';
  const svg = select(root).append('svg').attr('viewBox', '0 0 600 140').attr('aria-label', '本地模拟布局');
  root.prepend(text);
  // The host creates a Worker from this revision's immutable script. No SDK or
  // user credentials are exposed inside the Worker.
  const worker = await sdk.createWorker('layout.worker.js');
  if (signal.aborted) { worker.terminate(); return {}; }
  onDispose(() => worker.terminate());
  worker.onmessage = ({ data }) => {
    const x = scaleLinear([0, data.length - 1], [60, 540]);
    svg.selectAll('circle').data(data).join('circle').attr('cx', (_, i) => x(i)).attr('cy', d => d.y).attr('r', 18).attr('fill', 'var(--accent)');
    text.textContent = `平台示例：D3 已绘制 Worker 返回的 ${data.length} 个模拟节点。`;
  };
  worker.postMessage({ count: 6 });
  return { destroy() { root.replaceChildren(); } };
}
