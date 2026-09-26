
let copies=[], emus=[], favs=[], showfav=false, emuSel=new Set(), sortKey='idx', sortDir=1;
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
fetchAPI = async (p, body) => {
  const r = await fetch(p, {method: body?'POST':'GET', headers:{'Content-Type':'application/json'},
    body: body?JSON.stringify(body):undefined});
  return r.json();
};
function sortBy(k){ if(sortKey===k){ sortDir=-sortDir; } else { sortKey=k; sortDir=1; } refresh(); }
function sortShow(arr){
  const a=[...arr];
  a.sort((x,y)=>{
    const f1=favs.includes(String(x.index))?0:1, f2=favs.includes(String(y.index))?0:1;
    if(f1!==f2) return f1-f2;
    let v=0;
    if(sortKey==='idx') v=(parseInt(x.index)||0)-(parseInt(y.index)||0);
    else v=String(x.name).localeCompare(String(y.name),'zh');
    return v*sortDir;
  });
  return a;
}
async function refresh(){
  const s = await fetchAPI('/api/state'); if(!s) return;
  document.getElementById('sample').textContent = s.sample_version||'(未初始化)';
  copies=s.copies; emus=s.emus; favs=s.favs||[];
  document.getElementById('onlyfav').checked=showfav;
  document.getElementById('opmsg').textContent = s.running_op||'';
  renderCopies(); renderEmus();
  const lg=document.getElementById('logs'); lg.textContent=(s.logs||[]).join('\n'); lg.scrollTop=lg.scrollHeight;
}
function renderCopies(){
  const t=document.getElementById('copies'), no=document.getElementById('nocopies');
  document.getElementById('copycnt').textContent = copies.length?('共 '+copies.length+' 个'):'';
  t.style.display=copies.length?'':'none'; no.style.display=copies.length?'none':'';
  t.innerHTML='';
  copies.forEach(c=>{
    const tr=document.createElement('tr');
    tr.innerHTML='<td><input type="checkbox" class="ck" data-id="'+esc(c.id)+'"></td>'+
      '<td class="dim">'+esc(c.id)+'</td><td>'+esc(c.name)+'</td>'+
      '<td>'+esc(c.emu_index)+' '+esc(c.emu_name)+'</td>'+
      '<td><span class="'+(c.verified?'ok':'err')+'" title="'+esc((c.verify_msg||'')||'')+'">'+esc(c.adb)+' '+(c.verified?'✓':'✗')+'</span></td>'+
      '<td>'+esc(c.version)+'</td>'+
      '<td class="'+(c.running?'ok':'')+'">'+(c.running?'运行中':'停止')+'</td>'+
      '<td><button class="small" onclick="op('open',''+esc(c.id)+'')">开</button>'+
      '<button class="small gray" onclick="op('close',''+esc(c.id)+'')">关</button>'+
      '<button class="small gray" onclick="op('bind',''+esc(c.id)+'')">换绑</button>'+
      '<button class="small orange" onclick="op('sync',''+esc(c.id)+'')">更新</button></td>';
    t.appendChild(tr);
  });
}
async function op(kind, key, extra){
  const body={op:kind};
  if(kind==='open'||kind==='close'||kind==='sync'){ if(key==='selected') body.keys=selKeys(); else if(key&&key!=='all') body.keys=[key]; }
  if(kind==='new'){ body.name=document.getElementById('nname').value; body.emu=document.getElementById('nemo').value; }
  if(kind==='newbatch'){ body.keys=[...emuSel]; body.name=document.getElementById('bname').value; }
  if(kind==='bind'){ const t=prompt('输入模拟器索引或名称:'); if(!t)return; body.key=key; body.emu=t; }
  if(kind==='close'&&extra) body.kill_emu=true;
  await fetchAPI('/api/op', body);
  const iv=setInterval(async()=>{ const s=await fetchAPI('/api/state'); if(!s) return;
    document.getElementById('opmsg').textContent=s.running_op||'';
    if(!s.running_op){ clearInterval(iv); refresh(); } }, 1500);
}
function renderEmus(){
  const et=document.getElementById('emus'), sel=document.getElementById('nemo');
  const shown = sortShow(showfav ? emus.filter(e=>favs.includes(String(e.index))) : emus);
  document.getElementById('sidx').textContent = (sortKey==='idx')?(sortDir>0?'△':'▽'):'';
  document.getElementById('sname').textContent = (sortKey==='name')?(sortDir>0?'△':'▽'):'';
  document.getElementById('emucnt').textContent =
    shown.length+' 台'+(showfav?('（收藏 '+favs.length+' / 全部 '+emus.length+'）'):'');
  et.innerHTML=''; sel.innerHTML='';
  shown.forEach(e=>{
    const faved = favs.includes(String(e.index));
    const opt=document.createElement('option'); opt.value=e.index;
    opt.textContent=e.index+' '+e.name+(e.android?' (运行中)':''); sel.appendChild(opt);
    const st = e.android?'运行中':(e.proc?'启动中':'已关闭');
    const tr=document.createElement('tr');
    tr.innerHTML='<td><input type="checkbox" class="emuCk" data-idx="'+esc(e.index)+'"'+
      (emuSel.has(String(e.index))?' checked':'')+' onchange="emuCkChanged()"></td>'+
      '<td style="cursor:pointer" title="收藏" onclick="toggleFav(''+esc(e.index)+'')" class="'+(faved?'ok':'dim')+'">'+(faved?'★':'☆')+'</td>'+
      '<td>'+esc(e.index)+'</td><td>'+esc(e.name)+'</td>'+
      '<td><span class="'+(e.adb_up?'ok':'dim')+'" title="端口可连=✓">'+esc(e.adb_port)+' '+(e.adb_up?'✓':'✗')+'</span></td>'+
      '<td class="'+(e.android?'ok':'')+'">'+st+'</td>'+
      '<td><button class="small" onclick="emuOp(''+esc(e.index)+'','launch')">启动</button>'+
      '<button class="small gray" onclick="emuOp(''+esc(e.index)+'','shutdown')">停止</button></td>';
    et.appendChild(tr);
  });
}
async function emuOp(i,a){ await fetchAPI('/api/op',{op:'emuop',index:i,action:a}); refresh(); }
async function toggleFav(i){ const faved=favs.includes(i);
  await fetchAPI('/api/op',{op:'fav',index:i,fav:!faved}); refresh(); }
function emuCkChanged(){ let n=0;
  document.querySelectorAll('.emuCk').forEach(c=>{ if(c.checked){emuSel.add(c.dataset.idx);n++;} else emuSel.delete(c.dataset.idx); });
  document.getElementById('emusel').textContent=n; }
function ckAll(box){ const t=[...document.querySelectorAll('.emuCk')];
  t.forEach(c=>{ c.checked=box.checked; if(box.checked) emuSel.add(c.dataset.idx); else emuSel.delete(c.dataset.idx); });
  document.getElementById('emusel').textContent=box.checked?t.length:0; }
function selAllCopies(){ const t=[...document.querySelectorAll('.ck')]; const all=t.every(c=>c.checked);
  t.forEach(c=>c.checked=!all); }
function selKeys(){ return [...document.querySelectorAll('.ck:checked')].map(x=>x.dataset.id); }
setInterval(refresh, 5000); refresh();
