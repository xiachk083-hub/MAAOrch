#!/usr/bin/env node
/**
 * rogue_watch.mjs —— 肉鸽机队看守（fz-maa l-1/l-2/l-4/l-5/l-7）
 *
 * MAA 的肉鸽任务链一旦 `TaskChainError`（掉线 / 界面残留 / 弹层）就**停在原地不动**：
 * 进程还在、日志不再写。MAA 自己没有失败重试，AUTO-MAS 的 RunTimesLimit 也不是重试 ⇒
 * 只能在外层补拉，而且按"便宜的先试、贵的自动上"分四级升级。
 *
 * 判据（任一成立即"停住"）：
 *   ① 该台 MAA.exe 进程不存在
 *   ② 该台 debug/asst.log 静默超过 --silent-sec（默认 180 秒；MAA 正常时每秒都在写）
 *
 * 升级阶梯（--quick-min 默认 15 分钟：上次动手后这么快又停 ⇒ 说明上一级不管用）：
 *   L1 杀残留 MAA → 登录态发车任务重拉
 *   L2 L1 后仍快速复停 ⇒ `adb shell am force-stop com.hypergryph.arknights` 再重拉（治「联系中断」掉线）
 *   L3 L2 后仍快速复停 ⇒ `MuMuManager control --vmindex N shutdown` + 登录态重新 launch + 重拉 MAA
 *   L4 L3 用满（--max-emu 默认 2）⇒ 当日封顶 + 台账记 done-for-day（不再空烧机器）
 *   · 无错误地停住（正常跑完 / 满级停）⇒ 直接当日封顶
 *
 * 让路闸：`E:\MAA-Multi\MAA_*` 的产线日常 MAA 在跑就不动手（--force 绕过）
 * 其它闸：每台当天总动作 ≤ --max（默认 8）｜ 一轮只处理一台 ｜ 取不到进程列表 fail-closed
 *        ｜ **默认 dry-run**，必须 --apply
 *
 * 用法：
 *   node rogue_watch.mjs                     # dry-run
 *   node rogue_watch.mjs --apply             # 真补（计划任务用这个）
 *   node rogue_watch.mjs --status            # 只看状态
 *   node rogue_watch.mjs --restart-emu l-4   # 手动演练 L3（重启指定那台模拟器）
 */
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

// ---------- 配置 ----------
const ROOT = process.env.ROGUE_ROOT || 'E:\\maa-alas\\fz-maa';
const MGR = 'E:\\MuMu Player 12\\nx_main\\MuMuManager.exe';
const ADB = 'E:\\MuMu Player 12\\shell\\adb.exe';
const PKG = 'com.hypergryph.arknights';
const EMUS = {
  'l-1': { idx: 25, adb: '127.0.0.1:17184' },
  'l-2': { idx: 28, adb: '127.0.0.1:17280' },
  'l-4': { idx: 31, adb: '127.0.0.1:17376' },
  'l-5': { idx: 34, adb: '127.0.0.1:16452' },
  'l-7': { idx: 52, adb: '127.0.0.1:17028' },
};
const NAMES = Object.keys(EMUS);
const TASK = 'fz-maa-l-autostart';                    // 登录态发车任务（幂等）
const DAILY_PREFIX = 'e:\\maa-multi\\maa_';           // 产线日常的 MAA 目录前缀（小写）
const STATE_FILE = path.join(ROOT, 'rogue_watch_state.json');
const LEDGER = path.join(ROOT, 'rogue_watch.log');
const SCAN_BYTES = 256 * 1024;

const MACHINES = NAMES.map((n) => ({
  name: n,
  idx: EMUS[n].idx,
  adb: EMUS[n].adb,
  exe: path.join(ROOT, 'l', n, 'MAA.exe'),
  log: path.join(ROOT, 'l', n, 'debug', 'asst.log'),
}));

