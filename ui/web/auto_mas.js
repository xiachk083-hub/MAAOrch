// ── auto-mas 页面：目标机 AUTO-MAS 账号信息总览 ──
const AUTO_MAS_HOST = 'http://100.79.173.69:36163';
async function autoMasApi(path, body) {
  const r = await fetch(AUTO_MAS_HOST + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {})
  });
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

// ── 主容器：顶部 tab（账号信息 / 数据 / 产线状态）──
// 默认停在「产线状态」：日常最常看的是它，少点一次是一次
let _autoMasTab = 'status';
async function renderAutoMas(container) {
  container.innerHTML = `<div style="padding:12px">
    <div style="display:flex;gap:0;margin-bottom:12px;border-bottom:2px solid var(--border)">
      <button class="auto-mas-tab" data-tab="accounts" onclick="switchAutoMasTab('accounts')">👤 账号信息</button>
      <button class="auto-mas-tab" data-tab="data" onclick="switchAutoMasTab('data')">📊 数据</button>
      <button class="auto-mas-tab" data-tab="status" onclick="switchAutoMasTab('status')">🏭 产线状态</button>
      <span style="flex:1"></span>
      <span style="font-size:10px;color:var(--text3);align-self:center;padding-right:4px">来源: ${AUTO_MAS_HOST}</span>
    </div>
    <div id="auto-mas-view"></div>
  </div>`;
  switchAutoMasTab(_autoMasTab);
}
function switchAutoMasTab(tab) {
  _autoMasTab = tab;
  document.querySelectorAll('.auto-mas-tab').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  const view = document.getElementById('auto-mas-view');
  if (!view) return;
  if (_masStatusTimer) { clearInterval(_masStatusTimer); _masStatusTimer = null; }  // 离开页签停掉轮询
  if (tab === 'accounts') renderAutoMasAccounts(view);
  else if (tab === 'status') renderAutoMasStatus(view);
  else renderAutoMasData(view);
}

