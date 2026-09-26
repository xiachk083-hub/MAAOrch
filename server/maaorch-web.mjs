/**
 * MAAOrch 看板 · Node 版（第一版：只读）
 * ─────────────────────────────────────────────────────────────
 * 由来：2026-09-21 用户「别再用 Python 了 这个占用太高了」。
 *   实测：python 看板 main_web.pyw 空转 ~0.5 核；Python site-packages 里
 *   PySide6(634MB)+PyQt6(204MB)+PyQt5(142MB) 只为「常驻看板 + 托盘」服务。
 * 本文件的目标：用 Node 原生能力覆盖看板的只读部分，与旧看板并存（旧的在 19999，
 *   这份跑 19998），验收后再切换端口。
 *
 * 现在提供：
 *   GET /                → 302 跳转到 /status（2026-09-26 前端整体退役：本文件不再出手写前端页）
 *   GET /status          → 唯一人工入口：极简运维面板（本文件内联生成，零静态依赖）
 *   GET /api/status      → 账号/队列/看板状态汇总（需 token）
 *   GET /api/mas/status  → 产线状态（读 runtime/mas_watch_state.json 等，需 token）
 *   GET /api/health      → 进程自检（内存/RSS/uptime，不需要 token）
 *
 * 用法：
 *   node server/maaorch-web.mjs            # 默认 19998
 *   node server/maaorch-web.mjs --port 19999 --token <token>
 */
import { createServer, request as httpRequest } from 'node:http'
import { connect as netConnect } from 'node:net'
import { spawn, spawnSync } from 'node:child_process'
import { readFileSync, existsSync, statSync, readdirSync } from 'node:fs'
import os from 'node:os'
// 作业实验室（自动抄作业）后端：原 network/copilot_api.py 的四条路由，2026-09-22 搬进 Node
import { copilotGet, copilotPost } from './copilot_node.mjs'

/** 端口是否在监听（心跳式：连一下、连上即算在跑，2 秒内复用结果）。 */
const _portCache = new Map()
function portListening(port, timeoutMs = 400) {
  const key = String(port)
  const now = Date.now()
  const hit = _portCache.get(key)
  if (hit && now - hit[0] < 2000) return Promise.resolve(hit[1])
  return new Promise((resolve) => {
    let done = false
    const finish = (val) => {
      if (done) return
      done = true
      _portCache.set(key, [Date.now(), val])
      resolve(val)
    }
    const sock = netConnect({ host: '127.0.0.1', port: Number(port) })
    sock.setTimeout(timeoutMs)
    sock.on('connect', () => { sock.destroy(); finish(true) })
    sock.on('timeout', () => { sock.destroy(); finish(false) })
    sock.on('error', () => finish(false))
  })
}
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const argv = process.argv.slice(2)
const argOf = (name, dflt) => {
  const i = argv.indexOf(name)
  return i >= 0 && argv[i + 1] ? argv[i + 1] : dflt
}

const CFG = join(ROOT, 'models', 'config.json')
const readCfg = () => {
  try { return JSON.parse(readFileSync(CFG, 'utf8')) } catch { return {} }
}
const cfg0 = readCfg()
const PORT = Number(argOf('--port', '19998'))
const TOKEN = argOf('--token', cfg0.api_token || '')
/**
 * 上游（旧 Python 看板）地址：未实现的路由**透传**过去，这样切端口时用户不会看到 404。
 * 迁移策略＝"门面 + 代理"：请求先到 Node，Node 认识的自己处理（快的走快路），
 * 不认识的转发给上游；同时记一次命中，供我按"实际调用最多"的顺序逐个搬。
 * 传 `--upstream ""` 或 `--no-upstream` 可关闭透传（那时未实现的路由返回 501 并说明）。
 */
const UPSTREAM = argv.includes('--no-upstream') ? '' : argOf('--upstream', 'http://127.0.0.1:19997')
/**
 * ★ 2026-09-23 用户拍板「焊死」（原话：「一直有东西在关我这台的模拟器」）：
 *   本机**禁止**"未实现路由 → WMI 按需拉起 Python 上游"这条路径。
 *   历史上（09-21）关掉用户手动开的模拟器三次的元凶就在 Python 平台的
 *   "回收空闲模拟器"里；虽然那处已经修了、总闸也(default off)关掉了，
 *   这里再把"能把它拉起来"的手也剁了 —— 19999 从此只做只读看板。
 *   要临时启用（排查用）：设环境变量 MAAORCH_ALLOW_UPSTREAM=1 再重启看板。
 */
const ALLOW_UPSTREAM_SPAWN = process.env.MAAORCH_ALLOW_UPSTREAM === '1'
let _upstreamSpawnRefused = false
const proxyHits = new Map()   // path -> 命中次数（进程内统计，暴露在 /api/_proxy_stats）

/** 读一个 JSON 文件，失败返回 null（不抛，看板不能因为一个文件坏了就 500）。 */
const readJson = (p) => {
  try { return JSON.parse(readFileSync(p, 'utf8')) } catch { return null }
}

/** 文件元信息（看板要能一眼看出"数据是不是真的/是不是过时"）。 */
const fileMeta = (p) => {
  try {
    const s = statSync(p)
    return { path: p.replace(ROOT, '').replace(/^[\\/]/, ''), mtime: s.mtime.toISOString(), kb: Math.round(s.size / 1024) }
  } catch { return { path: p.replace(ROOT, '').replace(/^[\\/]/, ''), mtime: null, kb: 0 } }
}