// ---------- 参数 ----------
const argv = process.argv.slice(2);
const flag = (k, d) => {
  const i = argv.indexOf(k);
  if (i < 0) return d;
  const v = argv[i + 1];
  return v && !v.startsWith('--') ? v : true;
};
const APPLY = argv.includes('--apply');
const FORCE = argv.includes('--force');
const STATUS_ONLY = argv.includes('--status');
const SILENT_SEC = Number(flag('--silent-sec', 180));
const MAX_ACTIONS = Number(flag('--max', 8));
const MAX_EMU = Number(flag('--max-emu', 2));
const QUICK_MIN = Number(flag('--quick-min', 15));
/** 冷启动（五台都没进程）时的发车窗口：窗口外不发车 —— 否则 04:00 计划重启后看守会立刻拉起肉鸽、正好撞 05:00 早班 */
const BOOTSTRAP = [10 * 60, 18 * 60];

// ---------- 工具 ----------
const now = () => new Date();
/** 本机日期（不用 toISOString：那是 UTC，会让"今天"在北京 08:00 才翻页、封顶计数看着莫名其妙） */
const today = () => {
  const d = now();
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
};
const sleepSync = (ms) => Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);

function ps(cmd) {
  try {
    // 末尾显式 `exit 0`：`Get-Process MAA` 在"一个 MAA 都没有"时会写非终止错误、让 powershell 退出码变 1，
    // 不补这一句就会把"五台全停"误判成"取不到进程列表"而 fail-closed（发不出车）。
    return execFileSync('powershell', ['-NoProfile', '-NonInteractive', '-Command', `${cmd}; exit 0`],
      { encoding: 'utf8', timeout: 300000 }).trim();
  } catch (e) {
    const out = String(e.stdout || '').trim();
    if (out) return out;                       // 非零退出但有输出：照用
    return `__ERR__ ${String(e.stderr || e.message).slice(0, 200)}`;
  }
}
function sh(cmd) {
  try {
    return execFileSync(cmd, { shell: true, encoding: 'utf8', timeout: 120000 }).trim();
  } catch (e) {
    return `__ERR__ ${String(e.stderr || e.message).slice(0, 200)}`;
  }
}

