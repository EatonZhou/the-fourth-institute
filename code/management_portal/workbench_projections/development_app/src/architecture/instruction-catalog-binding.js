import { schemaMatches } from '../shared/format-tags.js';

export const currentCatalogAttachment = {
  address: 'workspace_13209cb6666b/file/deep-architecture/objects/isa-catalog-attachment-664e88043ae96e8717abac64faebf7bd4be75f25912ad3085954384fb4ae15fb.json',
  sha256: '664e88043ae96e8717abac64faebf7bd4be75f25912ad3085954384fb4ae15fb', size: 6217,
};

export async function readCatalogAttachment({ read, pin }) {
  const entry = await read.json(pin);
  if (!schemaMatches(entry.schema, 'tfi.hatg-catalog-attachment.v1')) throw new Error('指令目录附件格式不受支持');
  if (!entry.catalog_manifest?.address) throw new Error('指令目录入口缺少目录地址');
  return { ...entry, pin };
}