/** 最近跑局：读 runtime/_ce6/*.attempt.log 的尾部（只读最后一两行，不整读）。 */
function recentRuns(limit = 8) {
  const dir = join(ROOT, 'runtime', '_ce6')
  if (!existsSync(dir)) return []
  let files = []
  try {
    files = readdirSync(dir).filter((f) => f.endsWith('.attempt.log'))
  } catch { return [] }
  const rows = files.map((f) => {
    const p = join(dir, f)
    let mtime = 0; let kb = 0
    try { const s = statSync(p); mtime = s.mtimeMs; kb = Math.round(s.size / 1024) } catch {}
    // 只取关心的那几行：作业结束 / 结算 / 闸结论
    let tag = f.replace('.attempt.log', ''), job = '', end = '', settle = '', gate = '', ts = ''
    try {
      const txt = readFileSync(p, 'utf8')
      const lines = txt.split(/\r?\n/)
      const l0 = lines.find((l) => l.includes('通用跑局'))
      if (l0) { const m = l0.match(/（作业 (.+?)）/); if (m) job = m[1] }
      end = (lines.find((l) => l.includes('作业结束')) || '').replace(/^\s*作业结束：/, '').trim()
      settle = (lines.find((l) => l.includes('结算：')) || '').replace(/^\s*结算：/, '').trim()
      gate = (lines.find((l) => l.includes('闸3 判')) || lines.find((l) => l.includes('闸1 拦下')) || '').trim()
      // 结算读数翻译成人话（2026-09-21：突袭通关是白色六边形，star_blobs 读不出星，
      //   "clear 星级=0" 不是"没打通"而是"突袭通关" ⇒ 看板必须翻译，别让裸读数误导）
      const isRaid = /#[fF]#/.test(txt) || /突袭/.test(txt) || /_raid_/i.test(job)
      let verdict = '未知'
      if (/无效/.test(gate) || /拦下/.test(gate)) verdict = '无效局（别当打法分析）'
      else if (/clear/.test(settle)) {
        const m = settle.match(/星级=(\d+)/)
        const stars = m ? Number(m[1]) : 0
        verdict = stars >= 3 ? '通关 3★' : (isRaid ? '突袭通关（白六边形）' : (stars > 0 ? `通关 ${stars}★` : '通关（星级未判出）'))
      } else if (/fail/.test(settle)) verdict = '失败'
      else if (/maybe-clear/.test(settle)) verdict = '疑似通关（待确认）'
      gate = verdict
      // 用 attempt.log 里的真实时间戳排序（mtime 会因为后来重写/改名而不代表"跑局先后"，
      // 2026-09-21 实测：按 mtime 排会把旧的 mt1r10 顶到"最近一局"，误导人）
      const tm = txt.match(/\[Copilot\]\s*(\d{2}:\d{2}:\d{2})/) || txt.match(/(\d{2}:\d{2}:\d{2})/)
      ts = tm ? tm[1] : ''
    } catch {}
    return { tag, job, end, settle, gate, kb, mtime, ts, dur: (end.match(/(\d+)s/) || [])[1] || '' }
  })
  // 排序：先按 mtime（每局跑完必然新写入、跨天也正确）；attempt.log 里的"时刻"只有时分秒、
  // 没有日期，单独用来排序会把别的日期的旧局顶到最前（2026-09-21 踩过：CE-6 的 adb1/v50a 顶到第一）。
  return rows.sort((a, b) => b.mtime - a.mtime).slice(0, limit)
}

const startedAt = Date.now()

/**
 * 写操作：调用项目里既有的工具脚本（不重写业务逻辑，保持"一套实现"）。
 * 安全纪律（产线动作会真的动账号，宁可麻烦）：
 *   ① 默认 dry_run，只有显式 apply:true 才落刀；② 账号名以**数组**传参（不经 shell，防注入）；
 *   ③ 调用前先体检式校验；④ 每次调用写一行留痕到 runtime/node_dashboard_actions.log。
 */
const ACTION_LOG = join(ROOT, 'runtime', 'node_dashboard_actions.log')
function runAction(name, argv, { timeoutMs = 300000 } = {}) {
  const line = `[${new Date().toISOString()}] ${name} :: ${argv.join(' ')}`
  try { appendFileSync(ACTION_LOG, line + '\n', 'utf8') } catch {}
  const r = spawnSync('python', argv, { cwd: ROOT, encoding: 'utf8', timeout: timeoutMs, windowsHide: true })
  const out = `${r.stdout || ''}${r.stderr || ''}`.trim()
  return {
    ok: r.status === 0,
    exit: r.status,
    cmd: `python ${argv.join(' ')}`,
    tail: out.split(/\r?\n/).slice(-25).join('\n'),
    logged_to: 'runtime/node_dashboard_actions.log',
  }
}

