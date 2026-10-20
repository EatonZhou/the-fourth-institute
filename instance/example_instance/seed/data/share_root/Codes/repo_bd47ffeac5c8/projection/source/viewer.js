import {mountMorphologyWall,mountLearningAlgorithm} from './original-wall.js';
const $=id=>document.getElementById(id),cache=new Map();
async function json(path){
 const asset=path.replace(/\.json$/,'.js');
 const worker=globalThis.Projection?.createWorker
  ? await Projection.createWorker(asset)
  : new Worker(new URL(asset,document.baseURI));
 return new Promise((resolve,reject)=>{
  const finish=(error,data)=>{clearTimeout(timer);worker.terminate();error?reject(error):resolve(data);};
  const timer=setTimeout(()=>finish(Error('动画数据加载超时：'+path)),30000);
  worker.onmessage=event=>finish(null,event.data);
  worker.onerror=event=>finish(Error('无法读取 '+path+'：'+event.message));
  worker.onmessageerror=()=>finish(Error('动画数据格式错误：'+path));
  worker.postMessage('read');
 });
}
async function start(){
$('wall').textContent='正在加载动画墙…';
const entries=await json('catalog.json');
const groups=[['overview','精选概览','featured'],['all','全选','all'],...new Map(entries.filter(e=>e.deliveryGroup!=='featured').map(e=>[e.deliveryGroup,e.deliveryGroupLabel])).entries()].map(([id,title,key])=>({id,title,storyLabel:id==='overview'?undefined:title,entries:entries.filter(e=>(key||id)==='all'||e.deliveryGroup===(key||id)),pageSize:6}));
async function loadClip(id){
 if(!cache.has(id))cache.set(id,(async()=>{
  const entry=entries.find(e=>e.id===id),record=await json(entry.offlineClip),payload=await json(record.framesFile),frames=Uint8Array.from(atob(payload.data),c=>c.charCodeAt(0));
  const hash=[...new Uint8Array(await crypto.subtle.digest('SHA-256',frames))].map(x=>x.toString(16).padStart(2,'0')).join('');
  if(hash!==record.sha256||frames.length!==record.width*record.height*record.steps.length)throw Error('回放校验失败：'+id);
  return {...record,frames};
 })().catch(error=>{cache.delete(id);throw error;}));
 return cache.get(id);
}
let wall,algorithm;
function showWall(){ $('learn').hidden=true;$('wall').hidden=false;algorithm?.hide();wall?.show(); }
function showLearning(){ wall.hide();$('wall').hidden=true;$('learn').hidden=false;algorithm.show(); }
algorithm=mountLearningAlgorithm($('algorithm'),{onObserve:()=>{showWall();wall.openCollection('learning');}});
$('wall').replaceChildren();
wall=await mountMorphologyWall($('wall'),{
 loadCatalog:async()=>({entries,collections:groups,loadClip,destroy(){cache.clear();}}),
 onLearn:showLearning,
 onObserve:openPaper
});
const learnButton=document.createElement('button');
learnButton.type='button';learnButton.className='portalButton';learnButton.textContent='学习算法动画';
learnButton.setAttribute('aria-controls','learn');learnButton.onclick=showLearning;
document.querySelector('.morphology-heading .morphology-tools').prepend(learnButton);
$('back').onclick=()=>{showWall();learnButton.focus();};
const nav=document.querySelector('.morphology-stories'),paper=document.createElement('a');
paper.className='portalButton';paper.textContent='论文与成果';paper.href='/resource/article?item=article_0565c33e0c03';paper.target='_blank';paper.rel='noopener';nav?.append(paper);
paper.onclick=event=>{event.preventDefault();openPaper();};
wall.show();document.body.dataset.ready='true';
window.addEventListener('pagehide',()=>{wall.destroy();algorithm.destroy();},{once:true});
}
function openPaper(){
 const url='/resource/article?item=article_0565c33e0c03';
 if(globalThis.Projection?.openPage)return Projection.openPage(url);
 window.open(url,'_blank','noopener');
}
start().catch(error=>{
 const message=document.createElement('p');message.setAttribute('role','alert');
 message.textContent='动画墙加载失败：'+error.message+'。请刷新重试。';
 $('wall').replaceChildren(message);console.error(error);
});
