#!/usr/bin/env node
/**
 * 活动关卡横扫器 —— 「普通 + 突袭 全清」（2026-09-22 新写，Node）
 *
 * 为什么需要它:
 *     作业实验室（🧪 /api/copilot/*）已经能"跑不了就换下一份"，但它一次只吃一关，
 *     而且要人先手动把游戏导航到那一关的详情页。清一整个活动（二十几个关卡难度）
 *     靠手点不现实，所以把"导航 → 查三星 → 按难度挑作业 → 降级重试 → 记账"串成一条流水线。
 *
 * 单位（unit）= 一个「关卡 × 难度」：
 *     ① 导航：`tools/bi_home.py --stage <代号> --want <难度>`（回大厅 → 终端 → 关卡详情页）
 *     ② 核对：`tools/bi_state.py --stage <代号> --want <难度>`——**以它为准**（bi_home 内部
 *        调 bi_goto 但丢弃返回码，成功与否看不出来）；它同时告诉我们"切没切到突袭"
 *     ③ 查三星：`verify_three_star()`（`tools/maa_job_fallback.py` 里的实现，单一来源）——
 *        已是三星就跳过，**不重复消耗理智**
 *     ④ 跑作业：筛作业 → **按难度过滤** → `maa_job_fallback.py` 依次试
 *     ⑤ 记账：每个单位一行 jsonl + 末尾一张 md 表
 *
 * 难度（prts.plus 作业 content 里的 `difficulty`）:
 *     0=当前难度 / 1=普通 / 2=突袭 / 3=普通+突袭通用
 *     ⇒ 打普通要 {1,3,0}，打突袭要 {2,3,0}；挑错会把普通轴拿到突袭页面上跑。
 *     排序上"同难度优先"（1 或 2 排最前），其次通用(3)，最后当前难度(0)。
 *
 * 纪律:
 *     · **默认 dry-run**（只打印要做什么），要真动手必须加 `--apply`
 *     · 理智：跑一关就要花；查三星/导航都不花。三星关一律跳过，不做无谓消耗
 *     · 不碰产线与肉鸽机队：只连 emu（默认 #1）与 `services/maa/instances/1`
 *     · 单实例不许两个 MAA：跑之前请确认没人在页面上同时点「跑前 N 份」
 *
 * 用法:
 *     node tools/event_sweep.mjs --stages "MT-1..MT-10,MT-EX-1..MT-EX-8,MT-S-1..MT-S-5"
 *     node tools/event_sweep.mjs --stages "MT-EX-7..MT-EX-8,MT-S-1..MT-S-5" --wants 普通,突袭 --apply
 *     node tools/event_sweep.mjs --only MT-EX-8 --wants 突袭 --apply
 */
