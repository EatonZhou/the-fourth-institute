import {createPersistence} from './persistence.js';
export async function openHttpDocument({paperId, principal, fetcher = fetch}) {
  async function call(path, body) {
    const response = await fetcher('/paper/api/reader/' + path, {
      method: body ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store',
      headers: {'X-Reader-Principal': principal, ...(body ? {'Content-Type': 'application/json'} : {})},
      ...(body ? {body: JSON.stringify(body)} : {}),
    });
    if (!response.ok) throw Object.assign(new Error((await response.json()).error), {status: response.status});
    return response;
  }
  const opened = await (await call('open', {paper_id: paperId})).json();
  if (opened.principal !== principal) throw Error('principal mismatch');
  const document = opened.document;
  const query = new URLSearchParams(document);
  const persistence = createPersistence({scope: {actorKey: principal, ...document}, transport: {
    actorKey: principal,
    read: async () => (await call('annotations?' + query)).json(),
    commit: async body => (await call('annotations', body)).json(),
  }});
  return {document, persistence, options: {
    load: async () => ({...await persistence.load(), pdf: new Uint8Array(await (await call('pdf?' + query)).arrayBuffer()), author: principal}),
    save: persistence.save, remove: persistence.remove,
    // Position persistence is not advertised until a formal position endpoint exists.
    savePosition: async () => {},
  }};
}
