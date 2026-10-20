// Filter source-backed items before counts, charts, parent links or cached excerpts.
export async function readableIndex(original, session) {
  const index=structuredClone(original), refs=new Set();
  function collect(value) {
    if(!value||typeof value!=='object')return;
    for(const [key,item] of Object.entries(value)) {
      if (['address','source','resident_id','experiment_id','report'].includes(key) && typeof item==='string' && /^[a-z]+_[0-9a-f]{12}(?:\/|$)/.test(item)) refs.add(item);
      if(key==='sources' && Array.isArray(item)) item.filter(x=>typeof x==='string').forEach(x=>refs.add(x));
      if(item&&typeof item==='object')collect(item);
    }
  }
  collect(index);
  Object.keys(index.sources||{}).forEach(x=>refs.add(x));
  Object.keys(index.normalized_reports||{}).forEach(x=>refs.add(x));
  const readable=await session.readable([...refs]);
  const permitted=value=> {
    if(!value||typeof value!=='object')return true;
    for(const key of ['address','source','resident_id','experiment_id','report']) {
      const ref=typeof value[key]==='object' ? value[key]?.address : value[key]; if(typeof ref==='string' && refs.has(ref) && !readable.has(ref))return false;
    }
    return !Array.isArray(value.sources)||value.sources.every(ref=>readable.has(ref));
  };
  function filter(value) {
    if(Array.isArray(value))return value.filter(permitted).map(filter);
    if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).map(([k,v])=>[k,filter(v)]));
    return value;
  }
  const filtered=filter(index);
  for(const key of ['sources','normalized_reports'])filtered[key]=Object.fromEntries(Object.entries(filtered[key]||{}).filter(([ref])=>readable.has(ref)));
  return filtered;
}
