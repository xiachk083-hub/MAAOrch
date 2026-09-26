#!/usr/bin/env node
/**
 * prime_plan —— 「首通源石」计划表（Node，2026-09-23 新写）
 *
 * 为什么需要它:
 *   目标＝在 #1（官服）上收集 100 个至纯源石。源石只来自**关卡首通**，
 *   而"哪些关还有首通源石"这件事全在 `data/_stage_table.json` 里（字段 `diamondOnceDrop`），
 *   所以先把"宇宙"算清楚：每个 zone 有多少源石、每关多少理智、游戏里显示什么代号。
 *
 * 关键字段（实测确认）:
 *   · `diamondOnceDrop` = 首通源石数（1 = 首通给 1 至纯源石）✓
 *   · `stageDropInfo.displayRewards` 里 `{"type":"DIAMOND","id":"4002","dropType":"COMPLETE"}` 是同一件事
 *   · `apCost` = 理智消耗（0 = 剧情关，纯白送源石）
 *   · `difficulty`: NORMAL=普通 / FOUR_STAR=突袭(#f#，第 0~9 章) / SIX_STAR=险地·磨难(#s，第 15 章起)
 *   · **代号错位**（第 15 章起）：`stageId=main_16-01` 在游戏里显示为 `16-2`（即 main_15-(N-1)），
 *     剧情关是 `st_15-01` 显示 `15-1`。给 MAA / 作业站传的是 **stageId**，不是显示代号。
 *
 * 用法:
 *   node tools/prime_plan.mjs                      # 主线全表 → data/prime_plan.json + 摘要
 *   node tools/prime_plan.mjs --zones act42side_zone1,act42side_zone2
 */
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const TABLE = join(ROOT, 'data', '_stage_table.json')
const OUT_JSON = join(ROOT, 'data', 'prime_plan.json')
const OUT_MD = join(ROOT, 'runtime', 'reports', 'prime_plan.md')

const argv = process.argv.slice(2)
const argOf = (n, d) => { const i = argv.indexOf('--' + n); return i >= 0 && argv[i + 1] ? argv[i + 1] : d }
const zoneFilter = (argOf('zones', '') || '').split(',').map((s) => s.trim()).filter(Boolean)

const table = JSON.parse(readFileSync(TABLE, 'utf8')).stages || {}
const DIFF_CN = { NORMAL: '普通', FOUR_STAR: '突袭', SIX_STAR: '险地' }
/** 第 0~14 章的主题曲 zone；第 15/16/17 章在主线的 act2/3/4mainss 里 */
const MAIN_ZONES = [
  ...Array.from({ length: 15 }, (_, i) => `main_${i}`),
  'act2mainss_zone1', 'act3mainss_zone1', 'act4mainss_zone1',
]
/** 显示代号 → 章节号（用于排序；st_15-01/main_15-01 都归 15） */
function chapterOf(zone) {
  const m = zone.match(/^main_(\d+)$/)
  if (m) return Number(m[1])
  const m2 = zone.match(/^act(\d)mainss_zone1$/)
  if (m2) return 13 + Number(m2[1])            // act2mainss→15, act3→16, act4→17
  return 99
}

const rows = []
for (const [stageId, v] of Object.entries(table)) {
  const dia = v.diamondOnceDrop || 0
  if (dia <= 0) continue
  const zone = v.zoneId || ''
  if (zoneFilter.length ? !zoneFilter.includes(zone) : !MAIN_ZONES.includes(zone)) continue
  rows.push({
    stageId,
    zone,
    chapter: chapterOf(zone),
    code: v.code || '',
    name: v.name || '',
    diff: v.difficulty || 'NORMAL',
    diffCn: DIFF_CN[v.difficulty] || v.difficulty,
    ap: v.apCost || 0,
    diamonds: dia,
    // 剧情关：**没有 levelId 就没有战斗地图**（`isStoryOnly` 在部分节点上没填，
    // 而 `apCost===0` 又会把 TR 教学关误伤 ⇒ 只认 levelId，2026-09-23 实测）
    story: !v.levelId,
  })
}

// 排序：章节升序 → 普通在前 → 理智升序 → 代号
const diffRank = (d) => (d === 'NORMAL' ? 0 : 1)
rows.sort((a, b) => a.chapter - b.chapter || diffRank(a.diff) - diffRank(b.diff)
  || a.ap - b.ap || a.code.localeCompare(b.code, undefined, { numeric: true }))

// 按 zone 汇总
const byZone = {}
for (const r of rows) {
  const z = (byZone[r.zone] ||= { zone: r.zone, chapter: r.chapter, stages: 0, diamonds: 0, ap: 0, byDiff: {} })
  z.stages += 1; z.diamonds += r.diamonds; z.ap += r.ap
  const d = (z.byDiff[r.diffCn] ||= { stages: 0, diamonds: 0, ap: 0 })
  d.stages += 1; d.diamonds += r.diamonds; d.ap += r.ap
}

const total = rows.reduce((a, r) => ({ stages: a.stages + 1, diamonds: a.diamonds + r.diamonds, ap: a.ap + r.ap }), { stages: 0, diamonds: 0, ap: 0 })

mkdirSync(dirname(OUT_MD), { recursive: true })
writeFileSync(OUT_JSON, JSON.stringify({ generated: new Date().toISOString(), total, byZone, rows }, null, 1), 'utf8')

const md = [
  '# 主线首通源石计划表', '',
  `- 生成：${new Date().toLocaleString('zh-CN', { hour12: false })}`,
  `- 范围：${zoneFilter.length ? zoneFilter.join(', ') : '主线全部（第 0~17 章 + 突袭/险地 + 绝境）'}`,
  `- **总计：${total.stages} 关 = ${total.diamonds} 源石（${total.ap} 理智，平均 ${(total.ap / Math.max(1, total.diamonds)).toFixed(1)} 理智/源石）**`, '',
  '| 章节 | zone | 普通 | 突袭/险地 | 关卡数 | 源石 | 理智 |', '|---|---|---|---|---|---|---|',
  ...Object.values(byZone).sort((a, b) => a.chapter - b.chapter).map((z) => {
    const n = z.byDiff['普通'] || { stages: 0, diamonds: 0 }
    const h = (z.byDiff['突袭'] || z.byDiff['险地'] || { stages: 0, diamonds: 0 })
    const hn = [z.byDiff['突袭'] ? '突袭' : null, z.byDiff['险地'] ? '险地' : null].filter(Boolean).join('/')
    return `| ${z.chapter} | ${z.zone} | ${n.stages} (${n.diamonds}石) | ${h.stages} (${h.diamonds}石) ${hn} | ${z.stages} | ${z.diamonds} | ${z.ap} |`
  }), '',
  '## 关卡明细（按计划顺序）', '', '| # | 显示代号 | stage_name | 难度 | 理智 | 源石 | 关卡名 |', '|---|---|---|---|---|---|---|',
  ...rows.map((r, i) => `| ${i + 1} | ${r.code} | ${r.stageId} | ${r.diffCn} | ${r.ap} | ${r.diamonds} | ${r.name} |`),
]
writeFileSync(OUT_MD, md.join('\n') + '\n', 'utf8')
console.log(`计划表：${total.stages} 关 / ${total.diamonds} 源石 / ${total.ap} 理智`)
console.log(`  → ${OUT_JSON}`)
console.log(`  → ${OUT_MD}`)
for (const z of Object.values(byZone).sort((a, b) => a.chapter - b.chapter)) {
  console.log(`  第${z.chapter}章 ${z.zone}: ${z.stages} 关 = ${z.diamonds} 源石 (${z.ap} 理智)`)
}
