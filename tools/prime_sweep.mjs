#!/usr/bin/env node
/**
 * prime_sweep —— 「首通源石」横扫器（Node，2026-09-23 新写）
 *
 * 一条流水线：**导航（自己的） → 判已通关（面板星级） → 筛作业 → 降级重试跑 → 记账**。
 *
 * 为什么每一步是这么做的（都是当天实测换来的）:
 *   · **导航必须自己做**：MAA 的 Copilot 不会自动导航（实测三次，直接在残留页面上开打）。
 *     ⇒ 每关先跑 `tools/main_nav.py --code <显示代号>`，它会核对面板标题栏的代号。
 *   · **判"已通关"看面板三个六边形**（`stars`）：0 = 未通关（灰的）⇒ 该打；
 *     ≥1 = 已通关 ⇒ 首通源石已拿，**跳过不浪费理智**（`main_nav` 已经把 `stars/cleared` 算好了）。
 *   · **作业只用作业站的人气轴**（用户 2026-09-23 口径：「直接就抄作业吧 不需要自己写 抄不了再想办法改作业」），
 *     `prts_job_finder.py` 会按 box 把"缺干员/练度不够"的排掉，`maa_job_fallback.py` 负责降级重试。
 *   · **允许借人**：`maa_job_fallback.py` 默认 `support_unit_usage=1`（借一个助战）——用户要求"能借就直接借"。
 *   · **失败会返还理智**（实测：三次打输后理智仍是 210/210）⇒ 试错不亏，敢试。
 *
 * 用法:
 *   node tools/prime_sweep.mjs --chapters 15-17            # dry-run：只打印队列
 *   node tools/prime_sweep.mjs --chapters 15-17 --apply --max 6
 *   node tools/prime_sweep.mjs --stages main_15-18,main_15-01 --apply
 *   node tools/prime_sweep.mjs --chapters 15-17 --apply --raid-only
 */
