// LibreStreamer frontend client logic
(function(){
'use strict';
function el(t,a){const e=document.createElement(t);if(a)for(const[k,v]of Object.entries(a)){if(k==='class')e.className=v;else if(k==='html')e.innerHTML=v;else if(k.startsWith('on'))e.addEventListener(k.slice(2),v);else e.setAttribute(k,v)}return e}
function escapeHtml(s){return String(s||'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
function pad(n){return String(n||0).padStart(2,'0')}
function subtext(it){if(it.type==='episode')return`${it.show_name||''} S${pad(it.season)}E${pad(it.episode)}`;if(it.year)return String(it.year);return''}

function makeCard(it,wide){
  const card=el('div',{class:'card '+(wide?'backdropCard':'portraitCard'),onclick:()=>location.href='/item/'+it.id});
  const box=el('div',{class:'cardBox'});
  const scal=el('div',{class:'cardScalable'});
  const padder=el('div',{class:wide?'cardPadder-backdrop':'cardPadder-portrait'});
  const imgC=el('div',{class:'cardImageContainer'});
  if(it.has_thumbnail){imgC.style.backgroundImage=`url('/api/thumbnail/${it.id}')`}
  else{const dt=el('span',{class:'cardDefaultText'});dt.textContent=(it.title||'?').slice(0,2);imgC.appendChild(dt)}
  const overlay=el('div',{class:'cardOverlayContainer'});
  const playBtn=el('div',{class:'cardOverlayPlayBtn',html:'\u25B6'});
  overlay.appendChild(playBtn);
  scal.appendChild(padder);scal.appendChild(imgC);scal.appendChild(overlay);
  const footer=el('div',{class:'cardFooter'});
  const name=el('div',{class:'cardText'});name.textContent=it.title;
  const sub=el('div',{class:'cardText cardText-secondary'});sub.textContent=subtext(it);
  footer.appendChild(name);footer.appendChild(sub);
  box.appendChild(scal);box.appendChild(footer);
  card.appendChild(box);
  return card;
}

async function getJson(u){const r=await fetch(u);if(!r.ok)throw new Error(r.status);return r.json()}

async function fillGrid(id,items,wide){
  const h=document.getElementById(id);if(!h)return;h.innerHTML='';
  if(!items||!items.length){const m=el('div',{class:'muted'});m.textContent='';h.appendChild(m);return}
  items.slice(0,18).forEach(it=>h.appendChild(makeCard(it,wide)));
}

async function loadHome(){
  if(!document.getElementById('movies'))return;
  try{
    const[m,s,st]=await Promise.all([getJson('/api/library?type=movie'),getJson('/api/library?type=show'),getJson('/api/status')]);
    fillGrid('movies',m.items);fillGrid('shows',s.items);renderBackends(st.backends||[]);
  }catch(e){console.error(e)}
}
function renderBackends(metrics){
  const h=document.getElementById('backends');if(!h)return;h.innerHTML='';
  metrics.forEach(m=>{
    const b=el('div',{class:'backend'});
    b.appendChild(el('div',{class:'bname'},el('span',{class:'dot '+(m.healthy?'ok':'bad')}),document.createTextNode(m.name)));
    b.appendChild(el('div',{class:'bkind'},document.createTextNode(m.kind||'')));
    if(m.cpu_usage_pct>=0)b.appendChild(mb('CPU',m.cpu_usage_pct));
    if(m.memory_usage_pct>=0)b.appendChild(mb('RAM',m.memory_usage_pct));
    if(m.gpu_usage_pct>=0)b.appendChild(mb('GPU',m.gpu_usage_pct));
    h.appendChild(b);
  });
}
function mb(l,p){const v=Math.max(0,Math.min(100,p));return el('div',{},el('div',{class:'bmetric'},el('span',{},l),el('span',{},v.toFixed(0)+'%')),el('div',{class:'bar'},el('span',{style:'width:'+v+'%'})))}
document.addEventListener('DOMContentLoaded',()=>{loadHome()})();
})();
