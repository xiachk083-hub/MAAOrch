// ── API ──
const API = '/api';
async function api(path, opts = {}) {
  const res = await fetch(API + path, {
    headers: { 'Content-Type': 'application/json', ...opts.headers },
    ...opts
  });
  return res.json();
}
async function apiGet(path) { return api(path); }
async function apiPost(path, body) { return api(path, { method: 'POST', body: JSON.stringify(body) }); }

// ── State ──
let state = { accounts: [], queue: [], config: {}, stats: {}, page: 'accounts', polling: false };
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
  const fns = { accounts: renderAccounts, queue: renderQueue, stats: renderStats, settings: renderSettings, about: renderAbout, logs: renderLogs, account: renderAccount, taskcfg: renderTaskConfig, batch: renderBatchEdit };
  if (fns[state.page]) fns[state.page](c);
}
async function renderAccounts(container) {
  try {
    const r = await apiGet('/accounts');
    if (!r.ok) { container.innerHTML = `<div class="error">加载失败: ${r.error}</div>`; return; }
    state.accounts = r.accounts;

    let searchText = (document.getElementById('search-input')?.value || '').toLowerCase();
    let filtered = r.accounts.filter(a => {
      const nameMatch = !searchText || a.name.toLowerCase().includes(searchText);
      return nameMatch;
    });
    if (filterKey === 'running') filtered = filtered.filter(a => a.running);
    else if (filterKey === 'waiting') filtered = filtered.filter(a => a.queued);
    else if (filterKey === 'error') filtered = filtered.filter(a => !a.running && !a.queued && a.failures > 0 && a.failures < 6);
    else if (filterKey === 'paused') filtered = filtered.filter(a => a.failures >= 6);

    const groups = {};
    filtered.forEach(a => {
      const vm = a.emu_instance_index || 'unbound';
      if (!groups[vm]) groups[vm] = [];
      groups[vm].push(a);
    });
    const vmKeys = Object.keys(groups).sort((a,b) => a === 'unbound' ? 1 : b === 'unbound' ? -1 : parseInt(a) - parseInt(b));
    const batchCount = selectedIds.size;
    let html = `<input type="text" id="search-input" placeholder="搜索账号..." 
  oninput="searchAccounts(this.value)" 
  style="width:100%;padding:6px 8px;background:var(--bg3);border:1px solid var(--border);color:var(--text);border-radius:var(--radius);font-size:12px;margin-bottom:6px">
<div style="display:flex;gap:4px;margin-bottom:8px;flex-wrap:wrap">
  <button class="${!filterKey ? 'primary' : ''}" onclick="setFilter('')">全部</button>
  <button class="${filterKey==='running'?'primary':''}" onclick="setFilter('running')">▶ 运行中</button>
  <button class="${filterKey==='waiting'?'primary':''}" onclick="setFilter('waiting')">⏳ 排队中</button>
  <button class="${filterKey==='error'?'primary':''}" onclick="setFilter('error')">✕ 错误</button>
  <button class="${filterKey==='paused'?'primary':''}" onclick="setFilter('paused')">⏸ 暂停</button>
</div>
<div class="top-actions" style="margin-bottom:8px">
  <button class="primary" onclick="smartAll(true)">▶ 含剿灭</button>
  <button onclick="smartAll(false)">▶ 不含剿灭</button>
  <button onclick="smartAll(false,true)">▶ 只剿灭</button>
  <button onclick="document.getElementById('file-input').click()" style="margin-left:8px">＋ 创建账号</button>
  <input type="file" id="file-input" style="display:none" accept=".exe" onchange="createAccount(this)">
  <button class="danger" onclick="stopAll()" style="margin-left:8px">⏹ 全部停止</button>
</div>
<div id="batch-bar" style="display:${batchCount?'flex':'none'};align-items:center;gap:8px;padding:4px 0;margin-bottom:4px">
  <span class="count" style="color:var(--accent);font-size:12px">已选 ${batchCount}</span>
  <button class="small" onclick="batchEnqueue()">批量入队</button>
  <button class="small danger" onclick="batchStop()">批量停止</button>
  <button class="small danger" onclick="batchDelete()">批量删除</button>
  <button class="small" onclick="selectedIds.clear();renderPage();">取消选择</button>
</div>
<div class="card-list">`;
    vmKeys.forEach(vm => {
      html += `<div style="color:var(--text3);font-size:10px;padding:4px 0;margin-top:4px">📱 模拟器 VM ${vm === 'unbound' ? '未绑定' : vm}</div>`;
      groups[vm].forEach(a => {
        const statusClass = a.running ? 'status-running' : a.queued ? 'status-queued' : a.failures >= 6 ? 'status-paused' : a.failures > 0 ? 'status-error' : '';
        const statusText = a.running ? '▶ 运行' : a.queued ? '⏳ 排队' : a.failures >= 6 ? '⏸ 暂停' : a.failures > 0 ? `✕ 错误x${a.failures}` : '';
        const checked = selectedIds.has(a.id) ? 'checked' : '';
        html += `<div class="card">
  <input type="checkbox" class="cb" ${checked} onchange="event.stopPropagation();toggleSelect('${a.id}')">
  <div style="flex:1" onclick="showAccountDetail('${a.id}')">
    <div class="info">
      <div class="name">${a.name}</div>
      <div class="meta">VM ${a.emu_instance_index||'?'} · ${a.game_client||'?'}${a.adb_address ? ' · ' + a.adb_address : ''}</div>
    </div>
    <div class="status ${statusClass}">${statusText}</div>
  </div>
  <div class="card-actions">
    <button class="small" onclick="event.stopPropagation();launchAccount('${a.id}')">启动</button>
    <button class="small danger" onclick="event.stopPropagation();deleteAccount('${a.id}')">删除</button>
  </div>
</div>`;
      });
    });
    html += '</div>';
    container.innerHTML = html;
  } catch(e) { container.innerHTML = `<div class="error">加载失败: ${e.message}</div>`; }
}

