import {createPersistence} from './persistence.js';
export async function openHttpDocument({paperId, principal, fetcher = fetch}) {
  async function call(path, body, headers, signal) {
    const response = await fetcher('/paper/api/reader/' + path, {
      method: body ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store',
      signal, headers: {'X-Reader-Principal': principal, ...headers, ...(body ? {'Content-Type': 'application/json'} : {})},
      ...(body ? {body: JSON.stringify(body)} : {}),
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw Object.assign(new Error(error.error || `Reader request failed (${response.status})`), {status: response.status});
    }
    return response;
  }
  const opened = await (await call('open', {paper_id: paperId}, {Prefer: 'return=minimal'})).json();
  if (opened.principal !== principal) throw Error('principal mismatch');
  const document = opened.document;
  const query = new URLSearchParams(document);
  const pdfPath = 'pdf?' + query;
  const chunkSize = 65536;
  async function readRange(begin, end, expectedLength, signal) {
    const response = await call(pdfPath, null, {Range: `bytes=${begin}-${end - 1}`}, signal);
    // Older hosts may return the whole representation to the first range request.
    if (response.status === 200 && expectedLength === undefined) {
      const bytes = new Uint8Array(await response.arrayBuffer());
      return {bytes, length: bytes.length};
    }
    const match = /^bytes (\d+)-(\d+)\/(\d+)$/.exec(response.headers.get('Content-Range'));
    const length = match && Number(match[3]);
    const stop = Math.min(end, length);
    if (response.status !== 206 || !Number.isSafeInteger(length) || begin >= stop ||
        Number(match[1]) !== begin || Number(match[2]) !== stop - 1 ||
        (expectedLength !== undefined && length !== expectedLength)) {
      throw Error('Invalid versioned PDF range');
    }
    const bytes = new Uint8Array(await response.arrayBuffer());
    if (bytes.length !== stop - begin) throw Error('Incomplete PDF range');
    return {bytes, length};
  }
  const persistence = createPersistence({scope: {actorKey: principal, ...document}, transport: {
    actorKey: principal,
    read: async () => (await call('annotations?' + query)).json(),
    commit: async body => (await call('annotations', body)).json(),
  }});
  return {document, persistence, options: {
    load: async () => {
      const [annotations, first] = await Promise.all([persistence.load(), readRange(0, chunkSize)]);
      if (first.bytes.length === first.length) return {...annotations, pdf: first.bytes, author: principal};
      return {...annotations, author: principal, pdfSource: {
        url: new URL('/paper/api/reader/' + pdfPath, location.href).href,
        chunkSize, length: first.length, initialBytes: first.bytes,
        read: async (begin, end, signal) => (await readRange(begin, end, first.length, signal)).bytes,
      }};
    },
    save: persistence.save, remove: persistence.remove,
    // Position persistence is not advertised until a formal position endpoint exists.
    savePosition: async () => {},
  }};
}
