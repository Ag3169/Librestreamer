// drag-and-drop upload
(function(){
'use strict';
const dz=document.getElementById('dropzone');const fi=document.getElementById('file-input');const fl=document.getElementById('file-list');const ub=document.getElementById('upload-btn');const pg=document.getElementById('upload-progress');
if(!dz)return;let files=[];
dz.addEventListener('click',()=>fi.click());
fi.addEventListener('change',()=>addFiles(fi.files));
['dragenter','dragover'].forEach(e=>dz.addEventListener(e,ev=>{ev.preventDefault();dz.classList.add('dragover')}));
['dragleave','drop'].forEach(e=>dz.addEventListener(e,ev=>{ev.preventDefault();dz.classList.remove('dragover')}));
dz.addEventListener('drop',e=>{if(e.dataTransfer.files.length)addFiles(e.dataTransfer.files)});
function addFiles(fo){for(const f of fo)files.push(f);render()}
function render(){fl.innerHTML='';files.forEach((f,i)=>{const it=el('div',{class:'file-item'},el('span',{class:'fname'},escapeHtml(f.name)),el('span',{class:'fsize'},fmt(f.size)),el('span',{class:'fremove',onclick:()=>{files.splice(i,1);render()}},'×'));fl.appendChild(it)});ub.disabled=!files.length}
function el(t){const e=document.createElement(t);for(let i=1;i<arguments.length;i++){const a=arguments[i];if(typeof a==='string')e.textContent=a;else if(a instanceof HTMLElement)e.appendChild(a);else if(a&&typeof a==='object')for(const[k,v]of Object.entries(a)){if(k==='onclick')e.addEventListener('click',v);else if(k==='class')e.className=v;else e.setAttribute(k,v)}}return e}
function fmt(b){if(b<1024)return b+' B';if(b<1048576)return(b/1024).toFixed(1)+' KB';return(b/1048576).toFixed(1)+' MB'}
function escapeHtml(s){return String(s||'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
ub.addEventListener('click',()=>{
  if(!files.length)return;const be=document.getElementById('upload-backend').value;const ca=document.getElementById('upload-category').value;const sp=document.getElementById('upload-subpath').value;
  const i=window.i18n_upload||{};ub.disabled=true;ub.textContent=i.uploading||'Uploading...';
  const fd=new FormData();fd.append('backend',be);fd.append('category',ca);if(sp)fd.append('subpath',sp);
  for(const f of files)fd.append('file',f,f.name);
  const xhr=new XMLHttpRequest();xhr.open('POST','/api/admin/upload');
  xhr.upload.onprogress=e=>{if(e.lengthComputable){const p=(e.loaded/e.total)*100;pg.innerHTML='<div class="progress-bar"><span style="width:'+p+'%"></span></div><div class="progress-label">'+p.toFixed(0)+'% of '+fmt(e.total)+'</div>'}};
  xhr.onload=()=>{let m;try{m=JSON.parse(xhr.responseText)}catch{m={}}if(xhr.status===200){pg.innerHTML='<div class="progress-label" style="color:var(--accent)">'+(i.uploaded||'Uploaded __N__ file(s)').replace('__N__',String(m.count||files.length))+'</div>';files=[];render();fetch('/api/refresh',{method:'POST'}).then(()=>setTimeout(()=>pg.innerHTML='',3000))}else{pg.innerHTML='<div class="progress-label" style="color:#f04747">'+(i.error||'Error: __M__').replace('__M__',m.error||xhr.statusText)+'</div>'}ub.disabled=false;ub.textContent=i.upload_btn||'Upload'};
  xhr.onerror=()=>{pg.innerHTML='<div class="progress-label" style="color:#f04747">'+(i.failed||'Failed')+'</div>';ub.disabled=false;ub.textContent=i.upload_btn||'Upload'};
  xhr.send(fd);
});
window.loadDir=async function(){const be=document.getElementById('upload-backend')?.value;const ca=document.getElementById('upload-category')?.value;const sp=document.getElementById('upload-subpath')?.value;const pv=document.getElementById('dir-preview');if(!be||!pv)return;try{const p=new URLSearchParams({backend:be,category:ca,subpath:sp||''});const r=await fetch('/api/admin/dir?'+p);const d=await r.json();if(d.entries&&d.entries.length){pv.innerHTML=d.entries.map(e=>'<div class="dir-entry '+(e.isDir?'folder':'')+'">'+(e.isDir?'&#128193;':'&#128196;')+' '+escapeHtml(e.name)+'</div>').join('')}else{pv.innerHTML='<span class="muted">'+((window.i18n_upload||{}).empty_dir||'Empty directory')+'</span>'}}catch{pv.innerHTML=''}};
setTimeout(()=>window.loadDir(),100);
})();
