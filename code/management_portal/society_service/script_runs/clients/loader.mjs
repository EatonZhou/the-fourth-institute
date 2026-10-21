export async function resolve(specifier, context, nextResolve) {
  if (specifier === 'portal-script') return { url: new URL('./portal-script/index.cjs', import.meta.url).href, shortCircuit: true };
  return nextResolve(specifier, context);
}
