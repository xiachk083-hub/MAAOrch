// ── API ──
const API = '/api';
async function api(path, opts = {}) {
  try {
    const res = await fetch(API + path, {
      headers: { 'Content-Type': 'application/json', ...opts.headers },
      ...opts
    });
    return res.json();
  } catch(e) {
    return { ok: false, error: e.message || '网络错误' };
  }
}
async function apiGet(path) { return api(path); }
async function apiPost(path, body) { return api(path, { method: 'POST', body: JSON.stringify(body) }); }

// ── Render helpers ──
function showLoading(container, msg = '加载中...') {
  container.innerHTML = `<div style="text-align:center;padding:40px"><div class="spinner"></div><div style="color:var(--text3);font-size:12px;margin-top:8px">${msg}</div></div>`;
}
function showError(container, msg = '加载失败') {
  container.innerHTML = `<div style="text-align:center;padding:40px"><div style="font-size:32px;margin-bottom:8px">⚠</div><div style="color:var(--danger);font-size:12px">${msg}</div></div>`;
}
function showEmpty(container, msg = '暂无数据') {
  container.innerHTML = `<div style="text-align:center;padding:40px"><div style="font-size:32px;margin-bottom:8px">📭</div><div style="color:var(--text3);font-size:12px">${msg}</div></div>`;
}

// ── State ──
let state = { accounts: [], queue: [], config: {}, stats: {}, page: 'accounts', polling: false, aiInsights: [], _tableMode: false };
let filterKey = '';
let selectedIds = new Set();

// ── Toast ──
function toast(msg, type = 'info') {
  const container = document.getElementById('toast-container') || (() => {
    const c = document.createElement('div'); c.id = 'toast-container';
    document.body.appendChild(c); return c;
  })();
  const t = document.createElement('div');
  t.className = `toast ${type}`; t.textContent = msg;
  container.appendChild(t);
  setTimeout(() => t.remove(), 3000);
}
function showConfirm(msg) {
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.className = 'dialog-overlay';
    overlay.innerHTML = `<div class="dialog" style="max-width:360px;text-align:center">
      <div style="font-size:12px;color:var(--text2);margin-bottom:16px">${msg}</div>
      <div class="btn-row" style="justify-content:center">
        <button class="primary" id="confirm-yes">确定</button>
        <button id="confirm-no">取消</button>
      </div>
    </div>`;
    document.body.appendChild(overlay);
    overlay.querySelector('#confirm-yes').onclick = () => { overlay.remove(); resolve(true); };
    overlay.querySelector('#confirm-no').onclick = () => { overlay.remove(); resolve(false); };
    overlay.onclick = (e) => { if (e.target === overlay) { overlay.remove(); resolve(false); } };
  });
}
function showNotif(msg, type) {
  toast(msg, type === 'error' ? 'error' : 'info');
  try {
    if (Notification.permission === 'granted') {
      new Notification('MAAOrch', { body: msg, icon: '/favicon.ico' });
    }
  } catch(e) {}
}

// ── Dialog helper ──
function showDialog(html) {
  const overlay = document.createElement('div'); overlay.className = 'dialog-overlay';
  overlay.innerHTML = `<div class="dialog">${html}</div>`;
  overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
  document.body.appendChild(overlay);
  return overlay;
}
function closeDialog(el) { el.remove(); }

// ── Navigation ──
function navigate(page) {
  state.page = page;
  document.querySelectorAll('.nav-item').forEach(n => n.classList.toggle('active', n.dataset.page === page));
  document.getElementById('page-title').textContent = document.querySelector(`.nav-item[data-page="${page}"]`)?.textContent || page;
  // Auto-open secondary nav if the page is in the "more" section
  const inSecondary = document.querySelector(`.nav-secondary .nav-item[data-page="${page}"]`);
  const details = document.getElementById('nav-more');
  if (inSecondary && details) details.open = true;
  else if (details && details.open && !details.querySelector('.nav-item.active')) details.open = false;
  renderPage();
}

// ── Sidebar refresh ──
async function refreshSidebar() {
  try {
    const s = await apiGet('/status');
    const runningCount = s.accounts ? s.accounts.filter(a => a.running).length : 0;
    const q = await apiGet('/queue');
    document.getElementById('queue-summary').textContent = `运行: ${runningCount} | 队列: ${q.pending_count || 0}`;
    const accts = await apiGet('/accounts');
    if (accts.ok) {
      const vms = {};
      accts.accounts.forEach(a => {
        if (a.running && a.emu_instance_index) vms[a.emu_instance_index] = a.name;
      });
      const html = Object.keys(vms).length ? Object.entries(vms).map(([vm, name]) =>
        `<div style="color:var(--accent)">VM ${vm}: ${name.slice(0,8)}</div>`
      ).join('') : '<div style="color:var(--text3)">空闲</div>';
      document.getElementById('vm-status').innerHTML = html;
    }
  } catch(e) { /* ignore polling errors */ }
}

// ── Theme ──
function setTheme(theme) {
  document.documentElement.className = theme === 'Light' ? 'theme-light' : theme === 'Notepaper' ? 'theme-notepaper' : '';
  localStorage.setItem('theme', theme);
}
const savedTheme = localStorage.getItem('theme') || 'Dark';
setTheme(savedTheme);

// ── Page renderers ──
function renderPage() {
  const c = document.getElementById('content');
  showLoading(c);
  const fns = { accounts: renderAccounts, queue: renderQueue, stats: renderStats,
                settings: renderSettings, about: renderAbout, logs: renderLogs,
                account: renderAccount, taskcfg: renderTaskConfig, batch: renderBatchEdit,
                health: renderHealth, onboarding: renderOnboarding, dashboard: renderDashboard,
                gallery: renderGallery, chronicle: renderChronicle, nodes: renderNodes,
                emus: renderEmus };
  if (fns[state.page]) fns[state.page](c);
}
async function renderAccounts(container) {
  try {
    const r = await apiGet('/accounts');
    if (!r.ok) { showError(container, r.error); return; }
    state.accounts = r.accounts;
    // Load stage library for display
    (async () => { const sr = await apiGet('/stages'); if (sr.ok) window._stageLib = sr.stages || []; })();

    let searchText = (document.getElementById('search-input')?.value || '').toLowerCase();
    let serverFilter = document.getElementById('server-filter')?.value || 'all';
    let statusFilter = document.getElementById('status-filter')?.value || 'all';

    let filtered = r.accounts.filter(a => {
      const nameMatch = !searchText || a.name.toLowerCase().includes(searchText) || (a.note||'').toLowerCase().includes(searchText);
      if (serverFilter !== 'all') {
        const client = a.game_client || '';
        if (serverFilter === 'official' && client !== 'Official') return false;
        else if (serverFilter === 'bilibili' && client !== 'Bilibili') return false;
        else if (serverFilter === 'yostar' && !['YoStarEN','YoStarJP','YoStarKR','txwy'].includes(client)) return false;
        else if (serverFilter === 'other' && ['Official','Bilibili','YoStarEN','YoStarJP','YoStarKR','txwy'].includes(client)) return false;
      }
      if (statusFilter === 'running' && !a.running) return false;
      if (statusFilter === 'error' && !(!a.running && a.failures > 0 && a.failures < 6)) return false;
      if (statusFilter === 'paused' && !(a.failures >= 6)) return false;
      return true;
    });

    const batchCount = selectedIds.size;
    const total = r.accounts.length;
    const countOfficial = r.accounts.filter(a => a.game_client === 'Official').length;
    const countBili = r.accounts.filter(a => a.game_client === 'Bilibili').length;
    const countYoStar = r.accounts.filter(a => ['YoStarEN','YoStarJP','YoStarKR','txwy'].includes(a.game_client)).length;

    let html = `<div style="display:flex;gap:4px;margin-bottom:6px">
      <input type="text" id="search-input" placeholder="搜索名称或备注..."
        oninput="searchAccounts(this.value)"
        style="flex:1;padding:5px 8px;background:var(--bg3);border:1px solid var(--border);color:var(--text);border-radius:var(--radius);font-size:11px">
      <button onclick="showCreateAccountForm()" style="white-space:nowrap;font-size:11px">＋ 创建</button>
      <button onclick="toggleTableMode()" style="white-space:nowrap;font-size:11px">📊 表格</button>
    </div>
    <div style="display:flex;gap:3px;margin-bottom:4px;flex-wrap:wrap">
      <select id="server-filter" onchange="renderPage()" style="font-size:10px;padding:2px 6px;background:var(--bg3);border:1px solid var(--border);color:var(--text);border-radius:3px">
        <option value="all">全部 (${total})</option>
        <option value="official">官服 (${countOfficial})</option>
        <option value="bilibili">B服 (${countBili})</option>
        <option value="yostar">外服 (${countYoStar})</option>
      </select>
      <select id="status-filter" onchange="renderPage()" style="font-size:10px;padding:2px 6px;background:var(--bg3);border:1px solid var(--border);color:var(--text);border-radius:3px">
        <option value="all">全部状态</option>
        <option value="running">🟢 运行中</option>
        <option value="error">❌ 错误</option>
        <option value="paused">⏸ 暂停</option>
      </select>
    </div>
    <div class="card-list">`;
    filtered.forEach(a => {
      const isRunning = a.running;
      const failCount = a.failures || 0;
      const isError = !isRunning && failCount > 0 && failCount < 6;
      const isPaused = failCount >= 6;
      const dotColor = isRunning ? 'var(--accent)' : isError ? 'var(--danger)' : isPaused ? 'var(--text3)' : 'var(--border)';
      const dotLabel = isRunning ? '运行中' : isError ? '错误' : isPaused ? '暂停' : '空闲';
      const clientLabel = {'Official':'官服','Bilibili':'B服','YoStarEN':'美服','YoStarJP':'日服','YoStarKR':'韩服','txwy':'繁中服'}[a.game_client] || a.game_client || '?';
      // Expiry warning
      let expireStr = '';
      if (a.expire_date) {
        const d = new Date(a.expire_date), n = new Date();
        const diff = Math.ceil((d - n) / 86400000);
        if (diff < 0) expireStr = `<span style="color:var(--danger);font-size:9px">⚠ 过期${Math.abs(diff)}天</span>`;
        else if (diff <= 3) expireStr = `<span style="color:var(--warn);font-size:9px">⏰ ${diff}天</span>`;
      }
      const checked = selectedIds.has(a.id) ? 'checked' : '';

      html += `<div class="card" style="padding:4px 8px;gap:6px">
  <input type="checkbox" class="cb" ${checked} onchange="event.stopPropagation();toggleSelect('${a.id}')">
  <div style="flex:1;min-width:0;cursor:pointer" onclick="showAccountDetail('${a.id}')">
    <div style="display:flex;align-items:center;gap:4px">
      <span style="width:8px;height:8px;border-radius:50%;background:${dotColor};display:inline-block" title="${dotLabel}"></span>
      <span style="font-size:11px;font-weight:bold">${a.name}</span>
      <span style="font-size:8px;color:var(--text3);background:var(--bg3);padding:1px 4px;border-radius:2px">${clientLabel}</span>
      ${expireStr}
    </div>
    <div style="font-size:9px;color:var(--text3);margin-top:1px">VM ${a.emu_instance_index||'?'}${a.note ? ' · '+a.note : ''}</div>
    ${(function(){if(!a.stages||!a.stages.length)return '';var snames=a.stages.map(function(s){var lib=window._stageLib||[];var found=lib.find(function(l){return l.id===s});return found?found.name:s});return '<div style="font-size:8px;color:var(--accent);margin-top:1px">🎯 '+snames.slice(0,3).join(', ')+(snames.length>3?' +'+(snames.length-3):'')+'</div>'})()}
  </div>
  <div style="display:flex;align-items:center;gap:3px">
    <button class="small" onclick="event.stopPropagation();launchAccount('${a.id}')" style="font-size:9px;padding:1px 5px" title="启动">▶</button>
    <button class="small danger" onclick="event.stopPropagation();showConfirm('确认删除 ${a.name}？').then(r=>r&&deleteAccount('${a.id}'))" style="font-size:9px;padding:1px 5px" title="删除">🗑</button>
  </div>
</div>`;
    });
    html += '</div>';

    // Batch action bar (always visible)
    html += `<div id="batch-bar" style="display:flex;align-items:center;gap:6px;padding:6px 8px;margin-top:6px;background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius);font-size:11px">
      <span id="batch-count">已选 ${batchCount}</span>
      <span style="flex:1"></span>
      <button class="small" onclick="batchSmart()" ${batchCount===0?'disabled':''} style="${batchCount===0?'opacity:0.4':''}">▶ 调度</button>
      <button class="small" onclick="batchEnqueue()" ${batchCount===0?'disabled':''} style="${batchCount===0?'opacity:0.4':''}">入队</button>
      <button class="small" onclick="batchStop()" ${batchCount===0?'disabled':''} style="${batchCount===0?'opacity:0.4':''}">⏹ 停止</button>
      <button class="small" onclick="batchAssignStage()" ${batchCount===0?'disabled':''} style="${batchCount===0?'opacity:0.4':''}">🎯 关卡</button>
      <button class="small" onclick="batchDelete()" ${batchCount===0?'disabled':''} style="${batchCount===0?'opacity:0.4':''}">🗑 删除</button>
      <span style="width:1px;height:18px;background:var(--border);display:inline-block"></span>
      <button class="small" onclick="openCsvEdit()">📋 CSV 编辑</button>
    </div>`;

    container.innerHTML = html;
  } catch(e) { showError(container, e.message); }
}

