/**
 * 作业实验室（自动抄作业）—— **Node 版后端**（2026-09-22 从 network/copilot_api.py 搬过来）
 *
 * 为什么搬:
 *     Python 上游（19997）已不常驻，而门面只实现了 21+ 个端点、其余透传给上游
 *     ⇒ `/api/copilot/*` 实测 503（Node 没实现 + 上游没起），「🧪 作业实验室」页面点不动。
 *     这四个路由本身不依赖 FastAPI 的任何东西，只是"起个瘦 python 助手 + 读产物"，
 *     所以直接落在 Node 里，页面从此不依赖常驻 Python（符合"Python 不常驻"的既定方向）。
 *
 * 被搬的四条路由（契约与旧 Python 一致，前端 ui/web/copilot_lab.js 不用改）:
 *     GET  /api/copilot/screen?stage=act42side_ex06[&top=6][&refresh=1]
 *     POST /api/copilot/fallback          {stage|codes, emu, max_tries, support, potion}
 *     GET  /api/copilot/fallback/status
 *     POST /api/copilot/fallback/stop
 *
 * 分工（谁是权威）:
 *     · 筛作业 = `tools/prts_job_finder.py`（访问 prts 作业站 + 对干员箱求差集）—— 仍用 python，
 *       按需起、跑完即走，不做常驻。
 *     · 降级重试 = `tools/maa_job_fallback.py`（拉作业正文 → 交给 MaaCore 跑 → 判 verdict → 核三星）。
 *     · 本模块只管：起进程、读落盘产物、把状态回给页面。**不自己实现业务判定**。
 *
 * 已知契约缺口（保持与旧实现一致，不偷偷改行为）:
 *     `potion` 参数收下但无处可传 —— `maa_job_fallback.py` 只有 `--potion`（默认就是 True），
 *     没有关闭开关，所以前端取消勾选"理智不足嗑药"当前无效。
 */
import { existsSync, statSync, readFileSync, readdirSync, mkdirSync, openSync, closeSync } from 'node:fs'
import { join, dirname, basename } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawn, spawnSync } from 'node:child_process'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const PYTHON = process.env.MAAORCH_PYTHON || 'python'

const FINDER = join(ROOT, 'tools', 'prts_job_finder.py')
const RUNNER = join(ROOT, 'tools', 'maa_job_fallback.py')
const SCREEN_DIR = join(ROOT, 'data', 'copilot', 'screening')
const PICKED_DIR = join(ROOT, 'data', 'copilot', 'picked')
const REPORT = join(ROOT, 'data', 'copilot', 'fallback_report.json')
const LOG_DIR = join(ROOT, 'logs')
const CACHE_TTL_MS = 6 * 3600 * 1000 // 与旧实现一致：筛选结果 6 小时内复用（除非 refresh=1）

/** 正在跑的降级重试（一次只允许一次，与旧实现一致） */
const state = { proc: null, started: 0, log: '', cmd: [] }

const readJson = (p) => { try { return JSON.parse(readFileSync(p, 'utf8')) } catch { return null } }
const stageOk = (s) => /^[A-Za-z0-9_-]+$/.test(s)   // stage 会被拼进文件名，必须挡住 ../ 之类

/** 最新一份干员箱（MAA OperBox 导出或手工上传的名单） */
function latestOperbox() {
  const dir = join(ROOT, 'data')
  const rows = readdirSync(dir)
    .filter((n) => /^operbox.*\.json$/i.test(n))
    .map((n) => ({ name: n, path: join(dir, n), mtime: statSync(join(dir, n)).mtimeMs }))
    .sort((a, b) => b.mtime - a.mtime)
  if (!rows.length) throw new Error('data/ 下没有 operbox*.json（先用 MAA 的「干员识别」导出一份）')
  return rows[0]
}

