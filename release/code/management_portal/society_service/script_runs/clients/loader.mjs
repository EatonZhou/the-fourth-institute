export async function resolve(specifier, context, nextResolve) {
  if (specifier === 'portal-script') return { url: new URL('./node_modules/portal-script/index.js', import.meta.url).href, shortCircuit: true };
  return nextResolve(specifier, context);
}
