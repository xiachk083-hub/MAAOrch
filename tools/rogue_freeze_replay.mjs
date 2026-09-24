#!/usr/bin/env node
/**
 * rogue_freeze_replay.mjs —— 用**真实日志里的现场图 md5 序列**重放"画面冻结阶梯"
 * ──────────────────────────────────────────────────────────────────────────────
 * 为什么要有它：2026-09-24 10:31–11:26，l-2 停在「探索終了」结算页上被连拍 **10 张现场图、
 * md5 完全相同**（一像素没变、55 分钟），而每一轮轻动作之后 2 分钟都被复核成「✅ 有效」——
 * 10 次救援全是**假有效**。冻结判据落地后（docs/ROGUE_WATCH.md §5.20），这份重放要回答一个问题：
 * **"如果新阶梯当时就在跑，它第几轮会动手、动哪一级？"**
 * 它 import 的是看守真正跑的那份决策内核 `lib/rogue_plan.mjs`，不是复刻品。
 *
 * 用法：
 *   node tools/rogue_freeze_replay.mjs --machine l-2            # 只看这一台
 *   node tools/rogue_freeze_replay.mjs                          # 日志里所有拍过图的机器
 *   node tools/rogue_freeze_replay.mjs --log runtime/xxx.log
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { planMachine, actionLabel } from './lib/rogue_plan.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const argv = process.argv.slice(2);
const flag = (k, d) => { const i = argv.indexOf(k); return i < 0 ? d : (argv[i + 1] && !argv[i + 1].startsWith('--') ? argv[i + 1] : true); };
const LOG = String(flag('--log', path.join(ROOT, 'runtime', 'rogue_fleet_watch.log')));
const ONLY = flag('--machine', null);
const SHOT = /^\[(\d\d:\d\d:\d\d)\]\s+现场图: (\{.*\})\s*$/;
const ACT = /^\[\d\d:\d\d:\d\d\]\s+([\w-]+): (\S+) → /;   // 日志行形如 `[10:42:16]   l-2: entry-error → 收掉本机队 MAA 再发车（…）`

const lines = fs.readFileSync(LOG, 'utf8').split(/\r?\n/);
const events = {};          // machine -> [{t, ms, kind, md5}]
let kind = null;
let day = 23;               // 日志跨零点：时间"倒退"就进一天（相对差才是重点）
let prevTod = -1;
for (const l of lines) {
  const a = ACT.exec(l);
  if (a) { kind = a[2]; continue; }
  const s = SHOT.exec(l);
  if (!s) continue;
  let r; try { r = JSON.parse(s[2]); } catch { continue; }
  const m = /_([\w-]+)\.png$/.exec(r.file || '');
  if (!m || !r.md5) continue;
  const [hh, mm, ss] = s[1].split(':').map(Number);
  const tod = hh * 3600 + mm * 60 + ss;
  if (tod < prevTod) day++;
  prevTod = tod;
  const ms = Date.parse(`2026-09-${String(day).padStart(2, '0')}T${s[1]}+08:00`);
  const name = m[1];
  if (ONLY && name !== ONLY) continue;
  (events[name] = events[name] || []).push({ t: s[1], ms, kind: kind || 'unknown', md5: r.md5 });
}

const base = { hist: [], lastActionAt: null, humanUntil: null, startFails: 0 };
const decide = (kind, frozen, nowMs, lastShotMs) => planMachine(
  { kind, why: kind },
  { ...base, frozenShots: frozen, lastShot: Number.isFinite(lastShotMs) ? { at: new Date(lastShotMs).toISOString() } : null },
  { force: false, nowMs, startGapMin: 8, noProgressMin: 20, gateOpen: true, frozenFreshMin: 20 },
);

let frozenTotal = 0;
for (const [name, ev] of Object.entries(events)) {
  const frozen = ev.filter((e, i) => i > 0 && e.md5 === ev[i - 1].md5).length;
  frozenTotal += frozen;
  console.log(`\n## ${name}：拍了 ${ev.length} 张现场图，其中 **${frozen} 张与上一张 md5 相同**`);
  console.log('| 时间 | 当时形态 | md5(前8) | 与上次同图 | 决策时的冻结计数 | 旧阶梯（无冻结判据） | 新阶梯 |');
  console.log('|---|---|---|---|---|---|---|');
  let streak = 0;
  for (let i = 0; i < ev.length; i++) {
    const e = ev[i];
    const same = i > 0 && e.md5 === ev[i - 1].md5;
    const t = e.ms;
    const prevShotMs = i > 0 ? ev[i - 1].ms : NaN;
    const oldP = decide(e.kind, 0, t, prevShotMs);
    const newP = decide(e.kind, streak, t, prevShotMs);   // 决策发生在拍图**之前** ⇒ 用上一轮的冻结计数与时间
    const star = same || newP.action !== oldP.action ? ' ⬅️' : '';
    console.log(`| ${e.t} | ${e.kind} | ${e.md5.slice(0, 8)} | ${same ? '**是**' : '否'} | ${streak} | `
      + `${oldP.action}${newP.action !== oldP.action ? '' : '（同）'} | ${newP.action === oldP.action ? '（同）' : `**${actionLabel(newP.action)}**`}${star} |`);
    streak = same ? streak + 1 : 0;
  }
}
if (!Object.keys(events).length) console.log('（日志里没有现场图记录 —— 这个日志还没跑到过动手）');
else console.log(`\n合计：${frozenTotal} 张"与上一张相同"的现场图（旧阶梯全按各自形态处理，新阶梯会逐级加重）。`);
