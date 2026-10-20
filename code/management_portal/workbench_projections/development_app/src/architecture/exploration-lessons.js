import { workspaceSource } from './exploration-resources.js';
import { schemaMatches } from '../shared/format-tags.js';

/** Source links support reading; their revisions never certify a lesson. */
export async function readLessonSources({ read, instructions, pin }) {
  const entry = await read.json(pin);
  const version = [1, 2].find(value => schemaMatches(entry.schema, `tfi.hatg-lesson-source-supplement-manifest.v${value}`));
  if (!version) throw new Error('指令说明入口格式不受支持');
  const body = await read.json(entry.artifact);
  if (!schemaMatches(body.schema, `tfi.hatg-lesson-source-supplement.v${version}`)) throw new Error('指令说明格式不受支持');
  if (!Array.isArray(body.lessons)) throw new Error('指令说明缺少教学内容');
  const sources = new Map();
  for (const source of body.sources || []) {
    if (!source.address) continue;
    const current = { address: workspaceSource(source.address) };
    sources.set(source.address, current);
    if (source.frozen?.address) sources.set(source.frozen.address, current);
  }
  const names = new Set(instructions.map(value => value.instruction));
  const lessons = Object.fromEntries(body.lessons.filter(value => names.has(value.instruction))
    .map(value => [value.instruction, value]));
  return { sources, lessons, pinned: { manifest: pin, artifact: entry.artifact } };
}
