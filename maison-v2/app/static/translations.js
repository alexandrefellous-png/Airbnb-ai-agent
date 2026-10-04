// Translation changes display only. The original feed data remains untouched.
function translatedMessage(text){
  return `<span class="translation"><span class="translation-status">Traduction française en cours…</span><span class="translation-text">${esc(text)}</span></span>`;
}
const translationMemory=new Map(),translationQueue=new Set();
let translationRunning=false;
function showTranslation(node,original,french){
  node.innerHTML=`<span class="translation-status">${original===french?'Message en français':'Traduction française · automatique'}</span><span class="translation-text">${esc(french)}</span>${original===french?'':`<details class="translation-original"><summary>Voir l’original</summary><span class="translation-text">${esc(original)}</span></details>`}`;
}
const translationVisibility=new IntersectionObserver(entries=>{
  entries.forEach(entry=>{if(entry.isIntersecting){translationVisibility.unobserve(entry.target);translationQueue.add(entry.target);}});
  runTranslations();
},{rootMargin:'100px'});
async function runTranslations(){
  if(translationRunning)return;translationRunning=true;
  try{
    while(translationQueue.size){
      const nodes=Array.from(translationQueue).slice(0,8);nodes.forEach(n=>translationQueue.delete(n));
      const pending=nodes.filter(n=>n.isConnected);
      if(!pending.length)continue;
      const texts=pending.map(n=>n.querySelector('.translation-text').textContent);
      try{
        // Keep requests bounded even for long messages.
        const missing=[...new Set(texts.filter(t=>!translationMemory.has(t)))];
        for(let start=0;start<missing.length;){
          const batch=[];let size=0;
          while(start<missing.length&&batch.length<8&&size+missing[start].length<=30000){const t=missing[start++];batch.push(t);size+=t.length;}
          if(!batch.length)throw Error('Message trop long pour la traduction automatique.');
          const result=await api('/translations',{method:'POST',body:{texts:batch}});
          batch.forEach((text,i)=>translationMemory.set(text,result.translations[i]));
        }
        pending.forEach((node,i)=>showTranslation(node,texts[i],translationMemory.get(texts[i])));
      }catch(error){
        pending.forEach(node=>{
          node.querySelector('.translation-status').textContent='Original · '+error.message;
          const retry=document.createElement('button');retry.type='button';retry.className='text-button';retry.textContent='Réessayer la traduction';
          retry.onclick=event=>{event.stopPropagation();retry.remove();translationQueue.add(node);runTranslations();};node.append(retry);
        });
      }
    }
  }finally{translationRunning=false;}
}
new MutationObserver(()=>{
  document.querySelectorAll('.translation:not([data-watched])').forEach(node=>{node.dataset.watched='true';translationVisibility.observe(node);});
}).observe(document.body,{childList:true,subtree:true});
document.addEventListener('click',event=>{
  if(event.target.closest('.translation-original'))event.stopPropagation();
},true);
