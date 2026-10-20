self.onmessage = ({ data }) => {
  const count = Math.min(24, Math.max(1, Number(data.count) || 1));
  self.postMessage(Array.from({ length: count }, (_, i) => ({ id: i, y: 70 + Math.sin(i) * 30 })));
};
