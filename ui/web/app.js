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
                gallery: renderGallery };
  if (fns[state.page]) fns[state.page](c);
}
async function renderAccounts(container) {
  try {
    const r = await apiGet('/accounts');
    if (!r.ok) { showError(container, r.error); return; }
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
  <button onclick="showCreateAccountForm()">＋ 创建账号</button>
</div>
<div id="batch-bar" style="display:${batchCount?'flex':'none'};align-items:center;gap:8px;padding:4px 0;margin-bottom:4px">
  <span class="count" style="color:var(--accent);font-size:12px">已选 ${batchCount}</span>
  <button class="small primary" onclick="batchSmart()">▶ 调度选中</button>
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
  } catch(e) { showError(container, e.message); }
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
  } catch(e) { showError(container); }
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
    <div class="tab-content" id="tab-maa">
      <div class="form-row"><label>MAA 版本</label><span style="color:var(--text2);font-size:12px">${cfg.maa_version||'未安装'}</span></div>
      <div class="form-row"><label>实例数</label><span style="color:var(--text2);font-size:12px">${cfg.maa_instances||0}</span></div>
      <div class="btn-row"><button onclick="rebuildInstances()">🔄 重建实例</button><button onclick="checkMaaUpdate()" id="btn-maa-update" style="margin-left:8px">📥 检查更新</button><button onclick="downloadLogs()" style="margin-left:8px">📦 导出日志</button><button onclick="exportConfig()" style="margin-left:8px">📤 导出配置</button><button onclick="showImportConfig()" style="margin-left:8px">📥 导入配置</button></div>
      <div id="maa-update-result" style="font-size:10px;color:var(--text3);margin-top:4px"></div>
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
  } catch(e) { showError(container); }
}

function renderOnboarding(container) {
  fetch('pages/onboarding.html').then(r => r.text()).then(html => {
    container.innerHTML = html;
  });
}