/**
 * 起一个子进程并收干净它的输出（**异步**：筛选要跑 10~60 秒，
 * 旧 Node 里惯用的 spawnSync 会把整个看板冻住，所以这里必须用异步版）。
 */
function spawnCapture(cmd, args, { timeoutMs = 600000 } = {}) {
  return new Promise((resolve) => {
    let out = '', err = '', done = false
    const finish = (r) => { if (!done) { done = true; clearTimeout(timer); resolve(r) } }
    const timer = setTimeout(() => {
      try { proc.kill() } catch {}
      finish({ code: -1, stdout: out, stderr: err + `\n[超时 ${timeoutMs}ms，已杀]` })
    }, timeoutMs)
    let proc
    try {
      proc = spawn(cmd, args, { cwd: ROOT, windowsHide: true })
    } catch (e) {
      return finish({ code: -1, stdout: '', stderr: String(e.message) })
    }
    proc.stdout?.on('data', (c) => { if (out.length < 2_000_000) out += c })
    proc.stderr?.on('data', (c) => { if (err.length < 2_000_000) err += c })
    proc.on('error', (e) => finish({ code: -1, stdout: out, stderr: err + String(e.message) }))
    proc.on('close', (code) => finish({ code, stdout: out, stderr: err }))
  })
}

/** 筛一关：调 prts_job_finder.py（网络慢，结果落盘缓存） */
async function screen(stage, top = 6, refresh = false) {
  if (!stageOk(stage)) throw new Error('stage_name 不合法（只允许字母/数字/下划线/连字符）')
  mkdirSync(SCREEN_DIR, { recursive: true })
  const box = latestOperbox()
  const cache = join(SCREEN_DIR, `${stage}.json`)
  const fresh = existsSync(cache) && Date.now() - statSync(cache).mtimeMs < CACHE_TTL_MS
  if (fresh && !refresh) {
    const hit = readJson(cache) || {}
    hit._operbox = basename(box.path)
    hit._cached = true
    return hit
  }
  mkdirSync(PICKED_DIR, { recursive: true })
  const args = [FINDER, '--stages', stage, '--operbox', box.path, '--out', cache,
                '--dump-dir', PICKED_DIR, '--top', String(top)]
  const r = await spawnCapture(PYTHON, args, { timeoutMs: 600000 })
  if (!existsSync(cache)) {
    throw new Error(`筛选失败（exit ${r.code}）：${(r.stdout || '').slice(-400)}${(r.stderr || '').slice(-200)}`)
  }
  const data = readJson(cache) || {}
  data._console = (r.stdout || '').slice(-4000)
  data._operbox = basename(box.path)
  return data
}

/** 起一次「自动降级重试」——独立进程跑，日志落盘，前端轮询 status */
function start({ stage = '', codes = '', emu = '1', max_tries = 4, support = true } = {}) {
  if (state.proc && state.proc.exitCode === null) throw new Error('已有一次运行在进行中')
  const emuS = String(emu).replace(/\D/g, '') || '1'
  const tries = Math.min(10, Math.max(1, parseInt(max_tries, 10) || 4))
  const args = [RUNNER, '--emu', emuS, '--max-tries', String(tries), '--out', REPORT]
  if (stage) {
    if (!stageOk(stage)) throw new Error('stage_name 不合法')
    const sc = join(SCREEN_DIR, `${stage}.json`)
    if (!existsSync(sc)) throw new Error(`还没有 ${stage} 的筛选结果 —— 先在页面上点「🔍 筛选这一关」`)
    args.push('--stage', stage, '--screening', sc)
  } else if (codes) {
    args.push('--codes', String(codes))
  } else {
    throw new Error('stage 或 codes 至少给一个')
  }
  if (!support) args.push('--no-support')

  mkdirSync(LOG_DIR, { recursive: true })
  // 用**本地时间**命名（原来用 ISO/UTC 会写成前一天的日期，排查时对不上班次）
  const d = new Date()
  const p2 = (n) => String(n).padStart(2, '0')
  const stamp = `${p2(d.getMonth() + 1)}${p2(d.getDate())}_${p2(d.getHours())}${p2(d.getMinutes())}${p2(d.getSeconds())}`
  const log = join(LOG_DIR, `fallback_${stamp}.log`)
  const fd = openSync(log, 'a')
  let proc
  try {
    // stdio 走文件描述符（不是 pipe）：日志直接落盘，status 读同一个文件。
    // **必须带 -u**：python 的 stdout 在非 tty 下是块缓冲，不带它页面 tail 会空白到跑完为止。
    proc = spawn(PYTHON, ['-u', ...args], { cwd: ROOT, windowsHide: true, stdio: ['ignore', fd, fd] })
  } finally {
    try { closeSync(fd) } catch {}
  }
  state.proc = proc
  state.started = Date.now()
  state.log = log
  state.cmd = args
  return { pid: proc.pid, log, cmd: 'python ' + args.join(' ') }
}

