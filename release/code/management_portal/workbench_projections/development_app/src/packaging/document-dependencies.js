// Dependencies describe only explicitly selected index bodies, never their links.
export function documentRequirements(indexes={}) {
  return Object.entries(indexes||{}).flatMap(([kind,value])=>value?.enabled?(value.requirements||[]).map(item=>({
    kind,type:item.type,project_ids:item.project_ids||[],entries:(item.entries||[]).filter(id=>value.entries?.includes(id)),
  })).filter(v=>v.type&&v.entries.length):[]);
}
export function includeDocumentTypes(manual,indexes) {
  const types=new Map(manual.filter(v=>v.manual!==false).map(v=>[v.type,{...v}]));
  for(const item of documentRequirements(indexes)){
    if(!types.has(item.type))types.set(item.type,{type:item.type,manual:false,instances:[],description:'',ownership:{}});
    const type=types.get(item.type);type.document_entries=[...(type.document_entries||[]),{index:item.kind,entries:item.entries}];
  }
  return [...types.values()];
}
