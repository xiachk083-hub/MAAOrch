#!/usr/bin/env node
/**
 * rogue0_history.mjs —— 从 #0 自己的 MAA 日志里挖"它历史上都是怎么卡的"
 * ──────────────────────────────────────────────────────────────────────────────
 * 为什么：机队的历史是从 `runtime/fleet_forensics_20260923.txt` 里挖的，但 **#0 是本机用户手开的那台**，
 * 它的病例只有它自己的 `debug/gui.log`(+`gui.bak.log`) 里才有。用户口径是"亲自盯" ⇒ 先把自己这台
 * 的历史病例数清楚，再谈覆盖。
 *
 * 用法：node tools/rogue0_history.mjs [--days 7] [--errors]
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const DEBUG = 'D:\\Xiach\\M-A\\MAA\\MAA-v6.12.2-win-x64\\debug';
const FILES = ['gui.bak.log', 'gui.log'];
const argv = process.argv.slice(2);
const flag = (k, d) => { const i = argv.indexOf(k); if (i < 0) return d; const v = argv[i + 1]; return v && !v.startsWith('--') ? v : true; };
const DAYS = Number(flag('--days', 7));
const ONLY_ERRORS = argv.includes('--errors');
const cut = Date.now() - DAYS * 86400 * 1000;

const lines = [];
for (const f of FILES) {
  const p = path.join(DEBUG, f);
  if (!fs.existsSync(p)) continue;
  for (const l of fs.readFileSync(p, 'utf8').split(/\r?\n/)) if (l) lines.push(l);
}

const tsOf = (l) => { const m = l.match(/^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})/); return m ? Date.parse(m[1].replace(' ', 'T')) : 0; };
const events = [];
for (let i = 0; i < lines.length; i++) {
  const l = lines[i];
  const t = tsOf(l);
  if (!t || t < cut) continue;
  const stamp = l.slice(1, 20);
  if (/开始任务: /.test(l)) events.push({ t, stamp, kind: 'start', what: (l.match(/开始任务: (.+)$/) || [])[1] || '' });
  else if (/任务出错: /.test(l)) events.push({ t, stamp, kind: 'error', what: (l.match(/任务出错: (.+)$/) || [])[1] || '' });
  else if (/本轮任务出现以下错误：/.test(l)) {
    const names = [];
    for (let j = i + 1; j < lines.length; j++) {
      const s = lines[j].trim();
      if (!s || /^\[/.test(s)) break;
      names.push(s);
    }
    events.push({ t, stamp, kind: 'errblock', what: names.join('+') });
  } else if (/任务已全部完成！/.test(l)) events.push({ t, stamp, kind: 'done', what: '' });
  else if (/已停止/.test(l)) events.push({ t, stamp, kind: 'stop', what: '' });
  else if (/Bootstrapper\].*GUI started/.test(l)) events.push({ t, stamp, kind: 'boot', what: '' });
}
events.sort((a, b) => a.t - b.t);

const day = (t) => new Date(t).toISOString().slice(0, 10);
const byDay = {};
for (const e of events) { byDay[day(e.t)] = byDay[day(e.t)] || { start: 0, error: 0, done: 0, stop: 0, boot: 0 }; byDay[day(e.t)][e.kind] = (byDay[day(e.t)][e.kind] || 0) + 1; }

console.log(`# #0（本机日服 MAA）历史病例 · 最近 ${DAYS} 天`);
console.log('');
console.log('## 每天的量（开始/出错/全部完成/被停/重启 MAA）');
for (const d of Object.keys(byDay).sort()) {
  const v = byDay[d];
  console.log(`- ${d}：开始 ${v.start || 0}｜出错 ${v.error || 0}｜完成 ${v.done || 0}｜被停 ${v.stop || 0}｜重启 MAA ${v.boot || 0}`);
}
console.log('');
console.log('## 出错明细（时间 → 任务）');
const errs = events.filter((e) => e.kind === 'error');
const tally = {};
errs.forEach((e) => { tally[e.what] = (tally[e.what] || 0) + 1; });
console.log('按任务汇总：' + (Object.entries(tally).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k} ${v} 次`).join('，') || '（无）'));
console.log('');
if (ONLY_ERRORS) for (const e of errs) console.log(`- ${e.stamp} → ${e.what}`);
else for (const e of errs.slice(-25)) console.log(`- ${e.stamp} → ${e.what}`);
console.log('');
console.log('## "被停"的时刻（用户手动停 / 我停的）');
for (const e of events.filter((x) => x.kind === 'stop').slice(-15)) console.log(`- ${e.stamp}`);
console.log('');
console.log('## 多任务同时报错的场次（＝整条链崩，多半是模拟器/客户端级故障）');
for (const e of events.filter((x) => x.kind === 'errblock' && x.what.includes('+'))) console.log(`- ${e.stamp} → ${e.what}`);