/** 运行状态：进程是否在跑 + 日志尾部 + 最近一次报告 */
function status() {
  const proc = state.proc
  const running = !!(proc && proc.exitCode === null && proc.signalCode === null)
  const out = {
    running,
    pid: proc?.pid ?? null,
    elapsed: state.started ? +((Date.now() - state.started) / 1000).toFixed(1) : 0,
    log: state.log || '',
    tail: '',
  }
  if (state.log && existsSync(state.log)) {
    out.tail = readFileSync(state.log, 'utf8').slice(-4000)
  }
  if (existsSync(REPORT)) {
    const rep = readJson(REPORT)
    if (rep) out.report = rep
  }
  return out
}

function stop() {
  const proc = state.proc
  if (proc && proc.exitCode === null) {
    spawnSync('taskkill', ['/PID', String(proc.pid), '/T', '/F'], { windowsHide: true })
    state.proc = null
    return { stopped: true }
  }
  return { stopped: false }
}

/** GET 侧路由：命中返回 {body, code}，未命中返回 null */
export async function copilotGet(pathname, url) {
  if (pathname === '/api/copilot/info') {
    return { code: 200, body: copilotInfo() }
  }
  if (pathname === '/api/copilot/screen') {
    const stage = url.searchParams.get('stage') || ''
    if (!stage) return { code: 400, body: { ok: false, error: 'stage 必填' } }
    const top = Math.min(30, Math.max(1, parseInt(url.searchParams.get('top') || '6', 10) || 6))
    const refresh = ['1', 'true', 'yes'].includes((url.searchParams.get('refresh') || '').toLowerCase())
    try {
      return { code: 200, body: await screen(stage, top, refresh) }
    } catch (e) {
      return { code: 500, body: { ok: false, error: `${e.name || 'Error'}: ${e.message}` } }
    }
  }
  if (pathname === '/api/copilot/fallback/status') {
    return { code: 200, body: status() }
  }
  return null
}

/** POST 侧路由：命中返回 {body, code}，未命中返回 null */
export function copilotPost(pathname, body = {}) {
  if (pathname === '/api/copilot/fallback') {
    try {
      return { code: 200, body: start(body) }
    } catch (e) {
      return { code: 400, body: { ok: false, error: `${e.name || 'Error'}: ${e.message}` } }
    }
  }
  if (pathname === '/api/copilot/fallback/stop') {
    return { code: 200, body: stop() }
  }
  return null
}

/** 供 /api/health 之类的自证用（页面外也能查一眼这模块有没有被挂上） */
export function copilotInfo() {
  return {
    python: PYTHON,
    finder: existsSync(FINDER),
    runner: existsSync(RUNNER),
    screening_dir: existsSync(SCREEN_DIR),
    operbox: (() => { try { return latestOperbox().name } catch (e) { return `(无: ${e.message})` } })(),
    running: !!(state.proc && state.proc.exitCode === null),
  }
}
