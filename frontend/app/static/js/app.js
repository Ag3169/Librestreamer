// LibreStreamer frontend client logic
(function(){
'use strict';
function el(t,a){const e=document.createElement(t);if(a)for(const[k,v]of Object.entries(a)){if(k==='class')e.className=v;else if(k==='html')e.innerHTML=v;else if(k.startsWith('on'))e.addEventListener(k.slice(2),v);else e.setAttribute(k,v)}return e}
function escapeHtml(s){return String(s||'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
function pad(n){return String(n||0).padStart(2,'0')}
function subtext(it){if(it.type==='episode')return`${it.show_name||''} · S${pad(it.season)}E${pad(it.episode)}`;if(it.year)return String(it.year);return''}
function card(it){
  const c=el('div',{class:'card',onclick:()=>location.href='/item/'+it.id});
  const thumb=it.has_thumbnail?{class:'thumb',style:`background-image:url('/api/thumbnail/${it.id}')`}:{class:'thumb empty',html:'&#127916;'};
  c.appendChild(el('div',thumb));
  if(it.type){const badge=el('div',{class:'card-badge '+it.type});badge.textContent=it.type;c.appendChild(badge)}
  const info=el('div',{class:'info'});
  info.appendChild(el('div',{class:'name',html:escapeHtml(it.title)}));
  info.appendChild(el('div',{class:'sub',html:subtext(it)||''}));
  c.appendChild(info);
  return c;
}
async function getJson(u){const r=await fetch(u);if(!r.ok)throw new Error(r.status);return r.json()}
async function fillGrid(id,items){
  const h=document.getElementById(id);if(!h)return;h.innerHTML='';
  if(!items||!items.length){h.appendChild(el('div',{class:'muted'},(window.i18n&&window.i18n.nothing_here)||'Nothing here yet.'));return}
  items.slice(0,24).forEach(it=>h.appendChild(card(it)));
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
    const b=el('div',{class:'backend'},el('div',{class:'bname'},el('span',{class:'dot '+(m.healthy?'ok':'bad')}),m.name),el('div',{class:'bkind'},m.kind||''));
    if(m.cpu_usage_pct>=0)b.appendChild(mb((window.i18n&&window.i18n.cpu)||'CPU',m.cpu_usage_pct));
    if(m.memory_usage_pct>=0)b.appendChild(mb((window.i18n&&window.i18n.ram)||'RAM',m.memory_usage_pct));
    if(m.gpu_usage_pct>=0)b.appendChild(mb((window.i18n&&window.i18n.gpu)||'GPU',m.gpu_usage_pct));
    h.appendChild(b);
  });
}
function mb(l,p){const v=Math.max(0,Math.min(100,p));return el('div',{},el('div',{class:'bmetric'},el('span',{},l),el('span',{},v.toFixed(0)+'%')),el('div',{class:'bar'},el('span',{style:'width:'+v+'%'})))}
document.addEventListener('DOMContentLoaded',()=>{loadHome()})();
})();
