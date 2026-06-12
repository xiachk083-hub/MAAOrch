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
    const q = await apiGet('/queue');
    document.getElementById('queue-summary').textContent = `运行: ${s.running || 0} | 队列: ${q.pending_count || 0}`;
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
  const fns = { accounts: renderAccounts, queue: renderQueue, stats: renderStats, settings: renderSettings, about: renderAbout };
  if (fns[state.page]) fns[state.page](c);
}
async function renderAccounts(container) {
  try {
    const r = await apiGet('/accounts');
    if (!r.ok) { container.innerHTML = `<div class="error">加载失败: ${r.error}</div>`; return; }
    state.accounts = r.accounts;
    const groups = {};
    r.accounts.forEach(a => {
      const vm = a.emu_instance_index || 'unbound';
      if (!groups[vm]) groups[vm] = [];
      groups[vm].push(a);
    });
    const vmKeys = Object.keys(groups).sort((a,b) => a === 'unbound' ? 1 : b === 'unbound' ? -1 : parseInt(a) - parseInt(b));
    let html = `<div class="top-actions" style="margin-bottom:8px">
      <button class="primary" onclick="smartAll(true)">▶ 含剿灭</button>
      <button onclick="smartAll(false)">▶ 不含剿灭</button>
      <button onclick="smartAll(false,true)">▶ 只剿灭</button>
      <button onclick="document.getElementById('file-input').click()" style="margin-left:8px">＋ 创建账号</button>
      <input type="file" id="file-input" style="display:none" accept=".exe" onchange="createAccount(this)">
      <button class="danger" onclick="stopAll()" style="margin-left:8px">⏹ 全部停止</button>
    </div>
    <div class="card-list">`;
    vmKeys.forEach(vm => {
      html += `<div style="color:var(--text3);font-size:10px;padding:4px 0;margin-top:4px">📱 模拟器 VM ${vm === 'unbound' ? '未绑定' : vm}</div>`;
      groups[vm].forEach(a => {
        const statusClass = a.running ? 'status-running' : a.queued ? 'status-queued' : a.failures >= 6 ? 'status-paused' : a.failures > 0 ? 'status-error' : '';
        const statusText = a.running ? '▶ 运行' : a.queued ? '⏳ 排队' : a.failures >= 6 ? '⏸ 暂停' : a.failures > 0 ? `✕ 错误x${a.failures}` : '';
        html += `<div class="card" onclick="showAccountDetail('${a.id}')">
          <div class="info">
            <div class="name">${a.name}</div>
            <div class="meta">VM ${a.emu_instance_index||'?'} · ${a.game_client||'?'}${a.adb_address ? ' · ' + a.adb_address : ''}</div>
          </div>
          <div class="status ${statusClass}">${statusText}</div>
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
      <button onclick="clearQueue()" style="margin-left:16px" class="danger small">清空队列</button>
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
    container.innerHTML = `<div class="stat-grid">
      <div class="stat-card"><div class="stat-value">${total}</div><div class="stat-label">总账号</div></div>
      <div class="stat-card"><div class="stat-value">${running}</div><div class="stat-label">运行中</div></div>
      <div class="stat-card"><div class="stat-value">${(r.stats||{}).total_runs || 0}</div><div class="stat-label">今日运行</div></div>
      <div class="stat-card"><div class="stat-value">${(r.stats||{}).total_drops || 0}</div><div class="stat-label">今日掉落</div></div>
    </div>
    <div style="margin-top:12px">${r.detail ? r.detail.map(d => `<div style="font-size:11px;color:var(--text2);padding:2px 0">${d.name}: ${d.runs || 0} 次, 掉落 ${d.drops || 0}</div>`).join('') : ''}</div>`;
  } catch(e) { container.innerHTML = `<div class="error">加载失败</div>`; }
}

async function renderSettings(container) {
  try {
    const cfg = await apiGet('/config');
    const smart = await apiGet('/settings/smart');
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
      <div class="form-row"><label>智能调度</label><label style="color:var(--text2);font-size:12px"><input type="checkbox" id="cb-smart" ${smart.enabled?'checked':''}> 启用</label></div>
      <div class="form-row"><label>体力阈值</label><input type="number" id="input-threshold" value="${smart.threshold||80}" min="0" max="200"> %</div>
      <div class="form-row"><label>过期药用</label><label style="color:var(--text2);font-size:12px"><input type="checkbox" id="cb-exp-med" ${smart.expiring_medicine?'checked':''}> 优先吃快过期药</label></div>
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
  const html = `<h3>${a.name}</h3>
    <div class="form-row"><label>名称</label><input id="ed-name" value="${a.name}"></div>
    <div class="form-row"><label>客户端</label><select id="ed-client"><option value="Official" ${a.game_client==='Official'?'selected':''}>官服</option><option value="Bilibili" ${a.game_client==='Bilibili'?'selected':''}>B服</option></select></div>
    <div class="form-row"><label>模拟器 VM</label><input id="ed-vm" value="${a.emu_instance_index||''}" placeholder="VM 索引"></div>
    <div class="form-row"><label>ADB 地址</label><input id="ed-adb" value="${a.adb_address||''}" placeholder="127.0.0.1:16384"></div>
    <div class="btn-row">
      <button onclick="saveAccount('${a.id}',${idx})">保存</button>
      <button class="danger" onclick="closeDialog(this.closest('.dialog-overlay'))">取消</button>
    </div>`;
  showDialog(html);
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
    expiring_medicine: document.getElementById('cb-exp-med').checked
  });
  if (r.ok) toast('已保存'); else toast(r.error || '保存失败', 'error');
}
async function rebuildInstances() {
  const r = await apiPost('/instance/rebuild', {});
  if (r.ok) toast('重建完成'); else toast(r.error || '重建失败', 'error');
}

// ── Polling ──
function startPolling() {
  if (state.polling) return;
  state.polling = true;
  async function poll() {
    if (!state.polling) return;
    await refreshSidebar();
    if (document.visibilityState === 'visible' && state.page === 'accounts') {
      try {
        const r = await apiGet('/accounts');
        if (r.ok) state.accounts = r.accounts;
      } catch(e) {}
    }
    setTimeout(poll, 3000);
  }
  setTimeout(poll, 1000);
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
  navigate('accounts');
  startPolling();
});