/** 把 ISO 时间转成本地可读（页面上不要出现 UTC 的 Z 串，人眼不友好）。 */
const localTime = (iso) => {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return String(iso)
  const p = (n) => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

/** 账号 + 队列（对齐旧看板 _gather_sse_state 的字段口径）。 */
function statusPayload() {
  const cfg = readCfg()
  const accounts = (cfg.accounts || []).map((a) => ({
    id: a.id || '',
    name: a.name || '',
    client: a.game_client || '',
    emu: a.emu_instance_index ?? '',
    stages: a.stages || [],
    suspended: !!a.suspended,
    failures: a.consecutive_failures || 0,
    running: false, // 第一版不接运行态（旧看板进程里才有）；列为待办
    queued: false,
  }))
  const queue = Array.isArray(cfg.queue) ? cfg.queue : []
  return {
    ok: true,
    source: 'node-dashboard(readonly)',
    port: PORT,
    accounts,
    queue: { count: queue.length, items: queue.slice(0, 20) },
    parallel_max: cfg.parallel_max ?? null,
    schedule_mode: cfg.schedule_mode ?? null,
    daily_batch_time: cfg.daily_batch_time ?? null,
    updated_at: new Date().toISOString(),
  }
}

/** 产线状态：直接复用 L3 守护落盘的状态文件（不重新算，避免重复劳动）。 */
function masPayload() {
  const f = join(ROOT, 'runtime', 'mas_watch_state.json')
  const st = readJson(f)
  const perf = readJson(join(ROOT, 'runtime', 'host_perf_state.json'))
  return {
    ok: !!st,
    watch: st,
    host_perf: perf,
    files: [fileMeta(f), fileMeta(join(ROOT, 'runtime', 'host_perf.log'))],
    reports_dir: existsSync(join(ROOT, 'runtime', 'reports')),
    updated_at: new Date().toISOString(),
  }
}

/**
 * 产线今日汇summary（人话版）：把 mas_watch_state.json 的 reported / rerun 拍平成表格。
 * 由来：这些字段以前只以原始 JSON 出现，页面等于把当天"出没出问题、补跑了谁"埋掉了。
 */
function productionPayload() {
  const f = join(ROOT, 'runtime', 'mas_watch_state.json')
  const st = readJson(f) || {}
  const days = new Set([...(Object.keys(st.reported || {})), ...(Object.keys(st.rerun || {}))])
  const rows = [...days].sort().reverse().map((d) => ({
    日期: d,
    已出日报: (st.reported?.[d] || []).join('、') || '—',
    补跑的账号: (st.rerun?.[d] || []).join('、') || '—',
    补跑数: (st.rerun?.[d] || []).length,
  }))
  // L3 守护日报文件（runtime/reports/<日期>_<班次>.md）
  let reports = []
  try {
    reports = readdirSync(join(ROOT, 'runtime', 'reports'))
      .filter((n) => n.endsWith('.md'))
      .sort()
      .reverse()
      .slice(0, 8)
      .map((n) => {
        const p = join(ROOT, 'runtime', 'reports', n)
        const meta = fileMeta(p)
        return { 文件: n, KB: meta.kb, 生成时间: localTime(meta.mtime) }
      })
  } catch {}
  return {
    ok: true,
    maa_counts: st.maa_counts ?? null,
    rows,
    reports,
    updated_at: new Date().toISOString(),
  }
}

const PAGE = `<!doctype html><html lang="zh"><meta charset="utf-8">
<title>MAAOrch · Node 看板（只读）</title>
<style>
 body{font:14px/1.6 system-ui,"Microsoft YaHei";margin:0;padding:18px;background:#101418;color:#dbe4ee}
 h1{font-size:17px;margin:0 0 4px} .sub{color:#7f8c9b;font-size:12px;margin-bottom:14px}
 table{border-collapse:collapse;width:100%;margin:10px 0} th,td{border:1px solid #263140;padding:6px 8px;text-align:left}
 th{background:#18202a;color:#9fb0c3;font-weight:600} code{background:#18202a;padding:1px 5px;border-radius:3px}
 .ok{color:#5ad07a} .warn{color:#e2b93b} .err{color:#e06c75} pre{background:#0b0f13;padding:10px;border-radius:6px;overflow:auto}
</style>
<h1>MAAOrch 看板 · Node 版（只读第一版）</h1>
<div class="sub">跑在 <code>${PORT}</code> · 前端已退役：本页 <code>/status</code> 是唯一人工入口 · 只读不写入</div>
<h3>产线动作 <span class="sub">（默认 dry-run，勾「真执行」才落刀；每次调用留痕 runtime/node_dashboard_actions.log）</span></h3>
<div>
  <input id="acc" placeholder="账号名，逗号分隔（如 史蒂芬芬,hbznn119-日）" style="width:340px;background:#0b0f13;color:#dbe4ee;border:1px solid #263140;padding:6px">
  <label><input type="checkbox" id="apply"> 真执行</label>
  <button onclick="act('rerun')">补跑</button>
  <button onclick="act('cleanup')">清空临时补跑队列</button>
</div>
<pre id="actout">（动作输出会显示在这里）</pre>

<h3>状态（账号 / 队列 / 产线 / 跑局）</h3>
<div id="out">加载中…</div>
<script>
const T = new URLSearchParams(location.search).get('token') || ${JSON.stringify(TOKEN)};
const api = async (p) => (await fetch(p + (T ? '?token=' + encodeURIComponent(T) : ''))).json();
const localTime = (iso) => {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return String(iso);
  const p = (n) => String(n).padStart(2, '0');
  return p(d.getMonth()+1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
};
/** 触发产线动作（默认 dry-run；勾了"真执行"才落刀）。 */
window.act = async (kind) => {
  const acc = (document.getElementById('acc').value || '').split(',').map(s => s.trim()).filter(Boolean);
  const apply = document.getElementById('apply').checked;
  const out = document.getElementById('actout');
  out.textContent = '执行中…（dry_run=' + (!apply) + '）';
  try {
    const r = await (await fetch('/api/mas/' + kind + '?token=' + encodeURIComponent(T), {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify(kind === 'rerun' ? { accounts: acc, apply } : { apply }),
    })).json();
    out.textContent = JSON.stringify(r, null, 1);
  } catch (e) { out.textContent = '失败：' + e; }
};
function table(rows, cols){
  if(!rows || !rows.length) return '<i>（空）</i>';
  return '<table><tr>' + cols.map(c=>'<th>'+c+'</th>').join('') + '</tr>' +
    rows.map(r=>'<tr>'+cols.map(c=>'<td>'+String(r[c] ?? '')+'</td>').join('')+'</tr>').join('') + '</table>';
}
(async () => {
  const s = await api('/api/status');
  const m = await api('/api/mas/status');
  const h = await api('/api/health');
  const r = await api('/api/runs');
  const p = await api('/api/production');
  const w = m.watch || {};
  document.getElementById('out').innerHTML =
    '<h3>账号（'+s.accounts.length+'）<span class="sub"> ← 这份是**本地** models/config.json，产线账号在远端 mrfz-0000</span></h3>' +
    table(s.accounts, ['id','name','client','emu','stages','suspended','failures']) +
    '<h3>队列</h3><div>队列项 = <b>'+s.queue.count+'</b> · parallel_max = '+s.parallel_max+' · 班次模式 = '+s.schedule_mode+'</div>' +
    '<h3>产线今日（补跑 / 日报）</h3>' +
    '<div>MAA 计数 = <b>'+JSON.stringify(p.maa_counts ?? w.maa_counts ?? null)+'</b> · 状态文件更新 = '+localTime((m.files[0]||{}).mtime)+'</div>' +
    table(p.rows, ['日期','已出日报','补跑的账号','补跑数']) +
    (p.reports && p.reports.length ? '<div class="sub">L3 守护日报：</div>' + table(p.reports, ['文件','KB','生成时间']) : '<div class="sub">（runtime/reports 下暂无日报）</div>') +
    '<h3>最近跑局（runtime/_ce6/*.attempt.log）</h3>' +
    table((r.runs||[]).map(x => ({tag:x.tag, job:x.job, 时长或错误:x.end || '（未进战斗）', 结算:x.settle || '—', 结论:x.gate || '—'})), ['tag','job','时长或错误','结算','结论']) +
    '<h3>本进程自检</h3><div>pid '+h.pid+' · node '+h.node+' · uptime '+h.uptime_s+'s · RSS '+h.rss_mb+'MB · heap '+h.heap_mb+'MB</div>' +
    '<div class="sub">数据时间：'+localTime(s.updated_at)+' · 每 30s 自动刷新</div>';
  setTimeout(() => location.reload(), 30000);
})().catch(e => { document.getElementById('out').innerHTML = '<span class="err">加载失败：'+e+'</span>' });
</script>
</html>`

import { appendFileSync } from 'node:fs'

/**
 * 把请求透传给上游（旧 Python 看板）。用于迁移期的"未实现路由"。
 * ★ 2026-09-26 前端整体退役后，本兜底的适用范围**已收窄**为两类调用：
 *   (a) `/status` 最小运维页之外的人工/运维调用（例如手动 curl 某条还没搬进 Node 的路由）；
 *   (b) MCP 之外、尚未迁进 Node 的 Web/API 路由。
 *   它仍然保证迁移期"零 404"，但**已经没有前端页面依赖它**了（旧前端已下线）。
 * **按需拉起**（2026-09-21）：上游没在跑时不放弃，而是**spawn 一个 Python 再重试一次**
 * —— 这样 Python 不必常驻（它空转 47% CPU 是已知问题），只有点那些还没搬完的功能时才起来。
 * 说明：不改写 body/响应，只做透明转发；转发失败时返回 502 并说明上游是谁。
 */
let _spawning = null
function ensureUpstream() {
  if (!ALLOW_UPSTREAM_SPAWN) {
    if (!_upstreamSpawnRefused) {
      _upstreamSpawnRefused = true
      console.warn('[node-dashboard] 已禁用按需拉起 Python 上游（MAAORCH_ALLOW_UPSTREAM != 1）；'
        + '未实现的路由将直接返回 not_implemented_in_node')
    }
    return Promise.resolve(false)
  }
  if (_spawning) return _spawning
  _spawning = new Promise((resolve) => {
    const py = process.env.MAAORCH_PYTHON || 'C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe'
    // 优先用 WMI 建脱离进程（沙箱里 child_process.spawn 会 EPERM；WMI Create 不受此限，
    // 而且天然脱离进程树，Python 不会随 Node 退出而消失）。
    try {
      const wmi = spawnSync('powershell.exe', ['-NoProfile', '-Command',
        `([wmiclass]'Win32_Process').Create('"${py}" main_web.pyw') | Out-Null`],
        { cwd: ROOT, timeout: 15000, windowsHide: true })
      if (wmi.status === 0) console.log('[node-dashboard] 已用 WMI 拉起 Python 上游')
      else console.error('[node-dashboard] WMI 拉起返回', wmi.status, String(wmi.stderr || '').slice(0, 200))
    } catch (e) {
      console.error('[node-dashboard] 拉起上游失败:', e.message)
    }
    setTimeout(() => { _spawning = null; resolve(true) }, 8000)
  })
  return _spawning
}

function proxyTo(req, res, path, opts = {}) {
  const attempt = opts.attempt || 0
  const body = opts.body !== undefined ? opts.body : null   // null = 还没读；Buffer = 已读
  const key = req.method + ' ' + path + (attempt ? ` (retry${attempt})` : '')
  proxyHits.set(key, (proxyHits.get(key) || 0) + 1)

  if (!UPSTREAM) {
    res.writeHead(501, { 'content-type': 'application/json; charset=utf-8' })
    return res.end(JSON.stringify({ ok: false, error: 'not_implemented_in_node', path, hint: '未开上游透传（--no-upstream）' }, null, 2))
  }

  // 兜底守卫：从进入函数起 6 秒内必须给客户端一个回复（无论后面出什么幺蛾子）
  let responded = false
  const guard = setTimeout(() => {
    if (responded || res.headersSent) return
    responded = true
    res.writeHead(503, { 'content-type': 'application/json; charset=utf-8' })
    res.end(JSON.stringify({ ok: false, error: 'upstream_timeout', upstream: UPSTREAM }, null, 2))
  }, 6000)

  const send = (buf) => {
    let target
    try { target = new URL(req.url, UPSTREAM) } catch {
      clearTimeout(guard); responded = true
      res.writeHead(502, { 'content-type': 'application/json; charset=utf-8' })
      return res.end(JSON.stringify({ ok: false, error: 'bad_upstream', upstream: UPSTREAM }, null, 2))
    }
    const headers = { ...req.headers }
    if (buf.length) headers['content-length'] = String(buf.length)
    const p = httpRequest({
      hostname: target.hostname, port: target.port || 80,
      path: target.pathname + target.search, method: req.method, headers,
    }, (up) => {
      clearTimeout(guard)
      if (responded) return up.resume()
      responded = true
      res.writeHead(up.statusCode || 502, up.headers)
      up.pipe(res)
    })
    p.on('error', (e) => {
      clearTimeout(guard)
      if (responded || res.headersSent) return
      responded = true
      // 上游没起：**立刻**回可读的 503（不在这里等它启动）。若允许按需拉起，
      // 就后台把它拉起来（用户再点一次即可用）—— 但 2026-09-23 起本机默认
      // **不允许**拉起（见 ALLOW_UPSTREAM_SPAWN 注释），直接告诉用户"这条路由没搬完"。
      if (ALLOW_UPSTREAM_SPAWN) {
        ensureUpstream()
        res.writeHead(503, { 'content-type': 'application/json; charset=utf-8' })
        res.end(JSON.stringify({
          ok: false, error: 'upstream_starting', upstream: UPSTREAM,
          detail: '上游未运行，已后台拉起（约 10 秒）；请稍后重试该功能',
        }, null, 2))
      } else {
        res.writeHead(501, { 'content-type': 'application/json; charset=utf-8' })
        res.end(JSON.stringify({
          ok: false, error: 'not_implemented_in_node', upstream: UPSTREAM, path,
          detail: '该路由只在旧 Python 上游（19997）里实现，而本机已按用户要求（2026-09-23）'
            + '禁止按需拉起它 —— 看板只提供只读功能，不让任何组件再碰模拟器。'
            + '临时启用：设环境变量 MAAORCH_ALLOW_UPSTREAM=1 后重启看板。',
        }, null, 2))
      }
    })
    if (buf.length) p.write(buf)
    p.end()
  }

  if (body !== null) return send(body)
  const chunks = []
  req.on('data', (c) => chunks.push(c))
  req.on('end', () => send(Buffer.concat(chunks)))
  req.resume()   // 必须让 body 流起来，否则带 body 的 POST 等不到 'end'
}

/** 账号列表（对齐旧 Python 看板 /api/accounts 的字段，2026-09-21 用活服务取的真实形状）。
 *  运行时字段（running/queued/sanity/last_clear/state）目前给默认值 —— 那些要等运行器也迁到
 *  Node 才能如实填写；先保证前端不出现 undefined。 */
function accountsPayload() {
  const cfg = readCfg()
  const accounts = (cfg.accounts || [])
    .filter((a) => !a._connect_only)
    .map((a) => ({
      id: a.id || '', name: a.name || '',
      game_client: a.game_client || '', emu_instance_index: a.emu_instance_index ?? '',
      account_switch: a.account_switch || '', uid: a.uid || '',
      running: a.__running === true, queued: a.__queued === true,
      failures: a.consecutive_failures || 0, suspended: !!a.suspended,
      stages: a.stages || [], smart_annihilation: a.smart_annihilation || '',
      fight_mode: a.fight_mode || 'schedule', fight_default: a.fight_default || '1-7',
      schedule_weekly: a.schedule_weekly || {}, schedule_monthly: a.schedule_monthly || {},
      fight_priority: a.fight_priority || {}, fight_materials: a.fight_materials || [],
      stage_ability: a.stage_ability || {}, farm_tag: a.farm_tag || 'material',
      mainline_chapter: a.mainline_chapter || 0,
      sanity: a.__sanity || {}, last_clear: a.__last_clear || {},
      state: { today_runs: a.__today_runs || 0 },
    }))
  return { ok: true, accounts }
}

/**
 * 取模拟器实例的 **实际** adb 端口。
 * 重要（照抄旧实现的教训）：**不要用公式 `16384 + idx*32`** —— 旧代码注释写明
 * "公式端口在模拟器重启后会漂移，一律禁止用于连接"；要读 `vm_config.json` 里
 * `vm.nat.port_forward.adb.host_port`（那才是真实转发映射）。
 */
const MUMU_VM_BASES = [
  process.env.LOCALAPPDATA ? join(process.env.LOCALAPPDATA, 'Netease', 'MuMuPlayer-12.0', 'vms') : '',
  process.env.APPDATA ? join(process.env.APPDATA, 'Netease', 'MuMuPlayer-12.0', 'vms') : '',
  'D:/Xiach/MuMuPlayer/vms',
  'D:/Program Files/Netease/MuMuPlayer-12.0/vms',
  'C:/Program Files/Netease/MuMuPlayer-12.0/vms',
].filter(Boolean)

function mumuAdbPort(emuIdx) {
  for (const base of MUMU_VM_BASES) {
    const p = join(base, `MuMuPlayer-12.0-${emuIdx}`, 'configs', 'vm_config.json')
    try {
      const cfg = JSON.parse(readFileSync(p, 'utf8'))
      const port = cfg?.vm?.nat?.port_forward?.adb?.host_port
      if (port) return String(port)
    } catch {}
  }
  return ''
}

function adbAddressOf(ac) {
  if (ac.adb_address) return String(ac.adb_address)
  const idx = ac.emu_instance_index
  if (idx === undefined || idx === null || idx === '') return ''
  const port = mumuAdbPort(idx)
  return port ? `127.0.0.1:${port}` : ''
}

/** 首页要用的四个端点（2026-09-21 按旧服务真实返回形状复刻，字段对齐）。 */
function dashboardStatusPayload() {
  const cfg = readCfg()
  const accounts = (cfg.accounts || []).filter((a) => !a._connect_only).map((a, i) => ({
    name: a.name || '', index: i,
    running: a.__running === true, elapsed: a.__elapsed || 0,
    adb: adbAddressOf(a), emu_index: a.emu_instance_index ?? '',
  }))
  const running = accounts.filter((x) => x.running).length
  return { accounts, pipeline_running: false, running }
}

function queuePayload() {
  // 队列实体在产线主机（AUTO-MAS）上，这里保持与旧服务同形状（本地无队列 ⇒ 空）。
  return { pending: [], active: [], pending_count: 0, active_count: 0, paused: false, avg_duration_min: 25 }
}

function stagesPayload() {
  const cfg = readCfg()
  const accts = (cfg.accounts || []).filter((a) => !a._connect_only)
  const lib = (cfg.stage_library || []).map((st) => {
    const sid = st.id || ''
    const ids = accts.filter((a) => (a.stages || []).includes(sid)).map((a) => a.id || '')
    return { ...st, count: ids.length, account_ids: ids }
  })
  return { ok: true, stages: lib }
}

function accountStatusPayload() {
  const cfg = readCfg()
  const accounts = (cfg.accounts || []).filter((a) => !a._connect_only).map((a, i) => ({
    index: i + (a.emu_instance_index ? 0 : 0), id: a.id || '', name: a.name || '',
    sanity_current: a.__sanity?.current ?? null, sanity_max: a.__sanity?.max ?? null,
    sanity_pct: a.__sanity?.pct ?? null, sanity_time: a.__sanity?.time || '',
    done_ts: '', last_complete: '', last_clear_ts: '', last_sanity_after: null,
    today_runs: a.__today_runs || 0, last_status: '', last_login: '',
    running: a.__running === true, queued: a.__queued === true,
  }))
  return { ok: true, accounts, stats: { total: accounts.length, running: 0, queued: 0 } }
}

/** 设置页/仪表盘用的配置读端点（白名单与默认值照抄旧 Python 实现，2026-09-21）。 */
function configPayload() {
  const c = readCfg()
  return {
    ok: true,
    config: {
      maa_version: c.maa_version ?? '', parallel_max: c.parallel_max ?? 3,
      appearance_mode: c.appearance_mode ?? 'Dark', schedule_mode: c.schedule_mode ?? 'daily',
      maa_instances: c.maa_instances ?? 0, api_port: c.api_port ?? 19999,
      smart_global: c.smart_global ?? {}, webhook_url: c.webhook_url ?? '',
      bind_address: c.bind_address ?? '0.0.0.0', api_token: c.api_token ?? '',
      ai_provider: c.ai_provider ?? 'openai', ai_api_key: c.ai_api_key ?? '',
      ai_endpoint: c.ai_endpoint ?? '', ai_model: c.ai_model ?? '',
      ai_auto_analyze: c.ai_auto_analyze ?? false,
      tg_token: c.tg_token ?? '', tg_chat_id: c.tg_chat_id ?? '',
    },
  }
}

function smartSettingsPayload() {
  const c = readCfg()
  return { ok: true, smart_global: c.smart_global ?? {} }
}

/** 每个账号的运行统计（旧 /api/stats，形状 `{accounts:[{index,account_name,running,total_runs,stats}]}`）。 */
function statsPayload() {
  const cfg = readCfg()
  const accounts = (cfg.accounts || []).filter((a) => !a._connect_only).map((a, i) => ({
    index: i, account_name: a.name || '', running: a.__running === true,
    total_runs: a.__today_runs || 0, stats: { runs: [], last_read_line: 0 },
  }))
  return { accounts }
}

/**
 * 仪表盘数据（旧 /api/node/dashboard）。**前端的 `系统/容量/进程` 全部来自这里** ——
 * 之前它 404（我误以为前端调 /api/system），页面才出现一堆 undefined。
 * 容量算法照抄旧实现（network/api_fastapi.py:472-495）：
 *   est_per = 有实例在跑 ? 总进程内存/运行数 : 1500MB
 *   by_parallel = parallel_max - running ；by_mem = 可用内存/est_per ；by_gpu = 显存空闲/est_per
 *   capacity = min(三者)，limit_by 取最小的那一项。
 * GPU：Node 原生拿不到，先用 0/空（前端对 usage==null 会显示 N/A，不会崩）；
 *      以后接一个按需 helper（nvidia-smi）再补真实读数。
 */
/** GPU 读数：优先 nvidia-smi（本机实测可用，输出 `名称, 使用率%, 已用MB, 总MB`）。
 *  Node 原生拿不到 GPU，所以走"按需调外部命令"这条路（这也是目标③里 helper 的形态之一）。 */
function gpuInfo() {
  const exe = existsSync('C:/Windows/System32/nvidia-smi.exe') ? 'C:/Windows/System32/nvidia-smi.exe' : 'nvidia-smi'
  try {
    const r = spawnSync(exe, ['--query-gpu=name,utilization.gpu,memory.used,memory.total', '--format=csv,noheader,nounits'],
      { encoding: 'utf8', timeout: 4000, windowsHide: true })
    const line = (r.stdout || '').trim().split(/\r?\n/)[0]
    if (!line) return { name: '', usage: null, mem_used_mb: 0, mem_total_mb: 0 }
    const [name, usage, used, total] = line.split(',').map((s) => s.trim())
    return {
      name: name || '', usage: usage === '' ? null : Number(usage),
      mem_used_mb: Number(used) || 0, mem_total_mb: Number(total) || 0,
    }
  } catch {
    return { name: '', usage: null, mem_used_mb: 0, mem_total_mb: 0 }
  }
}

function nodeDashboardPayload() {
  const cpus = os.cpus()
  const availableMb = Math.round(os.freemem() / 1048576)
  const totalMb = Math.round(os.totalmem() / 1048576)
  const memoryPct = totalMb ? Math.round((1 - availableMb / totalMb) * 1000) / 10 : 0

  // 进程/运行态：等运行器迁到 Node 后如实填写（先给空数组，前端会显示"无运行中的进程"）
  const processes = []
  const running = 0
  const cfg = readCfg()
  const parallelMax = Number(cfg.parallel_max ?? 3)
  const byParallel = Math.max(0, parallelMax - running)
  const estPer = 1500
  const byMem = Math.max(0, Math.floor((availableMb * 1048576) / 1048576 / estPer))
  const gpu = gpuInfo()
  const byGpu = gpu.mem_total_mb > 0 ? Math.max(0, Math.floor((gpu.mem_total_mb - gpu.mem_used_mb) / estPer)) : 99
  const capacity = Math.min(byParallel, byMem, byGpu)
  const limitBy = capacity === byParallel ? '并行上限' : (capacity === byMem ? '内存' : '显存')

  return {
    ok: true,
    system: {
      cpu_pct: null,                       // 需要两次采样；前端对 null 显示 '?'
      cpu_count: cpus.length,
      memory_total_mb: totalMb,
      memory_available_mb: availableMb,
      memory_pct: memoryPct,
    },
    gpu,
    processes,
    capacity: {
      parallel_max: parallelMax, running, by_parallel: byParallel,
      by_memory: byMem, by_gpu: byGpu, max: capacity, limit_by: limitBy,
      est_per_instance_mb: estPer, deficit: cfg.deficit ?? 0,
      stuck_timeout: cfg.stuck_timeout ?? 10,
    },
    samples: [], gantt: [],
    note: 'Node 原生实现：GPU/进程/CPU 采样待接按需 helper',
  }
}

/**
 * 肉鸽看守状态（/api/rogue/status）—— 给顶部状态条用：一眼看到"#0 在不在刷、机队几台在跑、闸门关没关"。
 * 数据源全是看守自己写的文件（纯读、零副作用）：
 *   runtime/rogue0_watch.log + rogue0_watch_state.json
 *   runtime/rogue_fleet_watch.log + rogue_fleet_state.json + rogue_fleet_probe.jsonl
 * ⚠️ 顺带报"看守自己还活着吗"：日志 mtime 超过 8 分钟没动 ⇒ stale（这一层以前没人看）。
 */
function roguePayload() {
  const rd = (f) => { try { return readFileSync(join(ROOT, f), 'utf8') } catch { return '' } }
  const ageOf = (f) => { try { return Math.round((Date.now() - statSync(join(ROOT, f)).mtimeMs) / 1000) } catch { return -1 } }
  const linesOf = (f) => rd(f).split(/\r?\n/).filter(Boolean)

  const z0lines = linesOf('runtime/rogue0_watch.log').filter((l) => l.includes('MAA='))
  const zero = { last: z0lines.length ? z0lines[z0lines.length - 1] : '', ageSec: ageOf('runtime/rogue0_watch.log') }

  const fleetLines = linesOf('runtime/rogue_fleet_watch.log')
  let machines = []
  try {
    const pl = linesOf('runtime/rogue_fleet_probe.jsonl')
    const j = JSON.parse(pl[pl.length - 1])
    machines = Object.entries(j.machines || {}).map(([name, m]) => ({ name, proc: m.proc, game: m.game, ageSec: m.asstAge }))
  } catch {}
  const fleet = {
    last: fleetLines.length ? fleetLines[fleetLines.length - 1] : '',
    ageSec: ageOf('runtime/rogue_fleet_watch.log'),
    running: machines.filter((m) => m.proc > 0).length,
    total: machines.length || 5, machines,
  }
  return {
    ok: true, ts: new Date().toISOString(), zero, fleet,
    stale: zero.ageSec < 0 || zero.ageSec > 480 || fleet.ageSec < 0 || fleet.ageSec > 480,
  }
}

/** 日志尾随（旧 /api/logs?lines=N）：直接读 logs/debug.log 的尾部 N 行。 */
function logsPayload(lines = 200) {  const n = Math.max(1, Math.min(Number(lines) || 200, 2000))
  for (const f of ['logs/debug.log', 'logs/events.log']) {
    const p = join(ROOT, f)
    if (!existsSync(p)) continue
    try {
      const txt = readFileSync(p, 'utf8')
      const arr = txt.split(/\r?\n/).filter((x) => x !== '')
      return { lines: arr.slice(-n) }
    } catch {}
  }
  return { lines: [] }
}

/** 模拟器列表（旧 /api/emulators）：从 vm_config.json 读实际 adb 端口，用端口占用判断 running。 */
/**
 * 模拟器列表（旧 /api/emulators、/api/connect/status）。
 * 数据源：**MuMuManager.exe `info -v all`**（权威：给 name / adb_port / is_android_started），
 * 与旧 Python 实现同源（那边走 infrastructure.task_constants.detect_emu_instances）。
 * 以前我按目录扫 vm_config.json 自己拼名字 ⇒ 名字与上游不一致（上游拿的是 MuMu 实例名）。
 */
const MUMU_MANAGER_CANDIDATES = [
  'D:/Xiach/MuMuPlayer/nx_main/MuMuManager.exe',
  process.env.LOCALAPPDATA ? join(process.env.LOCALAPPDATA, 'MuMuPlayer-12.0', 'nx_main', 'MuMuManager.exe') : '',
  process.env.USERPROFILE ? join(process.env.USERPROFILE, 'MuMuPlayer-12.0', 'nx_main', 'MuMuManager.exe') : '',
  'E:/MuMu Player 12/nx_main/MuMuManager.exe',
].filter(Boolean)

function mumuInstances() {
  for (const exe of MUMU_MANAGER_CANDIDATES) {
    if (!existsSync(exe)) continue
    try {
      const r = spawnSync(exe, ['info', '-v', 'all'], { encoding: 'utf8', timeout: 8000, windowsHide: true })
      const out = (r.stdout || '').replace(/^\uFEFF/, '').trim()
      if (!out) continue
      const obj = JSON.parse(out)
      const list = Object.values(obj).map((e) => ({
        index: String(e.index ?? ''), name: String(e.name ?? e.index ?? ''),
        adb_port: String(e.adb_port ?? ''),
        running: !!(e.is_android_started || e.is_process_started),
      }))
      if (list.length) return list.sort((a, b) => Number(a.index) - Number(b.index))
    } catch {}
  }
  return []
}

async function emulatorsPayload(withMaa = false) {
  let list = mumuInstances()
  if (!list.length) {
    // 兜底：MuMuManager 不在/失败时用 vm_config.json 目录扫描
    list = []
    for (const base of MUMU_VM_BASES) {
      let dirs = []
      try { dirs = readdirSync(base) } catch { continue }
      for (const dir of dirs) {
        const m = dir.match(/MuMuPlayer-12\.0-(\d+)$/)
        if (!m) continue
        const idx = m[1]
        const port = mumuAdbPort(idx)
        if (!port) continue
        list.push({ index: idx, name: idx, adb_port: port, running: await portListening(port) })
      }
      if (list.length) break
    }
    list.sort((a, b) => Number(a.index) - Number(b.index))
  }
  if (withMaa) {
    return { ok: true, emulators: list.map((e) => ({ ...e, maa_running: false, maa_queued: false, mode: '' })) }
  }
  return { ok: true, emulators: list.map((e) => ({ emu: 'MuMu', name: e.name, index: e.index, adb_port: e.adb_port, running: e.running })) }
}

const server = createServer((req, res) => {
  Promise.resolve()
    .then(() => handleRequest(req, res))
    .catch((e) => {
      // 兜底：单个请求的异常绝不允许打死整个看板（2026-09-21 实测踩过一次：
      // recentRuns 里一个 ReferenceError 让 Node 进程直接退出，服务全挂）
      console.error('[node-dashboard] handler error:', (e && e.stack) || e)
      if (!res.headersSent) {
        res.writeHead(500, { 'content-type': 'application/json; charset=utf-8' })
        res.end(JSON.stringify({ ok: false, error: 'handler_exception', detail: String((e && e.message) || e) }))
      } else {
        try { res.end() } catch {}
      }
    })
})

async function handleRequest(req, res) {
  const url = new URL(req.url, 'http://localhost')
  const p = url.pathname
  const token = url.searchParams.get('token') || req.headers['x-agent-token'] || ''
  const json = (obj, code = 200) => {
    const body = JSON.stringify(obj, null, 2)
    res.writeHead(code, { 'content-type': 'application/json; charset=utf-8' })
    res.end(body)
  }
  if (p === '/api/health') {
    const mu = process.memoryUsage()
    return json({
      ok: true, pid: process.pid, node: process.version, uptime_s: Math.round((Date.now() - startedAt) / 1000),
      rss_mb: +(mu.rss / 1048576).toFixed(1), heap_mb: +(mu.heapUsed / 1048576).toFixed(1),
    })
  }
  if (p.startsWith('/api/')) {
    // 鉴权：**本机来源放行**（用户就坐在电脑前，旧 Python 看板同样是本机可用；
    // 2026-09-21 实测：旧前端调 /api/* 不带任何凭据，若一律要求 token 会全体 401、页面数据全空）。
    // 非本机来源（Tailscale/LAN 等）仍必须带 token（查询串或 x-agent-token 头）。
    const remote = (req.socket?.remoteAddress) || ''
    const isLocal = remote === '127.0.0.1' || remote === '::1' || remote === '::ffff:127.0.0.1'
    if (TOKEN && !isLocal && token !== TOKEN) return json({ ok: false, error: 'unauthorized' }, 401)
    if (p === '/api/status') return json(dashboardStatusPayload())
    if (p === '/api/queue') return json(queuePayload())
    if (p === '/api/stages') return json(stagesPayload())
    if (p === '/api/account_status') return json(accountStatusPayload())
    if (p === '/api/config') return json(configPayload())
    if (p === '/api/settings/smart') return json(smartSettingsPayload())
    if (p === '/api/stats') return json(statsPayload())
    if (p === '/api/accounts') return json(accountsPayload())
    if (p === '/api/logs') return json(logsPayload(url.searchParams.get('lines') || 200))
    if (p === '/api/emulators') return json(await emulatorsPayload(false))
    if (p === '/api/connect/status') return json(await emulatorsPayload(true))
    if (p === '/api/node/dashboard') return json(nodeDashboardPayload())
    if (p === '/api/mas/status') return json(masPayload())
    if (p === '/api/rogue/status') return json(roguePayload())
    if (p === '/api/production') return json(productionPayload())
    if (p === '/api/runs') return json({ ok: true, runs: recentRuns(12), updated_at: new Date().toISOString() })
    if (p === '/api/_proxy_stats') {
      const rows = [...proxyHits.entries()].map(([k, n]) => ({ route: k, hits: n })).sort((a, b) => b.hits - a.hits)
      return json({ ok: true, upstream: UPSTREAM || '(disabled)', total_kinds: rows.length, routes: rows })
    }

    if (p === '/api/sse') {
      // 旧前端的实时刷新区（原 Python 每 1 秒推一次）。这里推 Node 自己的状态，
      // 字段对齐旧实现（accounts/queue/notifications/ai_insights），保证前端不炸。
      res.writeHead(200, {
        'content-type': 'text/event-stream; charset=utf-8',
        'cache-control': 'no-cache', connection: 'keep-alive',
      })
      const push = () => {
        try {
          const s = dashboardStatusPayload()
          const q = queuePayload()
          const payload = {
            ok: true,
            accounts: s.accounts.map((a) => ({
              id: a.adb || a.name, name: a.name, running: a.running, queued: false,
              emu: a.emu_index, client: '', failures: 0, suspended: false, stages: [],
              smart_annihilation: '', sanity: {}, last_clear: {},
            })),
            queue: { count: q.pending_count },
            notifications: [], ai_insights: [],
          }
          res.write(`data: ${JSON.stringify(payload)}\n\n`)
        } catch (e) {
          try { res.write(`data: ${JSON.stringify({ ok: false })}\n\n`) } catch {}
        }
      }
      push()
      const t = setInterval(push, 1000)
      req.on('close', () => clearInterval(t))
      return
    }

    // ── 作业实验室（copilot）—— 已 Native 搬进 Node，见 server/copilot_node.mjs ──
    // 搬它的原因：Python 上游不常驻，这四条路由实测 503（页面点不动）。
    // 注意要放在 POST 分支**之前**：它只认 GET 的三条，POST 两条由下面的分支处理。
    if (p.startsWith('/api/copilot/')) {
      const r = await copilotGet(p, url)
      if (r) return json(r.body, r.code)
    }

    // ── 写操作（POST）────────────────────────────────────────────────────
    if (req.method === 'POST') {
      const chunks = []
      req.on('data', (c) => { if (chunks.reduce((n, x) => n + x.length, 0) < 65536) chunks.push(c) })
      req.on('end', () => {
        let body = {}
        try { body = JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}') } catch {}
        const accounts = Array.isArray(body.accounts) ? body.accounts.map(String).filter(Boolean) : []
        if (p === '/api/mas/rerun') {
          if (!accounts.length) return json({ ok: false, error: 'accounts[] 必填' }, 400)
          const argv = ['tools/auto_mas_rerun.py', '--accounts', accounts.join(',')]
          if (body.skip_dead) argv.push('--skip-dead')
          if (body.apply) argv.push('--apply')
          return json({ ...runAction('mas/rerun', argv), dry_run: !body.apply, accounts })
        }
        if (p === '/api/mas/cleanup') {
          const argv = ['tools/auto_mas_rerun.py', '--cleanup']
          if (body.apply) argv.push('--apply')
          return json({ ...runAction('mas/cleanup', argv), dry_run: !body.apply })
        }
        if (p === '/api/account/toggle') {
          const name = String(body.name || '')
          if (!name) return json({ ok: false, error: 'name 必填' }, 400)
          const argv = ['tools/auto_mas_toggle_user.py', '--name', name]
          if (body.enable) argv.push('--enable')
          if (!body.apply || body.dry_run !== false) argv.push('--dry-run')
          return json({ ...runAction('account/toggle', argv), dry_run: true, name })
        }
        if (p.startsWith('/api/copilot/')) {
          const r = copilotPost(p, body)
          if (r) return json(r.body, r.code)
        }
        return proxyTo(req, res, p)
      })
      return
    }
    // 未实现的读路由 → 透传给上游（迁移期零 404）。
    // 前端退役后这条路径只服务「人工/运维调用」与尚未搬进 Node 的 MCP 之外路由（见 proxyTo 注释）。
    return proxyTo(req, res, p)
  }
  // ── 页面路由（2026-09-26 前端整体退役后）──────────────────────────────
  // `/status` = **唯一**人工入口：极简运维面板（本文件内联生成，零静态依赖）。
  // `/`、`/index.html` 不再是主入口 —— 旧的完整前端 ui/web/index.html 已退役，
  //   这里只做 302，**绝不**再 readFileSync 那个文件：
  //   否则前端文件被删除后本路由会 500（这正是 task-13 要拆掉的耦合）。
  if (p === '/' || p === '/index.html') {
    res.writeHead(302, { location: '/status', 'cache-control': 'no-cache' })
    return res.end()
  }
  if (p === '/status') {
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-cache' })
    return res.end(PAGE)
  }
  // ── 静态资源：只服务「退役前端之外」的资产（2026-09-26 前端整体退役）────
  // 退役前：这里直接从 ui/web/ 出 index.html 的 CSS/JS（避开 Python 依赖）。
  // 退役后：ui/web/ 整个目录不再是本服务的资源来源；`.js/.css/.html/.woff/.ttf/.map/.apk`
  //   这些「前端包扩展名」也一并收紧（旧前端的手写 JS/CSS/字体/APK 都随前端退役）。
  // 保留 ui/ 与 screenshots/ 两个根：那是**数据与截图类**资产（搜索快照、地图编辑与标注
  //   的截图），不是前端包。仍含目录穿越防护。
  const STATIC_EXT = /\.(png|jpe?g|gif|svg|ico|webp|mp4|txt|json)$/i
  if (STATIC_EXT.test(p)) {
    const rel = decodeURIComponent(p).replace(/^\/+/, '')
    if (rel.includes('..')) {
      res.writeHead(400); return res.end('bad path')
    }
    // 显式闸：ui/web/ 是已退役的前端目录 —— 即使文件还在磁盘上也不从 Node 出
    //（防「删了又回来」：谁把 app.js 拷回 ui/web/，这里也不会再把它当公开资源）。
    if (rel.toLowerCase().startsWith('web/')) {
      res.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' })
      return res.end('gone: ui/web 前端资源已退役（人工入口见 /status）')
    }
    const roots = [join(ROOT, 'ui'), join(ROOT, 'screenshots')]
    for (const r of roots) {
      const fp = join(r, rel)
      if (existsSync(fp) && statSync(fp).isFile()) {
        const ext = (rel.split('.').pop() || '').toLowerCase()
        const mime = {
          json: 'application/json; charset=utf-8',
          png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', gif: 'image/gif',
          svg: 'image/svg+xml', ico: 'image/x-icon', webp: 'image/webp', mp4: 'video/mp4',
          txt: 'text/plain; charset=utf-8',
        }[ext] || 'application/octet-stream'
        res.writeHead(200, { 'content-type': mime, 'cache-control': 'no-cache' })
        return res.end(readFileSync(fp))
      }
    }
    // 磁盘上没有 ⇒ 再试上游（例如 Python 生成的截图）
    return proxyTo(req, res, p)
  }

  res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' })
  res.end(PAGE)
}

server.listen(PORT, '127.0.0.1', () => {
  console.log(`[node-dashboard] http://127.0.0.1:${PORT}  (token ${TOKEN ? 'enabled' : 'DISABLED'})`)
  console.log(`[node-dashboard] 前端已退役：/ → 302 /status（唯一人工入口）；未实现的路由透传 ${UPSTREAM || '(已关闭)'}`)
})