async function renderQueue(container) {
  try {
    const q = await apiGet('/queue');
    const s = await apiGet('/status');
    container.innerHTML = `<div style="margin-bottom:8px">
      <span>运行中: <strong>${s.running || 0}</strong></span>
      <span style="margin-left:16px">排队: <strong>${q.pending_count || 0}</strong></span>
      <button onclick="toggleQueuePause()" style="margin-left:16px">${q.paused ? '▶ 恢复队列' : '⏸ 暂停队列'}</button>
      <button onclick="clearQueue()" style="margin-left:8px" class="danger small">清空队列</button>
    </div>
    <div id="queue-list"></div>`;
    const ql = document.getElementById('queue-list');
    if (q.pending && q.pending.length) {
      ql.innerHTML = q.pending.map(e => {
        const srcMap = {manual:'手动', schedule:'定时', sanity:'理智', force:'强制', retry:'重试'};
        return `<div class="queue-item">
          <span class="name">${e.account_name || e.account_id?.slice(0,8) || '?'}</span>
          <span class="source">${srcMap[e.source]||e.source}</span>
          <span class="eta">${e.not_before ? new Date(e.not_before).toLocaleTimeString() : '立即'}</span>
        </div>`;
      }).join('');
    } else {
      ql.innerHTML = '<div style="color:var(--text3);padding:20px;text-align:center">队列为空</div>';
    }
  } catch(e) { container.innerHTML = `<div class="error">加载失败: ${e.message}</div>`; }
}