let _tableData = []; let _tableStages = [];
async function toggleTableMode() {
  state._tableMode = !state._tableMode;
  if (!state._tableMode) { renderPage(); return; }
  const c = document.getElementById('content');
  showLoading(c);
  const r = await apiGet('/accounts');
  if (!r.ok) { showError(c, r.error); return; }
  state.accounts = r.accounts;
  await renderAccountTable(c, r.accounts);
}
let _tableContainer = null;
async function renderAccountTable(container, accts) {
  _tableContainer = container;
  try {
    _tableData = accts.map(a => ({ ...a, stages: [...(a.stages || [])] }));
    const sr = await apiGet('/stages');
    _tableStages = (sr.ok ? sr.stages || [] : []).map(s => s.id).filter(Boolean);
    _renderTable();
  } catch(e) {
    container.innerHTML = `<div style="padding:20px;color:var(--danger)">❌ 表格加载失败: ${e.message}</div>`;
  }
}
function _renderTable() {
  const container = _tableContainer;
  const accts = _tableData;
  const stageIds = _tableStages;
  const fields = ['名称','游戏客户端','模拟器索引','切换标识','UID','备注','过期日','已暂停','剿灭'];
  const eng = { '名称':'name','游戏客户端':'game_client','模拟器索引':'emu_instance_index','切换标识':'account_switch','UID':'uid','备注':'note','过期日':'expire_date','已暂停':'suspended','剿灭':'smart_annihilation' };
  
  let html = `<div style="overflow-x:auto;max-height:calc(100vh - 260px);overflow-y:auto;border:1px solid var(--border);border-radius:var(--radius);font-size:10px">
  <table style="width:100%;border-collapse:collapse;white-space:nowrap">
  <thead><tr style="position:sticky;top:0;background:var(--bg2);z-index:1">`;
  html += `<th style="padding:2px 4px;border:1px solid var(--border);text-align:center;width:22px"><input type="checkbox" onchange="document.querySelectorAll('.tb-cb').forEach(c=>c.checked=this.checked)"></th>`;
  html += `<th style="padding:2px 4px;border:1px solid var(--border)">ID</th>`;
  for (const f of fields) html += `<th style="padding:2px 4px;border:1px solid var(--border)">${f}</th>`;
  for (const s of stageIds) html += `<th style="padding:2px 4px;border:1px solid var(--border);text-align:center;font-weight:normal;color:var(--text3)">${s}</th>`;
  html += `<th style="padding:2px 4px;border:1px solid var(--border);text-align:center;width:24px" onclick="addTableRow()" title="新增行">＋</th>`;
  html += `</tr></thead><tbody>`;

  for (let i = 0; i < accts.length; i++) {
    const a = accts[i];
    html += `<tr>`;
    html += `<td style="padding:2px 4px;border:1px solid var(--border);text-align:center"><input type="checkbox" class="tb-cb" data-idx="${i}"></td>`;
    html += `<td style="padding:2px 4px;border:1px solid var(--border);color:var(--text3);font-size:9px">${(a.id||'').slice(0,8)}</td>`;
    for (const f of fields) {
      const key = eng[f];
      if (key === 'suspended') {
        const checked = a[key] ? 'checked' : '';
        html += `<td style="padding:2px 4px;border:1px solid var(--border);text-align:center"><input type="checkbox" ${checked} onchange="editTableVal(${i},'${key}',this.checked)"></td>`;
      } else if (key === 'smart_annihilation') {
        const v = a[key] || '';
        html += `<td style="padding:2px 4px;border:1px solid var(--border)"><select onchange="editTableVal(${i},'${key}',this.value)" style="border:none;background:var(--bg3);color:var(--text);font-size:10px;outline:none;padding:1px 2px;border-radius:2px;cursor:pointer">`;
        html += `<option value="">（留空=不跑剿灭）</option>`;
        const _anniStages = ['Annihilation','龙门外环@Annihilation','龙门市区@Annihilation','切尔诺伯格@Annihilation'];
        for (const o of _anniStages) html += `<option value="${o}" ${o===v?'selected':''}>${o}</option>`;
        html += `</select></td>`;
      } else if (key === 'game_client') {
        const opts = ['Official','Bilibili','YoStarEN','YoStarJP','YoStarKR','txwy'];
        const v = a[key] || 'Official';
        html += `<td style="padding:2px 4px;border:1px solid var(--border)"><select onchange="editTableVal(${i},'${key}',this.value)" style="border:none;background:var(--bg3);color:var(--text);font-size:10px;outline:none;padding:1px 2px;border-radius:2px;cursor:pointer">`;
        for (const o of opts) {
          html += `<option value="${o}" ${o===v?'selected':''}>${o}</option>`;
        }
        html += `</select></td>`;
      } else {
        const val = a[key] !== undefined && a[key] !== null ? String(a[key]).replace(/"/g,'&quot;') : '';
        html += `<td style="padding:2px 4px;border:1px solid var(--border)"><input type="text" value="${val}" onchange="editTableVal(${i},'${key}',this.value)" style="min-width:30px;max-width:150px;border:none;background:transparent;color:var(--text);font-size:10px;outline:none"></td>`;
      }
    }
    for (const s of stageIds) {
      const checked = a.stages.includes(s) ? 'checked' : '';
      html += `<td style="padding:2px 4px;border:1px solid var(--border);text-align:center"><input type="checkbox" ${checked} onchange="editTableStage(${i},'${s}',this.checked)"></td>`;
    }
    html += `<td style="padding:2px 4px;border:1px solid var(--border);text-align:center"><span style="cursor:pointer;color:var(--danger)" onclick="deleteTableRow(${i})">✕</span></td>`;
    html += `</tr>`;
  }
  html += `</tbody></table></div>`;
  html += `<div style="display:flex;gap:6px;margin-top:6px;justify-content:flex-end;font-size:11px">
    <button class="small" onclick="addTableRow()">＋ 新增行</button>
    <button class="small" onclick="addTableStage()">＋ 添加关卡列</button>
    <button class="small danger" onclick="deleteSelectedRows()" id="tb-del-btn" disabled>🗑 删除选中</button>
    <span style="flex:1"></span>
    <button class="small" onclick="state._tableMode=false;renderPage()">取消</button>
    <button class="small" style="background:var(--accent);color:#fff" onclick="saveTable()">💾 保存表格</button>
  </div>`;
  container.innerHTML = html;
}
function editTableVal(idx, key, val) {
  if (key === 'suspended') _tableData[idx][key] = val;
  else _tableData[idx][key] = val;
}
function editTableStage(idx, stageId, checked) {
  const a = _tableData[idx];
  if (checked) { if (!a.stages.includes(stageId)) a.stages.push(stageId); }
  else { a.stages = a.stages.filter(s => s !== stageId); }
}
function addTableRow() {
  _tableData.push({ id: '', name: '', game_client: 'Official', emu_instance_index: '', account_switch: '', uid: '', note: '', expire_date: '', suspended: false, stages: [], smart_annihilation: '' });
  _renderTable();
}
function deleteTableRow(idx) {
  _tableData.splice(idx, 1);
  _renderTable();
}
function deleteSelectedRows() {
  const cbs = document.querySelectorAll('.tb-cb:checked');
  const indices = Array.from(cbs).map(cb => parseInt(cb.dataset.idx)).filter(i => !isNaN(i)).sort((a,b) => b-a);
  for (const i of indices) _tableData.splice(i, 1);
  _renderTable();
}
function addTableStage() {
  const name = prompt('输入新关卡 ID（如 2-7）:');
  if (!name || !name.trim()) return;
  if (!_tableStages.includes(name.trim())) _tableStages.push(name.trim());
  _renderTable();
}
async function saveTable() {
  // Collect new stages that aren't in library yet
  const sr = await apiGet('/stages');
  const existing = (sr.ok ? sr.stages || [] : []).map(s => s.id);
  const newStages = _tableStages.filter(s => !existing.includes(s));
  // Build accounts payload
  const accounts = _tableData.map(a => {
    const out = { id: a.id };
    for (const f of ['name','game_client','emu_instance_index','account_switch','uid','note','expire_date','suspended','smart_annihilation']) {
      if (f === 'suspended') out[f] = !!a[f];
      else out[f] = a[f] !== undefined ? String(a[f]) : '';
    }
    out.stages = a.stages || [];
    return out;
  });
  const r = await apiPost('/accounts/batch_save', { accounts, new_stages: newStages });
  if (r.ok) {
    let msg = '';
    if (r.updated) msg += '更新 ' + r.updated + ' 个';
    if (r.created) msg += (msg?', ':'') + '新增 ' + r.created + ' 个';
    toast(msg || '保存完成');
    state._tableMode = false;
    renderPage();
  } else {
    toast(r.error || '保存失败', 'error');
  }
}

async function renderQueue(container) {
  try {
    const q = await apiGet('/queue');
    const s = await apiGet('/status');
    const accts = state.accounts.length ? state.accounts : (await apiGet('/accounts')).ok ? (await apiGet('/accounts')).accounts : [];

    // Build active (running) list
    let activeHtml = '';
    const activeIds = q.active || [];
    if (activeIds.length > 0) {
      activeHtml = `<div style="color:var(--accent);font-size:11px;font-weight:bold;padding:4px 0;margin-top:4px">── 进行中 ──</div>`;
      activeIds.forEach(aid => {
        const a = accts.find(x => x.id === aid);
        const name = a?.name || aid.slice(0,8);
        const vm = a?.emu_instance_index || '?';
        activeHtml += `<div class="queue-item" style="border-color:var(--accent)">
          <span class="name">▶ ${name}</span>
          <span class="source">VM ${vm}</span>
          <span class="eta" style="color:var(--accent)">运行中</span>
        </div>`;
      });
    }

    // Pending list
    let pendingHtml = '';
    if (q.pending && q.pending.length) {
      pendingHtml = `<div style="color:var(--warn);font-size:11px;font-weight:bold;padding:4px 0;margin-top:4px">── 排队中 ──</div>`;
      pendingHtml += q.pending.map(e => {
        const srcMap = {manual:'手动', schedule:'定时', sanity:'理智', force:'强制', retry:'重试'};
        return `<div class="queue-item">
          <span class="name">⏳ ${e.account_name || e.account_id?.slice(0,8) || '?'}</span>
          <span class="source">${srcMap[e.source]||e.source}</span>
          <span class="eta">${e.not_before ? new Date(e.not_before).toLocaleTimeString() : '等待中'}</span>
        </div>`;
      }).join('');
    }

    container.innerHTML = `<div style="margin-bottom:8px">
      <span>运行中: <strong>${activeIds.length}</strong></span>
      <span style="margin-left:16px">排队: <strong>${q.pending_count || 0}</strong></span>
      <button onclick="toggleQueuePause()" style="margin-left:16px">${q.paused ? '▶ 恢复队列' : '⏸ 暂停队列'}</button>
      <button onclick="clearQueue()" style="margin-left:8px" class="danger small">清空队列</button>
    </div>
    <div id="queue-list">${activeHtml}${pendingHtml || '<div style="color:var(--text3);padding:20px;text-align:center;margin-top:8px">队列为空</div>'}</div>`;
  } catch(e) { showError(container, e.message); }
}

async function renderSettings(container) {
  try {
    const r = await apiGet('/config');
    const cfg = r.config || {};
    const sr = await apiGet('/settings/smart');
    const smart = sr.smart_global || {};
    container.innerHTML = `<div class="tabs" id="settings-tabs">
      <div class="tab active" data-tab="general">通用</div>
      <div class="tab" data-tab="smart">智能调度</div>
      <div class="tab" data-tab="ai">AI 分析</div>
      <div class="tab" data-tab="notify">通知</div>
      <div class="tab" data-tab="maa">MAA 实例</div>
      <div class="tab" data-tab="stages">关卡仓库</div>
    </div>
    <div class="tab-content active" id="tab-general">
      <div class="form-row"><label>主题</label><select id="sel-theme" onchange="setTheme(this.value)">
        <option value="Dark">暗色</option><option value="Light">亮色</option><option value="Notepaper">Notepaper</option>
      </select></div>
      <div class="form-row"><label>并行上限</label>
  <div style="display:flex;align-items:center;gap:6px">
    <input type="range" id="input-parallel" value="${cfg.parallel_max||1}" min="1" max="30" oninput="document.getElementById('parallel-val').textContent=this.value" style="flex:1;height:4px">
    <span id="parallel-val" style="min-width:24px;font-weight:bold;color:var(--accent)">${cfg.parallel_max||1}</span>
  </div>
  <div style="display:flex;justify-content:space-between;font-size:8px;color:var(--text3);padding:0 2px;margin-top:-2px">
    <span>1</span><span></span><span>5</span><span></span><span>10</span><span></span><span>15</span><span></span><span>20</span><span></span><span>25</span><span></span><span>30</span>
  </div>
</div>
      <div class="form-row"><label>调度模式</label><select id="sel-mode">
        <option value="daily" ${cfg.schedule_mode==='daily'?'selected':''}>日常</option>
        <option value="roguelike" ${cfg.schedule_mode==='roguelike'?'selected':''}>肉鸽</option>
        <option value="reclamation" ${cfg.schedule_mode==='reclamation'?'selected':''}>生息</option>
      </select></div>
      <div class="form-row"><label>API 端口</label><input type="number" id="input-port" value="${cfg.api_port||19999}"></div>
      <div class="form-row"><label>绑定地址</label><input type="text" id="input-bind" value="${cfg.bind_address||'127.0.0.1'}" placeholder="127.0.0.1" style="font-size:10px">
        <span style="color:var(--text3);font-size:9px">0.0.0.0 允许远程访问</span></div>
      <div class="form-row"><label>Webhook</label><input type="text" id="input-webhook" value="${cfg.webhook_url||''}" placeholder="https://example.com/webhook" style="font-size:10px">
        <span style="color:var(--text3);font-size:9px">任务完成时推送</span></div>
      <div class="btn-row"><button class="primary" onclick="saveGeneral()">保存</button></div>
    </div>
    <div class="tab-content" id="tab-smart">
      <div class="form-row"><label>智能调度</label>
        <label style="color:var(--text2);font-size:12px"><input type="checkbox" id="cb-smart" ${smart.enabled?'checked':''}> 启用</label></div>
      <div class="form-row"><label>体力阈值</label><input type="number" id="input-threshold" value="${smart.threshold||80}" min="0" max="200"> %</div>
      <div class="form-row"><label>理智缺口</label><input type="number" id="input-deficit" value="${cfg.deficit??0}" min="0" max="200"> (小于此值自动入队)</div>
      <div class="form-row"><label>卡死超时</label><input type="number" id="input-stuck" value="${cfg.stuck_timeout||10}" min="0" max="60"> 分钟</div>
      <div class="form-row"><label>日常定时</label><input type="time" id="input-batch-time" value="${cfg.daily_batch_time||'08:00'}"></div>
      <div class="form-row"><label>过期药</label><label><input type="checkbox" id="cb-exp-med" ${smart.expiring_medicine?'checked':''}> 优先吃快过期药</label></div>
      <div class="form-row"><label>剿灭</label><label><input type="checkbox" id="cb-anni" ${smart.annihilation_enabled!==false?'checked':''}> 启用自动剿灭</label></div>
      <div class="form-row"><label>自动招募</label><label><input type="checkbox" id="cb-recruit" ${smart.recruit_enabled!==false?'checked':''}> 启用</label></div>
      <div class="form-row"><label>自动商店</label><label><input type="checkbox" id="cb-mall" ${smart.mall_enabled!==false?'checked':''}> 启用</label></div>
      <div class="btn-row"><button class="primary" onclick="saveSmart()">保存</button></div>
    </div>
    <div class="tab-content" id="tab-ai">
      <div class="form-row"><label>自动分析</label>
        <label style="color:var(--text2);font-size:12px"><input type="checkbox" id="cb-ai-auto" ${cfg.ai_auto_analyze?'checked':''}> 任务失败时自动 AI 分析</label></div>
      <div class="form-row"><label>接口</label><select id="sel-ai-provider" onchange="onAIProviderChange()">
        <option value="openai" ${cfg.ai_provider==='openai'?'selected':''}>OpenAI</option>
        <option value="deepseek" ${cfg.ai_provider==='deepseek'?'selected':''}>DeepSeek</option>
        <option value="qwen" ${cfg.ai_provider==='qwen'?'selected':''}>通义千问</option>
        <option value="siliconflow" ${cfg.ai_provider==='siliconflow'?'selected':''}>硅基流动</option>
        <option value="custom" ${cfg.ai_provider==='custom'?'selected':''}>自定义</option>
      </select></div>
      <div class="form-row"><label>API Key</label><input type="password" id="input-ai-key" value="${cfg.ai_api_key||''}" placeholder="sk-..." style="font-size:10px"></div>
      <div class="form-row"><label>接口地址</label><input type="text" id="input-ai-endpoint" value="${cfg.ai_endpoint||''}" placeholder="https://api.openai.com/v1/chat/completions" style="font-size:10px"></div>
      <div class="form-row"><label>模型</label><input type="text" id="input-ai-model" value="${cfg.ai_model||''}" placeholder="gpt-4o-mini" style="font-size:10px"></div>
      <div class="btn-row"><button class="primary" onclick="saveAI()">保存</button></div>
    </div>
    <div class="tab-content" id="tab-notify">
      <div class="form-row"><label>Webhook</label><input type="text" id="input-webhook2" value="${cfg.webhook_url||''}" placeholder="https://example.com/webhook" style="font-size:10px">
        <span style="color:var(--text3);font-size:9px">任务完成时 HTTP POST 推送</span></div>
      <div style="border-top:1px solid var(--border);margin:8px 0;padding-top:8px">
        <div style="font-size:11px;color:var(--text2);margin-bottom:6px">Telegram</div>
        <div class="form-row"><label>Bot Token</label><input type="password" id="input-tg-token" value="${cfg.tg_token||''}" placeholder="123456:ABCdef..." style="font-size:10px">
          <span style="color:var(--text3);font-size:9px">@BotFather 创建</span></div>
        <div class="form-row"><label>Chat ID</label><input type="text" id="input-tg-chat" value="${cfg.tg_chat_id||''}" placeholder="123456789" style="font-size:10px">
          <span style="color:var(--text3);font-size:9px">向 @userinfobot 发 /start 获取</span></div>
        <div style="font-size:9px;color:var(--text3);margin-top:2px">任务失败时自动发送告警到 Telegram</div>
      </div>
      <div class="btn-row"><button class="primary" onclick="saveNotify()">保存</button></div>
    </div>
    <div class="tab-content" id="tab-maa">
      <div class="form-row"><label>MAA 版本</label><span style="color:var(--text2);font-size:12px">${cfg.maa_version||'未安装'}</span></div>
      <div class="form-row"><label>实例数</label><span style="color:var(--text2);font-size:12px">${cfg.maa_instances||0}</span></div>
      <div class="btn-row"><button onclick="rebuildInstances()">🔄 重建实例</button><button onclick="checkMaaUpdate()" id="btn-maa-update" style="margin-left:8px">📥 检查更新</button><button onclick="downloadLogs()" style="margin-left:8px">📦 导出日志</button><button onclick="exportConfig()" style="margin-left:8px">📤 导出配置</button><button onclick="showImportConfig()" style="margin-left:8px">📥 导入配置</button><button onclick="restartMAAOrch()" style="margin-left:8px;color:var(--warn)">🔄 重启服务</button></div>
      <div id="maa-update-result" style="font-size:10px;color:var(--text3);margin-top:4px"></div>
    </div>
    <div class="tab-content" id="tab-stages">
      <div id="stage-lib-content"></div>
    </div>`;
    // Tab switching
    container.querySelectorAll('.tab').forEach(tab => {
      tab.addEventListener('click', () => {
        container.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        container.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        const tc = document.getElementById('tab-' + tab.dataset.tab);
        if (tc) tc.classList.add('active');
        if (tab.dataset.tab === 'stages') renderStageLibrary();
      });
    });
    // Pre-load stages content in background
    renderStageLibrary();
  } catch(e) { showError(container); }
}
// ── Stage Library ──
var _stageLibCache = null;
var _stageAccountsCache = null;
async function renderStageLibrary() {
  try {
    _stageLibCache = await apiGet('/stages');
    if (!_stageLibCache.ok) { _stageLibCache = null; return; }
    const stages = _stageLibCache.stages || [];
    // Fetch accounts for assignment
    _stageAccountsCache = await apiGet('/accounts');
    const allAccts = (_stageAccountsCache.ok ? _stageAccountsCache.accounts : []) || [];
    // Find or create the container
    let el = document.getElementById('stage-lib-content');
    if (!el) {
      var tc = document.getElementById('tab-stages');
      if (!tc) return;
      el = document.createElement('div'); el.id = 'stage-lib-content';
      tc.appendChild(el);
    }
    let html = '<div style="display:flex;gap:4px;margin-bottom:6px">';
    html += '<button class="small primary" onclick="addStage()">＋ 新建关卡</button>';
    html += '<span style="flex:1"></span>';
    html += '<button class="small" onclick="saveStageLibrary()">💾 保存</button></div>';
    html += '<div class="card-list" id="stage-card-list">';
    stages.forEach(function(st, i) {
      var assignedIds = (st.account_ids || []);
      var assignedAccts = allAccts.filter(function(a){return assignedIds.indexOf(a.id)>=0});
      html += '<div class="card" style="padding:5px 8px;flex-direction:column;align-items:stretch;margin-bottom:4px">';
      // Stage name row
      html += '<div style="display:flex;align-items:center;gap:4px;margin-bottom:4px">';
      html += '<input type="text" class="stage-name" data-idx="'+i+'" value="'+(st.name||'')+'" style="flex:1;padding:2px 6px;background:var(--bg3);border:1px solid var(--border);color:var(--text);border-radius:3px;font-size:12px;font-weight:bold" placeholder="关卡名">';
      html += '<span style="font-size:9px;color:var(--text3)">'+assignedAccts.length+'个</span>';
      html += '<button class="small" onclick="removeStage('+i+')" style="font-size:9px;padding:1px 5px;color:var(--danger)">✕</button>';
      html += '</div>';
      html += '<input type="text" class="stage-note" data-idx="'+i+'" value="'+(st.note||'')+'" style="width:100%;margin-bottom:4px;padding:2px 6px;background:var(--bg3);border:1px solid var(--border);color:var(--text2);border-radius:3px;font-size:10px" placeholder="备注(可选)">';
      html += '<div style="display:flex;gap:4px;margin-bottom:4px;align-items:center">';
      html += '<input type="time" class="stage-available-from" data-idx="'+i+'" value="'+(st.available_from||'')+'" style="width:90px;padding:2px 4px;background:var(--bg3);border:1px solid var(--border);color:var(--text);border-radius:3px;font-size:10px" title="开始时间">';
      html += '<span style="font-size:9px;color:var(--text3)">→</span>';
      html += '<input type="time" class="stage-available-until" data-idx="'+i+'" value="'+(st.available_until||'')+'" style="width:90px;padding:2px 4px;background:var(--bg3);border:1px solid var(--border);color:var(--text);border-radius:3px;font-size:10px" title="结束时间">';
      html += '<span style="font-size:9px;color:var(--text3)">可刷时段(留空不限)</span>';
      html += '</div>';
      // Account list for this stage
      html += '<div style="display:flex;gap:2px;flex-wrap:wrap">';
      allAccts.forEach(function(a) {
        var has = assignedIds.indexOf(a.id) >= 0;
        var label = {'Official':'官','Bilibili':'B','YoStarEN':'美','YoStarJP':'日','YoStarKR':'韩','txwy':'繁'}[a.game_client] || a.game_client || '?';
        html += '<span onclick="toggleStageAccount(\''+st.id+'\',\''+a.id+'\')" style="font-size:9px;padding:1px 5px;border-radius:3px;cursor:pointer;background:'+(has?'var(--accent)':'var(--bg3)')+';color:'+(has?'#fff':'var(--text3)')+';border:1px solid '+(has?'var(--accent)':'var(--border)')+'">'+label+' '+a.name.slice(0,4)+'</span>';
      });
      html += '</div></div>';
    });
    html += '</div>';
    if (!stages.length) html += '<div style="color:var(--text3);text-align:center;padding:20px;font-size:11px">暂无关卡，点击上方按钮创建</div>';
    el.innerHTML = html;
  } catch(e) {}
}
async function toggleStageAccount(stageId, accountId) {
  var toggle = true;
  // Check if currently assigned
  var accts = (_stageAccountsCache && _stageAccountsCache.accounts) || [];
  var a = accts.find(function(x){return x.id===accountId});
  if (a && a.stages && a.stages.indexOf(stageId) >= 0) toggle = false;
  await apiPost('/stages/apply', {stage_id:stageId,account_ids:[accountId],toggle:toggle});
  _stageLibCache = null;
  renderStageLibrary();
}
function addStage() {
  const el = document.getElementById('stage-card-list') || document.getElementById('stage-lib-content');
  if (!el) return;
  const div = document.createElement('div');
  div.className = 'card';
  div.style.cssText = 'padding:5px 8px;margin-top:4px';
  _stageIdCounter++;
  div.innerHTML = '<div style="flex:1;min-width:0"><div style="display:flex;align-items:center;gap:4px">'
    + '<input type="text" class="stage-name" value="" style="flex:1;padding:2px 6px;background:var(--bg3);border:1px solid var(--accent);color:var(--text);border-radius:3px;font-size:12px" placeholder="关卡名如 1-7">'
    + '<button class="small" onclick="this.parentElement.parentElement.parentElement.remove()" style="font-size:9px;padding:1px 5px;color:var(--danger)">✕</button></div>'
    + '<input type="text" class="stage-note" value="" style="width:100%;margin-top:2px;padding:2px 6px;background:var(--bg3);border:1px solid var(--border);color:var(--text2);border-radius:3px;font-size:10px" placeholder="备注(可选)">'
    + '<div style="display:flex;gap:4px;margin-top:2px;align-items:center">'
    + '<input type="time" class="stage-available-from" value="" style="width:90px;padding:2px 4px;background:var(--bg3);border:1px solid var(--border);color:var(--text);border-radius:3px;font-size:10px" title="开始时间">'
    + '<span style="font-size:9px;color:var(--text3)">→</span>'
    + '<input type="time" class="stage-available-until" value="" style="width:90px;padding:2px 4px;background:var(--bg3);border:1px solid var(--border);color:var(--text);border-radius:3px;font-size:10px" title="结束时间">'
    + '<span style="font-size:9px;color:var(--text3)">可刷时段</span>'
    + '</div>'
    + '</div>';
  if (el.firstChild) el.insertBefore(div, el.firstChild);
  else el.appendChild(div);
  div.querySelector('.stage-name').focus();
}
function removeStage(idx) {
  const el = document.getElementById('stage-lib-content');
  if (!el) return;
  const cards = el.querySelectorAll('.stage-name');
  if (cards[idx]) cards[idx].closest('.card')?.remove();
}
async function saveStageLibrary() {
  const el = document.getElementById('stage-lib-content');
  if (!el) return;
  const names = el.querySelectorAll('.stage-name');
  const notes = el.querySelectorAll('.stage-note');
  const availableFrom = el.querySelectorAll('.stage-available-from');
  const availableUntil = el.querySelectorAll('.stage-available-until');
  const stages = [];
  const usedIds = new Set();
  names.forEach((input, i) => {
    const name = input.value.trim();
    if (!name) return;
    // Collect existing data from the in-page data attributes
    let sid = input.dataset.idx || 'new';
    // Generate stable IDs
    let id = 's' + (i + 1);
    while (usedIds.has(id)) id = 's' + (id.slice(1) - 0 + 1);
    usedIds.add(id);
    const note = notes[i] ? notes[i].value.trim() : '';
    stages.push({id, name, note, available_from: availableFrom[i]?.value?.trim() || '', available_until: availableUntil[i]?.value?.trim() || ''});
  });
  const r = await apiPost('/stages', {stages});
  if (r.ok) toast('关卡仓库已保存');
  else toast(r.error || '保存失败', 'error');
  _stageLibCache = null;
  renderStageLibrary();
}
function renderOnboarding(container) {
  fetch('pages/onboarding.html').then(r => r.text()).then(html => {
    container.innerHTML = html;
  });
}

async function openMaaFolder() {
  const r = await apiPost('/system/open_folder', { path: '/services/maa/source' });
  if (!r.ok) toast(r.error || '无法打开文件夹', 'error');
}

async function finishOnboarding() {
  const done = document.getElementById('onboarding-done')?.checked || false;
  if (done) {
    await apiPost('/config', { onboarding_done: true });
  }
  navigate('accounts');
}

function renderAbout(container) {
  container.innerHTML = `<div class="about-version">MAAOrch</div>
  <div class="about-info">多账号 MAA 编排调度器<br><br>
  Python + PySide6 + Web UI<br>
  <a href="https://github.com/xiachk083-hub/MAAOrch" target="_blank" style="color:var(--accent)">GitHub</a><br><br>
  MAA v6 兼容 | 开源软件 (MIT)</div>
<div style="margin-top:8px"><button class="small" onclick="checkOrchUpdate()" id="btn-orch-update">📥 检查 MAAOrch 更新</button>
  <span id="orch-update-result" style="font-size:10px;color:var(--text3);margin-left:8px"></span></div>
<div style="margin-top:16px;border-top:1px solid var(--border);padding-top:12px">
  <div style="font-size:12px;color:var(--text2);margin-bottom:4px">快捷键</div>
  <div style="font-size:11px;color:var(--text3);line-height:1.8">
    Ctrl+Enter — 一键调度<br>
    Esc — 停止全部<br>
    Alt+D — 调度台<br>
    Alt+Q — 队列<br>
    Alt+A — 账号<br>
    Alt+S — 设置
  </div>
</div>
<div id="oplog-section" style="margin-top:12px;border-top:1px solid var(--border);padding-top:8px">
  <div style="font-size:12px;color:var(--text2);margin-bottom:4px">操作记录</div>
  <div id="oplog-list" style="font-size:10px;color:var(--text3);max-height:300px;overflow-y:auto"></div>
</div>`;
  loadOplog();
}
async function checkOrchUpdate() {
  const btn = document.getElementById('btn-orch-update');
  const result = document.getElementById('orch-update-result');
  if (btn) { btn.textContent = '检查中...'; btn.disabled = true; }
  try {
    const r = await apiGet('/orch/check_update');
    if (r.ok && r.latest) {
      result.innerHTML = `最新版 <b>${r.latest}</b> <a href="${r.html_url}" target="_blank">下载</a>`;
    } else {
      result.textContent = '检查失败';
    }
  } catch(e) { result.textContent = '网络错误'; }
  if (btn) { btn.textContent = '📥 检查 MAAOrch 更新'; btn.disabled = false; }
}
async function loadOplog() {
  try {
    const r = await apiGet('/oplog');
    const el = document.getElementById('oplog-list');
    if (!el) return;
    if (r.ok && r.ops && r.ops.length) {
      el.innerHTML = r.ops.reverse().map(o =>
        `<div style="padding:2px 0;border-bottom:1px solid var(--border)"><span style="color:var(--text3)">${o.ts}</span> ${o.action}${o.detail ? ' · ' + o.detail : ''}</div>`
      ).join('');
    } else {
      el.innerHTML = '<div style="color:var(--text3);text-align:center;padding:8px">暂无记录</div>';
    }
  } catch(e) {}
}

// ── Stats dashboard (heatmap + charts) ──
async function renderStats(container) {
  container.innerHTML = `<div class="stats-page">
    <div style="margin-bottom:8px;display:flex;align-items:center;gap:8px;flex-wrap:wrap">
      <span style="color:var(--text2);font-size:12px;font-weight:bold">统计</span>
      <button class="small" onclick="refreshStats()">刷新</button>
    </div>
    <div id="stats-content"></div>
  </div>`;
  await refreshStats();
  if (logTimer) clearInterval(logTimer);
  logTimer = setInterval(() => {
    if (state.page !== 'stats') { clearInterval(logTimer); logTimer = null; return; }
    refreshStats();
  }, 15000);
}
async function refreshStats() {
  try {
    const r = await apiGet('/stats/dashboard');
    const el = document.getElementById('stats-content');
    if (!el) return;
    const s = r.summary || {};
    let html = '';
    // Summary cards
    html += `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px;margin-bottom:12px">`;
    html += _sc(s.total_runs||0, '累计运行', 'var(--accent)');
    html += _sc(s.today_runs||0, '今日运行', '#4caf50');
    html += _sc(s.total_drops||0, '累计掉落', 'var(--warn)');
    html += _sc(s.accounts||0, '账号数', 'var(--text)');
    html += `</div>`;
    // Heatmap
    html += `<div class="card" style="padding:12px;margin-bottom:12px"><div style="font-size:12px;font-weight:bold;margin-bottom:8px;color:var(--text2)">运行时热力图</div>`;
    html += _renderHeatmap(r.heatmap||[], r.weekdays||[]);
    html += `</div>`;
    // Material chart
    const topMats = r.top_materials || [];
    if (topMats.length) {
      html += `<div class="dash-grid-2" style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px">`;
      html += `<div class="card" style="padding:12px"><div style="font-size:12px;font-weight:bold;margin-bottom:8px;color:var(--text2)">材料增长</div>`;
      html += _renderMatChart(r.daily_drops||{}, topMats);
      html += `</div>`;
      html += `<div class="card" style="padding:12px"><div style="font-size:12px;font-weight:bold;margin-bottom:8px;color:var(--text2)">每日趋势</div>`;
      html += _renderDailyTrend(r.daily_runs||{});
      html += `</div>`;
      html += `</div>`;
    } else {
      html += `<div style="color:var(--text3);text-align:center;padding:20px;font-size:12px">暂无数据，运行后自动生成</div>`;
    }
    el.innerHTML = html;
  } catch(e) { /* silent */ }
}
function _sc(val, label, color) {
  return `<div class="card" style="padding:10px;text-align:center"><div style="font-size:20px;font-weight:bold;color:${color}">${val}</div><div style="font-size:10px;color:var(--text3)">${label}</div></div>`;
}
// ── Heatmap 7×24 ──
function _renderHeatmap(hm, weekdays) {
  const allZero = hm.every(row => row.every(v => v === 0));
  if (!hm.length || allZero) return '<div style="color:var(--text3);font-size:10px;text-align:center;padding:10px">暂无运行数据</div>';
  const maxVal = Math.max(1, ...hm.flat());
  const getColor = v => {
    if (v === 0) return 'var(--bg3)';
    const i = Math.min(v / maxVal, 1);
    const r = Math.round(10 + 200 * i);
    const g = Math.round(140 - 100 * i);
    const b = Math.round(60 - 40 * i);
    return `rgb(${r},${g},${b})`;
  };
  let html = `<div style="display:flex;gap:2px;font-size:9px;line-height:1">`;
  // hour labels
  html += `<div style="width:28px"></div>`;
  for (let h = 0; h < 24; h++) {
    html += `<div style="flex:1;text-align:center;color:var(--text3)">${h}</div>`;
  }
  html += `</div>`;
  for (let d = 0; d < 7; d++) {
    html += `<div style="display:flex;gap:2px;align-items:center;height:16px;margin-top:2px">`;
    html += `<div style="width:28px;font-size:9px;color:var(--text3)">${weekdays[d]}</div>`;
    for (let h = 0; h < 24; h++) {
      const v = hm[d][h] || 0;
      const color = getColor(v);
      html += `<div title="${weekdays[d]} ${h}:00 — ${v}次" style="flex:1;height:14px;border-radius:2px;background:${color};cursor:pointer"></div>`;
    }
    html += `</div>`;
  }
  return html;
}
// ── Material growth line chart (SVG) ──
function _renderMatChart(dailyDrops, topMats) {
  const dates = Object.keys(dailyDrops).sort();
  if (!dates.length || !topMats.length) return '<div style="color:var(--text3);font-size:10px">暂无数据</div>';
  const colors = ['#e74c3c','#3498db','#2ecc71','#f39c12','#9b59b6','#1abc9c','#e67e22','#34495e','#7f8c8d','#c0392b'];
  // Compute cumulative per material
  const cum = {};
  for (const mat of topMats) cum[mat] = [];
  let running = {};
  for (const mat of topMats) running[mat] = 0;
  for (const d of dates) {
    for (const mat of topMats) {
      running[mat] += (dailyDrops[d][mat] || 0);
      cum[mat].push(running[mat]);
    }
  }
  const maxY = Math.max(1, ...topMats.map(m => running[m]));
  const W = 500, H = 180, pad = {t:10,r:10,b:30,l:40};
  const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
  const xs = iw / Math.max(dates.length - 1, 1);
  let svg = `<svg width="${W}" height="${H}" style="width:100%;height:auto;max-height:200px;background:transparent">`;
  // grid lines
  for (let y = 0; y <= 4; y++) {
    const yy = pad.t + ih * (1 - y/4);
    svg += `<line x1="${pad.l}" y1="${yy}" x2="${W-pad.r}" y2="${yy}" stroke="var(--border)" stroke-width="0.5"/>`;
    svg += `<text x="${pad.l-4}" y="${yy+3}" fill="var(--text3)" font-size="8" text-anchor="end">${Math.round(maxY * y / 4)}</text>`;
  }
  // lines
  for (let mi = 0; mi < Math.min(topMats.length, 6); mi++) {
    const mat = topMats[mi];
    const pts = cum[mat];
    let path = '';
    for (let i = 0; i < pts.length; i++) {
      const x = pad.l + i * xs;
      const y = pad.t + ih * (1 - pts[i] / maxY);
      path += (i === 0 ? 'M' : 'L') + x.toFixed(1) + ',' + y.toFixed(1);
    }
    svg += `<path d="${path}" fill="none" stroke="${colors[mi]}" stroke-width="1.5" stroke-linejoin="round"/>`;
  }
  svg += `</svg>`;
  // Legend
  for (let mi = 0; mi < Math.min(topMats.length, 6); mi++) {
    svg += `<span style="font-size:9px;color:var(--text3);margin-right:8px"><span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${colors[mi]};margin-right:2px;vertical-align:middle"></span>${topMats[mi]}</span>`;
  }
  return svg;
}
// ── Daily trend ──
function _renderDailyTrend(dailyRuns) {
  const dates = Object.keys(dailyRuns).sort().slice(-30);
  if (!dates.length) return '<div style="color:var(--text3);font-size:10px">暂无数据</div>';
  const vals = dates.map(d => dailyRuns[d]);
  const maxV = Math.max(1, ...vals);
  const W = 500, H = 180, pad = {t:10,r:10,b:20,l:30};
  const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
  const xs = iw / Math.max(vals.length - 1, 1);
  const fill = 'var(--accent)';
  let svg = `<svg width="${W}" height="${H}" style="width:100%;height:auto;max-height:200px;background:transparent">`;
  // grid
  for (let y = 0; y <= 4; y++) {
    const yy = pad.t + ih * (1 - y/4);
    svg += `<line x1="${pad.l}" y1="${yy}" x2="${W-pad.r}" y2="${yy}" stroke="var(--border)" stroke-width="0.5"/>`;
    svg += `<text x="${pad.l-4}" y="${yy+3}" fill="var(--text3)" font-size="8" text-anchor="end">${Math.round(maxV * y / 4)}</text>`;
  }
  // area fill
  let area = '';
  for (let i = 0; i < vals.length; i++) {
    const x = pad.l + i * xs;
    const y = pad.t + ih * (1 - vals[i] / maxV);
    area += (i === 0 ? 'M' : 'L') + x.toFixed(1) + ',' + y.toFixed(1);
  }
  const lastX = pad.l + (vals.length-1) * xs;
  area += ` L${lastX.toFixed(1)},${pad.t+ih} L${pad.l},${pad.t+ih} Z`;
  svg += `<path d="${area}" fill="${fill}" fill-opacity="0.1" stroke="${fill}" stroke-width="1.5" stroke-linejoin="round"/>`;
  svg += `</svg>`;
  return svg;
}

// ── Dashboard (调度台) ──
async function renderDashboard(container) {
  container.innerHTML = `<div>
    <div style="margin-bottom:8px;display:flex;align-items:center;gap:8px">
      <span style="color:var(--text2);font-size:12px;font-weight:bold">调度台</span>
      <span style="font-size:9px;color:var(--text3);flex:1" id="dash-subtitle"></span>
      <button class="small" onclick="loadDashboard()">刷新</button>
    </div>
    <div id="dash-content"></div>
  </div>`;
  _dashCache = '';
  await loadDashboard();
  if (logTimer) clearInterval(logTimer);
  logTimer = setInterval(() => {
    if (state.page !== 'dashboard') { clearInterval(logTimer); logTimer = null; return; }
    loadDashboard();
  }, 5000);
}
let _dashCache = '';
async function loadDashboard() {
  try {
    const r = await apiGet('/node/dashboard');
    const el = document.getElementById('dash-content');
    if (!el) return;
    // Save details open state before re-render
    const detailsState = {};
    el.querySelectorAll('details').forEach(d => { if (d.id) detailsState[d.id] = d.open; });
    const key = JSON.stringify(r);
    if (key === _dashCache) return;
    _dashCache = key;
    const d = r || {};
    const sys = d.system || {};
    const gpu = d.gpu || {};
    const cap = d.capacity || {};
    const procs = d.processes || [];
    const q = await apiGet('/queue');
    var html = '';

    // ── 顶部状态条 ──
    html += `<div style="display:flex;gap:6px;margin-bottom:8px">`;
    html += _dashStat(`${sys.cpu_pct != null ? sys.cpu_pct : '?'}%`, 'CPU', sys.cpu_pct > 80 ? 'var(--danger)' : 'var(--accent)');
    html += _dashStat(`${sys.memory_pct||0}%`, '内存', 'var(--text)');
    html += _dashStat(gpu.usage != null ? `${gpu.usage}%` : 'N/A', 'GPU', gpu.usage > 80 ? 'var(--danger)' : 'var(--accent)');
    html += `<div style="flex:1;display:flex;gap:6px;justify-content:flex-end">`;
    html += _dashStat(`${cap.running||0}/${cap.parallel_max||3}`, '运行中', cap.running >= cap.parallel_max ? 'var(--danger)' : 'var(--accent)');
    html += _dashStat(`${q.pending_count||0}`, '排队中', 'var(--warn)');
    html += `</div></div>`;

    // ── 到期提醒 ──
    var expireHtml = '';
    try {
      var accts = (await apiGet('/accounts')).accounts || [];
      var now = new Date();
      accts.forEach(function(a) {
        if (!a.expire_date) return;
        var d = new Date(a.expire_date);
        var diff = Math.ceil((d - now) / 86400000);
        if (diff < 0) expireHtml += '<div style="font-size:9px;color:var(--danger);padding:2px 0">⚠ <b>'+a.name+'</b> 已过期 '+Math.abs(diff)+' 天'+(a.note ? ' ('+a.note+')' : '')+'</div>';
        else if (diff <= 3) expireHtml += '<div style="font-size:9px;color:var(--warn);padding:2px 0">⏰ <b>'+a.name+'</b> '+diff+' 天后到期'+(a.note ? ' ('+a.note+')' : '')+'</div>';
      });
    } catch(e) {}
    if (expireHtml) html += '<div class="card" style="padding:4px 8px;margin-bottom:6px;flex-direction:column;align-items:stretch;border-left:3px solid var(--danger)">'+expireHtml+'</div>';

    // ── 今日汇总 ──
    try {
      var todayRuns = 0, todayFails = 0, todayTotal = 0;
      var st = await apiGet('/stats');
      if (st.ok && st.accounts) {
        var todayKey = new Date().toISOString().slice(0, 10);
        st.accounts.forEach(function(a) {
          if (a.stats && a.stats[todayKey]) {
            var day = a.stats[todayKey];
            todayTotal += day.launches || 0;
            if (day.total_sec > 0) todayRuns++;
          }
        });
      }
      // Count running from dashboard data
      var runningNow = cap.running || 0;
      var rate = todayTotal > 0 ? Math.round(todayRuns / todayTotal * 100) : 100;
      html += '<div class="card" style="padding:4px 10px;margin-bottom:6px;flex-direction:row;gap:12px;flex-wrap:wrap">';
      html += '<span style="font-size:9px;color:var(--text2);font-weight:bold">📊 今日</span>';
      html += '<span style="font-size:10px">运行 <b style="color:var(--accent)">'+todayTotal+'</b> 次</span>';
      html += '<span style="font-size:10px">在线 <b style="color:var(--accent)">'+runningNow+'</b> 个</span>';
      html += '<span style="font-size:10px">成功率 <b style="color:'+(rate>80?'var(--accent)':'var(--warn)')+'">'+rate+'%</b></span>';
      html += '</div>';
    } catch(e) {}

    // ── 操作 + 调度配置 ──
    html += `<div class="card" style="padding:6px 8px;margin-bottom:6px;flex-direction:column;align-items:stretch">`;
    // Row 1: action buttons
    html += `<div style="display:flex;gap:4px;flex-wrap:wrap;align-items:center;margin-bottom:4px">`;
    html += `<button class="small primary" onclick="smartAll()" title="同时入队维护+刷关+剿灭">▶ 一键调度</button>`;
    html += `<button class="small" onclick="smartMaintenance()" title="基建/公招/信用">🏗 维护</button>`;
    html += `<button class="small" onclick="smartFight()" title="理智刷关">⚔ 刷关</button>`;
    html += `<button class="small" onclick="smartAnnihilation()" title="剿灭作战">🔥 剿灭</button>`;
    html += `<button class="small" onclick="smartLogin()" title="仅启动游戏验证登录">🔍 登录</button>`;
    html += `<span style="flex:1"></span>`;
    html += `<button class="small" onclick="stopAll()" style="color:var(--danger)">⏹ 停止全部</button>`;
    html += `<button class="small" onclick="toggleDashPause()" id="dash-pause-btn" style="color:var(--warn)">⏸ 暂停</button>`;
    html += `<button class="small" onclick="clearQueue()">🗑 清空</button>`;
    html += `</div>`;
    // Row 2: mode + thresholds + capacity (compact, wrap-friendly)
    html += `<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;font-size:9px;color:var(--text3)">`;
    html += `<label><input type="radio" name="dash-mode" value="" ${_dashMode===''?'checked':''} onchange="dashModeChange(this)"> 日常</label>`;
    html += `<label><input type="radio" name="dash-mode" value="anni" ${_dashMode==='anni'?'checked':''} onchange="dashModeChange(this)"> 含剿灭</label>`;
    html += `<label><input type="radio" name="dash-mode" value="anni-only" ${_dashMode==='anni-only'?'checked':''} onchange="dashModeChange(this)"> 仅剿灭</label>`;
    html += `<span style="background:var(--border);width:1px;height:14px"></span>`;
    html += `<span>理智</span><input type="range" min="0" max="200" value="${cap.deficit??0}" id="dash-deficit" onchange="dashSaveDeficit(this.value)" style="width:50px;height:4px"><span id="dash-deficit-val">${cap.deficit??0}</span>`;
    html += `<span>卡死</span><input type="range" min="0" max="30" value="${cap.stuck_timeout||10}" id="dash-stuck" onchange="dashSaveStuck(this.value)" style="width:50px;height:4px"><span id="dash-stuck-val">${cap.stuck_timeout||10}min</span>`;
    html += `<button class="small" onclick="dashApplyAll()" style="font-size:8px">应用到全部</button>`;
    html += `<span style="background:var(--border);width:1px;height:14px"></span>`;
    html += `<span>并行</span><input type="range" min="1" max="30" value="${cap.parallel_max||3}" id="dash-slider" oninput="dashSliderChange(this.value)" onchange="dashSaveSlider()" style="width:80px;height:4px"><span id="dash-slider-val" style="min-width:24px;font-weight:bold;color:var(--accent)">${cap.parallel_max||3}</span>`;
    html += `<span>还可开 <b style="color:${cap.max>0?'var(--accent)':'var(--warn)'}">${cap.max}</b></span>`;
    html += `<span>内存<b>${cap.by_memory}</b> 显存<b>${cap.by_gpu}</b> <b>${cap.limit_by||''}</b></span>`;
    html += `</div></div>`;

    // ── 智能调度 ──
    const smartR = await apiGet('/settings/smart');
    const smart = smartR.smart_global || {};
    const smartEnabled = smart.enabled ? true : false;
    html += `<div class="card" style="padding:4px 8px;margin-bottom:6px;flex-direction:column;align-items:stretch">`;
    html += `<div style="display:flex;align-items:center;gap:6px">`;
    html += `<span style="font-size:10px;font-weight:bold;color:var(--text2)">⚙ 智能调度</span>`;
    html += `<button class="small" onclick="toggleSmartEnabled()" style="font-size:8px;padding:1px 6px;background:${smartEnabled ? 'var(--accent)' : 'var(--bg3)'};color:${smartEnabled ? '#fff' : 'var(--text3)'};border:1px solid ${smartEnabled ? 'var(--accent)' : 'var(--border)'}">${smartEnabled ? '● 启用' : '○ 关闭'}</button>`;
    html += `<details id="dash-smart-config" style="font-size:9px"><summary style="color:var(--text3);cursor:pointer">详细配置 ▸</summary>`;
    html += `<div style="margin-top:4px;display:flex;gap:6px;flex-wrap:wrap;align-items:center">`;
    html += `<label><input type="checkbox" id="ds-anni" ${smart.annihilation_enabled!==false?'checked':''}> 剿灭</label>`;
    html += `<label><input type="checkbox" id="ds-recruit" ${smart.recruit_enabled!==false?'checked':''}> 招募</label>`;
    html += `<label><input type="checkbox" id="ds-mall" ${smart.mall_enabled!==false?'checked':''}> 商店</label>`;
    html += `<label><input type="checkbox" id="ds-exp-med" ${smart.expiring_medicine?'checked':''}> 过期药</label>`;
    html += `<span>体力阈值</span><input type="number" id="ds-threshold" value="${smart.threshold||80}" min="0" max="200" style="width:40px;padding:1px 3px;font-size:9px;background:var(--bg3);border:1px solid var(--border);color:var(--text);border-radius:2px">%`;
    html += `<button class="small primary" onclick="dashSaveSmart()" style="font-size:8px">保存</button>`;
    html += `</div></details></div></div>`;

    // ── 运行中 + 排队 ──
    const runningProcs = procs.filter(p => p.running);
    const pending = (q.pending||[]).slice(0, 5);
    html += `<div class="dash-grid-2" style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:8px">`;
    html += `<div class="card" style="padding:8px;flex-direction:column;align-items:stretch">`;
    html += `<div style="font-size:10px;font-weight:bold;color:var(--accent);margin-bottom:4px">▶ 运行中 ${runningProcs.length}</div>`;
    if (runningProcs.length) {
      for (const p of runningProcs) {
        const mem = ((p.maa_mem_mb||0)+(p.emu_mem_mb||0))/1024;
        const task = p.last_task || '-';
        html += `<div style="font-size:10px;padding:3px 0;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center">`;
        html += `<span>${p.name}</span>`;
        html += `<span style="color:var(--text3)">${task} · ${mem.toFixed(1)}G <a href="#" onclick="stopAccountByAid('${p.aid}');return false" style="color:var(--danger);text-decoration:none">停止</a>`;
        html += ` <a href="#" onclick="togglePreview('${p.aid}','${p.name}');return false" style="color:var(--accent);text-decoration:none">📷</a></span></div>`;
        html += `<div id="preview-${p.aid}" style="display:none;margin-top:2px"><img src="" style="width:100%;border-radius:3px;border:1px solid var(--border)" onerror="this.style.display='none'"></div>`;
      }
    } else {
      html += `<div style="font-size:10px;color:var(--text3);text-align:center;padding:12px">无运行中的进程</div>`;
    }
    html += `</div>`;
    html += `<div class="card" style="padding:8px;flex-direction:column;align-items:stretch">`;
    html += `<div style="font-size:10px;font-weight:bold;color:var(--warn);margin-bottom:4px">⏳ 排队中 ${q.pending_count||0}</div>`;
    if (pending.length) {
      for (const item of pending) {
        const timeStr = item.not_before ? item.not_before.slice(11, 16) : '';
        html += `<div style="font-size:10px;padding:3px 0;border-bottom:1px solid var(--border);display:flex;justify-content:space-between">`;
        html += `<span>${item.suspended ? '⏸' : ''} ${item.account_name}</span>`;
        html += `<span style="color:var(--text3)">${timeStr || item.source} <a href="#" onclick="dequeueAccount('${item.account_id}');return false" style="color:var(--danger);text-decoration:none">出队</a></span></div>`;
      }
    } else {
      html += `<div style="font-size:10px;color:var(--text3);text-align:center;padding:12px">队列为空</div>`;
    }
    html += `</div></div>`;

    // ── 资源趋势 (折叠) ──
    const samples = d.samples || [];
    if (samples.length > 5) {
      html += `<details id="dash-trend" style="margin-bottom:6px;font-size:10px" ${samples.length > 5 ? 'open' : ''}><summary style="cursor:pointer;color:var(--text2);font-weight:bold;padding:6px 0">📈 资源趋势</summary>`;
      html += `<div class="card" style="padding:8px;flex-direction:column;align-items:stretch;overflow-x:auto">`;
      html += _trendChart(samples);
      html += `<div style="font-size:8px;color:var(--text3);margin-top:2px"><span style="color:#e74c3c">─ CPU</span> <span style="color:#3498db;margin-left:6px">─ 内存</span> <span style="color:#2ecc71;margin-left:6px">─ GPU</span></div>`;
      html += `</div></details>`;
    }

    // ── 调度编年史 (折叠) ──
    const gantt = d.gantt || [];
    html += `<details id="dash-gantt" style="margin-bottom:6px;font-size:10px" open><summary style="cursor:pointer;color:var(--text2);font-weight:bold;padding:6px 0">📜 编年史 (${gantt.length}条)</summary>`;
    html += `<div class="card" style="padding:10px;flex-direction:column;align-items:stretch">`;
    html += _chronicleTimeline(gantt);
    html += `</div></details>`;

    // ── 进程资源表 (折叠) ──
    html += `<details id="dash-procs" style="margin-bottom:6px;font-size:10px" open><summary style="cursor:pointer;color:var(--text2);font-weight:bold;padding:6px 0">🖥 进程资源${procs.length ? ' ('+procs.length+'个)' : ''}</summary>`;
    if (procs.length) {
      html += `<div class="card" style="padding:8px;flex-direction:column;align-items:stretch">`;
      const ncpu = sys.cpu_count || 1;
      html += `<table style="width:100%;font-size:10px;border-collapse:collapse">`;
      html += `<tr style="color:var(--text3)"><th style="text-align:left;padding:2px 4px">账号</th><th style="text-align:right;padding:2px 4px">CPU</th><th style="text-align:right;padding:2px 4px">内存</th><th style="text-align:right;padding:2px 4px">进程</th></tr>`;
      let tCpu = 0, tMem = 0;
      for (const p of procs) {
        const cpu = ((p.maa_cpu_pct||0) + (p.emu_cpu_pct||0)) / ncpu;
        const mem = ((p.maa_mem_mb||0) + (p.emu_mem_mb||0)) / 1024;
        tCpu += cpu; tMem += mem;
        const label = p.running ? '🟢' : '⚪';
        const detail = p.maa_pid ? `MAA ${p.emu_name||''}` : '-';
        html += `<tr style="border-top:1px solid var(--border)"><td style="padding:2px 4px">${label} ${p.name}</td><td style="text-align:right;padding:2px 4px">${cpu.toFixed(1)}%</td><td style="text-align:right;padding:2px 4px">${mem.toFixed(1)}G</td><td style="text-align:right;padding:2px 4px;font-size:9px;color:var(--text3)">${detail}</td></tr>`;
      }
      html += `<tr style="border-top:1px solid var(--border);color:var(--text3);font-weight:bold"><td style="padding:2px 4px">合计</td><td style="text-align:right;padding:2px 4px">${tCpu.toFixed(1)}%</td><td style="text-align:right;padding:2px 4px">${tMem.toFixed(1)}G</td><td style="text-align:right;padding:2px 4px;font-size:9px">GPU ${gpu.usage}% / ${(gpu.mem_used_mb/1024).toFixed(1)}G</td></tr>`;
      html += `</table></div>`;
    }
    html += `</details>`;

    // ── AI 分析 ──
    html += `<div id="ai-insights-area"></div>`;

    el.innerHTML = html;
    // Restore details open state after re-render
    Object.entries(detailsState).forEach(([id, open]) => {
      const d = el.querySelector(`#${CSS.escape(id)}`);
      if (d) d.open = open;
    });
    const sub = document.getElementById('dash-subtitle');
    if (sub) sub.textContent = `并行${cap.parallel_max} · 还可${cap.max} · ${cap.limit_by||''}${gpu.name ? ' · ' + gpu.name : ''}`;
    renderAIInsights();
    if (q && q.paused) {
      const pb = document.getElementById('dash-pause-btn');
      if (pb) pb.textContent = '▶ 恢复';
    }
  } catch(e) { /* silent */ }
}
function _dashStat(val, label, color) {
  return `<div class="card" style="padding:4px 10px;gap:4px;min-width:0"><div style="font-size:15px;font-weight:bold;color:${color};line-height:1.2">${val}</div><div style="font-size:8px;color:var(--text3);line-height:1">${label}</div></div>`;
}
function _trendChart(samples) {
  const W = 800, H = 150, pad = {t:8,r:8,b:20,l:30};
  const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
  const n = samples.length;
  const step = iw / Math.max(n - 1, 1);
  const series = [
    {key:'cpu', color:'#e74c3c'}, {key:'mem', color:'#3498db'}, {key:'gpu', color:'#2ecc71'}
  ];
  let svg = `<svg width="${W}" height="${H}" style="width:100%;height:auto;max-height:160px;background:transparent" viewBox="0 0 ${W} ${H}">`;
  // grid
  for (let y = 0; y <= 4; y++) {
    const yy = pad.t + ih * (1 - y/4);
    svg += `<line x1="${pad.l}" y1="${yy}" x2="${W-pad.r}" y2="${yy}" stroke="var(--border)" stroke-width="0.5"/>`;
    svg += `<text x="${pad.l-4}" y="${yy+3}" fill="var(--text3)" font-size="8" text-anchor="end">${y*25}</text>`;
  }
  // lines
  for (const s of series) {
    let path = '';
    for (let i = 0; i < n; i++) {
      const val = Math.min((samples[i][s.key] || 0), 100);
      const x = pad.l + i * step;
      const y = pad.t + ih * (1 - val / 100);
      path += (i === 0 ? 'M' : 'L') + x.toFixed(1) + ',' + y.toFixed(1);
    }
    svg += `<path d="${path}" fill="none" stroke="${s.color}" stroke-width="1.5" stroke-linejoin="round"/>`;
  }
  svg += `</svg>`;
  return svg;
}
function _chronicleTimeline(events) {
  const starts = {}, runs = [], tasks = {};
  for (const e of events) {
    if (e.event === 'task') {
      if (!tasks[e.aid]) tasks[e.aid] = [];
      tasks[e.aid].push({ts: e.ts, task: e.task});
    }
  }
  for (const e of events) {
    if (e.event === 'start') starts[e.aid] = e.ts;
    else if (e.event === 'stop' && starts[e.aid]) {
      const aid = e.aid;
      const run = {aid, name: e.name, start: starts[e.aid], stop: e.ts, dur: e.ts - starts[e.aid]};
      run.taskList = (tasks[aid]||[]).filter(t => t.ts >= run.start && t.ts <= run.stop).map(t => t.task);
      runs.push(run);
      delete starts[aid];
    } else if (e.event === 'stop') {
      // Orphaned stop (no matching start) — still show it
      const aid = e.aid;
      runs.push({aid, name: e.name, start: e.ts - 1, stop: e.ts, dur: 0, taskList: []});
    }
  }
  for (const [aid, ts] of Object.entries(starts)) {
    const name = events.find(e => e.aid === aid)?.name || aid;
    runs.push({aid, name, start: ts, stop: 0, dur: 0, taskList: (tasks[aid]||[]).filter(t => t.ts >= ts).map(t => t.task)});
  }
  runs.sort((a, b) => b.start - a.start);
  if (!runs.length) return '<div style="font-size:10px;color:var(--text3);text-align:center;padding:20px">📭 暂无调度记录</div>';
  const taskColors = {'唤醒':'#3498db','刷关':'#e74c3c','公招':'#f39c12','基建':'#2ecc71','信用':'#9b59b6','奖励':'#1abc9c','肉鸽':'#e67e22','生息':'#34495e'};
  let lastHour = -1, lastDate = '';
  let html = '<div style="display:flex;flex-direction:column;gap:4px">';
  runs.slice(0, 20).forEach((r) => {
    const date = new Date(r.start * 1000);
    const dateKey = `${date.getFullYear()}-${(date.getMonth()+1).toString().padStart(2,'0')}-${date.getDate().toString().padStart(2,'0')}`;
    const hour = date.getHours();
    const timeStr = `${hour.toString().padStart(2,'0')}:${date.getMinutes().toString().padStart(2,'0')}`;
    const durStr = r.dur > 0 ? `${Math.floor(r.dur/60)}m${Math.floor(r.dur%60)}s` : '运行中';
    const isRunning = r.dur === 0;

    // Hour marker
    if (hour !== lastHour || dateKey !== lastDate) {
      html += `<div style="display:flex;align-items:center;gap:6px;padding:2px 0">`;
      html += `<span style="font-size:10px;font-weight:bold;color:var(--text3);white-space:nowrap">${hour.toString().padStart(2,'0')}:00</span>`;
      html += `<span style="flex:1;height:1px;background:var(--border)"></span>`;
      if (dateKey !== lastDate) {
        html += `<span style="font-size:8px;color:var(--text3)">${dateKey}</span>`;
      }
      html += `</div>`;
      lastHour = hour;
      lastDate = dateKey;
    }

    // Dot + card in one flex row
    html += `<div style="display:flex;gap:8px;align-items:stretch">`;
    html += `<div style="display:flex;flex-direction:column;align-items:center;width:14px;flex-shrink:0">`;
    html += `<div style="width:14px;height:14px;border-radius:50%;background:${isRunning ? 'var(--accent)' : 'var(--text3)'};border:2px solid var(--bg2);margin-top:4px"></div>`;
    html += `<div style="flex:1;width:2px;background:var(--border)"></div>`;
    html += `</div>`;
    html += `<div style="flex:1;margin-bottom:2px;padding:6px 8px;background:var(--bg3);border-radius:var(--radius);border-left:3px solid ${isRunning ? 'var(--accent)' : 'transparent'}">`;
    html += `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:2px">`;
    html += `<span style="font-size:10px;font-weight:bold;color:var(--text2)">${r.name}</span>`;
    html += `<span style="font-size:9px;color:var(--text3)">${timeStr} · ${durStr}</span>`;
    html += `</div>`;
    const tlist = r.taskList || [];
    if (tlist.length) {
      html += `<div style="display:flex;gap:3px;flex-wrap:wrap">`;
      const seen = [];
      tlist.forEach((t) => {
        const col = taskColors[t] || '#555';
        if (seen.includes(t)) return;
        seen.push(t);
        const cnt = tlist.filter(x => x === t).length;
        html += `<span style="font-size:9px;padding:1px 5px;border-radius:3px;background:${col}22;color:${col};border:1px solid ${col}44">${t}${cnt > 1 ? '×'+cnt : ''}</span>`;
      });
      html += `</div>`;
    }
    if (isRunning) html += `<div style="font-size:9px;color:var(--accent);margin-top:2px">▶ 运行中</div>`;
    html += `</div></div>`;
  });
  html += '</div>';
  return html;
}
function dashSliderChange(val) {
  const lbl = document.getElementById('dash-slider-val');
  if (lbl) lbl.textContent = val;
}
async function dashSaveSlider() {
  const val = document.getElementById('dash-slider')?.value;
  if (val) await apiPost('/config', { parallel_max: parseInt(val) });
}
function showNewDispatch() {
  let accts = state.accounts;
  if (!accts || !accts.length) {
    apiGet('/accounts').then(r => { if (r.ok) { state.accounts = r.accounts; showNewDispatchDialog(r.accounts); } });
    return;
  }
  showNewDispatchDialog(accts);
}
function showNewDispatchDialog(accts) {
  const allChecked = true;
  const html = `<div class="dialog-overlay" onclick="event.target==this&&this.remove()">
    <div class="dialog" style="max-width:420px">
      <div style="font-size:14px;font-weight:bold;margin-bottom:8px;color:var(--text2)">新调度</div>
      <div style="margin-bottom:6px;display:flex;gap:4px;flex-wrap:wrap">
        <button class="small primary" onclick="document.querySelectorAll('.dispatch-acct').forEach(c=>c.checked=true)">全选</button>
        <button class="small" onclick="document.querySelectorAll('.dispatch-acct').forEach(c=>c.checked=false)">取消</button>
        <input type="text" id="dispatch-search" placeholder="搜索..." oninput="filterDispatchList()" style="flex:1;min-width:80px;padding:2px 6px;font-size:10px;background:var(--bg3);border:1px solid var(--border);color:var(--text);border-radius:var(--radius)">
      </div>
      <div id="dispatch-acct-list" style="max-height:300px;overflow-y:auto;margin-bottom:8px">
        ${accts.map(a => `<label class="dispatch-item" data-name="${a.name}" style="display:flex;align-items:center;gap:6px;padding:3px 0;font-size:11px;border-bottom:1px solid var(--border);cursor:pointer">
          <input type="checkbox" class="dispatch-acct" value="${a.id}" checked> ${a.name} <span style="color:var(--text3);font-size:9px">${a.emu_instance_index ? 'VM'+a.emu_instance_index : ''} ${a.game_client||''}</span></label>`).join('')}
      </div>
      <div style="margin-bottom:8px;font-size:10px;color:var(--text3)">
        <label style="margin-right:8px"><input type="radio" name="dispatch-mode" value="" checked onchange="window._dispatchMode=this.value"> 日常</label>
        <label style="margin-right:8px"><input type="radio" name="dispatch-mode" value="anni" onchange="window._dispatchMode=this.value"> 含剿灭</label>
        <label><input type="radio" name="dispatch-mode" value="anni-only" onchange="window._dispatchMode=this.value"> 仅剿灭</label>
      </div>
      <div class="btn-row">
        <button class="primary" onclick="submitDispatch()">开始调度</button>
        <button onclick="this.closest('.dialog-overlay').remove()">取消</button>
      </div>
    </div>
  </div>`;
  document.body.insertAdjacentHTML('beforeend', html);
}
function filterDispatchList() {
  const q = (document.getElementById('dispatch-search')?.value || '').toLowerCase();
  document.querySelectorAll('.dispatch-item').forEach(el => {
    el.style.display = el.dataset.name.toLowerCase().includes(q) ? '' : 'none';
  });
}
async function submitDispatch() {
  const checked = document.querySelectorAll('.dispatch-acct:checked');
  const ids = [...checked].map(cb => cb.value);
  if (!ids.length) { toast('请至少选择一个账号', 'error'); return; }
  const mode = window._dispatchMode || '';
  const include_anni = mode === 'anni' || mode === 'anni-only';
  const only_anni = mode === 'anni-only';
  const r = await apiPost('/action/smart_selected', { account_ids: ids, include_anni, only_anni });
  document.querySelector('.dialog-overlay')?.remove();
  if (r.ok) toast(`已调度 ${ids.length} 个账号`); else toast(r.error || '调度失败', 'error');
}
function toggleDashPause() {
  const btn = document.getElementById('dash-pause-btn');
  if (!btn) return;
  if (btn.textContent.includes('暂停')) {
    apiPost('/queue/pause');
    btn.textContent = '▶ 恢复排队';
  } else {
    apiPost('/queue/resume');
    btn.textContent = '⏸ 暂停排队';
  }
}
async function stopAccountByAid(aid) {
  let accts = state.accounts;
  if (!accts || !accts.length) {
    const r = await apiGet('/accounts');
    if (r.ok) accts = r.accounts;
  }
  const idx = accts?.findIndex(a => a.id === aid) ?? -1;
  if (idx >= 0) await apiPost(`/account/${idx}/stop`);
  else await apiPost('/action/stop_all');
  loadDashboard();
}
async function dequeueAccount(aid) {
  await apiPost('/queue/dequeue', { account_id: aid });
  loadDashboard();
}
let _dashMode = '';
function dashModeChange(el) {
  _dashMode = el.value;
}
async function smartAll() {
  const r = await apiPost('/action/smart_all', {});
  if (r.ok) toast('已发起一键调度'); else toast(r.error || '调度失败', 'error');
}
async function smartMaintenance() {
  const r = await apiPost('/action/smart_maintenance', {});
  if (r.ok) toast('已发起维护调度'); else toast(r.error || '调度失败', 'error');
}
async function smartFight() {
  const r = await apiPost('/action/smart_fight', {});
  if (r.ok) toast('已发起刷关调度'); else toast(r.error || '调度失败', 'error');
}
async function smartAnnihilation() {
  const r = await apiPost('/action/smart_annihilation', {});
  if (r.ok) toast('已发起剿灭调度'); else toast(r.error || '调度失败', 'error');
}
async function smartLogin() {
  const r = await apiPost('/action/smart_login', {});
  if (r.ok) toast('已发起登录验证'); else toast(r.error || '调度失败', 'error');
}
function dashSaveDeficit(val) {
  const el = document.getElementById('dash-deficit-val');
  if (el) el.textContent = val;
  apiPost('/config', { deficit: parseInt(val) });
}
function dashSaveStuck(val) {
  const el = document.getElementById('dash-stuck-val');
  if (el) el.textContent = val + 'min';
  apiPost('/config', { stuck_timeout: parseInt(val) });
}
async function dashApplyAll() {
  try {
    const accts = await apiGet('/accounts');
    if (!accts.ok || !accts.accounts) return;
    const deficit = parseInt(document.getElementById('dash-deficit')?.value || '0');
    const stuck = parseInt(document.getElementById('dash-stuck')?.value || '10');
    for (let i = 0; i < accts.accounts.length; i++) {
      await apiPost(`/account/${i}/edit`, { round_robin_deficit: deficit, stuck_timeout_min: stuck });
    }
    toast('已应用到所有账号');
  } catch(e) { toast('应用失败', 'error'); }
}
async function dashSaveSmart() {
  const r = await apiPost('/settings/smart', {
    threshold: parseInt(document.getElementById('ds-threshold')?.value) || 80,
    annihilation_enabled: !!document.getElementById('ds-anni')?.checked,
    recruit_enabled: !!document.getElementById('ds-recruit')?.checked,
    mall_enabled: !!document.getElementById('ds-mall')?.checked,
    expiring_medicine: !!document.getElementById('ds-exp-med')?.checked,
  });
  if (r.ok) { toast('配置已保存'); loadDashboard(); } else toast(r.error || '保存失败', 'error');
}
async function toggleSmartEnabled() {
  const smartR = await apiGet('/settings/smart');
  const current = smartR.smart_global?.enabled ?? false;
  await apiPost('/settings/smart', { enabled: !current });
  loadDashboard();
}

let logAutoRef = true;
let logTimer = null;

// ── Logs (MAA-style event feed) ──
const LOG_ICONS = {
  connect:      '🔗', // 连接模拟器
  launch:       '🚀', // 启动
  task_start:   '▶',  // 任务开始
  task_done:    '✅', // 任务完成
  infra:        '🏭', // 基建
  fight:        '⚔️', // 战斗
  recruit:      '🔍', // 公招
  mall:         '🛒', // 商店
  award:        '🎁', // 领取奖励
  error:        '❌',
  warn:         '⚠️',
  info:         'ℹ️',
  cleanup:      '🧹', // 清理
  queue:        '📋', // 队列
  plan:         '📋', // 计划
};

function classifyLog(msg) {
  if (!msg) return null;
  const m = msg.toLowerCase();
  if (/启动|连接|adb|emulator|mumu|connect/i.test(m)) return LOG_ICONS.connect;
  if (/战斗|作战|关卡|开始战斗|fight|stage|battle/i.test(m)) return LOG_ICONS.fight;
  if (/基建|infrast/i.test(m)) return LOG_ICONS.infra;
  if (/公招|recruit/i.test(m)) return LOG_ICONS.recruit;
  if (/商店|mall|采购/i.test(m)) return LOG_ICONS.mall;
  if (/领取|奖励|award/i.test(m)) return LOG_ICONS.award;
  if (/错误|失败|error|fail|crash|异常|断开/i.test(m)) return LOG_ICONS.error;
  if (/警告|warn/i.test(m) || /超时|timeout/i.test(m)) return LOG_ICONS.warn;
  if (/入队|出队|重排|重试|计划|enqueue|dequeue|retry|dispatch/i.test(m)) return LOG_ICONS.queue;
  if (/完成|done|finish|success|complete/i.test(m)) return LOG_ICONS.task_done;
  if (/清理|cleanup|关闭|停止|shutdown|kill|exit/i.test(m)) return LOG_ICONS.cleanup;
  if (/开始|start|running/i.test(m)) return LOG_ICONS.task_start;
  if (/注入|调度|设置|config|inject/i.test(m)) return LOG_ICONS.info;
  return null;
}

function parseLogEntry(line, overrideLvl) {
  const m = line.match(/\[(.*?)\]\s+\[(.+?)\]\s+\[(.*?)\]\s+(.*)/);
  if (!m) return null;
  const ts = m[1], lvl = overrideLvl || m[2].trim(), msg = m[4];
  return { ts, lvl, msg, raw: line };
}

// ── Gallery ──
async function renderGallery(container) {
  const accts = state.accounts.length ? state.accounts : ((await apiGet('/accounts')).ok ? (await apiGet('/accounts')).accounts : []);
  container.innerHTML = `<div>
    <div style="margin-bottom:8px;display:flex;align-items:center;gap:8px;flex-wrap:wrap">
      <span style="color:var(--text2);font-size:12px;font-weight:bold">🖼 往期图库</span>
      <select id="gal-account" onchange="loadGallery()" style="font-size:10px;padding:2px 4px;background:var(--bg3);border:1px solid var(--border);color:var(--text);border-radius:3px">
        <option value="">选择账号</option>
        ${accts.map(a => `<option value="${a.id}">${a.name}</option>`).join('')}
      </select>
    </div>
    <div id="gallery-content"></div>
  </div>`;
}
async function loadGallery() {
  const aid = document.getElementById('gal-account')?.value;
  const el = document.getElementById('gallery-content');
  if (!el || !aid) { if(el) el.innerHTML = ''; return; }
  try {
    const r = await apiGet('/screenshots/' + aid);
    if (!r.ok) { el.innerHTML = '<div style="color:var(--text3);text-align:center;padding:20px">加载失败</div>'; return; }
    if (!r.runs || !r.runs.length) { el.innerHTML = '<div style="color:var(--text3);text-align:center;padding:20px">暂无截图</div>'; return; }
    let html = '';
    for (const run of r.runs) {
      const dateStr = new Date(run.ts * 1000).toLocaleString();
      html += `<div style="margin-bottom:12px"><div style="font-size:11px;color:var(--text2);font-weight:bold;margin-bottom:4px">${dateStr} (${run.shots.length}张) <a href="${API}/screenshots/${r.aid}/export/${run.dir}" style="font-size:9px;font-weight:normal;color:var(--accent);text-decoration:none">📥 下载</a></div>`;
      html += `<div style="display:flex;gap:4px;overflow-x:auto;padding:4px 0">`;
      for (const shot of run.shots) {
        const ts = new Date(shot.ts * 1000).toLocaleTimeString();
        html += `<div style="flex-shrink:0;cursor:pointer" onclick="showFullImage('${r.aid}','${run.dir}','${shot.file}')">`;
        html += `<img src="${API}/screenshots/file/${r.aid}/${run.dir}/${shot.file}" style="height:120px;border-radius:var(--radius);border:1px solid var(--border)">`;
        html += `<div style="font-size:9px;color:var(--text3);text-align:center">${ts}</div></div>`;
      }
      html += `</div></div>`;
    }
    el.innerHTML = html;
  } catch(e) { el.innerHTML = '<div style="color:var(--text3);text-align:center;padding:20px">加载失败</div>'; }
}
function showFullImage(aid, run, file) {
  const html = `<div class="dialog-overlay" onclick="event.target==this&&this.remove()" style="cursor:zoom-out">
    <img src="${API}/screenshots/file/${aid}/${run}/${file}" style="max-width:90vw;max-height:90vh;border-radius:6px;box-shadow:0 8px 32px rgba(0,0,0,0.5)">
  </div>`;
  document.body.insertAdjacentHTML('beforeend', html);
}

// ── Chronicle (编年史) ──
async function renderChronicle(container) {
  container.innerHTML = `<div>
    <div style="margin-bottom:8px;display:flex;align-items:center;gap:8px;flex-wrap:wrap">
      <span style="color:var(--text2);font-size:12px;font-weight:bold">📜 编年史</span>
      <span style="font-size:10px;color:var(--text3)">运行时间线</span>
      <button class="small" onclick="downloadGantt()">📥 导出</button>
      <button class="small" onclick="loadChronicle()">刷新</button>
    </div>
    <div id="chronicle-content"></div>
  </div>`;
  await loadChronicle();
  if (logTimer) clearInterval(logTimer);
  logTimer = setInterval(() => {
    if (state.page !== 'chronicle') { clearInterval(logTimer); logTimer = null; return; }
    loadChronicle();
  }, 10000);
}
function downloadGantt() {
  window.open(API + '/export/gantt', '_blank');
}
async function loadChronicle() {
  try {
    const r = await apiGet('/node/dashboard');
    const el = document.getElementById('chronicle-content');
    if (!el) return;
    const gantt = r.gantt || [];
    let html = '';
    // Summary
    const totalRuns = gantt.filter(e => e.event === 'start').length;
    html += `<div style="display:flex;gap:12px;font-size:10px;color:var(--text3);margin-bottom:8px">`;
    html += `<span>累计调度 <b style="color:var(--accent)">${totalRuns}</b> 次</span>`;
    html += `<span>累计事件 <b style="color:var(--text2)">${gantt.length}</b> 条</span>`;
    html += `</div>`;
    html += _chronicleTimeline(gantt);
    el.innerHTML = html;
  } catch(e) { /* silent */ }
}

// ── Remote Nodes (Agent) ──
async function renderNodes(container) {
  // Load saved nodes from localStorage
  let nodes = [];
  try { nodes = JSON.parse(localStorage.getItem('maorch_nodes') || '[]'); } catch(e) {}
  container.innerHTML = `<div>
    <div style="margin-bottom:8px;display:flex;align-items:center;gap:8px;flex-wrap:wrap">
      <span style="color:var(--text2);font-size:12px;font-weight:bold">🌐 远程节点</span>
      <button class="small" onclick="addNode()">＋ 添加节点</button>
      <button class="small" onclick="refreshAllNodes()">刷新全部</button>
    </div>
    <div id="nodes-content">
      ${nodes.length ? '' : '<div style="color:var(--text3);text-align:center;padding:20px;font-size:11px">暂无节点，点击「添加节点」添加远程 MAAOrch Agent</div>'}
    </div>
  </div>`;
  // Render each node
  for (const node of nodes) {
    await renderNodeCard(node);
  }
}

function renderNodeCard(node) {
  // Placeholder - will be filled by refreshAllNodes or addNode
}

async function refreshAllNodes() {
  const nodes = JSON.parse(localStorage.getItem('maorch_nodes') || '[]');
  for (const node of nodes) {
    await refreshNode(node);
  }
}

async function refreshNode(node) {
  const el = document.getElementById(`node-${node.id}`);
  if (!el) return;
  el.innerHTML = '<div style="padding:12px;text-align:center;color:var(--text3)">连接中...</div>';
  try {
    const r = await fetch(`http://${node.addr}/api/agent/status`);
    const data = await r.json();
    el.innerHTML = `<div class="card" style="padding:8px;flex-direction:column;align-items:stretch;margin-bottom:6px">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <span style="font-weight:bold;font-size:11px">${data.hostname || node.addr}</span>
        <span style="font-size:9px;color:var(--accent)">🟢 在线</span>
      </div>
      <div style="font-size:9px;color:var(--text3);margin-top:2px">
        ${data.work_dir ? '工作目录: ' + data.work_dir : ''}
        ${data.version ? ' · v' + data.version : ''}
      </div>
      <div style="display:flex;gap:4px;margin-top:6px">
        <button class="small" onclick="execOnNode('${node.id}','git_pull',[])">🔄 Git Pull</button>
        <button class="small" onclick="execOnNode('${node.id}','start_maa',[])">▶ 启动 MAAOrch</button>
        <button class="small" onclick="execOnNode('${node.id}','stop_maa',[])" style="color:var(--danger)">⏹ 停止</button>
        <button class="small" onclick="removeNode('${node.id}')" style="color:var(--danger)">🗑 移除</button>
      </div>
      <div id="node-out-${node.id}" style="font-size:9px;color:var(--text3);margin-top:4px;max-height:150px;overflow-y:auto"></div>
    </div>`;
  } catch(e) {
    el.innerHTML = `<div class="card" style="padding:8px;margin-bottom:6px">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <span style="font-weight:bold;font-size:11px">${node.addr}</span>
        <span style="font-size:9px;color:var(--danger)">🔴 离线</span>
      </div>
      <div style="font-size:9px;color:var(--text3);margin-top:2px">${e.message}</div>
      <button class="small" onclick="removeNode('${node.id}')" style="margin-top:4px;font-size:9px">🗑 移除</button>
    </div>`;
  }
}

function addNode() {
  const html = `<div class="dialog-overlay" onclick="event.target==this&&this.remove()">
    <div class="dialog" style="max-width:360px">
      <div style="font-size:14px;font-weight:bold;margin-bottom:10px;color:var(--text2)">添加远程节点</div>
      <div class="form-row"><label>地址</label><input type="text" id="node-addr-input" value="" placeholder="100.79.173.69:19998"></div>
      <div class="form-row"><label>Token</label><input type="text" id="node-token-input" value="" placeholder="可选"></div>
      <div class="btn-row" style="margin-top:10px">
        <button class="primary" onclick="submitAddNode()">添加</button>
        <button onclick="this.closest('.dialog-overlay').remove()">取消</button>
      </div>
    </div>
  </div>`;
  document.body.insertAdjacentHTML('beforeend', html);
}

async function submitAddNode() {
  const addr = document.getElementById('node-addr-input')?.value?.trim();
  if (!addr) { toast('请输入地址', 'error'); return; }
  const token = document.getElementById('node-token-input')?.value?.trim() || '';
  let nodes = [];
  try { nodes = JSON.parse(localStorage.getItem('maorch_nodes') || '[]'); } catch(e) {}
  const id = 'node_' + Date.now().toString(36);
  nodes.push({id, addr, token});
  localStorage.setItem('maorch_nodes', JSON.stringify(nodes));
  document.querySelector('.dialog-overlay')?.remove();
  toast('节点已添加');
  renderNodes(document.getElementById('content'));
  setTimeout(() => refreshNode({id, addr, token}), 500);
}

function removeNode(id) {
  let nodes = [];
  try { nodes = JSON.parse(localStorage.getItem('maorch_nodes') || '[]'); } catch(e) {}
  nodes = nodes.filter(n => n.id !== id);
  localStorage.setItem('maorch_nodes', JSON.stringify(nodes));
  renderNodes(document.getElementById('content'));
}

async function execOnNode(nodeId, action, args) {
  const nodes = JSON.parse(localStorage.getItem('maorch_nodes') || '[]');
  const node = nodes.find(n => n.id === nodeId);
  if (!node) return;
  const outEl = document.getElementById(`node-out-${nodeId}`);
  if (outEl) outEl.innerHTML = '执行中...';

  let body = {};
  if (action === 'git_pull') {
    body = {command: 'git', args: ['pull'], dir: '', timeout: 30};
  } else if (action === 'start_maa') {
    body = {command: 'python', args: ['main_web.pyw'], dir: '', timeout: 10};
  } else if (action === 'stop_maa') {
    body = {command: 'taskkill', args: ['/F', '/IM', 'pythonw.exe'], dir: '', timeout: 10};
  }

  try {
    const headers = {'Content-Type': 'application/json'};
    if (node.token) headers['x-agent-token'] = node.token;
    const r = await fetch(`http://${node.addr}/api/agent/exec`, {
      method: 'POST', headers, body: JSON.stringify(body)
    });
    const data = await r.json();
    if (outEl) outEl.innerHTML = `<pre style="margin:0;white-space:pre-wrap;font-family:Consolas,monospace">${data.stdout || ''}${data.stderr ? '\nSTDERR:\n' + data.stderr : ''}${data.error ? '\nERROR: ' + data.error : ''}</pre>`;
  } catch(e) {
    if (outEl) outEl.innerHTML = `<span style="color:var(--danger)">${e.message}</span>`;
  }
  // Auto refresh node status after exec
  setTimeout(() => refreshNode(node), 2000);
}

// ── Emulator Management ──
async function renderEmus(container) {
  // Clear previous auto-refresh timer
  if (window._emuTimer) clearInterval(window._emuTimer);
  try {
    const r = await apiGet('/emulators');
    if (!r.ok) { showError(container); return; }
    const emus = r.emulators || [];
    const isVisible = container === document.getElementById('content');
    container.innerHTML = '<div style="margin-bottom:8px;display:flex;align-items:center;gap:8px">'
      + '<span style="color:var(--text2);font-size:12px;font-weight:bold">📱 模拟器管理</span>'
      + '<span style="font-size:10px;color:var(--text3)">共 ' + emus.length + ' 个 | 每5s刷新</span>'
      + '<button class="small" onclick="renderEmus(document.getElementById(\'content\'))">刷新</button>'
      + '</div><div class="card-list" id="emus-list"></div>';
    const el = document.getElementById('emus-list');
    if (!el) return;
    let html = '';
    for (const e of emus) {
      const running = e.running ? true : false;
      const port = e.adb_port || '-';
      html += '<div class="card" style="padding:5px 8px">'
        + '<div style="flex:1;min-width:0">'
        + '<div style="display:flex;align-items:center;gap:4px">'
        + '<span style="width:8px;height:8px;border-radius:50%;background:' + (running ? 'var(--accent)' : 'var(--border)') + ';display:inline-block"></span>'
        + '<span style="font-size:11px;font-weight:bold">VM ' + e.index + '</span>'
        + '<span style="font-size:9px;color:var(--text3)">' + (e.name || '') + '</span>'
        + '<span style="font-size:9px;color:var(--text3)">ADB:' + port + '</span>'
        + '<span style="flex:1"></span>'
        + '<button class="small" onclick="emuControl(' + e.index + ',\'start\')" ' + (running ? 'disabled' : '') + ' style="font-size:9px;padding:1px 5px;' + (running ? 'opacity:0.4' : '') + '">▶ 启动</button>'
        + '<button class="small" onclick="emuControl(' + e.index + ',\'stop\')" ' + (!running ? 'disabled' : '') + ' style="font-size:9px;padding:1px 5px;color:var(--danger);' + (!running ? 'opacity:0.4' : '') + '">⏹ 关闭</button>'
        + '<button class="small" onclick="emuControl(' + e.index + ',\'restart\')" style="font-size:9px;padding:1px 5px">🔄 重启</button>'
        + '</div></div></div>';
    }
    el.innerHTML = html || '<div style="color:var(--text3);text-align:center;padding:20px">未检测到模拟器</div>';
  } catch(e) {}
  // Auto-refresh every 5s while on this page
  if (state.page === 'emus' && document.getElementById('content') === container) {
    if (window._emuTimer) clearInterval(window._emuTimer);
    window._emuTimer = setInterval(async () => {
      if (state.page !== 'emus') { clearInterval(window._emuTimer); window._emuTimer = null; return; }
      renderEmus(document.getElementById('content'));
    }, 5000);
  }
}
async function emuControl(idx, action) {
  const r = await apiPost('/emulator/' + idx + '/' + action, {});
  if (r.ok) toast(action === 'start' ? '启动中...' : action === 'stop' ? '关闭中...' : '重启中...');
  else toast(r.error || '操作失败', 'error');
  setTimeout(() => renderEmus(document.getElementById('content')), 2000);
}

// ── Operation Log ──
function toggleOplog() {
  var el = document.getElementById('oplog-panel');
  if (el) { el.remove(); return; }
  el = document.createElement('div');
  el.id = 'oplog-panel';
  el.style.cssText = 'position:fixed;top:0;right:0;width:380px;height:100vh;background:var(--bg2);border-left:1px solid var(--border);z-index:200;padding:12px;overflow-y:auto;font-size:11px;box-shadow:-4px 0 20px rgba(0,0,0,0.3)';
  el.innerHTML = '<div style="display:flex;align-items:center;gap:6px;margin-bottom:8px">'
    + '<span style="font-size:12px;font-weight:bold;color:var(--text2)">📋 操作记录</span>'
    + '<span style="flex:1"></span>'
    + '<button class="small" onclick="this.parentElement.parentElement.remove()">✕</button></div>'
    + '<div id="oplog-list"></div>';
  document.body.appendChild(el);
  loadOplogPanel();
}
async function loadOplogPanel() {
  try {
    var el = document.getElementById('oplog-list');
    if (!el) return;
    var r = await apiGet('/oplog');
    if (!r.ok || !r.ops) { el.innerHTML = '<div style="color:var(--text3);padding:10px">暂无记录</div>'; return; }
    el.innerHTML = r.ops.slice(-50).reverse().map(function(o) {
      return '<div style="padding:4px 0;border-bottom:1px solid var(--border);display:flex;gap:4px">'
        + '<span style="color:var(--text3);white-space:nowrap">' + (o.ts || '') + '</span>'
        + '<span style="color:var(--text)">' + (o.action || '') + '</span>'
        + (o.detail ? '<span style="color:var(--text3)">' + o.detail + '</span>' : '')
        + '</div>';
    }).join('');
  } catch(e) {}
}

async function renderLogs(container) {
  // Fetch accounts for MAA log selector
  const accts = state.accounts.length ? state.accounts : ((await apiGet('/accounts')).ok ? (await apiGet('/accounts')).accounts : []);
  container.innerHTML = `<div class="log-page">
    <div style="margin-bottom:8px;display:flex;align-items:center;gap:6px;flex-wrap:wrap">
      <span style="color:var(--text2);font-size:12px;font-weight:bold">动态</span>
      <select id="log-source" onchange="switchLogSource()" style="font-size:10px;padding:2px 4px;background:var(--bg3);border:1px solid var(--border);color:var(--text);border-radius:3px">
        <option value="app">系统日志</option>
        ${accts.map(a => `<option value="maa_${a.id}">MAA: ${a.name}</option>`).join('')}
      </select>
      <select id="log-filter" onchange="refreshLogFeed()" style="font-size:10px;padding:2px 4px;background:var(--bg3);border:1px solid var(--border);color:var(--text);border-radius:3px">
        <option value="all">全部</option>
        <option value="error">仅错误</option>
      </select>
      <button class="small" onclick="clearLogView()">清空</button>
      <button class="small" id="ai-analyze-btn" onclick="manualAIAnalyze()" style="color:var(--accent);display:none">🤖 AI 分析</button>
    </div>
    <pre id="log-feed" style="background:var(--bg3);border:1px solid var(--border);border-radius:var(--radius);padding:8px;font-size:11px;line-height:1.4;height:calc(100vh - 120px);overflow-y:auto;color:var(--text2);white-space:pre-wrap;word-break:break-all;font-family:Consolas,'Courier New',monospace"></pre>
  </div>`;
  await refreshLogFeed();
  if (logTimer) clearInterval(logTimer);
  logTimer = setInterval(() => {
    if (state.page !== 'logs') { clearInterval(logTimer); logTimer = null; return; }
    refreshLogFeed();
  }, 3000);
}

async function manualAIAnalyze() {
  if (!_logSource || !_logSource.startsWith('maa_')) return;
  const aid = _logSource.replace('maa_', '');
  const btn = document.getElementById('ai-analyze-btn');
  if (btn) { btn.textContent = '分析中...'; btn.disabled = true; }
  const r = await apiPost('/ai/analyze', { aid, exit_code: -11, failed_tasks: [] });
  if (btn) { btn.textContent = '🤖 AI 分析'; btn.disabled = false; }
  if (r.ok && r.result) {
    const ins = r.result;
    toast(`AI: ${ins.reason}`, ins.confidence === 'high' ? 'error' : 'info');
    if (ins.suggestion) toast(`建议: ${ins.suggestion}`, 'info');
  } else {
    toast('AI 分析失败', 'error');
  }
}

let _logSource = 'app';

function switchLogSource() {
  const sel = document.getElementById('log-source');
  _logSource = sel?.value || 'app';
  const btn = document.getElementById('ai-analyze-btn');
  if (btn) btn.style.display = _logSource.startsWith('maa_') ? 'inline-block' : 'none';
  refreshLogFeed();
}

async function refreshLogFeed() {
  try {
    const el = document.getElementById('log-feed');
    if (!el) return;
    const filter = document.getElementById('log-filter')?.value || 'all';
    const showOnlyError = filter === 'error';
    let lines = [];
    if (_logSource === 'app') {
      const resp = await fetch(API + '/logs?lines=200');
      const data = await resp.json();
      lines = data.lines || [];
    } else {
      const aid = _logSource.replace('maa_', '');
      const resp = await fetch(API + `/maa/log?aid=${aid}&lines=200`);
      const data = await resp.json();
      lines = data.lines || [];
    }
    if (showOnlyError) {
      lines = lines.filter(l => l.includes('[ERR]') || l.includes('ERROR') || l.includes('失败'));
    }
    el.textContent = lines.length ? lines.join('\n') : '(空)';
    el.scrollTop = el.scrollHeight;
  } catch(e) {}
}

function clearLogView() {
  const el = document.getElementById('log-feed');
  if (el) el.textContent = '(已清空)';
}

// ── Actions ──
async function stopAll() {
  if (!await showConfirm('确认停止所有运行中的账号？')) return;
  const r = await apiPost('/action/stop_all', {});
  if (r.ok) { toast(`已停止 ${r.count || 0} 个账号`); renderPage(); } else toast(r.error || '停止失败', 'error');
}
async function clearQueue() {
  if (!await showConfirm('确认清空排队队列？')) return;
  const r = await apiPost('/queue/clear', {});
  if (r.ok) { toast('队列已清空'); renderPage(); } else toast(r.error, 'error');
}
async function launchAccount(id) {
  const r = await apiPost(`/account/${state.accounts.findIndex(a => a.id === id)}/launch`, {});
  if (r.ok) toast('已启动'); else toast(r.error || '启动失败', 'error');
}
async function deleteAccount(id) {
  if (!await showConfirm('确认删除此账号？')) return;
  const idx = state.accounts.findIndex(a => a.id === id);
  const r = await apiPost(`/account/${idx}/delete`, {});
  if (r.ok) { toast('已删除'); renderPage(); } else toast(r.error, 'error');
}
async function showAccountDetail(id) {
  const a = state.accounts.find(x => x.id === id);
  if (!a) return;
  const idx = state.accounts.indexOf(a);
  state._detailId = id;
  navigate('account');
}

async function renderBatchEdit(container) {
  const ids = [...selectedIds];
  if (ids.length === 0) { container.innerHTML = '<div>请先选择账号</div>'; return; }
  const r = await apiGet('/accounts');
  if (!r.ok) { showError(container); return; }
  const accounts = r.accounts.filter(a => ids.includes(a.id));
  if (accounts.length === 0) return;
  const total = accounts.length;
  let curIdx = 0;
  
  function renderForm(idx) {
    const a = accounts[idx];
    container.innerHTML = `<div>
      <div class="pager">
        <button onclick="prevPage()">◀ 上一页</button>
        <span>${idx+1} / ${total}</span>
        <button onclick="nextPage()">下一页 ▶</button>
      </div>
      <div style="color:var(--text2);font-size:11px;margin-bottom:8px">
        账号: ${a.name} | VM ${a.emu_instance_index||'?'} | ${a.game_client||'?'}
      </div>
      <div class="form-row"><label>默认关卡</label><input id="be-stage" value="${a.smart_stage||''}" placeholder="留空不修改"></div>
      <div class="form-row"><label>客户端</label><select id="be-client">
        <option value="">不修改</option>
        <option value="Official" ${a.game_client==='Official'?'selected':''}>官服</option>
        <option value="Bilibili" ${a.game_client==='Bilibili'?'selected':''}>B服</option>
        <option value="YoStarEN">美服</option>
        <option value="YoStarJP">日服</option>
        <option value="YoStarKR">韩服</option>
        <option value="txwy">繁中服</option>
      </select></div>
      <div class="form-row"><label>完成后</label>
        <label><input type="checkbox" id="be-exit-emu">关模拟器</label>
        <label><input type="checkbox" id="be-exit-self">退MAA</label>
      </div>
      <div class="btn-row">
        <button class="primary" onclick="saveBatchItem(${idx})">保存</button>
      </div>
    </div>`;
    curIdx = idx;
    const pa = a.post_action || '';
    const be1 = document.getElementById('be-exit-emu');
    const be2 = document.getElementById('be-exit-self');
    if (be1) be1.checked = pa.includes('ExitEmulator');
    if (be2) be2.checked = pa.includes('ExitSelf');
  }
  
  window.prevPage = () => { if (curIdx > 0) saveBatchItem(curIdx - 1); renderForm(curIdx - 1); };
  window.nextPage = () => { if (curIdx < total - 1) saveBatchItem(curIdx + 1); renderForm(curIdx + 1); };
  window.saveBatchItem = async (nextIdx) => {
    const a = accounts[curIdx];
    const idx = r.accounts.indexOf(a);
    const body = {};
    const stage = document.getElementById('be-stage')?.value?.trim();
    const client = document.getElementById('be-client')?.value;
    const exE = document.getElementById('be-exit-emu')?.checked;
    const exS = document.getElementById('be-exit-self')?.checked;
    if (stage) { body.smart_stage = stage; body.fight_stage = stage; }
    if (client) body.game_client = client;
    if (exE || exS) {
      const acts = [];
      if (exE) acts.push('ExitEmulator');
      if (exS) acts.push('ExitSelf');
      body.post_action = acts.join(',');
    }
    const res = await apiPost(`/account/${idx}/edit`, body);
    if (res.ok) toast(`已保存 ${a.name}`);
    if (nextIdx !== undefined && nextIdx !== curIdx) {
      renderForm(nextIdx);
    }
  };
  
  renderForm(0);
}

async function renderAccount(container) {
  const id = state._detailId;
  if (!id) { container.innerHTML = '<div>未选择账号</div>'; return; }
  const r = await apiGet('/accounts');
  if (!r.ok) { showError(container); return; }
  const a = r.accounts.find(x => x.id === id);
  if (!a) { container.innerHTML = '<div>账号不存在</div>'; return; }
  const idx = r.accounts.indexOf(a);
  
  container.innerHTML = `<div>
    <button onclick="navigate('accounts')" style="margin-bottom:8px">← 返回</button>
    <div class="form-row"><label>名称</label><input id="ed-name" value="${a.name}"></div>
    <div class="form-row"><label>服务器</label><select id="ed-client" style="flex:1;padding:4px 8px;background:var(--bg3);border:1px solid var(--border);color:var(--text);border-radius:var(--radius);font-size:11px">
      <option value="Official" ${a.game_client==='Official'?'selected':''}>官服</option>
      <option value="Bilibili" ${a.game_client==='Bilibili'?'selected':''}>B服</option>
      <option value="YoStarEN" ${a.game_client==='YoStarEN'?'selected':''}>美服</option>
      <option value="YoStarJP" ${a.game_client==='YoStarJP'?'selected':''}>日服</option>
      <option value="YoStarKR" ${a.game_client==='YoStarKR'?'selected':''}>韩服</option>
      <option value="txwy" ${a.game_client==='txwy'?'selected':''}>繁中服</option>
    </select></div>
    <div class="form-row"><label>模拟器</label>
      <select id="ed-vm" style="flex:1;padding:4px 8px;background:var(--bg3);border:1px solid var(--border);color:var(--text);border-radius:var(--radius);font-size:11px">
        <option value="">未绑定</option>
        ${Array.from({length:50}, (_,i) => `<option value="${i}" ${a.emu_instance_index==String(i)?'selected':''}>VM ${i}</option>`).join('')}
      </select>
    </div>
    <div class="form-row"><label>切换账号</label><input id="ed-switch" value="${a.account_switch||''}" placeholder="游戏内切换账号标识"></div>
    <div class="form-row"><label>游戏 UID</label><input id="ed-uid" value="${a.uid||''}" placeholder="游戏内 UID"></div>
    <div class="form-row"><label>备注</label><input id="ed-note" value="${a.note||''}" placeholder="任意备注"></div>
    <div class="form-row"><label>到期</label><input type="date" id="ed-expire" value="${a.expire_date||''}"></div>
    <div class="form-row"><label>可刷关卡</label><div id="ed-stages" style="flex:1;display:flex;gap:3px;flex-wrap:wrap;font-size:11px">加载中...</div></div>
    <div style="border-top:1px solid var(--border);margin:8px 0;padding-top:8px">
      <div style="font-size:12px;color:var(--text2);margin-bottom:4px">操作</div>
      <button onclick="launchAccount('${a.id}')" style="margin-right:4px">▶ 启动</button>
      <button onclick="previewEmulator('${a.id}',${idx})" style="margin-right:4px">📷 画面预览</button>
      <button onclick="showFightConfig('${a.id}')" style="margin-right:4px">⚔ 刷关策略</button>
      <button class="danger" onclick="showConfirm('确认删除 ${a.name}？').then(r=>r&&deleteAccount('${a.id}'))">🗑 删除</button>
    </div>
    <div id="emu-preview" style="display:none;margin-top:8px">
      <img id="emu-shot" style="width:100%;max-width:480px;border-radius:var(--radius);border:1px solid var(--border)">
      <div style="font-size:9px;color:var(--text3);margin-top:4px">每2秒刷新 · <a href="#" onclick="stopPreview()">停止预览</a></div>
    </div>
    <div class="btn-row">
      <button class="primary" onclick="saveAccountDetail('${a.id}',${idx})">保存</button>
    </div>
  </div>`;
  // Load stage library for account edit
  (async () => {
    const sr = await apiGet('/stages');
    if (!sr.ok || !sr.stages) return;
    const el = document.getElementById('ed-stages');
    if (!el) return;
    const accountStages = a.stages || [];
    el.innerHTML = sr.stages.map(s => `<label style="display:flex;align-items:center;gap:4px;padding:2px 6px;background:var(--bg3);border-radius:3px;cursor:pointer">
      <input type="checkbox" class="ed-stage-cb" value="${s.id}" ${accountStages.includes(s.id)?'checked':''}> ${s.name}
    </label>`).join('') || '<span style="color:var(--text3)">暂无可用关卡，先去设置添加</span>';
  })();
}

async function saveAccountDetail(id, idx) {
  const name = document.getElementById('ed-name')?.value?.trim();
  const client = document.getElementById('ed-client')?.value;
  const vm = document.getElementById('ed-vm')?.value;
  const sw = document.getElementById('ed-switch')?.value?.trim();
  const uid = document.getElementById('ed-uid')?.value?.trim();
  const note = document.getElementById('ed-note')?.value?.trim();
  const expire = document.getElementById('ed-expire')?.value;
  const body = { name, game_client: client, emu_instance_index: vm || '' };
  if (sw) body.account_switch = sw;
  if (uid) body.uid = uid;
  body.note = note || '';
  body.expire_date = expire || '';
  // Collect stages from checkboxes
  const stageCbs = document.querySelectorAll('.ed-stage-cb');
  if (stageCbs.length) {
    body.stages = [...stageCbs].filter(cb => cb.checked).map(cb => cb.value);
  }
  const r = await apiPost(`/account/${idx}/edit`, body);
  if (r.ok) toast('已保存');
  else toast(r.error || '保存失败', 'error');
}
let _previewTimer = null;
function previewEmulator(id, idx) {
  const preview = document.getElementById('emu-preview');
  const img = document.getElementById('emu-shot');
  if (!preview || !img) return;
  preview.style.display = 'block';
  if (_previewTimer) clearInterval(_previewTimer);
  const update = () => { img.src = API + `/account/${idx}/screenshot?_t=${Date.now()}`; };
  update();
  _previewTimer = setInterval(update, 2000);
}
function stopPreview() {
  if (_previewTimer) { clearInterval(_previewTimer); _previewTimer = null; }
  const preview = document.getElementById('emu-preview');
  if (preview) preview.style.display = 'none';
}
const _previewTimers = {};
function togglePreview(aid, name) {
  const el = document.getElementById(`preview-${aid}`);
  if (!el) return;
  if (el.style.display !== 'none') {
    el.style.display = 'none';
    if (_previewTimers[aid]) { clearInterval(_previewTimers[aid]); delete _previewTimers[aid]; }
    return;
  }
  el.style.display = 'block';
  const img = el.querySelector('img');
  if (!img) return;
  const accts = state.accounts || [];
  const idx = accts.findIndex(a => a.id === aid);
  if (idx < 0) return;
  const update = () => { img.src = API + `/account/${idx}/screenshot?_t=${Date.now()}`; img.style.display = ''; };
  update();
  _previewTimers[aid] = setInterval(update, 2000);
}

async function renderTaskConfig(container) {
  const id = state._detailId;
  if (!id) { container.innerHTML = '<div>未选择账号</div>'; return; }
  const r = await apiGet('/accounts');
  if (!r.ok) { showError(container); return; }
  const a = r.accounts.find(x => x.id === id);
  if (!a) { container.innerHTML = '<div>账号不存在</div>'; return; }
  const idx = r.accounts.indexOf(a);
  const ts = a.task_settings || {};
  
  container.innerHTML = `<div>
    <button onclick="navigate('account')" style="margin-bottom:8px">← 返回 ${a.name}</button>
    <h3 style="margin-bottom:8px">任务配置 — ${a.name}</h3>
    <div class="tabs" id="cfg-tabs">
      <div class="tab active" data-tab="startup">启动</div>
      <div class="tab" data-tab="fight">刷关</div>
      <div class="tab" data-tab="recruit">招募</div>
      <div class="tab" data-tab="infrast">基建</div>
      <div class="tab" data-tab="mall">商店</div>
      <div class="tab" data-tab="award">领取</div>
    </div>
    <div id="cfg-tab-content"></div>
    <div class="btn-row" style="margin-top:12px">
      <button class="primary" onclick="saveTaskConfig(${idx})">保存配置</button>
    </div>
  </div>`;
  
  renderCfgTab('startup', ts);
  
  // Tab switching
  container.querySelectorAll('#cfg-tabs .tab').forEach(tab => {
    tab.addEventListener('click', () => {
      container.querySelectorAll('#cfg-tabs .tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      renderCfgTab(tab.dataset.tab, ts);
    });
  });
}

function renderCfgTab(tab, ts) {
  const ct = document.getElementById('cfg-tab-content');
  const ft = ts.Fight || {};
  const rt = ts.Recruit || {};
  const it = ts.Infrast || {};
  const mt = ts.Mall || {};
  const at = ts.Award || {};
  
  switch(tab) {
    case 'startup':
      ct.innerHTML = `<div class="form-row"><label>账号切换</label><input id="cfg-acct-switch" value="${ts.account_switch||''}" placeholder="留空用当前账号"></div>`;
      break;
    case 'fight':
      ct.innerHTML = `<div class="form-row"><label>吃药</label><input type="checkbox" id="cfg-med" ${ft.use_medicine?'checked':''}></div>
        <div class="form-row"><label>过期药</label><input type="checkbox" id="cfg-exp-med" ${ft.use_expiring_medicine?'checked':''}></div>
        <div class="form-row"><label>过期天数</label><input type="number" id="cfg-med-days" value="${ft.medicine_expire_days||2}" min="1" max="7"></div>
        <div class="form-row"><label>重置模式</label><select id="cfg-reset"><option value="Current" ${(ft.stage_reset_mode||'Current')==='Current'?'selected':''}>当前</option><option value="Last" ${ft.stage_reset_mode==='Last'?'selected':''}>上周</option><option value="Yesterday" ${ft.stage_reset_mode==='Yesterday'?'selected':''}>昨日</option><option value="Clear" ${ft.stage_reset_mode==='Clear'?'selected':''}>已通关</option></select></div>
        <div class="form-row"><label>次数</label><input type="number" id="cfg-times" value="${ft.times||99}" min="1" max="999"></div>
        <div class="form-row"><label>限次</label><input type="checkbox" id="cfg-limit" ${ft.enable_times_limit?'checked':''}></div>
        <div class="form-row"><label>搓玉</label><input type="checkbox" id="cfg-grandet" ${ft.is_dr_grandet?'checked':''}></div>
        <div class="form-row"><label>可选关</label><input type="checkbox" id="cfg-opt" ${ft.use_optional_stage?'checked':''}></div>
        <div class="form-row"><label>仓库目标</label><input type="checkbox" id="cfg-inv" ${ft.is_inventory_target?'checked':''}></div>
        <div class="form-row"><label>周计划</label><input type="checkbox" id="cfg-weekly" ${ft.use_weekly_schedule?'checked':''}></div>`;
      break;
    case 'recruit':
      ct.innerHTML = `<div class="form-row"><label>刷新</label><input type="checkbox" id="cfg-refresh" ${rt.refresh!==false?'checked':''}></div>
        <div class="form-row"><label>强制刷新</label><input type="checkbox" id="cfg-force" ${rt.force_refresh!==false?'checked':''}></div>
        <div class="form-row"><label>次数</label><input type="number" id="cfg-rec-times" value="${rt.times||4}" min="1" max="20"></div>
        <div class="form-row"><label>3星时间</label><input type="number" id="cfg-l3t" value="${rt.level3_time||540}" min="60"> 分</div>
        <div class="form-row"><label>保留词条</label><input id="cfg-preserve" value="${rt.preserve_tags||'支援机械'}" placeholder="用;分隔"></div>
        <div class="form-row"><label>保留启用</label><input type="checkbox" id="cfg-preserve-en" ${rt.preserve_tag_enabled?'checked':''}></div>`;
      break;
    case 'infrast':
      ct.innerHTML = `<div class="form-row"><label>模式</label><select id="cfg-inf-mode"><option value="Normal" ${(it.mode||'Normal')==='Normal'?'selected':''}>常规</option><option value="Rotation" ${it.mode==='Rotation'?'selected':''}>轮换</option></select></div>
        <div class="form-row"><label>无人机</label><select id="cfg-drones"><option value="Money" ${(it.drones||'Money')==='Money'?'selected':''}>贸易</option><option value="Combat" ${it.drones==='Combat'?'selected':''}>制造</option></select></div>
        <div class="form-row"><label>宿舍阈值</label><input type="number" id="cfg-dorm" value="${it.dorm_threshold||30}" min="0" max="100"> %</div>
        <div class="form-row"><label>宿舍信任</label><input type="checkbox" id="cfg-dorm-trust" ${it.dorm_trust_enabled!==false?'checked':''}></div>
        <div class="form-row"><label>自动搓玉</label><input type="checkbox" id="cfg-shard" ${it.originium_shard_auto!==false?'checked':''}></div>
        <div class="form-row"><label>线索</label><input type="checkbox" id="cfg-clue" ${it.reception_clue!==false?'checked':''}></div>
        <div class="form-row"><label>送线索</label><input type="checkbox" id="cfg-send" ${it.send_clue!==false?'checked':''}></div>
        <div class="form-row"><label>继续训练</label><input type="checkbox" id="cfg-train" ${it.continue_training?'checked':''}></div>`;
      break;
    case 'mall':
      ct.innerHTML = `<div class="form-row"><label>购物</label><input type="checkbox" id="cfg-shop" ${mt.shopping!==false?'checked':''}></div>
        <div class="form-row"><label>信用战</label><input type="checkbox" id="cfg-cf" ${mt.credit_fight?'checked':''}></div>
        <div class="form-row"><label>访问好友</label><input type="checkbox" id="cfg-vf" ${mt.visit_friends!==false?'checked':''}></div>
        <div class="form-row"><label>黑名单</label><input id="cfg-bl" value="${mt.blacklist||'碳;家具;加急许可'}" placeholder="用;分隔"></div>
        <div class="form-row"><label>优先购买</label><input id="cfg-fl" value="${mt.first_list||'招聘许可'}"></div>
        <div class="form-row"><label>仅折扣</label><input type="checkbox" id="cfg-od" ${mt.only_buy_discount?'checked':''}></div>
        <div class="form-row"><label>保留信用</label><input type="checkbox" id="cfg-rm" ${mt.reserve_max_credit?'checked':''}></div>`;
      break;
    case 'award':
      ct.innerHTML = `<div class="form-row"><label>奖励</label><input type="checkbox" id="cfg-aw" ${at.award!==false?'checked':''}></div>
        <div class="form-row"><label>邮件</label><input type="checkbox" id="cfg-mail" ${at.mail!==false?'checked':''}></div>
        <div class="form-row"><label>免费抽</label><input type="checkbox" id="cfg-gacha" ${at.free_gacha?'checked':''}></div>
        <div class="form-row"><label>合成玉</label><input type="checkbox" id="cfg-oru" ${at.orundum!==false?'checked':''}></div>
        <div class="form-row"><label>采矿</label><input type="checkbox" id="cfg-mine" ${at.mining?'checked':''}></div>
        <div class="form-row"><label>特别许可</label><input type="checkbox" id="cfg-sp" ${at.special_access?'checked':''}></div>`;
      break;
    default:
      ct.innerHTML = '';
  }
}

function collectTaskConfig() {
  return {
    account_switch: getVal('cfg-acct-switch'),
    Fight: {
      use_medicine: isChecked('cfg-med'),
      use_expiring_medicine: isChecked('cfg-exp-med'),
      medicine_expire_days: parseInt(getVal('cfg-med-days')) || 2,
      stage_reset_mode: getVal('cfg-reset') || 'Current',
      times: parseInt(getVal('cfg-times')) || 99,
      enable_times_limit: isChecked('cfg-limit'),
      is_dr_grandet: isChecked('cfg-grandet'),
      use_optional_stage: isChecked('cfg-opt'),
      is_inventory_target: isChecked('cfg-inv'),
      use_weekly_schedule: isChecked('cfg-weekly'),
    },
    Recruit: {
      refresh: isChecked('cfg-refresh'),
      force_refresh: isChecked('cfg-force'),
      times: parseInt(getVal('cfg-rec-times')) || 4,
      level3_time: parseInt(getVal('cfg-l3t')) || 540,
      preserve_tags: getVal('cfg-preserve') || '支援机械',
      preserve_tag_enabled: isChecked('cfg-preserve-en'),
    },
    Infrast: {
      mode: getVal('cfg-inf-mode') || 'Normal',
      drones: getVal('cfg-drones') || 'Money',
      dorm_threshold: parseInt(getVal('cfg-dorm')) || 30,
      dorm_trust_enabled: isChecked('cfg-dorm-trust'),
      originium_shard_auto: isChecked('cfg-shard'),
      reception_clue: isChecked('cfg-clue'),
      send_clue: isChecked('cfg-send'),
      continue_training: isChecked('cfg-train'),
    },
    Mall: {
      shopping: isChecked('cfg-shop'),
      credit_fight: isChecked('cfg-cf'),
      visit_friends: isChecked('cfg-vf'),
      blacklist: getVal('cfg-bl') || '碳;家具;加急许可',
      first_list: getVal('cfg-fl') || '招聘许可',
      only_buy_discount: isChecked('cfg-od'),
      reserve_max_credit: isChecked('cfg-rm'),
    },
    Award: {
      award: isChecked('cfg-aw'),
      mail: isChecked('cfg-mail'),
      free_gacha: isChecked('cfg-gacha'),
      orundum: isChecked('cfg-oru'),
      mining: isChecked('cfg-mine'),
      special_access: isChecked('cfg-sp'),
    },
  };
}

function getVal(id) { const e = document.getElementById(id); return e ? e.value : ''; }
function isChecked(id) { const e = document.getElementById(id); return e ? e.checked : false; }

async function saveTaskConfig(idx) {
  const cfg = collectTaskConfig();
  const r = await apiPost(`/account/${idx}/config`, { task_settings: cfg });
  if (r.ok) toast('配置已保存');
  else toast(r.error || '保存失败', 'error');
}
async function saveAccount(id, idx) {
  const overlay = document.querySelector('.dialog-overlay');
  const name = document.getElementById('ed-name').value.trim();
  const client = document.getElementById('ed-client').value;
  const vm = document.getElementById('ed-vm').value.trim();
  const adb = document.getElementById('ed-adb').value.trim();
  const r = await apiPost(`/account/${idx}/edit`, { name, game_client: client, emu_instance_index: vm, adb_address: adb });
  if (r.ok) { toast('已保存'); if (overlay) overlay.remove(); renderPage(); }
  else toast(r.error || '保存失败', 'error');
}
async function createAccount(input) {
  const file = input.files[0];
  if (!file) return;
  const name = file.name.replace(/\.exe$/,'').trim() || '新账号';
  const r = await apiPost('/account', { name, game_client: 'Official' });
  if (r.ok) { toast(`已创建 ${name}`); renderPage(); } else toast(r.error || '创建失败', 'error');
  input.value = '';
}
async function showFightConfig(aid) {
  _fcMonthly = {};
  const r = await apiGet('/accounts');
  if (!r.ok) return;
  const a = r.accounts.find(x => x.id === aid);
  if (!a) return;
  const idx = r.accounts.indexOf(a);
  const mode = a.fight_mode || 'schedule';
  const defStage = a.fight_default || '1-7';
  const weekly = a.schedule_weekly || {};
  const monthly = a.schedule_monthly || {};
  const prio = a.fight_priority || {};
  const mats = a.fight_materials || [];
  const dayNames = {mon:'周一',tue:'周二',wed:'周三',thu:'周四',fri:'周五',sat:'周六',sun:'周日'};

  const html = `<div class="dialog-overlay" onclick="if(event.target===this)this.remove()">
    <div class="dialog" style="max-width:550px">
      <div style="font-size:14px;font-weight:bold;margin-bottom:8px;color:var(--text2)">⚔ 刷关策略 · ${a.name}</div>
      <div class="form-row"><label>默认关卡</label><input id="fc-default" value="${defStage}" placeholder="1-7"></div>
      <div class="form-row"><label>刷关模式</label>
        <select id="fc-mode" onchange="fcToggleMode(this.value)" style="flex:1;padding:4px 8px;background:var(--bg3);border:1px solid var(--border);color:var(--text);border-radius:var(--radius);font-size:11px">
          <option value="schedule" ${mode==='schedule'?'selected':''}>📅 按计划</option>
          <option value="priority" ${mode==='priority'?'selected':''}>📊 按优先级</option>
          <option value="material" ${mode==='material'?'selected':''}>📦 按材料</option>
        </select>
      </div>
      <div id="fc-schedule" style="${mode!=='schedule'?'display:none':''}">
        <div style="font-size:10px;color:var(--text3);margin-bottom:4px">每周计划（留空=不限制）</div>
        ${Object.entries(dayNames).map(([k,v]) =>
          `<div class="form-row"><label style="min-width:40px">${v}</label><input id="fc-w-${k}" value="${weekly[k]||''}" placeholder="如CE-6" style="flex:1"></div>`
        ).join('')}
        <div style="font-size:10px;color:var(--text3);margin-bottom:4px;margin-top:4px">每月计划（日期→关卡）</div>
        <div id="fc-monthly-list">${Object.entries(monthly).map(([d,s]) =>
          `<div class="form-row"><label style="min-width:40px">${d}号</label><input value="${s}" onchange="fcSetMonthly('${d}',this.value)" placeholder="关卡" style="flex:1"><button class="small" onclick="fcRemoveMonthly('${d}')">✕</button></div>`
        ).join('')}</div>
        <button class="small" onclick="fcAddMonthly()">＋ 添加月计划</button>
      </div>
      <div id="fc-priority" style="${mode!=='priority'?'display:none':''}">
        <div style="font-size:10px;color:var(--text3);margin-bottom:4px">关卡优先级（数字越大越优先）</div>
        ${(a.stages||[]).map(s =>
          `<div class="form-row"><label style="min-width:60px">${s}</label><input type="number" id="fc-p-${s}" value="${prio[s]||0}" min="0" max="10" style="width:60px"></div>`
        ).join('')}
      </div>
      <div id="fc-material" style="${mode!=='material'?'display:none':''}">
        <div style="font-size:10px;color:var(--text3);margin-bottom:4px">材料目标</div>
        <div id="fc-mat-list">${mats.map((m,i) =>
          `<div class="form-row"><label>${m.item||'?'}</label><input id="fc-mt-${i}-target" type="number" value="${m.target||0}" min="0" style="width:60px" placeholder="目标"><input id="fc-mt-${i}-achieved" type="number" value="${m.achieved||0}" min="0" style="width:60px" placeholder="已刷"><span style="font-size:9px;color:var(--text3);min-width:40px">${m.achieved||0}/${m.target||0}</span><button class="small" onclick="fcRemoveMat(${i})">✕</button></div>`
        ).join('')}</div>
        <div style="display:flex;gap:4px;margin-top:4px">
          <input id="fc-new-mat-item" placeholder="材料名" style="flex:1;padding:2px 6px;background:var(--bg3);border:1px solid var(--border);color:var(--text);border-radius:var(--radius);font-size:10px">
          <input id="fc-new-mat-target" type="number" placeholder="目标" style="width:60px;padding:2px 6px;background:var(--bg3);border:1px solid var(--border);color:var(--text);border-radius:var(--radius);font-size:10px">
          <button class="small" onclick="fcAddMat()">添加</button>
        </div>
      </div>
      <div class="btn-row" style="margin-top:8px">
        <button class="primary" onclick="saveFightConfig('${aid}',${idx})">💾 保存</button>
        <button onclick="this.closest('.dialog-overlay').remove()">取消</button>
      </div>
    </div>
  </div>`;
  document.body.insertAdjacentHTML('beforeend', html);
}

// Fight config helpers (global, used by onchange inline handlers)
let _fcMonthly = {};
function fcToggleMode(val) {
  document.getElementById('fc-schedule').style.display = val==='schedule'?'':'none';
  document.getElementById('fc-priority').style.display = val==='priority'?'':'none';
  document.getElementById('fc-material').style.display = val==='material'?'':'none';
}
function fcAddMonthly() {
  const d = prompt('输入日期（1-31）:');
  if (!d || isNaN(d) || d<1 || d>31) return;
  const div = document.getElementById('fc-monthly-list');
  div.innerHTML += `<div class="form-row"><label style="min-width:40px">${d}号</label><input onchange="fcSetMonthly('${d}',this.value)" placeholder="关卡" style="flex:1"><button class="small" onclick="fcRemoveMonthly('${d}')">✕</button></div>`;
}
function fcSetMonthly(d, val) {
  if (val) _fcMonthly[d] = val;
  else delete _fcMonthly[d];
}
function fcRemoveMonthly(d) {
  delete _fcMonthly[d];
  const div = document.getElementById('fc-monthly-list');
  const els = div.querySelectorAll('.form-row');
  for (const el of els) {
    if (el.querySelector('label')?.textContent === d+'号') { el.remove(); break; }
  }
}
function fcAddMat() {
  const item = document.getElementById('fc-new-mat-item')?.value?.trim();
  const target = parseInt(document.getElementById('fc-new-mat-target')?.value) || 0;
  if (!item || !target) return;
  const div = document.getElementById('fc-mat-list');
  const i = div.children.length;
  div.innerHTML += `<div class="form-row"><label>${item}</label><input id="fc-mt-${i}-target" type="number" value="${target}" min="0" style="width:60px"><input id="fc-mt-${i}-achieved" type="number" value="0" min="0" style="width:60px"><span style="font-size:9px;color:var(--text3);min-width:40px">0/${target}</span><button class="small" onclick="fcRemoveMat(${i})">✕</button></div>`;
  document.getElementById('fc-new-mat-item').value = '';
  document.getElementById('fc-new-mat-target').value = '';
}
function fcRemoveMat(idx) {
  const el = document.getElementById('fc-mat-list').children[idx];
  if (el) el.remove();
}
async function saveFightConfig(aid, idx) {
  const mode = document.getElementById('fc-mode')?.value || 'schedule';
  const defStage = document.getElementById('fc-default')?.value || '1-7';
  // Weekly schedule
  const weekly = {};
  for (const d of ['mon','tue','wed','thu','fri','sat','sun']) {
    const v = document.getElementById('fc-w-'+d)?.value?.trim();
    if (v) weekly[d] = v;
  }
  // Monthly schedule
  const monthly = {};
  for (const el of document.getElementById('fc-monthly-list')?.querySelectorAll('.form-row') || []) {
    const label = el.querySelector('label')?.textContent || '';
    const day = label.replace('号','');
    const input = el.querySelector('input');
    if (day && input?.value?.trim()) monthly[day] = input.value.trim();
  }
  // Priority
  const priority = {};
  for (const el of document.getElementById('fc-priority')?.querySelectorAll('.form-row') || []) {
    const label = el.querySelector('label')?.textContent;
    const val = parseInt(el.querySelector('input')?.value) || 0;
    if (label && val > 0) priority[label] = val;
  }
  // Materials
  const materials = [];
  for (const el of document.getElementById('fc-mat-list')?.children || []) {
    const label = el.querySelector('label')?.textContent || '';
    const inputs = el.querySelectorAll('input');
    const target = parseInt(inputs[0]?.value) || 0;
    const achieved = parseInt(inputs[1]?.value) || 0;
    if (label && target > 0) materials.push({item: label, target, achieved});
  }
  // Collect monthly from inline _fcMonthly
  for (const [d, v] of Object.entries(_fcMonthly)) {
    if (v && !monthly[d]) monthly[d] = v;
  }

  const body = { fight_mode: mode, fight_default: defStage, schedule_weekly: weekly, schedule_monthly: monthly, fight_priority: priority, fight_materials: materials };
  const r = await apiPost(`/account/${idx}/fight_config`, body);
  if (r.ok) {
    toast('刷关策略已保存');
    document.querySelector('.dialog-overlay')?.remove();
  } else {
    toast(r.error || '保存失败', 'error');
  }
}
function showCreateAccountForm(preset) {
  const html = `<div class="dialog-overlay" onclick="event.target==this&&this.remove()">
    <div class="dialog" style="max-width:420px">
      <div style="font-size:14px;font-weight:bold;margin-bottom:8px;color:var(--text2)">创建账号</div>
      <div style="display:flex;gap:4px;margin-bottom:8px;flex-wrap:wrap">
        <button class="small" onclick="quickCreate('Official')" style="background:var(--accent);color:#fff;border-color:var(--accent);font-size:10px">＋ 官服</button>
        <button class="small" onclick="quickCreate('Bilibili')" style="font-size:10px">＋ B服</button>
        <button class="small" onclick="quickCreate('YoStarJP')" style="font-size:10px">＋ 日服</button>
        <button class="small" onclick="quickCreate('YoStarEN')" style="font-size:10px">＋ 美服</button>
      </div>
      <div id="create-form-loading" style="text-align:center;padding:20px;color:var(--text3);font-size:11px">正在检测模拟器...</div>
    </div>
  </div>`;
  document.body.insertAdjacentHTML('beforeend', html);
  loadCreateForm();
}
async function loadCreateForm() {
  try {
    const emuR = await apiGet('/emulators');
    const emus = emuR.ok ? (emuR.emulators || []) : [];
    const loading = document.getElementById('create-form-loading');
    if (!loading) return;
    window._emuList = emus;
    loading.outerHTML = `
      <div class="form-row"><label>名称</label><input type="text" id="form-name" value="" placeholder="账号名称"></div>
      <div class="form-row"><label>服务器</label><select id="form-client" style="flex:1;padding:4px 8px;background:var(--bg3);border:1px solid var(--border);color:var(--text);border-radius:var(--radius);font-size:11px"><option value="Official">官服</option><option value="Bilibili">B服</option><option value="YoStarEN">美服</option><option value="YoStarJP">日服</option><option value="YoStarKR">韩服</option><option value="txwy">繁中服</option></select></div>
      <div class="form-row" style="flex-direction:column;align-items:stretch">
        <label style="margin-bottom:2px">模拟器 (${emus.length} 个)</label>
        <input type="text" id="form-emu-search" placeholder="搜索 VM 编号..." oninput="filterEmuList()" style="padding:4px 8px;background:var(--bg3);border:1px solid var(--border);color:var(--text);border-radius:var(--radius);font-size:11px">
        <div id="form-emu-list" style="max-height:160px;overflow-y:auto;margin-top:4px;border:1px solid var(--border);border-radius:var(--radius);background:var(--bg3)"></div>
      </div>
      <div id="form-auto-info" style="font-size:9px;color:var(--text3);padding:2px 0 6px;display:none"></div>
      <div class="form-row"><label>切换账号</label><input type="text" id="form-switch" value="" placeholder="游戏内切换账号标识(可选)"></div>
      <div class="form-row"><label>游戏 UID</label><input type="text" id="form-uid" value="" placeholder="游戏内 UID(可选)"></div>
      <div class="form-row"><label>备注</label><input type="text" id="form-note" value="" placeholder="任意备注(可选)"></div>
      <div class="form-row"><label>到期</label><input type="date" id="form-expire" value=""></div>
      <div class="btn-row" style="margin-top:10px">
        <button class="primary" onclick="submitCreateAccount()">创建</button>
        <button onclick="this.closest('.dialog-overlay').remove()">取消</button>
      </div>`;
    renderEmuList(emus);
  } catch(e) {
    const loading = document.getElementById('create-form-loading');
    if (loading) loading.textContent = '检测失败，请手动填写';
  }
}
function renderEmuList(emus) {
  const el = document.getElementById('form-emu-list');
  if (!el) return;
  if (!emus.length) { el.innerHTML = '<div style="padding:8px;color:var(--text3);font-size:10px;text-align:center">无匹配模拟器</div>'; return; }
  el.innerHTML = emus.map(e => {
    const sel = window._emuData && window._emuData.idx === e.index ? 'selected' : '';
    const label = `${e.emu} ${e.name} · VM ${e.index}` + (e.adb_port ? ` · :${e.adb_port}` : '') + (e.running ? ' 🟢' : '');
    return `<div class="emu-item ${sel}" data-idx="${e.index}" data-port="${e.adb_port||''}" data-emu="${e.emu||''}" data-name="${e.name||''}"
      onclick="selectEmu(this)" style="padding:4px 8px;font-size:10px;cursor:pointer;border-bottom:1px solid var(--border);background:${sel ? 'var(--accent)' : 'transparent'};color:${sel ? '#fff' : 'var(--text)'}">${label}</div>`;
  }).join('');
}
function filterEmuList() {
  const q = (document.getElementById('form-emu-search')?.value || '').toLowerCase();
  const emus = (window._emuList || []).filter(e =>
    (e.name||'').toLowerCase().includes(q) || String(e.index).includes(q) || (e.emu||'').toLowerCase().includes(q)
  );
  renderEmuList(emus);
}
function selectEmu(el) {
  document.querySelectorAll('.emu-item').forEach(d => { d.style.background = ''; d.style.color = 'var(--text)'; });
  el.style.background = 'var(--accent)'; el.style.color = '#fff';
  window._emuData = {idx: el.dataset.idx, port: el.dataset.port, emu: el.dataset.emu, name: el.dataset.name};
  const info = document.getElementById('form-auto-info');
  if (info) {
    const port = el.dataset.port || (16384 + parseInt(el.dataset.idx||0) * 32);
    info.innerHTML = `已选: VM ${el.dataset.idx} · ADB 127.0.0.1:${port} · ${el.dataset.emu} ${el.dataset.name}`;
    info.style.display = '';
  }
}
async function submitCreateAccount() {
  const name = document.getElementById('form-name')?.value?.trim();
  if (!name) { toast('请输入名称', 'error'); return; }
  const body = {
    name,
    game_client: document.getElementById('form-client')?.value || 'Official',
    account_switch: document.getElementById('form-switch')?.value?.trim() || '',
    uid: document.getElementById('form-uid')?.value?.trim() || '',
    note: document.getElementById('form-note')?.value?.trim() || '',
    expire_date: document.getElementById('form-expire')?.value || '',
  };
  const emuData = window._emuData;
  body.emu_instance_index = emuData ? emuData.idx : '';
  const r = await apiPost('/account', body);
  document.querySelector('.dialog-overlay')?.remove();
  window._emuData = null;
  if (r.ok) { toast(`已创建 ${name}`); renderPage(); } else toast(r.error || '创建失败', 'error');
}
async function quickCreate(client) {
  const label = {Official:'官服',Bilibili:'B服',YoStarEN:'美服',YoStarJP:'日服',YoStarKR:'韩服',txwy:'繁中服'}[client]||client;
  const name = prompt(`输入 ${label} 账号名称:`);
  if (!name) return;
  const r = await apiPost('/account', {name,game_client:client,emu_instance_index:'',note:'',expire_date:''});
  if (r.ok) { toast(`已创建 ${name} (${label})`); document.querySelector('.dialog-overlay')?.remove(); renderPage(); }
  else toast(r.error || '创建失败','error');
}
async function _accountsToCsv() {
  let stageIds = _tableStages || [];
  if (!stageIds.length) {
    const sr = await apiGet('/stages');
    if (sr.ok && sr.stages) stageIds = sr.stages.map(s => s.id).filter(Boolean);
  }
  const fields = ['id','名称','游戏客户端','模拟器索引','切换标识','UID','备注','过期日','已暂停', ...stageIds];
  let csv = fields.join(',') + '\n';
  const accts = state.accounts;
  const engFields = ['id','name','game_client','emu_instance_index','account_switch','uid','note','expire_date','suspended'];
  for (const a of accts) {
    const row = engFields.map(f => {
      let v = a[f] !== undefined ? String(a[f]) : '';
      if (v.includes(',') || v.includes('"') || v.includes('\n')) v = '"' + v.replace(/"/g,'""') + '"';
      return v;
    });
    const acStages = a.stages || [];
    for (const s of stageIds) {
      row.push(acStages.includes(s) ? '1' : '0');
    }
    csv += row.join(',') + '\n';
  }
  return csv;
}
function openCsvEdit() {
  (async () => {
    const csv = await _accountsToCsv();
    const html = `<div class="dialog-overlay" onclick="event.target==this&&this.remove()">
    <div class="dialog" style="max-width:700px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
        <span style="font-size:14px;font-weight:bold;color:var(--text2)">📋 CSV 编辑账号</span>
        <span style="font-size:10px;color:var(--text3)">复制到 Excel 修改后粘贴回来，点保存</span>
      </div>
      <textarea id="csv-text" style="width:100%;height:320px;font-family:monospace;font-size:10px;padding:6px;background:var(--bg3);border:1px solid var(--border);color:var(--text);border-radius:var(--radius);white-space:pre;overflow:auto" readonly>${csv}</textarea>
      <div style="display:flex;gap:6px;margin-top:6px;justify-content:flex-end">
        <input type="file" id="csv-file-input" accept=".csv" style="display:none" onchange="csvFileSelected(event)">
        <button class="small" onclick="document.getElementById('csv-text').readOnly=false;document.getElementById('csv-text').focus()">编辑模式</button>
        <button class="small" onclick="document.getElementById('csv-file-input').click()">📂 从文件导入</button>
        <a href="/api/accounts/export" download="accounts.csv" style="text-decoration:none"><button class="small">⬇ 导出 CSV</button></a>
        <span style="flex:1"></span>
        <button class="small" onclick="this.closest('.dialog-overlay').remove()">取消</button>
        <button class="small" style="background:var(--accent);color:#fff" onclick="saveCsvEdit()">💾 保存</button>
      </div>
    </div>
  </div>`;
    document.body.insertAdjacentHTML('beforeend', html);
  })();
}
function csvFileSelected(event) {
  const file = event.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = function(e) {
    document.getElementById('csv-text').value = e.target.result;
    document.getElementById('csv-text').readOnly = false;
    toast('文件已加载，点击保存应用更改');
  };
  reader.readAsText(file, 'utf-8');
  event.target.value = '';
}
async function saveCsvEdit() {
  const csv = document.getElementById('csv-text').value;
  if (!csv.trim()) { toast('CSV 内容为空', 'error'); return; }
  const r = await apiPost('/accounts/csv_import', { csv });
  if (r.ok) {
    const parts = [];
    if (r.updated) parts.push(`更新 ${r.updated} 个`);
    if (r.created) parts.push(`新增 ${r.created} 个`);
    if (r.errors && r.errors.length) parts.push(`${r.errors.length} 个错误`);
    toast(parts.join('、') || '无变化');
    document.querySelector('.dialog-overlay')?.remove();
    renderPage();
  } else {
    toast(r.error || '导入失败', 'error');
  }
}
async function saveGeneral() {
  const theme = document.getElementById('sel-theme').value;
  setTheme(theme);
  const r = await apiPost('/config', {
    parallel_max: parseInt(document.getElementById('input-parallel').value) || 1,
    schedule_mode: document.getElementById('sel-mode').value,
    api_port: parseInt(document.getElementById('input-port').value) || 19999,
    appearance_mode: document.getElementById('sel-theme').value,
    deficit: parseInt(document.getElementById('input-deficit')?.value) || 0,
    stuck_timeout: parseInt(document.getElementById('input-stuck')?.value) || 10,
    daily_batch_time: document.getElementById('input-batch-time')?.value || '08:00',
    webhook_url: document.getElementById('input-webhook')?.value?.trim() || '',
    bind_address: document.getElementById('input-bind')?.value?.trim() || '127.0.0.1',
  });
  if (r.ok) toast('已保存'); else toast(r.error || '保存失败', 'error');
}
function onAIProviderChange() {
  const sel = document.getElementById('sel-ai-provider');
  const ep = document.getElementById('input-ai-endpoint');
  const model = document.getElementById('input-ai-model');
  const hints = { openai: ['https://api.openai.com/v1/chat/completions', 'gpt-4o-mini'], deepseek: ['https://api.deepseek.com/v1/chat/completions', 'deepseek-chat'], qwen: ['https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions', 'qwen-plus'], siliconflow: ['https://api.siliconflow.cn/v1/chat/completions', 'Qwen/Qwen2.5-7B-Instruct'] };
  const h = hints[sel.value];
  if (h && sel.value !== 'custom') { ep.placeholder = h[0]; model.placeholder = h[1]; if (!ep.value) ep.value = ''; if (!model.value) model.value = ''; }
}
async function saveAI() {
  const r = await apiPost('/config', {
    ai_provider: document.getElementById('sel-ai-provider')?.value || 'openai',
    ai_api_key: document.getElementById('input-ai-key')?.value?.trim() || '',
    ai_endpoint: document.getElementById('input-ai-endpoint')?.value?.trim() || '',
    ai_model: document.getElementById('input-ai-model')?.value?.trim() || '',
    ai_auto_analyze: document.getElementById('cb-ai-auto')?.checked || false,
  });
  if (r.ok) toast('AI 配置已保存'); else toast(r.error || '保存失败', 'error');
}
async function saveNotify() {
  const r = await apiPost('/config', {
    webhook_url: document.getElementById('input-webhook2')?.value?.trim() || '',
    tg_token: document.getElementById('input-tg-token')?.value?.trim() || '',
    tg_chat_id: document.getElementById('input-tg-chat')?.value?.trim() || '',
  });
  if (r.ok) toast('通知配置已保存'); else toast(r.error || '保存失败', 'error');
}
async function saveSmart() {
  const r = await apiPost('/settings/smart', {
    enabled: document.getElementById('cb-smart').checked,
    threshold: parseInt(document.getElementById('input-threshold').value) || 80,
    expiring_medicine: document.getElementById('cb-exp-med').checked,
    annihilation_enabled: document.getElementById('cb-anni').checked,
    recruit_enabled: document.getElementById('cb-recruit').checked,
    mall_enabled: document.getElementById('cb-mall').checked,
  });
  if (r.ok) toast('已保存'); else toast(r.error || '保存失败', 'error');
}
async function rebuildInstances() {
  const r = await apiPost('/instance/rebuild', {});
  if (r.ok) toast('重建完成'); else toast(r.error || '重建失败', 'error');
}
async function checkMaaUpdate() {
  const btn = document.getElementById('btn-maa-update');
  const result = document.getElementById('maa-update-result');
  if (btn) btn.disabled = true;
  if (result) result.textContent = '检查中...';
  try {
    const r = await apiPost('/maa/check_update');
    if (r.ok) {
      if (result) result.innerHTML = r.has_update
        ? `发现新版本 <b>${r.latest}</b> (当前 ${r.current}) <a href="#" onclick="downloadMaaUpdate();return false">立即更新</a>`
        : `已是最新版本 (${r.current})`;
    } else {
      if (result) result.innerHTML = `❌ ${r.error || '检查失败'} <a href="${r.manual_url || 'https://github.com/MaaAssistantArknights/MaaAssistantArknights/releases'}" target="_blank" style="color:var(--accent)">手动查看</a>`;
    }
  } catch(e) {
    if (result) result.textContent = '网络错误';
  }
  if (btn) btn.disabled = false;
}
async function downloadMaaUpdate() {
  const btn = document.getElementById('btn-maa-update');
  if (btn) btn.disabled = true;
  document.getElementById('maa-update-result').textContent = '开始下载更新...';
  const r = await apiPost('/maa/download_update');
  if (r.ok) {
    document.getElementById('maa-update-result').innerHTML = '✅ 更新已后台下载，完成后会自动重建实例';
    toast('更新下载中...');
  } else {
    document.getElementById('maa-update-result').textContent = '❌ ' + (r.error || '下载失败');
    toast(r.error || '下载失败', 'error');
  }
  if (btn) btn.disabled = false;
}
async function renderHealth(container) {
  container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text3)">正在检测...</div>';
  try {
    const r = await apiGet('/health');
    if (r.ok) {
      let html = '<div class="stat-grid">';
      r.checks?.forEach(c => {
        html += `<div class="stat-card">
          <div class="stat-value" style="color:${c.passed ? 'var(--accent)' : 'var(--danger)'}">${c.passed ? '✓' : '✕'}</div>
          <div class="stat-label">${c.name}</div>
          <div style="font-size:10px;color:var(--text3)">${c.message || ''}</div>
        </div>`;
      });
      html += '</div>';
      container.innerHTML = html;
    } else {
      container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text3)">检测不可用</div>';
    }
  } catch(e) {
    container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--danger)">检测失败</div>';
  }
}
async function exportConfig() {
  const r = await apiPost('/config/export');
  if (r.ok && r.data) {
    const blob = new Blob([JSON.stringify(r.data, null, 2)], {type: 'application/json'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'maorch_config_export.json';
    a.click();
    URL.revokeObjectURL(url);
    toast('配置已导出');
  } else toast('导出失败', 'error');
}
async function downloadLogs() {
  toast('正在打包日志...');
  window.open(API + '/export/logs', '_blank');
}
async function restartMAAOrch() {
  if (!await showConfirm('确认重启服务？页面将短暂断开连接。')) return;
  const r = await apiPost('/system/restart', {});
  if (r.ok) { toast('重启中...'); setTimeout(() => { window.location.reload(); }, 3000); }
  else toast(r.error || '重启失败', 'error');
}
function showImportConfig() {
  const html = `<div class="dialog-overlay" onclick="event.target==this&&this.remove()">
    <div class="dialog" style="max-width:400px">
      <div style="font-size:14px;font-weight:bold;margin-bottom:12px;color:var(--text2)">导入配置</div>
      <textarea id="import-json" rows="8" style="width:100%;background:var(--bg3);border:1px solid var(--border);color:var(--text);border-radius:var(--radius);padding:6px;font-size:10px;font-family:monospace" placeholder='粘贴 JSON 或选择文件...'></textarea>
      <input type="file" id="import-file" accept=".json" onchange="loadImportFile(this)" style="font-size:10px;margin-top:4px">
      <div class="btn-row" style="margin-top:8px">
        <button class="primary" onclick="submitImportConfig()">导入</button>
        <button onclick="this.closest('.dialog-overlay').remove()">取消</button>
      </div>
    </div>
  </div>`;
  document.body.insertAdjacentHTML('beforeend', html);
}
function loadImportFile(input) {
  const file = input.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = (e) => {
    const ta = document.getElementById('import-json');
    if (ta) ta.value = e.target.result;
  };
  reader.readAsText(file);
}
async function submitImportConfig() {
  const ta = document.getElementById('import-json');
  if (!ta || !ta.value.trim()) { toast('请输入或选择要导入的配置', 'error'); return; }
  try {
    const data = JSON.parse(ta.value);
    const r = await apiPost('/config/import', { data });
    document.querySelector('.dialog-overlay')?.remove();
    if (r.ok) toast(`已导入 ${r.imported||0} 个账号`); else toast(r.error || '导入失败', 'error');
  } catch(e) { toast('JSON 格式错误', 'error'); }
}

// ── Warehouse ──
async function renderWarehouse(container) {
  try {
    const r = await apiGet('/status');
    container.innerHTML = `<div style="margin-bottom:8px"><button class="primary small" onclick="showAddWarehouse()">＋ 添加程序</button></div>
    <div id="warehouse-list"></div>`;
    const wl = document.getElementById('warehouse-list');
    if (!r.warehouse || r.warehouse.length === 0) {
      wl.innerHTML = '<div style="color:var(--text3);padding:20px">仓库为空，点击上方按钮添加程序</div>';
      return;
    }
    wl.innerHTML = r.warehouse.map((w, i) => `
      <div class="card" style="margin-bottom:2px">
        <div class="info">
          <div class="name">${w.name || w.path?.split('\\').pop() || '?'}</div>
          <div class="meta">${w.path || ''} ${w.type ? '· ' + w.type : ''}</div>
        </div>
        <div class="card-actions">
          <button class="small" onclick="launchWarehouse(${i})">启动</button>
          <button class="small danger" onclick="deleteWarehouse(${i})">删除</button>
        </div>
      </div>
    `).join('');
  } catch(e) { showError(container); }
}

function showAddWarehouse() {
  const html = `<div class="dialog"><h3>添加程序</h3>
    <div class="form-row"><label>路径</label><input id="wh-path" placeholder="程序 exe 路径" style="flex:1"></div>
    <div class="form-row"><label>名称</label><input id="wh-name" placeholder="留空自动取文件名"></div>
    <div class="form-row"><label>类型</label><select id="wh-type">
      <option value="general">通用</option>
      <option value="maa">MAA</option>
      <option value="cli">CLI</option>
    </select></div>
    <div class="btn-row">
      <button onclick="saveWarehouse()">保存</button>
      <button class="danger" onclick="this.closest('.dialog-overlay').remove()">取消</button>
    </div>
  </div>`;
  const ov = document.createElement('div'); ov.className = 'dialog-overlay';
  ov.innerHTML = html; document.body.appendChild(ov);
}

async function saveWarehouse() {
  const path = document.getElementById('wh-path')?.value?.trim();
  if (!path) { toast('请输入路径', 'error'); return; }
  const name = document.getElementById('wh-name')?.value?.trim() || path.split('\\').pop().replace(/\.exe$/,'');
  const type = document.getElementById('wh-type')?.value || 'general';
  const r = await apiPost('/config/sync', { add_warehouse: { path, name, type } });
  if (r.ok) { toast('已添加'); document.querySelector('.dialog-overlay')?.remove(); renderPage(); }
  else toast(r.error || '添加失败', 'error');
}

async function launchWarehouse(idx) {
  const r = await apiPost('/pipeline/start', { warehouse_index: idx });
  if (r.ok) toast('已启动'); else toast(r.error || '启动失败', 'error');
}

async function deleteWarehouse(idx) {
  if (!await showConfirm('确认删除？')) return;
  const r = await apiPost('/config/sync', { remove_warehouse_index: idx });
  if (r.ok) { toast('已删除'); renderPage(); } else toast(r.error || '删除失败', 'error');
}

// ── Groups ──
async function renderGroups(container) {
  try {
    const r = await apiGet('/status');
    const groups = r.groups || [];
    container.innerHTML = `<div style="margin-bottom:8px">
      <button class="primary small" onclick="showAddGroup()">＋ 新建分组</button>
    </div>
    <div id="groups-list">${groups.length === 0 ? '<div style="color:var(--text3);padding:20px">暂无分组</div>' :
      groups.map((g, i) => `
        <div class="card" style="margin-bottom:4px">
          <div class="info">
            <div class="name">${g.name || '未命名'}</div>
            <div class="meta">模式: ${g.mode || 'parallel'} | 程序: ${(g.programs||[]).length} 个</div>
          </div>
          <div class="card-actions">
            <button class="small" onclick="launchGroup(${i})">▶ 启动</button>
            <button class="small danger" onclick="deleteGroup(${i})">删除</button>
          </div>
        </div>
      `).join('')
    }</div>`;
  } catch(e) { showError(container); }
}

function showAddGroup() {
  const html = `<div class="dialog"><h3>新建分组</h3>
    <div class="form-row"><label>名称</label><input id="grp-name" placeholder="分组名称"></div>
    <div class="form-row"><label>模式</label><select id="grp-mode">
      <option value="parallel">并行</option>
      <option value="sequential">串行</option>
    </select></div>
    <div class="btn-row">
      <button onclick="saveGroup()">保存</button>
      <button class="danger" onclick="this.closest('.dialog-overlay').remove()">取消</button>
    </div>
  </div>`;
  const ov = document.createElement('div'); ov.className = 'dialog-overlay';
  ov.innerHTML = html; document.body.appendChild(ov);
}

async function saveGroup() {
  const name = document.getElementById('grp-name')?.value?.trim();
  if (!name) { toast('请输入名称', 'error'); return; }
  const mode = document.getElementById('grp-mode')?.value || 'parallel';
  const r = await apiPost('/config/sync', { add_group: { name, mode, programs: [] } });
  if (r.ok) { toast('已创建'); document.querySelector('.dialog-overlay')?.remove(); renderPage(); }
  else toast(r.error || '创建失败', 'error');
}

async function launchGroup(idx) {
  const r = await apiPost('/pipeline/start', { group_index: idx });
  if (r.ok) toast('已启动'); else toast(r.error || '启动失败', 'error');
}

async function deleteGroup(idx) {
  if (!await showConfirm('确认删除？')) return;
  const r = await apiPost('/config/sync', { remove_group_index: idx });
  if (r.ok) { toast('已删除'); renderPage(); } else toast(r.error || '删除失败', 'error');
}

// ── Pipeline ──
async function renderPipeline(container) {
  try {
    const s = await apiGet('/status');
    const running = s.pipeline_running || false;
    container.innerHTML = `<div id="pipeline-page">
      <div style="margin-bottom:16px">
        <div style="font-size:18px;font-weight:bold;margin-bottom:4px">流水线</div>
        <div style="font-size:12px;color:var(--text2)">状态: <span style="color:${running ? 'var(--accent)' : 'var(--text3)'}">${running ? '▶ 运行中' : '⏹ 已停止'}</span></div>
      </div>
      <div style="display:flex;gap:8px;margin-bottom:16px">
        <button class="${running ? '' : 'primary'}" onclick="pipelineAction('start')" ${running ? 'disabled' : ''}>▶ 启动</button>
        <button class="${running ? 'danger' : ''}" onclick="pipelineAction('stop')" ${!running ? 'disabled' : ''}>⏹ 停止</button>
      </div>
      <div style="font-size:11px;color:var(--text3);line-height:1.6">
        流水线会按分组顺序依次执行各组内的程序。<br>
        启动前请确保已在「分组」页面配置好任务。
      </div>
    </div>`;
  } catch(e) { showError(container); }
}

async function pipelineAction(action) {
  const r = await apiPost('/pipeline/' + action, {});
  if (r.error) toast(r.error, 'error');
  else toast(action === 'start' ? '流水线已启动' : '流水线已停止');
  renderPage();
}

// ── Filter & Search ──
function setFilter(key) {
  filterKey = key;
  if (state.page === 'accounts') renderPage();
}
function searchAccounts(value) {
  if (state.page === 'accounts') renderPage();
}

// ── Queue Pause ──
async function toggleQueuePause() {
  const q = await apiGet('/queue');
  const isPaused = q.paused;
  const r = await apiPost(isPaused ? '/queue/resume' : '/queue/pause', {});
  if (r.ok) toast(isPaused ? '队列已恢复' : '队列已暂停');
  renderPage();
}

// ── Batch Operations ──
function toggleSelect(id) {
  if (selectedIds.has(id)) selectedIds.delete(id);
  else selectedIds.add(id);
  updateBatchBar();
}
function updateBatchBar() {
  const count = selectedIds.size;
  const bar = document.getElementById('batch-bar');
  if (!bar) return;
  const countEl = document.getElementById('batch-count');
  if (countEl) countEl.textContent = `已选 ${count}`;
  bar.querySelectorAll('button').forEach(btn => {
    btn.disabled = count === 0;
    btn.style.opacity = count === 0 ? '0.4' : '1';
  });
}
async function batchSmart() {
  if (selectedIds.size === 0) return;
  const ids = [...selectedIds];
  const r = await apiPost('/action/smart_selected', { account_ids: ids, include_anni: true, only_anni: false });
  if (r.ok) toast(`已调度 ${ids.length} 个账号`); else toast(r.error || '调度失败', 'error');
  selectedIds.clear(); renderPage();
}
async function batchEnqueue() {
  if (selectedIds.size === 0) return;
  navigate('batch');
}
async function batchStop() {
  for (const id of selectedIds) {
    const idx = state.accounts.findIndex(a => a.id === id);
    if (idx >= 0) await apiPost(`/account/${idx}/stop`, {});
  }
  selectedIds.clear(); renderPage();
}
async function batchDelete() {
  if (!await showConfirm(`确认删除 ${selectedIds.size} 个账号？`)) return;
  for (const id of selectedIds) {
    const idx = state.accounts.findIndex(a => a.id === id);
    if (idx >= 0) {
      await apiPost(`/account/${idx}/delete`, {});
    }
  }
  selectedIds.clear(); renderPage();
}
// ── Batch Assign Stage ──
async function batchAssignStage() {
  if (selectedIds.size === 0) return;
  const sr = await apiGet('/stages');
  if (!sr.ok || !sr.stages || !sr.stages.length) {
    toast('暂无可用关卡，请先去设置→关卡仓库添加', 'error');
    return;
  }
  const ids = [...selectedIds];
  const stages = sr.stages;
  let html = stages.map(s => `<label style="display:flex;align-items:center;gap:6px;padding:4px 0;cursor:pointer">
    <input type="checkbox" class="stage-assign" value="${s.id}"> <span style="font-size:12px">${s.name}</span>
    <span style="font-size:9px;color:var(--text3)">${s.count || 0}个</span>
  </label>`).join('');
  html = `<div class="dialog" style="max-width:360px">
    <div style="font-size:14px;font-weight:bold;margin-bottom:8px;color:var(--text2)">分配关卡 (${ids.length}个账号)</div>
    ${html || '<div style="color:var(--text3);padding:10px">暂无可用关卡</div>'}
    <div style="display:flex;gap:6px;margin-top:8px">
      <button class="small primary" onclick="submitAssignStage()">确定添加</button>
      <button class="small" onclick="submitRemoveStage()" style="color:var(--danger)">移除选中</button>
      <button class="small" onclick="this.closest('.dialog-overlay').remove()">取消</button>
    </div>
  </div>`;
  const ov = document.createElement('div'); ov.className = 'dialog-overlay';
  ov.innerHTML = html; document.body.appendChild(ov);
}
async function submitAssignStage() {
  const checked = document.querySelectorAll('.stage-assign:checked');
  if (!checked.length) { toast('请选择关卡', 'error'); return; }
  const ids = [...selectedIds];
  for (const cb of checked) {
    await apiPost('/stages/apply', { stage_id: cb.value, account_ids: ids, toggle: true });
  }
  document.querySelector('.dialog-overlay')?.remove();
  toast('已分配');
  _stageLibCache = null;
  renderPage();
}
async function submitRemoveStage() {
  const checked = document.querySelectorAll('.stage-assign:checked');
  if (!checked.length) { toast('请选择关卡', 'error'); return; }
  const ids = [...selectedIds];
  for (const cb of checked) {
    await apiPost('/stages/apply', { stage_id: cb.value, account_ids: ids, toggle: false });
  }
  document.querySelector('.dialog-overlay')?.remove();
  toast('已移除');
  renderPage();
}

// ── AI Insights ──
function renderAIInsights() {
  const area = document.getElementById('ai-insights-area');
  if (!area) return;
  const insights = state.aiInsights || [];
  if (!insights.length) { area.innerHTML = ''; return; }
  let html = '';
  for (const ins of insights) {
    const confColor = ins.confidence === 'high' ? 'var(--danger)' : ins.confidence === 'medium' ? 'var(--warn)' : 'var(--text3)';
    html += `<div class="card" style="padding:6px 10px;margin-bottom:4px;flex-direction:column;align-items:stretch;border-left:3px solid ${confColor}">`;
    html += `<div style="display:flex;align-items:center;gap:4px;font-size:10px;font-weight:bold;color:var(--text2);margin-bottom:2px">`;
    html += `<span>🤖 AI 分析</span>`;
    html += `<span style="font-size:8px;color:var(--text3)">${new Date(ins.ts * 1000).toLocaleTimeString()}</span>`;
    html += `<span style="flex:1"></span>`;
    html += `<span style="font-size:8px;padding:1px 4px;border-radius:2px;background:${confColor}20;color:${confColor}">${ins.confidence}</span>`;
    html += `</div>`;
    html += `<div style="font-size:11px;color:var(--text);margin-bottom:2px">❌ ${ins.reason}</div>`;
    if (ins.suggestion) {
      html += `<div style="font-size:10px;color:var(--accent)">💡 ${ins.suggestion}</div>`;
    }
    html += `</div>`;
  }
  area.innerHTML = html;
}

// ── SSE (Server-Sent Events) — replace polling ──
function startSSE() {
  const evtSource = new EventSource(API + '/sse');
  let _notifIds = new Set();
  evtSource.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      if (data.ok && data.accounts) {
        state.accounts = data.accounts;
        const running = data.accounts.filter(a => a.running).length;
        const queued = data.queue?.count || 0;
        document.getElementById('queue-summary').textContent =
          `运行: ${running} | 队列: ${queued}`;
        document.title = `MAAOrch [${running}/${queued}]`;
        // Show desktop notifications for new events
        if (data.notifications && data.notifications.length) {
          for (const n of data.notifications) {
            if (!_notifIds.has(n.id)) {
              _notifIds.add(n.id);
              showNotif(n.message, n.type);
              // Refresh emulator page if it mentions emu status
              if (n.message.includes('模拟器') && state.page === 'emus') {
                renderEmus(document.getElementById('content'));
              }
            }
          }
        }
        // Update AI insights on dashboard
        if (data.ai_insights && data.ai_insights.length) {
          state.aiInsights = data.ai_insights;
          if (state.page === 'dashboard') renderAIInsights();
        }
        // Don't re-render accounts page on SSE (causes flicker via innerHTML replace)
      }
    } catch(ex) { /* ignore parse errors */ }
  };
  evtSource.onerror = () => {
    // SSE will auto-reconnect
  };
}

