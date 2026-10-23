// Portal supplies the connection; DSH owns the Messages transport and catalog.
import {assertUsableApiKey} from '@deepseek-ai/dsh-llm';
import {registerDeepSeekProvider, resolveAdapterOptions, catalogModelInfo} from '@deepseek-ai/dsh-llm-deepseek';

export const name = 'portal-api';
export const inject = ['llm'];
export function apply(ctx, config) {
  const connection = resolveAdapterOptions(config);
  const key = () => assertUsableApiKey(process.env.DEEPSEEK_API_KEY, name, 'DEEPSEEK_API_KEY');
  registerDeepSeekProvider(ctx, 'deepseek-official', {
    options: () => connection,
    providerName: 'Portal Model API',
    resolveAuth: async () => ({headers: {
      ...JSON.parse(process.env.PORTAL_DSH_API_HEADERS || '{}'),
      ...(config.auth === 'bearer' ? {Authorization: `Bearer ${key()}`} : {'x-api-key': key()}),
    }}),
    discoverModels: async provider => {
      key();
      return connection.models.map(model => catalogModelInfo(provider, model));
    },
  });
}
