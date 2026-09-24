#!/usr/bin/env node
/**
 * rogue_report.mjs —— 肉鸽"盯号"日报：把逐轮采样变成"谁在跑、谁挂了、怎么救的"
 * ──────────────────────────────────────────────────────────────────────────────
 * 数据源全是看守自己留下的：`runtime/rogue_fleet_probe.jsonl`（每轮 5 台 + 本机 #0 的现场）
 * ＋ `runtime/rogue_fleet_state.json`（当日动作台账）＋ `runtime/rogue0_watch.log`。
 * 形态判读直接复用 `lib/rogue_judge.mjs` 的 classify()（与看守同一份判据，不会各说各话）。
 *
 * 用法：
 *   node tools/rogue_report.mjs                 # 看最近 6 小时
 *   node tools/rogue_report.mjs --hours 12
 *   node tools/rogue_report.mjs --md            # 同时写 runtime/reports/rogue_watch_<日期>.md
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { classify } from './lib/rogue_judge.mjs';
import { gate } from './lib/rogue_plan.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PROBE = path.join(ROOT, 'runtime', 'rogue_fleet_probe.jsonl');
const STATE = path.join(ROOT, 'runtime', 'rogue_fleet_state.json');
const CASES = path.join(ROOT, 'runtime', 'fleet_cases');
const CASES0 = path.join(ROOT, 'runtime', 'rogue0_cases');

const argv = process.argv.slice(2);
const flag = (k, d) => { const i = argv.indexOf(k); if (i < 0) return d; const v = argv[i + 1]; return v && !v.startsWith('--') ? v : true; };
const HOURS = Number(flag('--hours', 6));
const WANT_MD = argv.includes('--md');
const MACHINES = ['l-1', 'l-2', 'l-4', 'l-5', 'l-7'];
const GOOD = new Set(['running-ok', 'running-after-error']);
const ACT = {
  start: '发车', 'restart-maa': '重拉 MAA', 'killgame-restart': '关游戏+重拉',
  'needs-human': '交人工', none: '不动手',
};

const readJsonl = (p) => {
  try {
    return fs.readFileSync(p, 'utf8').split(/\r?\n/).filter(Boolean).map((l) => { try { return JSON.parse(l); } catch { return null; } }).filter(Boolean);
  } catch { return []; }
};
const hhmm = (iso) => { const d = new Date(iso); return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`; };
const pct = (n, d) => (d ? Math.round((n / d) * 100) : 0);

const all = readJsonl(PROBE);
const cut = Date.now() - HOURS * 3600 * 1000;
const samples = all.filter((s) => new Date(s.ts).getTime() >= cut);

const lines = [];
const W = (s = '') => { lines.push(s); };

W(`# 肉鸽"盯号"日报 · ${new Date().toLocaleString('zh-CN')}`);
W('');
W(`采样窗口：最近 ${HOURS} 小时，共 **${samples.length}** 轮（每轮覆盖 #0 + l-1/l-2/l-4/l-5/l-7）`);
W(samples.length ? `最近一轮：${new Date(samples[samples.length - 1].ts).toLocaleString('zh-CN')}（产线 active=${samples[samples.length - 1].prod ? samples[samples.length - 1].prod.active : '?'}/${samples[samples.length - 1].prod ? samples[samples.length - 1].prod.slots : '?'}，主机内存剩 ${samples[samples.length - 1].host ? samples[samples.length - 1].host.ramFreeGB : '?'}G，CPU ${samples[samples.length - 1].host ? samples[samples.length - 1].host.cpu : '?'}%）` : '');
{
  const lastS = samples[samples.length - 1];
  if (lastS) {
    const nowD = new Date();
    const g = gate({
      prodRunning: !!(lastS.prod && lastS.prod.active > 0),
      prodIdleMin: 999,                              // 日报只反映闸门规则本身，不做"连续空闲"累计
      hourFloat: nowD.getHours() + nowD.getMinutes() / 60,
      ramFree: lastS.host ? lastS.host.ramFreeGB : -1,
      force: false,
    });
    W(`**闸门**：${g.open ? '开' : '关'} —— ${g.why}${g.open ? '' : '（机队停着＝在等闸开，不是故障）'}`);
  }
}
W('');

// ---- 每台 ---- 
const keyOf = (s, name) => (name === '#0' ? s.local0 : (s.machines || {})[name]);
const summary = [];
for (const name of ['#0', ...MACHINES]) {
  const seq = samples.map((s) => keyOf(s, name)).filter(Boolean);
  const kinds = seq.map((r) => classify(r).kind);
  const hist = {};
  kinds.forEach((k) => { hist[k] = (hist[k] || 0) + 1; });
  const alive = seq.filter((r) => r.proc > 0).length;
  const gameOk = seq.filter((r) => r.game === true).length;
  const gameKnown = seq.filter((r) => r.game !== undefined && r.game !== null).length;
  const problems = kinds.filter((k) => !GOOD.has(k) && k !== 'unknown' && k !== 'parked');
  // 事故＝形态发生切换（记第一帧）
  const incidents = [];
  for (let i = 0; i < kinds.length; i++) {
    if (i === 0 || kinds[i] !== kinds[i - 1]) {
      if (!GOOD.has(kinds[i]) && kinds[i] !== 'unknown') incidents.push({ at: samples[i].ts, kind: kinds[i], i });
    }
  }
  const actions = (() => {
    try { const st = JSON.parse(fs.readFileSync(STATE, 'utf8')); return ((st.machines || {})[name] || {}).hist || []; } catch { return []; }
  })().filter((h) => new Date(h.ts).getTime() >= cut);
  const last = seq[seq.length - 1];
  summary.push({ name, rounds: seq.length, alive, gameOk, gameKnown, hist, problems: problems.length, incidents, actions, last, kinds });
}

W('## 一、谁在跑');
W('');
for (const s of summary) {
  const cur = s.kinds[s.kinds.length - 1] || 'n/a';
  W(`- **${s.name}**：${s.rounds} 轮样本｜MAA 在 ${pct(s.alive, s.rounds)}% 轮`
    + `｜游戏进程存活 ${s.gameKnown ? pct(s.gameOk, s.gameKnown) + '%' : '未采到'}`
    + `｜当前形态 \`${cur}\`｜异常帧 ${s.problems}｜动作 ${s.actions.length} 次`);
}
W('');

W('## 二、形态分布（判据与看守同一份 classify）');
W('');
for (const s of summary) {
  const top = Object.entries(s.hist).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k}×${v}`).join('，');
  W(`- ${s.name}：${top || '（无样本）'}`);
}
W('');

W('## 三、事故与处置（时间正序）');
W('');
let any = false;
const events = [];
for (const s of summary) {
  for (const inc of s.incidents) {
    if (inc.kind === 'parked' || inc.kind === 'idle-clean') continue;   // 停着=没在刷，单列
    events.push({ ts: inc.at, name: s.name, kind: inc.kind });
  }
  for (const a of s.actions) events.push({ ts: a.ts, name: s.name, act: a.act, kind: a.kind });
}
events.sort((a, b) => new Date(a.ts) - new Date(b.ts));
for (const e of events) {
  any = true;
  W(`- \`${hhmm(e.ts)}\` **${e.name}**：${e.act ? `动作「${ACT[e.act] || e.act}」` : `形态 \`${e.kind}\``}`);
}
if (!any) W('- （窗口内没有异常切换，也没有动作）');
W('');

W('## 四、现场证据');
W('');
const list = (p) => { try { return fs.readdirSync(p).filter((f) => f.endsWith('.png')).sort(); } catch { return []; } };
const c0 = list(CASES0).filter((f) => new Date(fs.statSync(path.join(CASES0, f)).mtimeMs).getTime() >= cut);
const c1 = list(CASES).filter((f) => new Date(fs.statSync(path.join(CASES, f)).mtimeMs).getTime() >= cut);
W(`- 本机 #0：${c0.length} 张（\`runtime/rogue0_cases/\`）${c0.length ? '：' + c0.slice(-4).join('、') : ''}`);
W(`- 机队：${c1.length} 张（\`runtime/fleet_cases/\`）${c1.length ? '：' + c1.slice(-4).join('、') : ''}`);
W('');
W('> 说明：形态判据/复活动作见 `tools/lib/rogue_judge.mjs` 与 `tools/lib/rogue_plan.mjs`；');
W('> 两份都有自测（`node tools/rogue_judge_selftest.mjs` / `node tools/rogue_plan_selftest.mjs`）。');

const text = lines.join('\n');
console.log(text);
if (WANT_MD) {
  const dir = path.join(ROOT, 'runtime', 'reports');
  fs.mkdirSync(dir, { recursive: true });
  const d = new Date();
  const p = path.join(dir, `rogue_watch_${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}.md`);
  fs.appendFileSync(p, text + '\n\n');
  console.log(`\n（已追加到 ${p}）`);
}