async function renderStats(container) {
  try {
    const r = await apiGet('/stats');
    const a = await apiGet('/accounts');
    const total = a.ok ? a.accounts.length : 0;
    const running = a.ok ? a.accounts.filter(x => x.running).length : 0;
    const todayRuns = r.accounts ? r.accounts.reduce((s,ac) => s + (ac.total_runs||0), 0) : 0;
    container.innerHTML = `<div class="stat-grid">
      <div class="stat-card"><div class="stat-value">${total}</div><div class="stat-label">总账号</div></div>
      <div class="stat-card"><div class="stat-value">${running}</div><div class="stat-label">运行中</div></div>
      <div class="stat-card"><div class="stat-value">${todayRuns}</div><div class="stat-label">运行次数</div></div>
    </div>
    <div style="margin-top:12px">${r.accounts ? r.accounts.filter(ac => ac.total_runs > 0).map(ac =>
      `<div style="font-size:11px;color:var(--text2);padding:2px 0">${ac.account_name}: ${ac.total_runs} 次</div>`
    ).join('') : '<div style="color:var(--text3)">暂无运行记录</div>'}</div>`;
  } catch(e) { container.innerHTML = `<div class="error">加载失败</div>`; }
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
      <div class="tab" data-tab="maa">MAA 实例</div>
    </div>
    <div class="tab-content active" id="tab-general">
      <div class="form-row"><label>主题</label><select id="sel-theme" onchange="setTheme(this.value)">
        <option value="Dark">暗色</option><option value="Light">亮色</option><option value="Notepaper">Notepaper</option>
      </select></div>
      <div class="form-row"><label>并行上限</label><input type="number" id="input-parallel" value="${cfg.parallel_max||1}" min="1" max="10"></div>
      <div class="form-row"><label>调度模式</label><select id="sel-mode">
        <option value="daily" ${cfg.schedule_mode==='daily'?'selected':''}>日常</option>
        <option value="roguelike" ${cfg.schedule_mode==='roguelike'?'selected':''}>肉鸽</option>
        <option value="reclamation" ${cfg.schedule_mode==='reclamation'?'selected':''}>生息</option>
      </select></div>
      <div class="form-row"><label>API 端口</label><input type="number" id="input-port" value="${cfg.api_port||19999}"></div>
      <div class="btn-row"><button class="primary" onclick="saveGeneral()">保存</button></div>
    </div>
    <div class="tab-content" id="tab-smart">
      <div class="form-row"><label>智能调度</label>
        <label style="color:var(--text2);font-size:12px"><input type="checkbox" id="cb-smart" ${smart.enabled?'checked':''}> 启用</label></div>
      <div class="form-row"><label>体力阈值</label><input type="number" id="input-threshold" value="${smart.threshold||80}" min="0" max="200"> %</div>
      <div class="form-row"><label>过期药</label><label><input type="checkbox" id="cb-exp-med" ${smart.expiring_medicine?'checked':''}> 优先吃快过期药</label></div>
      <div class="form-row"><label>剿灭</label><label><input type="checkbox" id="cb-anni" ${smart.annihilation_enabled!==false?'checked':''}> 启用自动剿灭</label></div>
      <div class="btn-row"><button class="primary" onclick="saveSmart()">保存</button></div>
    </div>
    <div class="tab-content" id="tab-maa">
      <div class="form-row"><label>MAA 版本</label><span style="color:var(--text2);font-size:12px">${cfg.maa_version||'未安装'}</span></div>
      <div class="form-row"><label>实例数</label><span style="color:var(--text2);font-size:12px">${cfg.maa_instances||0}</span></div>
      <div class="btn-row"><button onclick="rebuildInstances()">🔄 重建实例</button></div>
    </div>`;
    // Tab switching
    container.querySelectorAll('.tab').forEach(tab => {
      tab.addEventListener('click', () => {
        container.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        container.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        const tc = document.getElementById('tab-' + tab.dataset.tab);
        if (tc) tc.classList.add('active');
      });
    });
  } catch(e) { container.innerHTML = `<div class="error">加载失败</div>`; }
}

function renderAbout(container) {
  container.innerHTML = `<div class="about-version">MAAOrch</div>
  <div class="about-info">多账号 MAA 编排调度器<br><br>
  Python + PySide6 + Web UI<br>
  <a href="https://github.com/xiachk083-hub/MAAOrch" target="_blank" style="color:var(--accent)">GitHub</a><br><br>
  MAA v6 兼容 | 开源软件 (MIT)</div>`;
}

let logAutoRef = true;
let logTimer = null;

async function renderLogs(container) {
  const resp = await fetch(API + '/logs?lines=200');
  const html = await resp.text();
  // Wrap in page layout
  container.innerHTML = `<div class="log-page">
    <div style="margin-bottom:8px;display:flex;align-items:center;gap:8px">
      <span style="color:var(--text2);font-size:12px">日志</span>
      <button class="small primary" onclick="toggleLogAuto()" id="log-auto-btn">${logAutoRef ? '⏸ 暂停' : '▶ 自动'}</button>
      <button class="small" onclick="clearLogView()">🗑 清空</button>
    </div>
    <pre id="log-content" style="background:var(--bg3);border:1px solid var(--border);border-radius:var(--radius);padding:8px;font-size:11px;line-height:1.4;height:calc(100vh - 120px);overflow-y:auto;color:var(--text2);white-space:pre-wrap;word-break:break-all"></pre>
  </div>`;
  document.getElementById('log-content').textContent = html;
  startLogAuto();
}

function startLogAuto() {
  if (logTimer) clearInterval(logTimer);
  if (!logAutoRef) return;
  logTimer = setInterval(async () => {
    if (state.page !== 'logs') { clearInterval(logTimer); logTimer = null; return; }
    try {
      const resp = await fetch(API + '/logs?lines=200');
      const html = await resp.text();
      const el = document.getElementById('log-content');
      if (el) el.textContent = html;
    } catch(e) {}
  }, 2000);
}

function toggleLogAuto() {
  logAutoRef = !logAutoRef;
  const btn = document.getElementById('log-auto-btn');
  if (btn) btn.textContent = logAutoRef ? '⏸ 暂停' : '▶ 自动';
  if (logAutoRef) startLogAuto();
  else if (logTimer) { clearInterval(logTimer); logTimer = null; }
}

function clearLogView() {
  const el = document.getElementById('log-content');
  if (el) el.textContent = '';
}

// ── Actions ──
async function smartAll(includeAnni, onlyAnni) {
  const r = await apiPost('/action/smart_all', { include_anni: includeAnni ?? true, only_anni: onlyAnni ?? false });
  if (r.ok) { toast(`已调度 ${r.count || 0} 个账号`); renderPage(); } else toast(r.error || '调度失败', 'error');
}
async function stopAll() {
  const r = await apiPost('/action/stop_all', {});
  if (r.ok) { toast(`已停止 ${r.count || 0} 个账号`); renderPage(); } else toast(r.error || '停止失败', 'error');
}
async function clearQueue() {
  const r = await apiPost('/queue/clear', {});
  if (r.ok) { toast('队列已清空'); renderPage(); } else toast(r.error, 'error');
}
async function launchAccount(id) {
  const r = await apiPost(`/account/${state.accounts.findIndex(a => a.id === id)}/launch`, {});
  if (r.ok) toast('已启动'); else toast(r.error || '启动失败', 'error');
}
async function deleteAccount(id) {
  if (!confirm('确认删除此账号？')) return;
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
  if (!r.ok) { container.innerHTML = '<div>加载失败</div>'; return; }
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
  if (!r.ok) { container.innerHTML = '<div>加载失败</div>'; return; }
  const a = r.accounts.find(x => x.id === id);
  if (!a) { container.innerHTML = '<div>账号不存在</div>'; return; }
  const idx = r.accounts.indexOf(a);
  
  container.innerHTML = `<div>
    <button onclick="navigate('accounts')" style="margin-bottom:8px">← 返回</button>
    <div class="form-row"><label>名称</label><input id="ed-name" value="${a.name}"></div>
    <div class="form-row"><label>客户端</label><select id="ed-client">
      <option value="Official" ${a.game_client==='Official'?'selected':''}>官服</option>
      <option value="Bilibili" ${a.game_client==='Bilibili'?'selected':''}>B服</option>
    </select></div>
    <div class="form-row"><label>模拟器 VM</label>
      <select id="ed-vm" style="flex:0.3">
        <option value="">未绑定</option>
        ${Array.from({length:20}, (_,i) => `<option value="${i}" ${a.emu_instance_index==String(i)?'selected':''}>VM ${i}</option>`).join('')}
      </select>
      <span style="color:var(--text3);font-size:10px;margin-left:4px">${a.game_client||''}</span>
    </div>
    <div class="form-row"><label>ADB 地址</label><input id="ed-adb" value="${a.adb_address||''}"></div>
    <div class="form-row"><label>ADB 路径</label><input id="ed-adb-path" value="${a.adb_path||''}" placeholder="自动检测"></div>
    <div class="form-row"><label>关卡</label><input id="ed-stage" value="${a.smart_stage||''}" placeholder="例如 1-7"></div>
    <div class="form-row"><label>剿灭</label>
      <select id="ed-anni"><option value="">自动选择</option><option value="Annihilation">当期剿灭</option></select>
    </div>
    <div style="border-top:1px solid var(--border);margin:8px 0;padding-top:8px">
      <div style="font-size:12px;color:var(--text2);margin-bottom:4px">操作</div>
      <button class="primary" onclick="navigate('taskcfg')" style="margin-right:4px">📋 任务配置</button>
      <button onclick="launchAccount('${a.id}')" style="margin-right:4px">▶ 启动</button>
      <button class="danger" onclick="deleteAccount('${a.id}')">🗑 删除</button>
    </div>
    <div class="btn-row">
      <button class="primary" onclick="saveAccountDetail('${a.id}',${idx})">保存</button>
    </div>
  </div>`;
}

async function saveAccountDetail(id, idx) {
  const name = document.getElementById('ed-name')?.value?.trim();
  const client = document.getElementById('ed-client')?.value;
  const vm = document.getElementById('ed-vm')?.value;
  const adb = document.getElementById('ed-adb')?.value?.trim();
  const adbPath = document.getElementById('ed-adb-path')?.value?.trim();
  const stage = document.getElementById('ed-stage')?.value?.trim();
  const anni = document.getElementById('ed-anni')?.value;
  const body = {};
  if (name) body.name = name;
  if (client) body.game_client = client;
  body.emu_instance_index = vm || '';
  if (adb) body.adb_address = adb;
  if (adbPath) body.adb_path = adbPath;
  if (stage) { body.smart_stage = stage; body.fight_stage = stage; }
  if (anni) body.smart_annihilation = anni;
  const r = await apiPost(`/account/${idx}/edit`, body);
  if (r.ok) toast('已保存');
  else toast(r.error || '保存失败', 'error');
}

async function renderTaskConfig(container) {
  const id = state._detailId;
  if (!id) { container.innerHTML = '<div>未选择账号</div>'; return; }
  const r = await apiGet('/accounts');
  if (!r.ok) { container.innerHTML = '<div>加载失败</div>'; return; }
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
async function saveGeneral() {
  const r = await apiPost('/config', {
    parallel_max: parseInt(document.getElementById('input-parallel').value) || 1,
    schedule_mode: document.getElementById('sel-mode').value,
    api_port: parseInt(document.getElementById('input-port').value) || 19999,
    appearance_mode: document.getElementById('sel-theme').value
  });
  if (r.ok) toast('已保存'); else toast(r.error || '保存失败', 'error');
}
async function saveSmart() {
  const r = await apiPost('/settings/smart', {
    enabled: document.getElementById('cb-smart').checked,
    threshold: parseInt(document.getElementById('input-threshold').value) || 80,
    expiring_medicine: document.getElementById('cb-exp-med').checked,
    annihilation_enabled: document.getElementById('cb-anni').checked,
  });
  if (r.ok) toast('已保存'); else toast(r.error || '保存失败', 'error');
}
async function rebuildInstances() {
  const r = await apiPost('/instance/rebuild', {});
  if (r.ok) toast('重建完成'); else toast(r.error || '重建失败', 'error');
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
  const r = await apiPost('/pipeline/pause', {});
  if (!r.error) toast('队列已' + (r.paused ? '暂停' : '恢复'));
  renderPage();
}

// ── Batch Operations ──
function toggleSelect(id) {
  if (selectedIds.has(id)) selectedIds.delete(id);
  else selectedIds.add(id);
  updateBatchBar();
}
function updateBatchBar() {
  const bar = document.getElementById('batch-bar');
  if (!bar) return;
  const count = selectedIds.size;
  bar.style.display = count ? 'flex' : 'none';
  const countEl = bar.querySelector('.count');
  if (countEl) countEl.textContent = `已选 ${count}`;
}
async function batchEnqueue() {
  if (selectedIds.size === 0) return;
  navigate('batch');
}
async function batchStop() {
  for (const id of selectedIds) {
    const idx = state.accounts.findIndex(a => a.id === id);
    if (idx >= 0) await apiPost(`/account/${idx}/launch`, { action: 'stop' });
  }
  selectedIds.clear(); renderPage();
}
async function batchDelete() {
  if (!confirm(`确认删除 ${selectedIds.size} 个账号？`)) return;
  for (const id of selectedIds) {
    const idx = state.accounts.findIndex(a => a.id === id);
    if (idx >= 0) {
      await apiPost(`/account/${idx}/delete`, {});
    }
  }
  selectedIds.clear(); renderPage();
}

// ── SSE (Server-Sent Events) — replace polling ──
function startSSE() {
  const evtSource = new EventSource(API + '/sse');
  evtSource.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      if (data.ok && data.accounts) {
        state.accounts = data.accounts;
        const running = data.accounts.filter(a => a.running).length;
        document.getElementById('queue-summary').textContent =
          `运行: ${running} | 队列: ${data.queue?.count || 0}`;
        // Only re-render if current page is accounts
        if (state.page === 'accounts' && document.getElementById('content')) {
          renderPage();
        }
      }
    } catch(ex) { /* ignore parse errors */ }
  };
  evtSource.onerror = () => {
    // SSE will auto-reconnect
  };
}

// ── Init ──
document.addEventListener('DOMContentLoaded', () => {
  // Navigation
  document.querySelectorAll('.nav-item').forEach(n => {
    n.addEventListener('click', () => navigate(n.dataset.page));
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
  setInterval(() => { if (state.page !== 'accounts') renderPage(); }, 5000);
  navigate('accounts');
});