async function checkOnboarding() {
  try {
    const r = await apiGet('/config');
    if (r.ok && !r.config?.onboarding_done) {
      // If accounts already exist, skip onboarding (old user)
      const accts = await apiGet('/accounts');
      if (accts.ok && accts.accounts && accts.accounts.length > 0) {
        await apiPost('/config', { onboarding_done: true });
        return;
      }
      if (!r.config?.maa_version && state.page === 'dashboard') {
        navigate('onboarding');
      }
    }
  } catch(e) {}
}

// ── Init ──
document.addEventListener('DOMContentLoaded', () => {
  // Navigation
  document.querySelectorAll('.nav-item').forEach(n => {
    n.addEventListener('click', () => {
      if (!n.dataset.page) return;
      document.getElementById('mobile-menu-overlay').style.display = 'none';
      navigate(n.dataset.page);
    });
  });
  // Mode switching
  document.querySelectorAll('.mode-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      document.querySelectorAll('.mode-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const mode = btn.dataset.mode;
      await apiPost('/config', { schedule_mode: mode });
    });
  });
  // Load saved mode
  (async () => {
    try {
      const cfg = await apiGet('/config');
      const mode = cfg.schedule_mode || 'daily';
      document.querySelectorAll('.mode-btn').forEach(b => {
        b.classList.toggle('active', b.dataset.mode === mode);
      });
    } catch(e) {}
  })();
  startSSE();
  checkOnboarding();
  try { if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission(); } catch(e) {}
  navigate('dashboard');
  // Keyboard shortcuts
  document.addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.key === 'Enter') { smartAll(); e.preventDefault(); }
    if (e.key === 'Escape') { stopAll(); }
    if (e.altKey && e.key === 's') { navigate('settings'); e.preventDefault(); }
    if (e.altKey && e.key === 'd') { navigate('dashboard'); e.preventDefault(); }
    if (e.altKey && e.key === 'q') { navigate('queue'); e.preventDefault(); }
    if (e.altKey && e.key === 'a') { navigate('accounts'); e.preventDefault(); }
  });
});
// ── Mobile Menu ──
function toggleMobileMenu() {
  var el = document.getElementById('mobile-menu-overlay');
  if (el) el.style.display = el.style.display === 'block' ? 'none' : 'block';
}
function closeMobileMenu(e) {
  if (e && e.target !== e.currentTarget) return;
  var el = document.getElementById('mobile-menu-overlay');
  if (el) el.style.display = 'none';
}