import { spawn, spawnSync } from 'node:child_process'
import { existsSync, statSync, readFileSync, writeFileSync, mkdirSync, appendFileSync, readdirSync, rmSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const PY = process.env.MAAORCH_PYTHON || 'python'
const SCREEN_DIR = join(ROOT, 'data', 'copilot', 'screening')
const REPORT_DIR = join(ROOT, 'data', 'copilot', 'sweep_reports')
const LEDGER_DIR = join(ROOT, 'runtime', 'reports')

// ── 报酬栏（未领首通源石徽章）判据阈值：**单一来源** `runtime/config/reward_gate.json` ──────
//   ★ 2026-09-24 Lead 定案（背景错误是 Lead 自己犯的）：本文件原来写死
//     `REWARD_AMBIG_LO = 0.46` / `REWARD_AMBIG_HI = 0.64`，把**落在 0.46–0.64 的分数一律降级成
//     unreliable**。那条带是**从 ⊘ 观测簇反推的，不是门槛本身**。
//   真正的语义在 `tools/panel_gate.py:28`（原文）：
//     「reward_score  模板最高分（0.999=徽章在；≤0.72=不在。阈值 0.75）」
//   ⇒ **低分不是"读数不可信"，低分就是"徽章不在"**（＝该关首通源石已领）。实测全库：
//     70 条 no_prime 分数 0.461–0.702（中位 0.472）、20 条 done 0.950–1.000 ⇒ 两组在
//     (0.702, 0.950] **完全无重叠**，0.75 本来就分得开。旧实现把 0.46–0.70 这批**本来就很确信的
//     "不在"读数**降到 unreliable ⇒ ⊘ 永远无法终结，队列把每个已领关无限重走。
//   为什么不用 0.8：0.8 会在 (0.75, 0.8] 凭空造一条 ambiguous 带（无依据）——panel_gate.py:28
//     的原文只给了 0.72/0.75，实测间隔 (0.702,0.950] 宽 0.248 也不需要抬高。
const GATE_CFG_PATH = join(ROOT, 'runtime', 'config', 'reward_gate.json')
const GATE_CFG_FALLBACK = { present: 0.75, absent: 0.72,
  source: '内置回退（tools/panel_gate.py:28 原文：0.999=徽章在；≤0.72=不在；阈值 0.75）' }

/** 读阈值配置。读不到/坏了 ⇒ 回退 panel_gate.py:28 的原文阈值并**在日志里警告**（绝不静默）。 */
function loadRewardGate() {
  try {
    const j = JSON.parse(readFileSync(GATE_CFG_PATH, 'utf8'))
    const present = Number(j.present)
    const absent = Number(j.absent)
    if (!Number.isFinite(present) || !Number.isFinite(absent) || !(present > absent)) {
      throw new Error(`present/absent 非法（present=${j.present} absent=${j.absent}，要求 present > absent）`)
    }
    return { present, absent, source: j.source || GATE_CFG_PATH }
  } catch (e) {
    console.error(`⚠️ 读不到 ${GATE_CFG_PATH}（${e.message}）⇒ 回退内置阈值 `
      + `present=${GATE_CFG_FALLBACK.present} / absent=${GATE_CFG_FALLBACK.absent}（panel_gate.py:28 原文）`)
    return GATE_CFG_FALLBACK
  }
}
const REWARD_GATE = loadRewardGate()
const REWARD_PRESENT = REWARD_GATE.present
const REWARD_ABSENT = REWARD_GATE.absent

// ── 参数 ────────────────────────────────────────────────────────────────
const argv = process.argv.slice(2)
const argOf = (name, dflt) => {
  const i = argv.indexOf('--' + name)
  return i >= 0 && argv[i + 1] && !argv[i + 1].startsWith('--') ? argv[i + 1] : dflt
}
const has = (name) => argv.includes('--' + name)
// **活动和关卡代号前缀都可换**（2026-09-23 泛化）：默认众生行记 act42side/MT，
// 空想花庭 = `--event act26side --prefix HE`，揭幕者们 = `--event act38side --prefix PV`。
// 不加这个参数时 `stageName()` 会把别的活动的代号拼成 act42side_xxx ⇒ 拉不到作业、白跑。
const EVENT = argOf('event', 'act42side')
const PREFIX = argOf('prefix', 'MT')
const APPLY = has('apply')
const EMU = String(argOf('emu', '1')).replace(/\D/g, '') || '1'
const MAX_TRIES = Math.min(8, Math.max(1, parseInt(argOf('max-tries', '3'), 10) || 3))
const RUN_TIMEOUT_MIN = Math.max(3, parseInt(argOf('run-timeout-min', '30'), 10) || 30)
// 理智门槛：低于它就**停止横扫**而不是硬跑。
// 理由：MaaCore 的 Copilot 自带 `BattleStartPre@UseMedicine` 子任务（会嗑理智药），
// 但药尽之后页面会弹「用源石恢复理智」，而 Copilot **没有源石开关**（只有 Fight 任务有 UseStone）
// ⇒ 硬跑只会每份作业干等 600s、连关都进不去。留给人工补理智再来（重跑会自动跳过已三星的关）。
const SANITY_MIN = Math.max(0, parseInt(argOf('sanity-min', '15'), 10) || 0)
const WANTS = (argOf('wants', '普通,突袭')).split(',').map((s) => s.trim()).filter(Boolean)
const STAGES_SPEC = argOf('only', '') || argOf('stages', 'MT-1..MT-10,MT-EX-1..MT-EX-8,MT-S-1..MT-S-5')
// 剧情看门狗：`--story-ms 1500`（tick 间隔）/ `--no-story-watch`（关掉，调试用）
// 实测单次 `/skipstory` 开销（2026-09-24，在跑的常驻服务上量的）：取帧 ~0.25s（忙时 0.6~0.8s）
//   + 右上角 229x70 小块 OCR ~0.8~1.3s ⇒ **一次 tick 约 1.3s**。所以间隔取 1.5s（与
//   `tools/story_skip.py --interval 1.5` 同口径）；tick 是"上一次跑完再排下一次"，
//   慢的时候不会堆积、不会把请求灌爆。
const STORY_MS = Math.max(500, parseInt(argOf('story-ms', '1500'), 10) || 1500)
const STORY_WATCH = !has('no-story-watch')
// 导航失败时是否先"按卡片模板进活动"再试一次（默认开；`--no-enter-fallback` 可关）
const ENTER_FALLBACK = !has('no-enter-fallback')
// `--enter-event <slug>`：显式指定活动卡片模板 slug（`data/templates/events/<slug>.png`）。
// 为什么需要它：index.json 的值里只有 xinhuo 那行带 `actNNside`，别的活动（如 不义之财 CV）
// 反查不到 ⇒ 那种情况由调用方直接给 slug（队列里 act28side→buyizhicai 是这么映射的）。
const ENTER_SLUG = argOf('enter-event', '')
// ── **演习优先**（task-15，2026-09-24）：先 drill（0 理智）拿到 3★ 的轴，才真打 ──
//   用户铁律「2★ 纯浪费」；Lead 手工那套（EA-1 / CV-EX-8）实测有效 ⇒ 接进流水线。
const DRILL_FIRST = !has('no-drill')                    // `--no-drill` ⇒ 退回老的"直接真打"
const DRILL_TRIES = Math.max(1, Math.min(8, parseInt(argOf('drill-tries', '2'), 10) || 2))
const DRILL_TIMEOUT_MIN = Math.max(3, parseInt(argOf('drill-timeout-min', '12'), 10) || 12)
const DRILL_STRICT = has('drill-strict')                // 演习通道整体不可用时也不退回真打（零理智优先）
const DRILL_RECORD = has('drill-record')                // 演习默认不录屏（省 180s + ~50MB 拉取）
// `drill_broken`（演习通道整体不可用）退回老路径时**最多真打几份轴**：默认 **1**；**0 = 不真打**。
//   为什么限制（2026-09-24 实测，AD-EX-2 12:38 那轮）：4 份演习全因技术原因无效 ⇒ 退老路径
//   一口气真打了 3 份轴（306s，MAA 自报 done、无结算证据）—— 这正是 pi 审查说的"盲目真打白花
//   理智"。限制成 1 份：既保留"通道坏也不至于让队列停摆"，又把最坏浪费从 3×理智压到 1×。
//   `--drill-broken-tries 0` ⇒ 完全不退（零理智优先，坏通道就标 no_axis 换下一关）。
const DRILL_BROKEN_TRIES = (() => {
  const v = parseInt(argOf('drill-broken-tries', '1'), 10)
  return Number.isFinite(v) ? Math.max(0, Math.min(8, v)) : 1
})()
// ── **突袭不跑演习**（2026-09-24 真机修复；task-15 末尾的预警成真）────────────────
//   证据（`runtime/_prime_queue3.log` 14:52 / 14:57，单位 `CV-EX-8|突袭`）：
//     `演习 1/2 prts://41415 ⇒ 无结论（gate3-invalid）`——534s，MAA 报 Completed，可**没有结算页**
//      `演习 2/2 prts://51786 ⇒ 无结论（gate3-invalid）`——`still_battle: true`（结果帧还在战斗 HUD）
//   ⇒ `stage_attempt --drill --raid` 这个组合**跑不出结算页**（2/2 份轴都无效），而且第二份把游戏留在
//     「演习中 + PAUSE 暂停中」那一屏 ⇒ 紧接着 4 次导航全废、CV-1/2/3 被队列误标 noreach。
//   ⇒ 突袭**默认直接走老的降级重试路径**（不点演习、不碰演习通道）；要强行试只能显式 `--raid-drill`（默认关）。
const DRILL_RAID = has('raid-drill')

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// ── 常驻服务（默认 8765）：帧 / OCR 引擎 / 模板全在它内存里 ──────────────
// 为什么都走它：冷启动一个 `python tools/look.py` 要 4~6s（RapidOCR 每次重载模型），
//   而服务里一次取帧 + 小块 OCR 是几百毫秒级；剧情看门狗要高频问，更不能每问一次就起进程。
const SVC = `http://127.0.0.1:${process.env.MAAORCH_SVC_PORT || 8765}`

/** 一次 GET，超时/连接失败/非 JSON 一律返回 null（调用方据此走兜底路径）。 */
async function svcJson(path, timeoutMs = 4000) {
  try {
    const ctl = new AbortController()
    const t = setTimeout(() => ctl.abort(), timeoutMs)
    const r = await fetch(SVC + path, { signal: ctl.signal })
    clearTimeout(t)
    if (!r.ok) return null
    return await r.json()
  } catch { return null }
}

/** 一次取帧读全部：页面指纹 + 面板字段 + **结算页星数**（`page == '结算页'` 时才有）。
 *  `shotPath` 非空 ⇒ 顺手把这一帧存下来当证据（服务不支持该参数时会忽略，不影响判定）。
 *  `opts.text === true` ⇒ 顺带要**整帧 OCR 文字**（服务的 `?text=1`，实测 +0.4~1.5s）——
 *    结算页判定要靠奖励栏那两行字（「首次掉落」/「理智返还」），见 `settleTextEvidence()`。 */
async function readAll(shotPath = '', opts = {}) {
  const parts = []
  if (shotPath) parts.push(`shot=${encodeURIComponent(join(ROOT, shotPath))}`)
  if (opts.text) parts.push('text=1')
  return svcJson('/all' + (parts.length ? '?' + parts.join('&') : ''), opts.text ? 8000 : 5000)
}

/** **奖励栏文字证据**（纯函数，`--selftest` 覆盖）—— 结算页判定的冗余判据。
 *
 *  为什么必须有它（2026-09-24 铁证，帧 `runtime/_settle_20260924_1900_CV-EX-8_raid.png`）：
 *  **突袭没有三星评价**（`docs/BIEX1_HANDOVER.md`：「突袭没有三星评价，所以星级读出 0 属于正常，
 *  **不是失败**；判定靠页面类型（含「理智返还」= 失败）与「首次掉落」」；`docs/CE6_HANDOVER.md` 同口径）
 *  ⇒ 真通关的突袭结算页星区**本来就是灰的**（`result_stars=0`、三槽蓝像素 0/0/0 是**正常读数**），
 *  唯一可靠的证据是奖励栏的「**首次掉落**（源石×1）」；失败页的正面证据是「**理智返还**」。
 *  ⇒ 用文字判：读不到文字只会让判定更保守（`null` = 未知），**不会**把赢的那局判死。
 *  返回 `{textSeen, firstDrop, sanityRefund, drillFrame}`：`null` = 没拿到文字（未知，不是 false）。 */
function settleTextEvidence(text) {
  const s = String(text || '')
  if (!s.length) return { textSeen: false, firstDrop: null, sanityRefund: null, drillFrame: null }
  return {
    textSeen: true,
    firstDrop: /首次掉落|首次掉/.test(s),
    sanityRefund: /理智返还|理智返/.test(s),
    drillFrame: /演习/.test(s),
  }
}

/** 结算页读数 —— **只有"确实是结算页"才返回结论**，否则 null。
 *
 *  为什么必须单独一条（2026-09-24 实测的 failed 误判就是这么来的）：`stars` 读的是**关卡面板**
 *  的 ROI（x980~1260 / y60~150），在结算页上那块是**干员立绘** ⇒ 读出来必然是垃圾/0；
 *  而"其实打完了"这件事只有结算页能证明。结算页星区是另一块 ROI（x60~300 / y290~360，
 *  三个六边形：**亮蓝=得星 / 灰=未得**），由服务算成 `result_stars`。
 *  星数读不到（服务版本旧）时 `stars: null` —— **绝不拿面板读数冒充结算读数**。 */
function settleFrom(st) {
  if (!st || st.page !== '结算页') return null
  const s = st.result_stars
  return {
    stars: Number.isInteger(s) ? s : null,
    lit: Array.isArray(st.result_lit) ? st.result_lit : null,
    blue: Array.isArray(st.result_blue) ? st.result_blue : null,
    conf: st.result_conf ?? null,
    score: st.page_score ?? null,
    // 奖励栏文字证据：只有调用方要了 `?text=1` 才有（`textSeen:false` = 未知，按"证据不足"处理）
    ...settleTextEvidence(st.text),
  }
}

// ── 基础 ────────────────────────────────────────────────────────────────
function expand(spec) {
  const out = []
  for (const raw of spec.split(',')) {
    const t = raw.trim()
    if (!t) continue
    const m = t.match(/^(.+?)(\d+)\.\.(.+?)(\d+)$/)
    if (!m) { out.push(t); continue }
    const [, p1, n1, p2, n2] = m
    if (p1 !== p2) throw new Error(`区间两端前缀不一致：${t}`)
    if (+n2 - +n1 > 30) throw new Error(`区间过大：${t}`)
    for (let i = +n1; i <= +n2; i++) out.push(p1 + i)
  }
  return out
}

/** 游戏里显示的关卡代号 → 内部 stage_name：MT-7→act42side_07 / MT-EX-7→act42side_ex07 / MT-S-3→act42side_s03 */
function stageName(code, event = EVENT) {
  const parts = String(code).toUpperCase().split('-')
  if (!parts[1]) throw new Error(`认不出这个关卡代号：${code}`)
  if (parts.length === 2) return `${event}_${parts[1].padStart(2, '0')}`
  return `${event}_${parts[1].toLowerCase()}${parts[2].padStart(2, '0')}`
}

/** 主关卡（MT-7）没有突袭，只有 EX/S 有 */
const hasRaid = (code) => String(code).toUpperCase().split('-').length > 2

function run(cmd, args, { timeoutMs = 300000, label = '' } = {}) {
  return new Promise((resolve) => {
    let out = '', err = '', done = false
    const t0 = Date.now()
    const finish = (r) => {
      if (done) return
      done = true
      clearTimeout(timer)
      resolve({ ...r, out, err, ms: Date.now() - t0 })
    }
    const timer = setTimeout(() => {
      try { p.kill() } catch {}
      finish({ code: -1, timedOut: true })
    }, timeoutMs)
    let p
    try {
      p = spawn(cmd, args, { cwd: ROOT, windowsHide: true })
    } catch (e) {
      clearTimeout(timer)
      return resolve({ code: -1, out: '', err: String(e.message), ms: 0 })
    }
    p.stdout?.on('data', (c) => { if (out.length < 1_000_000) out += c })
    p.stderr?.on('data', (c) => { if (err.length < 1_000_000) err += c })
    p.on('error', (e) => finish({ code: -1, err: String(e.message) }))
    p.on('close', (code) => { if (label) log(`    ${label} rc=${code} ${Math.round((Date.now() - t0) / 1000)}s`); finish({ code }) })
  })
}

function log(msg) {
  const line = `[${new Date().toLocaleTimeString('zh-CN', { hour12: false })}] ${msg}`
  console.log(line)
}

const readJson = (p) => { try { return JSON.parse(readFileSync(p, 'utf8')) } catch { return null } }

function latestOperbox() {
  const dir = join(ROOT, 'data')
  const rows = readdirSync(dir).filter((n) => /^operbox.*\.json$/i.test(n))
    .map((n) => ({ name: n, path: join(dir, n), mtime: statSync(join(dir, n)).mtimeMs }))
    .sort((a, b) => b.mtime - a.mtime)
  if (!rows.length) throw new Error('data/ 下没有 operbox*.json')
  return rows[0]
}

/** 从 `data/templates/events/index.json` 反查「事件 id → 卡片模板 slug」。
 *  index.json 的值里写着中文名 + 英文名 + `actNNside` + 关卡前缀，例如
 *  `"xinhuo": "挽歌燃烧殆尽（WHEN ELEGIES ARE ASHES｜act41side｜关卡 EA-1..EA-ST-1）"`。
 *  为什么要它（2026-09-24 实测）：导航失败时唯一可靠的路是 `enter_by_template` 按卡片进活动，
 *  而它要的是 slug；写死 slug 不通用，这里从索引反查 ⇒ 换活动不用改代码。 */
function eventSlug(event = EVENT) {
  if (ENTER_SLUG) return ENTER_SLUG               // 调用方显式给了 slug ⇒ 优先（如 CV→buyizhicai）
  try {
    const idx = JSON.parse(readFileSync(join(ROOT, 'data', 'templates', 'events', 'index.json'), 'utf8'))
    const want = String(event).toLowerCase()
    for (const [slug, desc] of Object.entries(idx)) {
      if (String(desc).toLowerCase().includes(want)) return slug
    }
  } catch { /* 没有索引 ⇒ 不试这条路 */ }
  return null
}

// ── 单位流水线 ──────────────────────────────────────────────────────────
/**
 * ① 导航 + **核实关卡身份** + 切难度 —— 全交给 `tools/mt_nav.py`（一次进进程，输出 JSON）。
 * 为什么不用 bi_home/bi_state：`bi_state` 的「已经在 X 详情页」只看页面特征、不核关卡代号，
 * 站在 MT-EX-7 的页面上问 MT-EX-8 它也会答"已在详情页"（2026-09-22 实测）——
 * 横扫里这一条会直接把 A 关的作业拿去 B 关跑。mt_nav 用**详情页标题栏的 OPERATION+代号**核实。
 */
async function navTo(code, want) {
  // **坐标导航优先**（2026-09-24 落地）：tools/nav_map.py 用"我读图记下的节点坐标 + 模板判页面"，
  //   实测 1.1~1.3s（已在同一张地图上）/ ~3.0s（要从别关面板退回来），而 mt_nav 走 OCR 要 4.1s。
  //   坐标表只覆盖已收录的地图（先 PV-EX），且**只管普通**；其余情况照旧走 mt_nav。
  if (want === '普通') {
    const fast = await run(PY, ['tools/nav_map.py', '--stage', code, '--json'],
      { timeoutMs: 120000, label: `nav_map ${code}` })
    const fl = fast.out.trim().split('\n').filter((l) => l.trim().startsWith('{')).pop()
    if (fl) {
      try {
        const j = JSON.parse(fl)
        if (j.ok) { log(`    nav_map ${code} ✓ ${j.total_ms}ms（坐标法）`); return j }
      } catch { /* 落到 mt_nav */ }
    }
  }
  const r = await run(PY, ['tools/mt_nav.py', '--stage', code, '--want', want, '--prefix', PREFIX],
    { timeoutMs: 900000, label: `mt_nav ${code}/${want}` })
  const line = r.out.trim().split('\n').filter((l) => l.trim().startsWith('{')).pop()
  let res
  if (!line) res = { ok: false, detail: `mt_nav 无输出：${(r.out + r.err).slice(-300)}` }
  else { try { res = JSON.parse(line) } catch { res = { ok: false, detail: 'mt_nav 输出无法解析' } } }

  // **导航失败时补一次"切区"再试**（2026-09-24 实测需要）：进活动后默认停在"主区"，
  //   而 EX 关在另一张区域卡上（AD/PV/LE 都是这样）⇒ 先点 EX 区域卡，再重试一遍导航。
  //   **同一个洞的另一半（2026-09-24 CV 实测补）**：原来只切 EX、**从不切回"主区"**，而游戏里
  //   AREA 选择是**粘的**（切过 EX 就留在 EX）⇒ 之后该活动的**普通**关（CV-1/2/3）站在 EX 图上
  //   点主区坐标，5 轮都"没能进到详情页"，被记成 noreach+6h **假失败**（帧存 _blocked_*）。
  //   现在：按代号推目标区（`-EX-` ⇒ EX，其余 ⇒ 主区），**且只在页面确实是"活动地图"时才点
  //   那张卡**（别在别传列表/详情页上盲点坐标）。
  //   **门怎么开（达芬奇 15:2x 实测 AD 后修）**：AD 没有整屏地图模板（`data/templates/pages/`
  //   只有 map_EP/LE/MT/PV/EA/CV）⇒ 不带 `?text=1` 时 `/all` 恒为「未知 0.367」，而且**结构判据
  //   不抬 page_score**（只改 page/page_src）⇒ 原来 `score>=0.9` 把 AD 全挡在门外（15:16~15:20
  //   六次 `zone AD EX rc=0` 一次都没点中）。现在：带 `?text=1` 让结构判据能触发，判据改成
  //   `page=="活动地图"`；结构判据那一支再要求 ≥2 个白名条代号（免得把详情页的 OPERATION 标题
  //   误当活动地图）。代价：这一支多一次整屏 OCR（~1.5s），只在导航失败时付一次。
  if (!res.ok) {
    const wantZone = /-EX-/.test(code) ? 'EX' : '主区'
    const zone = await run(PY, ['-c',
      `import json,urllib.request,time;` +
      `cfg=json.load(open(r"data/nav_nodes.json",encoding="utf-8"));` +
      `pt=(cfg.get("zones") or {}).get("${PREFIX}",{}).get("${wantZone}");` +
      `a=json.loads(urllib.request.urlopen("http://127.0.0.1:8765/all?text=1",timeout=20).read().decode("utf-8"));` +
      `sc=float(a.get("page_score") or 0); codes=a.get("struct_codes") or [];` +
      `onmap=("活动地图" in str(a.get("page") or "")) and (sc>=0.8 or len(codes)>=2);` +
      `print("nozone", round(sc,3), len(codes), str(a.get("page"))[:40], "pt="+str(pt)) if (not pt or not onmap) else (urllib.request.urlopen(f"http://127.0.0.1:8765/tap?x={pt[0]}&y={pt[1]}").read(),time.sleep(3),print("zoned"))`],
      { timeoutMs: 60000, label: `zone ${PREFIX} ${wantZone}` })
    if (/zoned/.test(String(zone.out))) {
      log(`    导航失败 ⇒ 点了 ${PREFIX} 的 ${wantZone} 区域卡，重试一次`)
      const r2 = await run(PY, ['tools/mt_nav.py', '--stage', code, '--want', want, '--prefix', PREFIX],
        { timeoutMs: 900000, label: `mt_nav retry ${code}/${want}` })
      const l2 = r2.out.trim().split('\n').filter((l) => l.trim().startsWith('{')).pop()
      if (l2) { try { const j2 = JSON.parse(l2); if (j2.ok) return j2; res = j2 } catch { /* 保持原结果 */ } }
    }
  }
  return res
}

/** ③ 查三星（蓝 = 已通关/代理可用） */
async function probeStar() {
  const code = `import sys;sys.path.insert(0,'.');from tools.maa_job_fallback import verify_three_star;print('STAR=' + str(verify_three_star(${EMU})))`
  const r = await run(PY, ['-c', code], { timeoutMs: 180000, label: 'probe-star' })
  const m = r.out.match(/STAR=(True|False|None)/)
  return m ? { True: true, False: false, None: null }[m[1]] : undefined
}

/** ③b **报酬闸**（用户 2026-09-23 口径：「报酬里面没有源石就是没有就不用打了」）
 *
 *  为什么必须有它（实测教训，别再退回去）：`probeStar` 问的是"三星没有"，
 *  而**判据是"是否已通关"** —— 2 星通关的关早就把首通源石领走了。
 *  2026-09-23 我用"未三星"放闸，白跑了 MT-6/MT-7 两局（源石仍是 7、理智掉了 30）。
 *  `panel_gate.py` 直接判面板：报酬区有没有**未领的至纯源石徽章** + 标题栏三星记录。
 */
async function panelGate() {
  // **优先走常驻服务**（tools/maaorch_service.py）：它借 MaaCore 的 MuMu 截图通道，
  //   一次面板判读 ~60ms；原来每次 spawn 一个 python 跑 panel_gate.py 要 ~5s（冷启动 + 全屏 OCR）。
  //   服务没起 / 超时 / 报错就退回原路径，功能不受影响。
  try {
    const ctl = new AbortController()
    const timer = setTimeout(() => ctl.abort(), 4000)
    const res = await fetch(`http://127.0.0.1:${process.env.MAAORCH_SVC_PORT || 8765}/panel`, { signal: ctl.signal })
    clearTimeout(timer)
    if (res.ok) {
      const j = await res.json()
      if (j?.ok) return j
    }
  } catch { /* 服务没起 → 走老路 */ }
  const r = await run(PY, ['tools/panel_gate.py', '--emu', EMU], { timeoutMs: 120000, label: 'gate' })
  const line = String(r.out).trim().split('\n').filter((l) => l.trim().startsWith('{')).pop()
  try { return JSON.parse(line) } catch { return null }
}

/** **⊘（报酬无源石 ⇒ 不打）的判别器**（纯函数，`--selftest` 覆盖）。
 *
 *  ★ 2026-09-24 审计（`runtime/_audit_settle_mirror_20260924.md` §3）指出：全库 14 条 ⊘ 里
 *  **10 条落在歧义分数带 0.46–0.64**（「奖励箱真被领空」与「ROI 假阴性」同分），
 *  而旧的判决串只写「分 X；N 星、徽章已消失」，**不记判别器** ⇒ 事后无法把两者分开；
 *  同一条前缀（徽章 ROI）**已经造过一次假阴性**（账本 `_待验证`：CV-EX-1..8 旧 ⊘ 已回滚成 todo）。
 *  而 ⊘ 的代价是**跳过不打** ⇒ 假阴性丢的是永久首通源石。
 *  ⇒ 判据：**没有判别器就不许把 ⊘ 当可终态的「不打」**（降级 `⊘?（待判别）`，不进 noreach）。
 *
 *  判别器（据审计 §3.1 的既有口径，全部读**详情页文字**，三态：`null` = 没读到，绝不当 false）：
 *    · `reward_obtain_tag` —— 奖励物是否**仍带「获得」标记**（＝未领 ⇒ 有矿）；
 *    · `lock_raid` / `lock_auto` —— 「开始突袭」/「代理指挥」是否出现（出现＝**已解锁** ⇒ 已领侧）；
 *    · `mode` —— 普通 / 突袭（突袭与普通的领取状态互不影响，必须分开记）。
 *  返回 `{..., has, source}`：`has=false` 表示**这帧里一个判别器都没读到**（⇒ 不许判终态）。 */
function rewardDiscriminator(st, text, want) {
  const t = String(text || '')
  const seen = t.length > 0
  const obtain = !seen ? null : (/三星获得|已获得|首次掉落/.test(t) ? true : null)
  const lockRaid = !seen ? null : (/开始突袭/.test(t) ? 'unlocked' : null)
  const lockAuto = !seen ? null : (/代理指挥/.test(t) ? 'unlocked' : null)
  return {
    reward_obtain_tag: obtain,                 // true = 奖励物还带「获得」标记（未领、有矿）
    lock_raid: lockRaid,                       // 'unlocked' = 突袭已解锁
    lock_auto: lockAuto,                       // 'unlocked' = 代理指挥已解锁
    mode: want || null,
    page: st?.page ?? null,
    reward_score: st?.reward_score ?? (typeof st?.reward_score === 'number' ? st.reward_score : null),
    has: obtain !== null || lockRaid !== null || lockAuto !== null,
    source: seen ? 'detail_page_text' : 'none',
  }
}

/** **报酬栏判决 —— 全流程唯一实现**（⊘ 分支与结算复核两处共用）。
 *
 *   score >= present(0.75)      ⇒ 'present'（有矿：徽章还在）
 *   score <= absent(0.72)       ⇒ 'absent' （无矿：徽章不在了 ⇒ 该关首通源石已领）
 *   absent < score < present    ⇒ 'ambiguous'（窄带，说不准）
 *   分数缺失/非数                ⇒ 'ambiguous'（**读不到 ≠ 没有**）
 *
 *  ★ **低分不是"读数不可信"**：0.46–0.70 这批是**很确信的"不在"**（Lead 实测 70 条 no_prime
 *    0.461–0.702 与 20 条 done 0.950–1.000 在 (0.702, 0.950] 完全无重叠）。阈值只从
 *    `runtime/config/reward_gate.json` 读（出处 = `tools/panel_gate.py:28` 原文）。
 */
function rewardVerdict(score) {
  if (typeof score !== 'number' || !Number.isFinite(score)) return 'ambiguous'
  if (score < 0) return 'ambiguous'      // -1 = 模板/ROI 读不到（reward_has_prime 的哨兵）⇒ 不是"不在"
  if (score >= REWARD_PRESENT) return 'present'
  if (score <= REWARD_ABSENT) return 'absent'
  return 'ambiguous'
}

/** 面板（复核用）「徽章还在吗」三态：**优先用分数**（唯一判据），分数说不准才退回布尔。
 *  结算侧的复核走它；⊘ 侧走 `rewardVerdict` / `rewardNoPrimeGate`（同一个阈值来源）。 */
function badgeStillThere(g) {
  const v = rewardVerdict(g?.reward_score ?? null)
  if (v === 'present') return true
  if (v === 'absent') return false
  return typeof g?.reward_primes === 'boolean' ? g.reward_primes : null
}

/** **⊘/unreliable 行的可审计四件套**（纯函数，`--selftest` 覆盖）。
 *
 *  铁律（Lead 2026-09-24）：每条 ⊘/unreliable 行**必须**凑齐
 *    ① `reward_score`、② `reward_primes`（三态 true/false/**null**）、
 *    ③ 页面守卫结果（`page` + `page_guard`）、④ `stars`。
 *  实测现有 70 条 ⊘ 里 `reward_primes` 为 `null`（压根没采）**47 条**、`false` 23 条
 *  ⇒ 那 47 条以后一条都不许再产生（三态字段缺 = 事后无法审计，正是"⊘ 永远说不清"的根）。
 */
function rewardAuditFields(gate, stt) {
  const score = (typeof gate?.reward_score === 'number' && Number.isFinite(gate.reward_score))
    ? gate.reward_score : null
  const primes = (typeof stt?.reward_primes === 'boolean') ? stt.reward_primes
    : ((typeof gate?.reward_primes === 'boolean') ? gate.reward_primes : null)
  const stars = Number.isInteger(stt?.stars) ? stt.stars
    : (Number.isInteger(gate?.stars) ? gate.stars : null)
  const page = stt?.page ?? gate?.page ?? null
  return {
    reward_score: score,
    reward_primes: primes,                                  // 三态：缺就是 **null**（明写，不许省略键）
    stars,
    page,
    page_guard: page === '关卡详情' ? 'ok' : 'reject',      // 页面守卫结果（闸门可信度）
    reward_verdict: rewardVerdict(score),
  }
}

/** **⊘ 这一支该不该下终态**（纯函数，`--selftest` 覆盖）。
 *  终态（`action='no_prime'`，队列把单位记 done）**必须同时满足**：
 *    ① 报酬栏判决 = **无矿**（`score <= absent`）—— **只有"无矿"才允许进入 ⊘ 终态**；
 *    ② 至少一项判别器读到（`disc.has`）；
 *    ③ **内部一致性**：`stars` 是整数时不许 `< 3` —— **没通关的关不可能没有首通源石**
 *       （`stars<3` 却说"无源石" ⇒ 矛盾 ⇒ 这才是真正的坏读数探测器）。
 *  否则 ⇒ `action='unreliable'` + `needs_discriminator: true`（**不进 noreach、不降权**、
 *  保持 todo 复查）。`ambiguous`（分数缺 / 落 (absent, present) 窄带）一律走这条。
 *  删除的旧写法：`REWARD_AMBIG_LO/HI = 0.46/0.64`（从 ⊘ 观测簇反推的带 ⇒ 把确信的"不在"
 *  当"不可信"，⊘ 永远无法终结）。 */
function rewardNoPrimeGate(score, disc, opts = {}) {
  const verdict = rewardVerdict(score)
  const hasDisc = disc?.has === true
  const stars = Number.isInteger(opts.stars) ? opts.stars : null
  const contradiction = verdict === 'absent' && stars !== null && stars < 3
  return {
    terminal: verdict === 'absent' && hasDisc && !contradiction,
    verdict,                              // 'present' / 'absent' / 'ambiguous'
    ambiguous: verdict === 'ambiguous',
    absent: verdict === 'absent',
    hasDisc,
    contradiction,                        // stars<3 与"无矿"矛盾 ⇒ 坏读数
    stars,
  }
}

// ── 剧情看门狗（MAA 打关期间自动跳过剧情）───────────────────────────────
// 为什么必须有（2026-09-24 实测，task-3 的起因）：**从没打过的关会先播剧情** —— 屏幕一直是对话
//   （「拉芙希妮 ……抱歉了」+ 右上「自动 OFF / 跳过 ▶」），MAA 在等战斗界面 ⇒ 干等 273s 后判
//   `failed｜未成功`，**可关卡其实已经打完了**（结算页 `EA-1 行动结束` 2★）。
// 三条硬约束（目标是"极致快"，一个都不碰）：
//   · **不 spawn Python**：看门狗 = Node 里一个 setTimeout 自循环 + 一次 HTTP；
//   · **不全屏 OCR**：只问右上角那一小块（ROI 1050,20,1279,90，就找"跳过"两个字）；
//   · **不固定长 sleep**：tick 1.2s，MAA 一返回立刻 stop()。
// 优先 `/skipstory`（一次调用 = 检测 + 点「跳过 ▶」(1200,53) + 点确认 ⊙(796,462)，OCR 引擎常驻）；
// 服务是旧版、没有这个端点时退回 `/look?roi=…` + `/tap`（同样只一次小块 OCR，不 spawn 进程）。
const STORY = { n: 0, timer: null, stop: false, mode: '?', py: null }

function stopStoryWatch() {
  if (STORY.stop) return
  STORY.stop = true
  if (STORY.timer) clearTimeout(STORY.timer)
  if (STORY.py) { try { STORY.py.kill() } catch { /* 已经退出了 */ } }
  if (STORY.n) log(`🎬 剧情看门狗收工：共跳过 ${STORY.n} 次（通道 ${STORY.mode}）`)
}

async function storyTick() {
  if (STORY.stop) return
  // `src=adb`：取帧走 **adb screencap**，不碰 MaaCore/MuMu 私有通道 —— 这时 MAA 正在同一台
  //   模拟器上打关（它自己也用那条通道取帧），两条链路互不排队，避免把 MAA 的取帧挤坏。
  let j = await svcJson('/skipstory?src=adb', 3500)
  if (j) {
    STORY.mode = 'skipstory'
  } else {
    // `/skipstory` 没有（旧版服务）或这一次超时 ⇒ 用最便宜的 `/health` 判断"服务到底在不在"：
    //   在 ⇒ 自己拼"看小块 + 点跳过 + 点确认"（同样只有一次小块 OCR，**不 spawn**）；
    //   不在 ⇒ 才起那 1 个 Python 兜底进程。（别因为一次超时就起进程 —— 那不叫"不 spawn"了。）
    const alive = await svcJson('/health', 2000)
    if (!alive) {
      if (!STORY.py) {
        try {
          STORY.py = spawn(PY, ['tools/story_skip.py', '--minutes', String(RUN_TIMEOUT_MIN)],
            { cwd: ROOT, windowsHide: true, stdio: 'ignore' })
          STORY.py.on('error', () => {})
          STORY.mode = 'py-watchdog'
          log(`  🎬 常驻服务不可用 ⇒ 起了 1 个 Python 看门狗兜底（story_skip.py --minutes ${RUN_TIMEOUT_MIN}）`)
        } catch (e) { log(`  ⚠️ 看门狗兜底起不来：${e.message}`) }
      }
      return                                   // Python 接管后 Node 侧不再敲（避免两个看门狗抢着点）
    }
    STORY.mode = 'look+tap'
    const seen = await svcJson('/look?roi=1050,20,1279,90', 3500)
    const found = ((seen?.texts) || []).join('').includes('跳过')
    if (found) {
      await svcJson('/tap?x=1200&y=53', 3000)
      await sleep(800)
      await svcJson('/tap?x=796&y=462', 3000)
    }
    j = { found }
  }
  if (j.found) {
    STORY.n += 1
    const how = j.taps ? `（${j.taps}）` : ''
    log(STORY.n === 1
      ? `  🎬 发现剧情 ⇒ 跳过${how}（通道 ${STORY.mode}；之后每 ${STORY_MS}ms 复查一次）`
      : `  🎬 又发现剧情 ⇒ 跳过（第 ${STORY.n} 次）${how}`)
  }
  // 跳过一次之后放宽间隔：剧情就在关刚开打那一段（跳完就进战斗），战斗期间没必要 1.5s 敲一次
  //   （每敲一次要占一次 adb 截图）⇒ 之后按 4s 复查，仍能在下一段剧情出现后几秒内跳掉。
  const ms = STORY.n ? Math.max(STORY_MS, 4000) : STORY_MS
  if (!STORY.stop) STORY.timer = setTimeout(storyTick, ms)
}

function startStoryWatch() {
  if (!STORY_WATCH) { log('（--no-story-watch：本轮不装剧情看门狗）'); return }
  log(`🎬 剧情看门狗启动（每 ${STORY_MS}ms 只看右上角 1050,20,1279,90；不 spawn 进程、不全屏 OCR）`)
  storyTick()
}

/** ③c 在游戏画面上点一下（复核三星前必须先把结算页/好友弹层点掉）
 *  走 `tools/tap.py`：adb 路径与端口漂移都由它解析，Node 侧不重复那些烂事。 */
async function tapPy(x, y, wait = 2) {
  await run(PY, ['tools/tap.py', String(x), String(y), '--wait', String(wait), '--emu', EMU],
    { timeoutMs: (wait + 25) * 1000, label: `tap ${x},${y}` })
}

/** ④a 筛作业（落盘缓存 6 小时，与页面共用） */
async function ensureScreening(stage) {
  const cache = join(SCREEN_DIR, `${stage}.json`)
  const fresh = existsSync(cache) && Date.now() - statSync(cache).mtimeMs < 6 * 3600 * 1000
  if (fresh && readJson(cache)?.[stage]) return cache
  mkdirSync(SCREEN_DIR, { recursive: true })
  const box = latestOperbox()
  const r = await run(PY, ['tools/prts_job_finder.py', '--stages', stage, '--operbox', box.path,
    '--out', cache, '--top', '8'], { timeoutMs: 600000, label: `screen ${stage}` })
  if (!existsSync(cache)) throw new Error(`筛选失败：${r.out.slice(-300)}${r.err.slice(-200)}`)
  return cache
}

/** ④b 按难度**排序**取候选 —— 刻意不硬过滤。
 *
 * 为什么不硬过滤（2026-09-22 实测踩到）：**主线关的作业没有 difficulty 字段**（act42side_01
 * 的 8 份候选全是 null），硬过滤会把主线关全判成"零候选"、整段扫不动。
 * 而突袭本来就不是另一张图 —— 只多 3 条 rune（生命=1 / 攻防血×1.2 / 法抗+30，见 docs/MT_EX1_RAID.md），
 * 站位的对错与难度无关 ⇒ 挑错难度是"打法可能不匹配"，不是"跑不了"。
 * 所以：同难度排最前（普通=1 / 突袭=2），其次通用(3)、当前难度(0)、未知(null)，反向难度垫底。
 */
function rankByDifficulty(rows, want) {
  const exact = want === '突袭' ? 2 : 1
  const other = want === '突袭' ? 1 : 2
  const tier = (d) => (d === exact ? 0 : d === 3 ? 1 : d === 0 ? 2
    : (d === null || d === undefined) ? 3 : d === other ? 4 : 5)
  // 次级排序：**声明要求少的优先**（2026-09-24 加）。
  //   作业里写的 `requirements`（如「黍 专10 模1」）就是"这份轴假定你有专三+模组"；
  //   本号是**专精零**，要求越多的轴越可能只打到 2 星（2 星不发源石还照样扣理智）。
  //   ⇒ 同难度档里先试"零缺口/无要求/摆完挂机"的轴，把需要练度的轴放到后面。
  const reqs = (r) => (Array.isArray(r.requirements) ? r.requirements.length : 0)
    + (r.clean === false ? 1 : 0)
  return rows.map((r, i) => ({ r, i }))
    .sort((a, b) => tier(a.r.difficulty) - tier(b.r.difficulty) || reqs(a.r) - reqs(b.r) || a.i - b.i)
    .map((x) => x.r)
}

// ── 战绩账本：让"成功率"随跑的次数**自己往上走**（2026-09-24 加）
//
// 为什么要它：作业站的排序是"别人点的赞"，不是"**这个号**能不能三星"。
//   实测同一关好几次都是**第 2、3 份**才过（LE-EX-5/6、PV-EX-5/6），
//   而 MAA 自报的三星又不可信（PV-EX-3 报 3★、结算页只有 2 颗星）。
//   ⇒ 把"我们自己跑出来的真实结果"记进 `runtime/axis_outcomes.jsonl`：
//     某份作业在某关拿过 **panel_gate 认可的 3 星** ⇒ 下次优先它；
//     拿过 <3 星 ⇒ 降到队尾（除非所有候选都被降过）。
//   这样每跑一关，后面关卡的候选排序就更贴近本号练度。
const OUTCOMES = join(ROOT, 'runtime', 'axis_outcomes.jsonl')

function readOutcomes() {
  const map = new Map()            // `${stage}|${id}` -> {good, bad}
  try {
    for (const line of readFileSync(OUTCOMES, 'utf8').split('\n')) {
      if (!line.trim()) continue
      const o = JSON.parse(line)
      const k = `${o.stage}|${o.id}`
      const v = map.get(k) || { good: 0, bad: 0 }
      // **星数未知的行不参与提权/降权**（2026-09-24，审计 auditor-1 指出的污染）：
      //   旧代码把 MAA 自报的 `three_star` 写成 3/1 进账本，而 MAA 自报不可信
      //   （PV-EX-3 自报 3★、结算页只有 2 颗）⇒ 假 3★ 会把烂轴顶到队首。
      //   现在：`stars` 有值就按它升降；`stars: null` 只有**明确知道"没到三星"**（`not3: true`，
      //   例如徽章还在 ⇒ 源石没发 ⇒ 这套轴这关没成）才降权，纯未知（跑过但没读数）直接跳过。
      if (o.stars == null) {
        if (o.not3 === true) v.bad += 1
        else continue
      } else if (o.stars >= 3) v.good += 1
      else v.bad += 1
      map.set(k, v)
    }
  } catch { /* 还没有账本 */ }
  return map
}

/** **账本行构造**（纯函数，`--selftest` 覆盖）：task-17 issue#2 —— **没有证据就不写 stars/stone**。
 *  `stars === null/undefined` 一律写 null（"未知"，读账本的人跳过它），`stone` 只由调用方在
 *  真读到 ≥3★ 时通过 extra 传入。 */
function outcomeRow(stage, want, id, stars, note, opers, src, not3, extra) {
  const hasStars = stars !== null && stars !== undefined
  const row = {
    ts: new Date().toISOString(),
    op_id: `${stage}_${id}`,        // 与 tools/axis_rank.py 的 op_id 对齐
    stage, want, id,
    stars: hasStars ? stars : null,
    note: note || '',
    src: src || '',                 // 读数来源：drill / settle / panel / settle+panel / maa-selfreport
    // **"已知没到三星"**：星星没读出来、但徽章还在（源石没发）这类 —— 让读账本的人照样降权它
    ...(not3 === true ? { not3: true } : {}),
    ...(extra && typeof extra === 'object' ? extra : {}),   // mode/evidence/stone/valid（task-15/17）
    // **`opers` 的三态语义**（2026-09-24 Lead 指定；另一个 bot 的干员闸按它取数）：
    //   · 非空数组 = 采到了这套轴的干员名单；
    //   · `[]` / `undefined` ⇒ **写 `null`**：要么"没采到"、要么"采到了但为空"，
    //     都**不许被下游当成"知道这轴是空的"**（干员闸把 `[]` 当"确认空"会误判）；
    //   · 想区分"没采到"与"采到了但为空"看 `opers_state`（`not-collected` / `collected-empty`）。
    opers: Array.isArray(opers) && opers.length ? opers : null,
    opers_state: Array.isArray(opers) ? (opers.length ? 'collected' : 'collected-empty') : 'not-collected',
    opers_raw_len: Array.isArray(opers) ? opers.length : null,
  }
  // **【断言】演习行永远不带收益**（2026-09-24 用户口径 + Lead 要求）：演习只烧「演习券」，
  //   不消耗理智、**也永远不产出收益**（不发首通源石、不算通关）⇒ `mode='drill'` 的账本行里
  //   出现 `stone`（源石到手）就是上游写错了 —— 在这里抹掉，别让它污染账本/状态。
  //   （上游已经改成只走 `drillOutcomeRow()`；这条是**兜底断言**，selftest 有用例。）
  if (row.mode === 'drill' && 'stone' in row) delete row.stone
  return row
}

/** **演习的账本行**（唯一入口，`--selftest` 覆盖）：演习只更新"挑轴依据"（星数/证据/有效性），
 *  收益字段（`stone`/首通语义）一律不写 —— 上游就算传了也会被 `outcomeRow` 的断言抹掉。 */
function drillOutcomeRow(stage, want, id, stars, note, opers, evidence, valid, not3) {
  return outcomeRow(stage, want, id, stars, note, opers, 'drill', not3 === true ? true : undefined,
    { mode: 'drill', evidence, ...(valid ? {} : { valid: false }) })
}

function recordRow(row) {
  try {
    appendFileSync(OUTCOMES, JSON.stringify(row) + '\n', 'utf8')
  } catch { /* 记账失败不该打断 */ }
}

function recordOutcome(stage, want, id, stars, note, opers, src, not3, extra) {
  recordRow(outcomeRow(stage, want, id, stars, note, opers, src, not3, extra))
}

/** 读 `tools/axis_rank.py` 产出的 P(3星) 排序（没有就返回空 → 退回原来的启发式排序）。 */
function readAxisRank(stage, want) {
  try {
    const d = JSON.parse(readFileSync(join(ROOT, 'runtime', 'axis_rank.json'), 'utf8'))
    const list = d[`${stage}|${want}`]
    return Array.isArray(list) ? list : null   // ['act39side_ex07_44480', ...]
  } catch { return null }
}

/** 按"本号战绩"重排：拿过 3 星的提前，拿过 <3 星的挪后，其余保持原序。 */
function rankByOutcome(rows, stage) {
  const hist = readOutcomes()
  const score = (r) => {
    const v = hist.get(`${stage}|${r.id}`)
    if (!v) return 0                // 没跑过 → 中间
    if (v.good > 0) return -1       // 本号在这关用它能三星 → 最前
    return 1                        // 跑过但没三星 → 最后
  }
  return rows.map((r, i) => ({ r, i }))
    .sort((a, b) => score(a.r) - score(b.r) || a.i - b.i)
    .map((x) => x.r)
}

/** **取候选轴**（唯一入口）：难度档 → `axis_rank.py` 的 P(3星) → 本号战绩。
 *  演习优先（task-15）与老的"直接真打"两条路都用它；**按 关卡|难度 缓存**（task-17 issue#6：
 *  演习阶段算过一次，`drill_broken` 退回老路径时又算一次 ⇒ 重复 screening/账本全量读）。 */
const _PICK_CACHE = new Map()

async function pickAxes(stage, want) {
  const ck = `${stage}|${want}`
  if (_PICK_CACHE.has(ck)) return _PICK_CACHE.get(ck)
  const cache = await ensureScreening(stage)
  const rows = (readJson(cache)?.[stage]) || []
  let picked = rankByDifficulty(rows, want)
  // **P(3星) 排序**（用户 2026-09-24 的迭代训练指导）：tools/axis_rank.py 会用
  //   账号快照(缺口硬门槛) + 本号战绩 + 相似迁移 + 热度 + 探索加成算分，
  //   没有这份排序时才退回"难度+要求数+历史"的启发式。
  const ranked = readAxisRank(stage, want)
  if (ranked) {
    const pos = new Map(ranked.map((opId, i) => [String(opId), i]))
    picked = picked.map((r, i) => ({ r, i }))
      .sort((a, b) => (pos.get(`${stage}_${a.r.id}`) ?? 9999) - (pos.get(`${stage}_${b.r.id}`) ?? 9999) || a.i - b.i)
      .map((x) => x.r)
  }
  // **最后再用"本号战绩"落一次**（2026-09-24，task-15 实测需要）：`axis_rank.json` 是**快照**，
  //   而"刚刚演习漏怪 0★（59733）"是**刚发生的事实** ⇒ 不压过快照的话，同一关重跑会把刚泄漏过的
  //   轴再演习一遍（实测白烧 5 分钟）。所以：跑出过 3★ 的提前、跑过没 3★ 的沉底，其余保持上面的序。
  const out = rankByOutcome(picked, stage)
  _PICK_CACHE.set(ck, out)
  return out
}

/** **降级重试路径的写行策略（批次级、对称）** —— 纯函数，`--selftest` 覆盖。
 *
 *  Lead 2026-09-24 追加（现场证据 `runtime/axis_outcomes.jsonl`，本地时间）：
 *   ```
 *   11:53:32Z 59743 note=drill-invalid  src=drill valid=false        ⇒ 0
 *   12:00:00Z 59818 note=drill-invalid  src=drill valid=false        ⇒ 0
 *   12:00:54Z 59743 note=fallback-tried src=maa-selfreport           ⇒ -6  ★
 *   ```
 *   同批、同结果的两份轴**只有 59743 被罚**（第一次：41427/41415/51786 三行 `ts` 逐位相同、
 *   只修了一行）⇒ 这是**固有行为**：旧代码逐份问"这份是不是**最后试**的那一份"，同批同证据
 *   于是被一罚一放。而那次运行自己写着 `drill_broken=true / drill_why=all-drills-invalid`
 *   ⇒ **整条链已被判为坏掉**，这时写 −6 = "用坏链的失败去定轴的罪"。
 *
 *  ⇒ 规则（**判据只依赖该批共有的证据，同批要么全写、要么全不写**）：
 *    ① `chainBroken`（演习通道坏 / 连报告都没拿到）⇒ 每份轴一律写**中性**行
 *       `note='chain-broken'`（行里带 `chain_broken` / `drill_broken` / `drill_why`），
 *       **不带任何负向结论**；
 *    ② 否则 ⇒ 每份轴一律写中性行 `note='no-conclusion'`（＝"试过、但没拿到结论"）。
 *  口径与 `runtime/plans/_axis_weights.py` 一致：**没有结论不是失败**（权重 0），降权只能由
 *  结算页/面板的**正面失败证据**给（`settle_clear:false` / `sanity_refund`，见 settleFinish）。
 *  ⇒ 本路径**不再产生任何 `fallback-tried`（−6）行**；标签固定由该批共有的证据决定，
 *    与"这份轴在批里的位置"无关 ⇒ 同批同结果必然同判（对称）。 */
function fallbackRowPlan(triedIds, ctx = {}) {
  const ids = (triedIds || []).map(String)
  if (!ids.length) return []
  const chainBroken = ctx.chainBroken === true
  const note = chainBroken ? 'chain-broken' : 'no-conclusion'
  return ids.map((id) => ({ id, note, penalty: false, chainBroken }))
}

/** ④c 交给降级重试跑（跑不了自动换下一份，直到三星）—— **老的"直接真打"路径**
 *  （`--no-drill` 或演习通道整体不可用时才走；默认走 `drillPhase` 的演习优先）。
 *  `ctx.chainBroken`（＝ `rec.drill_broken`）为真时：整批只写中性 `chain-broken` 行。 */
async function runFallback(stage, want, extraArgs = [], maxTries = MAX_TRIES, ctx = {}) {
  const picked = await pickAxes(stage, want)
  if (!picked.length) return { verdict: 'no_candidate', candidates: 0, report: null, mix: {} }
  const mix = picked.reduce((m, r) => { const k = r.difficulty ?? 'null'; m[k] = (m[k] || 0) + 1; return m }, {})
  mkdirSync(REPORT_DIR, { recursive: true })
  mkdirSync(SCREEN_DIR, { recursive: true })
  const filtered = join(SCREEN_DIR, `${stage}__${want}.json`)
  writeFileSync(filtered, JSON.stringify({ [stage]: picked }, null, 1), 'utf8')
  const report = join(REPORT_DIR, `${stage}__${want}.json`)
  const head = picked.slice(0, maxTries)
  log(`    候选 ${picked.length} 份（难度分布 ${JSON.stringify(mix)}）→ 先试 ${head.map((r) => `prts://${r.id}(难度${r.difficulty ?? '?'})`).join(', ')}`
    + (maxTries !== MAX_TRIES ? `｜**只真打 ${maxTries} 份**（drill_broken 限流，见 --drill-broken-tries）` : ''))
  await run(PY, ['tools/maa_job_fallback.py', '--emu', EMU, '--max-tries', String(maxTries),
    '--out', report, '--stage', stage, '--screening', filtered, ...extraArgs],
    { timeoutMs: RUN_TIMEOUT_MIN * 60000, label: `fallback ${stage}/${want}` })
  const rep = readJson(report)
  const verdict = rep?.result?.startsWith('成功') ? 'done' : (rep ? 'failed' : 'no_report')
  // 记战绩：被 fallback 认可的轴交给 settleFinish 按结算页证据写（它有正面证据）；
  //   其余"试过的轴"一律写**中性**行（见 fallbackRowPlan 的注释）。
  const accepted = (rep?.result || '').match(/prts:\/\/(\d+)/)?.[1]
  const acceptedRow = picked.find((r) => String(r.id) === String(accepted))
  const tries = rep?.tries || []
  // **最后一次试的轴**：fallback 结束时屏上那份结算页就是它跑出来的那一局
  //   （`judgeAfterRun` 紧接着读的就是这个画面）⇒ 判定要记在它头上，见 settleFinish 的 ledgerId。
  const lastAxis = tries.length ? tries[tries.length - 1].id : null
  const lastRow = picked.find((r) => String(r.id) === String(lastAxis))
  // **批次级写行**（对称；判据只依赖该批共有的证据）：链坏 = 演习通道坏（调用方传）
  //   或连报告都没拿到（fallback 自己崩了）。整批一个标签，逐份一致。
  const chainBroken = ctx.chainBroken === true || !rep
  const plan = fallbackRowPlan(tries.filter((t) => String(t.id) !== String(accepted)).map((t) => t.id),
    { chainBroken })
  for (const p of plan) {
    // **绝不把 MAA 自报的 `three_star` 写进账本**（2026-09-24，auditor-1 指出）：MAA 自报不可信
    //   （PV-EX-3 自报 3★、结算页只有 2 颗）⇒ 一律 `stars: null` = "跑过、但没有可靠星数"；
    //   真星数只在结算页/面板复核时由 settleFinish 记。
    recordOutcome(stage, want, p.id, null, p.note,
      (picked.find((r) => String(r.id) === String(p.id)) || {}).used, 'maa-selfreport', false,
      p.chainBroken
        ? { chain_broken: true, drill_broken: ctx.drillBroken === true,
            drill_why: ctx.drillWhy ?? null }
        : { chain_broken: false })
  }
  if (plan.length) {
    log(`  📝 账本：本批 ${plan.length} 份轴**一律**写中性行 note=${plan[0].note}`
      + `（同批同证据 ⇒ 全写/全不写，**不写降权**${chainBroken ? '；链已判坏 ⇒ 禁止任何负向结论' : ''}）`)
  }
  return { verdict, candidates: picked.length, report: rep, mix, accepted,
    acceptedOpers: acceptedRow?.used || [], lastTried: lastAxis, lastOpers: lastRow?.used || [],
    rowPlan: plan, chainBroken }
}

// ── **演习优先**（task-15）：先 drill（0 理智）拿到 3★ 的轴，才真打 ──────────
//
// 用户铁律：**2★ 纯浪费理智、没有任何作用**（首通源石只认 3★）。而老路径是"挑 3 份轴
//   直接真打"，一份 2★ 就是 9~21 理智打水漂。Lead 手工那套"先演习试轴（0 理智）→ 3★ 才真打"
//   实测拿到 EA-1 与 CV-EX-8 两颗源石 ⇒ 接进流水线，让队列自己省理智。
//
// 判定与 task-3 同源：**看结算页的 `result_stars`**（演习与真打的结算页是同一个页面），
//   读不到才退回 `stage_attempt.py` 自己数的「星级=N」/ `--json-out` 的 JSON。

/** **演习决策**（纯函数，`--selftest` 直接跑）：给各轴的演习读数 → 下一步干什么。
 *   drills = [{id, stars, valid, not3}]
 *     · `valid` = 拿到结算页读数；`not3` = **有证据证明这轴到不了 3★**（结算 <3★，或进战斗后漏怪被掐）
 *   · 有 valid 且 stars===3 的轴 ⇒ `real`（真打**它**，只真打一次）；
 *   · 有"到不了 3★"的证据（valid 或 not3）⇒ `no_axis`（**一份都不真打 ⇒ 零理智**，换下一关）；
 *   · 完全没有结论（闸1/超时/没进战斗/MAA 报错）⇒ `drill_broken`
 *     （技术上不可用，默认退回老的"直接真打"路径，且**限流**；`--drill-strict` 则不退）。
 *
 *  ⚠️ 为什么把 `not3` 单列（2026-09-24 真机教训）：AD-EX-2 有 4 份轴**进战斗后漏怪被掐**
 *  （`--abort-on-leak`）——那是"这份轴不行"的**有效结论**（零理智），但旧分类把它算成
 *  "通道坏" ⇒ 全轴"无效" ⇒ 退回老路径**盲目真打**了 3 份我们已经知道会漏怪的轴。 */
function drillDecision(drills) {
  if (!Array.isArray(drills) || !drills.length) return { action: 'no_axis', axis: null, why: 'no_candidate' }
  const hit = drills.find((d) => d.valid === true && d.stars === 3)
  if (hit) return { action: 'real', axis: hit.id, why: 'drill-3star' }
  if (drills.some((d) => d.valid === true || d.not3 === true)) {
    return { action: 'no_axis', axis: null, why: 'all-drills-under-3' }
  }
  // **「没券」不是「通道坏」**（2026-09-24 加，用户口径）：演习券不足造成的"没跑出结论"必须
  //   单独成一类 `no_tickets` ⇒ 走老的"直接试轴"路径（正常 MAX_TRIES），**不许**归成
  //   `drill_broken`（那会按"通道不可用"限流真打、账也记错，正是用户说的"白烧一次真打"）。
  if (drills.some((d) => d.notickets === true)) {
    return { action: 'no_tickets', axis: null, why: 'drill_tickets_low' }
  }
  return { action: 'drill_broken', axis: null, why: 'all-drills-invalid' }
}

/** **这个单位要不要跑演习**（纯函数，`--selftest` 覆盖）——决策入口，别再在流程里散写 `DRILL_FIRST`。
 *
 *  为什么要有它（2026-09-24 真机教训，CV-EX-8|突袭）：演习优先对**突袭**不成立 ——
 *  `stage_attempt --drill --raid` 两份轴都只跑出 `gate3-invalid`（MAA 报 Completed、没有结算页），
 *  还把游戏留在「演习中 + PAUSE」的战斗里。task-15 的交付说明末尾自己预警过这条，
 *  实测确认后按那条建议办：**突袭走老路径**（`--raid-drill` 是唯一的例外开关，默认关）。
 *  返回 `{drill: bool, why}`；`why`：`drill-first` / `raid-no-drill` / `drill_tickets_low` / `no-drill`。 */
function drillPlan(want, { drillFirst = DRILL_FIRST, raidDrill = DRILL_RAID,
  tickets = undefined, need = DRILL_TRIES } = {}) {
  if (!drillFirst) return { drill: false, why: 'no-drill' }
  if (want === '突袭' && !raidDrill) return { drill: false, why: 'raid-no-drill', need }
  // **演习券不够 ⇒ 不演习**（2026-09-24 用户口径：演习只烧「演习券」，每份轴 1 张）。
  //   读数**只有真的读到数字**（`tickets` 是整数）才拦；读不到（null/undefined）⇒ 照常演习
  //   （日志记 null，**不瞎猜**：把"读不到"当"没券"会让所有关都打不了演习）。
  //   ⚠️ 这里的 `why='drill_tickets_low'` 是**独立原因**：它走老的"直接试轴"路径，
  //   绝不能被记成 `drill_broken`（"演习通道坏"会触发限流真打，账也会记错）。
  if (Number.isInteger(tickets) && tickets < need) {
    return { drill: false, why: 'drill_tickets_low', tickets, need }
  }
  return { drill: true, why: 'drill-first', tickets: Number.isInteger(tickets) ? tickets : null, need }
}

/** **这一局要不要 sweep 侧再补一次"离开战斗"的收尾**（纯函数，`--selftest` 覆盖）。
 *
 *  正常情况由 `stage_attempt.py` 自己收尾（它的 `leave_battle()`：齿轮 → 找「放弃行动」→ 确认）。
 *  这里只兜三种它兜不到的：
 *    ① `battle_after === true` —— 它收尾了，但结果帧**仍在战斗 HUD**（真机 51786 就是这种）；
 *    ② `still_battle === true` 且 `left_battle !== true` —— 旧版 `stage_attempt.py` 压根没这两个字段
 *       （灰度期兼容：机器上还跑着旧代码时，sweep 侧照样能救回来）；
 *    ③ `timedOut` —— sweep 把 python 掐了 ⇒ 收尾代码没机会跑。
 *  返回原因字符串；`null` = 什么都不用做（不要在正常画面上乱点）。 */
function needBattleRecovery(j, timedOut = false) {
  if (timedOut) return 'run-killed'
  if (!j || typeof j !== 'object') return null
  if (j.battle_after === true) return 'still-in-battle'
  if (j.still_battle === true && j.left_battle !== true) return 'cleanup-failed'
  return null
}

/** 把一个 prts 作业落到本地，给 `stage_attempt.py --job` 用。
 *  **复用 `maa_job_fallback.fetch_job()`**（唯一来源，别再写一份下载逻辑）。 */
async function ensureJobFile(id) {
  const dir = join(ROOT, 'runtime', 'plans', 'drill_jobs')
  const dst = join(dir, `job_${id}.json`)
  if (existsSync(dst)) return dst
  mkdirSync(dir, { recursive: true })
  const code = 'import sys,pathlib;sys.path.insert(0,r"' + join(ROOT, 'tools') + '");'
    + `from maa_job_fallback import fetch_job;print("JOB="+str(fetch_job(${id}, pathlib.Path(r"${dir}"))))`
  const r = await run(PY, ['-c', code], { timeoutMs: 120000, label: `job ${id}` })
  const m = String(r.out).match(/JOB=(.+)/)
  return m && existsSync(m[1].trim()) ? m[1].trim() : null
}

/** **多帧星数取最大**（纯函数，`--selftest` 覆盖）：星星逐颗亮起 ⇒ 读到 2★ 不能就下结论，
 *  复读一帧取较大者。`null`（读不到）不参与。 */
function maxStars(list) {
  const vals = (list || []).filter((x) => x !== null && x !== undefined)
  return vals.length ? Math.max(...vals) : null
}

/** **结算页读数 —— 唯一口径**（task-17 issue#1）：短轮询等结算页出现 + 星数复读取大（动画）。
 *
 *  演习与真打**必须共用这一条**：原来演习侧只看单帧 `===3`，读糊一次就换轴（可能漏掉真 3★）。
 *  返回 `settleFrom()` 的结构（`stars` 可能是 null = 没有结算页/没读到），拿不到就是 null。 */
async function readSettleConfirmed(shotPath = '', opts = {}) {
  let set = settleFrom(await readAll(shotPath))
  for (let i = 0; i < 4 && !set; i++) {
    await sleep(900)
    set = settleFrom(await readAll())
  }
  if (set && set.stars !== null && set.stars < 3) {
    await sleep(1100)
    const b = settleFrom(await readAll())
    const best = maxStars([set.stars, b?.stars ?? null])
    if (best !== null && b && best > set.stars) {
      log(`  👁 星数复读：${set.stars}★ → ${best}★（第一帧赶上逐颗亮起的动画）⇒ 取较大者`)
      set = b
    }
  }
  // **奖励栏冗余证据**（2026-09-24 误判修复）：确认是结算页之后**再**付一次整帧 OCR 的钱
  //   （~0.4~1.5s/单位），拿「首次掉落」/「理智返还」那两行字 —— 1900 那张真通关就是
  //   星区 0 蓝像素 + 没人看文字才被判死的。拿不到就记 unknown（判定会自动变保守）。
  if (set && opts.wantText) {
    const t = await readAll('', { text: true })
    if (t && t.page === '结算页') set = { ...set, ...settleTextEvidence(t.text) }
    else log('  （奖励栏 OCR 没取到 / 页面已变 ⇒ 文字证据记 unknown：**不会因此判 failed**）')
  }
  if (set) {
    log(`  👁 结算页已确认（页面分 ${set.score}）｜星数读数：${set.stars ?? '?'}★`
      + `（六边形亮蓝像素 ${(set.blue || []).join('/') || '?'}${shotPath ? `，帧存 ${shotPath}` : ''}）`)
    if (opts.wantText) {
      log(`  🧾 奖励栏证据：首次掉落=${set.firstDrop === null ? '未知' : (set.firstDrop ? '有' : '无')}`
        + `｜理智返还=${set.sanityRefund === null ? '未知' : (set.sanityRefund ? '有' : '无')}`
        + `（0★ + 有首次掉落 ⇒ 已通关；只有「理智返还」才判 failed）`)
    }
    if (set.stars === null) {
      log('  ⚠️ 结算页认出来了但服务没给 result_stars（服务版本旧？）⇒ 星数不可用')
    }
  }
  return set
}

/** 从 `runtime/_ce6/<tag>.attempt.log` 里读「结算：页面类型=X  星级=N」（`/all` 读不到时的兜底）。
 *  ⚠️ **必须取最后一条**（task-17 issue#3）：stage_attempt 的日志是 `open(...,"a")` **追加**写，
 *  同一个 tag 在同一分钟里重跑就会有两段 ⇒ 取第一条会读到**上一局**的星数。
 *  `sinceSize` 是开跑前记录的日志字节数，只解析**本次新增**的那段（更保险）。 */
function starsFromAttemptLog(tag, sinceSize = 0) {
  try {
    const p = join(ROOT, 'runtime', '_ce6', `${tag}.attempt.log`)
    const buf = readFileSync(p, 'utf8')
    const tail = sinceSize > 0 && buf.length > sinceSize ? buf.slice(sinceSize) : buf
    const all = [...tail.matchAll(/结算：页面类型=(\S+)\s+星级=(\d+)/g)]
    if (!all.length) return { kind: null, stars: null }
    const m = all[all.length - 1]
    return { kind: m[1], stars: parseInt(m[2], 10) }
  } catch { return { kind: null, stars: null } }
}

/** **屏上文字像不像"战斗/暂停态"**（纯函数，`--selftest` 覆盖）——与 `stage_attempt.py` 的
 *  `battle_hud_text()` 同一口径、两处共用同一组判据。
 *  用它来回答"这次导航失败，是不是因为游戏被留在战斗里"。 */
function battleTextHint(t) {
  const s = String(t || '').replace(/\s+/g, '').toUpperCase()
  if (!s) return false
  if (s.includes('PAUSE') || s.includes('暂停中')) return true
  // 战斗暂停菜单**独有的按钮文案**（点齿轮/PAUSE 后的覆盖层）
  if (s.includes('继续战斗') || s.includes('放弃行动')) return true
  return s.includes('演习中') && s.includes('剩余可放置角色')
}

/** 让常驻服务整屏 OCR 一次，拿屏上文字（`/panel?text=1`；服务是旧版或不在 ⇒ null）。 */
async function readScreenText() {
  const j = await svcJson('/panel?text=1', 6000)
  return j?.text ?? null
}

/** **离开战斗的收尾**（真调用）：走 `stage_attempt.py --leave-battle` —— 同一份实现，不重写第二遍。
 *  起因见 `DRILL_RAID` 的注释：游戏被留在「演习中 + PAUSE」的战斗里时，**所有**导航都会失败。 */
async function leaveBattle(tag, why) {
  const jsonOut = join(ROOT, 'runtime', 'reports', `leavebattle_${tag}.json`)
  log(`      🚪 收尾：把游戏带出战斗/暂停态（${why} ⇒ tools/stage_attempt.py --leave-battle）`)
  await run(PY, ['tools/stage_attempt.py', '--leave-battle', '--tag', `${tag}_leave`, '--json-out', jsonOut],
    { timeoutMs: 180000, label: 'leave-battle' })
  const j = readJson(jsonOut)
  if (!j) { log('      ⚠️ 收尾没拿到 JSON（工具没起来？）⇒ 后面的导航可能还会失败'); return null }
  log(j.left_battle === true
    ? `      ✓ 已离开战斗（放弃/退出命中 ${j.abandon_hits ?? '?'} 次）`
    : `      ⚠️ **没能确认离开战斗**（命中 ${j.abandon_hits ?? '?'} 次）⇒ 下一关导航大概率失败，请人工看一眼`)
  return j
}

/** **尽力读「演习券」**（真调用）：走 `stage_attempt.py --read-tickets` —— ROI 与解析都在那边
 *  （单一来源，不在这边重写一份 OCR）。**读不到就是 null**（照常演习、日志记 null，不瞎猜）。 */
async function readDrillTickets(tag) {
  const jsonOut = join(ROOT, 'runtime', 'reports', `tickets_${tag}.json`)
  await run(PY, ['tools/stage_attempt.py', '--read-tickets', '--tag', `${tag}_tickets`, '--json-out', jsonOut],
    { timeoutMs: 120000, label: 'read-tickets' })
  const j = readJson(jsonOut)
  return { tickets: j && Number.isInteger(j.drill_tickets) ? j.drill_tickets : null,
    low: j?.drill_tickets_low === true, text: String(j?.tickets_text || '') }
}

/** 跑**一局**（演习或真打同一套）→ `{id, code, valid, stars, kind, why}`。
 *  星数优先级：**结算页 `result_stars`**（task-3 的地面真值，且与真打同一口径）> `--json-out` 的 stars > attempt.log。
 *  **闸1 失败（人不在详情页）时自己重导航一次再重试这一局**（task-17 issue#4：不再"每局都先全量重导航"）。 */
async function runAttempt(stage, want, id, opts) {
  const { drill, tag, code = '' } = opts
  const out = { id, mode: drill ? 'drill' : 'real', tag, job: null, code: null, json: null,
    valid: false, stars: null, kind: null, why: '', evidence: 'none' }
  out.job = await ensureJobFile(id)
  if (!out.job) { out.why = 'no_job_file'; return out }
  const jsonOut = join(ROOT, 'runtime', 'reports', `attempt_${tag}.json`)
  const logPath = join(ROOT, 'runtime', '_ce6', `${tag}.attempt.log`)
  for (let round = 0; round < 2; round++) {
    const sinceSize = existsSync(logPath) ? statSync(logPath).size : 0   // 只解析本次新增的日志段
    const args = ['tools/stage_attempt.py', '--job', out.job, '--tag', tag,
      '--job-timeout', String(DRILL_TIMEOUT_MIN * 60), '--json-out', jsonOut]
    if (want === '突袭') args.push('--raid')
    if (drill) { args.push('--drill'); if (!DRILL_RECORD) args.push('--no-record') }
    const r = await run(PY, args, { timeoutMs: (DRILL_TIMEOUT_MIN * 60 + 180) * 1000, label: `${out.mode} ${id}` })
    out.code = r.code
    out.json = readJson(jsonOut)
    const lg = starsFromAttemptLog(tag, sinceSize)
    const jStars = out.json && Number.isInteger(out.json.stars) ? out.json.stars : null
    const set = await readSettleConfirmed()          // ← 与真打**同一条读数**（task-17 issue#1）
    out.kind = out.json?.kind ?? lg.kind ?? null
    out.stars = set?.stars ?? jStars ?? lg.stars ?? null
    if (set && set.stars !== null && jStars !== null && set.stars !== jStars) {
      log(`    ⚠️ 星数两口径不一致：结算页 result_stars=${set.stars} vs stage_attempt 星级=${jStars}（**以结算页为准**）`)
    }
    out.valid = out.json ? out.json.valid === true
      : (out.stars !== null && ['clear', 'maybe-clear', 'fail'].includes(String(out.kind)))
    out.why = out.json?.reason || (out.valid ? 'ok' : 'no-settlement')
    // **"这轴到不了 3★"的有效证据**（task-15/17 真机教训）：
    //   ① 结算页读到 <3★；② 进过战斗但**漏怪被掐**（--abort-on-leak）—— 后者没有结算页，
    //   但结论同样确定（泄露了就拿不到 3★）⇒ 算 `not3`，让决策**换轴**而不是"通道坏⇒盲目真打"。
    out.leaked = out.json?.leak === true
    out.battle = out.json?.battle === true
    // **演习券**（2026-09-24）：`stage_attempt` 在点演习前后各读一次；这局是不是因为"没券"没跑成
    //   —— 这个信号决定了上层走"老路径（正常重试）"而不是把它当"通道坏"。
    out.tickets = Number.isInteger(out.json?.drill_tickets) ? out.json.drill_tickets : null
    out.tickets_low = out.json?.drill_tickets_low === true
    out.not3 = (out.valid && out.stars !== null && out.stars < 3) || out.leaked
    // 证据来源：有结算页星数 ⇒ settle；只有 json/log 的星 ⇒ attempt；漏怪 ⇒ leak；都没有 ⇒ none
    out.evidence = set && set.stars !== null ? 'settle'
      : (out.leaked ? 'leak' : (out.stars !== null ? 'attempt' : 'none'))
    // **收尾：绝不把游戏留在战斗/暂停态**（2026-09-24 修复）——
    //   放在闸1 重试**之前**：闸1 判"不在详情页"最常见的原因就是上一局把游戏留在了战斗里
    //   （真机 CV-EX-8 突袭 51786 的第一次就是这样：detail=False，而屏上正在演习战斗）。
    //   正常由 `stage_attempt.py` 自己收尾，这里只兜它兜不到的（见 `needBattleRecovery` 的注释）。
    const need = needBattleRecovery(out.json, r.timedOut)
    if (need) out.leave_battle = await leaveBattle(tag, need)
    // **闸1 = 人不在关卡详情页** ⇒ 重导航一次再重试同一局（只重试一次，别变成"从头再来"循环）
    if (out.why === 'gate1-not-detail' && round === 0) {
      log('    ⟳ 闸1：不在关卡详情页 ⇒ 重导航一次后重试本局（只重试一次）')
      const nav = code ? await navTo(code, want) : { ok: false, detail: '调用方没给代号' }
      if (!nav?.ok) {
        log(`    ⚠️ 重导航失败（${nav?.detail || '?'}）⇒ 不再重试，如实记无效`)
        break
      }
      continue
    }
    break
  }
  return out
}

/** **每关的演习阶段**：按轴序 drill（0 理智），返回
 *  `{action:'real'|'no_axis'|'drill_broken', drills, axis, why}`。
 *  跑局前**不再**做全量重导航（task-17 issue#4）：交给 `runAttempt` 的"闸1 失败才重导航一次"，
 *  这样一关只会在真的没到位时导航，而不是每份轴都从头导航一遍。 */
async function drillPhase(u, rec, tagBase) {
  const picked = await pickAxes(u.stage, u.want)
  if (!picked.length) return { action: 'no_axis', drills: [], axis: null, why: 'no_candidate' }
  const picks = picked.slice(0, DRILL_TRIES)
  const drills = []
  log(`  🧪 演习优先：按轴序先试 ${picks.length}/${picked.length} 份（**0 理智**，每份最多 ${DRILL_TIMEOUT_MIN} 分钟）`)
  for (const [i, row] of picks.entries()) {
    const d = await runAttempt(u.stage, u.want, row.id,
      { drill: true, tag: `${tagBase}_drill_${row.id}`, code: u.code })
    drills.push({ id: row.id, stars: d.stars, kind: d.kind, valid: d.valid, not3: d.not3,
      leaked: d.leaked, code: d.code, why: d.why, evidence: d.evidence, tag: d.tag,
      tickets: d.tickets, notickets: d.tickets_low })     // 「没券」≠「通道坏」，单列给决策用
    // 账本：**演习读数**（mode=drill）。task-17 issue#2：**无效局不写 stars**，只写 mode/verdict/evidence
    //   （没有结算页证据的星数是垃圾，写进去事后没法审计）；3★ 提权、<3★/漏怪降权、无结论不参与。
    //   ★ 2026-09-24：唯一入口是 `drillOutcomeRow()` —— **演习不产收益**（不写 stone/首通语义）。
    const ledgerStars = d.valid ? d.stars : null
    const note = d.valid ? `drill-${d.stars}star`
      : (d.leaked ? 'drill-leak-not3' : (d.tickets_low ? 'drill-tickets-low' : 'drill-invalid'))
    recordRow(drillOutcomeRow(u.stage, u.want, row.id, ledgerStars, note, row.used,
      d.evidence, d.valid || d.leaked, d.not3))
    const verdict = d.valid ? `${d.stars}★（${d.kind}）`
      : (d.leaked ? `漏怪被掐 ⇒ 不可能 3★（t≈${d.json?.leak_s ?? '?'}s，证据 leak）` : `无结论（${d.why}）`)
    log(`    演习 ${i + 1}/${picks.length} prts://${row.id} ⇒ ${verdict}`
      + (d.valid && d.stars === 3 ? ' ✓ 达标' : '')
      + (d.tickets !== null ? `｜演习券 ${d.tickets}` : ''))
    if (d.valid && d.stars === 3) {
      log('    🎯 演习 3★ ⇒ 用**同一轴**真打（真打只发生一次）')
      return { action: 'real', drills, axis: row, why: 'drill-3star' }
    }
    if (d.not3) log('    （这轴确定到不了 3★ ⇒ **不真打**，换下一轴 —— 省理智就省在这）')
    if (d.tickets_low) {
      // **没券就别再试了**（2026-09-24）：演习券不足是"资源不够"，不是"通道坏" ⇒ 停止演习、
      //   把这条单位交给**老的直接试轴路径**，并且**不记 drill_broken**（那会白烧一次真打）。
      log('    🎫 演习券不足 ⇒ 停止演习：这不是"演习通道坏"，交给老的直接试轴路径（如实记 drill_tickets_low）')
      break
    }
  }
  const dec = drillDecision(drills)
  return { action: dec.action, drills, axis: null, why: dec.why }
}

/** **结算页 → 判定**（纯函数，`--selftest` 直接跑，不用模拟器）。
 *
 *  ★ 2026-09-24 大修（误判事故：真通关被读成 0★/理智返还 ⇒ 记 failed + 降权赢的轴 + 漏登源石）：
 *    **冗余证据优先于像素星数；`failed` 必须有正面证据。**
 *  判据（按优先级，每一条都写进 `evidence` 落账，不静默拍板）：
 *    ① **没有结算页**（`onSettlePage:false`）⇒ 保持老兜底：MAA 判 done ⇒ done（注明读数缺失）、
 *       否则 failed —— 这条只覆盖"屏上真没有结算页"的情形；结算页在就一律走 ②~⑥。
 *    ② **唯一的 failed 通道（正面失败证据）**：奖励栏有「理智返还」且**没有**「首次掉落」、且不是演习局；
 *    ③ 演习局（文字含「演习」）⇒ 不判胜负（演习不产出收益、也不是真打的战果）；
 *    ④′ 徽章仍在（面板 `reward_primes === true`）**且没有首通文案** ⇒ 源石没发 ⇒ 最多 partial；
 *    ④″ 徽章仍在 **且** 有首通文案（两个证据冲突）⇒ **以游戏印的文案为准** ⇒ done+源石，
 *       并落 `conflict: 'badge-remains-vs-firstdrop'`（显式冲突，不静默按徽章拍板）；
 *    ⑤ **首通奖励在（「首次掉落」）⇒ 已通关**：≥3★ ⇒ done+源石；**突袭 ⇒ done+源石**（无星级评价）；
 *       1~2★ ⇒ partial（没到三星）；**星数读不出来 ⇒ partial「已通关·星数未知」，绝不判 failed**；
 *    ⑥ 星数读数本身：≥3★ ⇒ done；1~2★ ⇒ partial；**0★ 或读不到、又没有「理智返还」⇒ 证据不足
 *       （`clear:null`）⇒ partial「待复核」，**不判 failed、不降权**。
 *  返回 `{action, stars, src, why, not3, clear, evidence, stone?}`：
 *    · `clear === true` 有正面通关证据、`false` 有正面失败证据、`null` 证据不足（不判）；
 *    · `not3 === true` 只给**有证据"到不了三星"**的行（读账本的人据此降权），证据不足一律 false。
 */
function settleVerdict(sig) {
  const S = sig || {}
  const stars = Number.isInteger(S.stars) ? S.stars : null
  const panelStars = Number.isInteger(S.panelStars) ? S.panelStars : null
  const badgeRemains = S.panelPrimes === true
  const firstDrop = S.firstDrop === true
  const refund = S.sanityRefund === true
  const drill = S.drillFrame === true
  const maaDone = S.maaDone === true
  const raid = S.want === '突袭'
  const evidence = {
    settle_stars: stars, settle_lit: Array.isArray(S.lit) ? S.lit : null,
    settle_blue: Array.isArray(S.blue) ? S.blue : null,
    first_drop: S.firstDrop ?? null, sanity_refund: S.sanityRefund ?? null,
    drill_frame: S.drillFrame ?? null, text_seen: S.textSeen ?? null,
    panel_stars: panelStars, badge_remains: badgeRemains, maa_done: maaDone,
    want: S.want ?? null, page_score: S.score ?? null,
  }
  const out = (action, s, src, why, not3, clear, extra) =>
    ({ action, stars: s, src, why, not3, clear, evidence, ...(extra || {}) })

  // ① 屏上根本没有结算页 ⇒ 老兜底口径（真正的判据在 judgeAfterRun 的面板分支里）
  if (S.onSettlePage === false) {
    return out(maaDone ? 'done' : 'failed', null, 'none',
      maaDone ? 'maa-done-no-read' : 'maa-failed-no-read', false, null)
  }
  // ② 唯一的 failed 通道：**正面**失败证据（理智返还 + 没有首通奖励 + 不是演习局）
  if (refund && !firstDrop && !drill) return out('failed', stars, 'settle', 'sanity-refund', true, false)
  // ③ 演习局的结算页：不判真打胜负
  if (drill) return out('partial', stars, 'settle', 'drill-frame', false, null)
  // ④′ 徽章仍在、**且没有正面首通证据** ⇒ 才按「源石没发」降权
  //   （★ 2026-09-24 Lead 复核审计 `runtime/_audit_settle_mirror_20260924.md` §2 后改：
  //    旧写法 ④ 排在 ⑤ 之前、且只看徽章 ⇒ **一旦 `panelPrimes===true` 就永不走 ⑤**，
  //    正面首通证据被吞、还带 `not3:true`（＝降权）—— 与这次要消灭的伤害同型。
  //    而 19:00 那条真通关的字段形状里 `reward_primes` **恰好是 true** ⇒ 旧顺序下仍是 partial/降权。
  //    另：徽章这个 ROI **有假阴性前科**（账本 `_待验证`：「CV-EX-1..8 旧的 ⊘ 是徽章 ROI bug
  //    造成的假阴性，已回滚成 todo」）⇒ 不许让"读数可能是坏的"字段否决"游戏自己印的文案"。）
  if (badgeRemains && !firstDrop) return out('partial', stars, 'settle+panel', 'badge-remains', true, null)
  // ④″ 两个证据**冲突**（徽章仍在 + 首通文案）⇒ 以游戏印的文案为准 ⇒ done+源石，
  //   但**必须显式标冲突落账**（`conflict` 进账本/报告），不许静默按徽章拍板。
  if (badgeRemains && firstDrop) {
    const s = (stars !== null && stars >= 1) ? stars : (panelStars ?? null)
    return out('done', s, 'settle+panel', 'firstdrop-badge-conflict', false, true,
      { stone: true, conflict: 'badge-remains-vs-firstdrop' })
  }
  // ⑤ 首通奖励在 ⇒ 已通关
  if (firstDrop) {
    if (stars !== null && stars >= 3) return out('done', stars, 'settle', 'settle-firstdrop-3star', false, true, { stone: true })
    if (raid) return out('done', stars === 0 ? null : stars, 'settle', 'raid-first-drop', false, true, { stone: true })
    if (stars !== null && stars >= 1) return out('partial', stars, 'settle', `settle-${stars}star`, true, true)
    if (panelStars !== null && panelStars >= 3) return out('done', 3, 'settle+panel', 'settle+panel-3star', false, true, { stone: true })
    return out('partial', null, 'settle', 'firstdrop-unknown-stars', false, true)
  }
  // ⑥ 没有首通证据 ⇒ 星数说话（0★/读不到一律不判 failed）
  if (stars !== null && stars >= 3) return out('done', stars, 'settle', 'settle-3star', false, true)
  if (stars !== null && stars >= 1) return out('partial', stars, 'settle', `settle-${stars}star`, true, true)
  if (panelStars !== null && panelStars >= 3) return out('done', 3, 'panel', 'panel-3star', false, true)
  return out('partial', null, 'settle', 'settle-no-evidence', false, null)
}

/** **跑完之后怎么判**（老路径与"演习优先"共用这一段）：读结算页星数 → `settleFinish` 定生死。
 *  抽成函数是为了 task-15：演习优先的真打结果与老的降级重试结果都走同一条判定，
 *  避免"两套代码两套口径"（2★ 被谁判成 done 都是灾难）。 */
async function judgeAfterRun(u, rec, fb) {
  rec.action = 'run'
  rec.verdict = fb.verdict
  rec.candidates = fb.candidates
  rec.difficulty_mix = fb.mix
  rec.tried = (fb.report?.tries || []).map((t) => ({ id: t.id, verdict: t.verdict, three_star: t.three_star }))
  rec.result = fb.report?.result || ''
  log(`  判定：${fb.verdict}｜${rec.result || ''}`)
  // **复核：首通源石要"三星"才发**（2026-09-23 当天两次更正后的定论）
  //   徽章上印的就是「三星获得」；实测 EP-EX-1 打通只拿 2 星 ⇒ 源石没涨、徽章还在 ✗
  //   ⇒ 只有 **≥3 星** 才算 done；"已通关但 1~2 星"只记 partial，留着下次补三星拿源石。
  // ★ 2026-09-24（task-3）大修：**先看屏上是不是结算页，并读那三颗六边形**。
  //   起因就是这条命令：`--only EA-1` 那次从没打过的关先播剧情，MAA 干等 273s 判 `failed`，
  //   可**关卡其实打完了**（结算页 `EA-1 行动结束` 2★）—— 旧代码在失败分支读 `/panel` 的
  //   `stars`，而那是**面板 ROI(x980~1260/y60~150)**，在结算页上正好是干员立绘 ⇒ 必然读 0/garbage。
  //   现在：结算页星区（x60~300/y290~360）的读数 **就是地面真值**，它说了算。
  const settleShot = `runtime/_settle_${stamp}_${u.code}${u.want === '突袭' ? '_raid' : ''}.png`
  const set1 = await readSettleConfirmed(settleShot, { wantText: true })   // ← 与演习**同一条读数口径**（task-17 issue#1）＋奖励栏文字证据（误判修复）
  if (fb.verdict === 'done' || set1) {
    // 结算页在 ⇒ 让结算页的星数说话（这正是"跑完却判 failed"的修复点）
    await settleFinish(u, rec, fb, set1)
  } else {
    // 屏上不是结算页、MAA 又判没成功 ⇒ 退回面板读数兜底。
    //   ⚠️ `stars` 低置信是 **null**（auditor-1 的契约），别拿 `?? 0` 冒充读数：
    //   旧代码 `(g2?.stars ?? 0) >= 3` 会把 null 当 0，而 `else if (g2?.cleared)`
    //   在 cleared==null 时会漏掉 ⇒ 掉进最后的兜底分支被记成 done。这里全部显式判 null。
    const g2 = await panelGate()
    // **报酬栏判据统一**（Lead 2026-09-24）：徽章"还在吗"由 `badgeStillThere()` 判（**优先用分数**，
    //   阈值单一来源 runtime/config/reward_gate.json），不再各处各读一个布尔。
    if (badgeStillThere(g2) === true) {
      rec.action = 'partial'
      rec.verdict = `徽章仍在（面板 ${g2?.stars ?? '?'} 星 / 置信 ${g2?.stars_conf ?? '?'}）⇒ 源石未发，记 partial 待补三星`
      log(`  ◐ ${rec.verdict}`)
    } else if (g2?.stars_conf === 'high' && (g2?.stars ?? null) !== null && g2.stars >= 3) {
      rec.action = 'done'
      rec.verdict = `三星通关（面板复核 ${g2.stars} 星·高置信）⇒ 源石到手`
      log(`  ✓ ${rec.verdict}`)
    } else if (g2?.cleared === true) {
      rec.action = 'partial'
      rec.verdict = `已通关但只 ${g2.stars} 星 ⇒ 首通源石未发，下次补三星`
      log(`  ◐ ${rec.verdict}`)
    } else {
      rec.action = 'failed'
      rec.verdict = `未成功（MAA 判 ${fb.verdict}；屏上不是结算页，面板无可靠星数）`
      log(`  ✗ ${rec.verdict}`)
    }
  }
}

/** **判定 → 一句话**（纯函数）：日志、账本、离线回放**共用同一份措辞** ——
 *  免得"离线演练说得漂亮、真机跑又是一套话"。 */
function settleVerdictText(v, ctx = {}) {
  const where = ctx.where || ''
  const ss = ctx.settleStars ?? null
  const ps = ctx.panelStars ?? null
  const score = ctx.pageScore ?? null
  const maa = ctx.maaVerdict || '?'
  return {
    'badge-remains': `复核：徽章仍在（${where}）⇒ 源石未发，记 partial 待补三星`,
    'firstdrop-badge-conflict': `结算页写着【首次掉落】而面板仍读到徽章（**两个证据冲突**）⇒ 以游戏印的文案为准：`
      + `记 done+源石，并落 conflict=badge-remains-vs-firstdrop（徽章 ROI 有假阴性前科，`
      + `且 19:00 那条真通关的字段形状就是这种冲突）`,
    'settle-firstdrop-3star': `三星通关（${where}，奖励栏【首次掉落】且徽章已消失）⇒ 源石到手`,
    'settle-3star': `三星通关（${where}，徽章已消失）⇒ 源石到手`,
    'panel-3star': `三星通关（${where}，徽章已消失）⇒ 源石到手`,
    'settle+panel-3star': `三星通关（结算页【首次掉落】+ 面板复核 ${ps}★）⇒ 源石到手`,
    'raid-first-drop': `突袭通关（结算页奖励栏【首次掉落×1】）⇒ 首通源石到手；`
      + `突袭**没有星数评价**，星区读数 ${ss ?? '?'}★ 是正常读数（**不判 failed**）`,
    'firstdrop-unknown-stars': `已通关（结算页奖励栏有【首次掉落】）但星数读不出来 ⇒ 记"已通关·星数未知"待复核`
      + `（**不判 failed、不降权**）`,
    'sanity-refund': `未通过（结算页奖励栏有【理智返还】且无首通奖励——正面失败证据`
      + `${ss !== null ? `，星区 ${ss}★` : ''}）⇒ 这局白跑，记 failed 并降权该轴`,
    'settle-no-evidence': `结算页在（页面分 ${score ?? '?'}），但既没有【首次掉落】也没有【理智返还】、`
      + `星数读数 ${ss ?? '?'} ⇒ **证据不足：不判 failed、不降权**，记待复核`,
    'drill-frame': `这一帧是**演习局**（文字里含「演习」）⇒ 不判真打胜负、不记收益`,
    'maa-done-no-read': '作业判 done，但结算页/面板都读不到星数 ⇒ 记 done 并注明读数缺失',
    'maa-failed-no-read': `未成功（MAA 判 ${maa}）且星数读不到 ⇒ 记 failed`,
  }[v.why] || `${v.stars}★（${where}）⇒ 首通源石未发，下次补三星`
}

/** **结算页之后的收尾**：用结算页星数定生死 → 点掉结算页 → 回面板复核徽章 → 写账本。
 *
 *  判定本身在 `verdictFromStars()`（纯函数）；这里只负责"取读数 + 落账"。 */
/** **等页面离开某个指纹**（条件轮询，≤timeout）：task-17 issue#5 —— 不长盲等。
 *  返回耗时 ms；超时返回 -1。 */
async function waitPageAway(fromPage, timeoutMs = 8000) {
  const t0 = Date.now()
  while (Date.now() - t0 < timeoutMs) {
    await sleep(600)
    const st = await readAll()
    if (!st || st.page !== fromPage) return Date.now() - t0
  }
  return -1
}

async function settleFinish(u, rec, fb, set1) {
  const settleStars = set1?.stars ?? null
  if (set1) {
    // 点掉结算页才谈得上复核（纪律：手工锚点必须先确认画面 —— 不是结算页就别点）。
    // 原来这里 `tapPy(...,3)` + `tapPy(...,9)` 是**固定盲等 12s**（task-17 issue#5）
    // ⇒ 改成短等 + **条件轮询**：页面一离开结算页就走。
    await tapPy(1131, 264, 1)        // 升级气泡 / 好友申请的 ✓
    await tapPy(640, 630, 2)         // 结算页 → 继续（等加载回地图）
    const left = await waitPageAway('结算页', 8000)
    log(left >= 0 ? `  ✓ 结算页已点掉（${left}ms）` : '  （结算页 8s 内没离开，继续按现有流程复核）')
  }
  const nav2 = await navTo(u.code, u.want)
  if (nav2 && nav2.ok === false) log(`  （复核导航没成功：${nav2.detail || '?'}）`)
  const g3 = await panelGate()
  const stars2 = g3?.stars ?? null
  const v = settleVerdict({
    onSettlePage: !!set1,
    stars: settleStars,
    lit: set1?.lit ?? null,
    blue: set1?.blue ?? null,
    firstDrop: set1?.firstDrop ?? null,
    sanityRefund: set1?.sanityRefund ?? null,
    drillFrame: set1?.drillFrame ?? null,
    textSeen: set1?.textSeen ?? null,
    score: set1?.score ?? null,
    panelStars: stars2,
    panelPrimes: badgeStillThere(g3),      // 徽章三态：优先用分数判（rewardVerdict），缺分数才退回布尔
    maaDone: fb.verdict === 'done',
    want: u.want,
  })
  rec.settle_stars = settleStars
  // **`result_stars` / `page_after` 是下游（`runtime/_prime_queue3.py::classify_report`）要读的字段**：
  //   队列的注释写着"① 优先用 result_stars（结算页星区，唯一权威）；② 退一步只在 page_after=='关卡详情'
  //   时才信 stars_after" —— 我的 sweep 之前**没写这两个字段** ⇒ 队列的"主路径"永远是空的，
  //   只能掉到 notdone。这里补齐（结算页星数就是 result_stars，复核导航成功就写 page_after）。
  //   ⚠️ **只有真读出星数（≥1）才写 `result_stars`**：突袭通关的星区读数 0★ 是"无星级评价"，
  //   写 0 会让队列把它当"没通关"（1900 那次就是这条链把赢的局记成 failed 的）。
  rec.result_stars = (v.stars !== null && v.stars >= 1) ? v.stars : null
  rec.stars_after = stars2
  rec.page_after = nav2?.ok ? '关卡详情' : (nav2?.page_after ?? null)
  rec.stars_final = v.stars
  rec.action = v.action
  // **判据 + 置信度落账**（铁律：不得静默拍板）—— 事后能一眼看出"为什么这么判"
  rec.settle_clear = v.clear
  rec.settle_why = v.why
  rec.settle_verdict_evidence = v.evidence
  if (v.conflict) rec.settle_conflict = v.conflict      // 证据冲突必须看得见（不许静默拍板）
  const where = settleStars !== null
    ? `结算页 ${settleStars}★${stars2 !== null ? ` / 面板 ${stars2}★` : ''}`
    : `面板复核 ${stars2 ?? '?'}★`
  rec.verdict = settleVerdictText(v, { where, settleStars, panelStars: stars2,
    pageScore: set1?.score ?? null, maaVerdict: fb.verdict })
  log(`  ${v.action === 'done' ? '✓' : v.action === 'partial' ? '◐' : '✗'} ${rec.verdict}`)
  log(`  ⚖️ 判据：clear=${v.clear === null ? '证据不足' : v.clear}｜why=${v.why}｜首次掉落=${v.evidence.first_drop}`
    + `｜理智返还=${v.evidence.sanity_refund}｜结算页星数=${v.evidence.settle_stars}`
    + `（写进账本 settle_verdict_evidence）`)
  // ── 账本：这一局该记在**哪份轴**头上 ──────────────────────────────────
  //   `fb.accepted`（降级重试自己认可的那份）优先；**没有它也要记**（2026-09-24 修）：
  //   fallback 结束时屏上那份结算页是**最后一次试的轴**跑出来的 ⇒ 记它。
  //   为什么必须记：旧代码只写 `fallback-tried`（stars=null）⇒ 赢家也被
  //   `_story_axes_big2.py`/`_event_axes_raid2.py` 按 -6 降权（1900 那次 prts://41427 就是这么被降的）。
  const ledgerId = fb.accepted ?? fb.lastTried ?? null
  if (ledgerId !== null && ledgerId !== undefined) {
    // task-17 issue#2：**没有证据就不写 stars/stone**。
    //   「有证据」= 真读到 ≥1★（自己的读数）**或**有正面失败证据（`clear===false`：奖励栏写着
    //   「理智返还」，结算页 0★ 就是权威读数 ⇒ 该写 0，让读账本的人照旧降权它 -12）。
    //   「0★ 但没有理智返还」= 突袭通关那种正常读数 ⇒ **不写 0**（写 null = 未知，不降权）。
    const hasEvidence = v.stars !== null && (v.stars >= 1 || v.clear === false)
    const stone = v.stone === true || (v.stars !== null && v.stars >= 3)
    recordOutcome(u.stage, u.want, ledgerId, hasEvidence ? v.stars : null,
      fb.accepted ? v.why : `${v.why}(最后一次试的轴)`,
      (fb.acceptedOpers && fb.acceptedOpers.length) ? fb.acceptedOpers : (fb.lastOpers || []), v.src,
      v.not3 === true,
      { mode: 'real', evidence: fb.realRun?.evidence || v.src || 'none',
        ...(hasEvidence ? {} : { valid: false }),
        ...(stone ? { stone: true } : {}),
        ...(v.conflict ? { conflict: v.conflict } : {}),
        settle_clear: v.clear, settle_why: v.why, settle_evidence: v.evidence })
  }
}

// ── **离线回放**（`node tools/event_sweep.mjs --settle-replay`）──────────────────
// 用 `runtime/fixtures/settle/fixtures.json`（真机截图的冻结夹具）把**同一批判据函数**
// （settleVerdict + settleVerdictText + outcomeRow）跑一遍，打印"真机再跑一次会是什么日志/账本行"。
// 为什么必须有：这次的 bug 就是"判据函数对了、可接线接错/没人看奖励栏"⇒ 回放让改动**看得见**。
function runSettleReplay() {
  const fx = JSON.parse(readFileSync(join(ROOT, 'runtime', 'fixtures', 'settle', 'fixtures.json'), 'utf8'))
  console.log('== 结算页判定回放（离线；夹具 = runtime/fixtures/settle/，真值肉眼核对）==')
  for (const f of fx.images) {
    const v = settleVerdict({
      onSettlePage: f.page_is_settle === true, stars: f.settle_stars, lit: f.settle_lit,
      blue: f.settle_blue, firstDrop: f.text_first_drop, sanityRefund: f.text_sanity_refund,
      drillFrame: f.text_drill, textSeen: true, panelStars: null, panelPrimes: null,
      maaDone: false, want: f.want, score: 0.895,
    })
    console.log(`\n--- ${f.file}（${f.src_file}）｜肉眼：${f.vision}`)
    if (f.page_is_settle) {
      console.log(`  👁 结算页已确认（页面分 0.895）｜星数读数：${f.settle_stars ?? '?'}★`
        + `（六边形亮蓝像素 ${(f.settle_blue || []).join('/')}，帧存 runtime/fixtures/settle/${f.file}）`)
      console.log(`  🧾 奖励栏证据：首次掉落=${f.text_first_drop ? '有' : '无'}`
        + `｜理智返还=${f.text_sanity_refund ? '有' : '无'}`)
      console.log(`  ${v.action === 'done' ? '✓' : v.action === 'partial' ? '◐' : '✗'} `
        + settleVerdictText(v, { where: `结算页 ${f.settle_stars}★`, settleStars: f.settle_stars }))
      console.log(`  ⚖️ 判据：clear=${v.clear === null ? '证据不足' : v.clear}｜why=${v.why}`
        + `｜首次掉落=${f.text_first_drop}｜理智返还=${f.text_sanity_refund}｜结算页星数=${f.settle_stars}`)
      if (f.expect_action !== null) {
        const hasEvidence = v.stars !== null && (v.stars >= 1 || v.clear === false)
        const stone = v.stone === true || (v.stars !== null && v.stars >= 3)
        console.log('  📒 账本行（最后试的那份轴）：' + JSON.stringify(outcomeRow(
          'act28side_ex08', f.want, 41427, hasEvidence ? v.stars : null, v.why, ['乌尔比安'], v.src,
          v.not3 === true,
          { mode: 'real', evidence: v.src, ...(hasEvidence ? {} : { valid: false }),
            ...(stone ? { stone: true } : {}), settle_clear: v.clear, settle_why: v.why })))
      }
    } else {
      console.log(`  ⊘ 不是结算页（页面守卫直接拦下；settleFrom 返回 null ⇒ 一个读数都不采）`
        + `｜我的探针在同一区域读到亮像素 ${JSON.stringify(f.slot_bright)} ⇒ 说明"亮"本身不能当通关证据`)
    }
  }
  console.log('\n== 回放完毕（不连模拟器、不花理智）==')
  return 0
}

// ── 离线自测（`node tools/event_sweep.mjs --selftest`）───────────────────
// 不连模拟器、不花理智：只验"结算页星数 → 判定"这段纯逻辑。
// 为什么必须有（task-3）：`failed` 误判的根因就是"结算页星数没参与判定"，
//   这段逻辑判错一次就是白花理智 / 白降权一套本来能过的轴，必须能离线回归。
function runSelfTest() {
  const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b)
  // **判定只要这几个字段**（`evidence`/`stone` 是落账用的细账，不参与断言对比）
  const pv = (v) => ({ action: v.action, stars: v.stars, src: v.src, why: v.why, not3: v.not3,
    clear: v.clear, stone: v.stone ?? null })
  const settleSig = (o) => settleVerdict({ onSettlePage: true, maaDone: false, ...o })
  const cases = [
    ['结算页 3★（徽章已消失）→ done', pv(settleSig({ stars: 3, panelPrimes: false })),
      { action: 'done', stars: 3, src: 'settle', why: 'settle-3star', not3: false, clear: true, stone: null }],
    ['结算页 2★ + 徽章仍在 → partial 降权', pv(settleSig({ stars: 2, panelPrimes: true })),
      { action: 'partial', stars: 2, src: 'settle+panel', why: 'badge-remains', not3: true, clear: null, stone: null }],
    ['结算页 1★ → partial 降权', pv(settleSig({ stars: 1 })),
      { action: 'partial', stars: 1, src: 'settle', why: 'settle-1star', not3: true, clear: true, stone: null }],
    // ★★ 本次修复的核心（task: 结算页误判）——1900 那张真通关的全部读数都在这里 ★★
    ['【铁证】1900 CV-EX-8 突袭**真通关**：星区 0★/蓝像素 0/0/0 + 奖励栏【首次掉落】⇒ done+源石，**绝不判 failed**',
      pv(settleSig({ stars: 0, blue: [0, 0, 0], firstDrop: true, sanityRefund: false, textSeen: true,
        panelPrimes: false, want: '突袭' })),
      { action: 'done', stars: null, src: 'settle', why: 'raid-first-drop', not3: false, clear: true, stone: true }],
    ['【铁证】真失败页（MT-EX-8 / 15-20 那两张）：三灰 + 【理智返还】⇒ failed（**唯一的 failed 通道**）',
      pv(settleSig({ stars: 0, blue: [0, 0, 0], firstDrop: false, sanityRefund: true, textSeen: true })),
      { action: 'failed', stars: 0, src: 'settle', why: 'sanity-refund', not3: true, clear: false, stone: null }],
    ['【铁律】结算页 0★ 而**没有**「理智返还」也没有首通奖励 ⇒ **不判 failed**（证据不足、不降权）',
      pv(settleSig({ stars: 0, blue: [0, 0, 0], firstDrop: false, sanityRefund: false, textSeen: true })),
      { action: 'partial', stars: null, src: 'settle', why: 'settle-no-evidence', not3: false, clear: null, stone: null }],
    ['【铁律】结算页有【首次掉落】但星数读不出来 ⇒ 已通关·星数未知（**不判 failed、不降权**）',
      pv(settleSig({ stars: null, firstDrop: true, sanityRefund: false, textSeen: true, want: '普通' })),
      { action: 'partial', stars: null, src: 'settle', why: 'firstdrop-unknown-stars', not3: false, clear: true, stone: null }],
    ['普通页 0★ + 【首次掉落】+ 面板复核 3★ ⇒ done+源石（两个独立证据）',
      pv(settleSig({ stars: 0, firstDrop: true, panelStars: 3, panelPrimes: false, want: '普通' })),
      { action: 'done', stars: 3, src: 'settle+panel', why: 'settle+panel-3star', not3: false, clear: true, stone: true }],
    ['结算页 3★ + 【首次掉落】⇒ done+源石', pv(settleSig({ stars: 3, firstDrop: true, textSeen: true })),
      { action: 'done', stars: 3, src: 'settle', why: 'settle-firstdrop-3star', not3: false, clear: true, stone: true }],
    ['演习局的结算页（文字含「演习」）⇒ 不判胜负（drill-frame）', pv(settleSig({ stars: 0, drillFrame: true })),
      { action: 'partial', stars: 0, src: 'settle', why: 'drill-frame', not3: false, clear: null, stone: null }],
    ['结算页 2★ 而面板读成 3★ ⇒ **结算页优先**', pv(settleSig({ stars: 2, panelStars: 3, maaDone: true })),
      { action: 'partial', stars: 2, src: 'settle', why: 'settle-2star', not3: true, clear: true, stone: null }],
    ['结算页在、星数读不到、面板 3★ ⇒ done（面板兜底，src=panel）',
      pv(settleSig({ stars: null, panelStars: 3, panelPrimes: false })),
      { action: 'done', stars: 3, src: 'panel', why: 'panel-3star', not3: false, clear: true, stone: null }],
    ['**屏上根本没有结算页**（onSettlePage:false）+ MAA 判 done ⇒ done（注明读数缺失）',
      pv(settleVerdict({ onSettlePage: false, maaDone: true })),
      { action: 'done', stars: null, src: 'none', why: 'maa-done-no-read', not3: false, clear: null, stone: null }],
    ['**屏上根本没有结算页** + MAA 判 failed ⇒ failed', pv(settleVerdict({ onSettlePage: false, maaDone: false })),
      { action: 'failed', stars: null, src: 'none', why: 'maa-failed-no-read', not3: false, clear: null, stone: null }],
    // ── 奖励栏文字证据的解析（`?text=1` 的整帧 OCR）───────────────────────────
    ['文字证据：真通关那帧的 OCR ⇒ 首次掉落=有、理智返还=无（这是 1900 那次缺的那条证据）',
      settleTextEvidence('OPERATION CV-EX-8 正中目标 行动结束 MISSIONRESULTS ISLAND 全员信赖提升 '
        + '120 0/100000 老240 首次掉落 1.2倍声望&龙门币'),
      { textSeen: true, firstDrop: true, sanityRefund: false, drillFrame: false }],
    ['文字证据：真失败那帧的 OCR ⇒ 理智返还=有、首次掉落=无',
      settleTextEvidence('OPERATION MT-EX-8 行动结束 MISSIONRESULTS ISLAND 资质平平。 120 0/100000 20 理智返还'),
      { textSeen: true, firstDrop: false, sanityRefund: true, drillFrame: false }],
    ['文字证据：演习中的战斗 HUD ⇒ drillFrame（不许当成真打的结算页）',
      settleTextEvidence('22/37 2X >演习中<X> 1157 324 剩余可放置角色：2'),
      { textSeen: true, firstDrop: false, sanityRefund: false, drillFrame: true }],
    ['文字证据：**没拿到文字** ⇒ 三个都 null（未知，不是 false；判定自动变保守）',
      settleTextEvidence(''),
      { textSeen: false, firstDrop: null, sanityRefund: null, drillFrame: null }],
    ['**不是结算页 ⇒ 不读星**（页面守卫，面板 stars 在结算页上是垃圾）',
      settleFrom({ page: '关卡详情', result_stars: 3, result_blue: [2200, 2200, 2200] }), null],
    ['是结算页但服务没给 result_stars ⇒ stars=null（不拿别的读数冒充）',
      settleFrom({ page: '结算页', page_score: 0.98 }),
      { stars: null, lit: null, blue: null, conf: null, score: 0.98,
        textSeen: false, firstDrop: null, sanityRefund: null, drillFrame: null }],
    ['结算页 2★ 正常解析（EA-1 夹具的真实亮蓝像素）',
      settleFrom({ page: '结算页', result_stars: 2, result_lit: [true, true, false],
        result_blue: [2161, 2210, 0], result_conf: 'high', page_score: 0.982 }),
      { stars: 2, lit: [true, true, false], blue: [2161, 2210, 0], conf: 'high', score: 0.982,
        textSeen: false, firstDrop: null, sanityRefund: null, drillFrame: null }],
    ['结算页 0★ + 文字证据（1900 那帧的真实 OCR 片段）⇒ settleFrom 里 firstDrop=true',
      (({ stars: _s, lit: _l, blue: _b, conf: _c, ...rest }) => rest)(
        settleFrom({ page: '结算页', result_stars: 0, result_lit: [false, false, false],
          result_blue: [0, 0, 0], result_conf: 'high', page_score: 0.895,
          text: 'OPERATION CV-EX-8 正中目标 行动结束 MISSIONRESULTS ISLAND 呼、呼 全员信赖提升 '
            + '120 0/100000 老240 首次掉落 1.2倍声望&龙门币' })),
      { score: 0.895, textSeen: true, firstDrop: true, sanityRefund: false, drillFrame: false }],
    // ── task-15：**演习优先**的决策（演习 3★ 才真打；演习 <3★ 就换轴；全无效才算通道坏）──
    ['演习：第 1 份就 3★ ⇒ 真打它', drillDecision([{ id: 11, stars: 3, valid: true }]),
      { action: 'real', axis: 11, why: 'drill-3star' }],
    ['演习：第 2 份才 3★ ⇒ 真打第 2 份（第 1 份不真打）',
      drillDecision([{ id: 11, stars: 2, valid: true }, { id: 22, stars: 3, valid: true }]),
      { action: 'real', axis: 22, why: 'drill-3star' }],
    ['演习：全是 1~2★ ⇒ no_axis（**零理智**，一份都不真打）',
      drillDecision([{ id: 11, stars: 2, valid: true }, { id: 22, stars: 1, valid: true }]),
      { action: 'no_axis', axis: null, why: 'all-drills-under-3' }],
    ['演习：0★（演习里翻车）也算"有读数" ⇒ no_axis，不是通道坏',
      drillDecision([{ id: 11, stars: 0, valid: true }]),
      { action: 'no_axis', axis: null, why: 'all-drills-under-3' }],
    ['演习：全都没跑出结算页 ⇒ drill_broken（可退回真打；--drill-strict 则不退）',
      drillDecision([{ id: 11, stars: null, valid: false }, { id: 22, stars: null, valid: false }]),
      { action: 'drill_broken', axis: null, why: 'all-drills-invalid' }],
    // ★ 真机教训（AD-EX-2，2026-09-24）：**进战斗后漏怪被掐**没有结算页，但结论确定（到不了 3★）
    //   ⇒ 必须是 no_axis（换轴、零理智），**绝不能算成"通道坏"**（那会退回盲目真打）。
    ['演习：漏怪被掐（无结算页但确定到不了 3★）⇒ no_axis，不是 drill_broken',
      drillDecision([{ id: 11, stars: null, valid: false, not3: true }]),
      { action: 'no_axis', axis: null, why: 'all-drills-under-3' }],
    ['演习：一份漏怪 + 一份无结论 ⇒ no_axis（有结论就够换轴）',
      drillDecision([{ id: 11, stars: null, valid: false, not3: true },
        { id: 22, stars: null, valid: false, not3: false }]),
      { action: 'no_axis', axis: null, why: 'all-drills-under-3' }],
    ['演习：漏怪 + 另一份 3★ ⇒ 真打那份 3★ 的',
      drillDecision([{ id: 11, stars: null, valid: false, not3: true }, { id: 22, stars: 3, valid: true }]),
      { action: 'real', axis: 22, why: 'drill-3star' }],
    ['演习：没有候选轴 ⇒ no_axis', drillDecision([]),
      { action: 'no_axis', axis: null, why: 'no_candidate' }],
    ['演习 3★ + 真打 3★ + 徽章消失 ⇒ done（stone=true 记在账本）',
      pv(settleSig({ stars: 3, panelStars: 3, panelPrimes: false, maaDone: true })),
      { action: 'done', stars: 3, src: 'settle', why: 'settle-3star', not3: false, clear: true, stone: null }],
    ['演习 3★ 但真打只 2★ ⇒ partial（**演习过 ≠ 真打必过**，别记 done）',
      pv(settleSig({ stars: 2, panelPrimes: true, maaDone: true })),
      { action: 'partial', stars: 2, src: 'settle+panel', why: 'badge-remains', not3: true, clear: null, stone: null }],
    // ── 【探针 A/B】审计 `runtime/_audit_settle_mirror_20260924.md` §2/§5：**19:00 那条真通关的
    //    字段形状里 `reward_primes` 就是 true** ⇒ 徽章否决排在首通证据之前 = 正面证据被吞 + 降权。
    //    旗舰夹具恰好写 `panelPrimes:false` 才全绿 ⇒ 这两条专门盯"徽章在 + 首次掉落"这种真实形状。
    ['【探针A】普通：首次掉落=有 且 徽章仍在=有（矛盾读数）⇒ 期望 done+源石（文案优先），不许降权',
      pv(settleSig({ stars: 0, firstDrop: true, panelStars: 3, panelPrimes: true, want: '普通' })),
      { action: 'done', stars: 3, src: 'settle+panel', why: 'firstdrop-badge-conflict', not3: false,
        clear: true, stone: true }],
    ['【探针B】突袭（=1900 那次的形状）：0★/蓝 0/首次掉落=有 且 徽章仍在=有 ⇒ 期望 done+源石、不降权',
      pv(settleSig({ stars: 0, blue: [0, 0, 0], firstDrop: true, sanityRefund: false, textSeen: true,
        panelPrimes: true, want: '突袭' })),
      { action: 'done', stars: null, src: 'settle+panel', why: 'firstdrop-badge-conflict', not3: false,
        clear: true, stone: true }],
    ['【回归】徽章仍在 **且没有**首通文案 ⇒ 仍按「源石没发」partial 降权（没把 ④′ 修坏）',
      pv(settleSig({ stars: 3, firstDrop: false, panelPrimes: true, want: '普通' })),
      { action: 'partial', stars: 3, src: 'settle+panel', why: 'badge-remains', not3: true,
        clear: null, stone: null }],
    // ── P2：`opers` **三态**（另一个 bot 的干员闸按它取数：**`null`≠`[]`**）──────────────
    ['账本 opers：没采到（undefined）⇒ `null` + opers_state=not-collected（**不许写成 []**）',
      (() => { const r = outcomeRow('s', '普通', 1, 3, 'settle-3star', undefined, 'settle', false, {})
        return { opers: r.opers, state: r.opers_state } })(),
      { opers: null, state: 'not-collected' }],
    ['账本 opers：采到了但为空（[]）⇒ `null` + opers_state=collected-empty（**与"没采到"区分开**）',
      (() => { const r = outcomeRow('s', '普通', 1, 3, 'settle-3star', [], 'settle', false, {})
        return { opers: r.opers, state: r.opers_state } })(),
      { opers: null, state: 'collected-empty' }],
    ['账本 opers：采到名单 ⇒ 原样落账（三态里只有这一种是数组）',
      (() => { const r = outcomeRow('s', '普通', 1, 3, 'settle-3star', ['银灰', '史尔特尔'], 'settle', false, {})
        return { opers: r.opers, state: r.opers_state } })(),
      { opers: ['银灰', '史尔特尔'], state: 'collected' }],
    // ── P3：⊘ 的判别器（徽章 ROI 有假阴性前科 ⇒ 必须留判别器）──────────────────────────
    ['⊘ 判别器：详情页文字「三星获得 / 开始突袭 / 代理指挥」⇒ 三项都要读出来，has=true',
      (() => { const d = rewardDiscriminator({ page: '关卡详情', reward_score: 0.7 },
        '三星获得 开始突袭 代理指挥 理智 -21', '普通')
        return { obtain: d.reward_obtain_tag, raid: d.lock_raid, auto: d.lock_auto, mode: d.mode, has: d.has } })(),
      { obtain: true, raid: 'unlocked', auto: 'unlocked', mode: '普通', has: true }],
    ['⊘ 判别器：空文字（OCR 没读到）⇒ 三项全 `null`、has=false（**null 绝不当 false 用**）',
      (() => { const d = rewardDiscriminator({ page: '关卡详情' }, '', '突袭')
        return { obtain: d.reward_obtain_tag, raid: d.lock_raid, auto: d.lock_auto, has: d.has, src: d.source } })(),
      { obtain: null, raid: null, auto: null, has: false, src: 'none' }],
    // ── P4（Lead 2026-09-24 定案）：**删掉写死的 0.46/0.64 歧义带**，阈值改为配置单一来源 ──────
    ['P4 阈值：**单一来源** `runtime/config/reward_gate.json` ⇒ present=0.75 / absent=0.72，'
      + '出处写明 panel_gate.py:28',
      (() => ({ present: REWARD_PRESENT, absent: REWARD_ABSENT,
        fromCfg: String(REWARD_GATE.source).includes('panel_gate.py:28'),
        cfgAt: GATE_CFG_PATH.includes('reward_gate.json')
          && GATE_CFG_PATH.split(/[\\/]/).includes('config') }))(),
      { present: 0.75, absent: 0.72, fromCfg: true, cfgAt: true }],
    ['P4 判决：0.702（≤ absent）⇒ absent —— **低分就是"徽章不在"，不是"读数不可信"**',
      rewardVerdict(0.702), 'absent'],
    ['P4 判决：0.73（落 (0.72,0.75) 窄带）⇒ ambiguous', rewardVerdict(0.73), 'ambiguous'],
    ['P4 判决：0.75（= present，边界含在内）⇒ present（有矿）', rewardVerdict(0.75), 'present'],
    ['P4 判决：分数缺失/非数 ⇒ ambiguous（**读不到 ≠ 没有**）',
      [rewardVerdict(null), rewardVerdict(undefined), rewardVerdict('')],
      ['ambiguous', 'ambiguous', 'ambiguous']],
    ['P4 判决：分数 -1（模板/ROI 读不到的哨兵）⇒ ambiguous（**不许当成"不在"**，那是 fail-open）',
      rewardVerdict(-1), 'ambiguous'],
    ['P4 闸：**score=0.702 ⇒ 仍判终态 no_prime**（确信"不在"；旧代码把它降级成 unreliable）',
      (() => { const g = rewardNoPrimeGate(0.702, rewardDiscriminator({}, '代理指挥', '普通'), { stars: 3 })
        return { terminal: g.terminal, verdict: g.verdict, contradiction: g.contradiction } })(),
      { terminal: true, verdict: 'absent', contradiction: false }],
    ['P4 闸：score=0.55（旧的"0.46–0.64 歧义带"里）+ 判别器 + stars=3 ⇒ 现在**判终态**',
      (() => { const g = rewardNoPrimeGate(0.55, rewardDiscriminator({}, '代理指挥 开始突袭', '普通'), { stars: 3 })
        return { terminal: g.terminal, verdict: g.verdict } })(),
      { terminal: true, verdict: 'absent' }],
    ['P4 闸：**score=0.73 ⇒ unreliable**（窄带说不准，不判终态）',
      (() => { const g = rewardNoPrimeGate(0.73, rewardDiscriminator({}, '代理指挥', '普通'), { stars: 3 })
        return { terminal: g.terminal, ambiguous: g.ambiguous, verdict: g.verdict } })(),
      { terminal: false, ambiguous: true, verdict: 'ambiguous' }],
    ['P4 闸：**stars=0 + score=0.638 ⇒ unreliable（内部矛盾）**：没通关的关不可能没有首通源石',
      (() => { const g = rewardNoPrimeGate(0.638, rewardDiscriminator({}, '代理指挥', '普通'), { stars: 0 })
        return { terminal: g.terminal, contradiction: g.contradiction, verdict: g.verdict } })(),
      { terminal: false, contradiction: true, verdict: 'absent' }],
    ['P4 闸：stars=2 + score=0.702（同样矛盾）⇒ unreliable（历史矛盾行的另一种形状）',
      (() => { const g = rewardNoPrimeGate(0.702, rewardDiscriminator({}, '代理指挥', '普通'), { stars: 2 })
        return { terminal: g.terminal, contradiction: g.contradiction } })(),
      { terminal: false, contradiction: true }],
    ['P4 闸：score=0.702 + stars=3 但**判别器缺** ⇒ 仍不判终态（没有判别器就别下「不打」）',
      (() => { const g = rewardNoPrimeGate(0.702, rewardDiscriminator({}, '', '普通'), { stars: 3 })
        return { terminal: g.terminal, hasDisc: g.hasDisc } })(),
      { terminal: false, hasDisc: false }],
    ['P4 闸：分数读不到（null）⇒ unreliable（旧代码会当可信）',
      (() => { const g = rewardNoPrimeGate(null, rewardDiscriminator({}, '代理指挥', '普通'), { stars: 3 })
        return { terminal: g.terminal, ambiguous: g.ambiguous } })(),
      { terminal: false, ambiguous: true }],
    ['P4 闸：score=0.999（徽章还在）⇒ **不判 ⊘**（有矿 ⇒ 该打）',
      (() => { const g = rewardNoPrimeGate(0.999, rewardDiscriminator({}, '代理指挥', '普通'), { stars: 3 })
        return { terminal: g.terminal, verdict: g.verdict } })(),
      { terminal: false, verdict: 'present' }],
    // ── P4：可审计四件套（每条 ⊘/unreliable 行都要凑齐；三态缺就是 **null**，键不许省）──────
    ['P4 四件套：`reward_primes` 没采到（undefined）⇒ **明写 null**（旧库 47/70 条缺这一项）',
      (() => { const a = rewardAuditFields({ reward_score: 0.638, stars: 3 },
        { page: '关卡详情', reward_primes: undefined })
        return { primes: a.reward_primes, keys: Object.keys(a).sort() } })(),
      { primes: null,
        keys: ['page', 'page_guard', 'reward_primes', 'reward_score', 'reward_verdict', 'stars'] }],
    ['P4 四件套：布尔 false / true 原样保留（三态里只有这两种是布尔）',
      [rewardAuditFields({}, { page: '关卡详情', reward_primes: false }).reward_primes,
        rewardAuditFields({}, { page: '关卡详情', reward_primes: true }).reward_primes],
      [false, true]],
    ['P4 四件套：页面守卫 reject（页面不是关卡详情）⇒ `page_guard="reject"`，四件套照样齐全',
      (() => { const a = rewardAuditFields({ reward_score: 0.66, stars: 2 },
        { page: '结算页', reward_primes: null })
        return { guard: a.page_guard, score: a.reward_score, stars: a.stars, primes: a.reward_primes } })(),
      { guard: 'reject', score: 0.66, stars: 2, primes: null }],
    // ── P5（Lead 追加）：`runFallback()` 的**写行策略**（批次级、对称、链坏不罚）──────────────
    ['P5 写行：`drill_broken=true` ⇒ 整批写**中性**行 `chain-broken`，**不含 `fallback-tried`**',
      (() => { const p = fallbackRowPlan([59743, 59818], { chainBroken: true })
        return { notes: p.map((x) => x.note), penalties: p.map((x) => x.penalty),
          anyFt: p.some((x) => String(x.note).startsWith('fallback-tried')) } })(),
      { notes: ['chain-broken', 'chain-broken'], penalties: [false, false], anyFt: false }],
    ['P5 写行：**同批两份轴、证据完全相同 ⇒ 判据/权重一致**（不许一罚一放：59743/59818 的第二次实例）',
      (() => { const p = fallbackRowPlan([59743, 59818], { chainBroken: false })
        const judge = (x) => JSON.stringify({ note: x.note, penalty: x.penalty })
        return { same: judge(p[0]) === judge(p[1]), notes: p.map((x) => x.note), n: p.length } })(),
      { same: true, notes: ['no-conclusion', 'no-conclusion'], n: 2 }],
    ['P5 写行：**全写 / 全不写**——每份试过的轴都恰好一行（不许漏掉"最后试的那一份"）',
      (() => { const ids = [1, 2, 3]
        return fallbackRowPlan(ids, { chainBroken: false }).map((x) => String(x.id)) })(),
      ['1', '2', '3']],
    ['P5 写行：没有任何试过的轴 ⇒ 一行都不写', fallbackRowPlan([], { chainBroken: true }), []],
  ]
  let bad = 0
  // task-15：`stage_attempt.py` 的 attempt.log 解析（`/all` 读不到星数时的兜底通路）——
  //   用真日志里的原句造两个样本（含 3★ 与 2★），断言解析口径，跑完删掉。
  const ce6 = join(ROOT, 'runtime', '_ce6')
  const fakeTag = '_selftest_attempt'
  try {
    mkdirSync(ce6, { recursive: true })
    const cases2 = [
      ['attempt.log 3★ ⇒ {clear, 3}', '  结算：页面类型=clear  星级=3', { kind: 'clear', stars: 3 }],
      ['attempt.log 2★ ⇒ {clear, 2}', '  结算：页面类型=maybe-clear  星级=2', { kind: 'maybe-clear', stars: 2 }],
      ['attempt.log 没有结算行 ⇒ {null, null}', '  作业结束：100s', { kind: null, stars: null }],
    ]
    for (const [name, line, want] of cases2) {
      writeFileSync(join(ce6, `${fakeTag}.attempt.log`), `=== 通用跑局 ${fakeTag} ===\n${line}\n=== 完毕 ===\n`, 'utf8')
      const got = starsFromAttemptLog(fakeTag)
      const ok = eq(got, want)
      if (!ok) bad += 1
      console.log(`  ${ok ? '✓' : '✗'} ${name}`)
      if (!ok) console.log(`      got  ${JSON.stringify(got)}\n      want ${JSON.stringify(want)}`)
      cases.push([name, got, want])
    }
    // ★ task-17 issue#3 回归：**同一 tag 在同一分钟内重跑**（日志是 append 写）——
    //   旧实现取**第一条** match ⇒ 会把上一局的 2★ 当成本局的读数（真 bug）。
    const appended = `=== 通用跑局 ${fakeTag}（第 1 局）===\n  结算：页面类型=clear  星级=2\n=== 完毕 ===\n`
      + `=== 通用跑局 ${fakeTag}（第 2 局）===\n  结算：页面类型=clear  星级=3\n=== 完毕 ===\n`
    writeFileSync(join(ce6, `${fakeTag}.attempt.log`), appended, 'utf8')
    const got2 = starsFromAttemptLog(fakeTag)                       // 不带 sinceSize ⇒ 必须取最后一条
    const want2 = { kind: 'clear', stars: 3 }
    const ok2 = eq(got2, want2)
    if (!ok2) bad += 1
    console.log(`  ${ok2 ? '✓' : '✗'} 【回归】同一日志追加两局 ⇒ 取**最后一条**（不是上局的 2★）`)
    if (!ok2) console.log(`      got  ${JSON.stringify(got2)}\n      want ${JSON.stringify(want2)}`)
    cases.push(['【回归】追加日志取最后一条', got2, want2])
    // 只解析"本次新增"那段（sinceSize）：从第 2 局的起始位置开始 ⇒ 仍然读到 3★
    const off = appended.indexOf('=== 通用跑局 ' + fakeTag + '（第 2 局）===')
    const got3 = starsFromAttemptLog(fakeTag, off)
    const ok3 = eq(got3, want2)
    if (!ok3) bad += 1
    console.log(`  ${ok3 ? '✓' : '✗'} 【回归】只解析本次新增段（sinceSize）⇒ 3★`)
    if (!ok3) console.log(`      got  ${JSON.stringify(got3)}\n      want ${JSON.stringify(want2)}`)
    cases.push(['【回归】sinceSize 只读新增段', got3, want2])
  } catch (e) {
    bad += 1
    console.log(`  ✗ attempt.log 解析自测异常：${e.message}`)
  } finally {
    try { rmSync(join(ce6, `${fakeTag}.attempt.log`), { force: true }) } catch { /* 忽略 */ }
  }
  // ★ task-17 issue#1：演习与真打**同一口径**的证据（离线）：多帧取最大 ——
  //   单帧读到 2★（逐颗亮起的动画中途）而复读一帧是 3★ ⇒ 必须判 3★（旧演习侧单帧就会漏掉）。
  for (const [name, frames, want] of [
    ['同口径：单帧 2★ + 复读 3★ ⇒ 取 3★（旧的单帧判法会漏掉真 3★）', [2, 3], 3],
    ['同口径：单帧 3★ ⇒ 3★', [3, null], 3],
    ['同口径：两帧都读不到 ⇒ null（不硬猜）', [null, null], null],
    ['同口径：空列表 ⇒ null', [], null],
  ]) {
    const got = maxStars(frames)
    const ok = got === want
    if (!ok) bad += 1
    console.log(`  ${ok ? '✓' : '✗'} ${name}`)
    if (!ok) console.log(`      got ${got} want ${want}`)
    cases.push([name, got, want])
  }
  // ★ task-17 issue#2：**无效局不写 stars/stone** 的离线证据（对账本行构造函数断言字段）
  for (const [name, row, want] of [
    ['账本：无效演习 ⇒ stars=null、evidence 有、无 stone',
      outcomeRow('s', '普通', 1, null, 'drill-invalid', [], 'drill', undefined,
        { mode: 'drill', evidence: 'none', valid: false }),
      { stars: null, mode: 'drill', evidence: 'none', stone: undefined }],
    ['账本：演习 0★（有结算页证据）⇒ stars=0、not3',
      outcomeRow('s', '普通', 2, 0, 'drill-0star', [], 'drill', true,
        { mode: 'drill', evidence: 'settle' }),
      { stars: 0, mode: 'drill', evidence: 'settle', not3: true, stone: undefined }],
    ['账本：真打 3★ 且徽章消失 ⇒ stars=3 + stone=true',
      outcomeRow('s', '普通', 3, 3, 'settle-3star', [], 'settle', false,
        { mode: 'real', evidence: 'settle', stone: true }),
      { stars: 3, mode: 'real', evidence: 'settle', stone: true }],
  ]) {
    const probe = { stars: row.stars, mode: row.mode, evidence: row.evidence,
      not3: row.not3, stone: row.stone }
    const ok = eq(probe, want)
    if (!ok) bad += 1
    console.log(`  ${ok ? '✓' : '✗'} ${name}`)
    if (!ok) console.log(`      got  ${JSON.stringify(probe)}\n      want ${JSON.stringify(want)}`)
    cases.push([name, probe, want])
  }

  // ★ 2026-09-24 本批修复（Lead 追加要求）：**突袭不跑演习** + **收尾不能把游戏留在战斗里**
  //   + **演习只烧演习券**（券不足走老路径 / 券读不到照常演习）+ **演习不产收益**（账本断言）。
  //   夹具用**真机原句/原 JSON**（注释里给了出处），不编数据。
  const FX_51786 = { tag: '20260924_1440_CV-EX-8_drill_51786', job: 'job_51786.json', exit: 5,
    mode: 'drill', raid: true, kind: 'unknown', stars: 0, dur_s: 180.6, still_battle: true,
    leak: false, battle: true, battle_s: 81.9, valid: false, reason: 'gate3-invalid' }
  // ↑ 原文：runtime/reports/attempt_20260924_1440_CV-EX-8_drill_51786.json
  for (const [name, got, want] of [
    // ── 该不该演习（`--wants 突袭` 的行为）──
    ['普通：默认演习优先 ⇒ 跑演习', drillPlan('普通'),
      { drill: true, why: 'drill-first', tickets: null, need: DRILL_TRIES }],
    ['【回归】突袭：默认**不跑演习**（实测 2/2 轴 gate3-invalid）⇒ 走老路径', drillPlan('突袭'),
      { drill: false, why: 'raid-no-drill', need: DRILL_TRIES }],
    ['突袭 + `--raid-drill`（默认关的开关）⇒ 才跑演习', drillPlan('突袭', { raidDrill: true }),
      { drill: true, why: 'drill-first', tickets: null, need: DRILL_TRIES }],
    ['`--no-drill` ⇒ 谁都不跑演习', drillPlan('普通', { drillFirst: false }),
      { drill: false, why: 'no-drill' }],
    // ── 演习券（用户口径：演习只烧演习券，每份轴 1 张）──
    ['【回归】券 0 < 需要 2 ⇒ **不演习、走老路径**（原因 drill_tickets_low，**不是** drill_broken）',
      drillPlan('普通', { tickets: 0, need: 2 }),
      { drill: false, why: 'drill_tickets_low', tickets: 0, need: 2 }],
    ['券 1 < 需要 2 ⇒ 不演习', drillPlan('普通', { tickets: 1, need: 2 }),
      { drill: false, why: 'drill_tickets_low', tickets: 1, need: 2 }],
    ['券刚好够（2 ≥ 需要 2）⇒ 照常演习', drillPlan('普通', { tickets: 2, need: 2 }),
      { drill: true, why: 'drill-first', tickets: 2, need: 2 }],
    ['券**读不到**（null）⇒ 照常演习、读数记 null（不瞎猜）', drillPlan('普通', { tickets: null, need: 2 }),
      { drill: true, why: 'drill-first', tickets: null, need: 2 }],
    ['【回归】演习券不足造成的"没结论" ⇒ no_tickets（**不许**记成 drill_broken ⇒ 免得白烧一次真打）',
      drillDecision([{ id: 11, stars: null, valid: false, notickets: true }]),
      { action: 'no_tickets', axis: null, why: 'drill_tickets_low' }],
    ['券不足 + 另一份 3★ ⇒ 真打那份 3★ 的（有结论优先）',
      drillDecision([{ id: 11, stars: null, valid: false, notickets: true }, { id: 22, stars: 3, valid: true }]),
      { action: 'real', axis: 22, why: 'drill-3star' }],
    // ── 收尾（不能把游戏留在战斗/暂停里）──
    ['【回归】真机 JSON（51786：still_battle:true、没有 left_battle 字段）⇒ 必须补收尾',
      needBattleRecovery(FX_51786), 'cleanup-failed'],
    ['收尾后结果帧仍在战斗（battle_after:true）⇒ 再收一次', needBattleRecovery({ battle_after: true }),
      'still-in-battle'],
    ['收尾成功（left_battle:true / battle_after:false）⇒ 不用再收尾',
      needBattleRecovery({ still_battle: true, left_battle: true, battle_after: false }), null],
    ['正常结算 ⇒ 什么都不做（别在正常画面上乱点）',
      needBattleRecovery({ still_battle: false, left_battle: true }), null],
    ['没有 JSON（工具没起来）⇒ 不乱动', needBattleRecovery(null), null],
    ['sweep 掐掉的那一局（超时）⇒ 收尾', needBattleRecovery(null, true), 'run-killed'],
    ['超时优先于 JSON 状态', needBattleRecovery(FX_51786, true), 'run-killed'],
    // 画面文字判据（与 stage_attempt.py::battle_hud_text 同口径）——两句都来自真机留证帧
    ['【回归】屏上 `27/37 … 演习中 … 剩余可放置角色` ⇒ 判战斗态',
      battleTextHint('27/37 2X X>演习中<> 剩余可放置角色：2'), true],
    ['【回归】屏上 `33/37 … 演习中 … PAUSE 暂停中 … 剩余可放置角色` ⇒ 判战斗态',
      battleTextHint('33/37 X>演习中<X> PAUSE 暂停中--- 剩余可放置角色：2'), true],
    ['战斗暂停菜单（只有按钮文案）⇒ 判战斗态', battleTextHint('继续战斗 放弃行动'), true],
    ['关卡详情页 ⇒ 不是战斗态', battleTextHint('CV-EX-8 附加条件 推荐平均等级 开始突袭 演习'), false],
    ['编队页（也有「剩余可放置角色」但没有演习中）⇒ 不是战斗态',
      battleTextHint('剩余可放置角色：2 开始行动'), false],
    ['读不到文字 ⇒ 不是战斗态（不乱动）', battleTextHint(null), false],
  ]) {
    const ok = eq(got, want)
    if (!ok) bad += 1
    console.log(`  ${ok ? '✓' : '✗'} ${name}`)
    if (!ok) console.log(`      got  ${JSON.stringify(got)}\n      want ${JSON.stringify(want)}`)
    cases.push([name, got, want])
  }
  // ── 【断言】演习的读数**永远不会**被记成"已通关/首通/源石到手" ────────────────
  //   用户口径（2026-09-24）：演习只消耗「演习券」，不消耗理智、**也永远不产出收益**；
  //   演习 3★ 只用于**挑轴**。所以：drill 行**不许**出现 `stone`（=源石到手），写字据的是 settleFinish 那条真打路径。
  for (const [name, row, want] of [
    ['【断言】演习 3★ 的账本行：mode=drill、有星数、**无 stone**',
      drillOutcomeRow('s', '普通', 1, 3, 'drill-3star', [], 'settle', true, false),
      { stars: 3, mode: 'drill', evidence: 'settle', note: 'drill-3star' }],
    ['【断言】演习 1★（有结算页证据）⇒ 记星数（挑轴要降权它），仍然**无 stone**',
      drillOutcomeRow('s', '普通', 2, 1, 'drill-1star', [], 'settle', true, true),
      { stars: 1, mode: 'drill', evidence: 'settle', note: 'drill-1star', not3: true }],
    ['【断言】上游硬塞 stone 进 drill 行 ⇒ 被 `outcomeRow` 抹掉（兜底断言）',
      outcomeRow('s', '普通', 3, 3, 'drill-3star', [], 'drill', false,
        { mode: 'drill', evidence: 'settle', stone: true }),
      { stars: 3, mode: 'drill', evidence: 'settle', note: 'drill-3star' }],
    ['对照：**真打** 3★ 的行照样带 stone=true（别把断言扩大化）',
      outcomeRow('s', '普通', 4, 3, 'settle-3star', [], 'settle', false,
        { mode: 'real', evidence: 'settle', stone: true }),
      { stars: 3, mode: 'real', evidence: 'settle', note: 'settle-3star', stone: true }],
  ]) {
    const probe = { stars: row.stars, mode: row.mode, evidence: row.evidence,
      note: row.note, not3: row.not3, stone: row.stone }
    const ok = eq(probe, want)
    if (!ok) bad += 1
    console.log(`  ${ok ? '✓' : '✗'} ${name}`)
    if (!ok) console.log(`      got  ${JSON.stringify(probe)}\n      want ${JSON.stringify(want)}`)
    cases.push([name, probe, want])
  }

  // ── **已落盘结算图夹具回归**（2026-09-24 误判修复的核心验收）────────────────────
  //   夹具 = `runtime/fixtures/settle/`：从 runtime/ 下那批真机截图**冻结**的 9 张副本
  //   （`runtime/_settle_*.png` 是活的抓屏文件，会被别的抓屏覆盖 ⇒ 必须拷贝固定，
  //    沿用 `runtime/fixtures/stars/` 的规矩），判据信号由 `runtime/_audit_settle_fixtures.py`
  //    离线实测、**真值由肉眼逐张核对**（`vision` 字段）。
  //   断言口径：`expect_clear` —— true=有正面通关证据 / false=有正面失败证据 / null=证据不足不判；
  //   外加"真失败那两张必然 clear=false（有「理智返还」）"与"真通关那张必须 clear=true"。
  try {
    const fx = JSON.parse(readFileSync(join(ROOT, 'runtime', 'fixtures', 'settle', 'fixtures.json'), 'utf8'))
    for (const f of (fx.images || [])) {
      const v = settleVerdict({
        onSettlePage: f.page_is_settle === true,
        stars: f.settle_stars, lit: f.settle_lit, blue: f.settle_blue,
        firstDrop: f.text_first_drop, sanityRefund: f.text_sanity_refund, drillFrame: f.text_drill,
        textSeen: true, panelStars: null, panelPrimes: null, maaDone: false, want: f.want,
      })
      const got = { clear: v.clear, action: v.action, why: v.why, not3: v.not3 }
      const want = { clear: f.expect_clear,
        action: f.expect_action === null ? v.action : f.expect_action, why: v.why, not3: v.not3 }
      const ok = v.clear === f.expect_clear && (f.expect_action === null || v.action === f.expect_action)
      if (!ok) bad += 1
      const miss = f.expect_action !== null && v.action !== f.expect_action
      console.log(`  ${ok ? '✓' : '✗'} 夹具 ${f.file}（${f.vision.slice(0, 28)}…）⇒ clear=${v.clear}/${f.expect_clear}`
        + ` action=${v.action}${miss ? `（期望 ${f.expect_action}）` : ''} why=${v.why}`)
      cases.push([`夹具 ${f.file}`, got, want])
    }
    // 【铁律】逐个夹具的硬断言（不靠 expectation 字段也能守住底线）
    const by = (n) => (fx.images || []).find((r) => r.file === n)
    const hard = [
      ['【铁律】1900 那张真通关（亮勋章 + 首次掉落、像素星数 0）**不得**判 failed',
        (() => { const f = by('clear_raid_firstdrop_CV-EX-8.png')
          const v = settleVerdict({ onSettlePage: true, stars: f.settle_stars, blue: f.settle_blue,
            firstDrop: f.text_first_drop, sanityRefund: f.text_sanity_refund, drillFrame: f.text_drill,
            textSeen: true, want: f.want })
          return { clear: v.clear, failed: v.action === 'failed', not3: v.not3 } })(),
        { clear: true, failed: false, not3: false }],
      ['【铁律】真失败那两张（三灰 + 理智返还）必须判 failed',
        ['fail_refund_MT-EX-8.png', 'fail_refund_15-20.png'].map((n) => { const f = by(n)
          return settleVerdict({ onSettlePage: true, stars: f.settle_stars, blue: f.settle_blue,
            firstDrop: f.text_first_drop, sanityRefund: f.text_sanity_refund, drillFrame: f.text_drill,
            textSeen: true, want: f.want }).action }), ['failed', 'failed']],
      ['【铁律】非结算页那 4 张（干员页/演习战斗/曲谱/暂停）都不能被判成"通关"',
        ['other_operator_page.png', 'other_drill_battle.png', 'other_storyline_book.png',
          'other_battle_pause.png'].map((n) => { const f = by(n)
          return settleVerdict({ onSettlePage: f.page_is_settle === true, stars: f.settle_stars,
            blue: f.settle_blue, firstDrop: f.text_first_drop, sanityRefund: f.text_sanity_refund,
            drillFrame: f.text_drill, textSeen: true, want: f.want }).clear !== true }), [true, true, true, true]],
    ]
    for (const [name, got, want] of hard) {
      const ok = eq(got, want)
      if (!ok) bad += 1
      console.log(`  ${ok ? '✓' : '✗'} ${name}`)
      if (!ok) console.log(`      got  ${JSON.stringify(got)}\n      want ${JSON.stringify(want)}`)
      cases.push([name, got, want])
    }
  } catch (e) {
    bad += 1
    console.log(`  ✗ 夹具回归异常（runtime/fixtures/settle/fixtures.json 读不到？）：${e.message}`)
  }
  // ── 日志措辞 + 赢家账本行（离线断言：真机跑出来的那句话/那行就是这个）──────────
  for (const [name, got, want] of [
    ['措辞：突袭真通关那句话里必须有「突袭通关」「首通源石到手」「不判 failed」',
      (() => {
        const v = settleVerdict({ onSettlePage: true, stars: 0, blue: [0, 0, 0], firstDrop: true,
          sanityRefund: false, textSeen: true, want: '突袭' })
        const t = settleVerdictText(v, { where: '结算页 0★', settleStars: 0 })
        return [t.includes('突袭通关'), t.includes('首通源石到手'), t.includes('不判 failed')]
      })(), [true, true, true]],
    ['账本：赢家轴的行 = stars:null（不骗 3★）+ stone:true + note=raid-first-drop + 无 not3（不降权）',
      (() => {
        const v = settleVerdict({ onSettlePage: true, stars: 0, blue: [0, 0, 0], firstDrop: true,
          sanityRefund: false, textSeen: true, want: '突袭' })
        const hasEvidence = v.stars !== null && (v.stars >= 1 || v.clear === false)
        const stone = v.stone === true || (v.stars !== null && v.stars >= 3)
        const row = outcomeRow('act28side_ex08', '突袭', 41427, hasEvidence ? v.stars : null, v.why, [],
          v.src, v.not3 === true, { mode: 'real', evidence: v.src,
            ...(hasEvidence ? {} : { valid: false }), ...(stone ? { stone: true } : {}),
            settle_clear: v.clear, settle_why: v.why })
        return { stars: row.stars, stone: row.stone, note: row.note, not3: row.not3,
          settle_clear: row.settle_clear }
      })(), { stars: null, stone: true, note: 'raid-first-drop', not3: undefined, settle_clear: true }],
    ['账本：**有正面失败证据**（理智返还）时照旧写 stars=0 —— 读账本的人要继续降权它（别把新规则扩大化）',
      (() => {
        const v = settleVerdict({ onSettlePage: true, stars: 0, blue: [0, 0, 0], firstDrop: false,
          sanityRefund: true, textSeen: true })
        const hasEvidence = v.stars !== null && (v.stars >= 1 || v.clear === false)
        const row = outcomeRow('s', '普通', 1, hasEvidence ? v.stars : null, v.why, [], 'settle',
          v.not3 === true, { mode: 'real', evidence: 'settle' })
        return { stars: row.stars, not3: row.not3, note: row.note }
      })(), { stars: 0, not3: true, note: 'sanity-refund' }],
  ]) {
    const ok = eq(got, want)
    if (!ok) bad += 1
    console.log(`  ${ok ? '✓' : '✗'} ${name}`)
    if (!ok) console.log(`      got  ${JSON.stringify(got)}\n      want ${JSON.stringify(want)}`)
    cases.push([name, got, want])
  }
  // ── **接线断言**（源码级）：这次的 bug 是"判据没接上/接错"，纯函数测不到这一段 ─────
  //   所以直接对源码断言那几处关键接线（`pit_check.py` 同款手法）。
  try {
    const src = readFileSync(join(ROOT, 'tools', 'event_sweep.mjs'), 'utf8')
    const deadWhy = 'settle-' + '0star'      // 拼出来，免得断言自己的字面量把自己匹配上
    const wiring = [
      ['接线：真打路径的结算页读数**要了奖励栏文字**（readSettleConfirmed(..., { wantText: true })）',
        /readSettleConfirmed\(settleShot, \{ wantText: true \}\)/.test(src), true],
      ['接线：`settleFrom` 把文字证据并进读数（...settleTextEvidence(st.text)）',
        /\.\.\.settleTextEvidence\(st\.text\)/.test(src), true],
      ['接线：**0★ 不再直接判 failed**（旧 `settle-?star` 判据已删）',
        !src.includes(`'${deadWhy}'`), true],
      ['接线：`result_stars` 只在真读到 ≥1★ 时才写（突袭通关的 0★ 不许写下去）',
        /rec\.result_stars = \(v\.stars !== null && v\.stars >= 1\) \? v\.stars : null/.test(src), true],
      // P5：写行策略改成**批次级、对称、链坏不罚**（旧断言 = 逐份跳过"最后试的那一份"，
      //   那正是 59743/59818 同批同证据一罚一放的来源 ⇒ 已删除）
      ['接线 P5：`runFallback()` **必须**走批次级写行（`fallbackRowPlan`），不许再逐份判',
        /const plan = fallbackRowPlan\(/.test(src), true],
      ['接线 P5：**写行者**不再产生 `fallback-tried`（除自测断言外，源码里没有以它作 `note` 的写行）',
        !/recordOutcome\([^)]*'fallback-tried'/.test(src)
          && /recordOutcome\(stage, want, p\.id, null, p\.note/.test(src), true],
      ['接线 P5：调用方把 `drill_broken` 传进 `runFallback`（链坏 ⇒ 整批只写中性行）',
        /chainBroken: rec\.drill_broken === true/.test(src)
          && /runFallback\(u\.stage, u\.want, \[\], tries, fbCtx\)/.test(src), true],
      ['接线 P4：⊘ 分支用配置阈值（`rewardNoPrimeGate(rec.reward_score, disc, { stars: starsNow })`）'
        + '并落四件套（`rewardAuditFields`）',
        /rewardNoPrimeGate\(rec\.reward_score, disc, \{ stars: starsNow \}\)/.test(src)
          && /Object\.assign\(rec, rewardAuditFields\(gate, stt\)\)/.test(src), true],
      ['接线：没有 `fb.accepted` 时账本仍记**最后一次试的轴**（ledgerId）',
        /const ledgerId = fb\.accepted \?\? fb\.lastTried \?\? null/.test(src), true],
      ['接线：判据+置信度落账（settle_clear / settle_why / settle_verdict_evidence）',
        /rec\.settle_verdict_evidence = v\.evidence/.test(src), true],
      // ── Lead 追加（P1/P2/P3）的接线断言：**判据接上了没**，纯函数测不到这一段 ──────────
      ['接线 P1：**④′/④″ 排在 ⑤ 之前**（徽章不再吞掉首通证据：探针 A/B 的真实字段形状）',
        src.indexOf('if (badgeRemains && !firstDrop)') > 0
          && src.indexOf('if (badgeRemains && !firstDrop)') < src.indexOf('if (firstDrop) {'), true],
      ['接线 P1：冲突分支 `firstdrop-badge-conflict` 存在且带 `conflict` 落账（账本 settle_conflict）',
        src.includes("'firstdrop-badge-conflict'") && src.includes('settle_conflict'), true],
      ['接线 P2：`opers` 三态（写 null，不许默认 []）+ `opers_state` 落账',
        /opers: Array\.isArray\(opers\) && opers\.length \? opers : null/.test(src)
          && /opers_state:/.test(src), true],
      ['接线 P3：⊘ 分支**必须**过 `rewardNoPrimeGate` 并落判别器（不许裸判 ⊘）',
        /const ng = rewardNoPrimeGate\(/.test(src) && /rec\.discriminator = disc/.test(src), true],
      ['接线 P3：`action = \'no_prime\'`（终态）只在 `if (ng.terminal)` 里出现 —— 判据不许旁路',
        src.indexOf('if (ng.terminal)') > 0
          && src.indexOf("rec.action = 'no_prime'") > src.indexOf('if (ng.terminal)'), true],
      ['接线 P3：降级路径必须标 `needs_discriminator` + `⊘?（待判别）`（供账本/顶栏对账）',
        /rec\.needs_discriminator = true/.test(src) && src.includes('⊘?（待判别）'), true],
    ]
    for (const [name, got, want] of wiring) {
      const ok = eq(got, want)
      if (!ok) bad += 1
      console.log(`  ${ok ? '✓' : '✗'} ${name}`)
      if (!ok) console.log(`      got ${JSON.stringify(got)} want ${JSON.stringify(want)}`)
      cases.push([name, got, want])
    }
    // ── P4 端到端：**`reward_primes=None` 的行必须被查询工具抓出来**（离线；不连模拟器）──────
    //   为什么放在这里：四件套纯函数只证明"我们自己写得出 null"，**抓不抓得到**要看查询工具
    //   （`tools/pending_reverify.py`）的真实选择规则 ⇒ 让自测直接跑它一次，断言被抓的集合。
    try {
      const q = spawnSync(PY, ['tools/pending_reverify.py', '--selftest'],
        { cwd: ROOT, encoding: 'utf8', timeout: 120000 })
      let qj = null
      try { qj = JSON.parse(String(q.stdout || '').trim().split('\n').pop()) } catch { /* 输出不是 JSON */ }
      const s = qj?.selftest
      const got = { ok: q.status === 0 && s?.ok === true,
        caught: s?.caught ?? null, cleanNotCaught: (s?.clean_caught ?? ['x']).length === 0,
        absentEdgeNotCaught: s?.edge_absent_not_caught === true,
        keyVsNull: s?.key_vs_null_distinguished === true,
        junkLines: s?.junk_lines_handled ?? null }
      const want = { ok: true, caught: ['A-1|普通', 'A-2|普通', 'A-3|普通', 'A-4|普通', 'A-5|普通'],
        cleanNotCaught: true, absentEdgeNotCaught: true, keyVsNull: true, junkLines: 3 }
      const ok = eq(got, want)
      if (!ok) bad += 1
      console.log(`  ${ok ? '✓' : '✗'} P4 查询工具：reward_primes=None / 分数缺失 / 分数 > absent / stars<3 `
        + `⇒ 全部被抓（干净行与 score=absent 的边界不抓）`)
      if (!ok) console.log(`      got  ${JSON.stringify(got)}\n      want ${JSON.stringify(want)}`
        + `\n      stderr ${String(q.stderr || '').slice(0, 200)}`)
      cases.push(['P4 查询工具抓 reward_primes=None', got, want])
    } catch (e) {
      bad += 1
      console.log(`  ✗ P4 查询工具自测异常：${e.message}`)
    }
  } catch (e) {
    bad += 1
    console.log(`  ✗ 接线断言异常：${e.message}`)
  }
  for (const [name, got, want] of cases) {
    const ok = eq(got, want)
    if (!ok) bad += 1
    console.log(`  ${ok ? '✓' : '✗'} ${name}`)
    if (!ok) console.log(`      got  ${JSON.stringify(got)}\n      want ${JSON.stringify(want)}`)
  }
  console.log(`\n--selftest（结算页星数判定，不连模拟器）：${cases.length - bad}/${cases.length} 通过`)
  return bad ? 1 : 0
}
if (has('selftest')) process.exit(runSelfTest())
if (has('settle-replay')) process.exit(runSettleReplay())

// ── 主流程 ──────────────────────────────────────────────────────────────
const units = []
for (const code of expand(STAGES_SPEC)) {
  for (const want of WANTS) {
    if (want === '突袭' && !hasRaid(code)) continue
    units.push({ code, want, stage: stageName(code) })
  }
}

const stamp = (() => {
  const d = new Date(); const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}`
})()
mkdirSync(LEDGER_DIR, { recursive: true })
const ledger = join(LEDGER_DIR, `event_sweep_${EVENT}_${stamp}.jsonl`)
const summary = join(LEDGER_DIR, `event_sweep_${EVENT}_${stamp}.md`)

log(`横扫 ${EVENT}：${units.length} 个单位（关卡×难度）｜emu #${EMU}｜每关最多试 ${MAX_TRIES} 份｜模式=${APPLY ? 'APPLY（真跑）' : 'DRY-RUN（只看）'}`)
log(`账本：${ledger}`)
for (const u of units) log(`  · ${u.code} ${u.want} → ${u.stage}`)
if (!APPLY) {
  log('这是 dry-run —— 要真动手加 --apply。')
  process.exit(0)
}

// 剧情看门狗：整轮横扫期间常驻（每 1.2s 问一次右上角），收工时自停。
//   只在整个流程里开**一个**，不是"每步 spawn 一个 Python"。
startStoryWatch()
process.on('SIGINT', () => { stopStoryWatch(); process.exit(130) })

// **服务契约自检**（2026-09-24，task-3）：结算页星数 / 低置信 null 这套判据是这次才上的，
//   服务还跑着旧代码时（内存里是旧版）`/all` 里没有 `result_conf`/`result_stars`，
//   而旧 `stars` 是坏除数（面板能读出 9 星、结算页读到的是干员立绘）—— 拿它判 done/⊘
//   会直接花掉理智还给出假结论。⇒ **缺字段就停下**（重启服务只要 5 秒，别拿坏读数硬跑）。
//   服务整个不在时不拦（下面还有 Python 兜底那条路）。
const CONTRACT = await readAll()
if (CONTRACT && CONTRACT.ok && CONTRACT.result_conf === undefined) {
  log('✗ 常驻服务是**旧版**：/all 里没有 result_conf/result_stars/stars_conf（结算页星数、低置信 null 都拿不到）')
  log('   ⇒ 现在跑出来的"星数/⊘"都不可信，先重启服务再来：')
  log('      python tools/maaorch_service.py --port 8765')
  stopStoryWatch()
  process.exit(2)
}

const results = []
const halt = { stop: false }
for (const [idx, u] of units.entries()) {
  const tag = `${idx + 1}/${units.length} ${u.code} ${u.want}`
  const rec = { ...u, ts: new Date().toISOString(), action: '', star: null, verdict: '', tried: [] }
  try {
    log(`▶ ${tag}｜导航 + 核实关卡身份…`)
    // **进图守卫已删除**（2026-09-24，实测证据）：它花 91 秒重新进活动（还失败），
    //   紧接着 `nav_map` 只用 **1.2 秒**就导航成功 ⇒ 人本来就在图里，守卫纯属白跑，
    //   而且它是"每关一次"，正是用户说的"从头再来"。导航本身会核对关卡代号，
    //   不在图里的话导航自然失败，不需要前置守卫。
    let nav = await navTo(u.code, u.want)
    rec.sanity = nav.sanity ?? null
    rec.on_page = nav.on_page || ''
    if (!nav.ok && ENTER_FALLBACK) {
      // **导航失败 ⇒ 先"按卡片模板进活动"再试一次**（2026-09-24 实测必需）：
      //   卡在「完成进度」档案页/主线章节地图那种位置时，nav_map 与 mt_nav 都会把人**带进
      //   主线地图**（10:39 那次 EA-1 就这么 block 的），而 `enter_by_template` 按活动卡片
      //   模板进活动命中 0.978 ⇒ 进去之后再导航才有意义。
      const slug = eventSlug()
      if (slug) {
        log(`  ⟳ 导航失败 ⇒ 按卡片模板进活动（enter_by_template --event ${slug} --prefix ${PREFIX}）后重试`)
        const ent = await run(PY, ['tools/enter_by_template.py', '--event', slug, '--prefix', PREFIX],
          { timeoutMs: 420000, label: `enter ${slug}` })
        const el = String(ent.out).trim().split('\n').filter((l) => l.trim().startsWith('{')).pop()
        let ej = null
        try { ej = JSON.parse(el) } catch { /* 输出不是 JSON ⇒ 就当没成功 */ }
        rec.enter_event = ej ? (ej.ok ?? null) : false
        if (ej && ej.ok) {
          log(`    进活动 ✓（${ej.page || ej.detail || ''}）→ 重试导航`)
          nav = await navTo(u.code, u.want)
          rec.sanity = nav.sanity ?? rec.sanity
          rec.on_page = nav.on_page || rec.on_page
        } else {
          log(`    进活动没成功：${(String(ent.out) + String(ent.err)).slice(-200)}`)
        }
      } else {
        log(`  ⟳ 导航失败，但 data/templates/events/index.json 里查不到 ${EVENT} 的卡片模板 ⇒ 不走 enter_by_template`)
      }
    }
    // **导航失败会不会是"被留在战斗里"**（2026-09-24 实测，CV-EX-8 之后那 4 次导航全废）：
    //   屏上文字像战斗/暂停态（「演习中」+「剩余可放置角色」、PAUSE 暂停中、继续战斗/放弃行动）
    //   ⇒ 先把游戏带出战斗，再重试一次导航；成功就当没这回事（不记 blocked，也不再误标 noreach）。
    if (!nav.ok) {
      const hint = await readScreenText()
      if (battleTextHint(hint)) {
        log('  ⟳ 导航失败，且屏上像"战斗/暂停态" ⇒ 先收尾把游戏带出战斗，再重试一次导航')
        rec.nav_battle_hint = String(hint || '').slice(0, 80)
        await leaveBattle(`${stamp}_${u.code}_nav`, 'nav-blocked-in-battle')
        const nav2 = await navTo(u.code, u.want)
        if (nav2.ok) {
          nav = nav2
          rec.sanity = nav2.sanity ?? rec.sanity
          rec.on_page = nav2.on_page || rec.on_page
          log(`    ✓ 收尾之后导航成功（${nav2.on_page || '?'}）`)
        } else {
          log(`    ⚠️ 收尾之后导航仍失败：${nav2.detail || '?'}`)
        }
      }
    }
    if (!nav.ok) {
      // **blocked 不许静默**（2026-09-24，Lead 要求）：把"断在哪一步 + 当时到底在哪一屏"连同
      //   证据帧一起记下来。上一次 EA-1 就是反面教材：报告里只有一句"5 轮内没能进到详情页"，
      //   回看画面才发现人被带进了「Main Story EPISODE 01」的**主线地图**（不是活动地图），
      //   于是怎么导航都找不到 EA-1 —— 没有这一帧，只能靠猜。
      rec.action = 'blocked'
      rec.blocked_step = 'nav'                        // 断在「导航 + 核实关卡身份」这一步
      const bShot = `runtime/_blocked_${stamp}_${u.code}.png`
      const stB = await readAll(bShot)
      rec.blocked_page = stB?.page ?? null
      rec.blocked_page_score = stB?.page_score ?? null
      rec.blocked_shot = bShot
      rec.verdict = `${nav.detail || '导航/核实失败'}｜断在=${rec.blocked_step}｜当时页面=${stB?.page ?? '?'}`
        + `（分 ${stB?.page_score ?? '?'}）｜帧=${bShot}`
      log(`  ✗ ${rec.verdict}`)
    } else if (nav.sanity != null && nav.sanity < SANITY_MIN) {
      rec.action = 'insufficient_sanity'
      rec.verdict = `理智 ${nav.sanity} < 门槛 ${SANITY_MIN} ⇒ 停止横扫、先补理智（药由 MAA 自动吃，源石只能手动用）`
      log(`  ⏸ ${rec.verdict}`)
      halt.stop = true
    } else {
      log(`  已在 ${nav.on_page}（${nav.difficulty}，理智=${nav.sanity ?? '?'}）｜判"报酬里有没有源石"…`)
      // **闸门必须可信才采信**（2026-09-24 实测教训）：那次 EA-1 打印的是
      //   `已在 undefined（undefined，理智=?）` 却照样给出 `⊘ 报酬无源石（分 0.644）`
      //   —— 在**没确认页面/没读到关卡身份**的情况下读的分是垃圾，跟 CV 上读出"11 星"同类。
      //   规则：页面不是「关卡详情」就重读（46ms/次，最多 3 次）；仍不可信 ⇒ 记"读数不可信"，
      //   **绝不据此判 ⊘**（⊘ 会让我们白白错过矿）。
      let gate = await panelGate()
      // **改用服务直接读"现在什么页/几星/有没有徽章"**（2026-09-24 修我自己刚犯的 bug）：
      //   上一版我用 `nav.on_page` 判断页面，而 `/nav` 的返回里**没有这个字段** ⇒ 恒为 undefined
      //   ⇒ 恒判"读数不可信" ⇒ 明明站在 EA-1 面板前也不打（我亲眼看过：EA-1 ☆☆☆、
      //   报酬「三星获得」、开始行动 −9）。这里改成读 `/all`（readAll 就是这一条）。
      let stt = await readAll()
      for (let i = 0; i < 3 && (!stt || stt.page !== '关卡详情'); i++) {
        await new Promise((r) => setTimeout(r, 900))
        stt = await readAll()
        gate = await panelGate()
      }
      // **可审计四件套**（铁律）：每条 ⊘/unreliable 行都凑齐 —— `reward_score` +
      //   `reward_primes`（三态 true/false/**null**）+ 页面守卫结果（`page` + `page_guard`）
      //   + `stars`。旧代码只在"页面对了"之后才落 ⇒ 70 条 ⊘ 里 47 条 `reward_primes` 压根没采。
      Object.assign(rec, rewardAuditFields(gate, stt))
      if (rec.page_guard !== 'ok') {
        rec.action = 'unreliable'
        rec.needs_discriminator = true
        rec.verdict = `闸门读数不可信（页面守卫=${rec.page ?? '?'} ⇒ reject）⇒ 本轮不打、也不判 ⊘`
        log(`  ⚠️ ${rec.verdict}`)
        results.push(rec)
        continue
      }
      // **报酬栏判决 = 唯一函数 `rewardVerdict()`**（阈值单一来源：`runtime/config/reward_gate.json`）：
      //   · `present`（≥ 0.75）⇒ **有矿**，打；
      //   · `absent`（≤ 0.72） ⇒ **无矿** ⇒ 走下面的 ⊘ 证据强度闸（判别器 + stars 一致性）；
      //   · `ambiguous`（分数缺 / 落 (0.72, 0.75) 窄带）⇒ 不判 ⊘、记 unreliable 等下轮；
      //   · 分数说"无矿"而布尔 `reward_primes === true` ⇒ 自相矛盾 ⇒ 同样 unreliable。
      //   ★ **低分不再是"读数不可信"**：0.46–0.70 这批是很确信的"不在"（旧代码把它们降级成
      //     unreliable ⇒ ⊘ 永远无法终结、队列把每个已领关无限重走 —— Lead 2026-09-24 定案）。
      const primes = rec.reward_primes
      const starsNow = rec.stars
      let hasStone = null
      if (rec.reward_verdict === 'present') hasStone = true
      else if (rec.reward_verdict === 'absent' && primes !== true) hasStone = false
      if (hasStone === null) {
        // 缺读数/矛盾：**绝不判 ⊘**（⊘ 会让我们白白错过矿），记 unreliable 等下轮
        rec.action = 'unreliable'
        rec.needs_discriminator = true
        rec.verdict = `报酬栏读数待判（verdict=${rec.reward_verdict}, stars=${starsNow ?? '?'},`
          + ` reward_primes=${primes === null ? 'null' : primes}, 分 ${rec.reward_score ?? '?'}）`
          + ` ⇒ 本轮不打、也不判 ⊘（不进 noreach）`
        log(`  ⚠️ ${rec.verdict}`)
        results.push(rec)
        continue
      }
      if (!hasStone) {
        // ── ⊘ 的**证据强度**闸（2026-09-24 Lead 定案，取代旧的 0.46–0.64 歧义带）──────────────
        //   旧的歧义带是从 ⊘ 观测簇**反推**的，不是门槛：它把 0.46~0.70 这批**很确信的「无矿」**
        //   一律降级成 unreliable ⇒ ⊘ 永远无法终结。现在只有三条同时成立才允许下终态：
        //     ① `rewardVerdict(score) === 'absent'`（分数 ≤ 0.72 ⇒ 真无矿）；
        //     ② 至少一项判别器读到（徽章 ROI 有假阴性前科：CV-EX-1..8 旧 ⊘ 已回滚成 todo）；
        //     ③ **内部一致性**：`stars` 是整数时不许 <3（没通关的关不可能没有首通源石）。
        //   任一不成立 ⇒ `⊘?（待判别）`（action=unreliable）+ needs_discriminator，
        //   队列语义＝本轮不打也不判 ⊘、保持 todo 复查、**不进 noreach**。
        const gtxt = await readAll('', { text: true })
        const disc = rewardDiscriminator({ page: rec.page, reward_score: rec.reward_score },
          gtxt?.text, u.want)
        const ng = rewardNoPrimeGate(rec.reward_score, disc, { stars: starsNow })
        rec.discriminator = disc
        rec.reward_obtain_tag = disc.reward_obtain_tag
        rec.lock_raid = disc.lock_raid
        rec.lock_auto = disc.lock_auto
        rec.mode = u.want
        rec.reward_ambiguous = ng.ambiguous
        rec.reward_verdict = ng.verdict
        rec.reward_contradiction = ng.contradiction
        const dTxt = `reward_obtain_tag=${disc.reward_obtain_tag ?? '?'}、lock_raid=${disc.lock_raid ?? '?'}`
          + `、lock_auto=${disc.lock_auto ?? '?'}、mode=${u.want}`
        if (ng.terminal) {
          rec.action = 'no_prime'
          rec.verdict = `报酬无源石（分 ${rec.reward_score ?? '?'} ≤ absent ${REWARD_ABSENT}；`
            + `${starsNow} 星、徽章已消失；判别器：${dTxt}；stars≥3 ⇒ 与"无矿"自洽）⇒ 不打`
          log(`  ⊘ ${rec.verdict}`)
        } else {
          rec.action = 'unreliable'
          rec.needs_discriminator = true
          const why = ng.contradiction
            ? `**内部矛盾**：stars=${starsNow} < 3 却说"无矿"（没通关的关不可能没有首通源石）`
            : (ng.ambiguous
              ? `分数 ${rec.reward_score ?? '?'} 说不准（缺读数，或落 (absent ${REWARD_ABSENT}, `
                + `present ${REWARD_PRESENT}) 窄带）`
              : '分数说"无矿"但判别器缺')
          rec.verdict = `⊘?（待判别）：${why}＋判别器${ng.hasDisc ? '有' : '**缺**'}（${dTxt}）`
            + ` ⇒ **本轮不打、不判终态、不进 noreach**，标 needs-discriminator 等下一轮复查`
          log(`  ⊘? ${rec.verdict}`)
        }
      } else {
        // ★ 2026-09-24（Lead 追加口径）：**演习只消耗「演习券」**，不消耗理智、也永远不产出收益。
        //   ⇒ 走演习之前先定"这个单位该不该演习"（突袭不演习 / 没券不演习），再谈跑。
        let plan = drillPlan(u.want)
        let tk = { tickets: null, low: false, text: '' }
        if (plan.drill) {
          tk = await readDrillTickets(`${stamp}_${u.code}`)
          rec.drill_tickets = tk.tickets
          plan = drillPlan(u.want, { tickets: tk.tickets })
          log(`  🎫 演习券读数：${tk.tickets === null ? 'null（读不到 ⇒ 照常演习，不瞎猜）' : `${tk.tickets} 张`}`
            + `（需要 ${DRILL_TRIES} 张，每份轴 1 张；读数来源 tools/stage_attempt.py --read-tickets）`)
        }
        rec.drill_plan = plan.why
        log(`  报酬有源石（分 ${gate?.reward_score ?? '?'}，${starsNow ?? '?'} 星，徽章在）→ `
          + (plan.drill ? '**演习优先**：先 0 理智试轴' : '挑作业并降级重试') + '…')
        // ★ task-15（2026-09-24）：**默认演习优先** —— 先 drill（0 理智）拿到 3★ 的轴才真打。
        //   老路径（试 MAX_TRIES 份轴**直接真打**）在三种情况下走：
        //     ① 显式 `--no-drill`；② **突袭**（实测演习通道对突袭不可用，见 `DRILL_RAID`）；
        //     ③ 演习通道整体不可用（全轴都没跑出结算页 ⇒ drill_broken）或**演习券不足**。
        let fb = null
        if (plan.drill) {
          const dp = await drillPhase(u, rec, `${stamp}_${u.code}`)
          rec.drill = dp.drills
          rec.drill_why = dp.why
          if (dp.action === 'no_axis') {
            const dTxt = (d) => `prts://${d.id}=` + (d.valid ? `${d.stars}★` : (d.leaked ? '漏怪(不可能3★)' : '无结论'))
            const detail = dp.drills.map(dTxt).join('、') || '（无候选）'
            rec.action = 'no_axis'
            rec.verdict = dp.why === 'no_candidate'
              ? '没有候选轴（筛选为空）⇒ 不真打'
              : `演习试了 ${dp.drills.length} 份轴（${detail}）**一份都没到 3★** ⇒ 不真打、标 no_axis（**零理智消耗**）`
            log(`  ⊘ ${rec.verdict}`)
          } else if (dp.action === 'real') {
            const rr = await runAttempt(u.stage, u.want, dp.axis.id,
              { drill: false, tag: `${stamp}_${u.code}_real_${dp.axis.id}`, code: u.code })
            rec.real_axis = String(dp.axis.id)
            rec.real_mode = 'after-drill'
            rec.real_stars = rr.stars
            log(`  ▶ 真打 prts://${dp.axis.id} ⇒ ${rr.valid ? `${rr.stars}★（${rr.kind}）` : `读数无效（${rr.why}）`}`
              + '｜只真打这一次（演习已 3★，不再试别的轴）')
            fb = {
              verdict: rr.valid && rr.stars === 3 ? 'done' : (rr.valid ? 'failed' : 'no_report'),
              candidates: 1, report: null, mix: {},
              accepted: String(dp.axis.id), acceptedOpers: dp.axis.used || [], realRun: rr,
            }
          } else if (dp.action === 'no_tickets') {
            // **没券 ⇒ 走老路径**（正常 MAX_TRIES），**不记 drill_broken**（那会白烧一次真打）。
            rec.drill_skipped = 'drill_tickets_low'
            rec.drill_why = 'drill_tickets_low'
            log('  ⛔ 演习因**演习券不足**中止（读数见上；这不是"演习通道坏"）⇒ 交给老的降级重试路径'
              + '（如实记 drill_tickets_low）')
          } else if (dp.action === 'drill_broken' && DRILL_STRICT) {
            rec.action = 'no_axis'
            rec.verdict = '演习通道不可用且开了 --drill-strict ⇒ 不真打（标 no_axis，零理智）'
            log(`  ⊘ ${rec.verdict}`)
          } else if (dp.action === 'drill_broken') {
            rec.drill_broken = true
            log(`  ⚠️ 演习通道整体不可用（${dp.drills.length} 份轴都没跑出结算页）⇒ 退回老的"直接真打"路径（如实记账 drill_broken）`)
          }
        } else if (plan.why === 'raid-no-drill') {
          // ★ 2026-09-24（本批修复）：**突袭不跑演习** —— 真机 2/2 份轴都是 gate3-invalid（MAA 报
          //   Completed 却没有结算页），第二份还把游戏留在「演习中 + PAUSE」的战斗里，后面 4 次导航全废。
          rec.drill_skipped = 'raid-no-drill'
          log('  ⛔ 突袭关卡**不跑演习**（实测 2/2 份轴 gate3-invalid，且会把游戏留在战斗里）'
            + '⇒ 直接走老的"降级重试"路径（要强行开启用 `--raid-drill`，默认关）')
        } else if (plan.why === 'drill_tickets_low') {
          // ★ 2026-09-24（Lead 追加口径）：演习只烧「演习券」⇒ 券不够就别演习，走老路径；
          //   **绝不许**记成 drill_broken（那是"通道坏"，会按限流真打，白烧一次）。
          rec.drill_skipped = 'drill_tickets_low'
          log(`  ⛔ 演习券不足（${tk.tickets} < 需要 ${DRILL_TRIES} 张）⇒ 不跑演习，直接走老的降级重试路径`
            + '（如实记 drill_tickets_low，**不是** drill_broken）')
        }
        if (!fb && rec.action !== 'no_axis') {
          // drill_broken ⇒ 限流（默认只真打 1 份轴；`--drill-broken-tries 0` = 完全不退）
          const tries = rec.drill_broken ? DRILL_BROKEN_TRIES : MAX_TRIES
          if (tries === 0) {
            rec.action = 'no_axis'
            rec.verdict = '演习通道整体不可用，且 `--drill-broken-tries 0` ⇒ **不真打**（零理智优先，标 no_axis）'
            log(`  ⊘ ${rec.verdict}`)
          } else {
            // **链坏标记一并传下去**（Lead 2026-09-24）：`drill_broken` ⇒ 整批只写中性行，
            //   禁止任何负向结论（否则就是"用坏链的失败去定轴的罪"）。
            const fbCtx = { chainBroken: rec.drill_broken === true,
              drillBroken: rec.drill_broken === true, drillWhy: rec.drill_why ?? null }
            fb = await runFallback(u.stage, u.want, [], tries, fbCtx)
            // **改作业 · 放宽练度门槛**（2026-09-24 实装）：本号专精零，热门轴常写「专10 模1」
            //   ⇒ MAA 直接判 unusable、连战斗都不开。这时带 `--ignore-requirements` 再跑一轮 ——
            //   实测（PV-EX-8）三份原本 unusable 的轴都能真打完；能不能三星另说，至少不是白跑。
            if (fb.verdict !== 'done' && (fb.report?.tries || []).some((t) => t.verdict === 'unusable')) {
              log('  有作业卡在练度要求 ⇒ 用 --ignore-requirements 再试一轮（改作业·放宽门槛）')
              fb = await runFallback(u.stage, u.want, ['--ignore-requirements'], tries, fbCtx)
            }
            // **写行策略落账**（可审计）：本批写了什么标签、链是否已判坏
            rec.axis_rows = (fb.rowPlan || []).map((p) => ({ id: p.id, note: p.note }))
            rec.axis_rows_chain_broken = fb.chainBroken === true
          }
        }
        if (rec.action !== 'no_axis') await judgeAfterRun(u, rec, fb)
      }
    }
  } catch (e) {
    rec.action = 'error'
    rec.verdict = String(e.message || e)
    log(`  ✗ 异常：${rec.verdict}`)
  }
  appendFileSync(ledger, JSON.stringify(rec) + '\n', 'utf8')
  results.push(rec)
  if (halt.stop) {
    log('⏸ 到理智门槛，提前收工（已完成的都在账本里；补完理智重跑同一条命令，已三星的关会自动跳过）')
    break
  }
  // 单位之间的喘息：短等即可（原来固定 2000ms 属"固定盲等"，task-17 issue#5）
  await sleep(600)
}

// 汇总
stopStoryWatch()
const GOOD = new Set(['skip', 'done', 'no_prime'])
const ok = results.filter((r) => GOOD.has(r.action))
const bad = results.filter((r) => !GOOD.has(r.action))
const lines = [
  `# ${EVENT} 普通+突袭 横扫报告（${stamp}）`, '',
  `- 单位：${results.length}（拿到/已通关/报酬无源石 **${ok.length}**，未通 **${bad.length}**）`,
  `- emu #${EMU}｜每关最多试 ${MAX_TRIES} 份｜理智门槛 ${SANITY_MIN}｜剧情跳过 ${STORY.n} 次（通道 ${STORY.mode}）｜账本：\`${ledger}\``,
  `- 轴选择：${DRILL_FIRST ? `**演习优先**（先 drill，拿到 3★ 才真打；每关最多试 ${DRILL_TRIES} 份轴${DRILL_STRICT ? '，--drill-strict' : ''}）`
    + `｜**突袭不跑演习**${DRILL_RAID ? '（本轮被 `--raid-drill` 强行打开）' : '（实测 2/2 份轴 gate3-invalid + 把游戏留在战斗里，默认关）'}`
    + `｜演习券 < ${DRILL_TRIES} 张 ⇒ 不演习（记 \`drill_tickets_low\`，走老路径）` : '直接真打（--no-drill）'}`,
  ...(DRILL_FIRST ? [`- 省理智：本报告里 \`演习\` 列的 1~2★ 轴**一份都没真打**；\`no_axis\` = 全部轴演习都不达标 ⇒ 该关零理智放弃`,
    '- 演习的账：演习**只消耗「演习券」**、不消耗理智、**也永远不产出收益**（不发首通源石、不算通关）；'
    + '`演习券` 列是本关的读数（`null` = 没读到，不瞎猜）'] : []), '',
  ...(halt.stop ? ['- ⏸ **提前收工**：到理智门槛而停（不是跑完）——补理智后重跑同一条命令即可续跑', ''] : []),
  '| 关卡 | 难度 | 结果 | 星数 | 演习 | 演习券 | 说明 | 试过的作业 |', '|---|---|---|---|---|---|---|---|',
  ...results.map((r) => `| ${r.code} | ${r.want} | ${r.action === 'unreliable' && r.needs_discriminator ? '⊘? 待判别（不进终态）' : r.action === 'no_prime' ? '⊘ 报酬无源石' : r.action === 'done' ? '✅ 通关' : r.action === 'skip' ? '✅ 已通关' : '❌ ' + (r.action || r.verdict)} | ${r.stars_final ?? r.stars ?? '-'} | ${(r.drill || []).map((d) => `prts://${d.id}=${d.valid ? `${d.stars}★` : (d.leaked ? '漏怪' : (d.notickets ? '没券' : '无结论'))}`).join(' ') || (r.drill_broken ? '通道不可用' : (r.drill_skipped === 'raid-no-drill' ? '突袭不跑演习' : (r.drill_skipped === 'drill_tickets_low' ? '券不足' : '-')))} | ${r.drill_tickets ?? (r.drill_skipped === 'raid-no-drill' ? '（不适用）' : 'null')} | ${r.result || r.verdict} | ${(r.tried || []).map((t) => `prts://${t.id}(${t.verdict})`).join(' ') || (r.real_axis ? `prts://${r.real_axis}(真打)` : '-')} |`),
]
writeFileSync(summary, lines.join('\n') + '\n', 'utf8')
log(`\n完成：成功/已三星 ${ok.length} / ${results.length}。报告：${summary}`)
if (bad.length) log('未通：' + bad.map((r) => `${r.code}${r.want}(${r.verdict})`).join('、'))