function maaPaths() {
  const out = ps('Get-Process MAA -ErrorAction SilentlyContinue | ForEach-Object { $_.Path }');
  if (out.startsWith('__ERR__')) return null;
  return out ? out.split(/\r?\n/).map((s) => s.trim().toLowerCase()).filter(Boolean) : [];
}
function logTail(file) {
  try {
    const size = fs.statSync(file).size;
    const fd = fs.openSync(file, 'r');
    const start = Math.max(0, size - SCAN_BYTES);
    const buf = Buffer.alloc(size - start);
    fs.readSync(fd, buf, 0, buf.length, start);
    fs.closeSync(fd);
    return buf.toString('utf8');
  } catch { return ''; }
}
const tailHasRoguelikeError = (f) => /TaskChainError \{"taskchain":"Roguelike"/.test(logTail(f));

function loadState() {
  try {
    const s = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'));
    return s.date === today() ? s : { date: today(), machines: {} };
  } catch { return { date: today(), machines: {} }; }
}
const saveState = (s) => fs.writeFileSync(STATE_FILE, JSON.stringify(s, null, 2));
/** 取当日状态；旧状态文件缺新字段要就地补默认值（否则 `undefined < N` 恒 false，阶梯升不上去） */
const entry = (s, n) => {
  const e = (s.machines[n] ||= {});
  e.count ??= 0;
  e.lastRelaunchAt ??= null;
  e.doneForDay ??= false;
  e.triedKillGame ??= false;
  e.emuRestarts ??= 0;
  return e;
};
const ledger = (o) => fs.appendFileSync(LEDGER, JSON.stringify({ ts: now().toISOString(), ...o }) + '\n');

// ---------- 三级动作 ----------
function killMaa(m) {
  return ps(`Get-Process MAA -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq '${m.exe}' } | ForEach-Object { Stop-Process -Id $_.Id -Force }`);
}
/** 走登录态发车任务补拉（脚本幂等：在跑的会 "MAA already running" 跳过） */
function launchTask() {
  return ps([
    `Enable-ScheduledTask -TaskName '${TASK}' -ErrorAction Stop | Out-Null`,
    `Start-ScheduledTask -TaskName '${TASK}'`,
    `Start-Sleep -Seconds 50`,
    `Disable-ScheduledTask -TaskName '${TASK}' -ErrorAction SilentlyContinue | Out-Null`,
    `'ok'`,
  ].join('; '));
}
const L1 = (m) => ({ level: 'L1-relaunch', kill: killMaa(m), go: launchTask() });

/** L2：杀游戏进程（治「联系中断」掉线）——MAA 的 StartUp 会重新拉起客户端 */
function L2(m) {
  const stop = sh(`"${ADB}" -s ${m.adb} shell am force-stop ${PKG}`);
  sleepSync(4000);
  return { level: 'L2-killgame', stop, kill: killMaa(m), go: launchTask() };
}

/** L3：重启该台模拟器（下下策）——shutdown 后由登录态任务重新 launch */
function L3(m) {
  const down = sh(`"${MGR}" control --vmindex ${m.idx} shutdown`);
  sleepSync(10000);
  const kill = killMaa(m);
  const go = launchTask();
  return { level: 'L3-restart-emu', down, kill, go };
}

// ---------- 主流程 ----------
const state = loadState();
const paths = maaPaths();

// 手动演练：--restart-emu <l-N>
const testEmu = flag('--restart-emu', null);
if (testEmu && testEmu !== true) {
  const m = MACHINES.find((x) => x.name === testEmu);
  if (!m) { console.log(`未知机器 ${testEmu}`); process.exit(1); }
  console.log(`[演练] 重启 ${m.name} 的模拟器（idx ${m.idx}）…`);
  const r = L3(m);
  console.log('  shutdown:', r.down, '| 发车:', r.go);
  process.exit(0);
}

console.log(`[rogue_watch] ${now().toISOString()} apply=${APPLY} silent>${SILENT_SEC}s`);
if (paths === null) { console.log('  !! 取不到 MAA 进程列表（fail-closed）'); process.exit(0); }

const dailyRunning = paths.filter((p) => p.startsWith(DAILY_PREFIX));
console.log(`  产线日常 MAA 在跑: ${dailyRunning.length} 个${dailyRunning.length ? '（让路：本轮不动手）' : ''}`);

// “现在几点” —— 支持 --now HH:MM 便于演练（只影响冷启动发车窗口的判定）
const nowFlag = flag('--now', null);
const nowMin = (typeof nowFlag === 'string' && /^\d{1,2}:\d{2}$/.test(nowFlag))
  ? Number(nowFlag.split(':')[0]) * 60 + Number(nowFlag.split(':')[1])
  : now().getHours() * 60 + now().getMinutes();
const inBootstrap = nowMin >= BOOTSTRAP[0] && nowMin < BOOTSTRAP[1];

const rows = [];
for (const m of MACHINES) {
  const alive = paths.includes(m.exe.toLowerCase());
  let silent = -1;
  try { silent = (now() - fs.statSync(m.log).mtime) / 1000; } catch { /* 无日志 */ }
  const err = tailHasRoguelikeError(m.log);
  const stuck = !alive || silent > SILENT_SEC;
  const e = entry(state, m.name);
  rows.push({ m, alive, silent, stuck, err, e });
  console.log(`  ${m.name}: 进程=${alive ? '有' : '无'} 静默=${silent < 0 ? 'n/a' : Math.round(silent) + 's'}`
    + ` 停住=${stuck ? 'YES' : 'no'} 动作=${e.count} 重启模拟器=${e.emuRestarts}`
    + (e.doneForDay ? ' (今日封顶)' : '') + (err ? ' [尾部 TaskChainError]' : ''));
}

if (STATUS_ONLY) process.exit(0);

// 肉鸽窗口闸：只在 10:00–18:00 动手 —— 冷启动、单机补拉、升级阶梯一律适用。
// 教训（2026-09-22 早班前）：原先只对"五台全停"设窗口，只要有一台活着（比如我在测试），
// 另外四台就会被当成"单机故障"随时补拉 ⇒ 05:0x 差点和早班同时跑 ✗。
if (!inBootstrap && !FORCE) {
  const hh = String(Math.floor(nowMin / 60)).padStart(2, '0');
  const mm = String(nowMin % 60).padStart(2, '0');
  console.log(`  不在肉鸽窗口 ${String(Math.floor(BOOTSTRAP[0] / 60)).padStart(2, '0')}:00–${String(Math.floor(BOOTSTRAP[1] / 60)).padStart(2, '0')}:00（现 ${hh}:${mm}）⇒ 本轮不动手（早班/晚班时段一律不碰肉鸽）`);
  process.exit(0);
}

if (!APPLY) {
  const t = rows.find((r) => r.stuck && !r.e.doneForDay && r.e.count < MAX_ACTIONS);
  console.log(t ? `  [dry-run] 会处理 ${t.m.name}` : '  [dry-run] 无需动作');
  process.exit(0);
}
if (dailyRunning.length && !FORCE) { console.log('  产线日常在跑 ⇒ 让路，本轮不动手。'); process.exit(0); }

for (const r of rows) {
  const { m, stuck, e } = r;
  if (!stuck) continue;
  if (e.doneForDay) { console.log(`  ${m.name}: 今日封顶，跳过`); continue; }
  if (e.count >= MAX_ACTIONS) {
    e.doneForDay = true; saveState(state);
    ledger({ machine: m.name, action: 'done-for-day', reason: `达总动作上限 ${MAX_ACTIONS}` });
    console.log(`  ${m.name}: 达总动作上限，今日封顶`); continue;
  }
  // 无错误地停住 ⇒ 正常跑完 / 满级停
  if (r.alive && !r.err) {
    e.doneForDay = true; saveState(state);
    ledger({ machine: m.name, action: 'done-for-day', reason: '无错误地停住（正常跑完/满级停）' });
    console.log(`  ${m.name}: 无错误地停住 ⇒ 判定已跑完/满级，当日封顶`); continue;
  }

  const sinceMin = e.lastRelaunchAt ? (now() - new Date(e.lastRelaunchAt)) / 60000 : Infinity;
  const quick = sinceMin < QUICK_MIN;                 // 上次动手后很快又停 ⇒ 升级
  let res;
  if (r.alive && quick && !e.triedKillGame && r.err) {
    e.triedKillGame = true; res = L2(m);              // L2
  } else if (r.alive && quick && e.triedKillGame && e.emuRestarts < MAX_EMU) {
    e.emuRestarts += 1; res = L3(m);                  // L3
  } else if (r.alive && quick) {
    e.doneForDay = true; saveState(state);
    ledger({ machine: m.name, action: 'done-for-day', reason: `L2/L3 都救不回来（重启模拟器 ${e.emuRestarts} 次）` });
    console.log(`  ${m.name}: L2/L3 都救不回来 ⇒ 今日封顶（留痕）`); continue;
  } else {
    e.triedKillGame = false; res = L1(m);             // L1（久违的一次停：从便宜的重来）
  }

  e.count += 1; e.lastRelaunchAt = now().toISOString();
  saveState(state);
  ledger({ machine: m.name, action: res.level, count: e.count, silentSec: Math.round(r.silent),
    alive: r.alive, tailError: r.err, emuRestarts: e.emuRestarts,
    detail: String(res.down || res.stop || res.kill || '').slice(0, 80) });
  console.log(`  → ${m.name} 执行 ${res.level}（今日第 ${e.count} 次动作）`);
  saveState(state);
  break;                                              // 一轮只处理一台
}