function openMaaFolder() {
  toast('请将 MAA 文件夹放到 services/maa/source/ 目录');
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
      html += `<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px">`;
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

    // ── 操作 + 调度配置 ──
    html += `<div class="card" style="padding:6px 8px;margin-bottom:6px;flex-direction:column;align-items:stretch">`;
    // Row 1: action buttons
    html += `<div style="display:flex;gap:4px;flex-wrap:wrap;align-items:center;margin-bottom:4px">`;
    html += `<button class="small primary" onclick="smartAll()">▶ 一键调度</button>`;
    html += `<button class="small" onclick="showNewDispatch()" style="color:var(--accent)">＋ 新调度</button>`;
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
    html += `<span>并行</span><input type="range" min="1" max="10" value="${cap.parallel_max||3}" id="dash-slider" oninput="dashSliderChange(this.value)" onchange="dashSaveSlider()" style="width:60px;height:4px"><span style="min-width:16px">${cap.parallel_max||3}</span>`;
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
    html += `<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:8px">`;
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

    // ── 图表区 ──
    const samples = d.samples || [];
    const gantt = d.gantt || [];
    window._lastGanttData = gantt;
    if (samples.length > 5 || gantt.length >= 1) {
      html += `<div style="display:grid;grid-template-columns:${samples.length>5 && gantt.length>=1 ? '1fr 1fr' : '1fr'};gap:8px;margin-bottom:8px">`;
      if (samples.length > 5) {
        html += `<div class="card" style="padding:8px;flex-direction:column;align-items:stretch">`;
        html += `<div style="font-size:10px;font-weight:bold;color:var(--text2);margin-bottom:4px">资源趋势</div>`;
        html += _trendChart(samples);
        html += `<div style="font-size:8px;color:var(--text3);margin-top:2px"><span style="color:#e74c3c">─ CPU</span> <span style="color:#3498db;margin-left:6px">─ 内存</span> <span style="color:#2ecc71;margin-left:6px">─ GPU</span></div>`;
        html += `</div>`;
      }
      const startCount = gantt.filter(e=>e.event==='start').length;
      html += `<div class="card" style="padding:8px;flex-direction:column;align-items:stretch">`;
      html += `<div style="font-size:10px;font-weight:bold;color:var(--text2);margin-bottom:4px">调度时间线${startCount ? ' ('+startCount+'次)' : ''}</div>`;
      html += `<div id="gantt-toggle" style="margin-bottom:4px">`;
      html += `<label style="font-size:9px;color:var(--text3);cursor:pointer"><input type="radio" name="gantt-view" value="agg" checked onchange="switchGanttView()"> 整体热度</label>`;
      html += `<label style="font-size:9px;color:var(--text3);cursor:pointer;margin-left:8px"><input type="radio" name="gantt-view" value="detail" onchange="switchGanttView()"> 账号明细</label>`;
      html += `</div>`;
      html += `<div id="gantt-chart">${_ganttAgg(gantt)}</div>`;
      html += `</div>`;
      html += `</div>`;
    }

    // ── 进程资源表 ──
    html += `<div class="card" style="padding:8px;flex-direction:column;align-items:stretch">`;
    html += `<div style="font-size:10px;font-weight:bold;color:var(--text2);margin-bottom:4px">进程资源 ${procs.length ? '' : '(空)'}</div>`;
    if (procs.length) {
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
      html += `</table>`;
    }
    html += `</div>`;

    el.innerHTML = html;
    // Restore details open state after re-render
    Object.entries(detailsState).forEach(([id, open]) => {
      const d = el.querySelector(`#${CSS.escape(id)}`);
      if (d) d.open = open;
    });
    const sub = document.getElementById('dash-subtitle');
    if (sub) sub.textContent = `并行${cap.parallel_max} · 还可${cap.max} · ${cap.limit_by||''}${gpu.name ? ' · ' + gpu.name : ''}`;
    if (q && q.paused) {
      const pb = document.getElementById('dash-pause-btn');
      if (pb) pb.textContent = '▶ 恢复';
    }
  } catch(e) { /* silent */ }
}
function _dashStat(val, label, color) {
  return `<div class="card" style="padding:4px 10px;gap:4px;min-width:0"><div style="font-size:15px;font-weight:bold;color:${color};line-height:1.2">${val}</div><div style="font-size:8px;color:var(--text3);line-height:1">${label}</div></div>`;
}
function _gaugeRing(val, max, label, pct) {
  const r = 28, circ = 2 * Math.PI * r;
  const offset = circ * (1 - Math.min(pct, 1));
  const color = pct > 0.8 ? 'var(--danger)' : pct > 0.5 ? 'var(--warn)' : 'var(--accent)';
  const displayVal = max != null ? `${val}/${max}` : val;
  return `<div class="card" style="padding:10px;display:flex;align-items:center;gap:10px">
    <svg width="70" height="70" viewBox="0 0 70 70">
      <circle cx="35" cy="35" r="${r}" fill="none" stroke="var(--bg3)" stroke-width="5"/>
      <circle cx="35" cy="35" r="${r}" fill="none" stroke="${color}" stroke-width="5"
        stroke-dasharray="${circ}" stroke-dashoffset="${offset}"
        transform="rotate(-90, 35, 35)" stroke-linecap="round"/>
      <text x="35" y="35" text-anchor="middle" dominant-baseline="central" fill="${color}" font-size="14" font-weight="bold">${displayVal}</text>
    </svg>
    <span style="font-size:11px;font-weight:bold;color:var(--text2)">${label}</span>
  </div>`;
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
function _ganttChart(events) {
  const W = 800, H = 200, pad = {t:10,r:10,b:25,l:80};
  const iw = W - pad.l - pad.r;
  // Collect task events per aid
  const tasks = {};
  for (const e of events) {
    if (e.event === 'task') {
      if (!tasks[e.aid]) tasks[e.aid] = [];
      tasks[e.aid].push({ts: e.ts, task: e.task});
    }
  }
  // Pair start/stop → runs
  const starts = {};
  const runs = [];
  for (const e of events) {
    if (e.event === 'start') starts[e.aid] = e.ts;
    else if (e.event === 'stop' && starts[e.aid]) {
      const run = {aid: e.aid, name: e.name, start: starts[e.aid], stop: e.ts, dur: e.ts - starts[e.aid]};
      run.tasks = (tasks[e.aid]||[]).filter(t => t.ts >= run.start && t.ts <= run.stop);
      runs.push(run);
      delete starts[e.aid];
    }
  }
  // Running entries (no stop yet)
  for (const [aid, ts] of Object.entries(starts)) {
    const name = events.find(e => e.aid === aid)?.name || aid;
    runs.push({aid, name, start: ts, stop: 0, dur: 0, tasks: (tasks[aid]||[]).filter(t => t.ts >= ts)});
  }
  if (!runs.length) return '<div style="font-size:10px;color:var(--text3);text-align:center;padding:8px">暂无运行记录</div>';
  runs.sort((a, b) => a.start - b.start);
  const minT = Math.min(...runs.map(r => r.start));
  const maxT = Math.max(...runs.map(r => r.stop || Date.now()/1000));
  const span = Math.max(maxT - minT, 60);
  const colors = ['#e74c3c','#3498db','#2ecc71','#f39c12','#9b59b6','#1abc9c'];
  const taskColors = {'唤醒':'#3498db','刷关':'#e74c3c','公招':'#f39c12','基建':'#2ecc71','信用':'#9b59b6','奖励':'#1abc9c','肉鸽':'#e67e22','生息':'#34495e'};
  let svg = `<svg width="${W}" height="${H + runs.length * 28}" style="width:100%;height:auto;background:transparent" viewBox="0 0 ${W} ${H + runs.length * 28}">`;
  runs.forEach((r, i) => {
    const y = pad.t + i * 28;
    svg += `<text x="${pad.l-6}" y="${y+14}" fill="var(--text2)" font-size="9" text-anchor="end">${r.name}</text>`;
    svg += `<rect x="${pad.l}" y="${y+4}" width="${iw}" height="18" fill="var(--bg3)" rx="3"/>`;
    if (r.tasks && r.tasks.length > 1) {
      let prevTs = r.start;
      r.tasks.forEach((t, ti) => {
        const x1 = pad.l + (prevTs - minT) / span * iw;
        const x2 = pad.l + (t.ts - minT) / span * iw;
        if (x2 > x1) {
          const col = taskColors[t.task] || colors[ti % colors.length];
          svg += `<rect x="${x1}" y="${y+5}" width="${Math.max(x2-x1, 3)}" height="16" fill="${col}" rx="2" opacity="0.8"/>`;
          if (x2 - x1 > 30) svg += `<text x="${(x1+x2)/2}" y="${y+16}" fill="#fff" font-size="7" text-anchor="middle">${t.task}</text>`;
        }
        prevTs = t.ts;
      });
      const end = r.stop || Date.now()/1000;
      const xLast = pad.l + (prevTs - minT) / span * iw;
      const xEnd = pad.l + (end - minT) / span * iw;
      if (xEnd > xLast) {
        const col = taskColors[r.tasks[r.tasks.length-1]?.task] || colors[i % colors.length];
        svg += `<rect x="${xLast}" y="${y+5}" width="${Math.max(xEnd-xLast, 2)}" height="16" fill="${col}" rx="2" opacity="0.6"/>`;
      }
    } else {
      const x1 = pad.l + (r.start - minT) / span * iw;
      const x2 = r.dur > 0 ? pad.l + (r.stop - minT) / span * iw : pad.l + iw;
      svg += `<rect x="${x1}" y="${y+5}" width="${Math.max(x2-x1, 2)}" height="16" fill="${colors[i % colors.length]}" rx="3" opacity="${r.dur>0?0.8:0.4}"/>`;
    }
    if (r.tasks && r.tasks.length > 1) {
      const taskNames = [...new Set(r.tasks.map(t => t.task))].join(' → ');
      svg += `<text x="${pad.l+iw+4}" y="${y+16}" fill="var(--text3)" font-size="7">${taskNames}</text>`;
    } else if (r.dur > 0) {
      svg += `<text x="${pad.l+iw+4}" y="${y+15}" fill="var(--text3)" font-size="8">${Math.floor(r.dur/60)}m${Math.floor(r.dur%60)}s</text>`;
    } else {
      svg += `<text x="${pad.l+iw+4}" y="${y+15}" fill="var(--warn)" font-size="8">运行中</text>`;
    }
  });
  svg += `</svg>`;
  return svg;
}
function _ganttAgg(events) {
  // Build concurrent count timeline from start/stop events
  const timeline = [];
  for (const e of events) {
    if (e.event === 'start') timeline.push({ts: e.ts, delta: 1});
    else if (e.event === 'stop') timeline.push({ts: e.ts, delta: -1});
  }
  if (!timeline.length) return '<div style="font-size:10px;color:var(--text3);text-align:center;padding:12px">暂无调度记录</div>';
  timeline.sort((a, b) => a.ts - b.ts);
  const W = 800, H = 120, pad = {t:8,r:8,b:20,l:30};
  const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
  // Build run count curve
  let count = 0;
  const points = [{x: timeline[0].ts, y: 0}];
  for (const t of timeline) {
    count += t.delta;
    points.push({x: t.ts, y: Math.max(0, count)});
  }
  const minT = points[0].x, maxT = Math.max(points[points.length-1].x, minT + 60);
  const span = maxT - minT;
  const maxY = Math.max(1, ...points.map(p => p.y));
  let svg = `<svg width="${W}" height="${H}" style="width:100%;height:auto;max-height:130px;background:transparent" viewBox="0 0 ${W} ${H}">`;
  // Grid
  for (let y = 0; y <= maxY; y++) {
    const yy = pad.t + ih * (1 - y / maxY);
    if (y > 0) svg += `<line x1="${pad.l}" y1="${yy}" x2="${W-pad.r}" y2="${yy}" stroke="var(--border)" stroke-width="0.5"/>`;
    svg += `<text x="${pad.l-4}" y="${yy+3}" fill="var(--text3)" font-size="8" text-anchor="end">${y}</text>`;
  }
  // Area fill
  let path = '';
  for (let i = 0; i < points.length; i++) {
    const x = pad.l + (points[i].x - minT) / span * iw;
    const y = pad.t + ih * (1 - points[i].y / maxY);
    path += (i === 0 ? 'M' : 'L') + x.toFixed(1) + ',' + y.toFixed(1);
  }
  const lastX = pad.l + iw;
  path += ` L${lastX.toFixed(1)},${pad.t+ih} L${pad.l},${pad.t+ih} Z`;
  svg += `<path d="${path}" fill="var(--accent)" fill-opacity="0.15" stroke="var(--accent)" stroke-width="1.5" stroke-linejoin="round"/>`;
  svg += `</svg>`;
  return svg;
}
function switchGanttView() {
  const el = document.getElementById('gantt-chart');
  if (!el) return;
  const view = document.querySelector('input[name="gantt-view"]:checked')?.value;
  const gantt = window._lastGanttData || [];
  el.innerHTML = view === 'agg' ? _ganttAgg(gantt) : _ganttChart(gantt);
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
  window._dispatchMode = '';
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
  let include_anni = true, only_anni = false;
  if (_dashMode === 'anni-only') { include_anni = true; only_anni = true; }
  else if (_dashMode === 'anni') { include_anni = true; only_anni = false; }
  else { include_anni = false; only_anni = false; }
  const r = await apiPost('/action/smart_all', { include_anni, only_anni });
  if (r.ok) toast('已发起一键调度'); else toast(r.error || '调度失败', 'error');
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
      html += `<div style="margin-bottom:12px"><div style="font-size:11px;color:var(--text2);font-weight:bold;margin-bottom:4px">${dateStr} (${run.shots.length}张)</div>`;
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

let _logSource = 'app';

function switchLogSource() {
  const sel = document.getElementById('log-source');
  _logSource = sel?.value || 'app';
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
      <button onclick="previewEmulator('${a.id}',${idx})" style="margin-right:4px">📷 画面预览</button>
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
function showCreateAccountForm() {
  const html = `<div class="dialog-overlay" onclick="event.target==this&&this.remove()">
    <div class="dialog" style="max-width:400px">
      <div style="font-size:14px;font-weight:bold;margin-bottom:12px;color:var(--text2)">创建账号</div>
      <div class="form-row"><label>名称</label><input type="text" id="form-name" value="" placeholder="账号名称"></div>
      <div class="form-row"><label>客户端</label><select id="form-client"><option value="Official">官服</option><option value="Bilibili">B服</option></select></div>
      <div class="form-row"><label>ADB 地址</label><input type="text" id="form-adb" value="" placeholder="127.0.0.1:16384"></div>
      <div class="form-row"><label>模拟器索引</label><input type="number" id="form-emu" value="0" min="0" max="9" style="width:80px"></div>
      <div class="form-row"><label><input type="checkbox" id="form-emu-launch" checked> 自动启动模拟器</label></div>
      <div class="btn-row" style="margin-top:12px">
        <button class="primary" onclick="submitCreateAccount()">创建</button>
        <button onclick="this.closest('.dialog-overlay').remove()">取消</button>
      </div>
    </div>
  </div>`;
  document.body.insertAdjacentHTML('beforeend', html);
}
async function submitCreateAccount() {
  const name = document.getElementById('form-name')?.value?.trim();
  if (!name) { toast('请输入名称', 'error'); return; }
  const r = await apiPost('/account', {
    name,
    game_client: document.getElementById('form-client')?.value || 'Official',
    adb_address: document.getElementById('form-adb')?.value || '',
    emu_instance_index: document.getElementById('form-emu')?.value || '0',
    emu_launch: document.getElementById('form-emu-launch')?.checked || false,
  });
  document.querySelector('.dialog-overlay')?.remove();
  if (r.ok) { toast(`已创建 ${name}`); renderPage(); } else toast(r.error || '创建失败', 'error');
}
async function saveGeneral() {
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
      if (result) result.textContent = r.error || '检查失败';
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
  const bar = document.getElementById('batch-bar');
  if (!bar) return;
  const count = selectedIds.size;
  bar.style.display = count ? 'flex' : 'none';
  const countEl = bar.querySelector('.count');
  if (countEl) countEl.textContent = `已选 ${count}`;
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
            }
          }
        }
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

async function checkOnboarding() {
  try {
    const r = await apiGet('/config');
    if (r.ok && !r.config?.onboarding_done) {
      if (!r.config?.maa_version) {
        navigate('onboarding');
      }
    }
  } catch(e) {}
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
