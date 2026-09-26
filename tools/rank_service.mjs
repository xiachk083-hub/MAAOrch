#!/usr/bin/env node
/**
 * rank_service —— **与模型无关**的作业排序接口层（2026-09-24）
 *
 * 为什么先写这一层：用户提的 Laya 方案（Node 在线 choice 排序 + Python 离线微调 + ONNX 回灌）
 * 技术上可行，但**当下不划算**：Laya 零样本准确率 0.362≈随机，必须微调；而微调要 ~150 条标签，
 * 我们账本才 20~30 条、每关还只有 1~6 条；同时 `mastery_known:false`（专精/模组读不到），
 * state 里最关键的一格是空的 —— 这时候上 1.7GB 模型只会学噪声。
 *
 * 所以我把方案里**不依赖模型的那一半**先落地：
 *   · `formatState()` / `formatCriteria()`：Laya 的输入格式（也是任何表格模型的特征来源）
 *   · `rankCandidates()`：排序入口，签名与用户方案一致
 *   · `RANKER` 环境变量切换实现：rule（现状）| gbm | laya，**任何异常/超时都回退到 rule**
 *   ⇒ 等账本够了，接 Laya/GBM 只是替换 `rankWith()` 里的一个分支，管道一行都不用动。
 *
 * 用法（CLI 自测）:
 *   node tools/rank_service.mjs --stage act39side_ex07
 *   RANKER=rule node tools/rank_service.mjs --stage act39side_ex07 --json
 */