import { spawn } from 'node:child_process'
import { existsSync, statSync, readFileSync, writeFileSync, mkdirSync, appendFileSync, readdirSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const PY = process.env.MAAORCH_PYTHON || 'python'
const PLAN = join(ROOT, 'data', 'prime_plan.json')
const SCREEN_DIR = join(ROOT, 'data', 'copilot', 'prime_screening')
const REPORT_DIR = join(ROOT, 'runtime', 'reports', 'prime')
const LEDGER = join(ROOT, 'runtime', 'prime_ledger.jsonl')
const STATE = join(ROOT, 'runtime', 'prime_state.json')

const argv = process.argv.slice(2)
const argOf = (n, d) => { const i = argv.indexOf('--' + n); return i >= 0 && argv[i + 1] && !argv[i + 1].startsWith('--') ? argv[i + 1] : d }
const has = (n) => argv.includes('--' + n)
const APPLY = has('apply')
const EMU = String(argOf('emu', '1'))
const MAX_TRIES = Math.max(1, Math.min(8, parseInt(argOf('max-tries', '3'), 10) || 3))
const MAX_UNITS = Math.max(1, parseInt(argOf('max', '99'), 10) || 99)
const SANITY_MIN = Math.max(0, parseInt(argOf('sanity-min', '20'), 10) || 0)
const RAID_ONLY = has('raid-only')
const NORMAL_ONLY = has('normal-only')
const BOX = argOf('box', '') || latestBox()
const CHAPTERS = (argOf('chapters', '') || '').split(',').map((s) => s.trim()).filter(Boolean)
const STAGES = (argOf('stages', '') || '').split(',').map((s) => s.trim()).filter(Boolean)

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const log = (m) => console.log(`[${new Date().toLocaleTimeString('zh-CN', { hour12: false })}] ${m}`)
const readJson = (p) => { try { return JSON.parse(readFileSync(p, 'utf8')) } catch { return null } }

function latestBox() {
  const dir = join(ROOT, 'data')
  const rows = readdirSync(dir).filter((n) => /^operbox.*\.json$/i.test(n))
    .map((n) => ({ n, p: join(dir, n), m: statSync(join(dir, n)).mtimeMs })).sort((a, b) => b.m - a.m)
  if (!rows.length) throw new Error('data/ 下没有 operbox*.json')
  return rows[0].p
}

function run(cmd, args, timeoutMs = 600000) {
  return new Promise((resolve) => {
    let out = '', err = '', done = false
    const t0 = Date.now()
    const fin = (r) => { if (!done) { done = true; clearTimeout(timer); resolve({ ...r, out, err, ms: Date.now() - t0 }) } }
    const timer = setTimeout(() => { try { p.kill() } catch {} ; fin({ code: -1, timedOut: true }) }, timeoutMs)
    let p
    try { p = spawn(cmd, args, { cwd: ROOT, windowsHide: true }) }
    catch (e) { clearTimeout(timer); return resolve({ code: -1, out: '', err: String(e), ms: 0 }) }
    p.stdout?.on('data', (c) => { if (out.length < 2_000_000) out += c })
    p.stderr?.on('data', (c) => { if (err.length < 500_000) err += c })
    p.on('error', (e) => fin({ code: -1, err: String(e) }))
    p.on('close', (code) => fin({ code }))
  })
}

/** 解析 python 脚本最后一行 JSON */
function lastJson(out) {
  const line = String(out).trim().split('\n').filter((l) => l.trim().startsWith('{')).pop()
  if (!line) return null
  try { return JSON.parse(line) } catch { return null }
}

// ── 队列 ────────────────────────────────────────────────────────────────
function buildQueue() {
  const plan = readJson(PLAN)
  if (!plan) throw new Error(`没有 ${PLAN}，先跑 node tools/prime_plan.mjs`)
  let rows = plan.rows.filter((r) => !r.story)          // 剧情关（0 理智）先不进这条流水线
  if (STAGES.length) return rows.filter((r) => STAGES.includes(r.stageId)).map(toUnit)
  if (CHAPTERS.length) {
    const want = new Set()
    for (const c of CHAPTERS) {
      const m = c.match(/^(\d+)-(\d+)$/)
      if (m) for (let i = +m[1]; i <= +m[2]; i++) want.add(i)
      else want.add(Number(c))
    }
    rows = rows.filter((r) => want.has(r.chapter))
  }
  if (RAID_ONLY) rows = rows.filter((r) => r.diff !== 'NORMAL')
  if (NORMAL_ONLY) rows = rows.filter((r) => r.diff === 'NORMAL')
  // 顺序：章节升序 → 普通在前（先把章节打通，险地/磨难才解锁）→ 理智升序 → 代号
  rows.sort((a, b) => a.chapter - b.chapter
    || (a.diff === 'NORMAL' ? 0 : 1) - (b.diff === 'NORMAL' ? 0 : 1)
    || a.ap - b.ap
    || a.code.localeCompare(b.code, undefined, { numeric: true }))
  return rows.map(toUnit)
}

function toUnit(r) {
  return {
    stageId: r.stageId, code: r.code, chapter: r.chapter, name: r.name,
    raid: r.diff !== 'NORMAL', ap: r.ap,
    // 险地/磨难：作业站用的是 `xxx#s` 这种键；那个键没作业时退回同代号的普通轴（同一张摆位图）
    mirror: r.diff === 'NORMAL' ? '' : r.stageId.replace(/#[a-z]#?$/, ''),
  }
}

function loadState() { return readJson(STATE) || { units: {} } }
function saveState(s) { writeFileSync(STATE, JSON.stringify(s, null, 1), 'utf8') }

// ── 每关 ────────────────────────────────────────────────────────────────
async function ensureScreening(stageId, cacheKey) {
  mkdirSync(SCREEN_DIR, { recursive: true })
  const cache = join(SCREEN_DIR, `${cacheKey}.json`)
  const fresh = existsSync(cache) && Date.now() - statSync(cache).mtimeMs < 12 * 3600 * 1000
  if (fresh && readJson(cache)?.[stageId]) return { cache, n: readJson(cache)[stageId].length }
  const r = await run(PY, ['tools/prts_job_finder.py', '--stages', stageId, '--operbox', BOX,
    '--out', cache, '--top', '8'], 600000)
  const rows = readJson(cache)?.[stageId] || []
  return { cache, n: rows.length, tail: (r.out || '').slice(-200) }
}

async function runOne(u, st) {
  const rec = { ...u, ts: new Date().toISOString(), action: '', stars: null, verdict: '', tries: [] }
  // ① 导航 + 判已通关
  log(`▶ ${u.code}${u.raid ? '（险地/磨难）' : ''} ${u.name}｜导航…`)
  const nav = lastJson((await run(PY, ['tools/main_nav.py', '--code', u.code, '--quiet',
    ...(u.raid ? ['--raid'] : [])], 600000)).out)
  rec.stars = nav?.stars ?? null
  rec.sanity = nav?.sanity ?? null
  if (!nav?.ok) {
    rec.action = 'nav_failed'; rec.verdict = nav?.detail || '导航失败'
    log(`  ✗ ${rec.verdict}`)
    return rec
  }
  if (nav.cleared) {
    rec.action = 'cleared'; rec.verdict = `已通关（${nav.stars} 星）⇒ 跳过`
    log(`  = 已通关（${nav.stars} 星）跳过`)
    return rec
  }
  // **报酬里没有源石就不打**（用户 2026-09-23 原话：「报酬里面没有源石就是没有就不用打了」）
  //   判据来自 main_nav 的 `reward_primes`：报酬区里有没有那颗"未领的至纯源石徽章"。
  //   实测例：15-20 普通 = 有[1.0]（该打）；15-3 险地 = 无[0.47]（源石已被普通那次领走 ⇒ 不打）。
  if (nav.reward_primes !== true) {
    rec.action = 'no_prime'
    rec.verdict = `报酬里没有源石（匹配分 ${nav.reward_score ?? '?'}）⇒ 不打`
    log(`  ⊘ 报酬无源石（${nav.reward_score ?? '?'}）⇒ 跳过`)
    return rec
  }
  rec.reward_score = nav.reward_score
  if (nav.sanity != null && nav.sanity < SANITY_MIN) {
    rec.action = 'insufficient_sanity'
    rec.verdict = `理智 ${nav.sanity} < ${SANITY_MIN} ⇒ 收工`
    log(`  ⏸ ${rec.verdict}`)
    return rec
  }
  if (!APPLY) { rec.action = 'would_run'; rec.verdict = 'dry-run'; return rec }

  // ② 筛作业（险地没有作业就退回同代号的普通轴）
  let key = u.stageId
  let sc = await ensureScreening(key, key.replace(/[#]/g, '_'))
  if (!sc.n && u.mirror) {
    log(`  · ${key} 没有作业 ⇒ 用同关普通轴 ${u.mirror}`)
    key = u.mirror
    sc = await ensureScreening(key, key.replace(/[#]/g, '_'))
  }
  if (!sc.n) {
    rec.action = 'no_candidate'; rec.verdict = '作业站零候选'
    log('  ✗ 作业站零候选')
    return rec
  }
  // ③ 跑（降级重试 + 借人）
  mkdirSync(REPORT_DIR, { recursive: true })
  const report = join(REPORT_DIR, `${u.stageId.replace(/#/g, '_')}.json`)
  log(`  · 候选 ${sc.n} 份 ⇒ 依次试（借人开）…`)
  const r = await run(PY, ['tools/maa_job_fallback.py', '--stage', key, '--screening', sc.cache,
    '--emu', EMU, '--max-tries', String(MAX_TRIES), '--out', report], 45 * 60000)
  const rep = readJson(report)
  rec.tried = (rep?.tries || []).map((t) => ({ id: t.id, verdict: t.verdict, three: t.three_star }))
  const ok = String(rep?.result || '').startsWith('成功')
  rec.action = ok ? 'done' : 'failed'
  rec.verdict = rep?.result || '跑完但无结论'
  log(`  ${ok ? '✓' : '✗'} ${rec.verdict}`)
  return rec
}

// ── main ────────────────────────────────────────────────────────────────
const queue = buildQueue()
const state = loadState()
const todo = queue.filter((u) => !['done', 'cleared', 'no_candidate', 'no_prime'].includes(state.units[u.stageId]?.action))
log(`队列 ${queue.length} 关（待办 ${todo.length}）｜box=${BOX.split('\\').pop()}｜模式=${APPLY ? 'APPLY' : 'DRY-RUN'}｜每关最多试 ${MAX_TRIES} 份`)
if (!APPLY) {
  for (const u of todo.slice(0, 40)) log(`  · ${u.stageId} = 显示 ${u.code}${u.raid ? '（险地/磨难）' : ''} ${u.name}（${u.ap} 理智）`)
  if (todo.length > 40) log(`  … 还有 ${todo.length - 40} 关`)
  log('这是 dry-run —— 要真动手加 --apply')
  process.exit(0)
}

let done = 0, fails = 0
for (const u of todo) {
  if (done + fails >= MAX_UNITS) { log(`到 --max ${MAX_UNITS} 上限，收工`); break }
  const rec = await runOne(u, state)
  state.units[u.stageId] = { action: rec.action, stars: rec.stars, ts: rec.ts, verdict: rec.verdict }
  saveState(state)
  appendFileSync(LEDGER, JSON.stringify(rec) + '\n', 'utf8')
  if (rec.action === 'done' || rec.action === 'cleared') done++
  else if (rec.action === 'failed') fails++
  if (rec.action === 'insufficient_sanity') { log('理智不足 ⇒ 收工（补理智后重跑同一条命令即可续）'); break }
  await sleep(2000)
}
log(`\n本次：成功/跳过 ${done}｜未过 ${fails}｜账本 ${LEDGER}`)
log(`累计已完成 ${Object.values(state.units).filter((x) => ['done', 'cleared'].includes(x.action)).length} / ${queue.length}`)
