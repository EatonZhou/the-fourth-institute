(()=>{self.onmessage=({data:e})=>{let t=Math.min(24,Math.max(1,Number(e.count)||1));self.postMessage(Array.from({length:t},(a,s)=>({id:s,y:70+Math.sin(s)*30})))};})();