// ── Tab1: 账号信息 ──
async function renderAutoMasAccounts(view) {
  view.innerHTML = `<div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;flex-wrap:wrap">
      <span style="font-size:12px;font-weight:bold;color:var(--text2)">账号信息</span>
      <span style="flex:1"></span>
      <button class="small" onclick="refreshAutoMas()">🔄 刷新</button>
      <button class="small" onclick="exportAutoMasCsv()">⬇ 导出 CSV</button>
    </div>
    <div id="auto-mas-table-wrap" style="overflow:auto;max-height:72vh;border:1px solid var(--border);border-radius:6px">
      <table style="width:100%;border-collapse:collapse;font-size:11px;white-space:nowrap">
        <thead id="auto-mas-head" style="position:sticky;top:0;background:var(--bg2)"></thead>
        <tbody id="auto-mas-body"></tbody>
      </table>
    </div>`;
  await loadAutoMasData();
}
async function loadAutoMasData() {
  const wrap = document.getElementById('auto-mas-table-wrap');
  const body = document.getElementById('auto-mas-body');
  const head = document.getElementById('auto-mas-head');
  if (!wrap) return;
  wrap.style.opacity = '.5';
  try {
    const sc = await autoMasApi('/api/scripts/get', { scriptId: null });
    const rows = [];
    for (const it of sc.index || []) {
      if (it.type !== 'MaaConfig') continue;
      const sid = it.uid;
      const d = sc.data[sid];
      const u = await autoMasApi('/api/scripts/user/get', { scriptId: sid, userId: null });
      for (const uid in (u.data || {})) {
        const ui = u.data[uid].Info || {};
        rows.push({
          acct: (d.Info?.Name || '?').replace('明日方舟-', ''),
          maa: (d.Info?.Path || '').split(String.fromCharCode(92)).slice(-2).join(String.fromCharCode(92)),
          server: ui.Server || '?',
          name: ui.Name || '',
          id: ui.Id || '',
          stage: ui.Stage || '-',
          ann: ui.Annihilation || '?',
          infra: ui.InfrastMode || '?',
          status: ui.Status === false ? '停用' : '启用'
        });
      }
    }
    rows.sort((a, b) => a.acct.localeCompare(b.acct, 'zh'));
    const cols = [
      ['acct', '账号'], ['maa', 'MAA副本'], ['server', '服务器'], ['name', '用户名'],
      ['id', 'Id'], ['stage', '主关卡'], ['ann', '剿灭'], ['infra', '基建'], ['status', '状态']
    ];
    head.innerHTML = `<tr>${cols.map(([k, t]) => `<th style="padding:6px 10px;text-align:left;border-bottom:1px solid var(--border)">${t}</th>`).join('')}</tr>`;
    body.innerHTML = rows.map(r => `<tr>
      ${cols.map(([k]) => `<td style="padding:5px 10px;border-bottom:1px solid var(--border)">${k === 'status' ? (r[k] === '停用' ? '<span style="color:var(--warn)">停用</span>' : '<span style="color:#4caf50">启用</span>') : (r[k] || '')}</td>`).join('')}
    </tr>`).join('');
    window._autoMasRows = rows;
  } catch(e) {
    body.innerHTML = `<tr><td style="padding:20px;color:var(--danger)">拉取失败: ${e.message}（AUTO-MAS 是否在运行 / 网络可达?）</td></tr>`;
    head.innerHTML = '';
  }
  wrap.style.opacity = '1';
}
function refreshAutoMas() { loadAutoMasData(); }
function exportAutoMasCsv() {
  const rows = window._autoMasRows || [];
  if (!rows.length) { toast('暂无数据', 'error'); return; }
  const cols = ['账号', 'MAA副本', '服务器', '用户名', 'Id', '主关卡', '剿灭', '基建', '状态'];
  const lines = [cols.join(',')];
  for (const r of rows) {
    lines.push([r.acct, r.maa, r.server, r.name, r.id, r.stage, r.ann, r.infra, r.status].map(v => '"' + String(v).replace(/"/g, '""') + '"').join(','));
  }
  const blob = new Blob(['\ufeff' + lines.join('\n')], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'auto_mas_accounts.csv';
  a.click();
  URL.revokeObjectURL(a.href);
  toast('CSV 已导出');
}

// ── Tab2: 数据（平均耗时 / 启动次数 / 总占用 / 热力图）──
const AUTO_MAS_DAYS = 60;
// 全局昵称映射：系统名(官-10) -> 用户名(昵称)
let _nickMap = {};
let _dataSource = '目标机实时';
function dispName(a) { return _nickMap[a] || a; }
async function renderAutoMasData(view) {
  view.innerHTML = `<div style="margin-bottom:10px;display:flex;align-items:center;gap:8px;flex-wrap:wrap">
      <span style="font-size:12px;font-weight:bold;color:var(--text2)">运行数据统计</span>
      <span id="auto-mas-src" style="font-size:10px;color:var(--text3)">来源: ${_dataSource}</span>
      <span style="flex:1"></span>
      <button class="small" onclick="loadAutoMasStats()">🔄 刷新</button>
    </div>
    <div id="auto-mas-stats"></div>`;
  await loadAutoMasStats();
}

async function loadAutoMasStats() {
  const wrap = document.getElementById('auto-mas-stats');
  if (!wrap) return;
  wrap.innerHTML = '<div style="color:var(--text3);padding:20px">统计中（从今天往前拉历史，直到连续 14 天无数据）…</div>';
  try {
    // ── 数据源 1：本地存档（优先，秒开 + 目标机丢失可恢复）──
    let arch = null;
    try {
      const rr = await fetch('data/auto_mas_history.json?_=' + Date.now());
      if (rr.ok) arch = await rr.json();
    } catch (e) {}
    let days = [], dayLabels = [];
    const agg = {};
    const acctDayRuns = {};
    const jsonToFetch = [];
    const intervals = [];
    let nickLoaded = false;
    if (arch && arch.days) {
      _dataSource = '本地存档（' + (arch.meta && arch.meta.latest || '') + '）';
      // 存档格式: {"days": {"2026-08-18": {"b-1": [{time,status,duration_sec,json_file}...]}}}
      const dsList = Object.keys(arch.days).sort();
      days = dsList.map(ds => arch.days[ds]);
      dayLabels = dsList;
      dsList.forEach((ds, di) => {
        const accts = arch.days[ds];
        for (const acct in accts) {
          const runs = accts[acct];
          if (!agg[acct]) agg[acct] = { runs: 0, done: 0, err: 0, dayDur: {}, totalSecs: 0, day3Secs: 0, dayRuns: {} };
          if (!acctDayRuns[acct]) acctDayRuns[acct] = {};
          agg[acct].runs += runs.length;
          for (const run of runs) {
            if (run.status === 'DONE') {
              agg[acct].done++;
              if (run.duration_sec) {
                agg[acct].totalSecs += run.duration_sec;
                agg[acct].dayDur[di] = (agg[acct].dayDur[di] || 0) + run.duration_sec;
                if (di >= dsList.length - 3) agg[acct].day3Secs = (agg[acct].day3Secs || 0) + run.duration_sec;
                const startTs = new Date(run.time.replace(' ', 'T')).getTime();
                if (!isNaN(startTs)) intervals.push({ s: startTs, e: startTs + run.duration_sec * 1000 });
              }
            } else if (run.status === 'ERROR') agg[acct].err++;
          }
          acctDayRuns[acct][di] = runs.length;
        }
      });
      if (wrap) wrap.innerHTML = '<div style="color:var(--text3);padding:20px">已加载本地存档 ' + dsList.length + ' 天（' + (arch.meta && arch.meta.latest || '') + '）</div>';
    } else {
      _dataSource = '目标机实时';
      // ── 数据源 2：目标机实时拉取（无存档 fallback）──
      const CHUNK = 42, CONC = 7, EMPTY_STOP = 14, MAX_DAYS = 420;
      const fmt = d => d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
      const dayResults = [];
      let collected = 0, stop = false;
      while (!stop && collected < MAX_DAYS) {
        const batchDates = [];
        for (let k = 0; k < CHUNK && collected + k < MAX_DAYS; k++) {
          const d = new Date();
          d.setDate(d.getDate() - (collected + k));
          batchDates.push(fmt(d));
        }
        const results = [];
        for (let s = 0; s < batchDates.length; s += CONC) {
          const part = await Promise.all(batchDates.slice(s, s + CONC).map(async ds => {
            const r = await autoMasApi('/api/history/search', { mode: 'DAILY', start_date: ds, end_date: ds });
            return { ds, data: (r.data && r.data[ds]) ? r.data[ds] : {} };
          }));
          results.push(...part);
        }
        results.sort((a, b) => (a.ds < b.ds ? -1 : 1));
        dayResults.unshift(...results);
        collected += batchDates.length;
        const tail = dayResults.slice(0, EMPTY_STOP);
        if (tail.length === EMPTY_STOP && tail.every(x => Object.keys(x.data).length === 0)) stop = true;
        if (wrap) wrap.innerHTML = '<div style="color:var(--text3);padding:20px">统计中… 已拉取 ' + dayResults.length + ' 天</div>';
      }
      days = dayResults.map(x => x.data);
      dayLabels = dayResults.map(x => x.ds);
      for (let di = 0; di < days.length; di++) {
        for (const fullName in days[di]) {
          const acct = fullName.replace('明日方舟-', '');
          const info = days[di][fullName];
          const idxs = info.index || [];
          if (!agg[acct]) agg[acct] = { runs: 0, done: 0, err: 0, dayDur: {}, totalSecs: 0, day3Secs: 0, dayRuns: {} };
          if (!acctDayRuns[acct]) acctDayRuns[acct] = {};
          agg[acct].runs += idxs.length;
          let doneN = 0;
          for (const run of idxs) {
            if (run.status === 'DONE') {
              agg[acct].done++;
              doneN++;
              if (doneN <= 3) jsonToFetch.push({ acct, dateIdx: di, jsonFile: run.jsonFile, startTs: new Date(run.date.replace(' ', 'T')).getTime() });
            } else if (run.status === 'ERROR') agg[acct].err++;
          }
          acctDayRuns[acct][di] = (acctDayRuns[acct][di] || 0) + idxs.length;
        }
      }
    }
    // 昵称映射：系统名(官-10) -> 用户名(是我MillerSAMA哒！)
    if (!nickLoaded) {
      try {
        const sc = await autoMasApi('/api/scripts/get', { scriptId: null });
        for (const it of (sc.index || [])) {
          if (it.type !== 'MaaConfig') continue;
          const sysName = (sc.data[it.uid].Info?.Name || '').replace('明日方舟-', '');
          const u = await autoMasApi('/api/scripts/user/get', { scriptId: it.uid, userId: null });
          for (const uid in (u.data || {})) {
            const nick = (u.data[uid].Info?.Name || '').trim();
            if (nick && nick !== sysName) _nickMap[sysName] = nick;
          }
        }
      } catch (e) {}
    }
    const durPat = /用时\s*(\d+)h\s*(\d+)m\s*(\d+)s/;
    let fetched = 0;
    const total = jsonToFetch.length;
    const concurrency = 6;
    async function worker() {
      while (jsonToFetch.length) {
        const item = jsonToFetch.shift();
        try {
          const d = await autoMasApi('/api/history/data', { jsonPath: item.jsonFile.replace(/\\/g, '/') });
          const log = (d.data && d.data.log_content) || '';
          const m = durPat.exec(log);
          if (m) {
            const secs = (+m[1]) * 3600 + (+m[2]) * 60 + (+m[3]);
            agg[item.acct].dayDur[item.dateIdx] = (agg[item.acct].dayDur[item.dateIdx] || 0) + secs;
            agg[item.acct].totalSecs += secs;
            if (item.dateIdx >= AUTO_MAS_DAYS - 3) agg[item.acct].day3Secs = (agg[item.acct].day3Secs || 0) + secs;
            if (item.startTs) intervals.push({ s: item.startTs, e: item.startTs + secs * 1000 });
          }
        } catch (e) {}
        fetched++;
        if (wrap && (fetched % 20 === 0 || fetched === total)) {
          wrap.innerHTML = '<div style="color:var(--text3);padding:20px">统计中… ' + fetched + '/' + total + '</div>';
        }
      }
    }
    await Promise.all(Array.from({ length: concurrency }, worker));
    const accts = Object.keys(agg).sort((a, b) => agg[b].totalSecs - agg[a].totalSecs);
    const rows = accts.map(a => {
      const g = agg[a];
      const avg = g.done ? Math.round(g.totalSecs / g.done / 60 * 10) / 10 : 0;
      const d3 = g.day3Secs ? Math.round(g.day3Secs / 3600 * 10) / 10 + ' h' : '—';
      return `<tr>
        <td style="padding:5px 10px;border-bottom:1px solid var(--border)">${dispName(a)}</td>
        <td style="padding:5px 10px;border-bottom:1px solid var(--border)">${g.runs}</td>
        <td style="padding:5px 10px;border-bottom:1px solid var(--border);color:#4caf50">${g.done}</td>
        <td style="padding:5px 10px;border-bottom:1px solid var(--border);color:var(--danger)">${g.err}</td>
        <td style="padding:5px 10px;border-bottom:1px solid var(--border)">${avg ? avg + ' 分' : '—'}</td>
        <td style="padding:5px 10px;border-bottom:1px solid var(--border)">${d3}</td>
        <td style="padding:5px 10px;border-bottom:1px solid var(--border)">${g.totalSecs ? Math.round(g.totalSecs / 3600 * 10) / 10 + ' h' : '—'}</td>
      </tr>`;
    }).join('');
    let maxMin = 1;
    for (const a of accts) for (const di in agg[a].dayDur) maxMin = Math.max(maxMin, agg[a].dayDur[di] / 60);
    // ── 全局按天热力图 + 点击看当天统计 ──
    // 汇总每天：启动/成功/失败/总用时
    const dayStats = dayLabels.map((lb, di) => {
      let runs = 0, done = 0, err = 0, secs = 0;
      const per = {};
      for (const a of accts) {
        const g = agg[a];
        const dRuns = acctDayRuns[a] ? (acctDayRuns[a][di] || 0) : 0;
        if (!dRuns) continue;
        const dSecs = g.dayDur[di] || 0;
        // 当天该账号的成功/失败数：从 dayDur 无法区分成功次数，用 runs 总数近似
        per[a] = { runs: dRuns, secs: dSecs };
        runs += dRuns; secs += dSecs;
      }
      // weekday: 0=周一..6=周日（本地时区）
      const now = new Date();
      now.setDate(now.getDate() - (AUTO_MAS_DAYS - 1 - di));
      const wd = (now.getDay() + 6) % 7;
      return { label: lb, full: lb, weekday: wd, runs, secs, per };
    });
    window._autoMasDayStats = dayStats;
    let dayMaxSec = 1;
    for (const d of dayStats) dayMaxSec = Math.max(dayMaxSec, d.secs);
    // 全局热力图（ECharts calendar）：7 天一列、周一到周日，颜色=当天总用时，点击看当天
    // ECharts calendar 数据: [YYYY-MM-DD, 当天总分钟]
    const heatData = dayStats.map((d, di) => ({
      ds: d.label, mins: Math.round(d.secs / 60), runs: d.runs, di,
      acct: Object.keys(d.per).length ? Object.keys(d.per)[0] : ''
    }));
    const heatStart = heatData[0].ds, heatEnd = heatData[heatData.length - 1].ds;
    const heatMaxMin = Math.max(1, ...heatData.map(d => d.mins));
    // 正方形格子热力图：从最早有数据日到今天，每天一格，无数据日保留灰格占位，
    // 反向填充（最新在右下角），行数固定 7，列数自适应
    // 1) 裁掉开头连续无数据的天（heatData 里 mins=0 且 runs=0 的天）
    let startIdx = 0;
    while (startIdx < heatData.length - 1 && heatData[startIdx].runs === 0 && heatData[startIdx].mins === 0) startIdx++;
    const seg = heatData.slice(startIdx);
    // 2) 顺序流渲染：最早在左上，最新在右下（auto-fill 自动换行，能放多少放多少）
    let heatCells = '';
    for (const d of seg) {
      const color = d.mins === 0 ? 'var(--bg3)' : `rgba(76,175,80,${0.15 + 0.85 * (d.mins / heatMaxMin)})`;
      const active = window._autoMasDaySel === d.di ? 'outline:2px solid var(--accent);' : '';
      heatCells += `<div onclick="showAutoMasDay(${d.di})" title="${d.ds} 启动${d.runs}次 用时${d.mins}分（点击看当天）"
        style="width:24px;height:24px;background:${color};border-radius:3px;cursor:pointer;${active}"></div>`;
    }
    let heatHtml = `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(24px,24px));grid-auto-rows:24px;gap:3px;width:100%">${heatCells}</div>`;
    const totalRuns = accts.reduce((s, a) => s + agg[a].runs, 0);
    const totalDone = accts.reduce((s, a) => s + agg[a].done, 0);
    const totalErr = accts.reduce((s, a) => s + agg[a].err, 0);
    const sumSecs = accts.reduce((s, a) => s + agg[a].totalSecs, 0);
    const totalH = Math.round(sumSecs / 3600 * 10) / 10;
    // 总平均耗时：所有成功运行用时均值
    const avgAll = totalDone ? Math.round(sumSecs / totalDone / 60 * 10) / 10 + ' 分' : '—';
    // 墙钟占用：合并所有运行区间（9 并发并行只算一次），空闲时段扣除
    intervals.sort((x, y) => x.s - y.s);
    let wallMs = 0, curS = 0, curE = 0;
    for (const iv of intervals) {
      if (!curE) { curS = iv.s; curE = iv.e; continue; }
      if (iv.s <= curE) { if (iv.e > curE) curE = iv.e; }
      else { wallMs += curE - curS; curS = iv.s; curE = iv.e; }
    }
    if (curE) wallMs += curE - curS;
    const wallH = Math.round(wallMs / 3600000 * 10) / 10;
    // 排序：默认按总耗时降序（用户要排序）
    accts.sort((a, b) => agg[b].totalSecs - agg[a].totalSecs);
    wrap.innerHTML = `
      <div style="display:flex;gap:12px;margin-bottom:12px;flex-wrap:wrap">
        ${[['账号数', accts.length], ['启动次数', totalRuns], ['成功', totalDone],
          ['失败', totalErr], ['总平均耗时', avgAll], ['总耗时', totalH + ' h'], ['墙钟占用', wallH + ' h']].map(([k, v]) => `<div style="background:var(--bg2);border:1px solid var(--border);border-radius:6px;padding:8px 14px;min-width:80px;text-align:center">
          <div style="font-size:16px;font-weight:bold;color:var(--accent)">${v}</div>
          <div style="font-size:9px;color:var(--text3)">${k}</div>
        </div>`).join('')}
      </div>
      <div style="overflow:auto;max-height:40vh;border:1px solid var(--border);border-radius:6px;margin-bottom:12px">
        <table style="width:100%;border-collapse:collapse;font-size:11px;white-space:nowrap">
          <thead style="position:sticky;top:0;background:var(--bg2)">
            <tr><th style="padding:6px 10px;text-align:left">账号</th><th>启动</th><th>成功</th><th>失败</th><th>平均耗时</th><th>近3天耗时</th><th>总耗时</th></tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      <div style="font-size:12px;font-weight:bold;color:var(--text2);margin-bottom:6px">总运行热力图（从最早数据日至今 · 每天一格 · 无数据灰格 · 最新右下角 · 点击查看当天统计）</div>
      <div style="overflow:auto;border:1px solid var(--border);border-radius:6px;padding:8px;margin-bottom:12px">
        ${heatHtml}
      </div>
      <div id="auto-mas-day-stats"></div>`;
    const srcEl = document.getElementById('auto-mas-src');
    if (srcEl) srcEl.textContent = '来源: ' + _dataSource;
    if (window._autoMasDaySel !== undefined && window._autoMasDaySel >= 0) showAutoMasDay(window._autoMasDaySel);
  } catch(e) {
    wrap.innerHTML = `<div style="color:var(--danger);padding:20px">统计失败: ${e.message}</div>`;
  }
}


// 点击热力图某天 → 显示当天各账号统计
function showAutoMasDay(di) {
  window._autoMasDaySel = di;
  const el = document.getElementById('auto-mas-day-stats');
  const dayStats = window._autoMasDayStats || [];
  if (!el || !dayStats[di]) return;
  const d = dayStats[di];
  const rows = Object.keys(d.per).sort((a, b) => d.per[b].secs - d.per[a].secs).map(a => {
    const p = d.per[a];
    return `<tr>
      <td style="padding:4px 10px;border-bottom:1px solid var(--border)">${dispName(a)}</td>
      <td style="padding:4px 10px;border-bottom:1px solid var(--border)">${p.runs}</td>
      <td style="padding:4px 10px;border-bottom:1px solid var(--border)">${p.secs ? Math.round(p.secs / 60) + ' 分' : '—'}</td>
    </tr>`;
  }).join('');
  el.innerHTML = `<div style="font-size:12px;font-weight:bold;color:var(--text2);margin-bottom:6px">${d.label} 当天统计 · 共启动 ${d.runs} 次 · 总用时 ${Math.round(d.secs / 60)} 分</div>
    <div style="overflow:auto;max-height:30vh;border:1px solid var(--border);border-radius:6px">
      <table style="width:100%;border-collapse:collapse;font-size:11px;white-space:nowrap">
        <thead style="position:sticky;top:0;background:var(--bg2)"><tr><th style="padding:5px 10px;text-align:left">账号</th><th>启动</th><th>用时</th></tr></thead>
        <tbody>${rows || '<tr><td style="padding:10px;color:var(--text3)">当天无运行</td></tr>'}</tbody>
      </table>
    </div>`;
  // 高亮选中的格子
  document.querySelectorAll('#auto-mas-stats table tbody td[onclick]').forEach(td => td.style.outline = 'none');
  const cells = document.querySelectorAll('#auto-mas-stats table tbody td[onclick]');
  if (cells[di]) cells[di].style.outline = '2px solid var(--accent)';
}

// ══ Tab3: 产线状态（L3 工作台）══════════════════════════════════════
// 数据：本地后端 GET /api/mas/status → services/mas_l3.py 聚合
//   ① 主机实时快照（SSH：在飞 MAA / 盘位 / 队列进度 / 失败 / 未跑）
//   ② AUTO-MAS 近 7 天历史（一次过 / 补跑成功 / 全败 / 原因 / 钉子户）
//   ③ 本地留痕（日报 latest.md、台账 mas_ledger.jsonl）
// 动作：POST /api/mas/rerun（后台补跑）· POST /api/mas/cleanup（清临时队列）
let _masStatusTimer = null, _masAutoMs = 60000, _masBusy = false;
const _masEsc = s => String(s == null ? '' : s).replace(/[&<>"]/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const _masShift = s => s === 'daily' ? '早班' : (s === 'evening' ? '晚班' : (s || '非窗口期'));
const _TH = 'padding:5px 10px;text-align:left;border-bottom:1px solid var(--border);font-size:10px;color:var(--text3);font-weight:normal';
const _TD = 'padding:5px 10px;border-bottom:1px solid var(--border)';
const _C_OK = '#4caf50', _C_WARN = 'var(--warn)', _C_BAD = 'var(--danger)';

async function renderAutoMasStatus(view) {
  view.innerHTML = `<div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;flex-wrap:wrap">
      <span style="font-size:12px;font-weight:bold;color:var(--text2)">产线状态</span>
      <span id="mas-src" style="font-size:10px;color:var(--text3)"></span>
      <span style="flex:1"></span>
      <button class="small" onclick="loadMasStatus(true)">🔄 刷新</button>
      <button class="small" onclick="masRerunAll()">🚑 补跑失败号</button>
      <button class="small" onclick="masCleanup()">🧹 清临时队列</button>
      <label style="font-size:10px;color:var(--text3);display:flex;align-items:center;gap:4px;cursor:pointer">
        <input type="checkbox" id="mas-auto" checked onchange="masToggleAuto()">自动刷新</label>
    </div>
    <div id="mas-body"></div>`;
  await loadMasStatus(false);
  masToggleAuto();
}

function masToggleAuto() {
  if (_masStatusTimer) { clearInterval(_masStatusTimer); _masStatusTimer = null; }
  const cb = document.getElementById('mas-auto');
  if (cb && cb.checked) _masStatusTimer = setInterval(() => loadMasStatus(false), _masAutoMs);
}

async function loadMasStatus(force) {
  const body = document.getElementById('mas-body');
  if (!body) return;
  if (force || !body.dataset.loaded)
    body.innerHTML = '<div style="color:var(--text3);padding:20px;font-size:11px">采集主机快照（SSH）+ 聚合近 7 天历史…</div>';
  const r = await apiGet('/mas/status?days=7' + (force ? '&refresh=1' : ''));
  const b2 = document.getElementById('mas-body');
  if (!b2) return;
  if (!r || !r.generated_at) { showError(b2, (r && (r.error || r.detail)) || '产线状态采集失败（后端 /api/mas/status）'); return; }
  b2.dataset.loaded = '1';
  b2.innerHTML = _masRender(r);
  const src = document.getElementById('mas-src');
  if (src) src.textContent = '更新 ' + r.generated_at + (r.host && r.host.cached ? '（缓存）' : '');
  _masAutoMs = (r.rerun && r.rerun.running) ? 20000 : 60000;   // 补跑中看紧一点
  if (_masStatusTimer) masToggleAuto();
}

function _masTile(label, val, color, sub) {
  return `<div style="flex:0 0 auto;min-width:84px;background:var(--bg2);border:1px solid var(--border);border-radius:6px;padding:7px 10px">
    <div style="font-size:10px;color:var(--text3)">${label}</div>
    <div style="font-size:19px;font-weight:bold;line-height:1.25;color:${color || 'var(--text)'}">${val}</div>
    ${sub ? `<div style="font-size:9px;color:var(--text3)">${sub}</div>` : ''}</div>`;
}

function _masRender(r) {
  const h = r.host || {}, t = r.today || {}, run = r.rerun || {}, n = v => (v == null ? 0 : v);
  const hostOk = h.ok !== false;
  const out = [];

  // ① 状态条
  out.push(`<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:11px;color:var(--text2);margin-bottom:8px">
    <span style="color:${hostOk ? _C_OK : _C_BAD}">● ${hostOk ? '主机在线' : '主机采集失败'}</span>
    <span>${_masEsc(h.now || r.generated_at)}</span><span>${_masShift(h.shift)}</span>
    <span>在跑 MAA <b>${n(h.maa_processes)}</b></span>
    <span>快照 ${h.age_s == null ? '-' : h.age_s}s 前</span>
    ${run.running ? `<span style="color:${_C_WARN}">🚑 补跑中：${_masEsc((run.accounts || []).join('、'))}（${_masEsc(run.started_at || '')} 起）</span>` : ''}
    ${!run.running && run.done_at && run.ok === false ? `<span style="color:${_C_BAD}">上次补跑失败 @ ${_masEsc(run.done_at)}</span>` : ''}
    ${h.stale ? `<span style="color:${_C_BAD}">⚠ 旧快照：${_masEsc(h.error || '')}</span>` : ''}
  </div>`);

  // ② 今日
  out.push(`<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">
    ${_masTile('一次过', n(t.ok), _C_OK, t.date || '')}
    ${_masTile('补跑成功', n(t.retry), n(t.retry) ? _C_WARN : 'var(--text3)', '失败后跑通')}
    ${_masTile('全败', n(t.fail), n(t.fail) ? _C_BAD : 'var(--text3)', '最后一次仍失败')}
    ${_masTile('账号', n(t.accounts), 'var(--text)', '今日有记录')}
    ${_masTile('尝试', n(t.runs), 'var(--text)', '错 ' + n(t.errors) + ' 次')}
    ${_masTile('未跑', n((h.never_ran || []).length), (h.never_ran || []).length ? _C_BAD : 'var(--text3)', '今天无记录')}
    ${_masTile('队列待跑', n(r.waiting), n(r.waiting) ? _C_WARN : 'var(--text3)', '还没轮到')}
    ${_masTile('首末', (t.first || '-') + '~' + (t.last || '-'), 'var(--text)', '今日起止')}
  </div>`);

  // ③ 需要处理
  const fails = h.failures || [];
  const never = h.never_ran || [];
  const todo = fails.length + never.length;
  if (todo) {
    const rows = fails.map(f => `<tr>
        <td style="${_TD};font-weight:bold">${_masEsc(dispName(f.name))}</td>
        <td style="${_TD};color:${_C_BAD}">${_masEsc(f.reason)}</td>
        <td style="${_TD};color:var(--text3)">${_masEsc((f.ts || '').slice(5))}</td>
        <td style="${_TD}"><button class="small" onclick="masRerun(['${_masEsc(f.name).replace(/'/g, "\\'")}'])">补跑</button></td>
      </tr>`).join('');
    out.push(`<div style="border:1px solid ${_C_BAD};border-radius:6px;padding:8px 10px;margin-bottom:10px;background:var(--bg2)">
      <div style="font-size:11px;font-weight:bold;color:${_C_BAD};margin-bottom:6px">⚠️ 需要处理 · 失败 ${fails.length} · 未跑 ${never.length}
        <button class="small" style="margin-left:8px" onclick="masRerunAll()">🚑 一键补跑全部失败号</button></div>
      ${rows ? `<div style="overflow:auto"><table style="width:100%;border-collapse:collapse;font-size:11px;white-space:nowrap">
        <thead><tr><th style="${_TH}">账号</th><th style="${_TH}">原因</th><th style="${_TH}">时间</th><th style="${_TH}">操作</th></tr></thead>
        <tbody>${rows}</tbody></table></div>` : ''}
      ${never.length ? `<div style="font-size:10px;color:var(--text2);margin-top:6px">未跑：${_masEsc(never.map(dispName).join('、'))}</div>` : ''}
    </div>`);
  } else if (hostOk && t.accounts) {
    out.push(`<div style="border:1px solid ${_C_OK};border-radius:6px;padding:8px 10px;margin-bottom:10px;background:var(--bg2);font-size:11px;color:${_C_OK}">
      ✅ 无需处理：今日 ${n(t.accounts)} 个号全部有成功记录，没有未跑的号</div>`);
  }

  // ④ 近 7 天
  const daily = (r.daily || []).slice().reverse();
  if (daily.length) {
    const drows = daily.map(d => {
      const rate = d.accounts ? Math.round(100 * (d.retry + d.fail) / d.accounts) : 0;
      const rc = d.fail ? _C_BAD : (d.retry ? _C_WARN : _C_OK);
      return `<tr>
        <td style="${_TD}">${_masEsc(d.date)}</td>
        <td style="${_TD}">${d.accounts}</td>
        <td style="${_TD};color:${_C_OK}">${d.ok}</td>
        <td style="${_TD};color:${d.retry ? _C_WARN : 'var(--text3)'}">${d.retry}</td>
        <td style="${_TD};color:${d.fail ? _C_BAD : 'var(--text3)'};font-weight:${d.fail ? 'bold' : 'normal'}">${d.fail}</td>
        <td style="${_TD};color:${rc}">${rate}%</td>
        <td style="${_TD};color:var(--text3)">${d.runs} / 错 ${d.errors}</td>
        <td style="${_TD};color:var(--text3)">${_masEsc((d.first || '-') + '~' + (d.last || '-'))}</td>
        <td style="${_TD};color:var(--text3);max-width:280px;overflow:hidden;text-overflow:ellipsis" title="${_masEsc((d.failed_names || []).join('、'))}">${_masEsc((d.failed_names || []).slice(0, 4).map(dispName).join('、'))}${(d.failed_names || []).length > 4 ? ' …' : ''}</td>
      </tr>`;
    }).join('');
    out.push(`<div style="font-size:11px;font-weight:bold;color:var(--text2);margin:10px 0 4px">近 7 天（新→旧）</div>
      <div style="overflow:auto;border:1px solid var(--border);border-radius:6px">
      <table style="width:100%;border-collapse:collapse;font-size:11px;white-space:nowrap">
        <thead><tr><th style="${_TH}">日期</th><th style="${_TH}">账号</th><th style="${_TH}">一次过</th><th style="${_TH}">补跑成功</th>
          <th style="${_TH}">全败</th><th style="${_TH}">失败率</th><th style="${_TH}">尝试/错</th><th style="${_TH}">首末</th><th style="${_TH}">当天全败的号</th></tr></thead>
        <tbody>${drows}</tbody></table></div>`);
  }

  // ⑤ 失败原因 + 钉子户
  const causes = r.causes || [], offs = r.offenders || [];
  if (causes.length || offs.length) {
    const crows = causes.map(c => `<tr><td style="${_TD}">${_masEsc(c.reason)}</td>
        <td style="${_TD};text-align:right">${c.count}</td><td style="${_TD};text-align:right;color:var(--text3)">${c.accounts}</td></tr>`).join('');
    const orows = offs.map(o => `<tr><td style="${_TD};font-weight:bold">${_masEsc(dispName(o.name))}</td>
        <td style="${_TD};text-align:right;color:${o.fails >= 2 ? _C_BAD : 'var(--text3)'}">${o.fails}</td>
        <td style="${_TD};text-align:right;color:var(--text3)">${o.days}</td>
        <td style="${_TD};color:var(--text3)">${_masEsc(o.last_reason)}</td>
        <td style="${_TD};color:var(--text3)">${_masEsc((o.last_ts || '').slice(5, 16))}</td></tr>`).join('');
    out.push(`<div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:10px">
      <div style="flex:1 1 320px">
        <div style="font-size:11px;font-weight:bold;color:var(--text2);margin-bottom:4px">失败原因（近 7 天）</div>
        <div style="border:1px solid var(--border);border-radius:6px;overflow:auto;max-height:34vh">
        <table style="width:100%;border-collapse:collapse;font-size:11px">
          <thead><tr><th style="${_TH}">原因</th><th style="${_TH};text-align:right">次数</th><th style="${_TH};text-align:right">涉及号</th></tr></thead>
          <tbody>${crows || `<tr><td style="${_TD};color:var(--text3)">无</td></tr>`}</tbody></table></div>
      </div>
      <div style="flex:1 1 320px">
        <div style="font-size:11px;font-weight:bold;color:var(--text2);margin-bottom:4px">钉子户（近 7 天）· 全败天/涉及天</div>
        <div style="border:1px solid var(--border);border-radius:6px;overflow:auto;max-height:34vh">
        <table style="width:100%;border-collapse:collapse;font-size:11px;white-space:nowrap">
          <thead><tr><th style="${_TH}">账号</th><th style="${_TH};text-align:right">全败</th><th style="${_TH};text-align:right">涉及</th><th style="${_TH}">最近原因</th><th style="${_TH}">最近时间</th></tr></thead>
          <tbody>${orows || `<tr><td style="${_TD};color:var(--text3)">无</td></tr>`}</tbody></table></div>
      </div>
    </div>`);
  }

  // ⑥ 机器：盘位 + 队列进度
  const disk = r.disk || {};
  const dc = Object.keys(disk).sort().map(k => {
    const d = disk[k] || {};
    const pct = d.total_gb ? Math.round(100 * (1 - n(d.free_gb) / d.total_gb)) : 0;
    const col = d.low ? _C_BAD : _C_OK;
    return `<div style="flex:0 0 auto;min-width:150px;background:var(--bg2);border:1px solid var(--border);border-radius:6px;padding:7px 10px">
      <div style="font-size:10px;color:var(--text3)">${_masEsc(k)}: 盘${d.low ? ' ⚠ 告急' : ''}</div>
      <div style="font-size:14px;font-weight:bold;color:${col}">剩 ${n(d.free_gb)}G
        <span style="font-size:10px;color:var(--text3);font-weight:normal">/ ${n(d.total_gb)}G</span></div>
      <div style="height:4px;background:var(--bg3);border-radius:2px;margin-top:5px;overflow:hidden">
        <div style="height:100%;width:${pct}%;background:${col}"></div></div>
      <div style="font-size:9px;color:var(--text3);margin-top:3px">已用 ${pct}% · 告警线 ${n(d.warn_gb)}G</div></div>`;
  }).join('');
  const queues = h.queues || {};
  const qc = Object.keys(queues).sort((a, b) => a.localeCompare(b, 'zh')).map(qn => {
    const q = queues[qn] || {}, items = n(q.items), done = n(q.done), pend = q.pending || [];
    const pct = items ? Math.round(100 * done / items) : 0;
    const col = pend.length ? _C_WARN : _C_OK;
    return `<div style="flex:0 0 auto;min-width:180px;max-width:250px;background:var(--bg2);border:1px solid var(--border);border-radius:6px;padding:7px 10px">
      <div style="display:flex;align-items:center;gap:6px"><span style="font-size:11px;font-weight:bold">${_masEsc(qn)}</span>
        <span style="flex:1"></span><span style="font-size:10px;color:var(--text3)">${done}/${items}</span></div>
      <div style="height:4px;background:var(--bg3);border-radius:2px;margin-top:5px;overflow:hidden">
        <div style="height:100%;width:${pct}%;background:${col}"></div></div>
      <div style="font-size:9px;color:var(--text3);margin-top:3px">${_masEsc((q.first || '-') + '~' + (q.last || '-'))} · ${pend.length ? '待跑 ' + pend.length : '完成'}</div>
      ${pend.length ? `<div style="font-size:9px;color:var(--text3);margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
        title="${_masEsc(pend.join('、'))}">${_masEsc(pend.slice(0, 3).map(dispName).join('、'))}${pend.length > 3 ? '…' : ''}</div>` : ''}
    </div>`;
  }).join('');
  out.push(`<div style="font-size:11px;font-weight:bold;color:var(--text2);margin:12px 0 4px">机器 · 盘位与队列进度</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap">${dc}${qc}</div>`);

  // ⑦ 留痕：日报 + 台账
  const rep = r.report || {};
  const led = (r.ledger || []).slice(-8).reverse().map(e => `<div style="font-size:9px;color:var(--text3)">${_masEsc((e.ts || '').slice(5))} · ${_masEsc(e.kind)}
      ${e.accounts ? ' · ' + _masEsc((e.accounts || []).join('、')) : ''}${e.shift ? ' · ' + _masShift(e.shift) : ''}${e.failures != null ? ' · 失败 ' + e.failures : ''}${e.ok === false ? ' ✗' : ''}</div>`).join('');
  out.push(`<div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:12px">
    <details style="flex:1 1 380px"${run.detail && !run.running ? ' open' : ''}>
      <summary style="cursor:pointer;font-size:11px;color:var(--text2)">📄 最新日报 ${_masEsc(rep.mtime || '')}（${_masEsc(rep.path || '无')}）</summary>
      <pre style="white-space:pre-wrap;font-size:10px;color:var(--text2);background:var(--bg2);border:1px solid var(--border);border-radius:6px;padding:8px;margin-top:6px;max-height:38vh;overflow:auto">${_masEsc(rep.text || '（还没有报告：班次收工后自动生成）')}</pre>
    </details>
    <div style="flex:0 1 300px">
      <div style="font-size:11px;font-weight:bold;color:var(--text2);margin-bottom:4px">台账（最近事件）</div>
      <div style="border:1px solid var(--border);border-radius:6px;padding:6px 8px;background:var(--bg2)">
        ${led || '<div style="font-size:9px;color:var(--text3)">暂无</div>'}
        ${run.detail && !run.running ? `<div style="font-size:9px;color:var(--text3);margin-top:6px;border-top:1px solid var(--border);padding-top:4px">上次补跑输出：${_masEsc(run.detail.slice(-400))}</div>` : ''}
      </div>
    </div>
  </div>`);

  return out.join('');
}

async function masRerun(names) {
  if (_masBusy) return;
  const okc = await showConfirm(names
    ? `补跑这 ${names.length} 个号？<br><b>${_masEsc(names.join('、'))}</b>`
    : '补跑当前所有失败号？<br>（同号每天最多一次，单批最多 8 个）');
  if (!okc) return;
  _masBusy = true;
  const r = await apiPost('/mas/rerun', { accounts: names || [] });
  _masBusy = false;
  if (r && r.ok) {
    toast('已发车：' + (r.started || []).join('、'), 'success');
    setTimeout(() => loadMasStatus(true), 1200);
  } else toast((r && r.error) || '补跑失败', 'error');
}
function masRerunAll() { masRerun(null); }

async function masCleanup() {
  if (_masBusy) return;
  if (!await (showConfirm('删除所有临时补跑队列？（正在跑的临时队列也会被删）'))) return;
  _masBusy = true;
  const r = await apiPost('/mas/cleanup', {});
  _masBusy = false;
  toast(r && r.ok ? '已清理临时补跑队列' : ((r && r.error) || '清理失败'), r && r.ok ? 'success' : 'error');
  loadMasStatus(true);
}
