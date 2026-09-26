#!/usr/bin/env node
/**
 * laya_shadow —— **影子对比**：同一关，规则排序 vs Laya 排序，两条都算、都记账，
 * 但**不接管**真正的出场决策（event_sweep 仍按 axis_rank.json 的规则/战绩排序跑）。
 *
 * 为什么先影子跑（2026-09-24 实测）：Laya 零样本概率 ≈ 均匀（0.3595/0.3189/0.3216）、
 *   `noul`=0.5085 ≈ 掷硬币 ⇒ 直接接管只会掉成功率。按用户方案第 2 周的验收方式：
 *   **并行跑、记录 20 组对比数据**，用命中率说话。
 *
 * 用法:
 *   node tools/laya_shadow.mjs                      # 跑默认几关
 *   node tools/laya_shadow.mjs act39side_ex07 act38side_ex03
 * 产出: runtime/rank_shadow.jsonl（每关一行）+ 控制台对比表
 */
import { execFileSync } from 'node:child_process'
import { appendFileSync, readFileSync, mkdirSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const OUT = join(ROOT, 'runtime', 'rank_shadow.jsonl')

const DEFAULT_STAGES = ['act39side_ex07', 'act39side_ex04', 'act38side_ex03', 'act38side_ex08',
  'act42side_ex02', 'act42side_ex08', 'act18side_ex07', 'act39side_ex05']

function runRanker(stage, ranker) {
  const env = { ...process.env, RANKER: ranker, RANKER_TIMEOUT_MS: '60000', LAYA_MODEL: process.env.LAYA_MODEL || join(ROOT, 'runtime', 'laya_spike', 'model') }
  const out = execFileSync('node', ['tools/rank_service.mjs', '--stage', stage, '--json'], { cwd: ROOT, env, encoding: 'utf8', maxBuffer: 32 * 1024 * 1024 })
  return JSON.parse(out)
}

const stages = process.argv.slice(2).length ? process.argv.slice(2) : DEFAULT_STAGES
mkdirSync(join(ROOT, 'runtime'), { recursive: true })
console.log('stage | 规则 top1 | Laya top1 | 一致? | 规则top1 在 Laya 前3? | Laya 概率')
let agree = 0, inTop3 = 0
for (const stage of stages) {
  let rule, laya
  try { rule = runRanker(stage, 'rule') } catch (e) { console.log(`${stage} 规则失败：${e.message}`); continue }
  try { laya = runRanker(stage, 'laya') } catch (e) { console.log(`${stage} Laya 失败（会回退规则）：${e.message}`); continue }
  const rTop = rule[0]?.id, lTop = laya[0]?.id
  const same = String(rTop) === String(lTop)
  const in3 = laya.slice(0, 3).map((x) => String(x.id)).includes(String(rTop))
  agree += same ? 1 : 0
  inTop3 += in3 ? 1 : 0
  const probs = laya.slice(0, 3).map((x) => `${x.id}:${x.score}`).join(' ')
  console.log(`${stage} | ${rTop} | ${lTop} | ${same ? '是' : '否'} | ${in3 ? '是' : '否'} | ${probs}`)
  appendFileSync(OUT, JSON.stringify({
    ts: new Date().toISOString(), stage,
    rule: rule.map((x) => ({ id: x.id, score: x.score })),
    laya: laya.map((x) => ({ id: x.id, score: x.score })),
    agree_top1: same, rule_top1_in_laya_top3: in3,
  }) + '\n', 'utf8')
}
console.log(`\n共 ${stages.length} 关：top1 一致 ${agree}，规则 top1 落在 Laya 前 3 有 ${inTop3} —— 明细写 ${OUT}`)
console.log('注意：这份对比**不改任何出场决策**，只为将来"要不要让 Laya 接管"提供命中率证据。')