import { readFileSync, existsSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const RT = join(ROOT, 'runtime')
const RANKER = (process.env.RANKER || 'rule').toLowerCase()
const TIMEOUT_MS = Number(process.env.RANKER_TIMEOUT_MS || 3000)

const readJson = (p, d = null) => { try { return JSON.parse(readFileSync(p, 'utf8')) } catch { return d } }

/** 账号 box：目前只有精英化/等级（MAA OperBox 读不到专精/模组） */
export function loadAccountBox() {
  const snap = readJson(join(RT, 'account_snapshot.json'), {})
  return { mastery_known: !!snap.mastery_known, opers: snap.opers || {} }
}

/** 一份作业 → Laya `criteria` 里的一行自然语言描述（也是人看的解释） */
export function formatCriteria(c) {
  const req = (c.requirements || []).join('、') || '无练度要求'
  const used = (c.used || []).join('/')
  const tags = []
  if (/挂机/.test(c.title || '')) tags.push('摆完挂机')
  if (/单核|双核/.test(c.title || '')) tags.push('借核')
  return `${c.title || c.id}｜干员：${used}｜要求：${req}${tags.length ? '｜' + tags.join('/') : ''}`
}

/** 候选池 + 账号 → Laya `state`（同一份数据以后也能直接喂 GBM 特征） */
export function formatState(cands, box, level) {
  return {
    关卡: level,
    账号box: box.opers,
    专精模组已采集: box.mastery_known,
    作业列表: cands.map((c) => ({
      op_id: String(c.id),
      干员: c.used || [],
      要求: c.requirements || [],
      声明缺口: c.missing || [],
      热度: c.like ?? null,
      难度: c.difficulty ?? null,
    })),
  }
}

/** 规则打分（现状实现，也是所有模型不可用时的回退） */
function scoreRule(c, box) {
  const own = box.opers || {}
  const reqs = c.requirements || []
  const miss = (c.used || []).filter((n) => !own[n]).length
  if (miss >= 2) return { id: c.id, score: -9, note: `缺口${miss}⇒踢` }
  let s = 0.35 - 0.10 * reqs.length - 0.15 * miss
  if (/挂机/.test(c.title || '')) s += 0.10
  s += 0.05 * Math.min(1, Math.log10((c.like || 0) + 1) / 4)
  return { id: c.id, score: Number(s.toFixed(4)), note: `缺口${miss}` }
}

/** 本号战绩：某关用这份作业跑过几次、几次三星 */
function historyScore(level, id) {
  const p = join(RT, 'axis_outcomes.jsonl')
  if (!existsSync(p)) return { n: 0, p: 0.5 }
  let n = 0, good = 0
  for (const line of readFileSync(p, 'utf8').split('\n')) {
    if (!line.trim()) continue
    let o; try { o = JSON.parse(line) } catch { continue }
    if (o.stage !== level || String(o.id) !== String(id)) continue
    // **口径（2026-09-24 修）：演习/无效行不进战绩。**
    //   `mode === 'drill'` 只烧演习券、不产收益 ⇒ 演习 3★ 不是胜绩；`valid === false` 没结论。
    //   胜绩 = 3★ 或 `stone: true`（源石到账是终局证据）。与 tools/plan_mines.py 同源。
    if (o.mode === 'drill' || o.valid === false) continue
    n += 1
    if ((o.stars || 0) >= 3 || o.stone === true) good += 1
  }
  return { n, p: (good + 1) / (n + 2), good }
}

/** ⚠️ 所有模型实现都塞这里；异常/超时由上层回退到 rule */
let _laya = null
async function loadLaya() {
  if (_laya) return _laya
  // ① 权重：本机直连 huggingface.co 不通（DNS 被污染、Node fetch 10s 超时）⇒ 走 hf-mirror
  //    预下载到本地目录（runtime/laya_spike/model，见 runtime/laya_spike/README 说明）。
  const modelDir = process.env.LAYA_MODEL || join(RT, 'laya_spike', 'model')
  // ② 依赖：装在 runtime/laya_spike，按路径动态 import，不污染项目根依赖
  const pkg = process.env.LAYA_PKG || join(RT, 'laya_spike', 'node_modules', '@receptron', 'laya', 'dist', 'index.js')
  const { pathToFileURL } = await import('node:url')
  const { Laya } = await import(pathToFileURL(pkg).href)
  _laya = await Laya.load({ modelDir, executionProviders: ['cpu'] })
  return _laya
}

async function rankWith(kind, cands, box, level) {
  if (kind === 'laya') {
    const laya = await loadLaya()
    const r = await laya.systemOne(formatState(cands, box, level), {
      最优作业: {
        type: 'choice',
        instructions: `关卡 ${level}：哪份作业对这个账号通过率最高？`,
        criteria: Object.fromEntries(cands.map((c) => [String(c.id), formatCriteria(c)])),
      },
      缺核心: { type: 'noul', instructions: '这份作业是否缺了成功组里几乎都有的核心干员？' },
    })
    const probs = r.answers?.最优作业?.probabilities || {}
    return cands.map((c) => ({
      id: c.id,
      score: Number((probs[String(c.id)] ?? 0).toFixed(4)),
      note: 'laya',
      noul: r.answers?.缺核心?.noul ?? null,
    }))
  }
  if (kind === 'gbm') {
    // 预留：tools/train_ranker.py 导出的系数 JSON（纯查表，不需要 ONNX）
    const coef = readJson(join(RT, 'ranker_coef.json'))
    if (!coef) throw new Error('ranker_coef.json 不存在（先跑 tools/train_ranker.py）')
    return cands.map((c) => ({ id: c.id, score: coef.bias + (coef.w_miss ?? 0) * ((c.used || []).length), note: 'gbm' }))
  }
  return cands.map((c) => scoreRule(c, box))
}

/** 统一入口：规则硬门槛 → 候选 ≤20 → 模型精排（失败自动回退） */
export async function rankCandidates(cands, box, level) {
  const filtered = cands.filter((c) => scoreRule(c, box).score > -9).slice(0, 20)
  if (!filtered.length) return []
  let rows
  try {
    rows = await Promise.race([
      rankWith(RANKER, filtered, box, level),
      new Promise((_, rej) => setTimeout(() => rej(new Error('ranker timeout')), TIMEOUT_MS)),
    ])
  } catch (e) {
    rows = filtered.map((c) => ({ ...scoreRule(c, box), note: `rule(回退:${e.message})` }))
  }
  // 叠加本号战绩：跑过且拿到三星的提到最前，跑过没三星的压后
  return rows.map((r, i) => {
    const h = historyScore(level, r.id)
    const bonus = h.n === 0 ? 0 : ((h.good || 0) > 0 ? 0.5 : -0.5)
    return { ...r, score: Number((r.score + bonus).toFixed(4)), hist: h, order: i }
  }).sort((a, b) => b.score - a.score || a.order - b.order)
}

// ── CLI 自测 ────────────────────────────────────────────────────────────
if (process.argv[1] && process.argv[1].endsWith('rank_service.mjs')) {
  const stage = process.argv[process.argv.indexOf('--stage') + 1]
  const asJson = process.argv.includes('--json')
  // 候选来源：screening 缓存（横扫自己落的）+ 所有 plan_*.json（作业站筛选结果）
  const { readdirSync } = await import('node:fs')
  let cands = []
  const cache = readJson(join(ROOT, 'data', 'copilot', 'screening', `${stage}.json`))
  if (cache?.[stage]) cands = cache[stage]
  if (!cands.length) {
    for (const f of readdirSync(join(RT, 'reports')).filter((n) => n.startsWith('plan_') && n.endsWith('.json'))) {
      const d = readJson(join(RT, 'reports', f), {})
      if (d?.[stage]?.length) { cands = d[stage]; break }
    }
  }
  if (!cands.length) { console.log(`没找到 ${stage} 的候选（用 --stage <stage_name>；来源=screening 缓存或 runtime/reports/plan_*.json）`); process.exit(1) }
  const box = loadAccountBox()
  const ranked = await rankCandidates(cands, box, stage)
  if (asJson) console.log(JSON.stringify(ranked, null, 1))
  else {
    console.log(`RANKER=${RANKER}｜${stage}｜账号 ${Object.keys(box.opers).length} 人（专精模组 ${box.mastery_known ? '已采集' : '未采集'}）`)
    for (const r of ranked.slice(0, 6)) console.log(`  prts://${r.id}  score=${r.score}  ${r.note}  历史 n=${r.hist.n}`)
  }
}
