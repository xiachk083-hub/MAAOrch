#!/usr/bin/env node
/**
 * rogue_fleet_watch.mjs —— 肉鸽机队看守（本机跑；l-1/l-2/l-4/l-5/l-7 @ MRFZ-0000）
 * ──────────────────────────────────────────────────────────────────────────────
 * 目标（用户 2026-09-23 口径）：「肉鸽卡住的问题是多种多样的…**你盯五六个号**之后，我才认可
 * 你做出来的这个看门狗」+「晚班没有那么长的时间，完了就自动刷肉鸽」。
 * ⇒ 本看守做两件事：① 每轮把 5 台+本机 #0 的状态**连内容**采下来（JSONL 留证）；
 *   ② 只在"产线空闲 + 时间窗内"动手，按**取证得到的形态**选复活动作，不搞"每版只动一点"。
 *
 * 形态判据（2026-09-23 取证，见 runtime/fleet_forensics_20260923.txt）：
 *   形态1 放弃确认弹层粘住 → `*@StartExplore` cur_retry 达 retry_times、pre_task=*AbandonConfirm
 *         ⇒ 关游戏客户端 + 重拉 MAA；**同形态 2 次失败就交人工**（取证证明 L1/L2/L3 都无效，别烧阶梯）
 *   形态2 假完成 → asst 有 `ExceededLimit`/`TaskChainCompleted`、gui 无「任务出错」但链已停
 *         ⇒ 关游戏 + 重拉；**绝不能判 done-for-day**（旧看守把它当"正常跑完"，最贵的一条）
 *   形态3 自定任务/MiniGame 自环卡死（`MiniGame@...`、`ProcessTask::run | leave` 几小时）
 *         ⇒ 重拉 MAA（不关游戏）
 *   形态4 入口可识别集合只剩 1 条 / pre_task 空 ⇒ 重拉 MAA
 *   形态5 开始唤醒 出错但队列照走 ⇒ 良性，不动手
 *   形态6 gui.log 被 19999 回调报错刷屏 ⇒ 修配置，不动机器
 *   形态7 战斗失败 ⇒ 良性（肉鸽会自己续）
 *   零进展（在跑但同一 cur_task 卡 >20min 且重试耗尽/只剩 screencap）⇒ 重拉 MAA
 *
 * 让路闸：**产线在跑（E:\MAA-Multi 有 asst.log 90s 内推进）一律不动手**（肉鸽×产线不许并行）。
 * 窗口：10:00–18:00 或 22:00–03:30；启动动作每轮最多 2 台、每台之间 ≥8 分钟。
 *
 * 用法：
 *   node tools/rogue_fleet_watch.mjs                 # dry-run（只看 + 采样 + 记账）
 *   node tools/rogue_fleet_watch.mjs --apply         # 真守（计划任务 MAAOrch-RogueFleet 每 2 分钟）
 *   node tools/rogue_fleet_watch.mjs --apply --force # 无视窗口/让路闸（演练用）
 *   node tools/rogue_fleet_watch.mjs --status        # 只打一行状态
 */
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { classify } from './lib/rogue_judge.mjs';
import { gate, inWindow as inWindowOf, startPerRound as startPerRoundOf, planMachine, actionLabel, wrapUp, updateScreen } from './lib/rogue_plan.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const HOST = 'Administrator@100.79.173.69';
const KEY = path.join(process.env.USERPROFILE || '', '.ssh', 'maaorch_target');
const PROBE = path.join(ROOT, 'tools', 'rogue_fleet_probe.mjs');
const LOG = path.join(ROOT, 'runtime', 'rogue_fleet_watch.log');
const STATE = path.join(ROOT, 'runtime', 'rogue_fleet_state.json');
const LOCK = path.join(ROOT, 'runtime', 'rogue_fleet_watch.lock');
const HOLD = path.join(ROOT, 'runtime', 'rogue_hold.flag');   // ⏸ 暂停开关（存在＝人按住的，看守不动手）
const CASES = path.join(ROOT, 'runtime', 'fleet_cases');

const MACHINES = [
  { name: 'l-1', idx: 25, adb: '127.0.0.1:17184', pkg: 'com.YoStarJP.Arknights' },
  { name: 'l-2', idx: 28, adb: '127.0.0.1:17280', pkg: 'com.YoStarJP.Arknights' },
  { name: 'l-4', idx: 31, adb: '127.0.0.1:17376', pkg: 'com.hypergryph.arknights' },
  { name: 'l-5', idx: 34, adb: '127.0.0.1:16452', pkg: 'com.YoStarJP.Arknights' },
  { name: 'l-7', idx: 52, adb: '127.0.0.1:17028', pkg: 'com.YoStarJP.Arknights' },
];
const ADB = 'E:\\MuMu Player 12\\shell\\adb.exe';
const MGR = 'E:\\MuMu Player 12\\nx_main\\MuMuManager.exe';

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
// --clear-human <name>：清掉某台机器的"交人工 + 时效复核"状态（走锁与统一存档路径，别手工改 state.json）
const CLEAR_HUMAN = (() => { const i = argv.indexOf('--clear-human'); return i >= 0 ? argv[i + 1] : null; })();
// --drill-wrapup：受控演练"收工让路"（收掉所有在跑的 MAA），练完用 `--apply --force` 重发车
const DRILL_WRAPUP = argv.includes('--drill-wrapup');
const MAX_ACTIONS_PER_HOUR = Number(flag('--max-per-hour', 12));
const START_PER_ROUND = Number(flag('--start-per-round', 2));
const START_GAP_MIN = Number(flag('--start-gap-min', 8));
const NO_PROGRESS_MIN = Number(flag('--no-progress-min', 20));
const HUMAN_AFTER = Number(flag('--human-after', 2));
/** 产线"真空了"的判定：必须**连续**这么多分钟没有任何槽在写日志才发车。
 *  单轮 active=0 不算数——产线两台任务之间会有几十秒空档，被它骗到就会和产线并行（红线⑤）。 */
const PROD_IDLE_MIN = Number(flag('--prod-idle-min', 6));

// ---------- 小工具 ----------
const nowIso = () => new Date().toISOString();
const minSince = (iso) => (iso ? (Date.now() - new Date(iso).getTime()) / 60000 : Infinity);
const pad = (n) => String(n).padStart(2, '0');
const hm = () => { const d = new Date(); return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`; };
function log(line) {
  const text = `[${hm()}] ${line}`;
  console.log(text);
  try { fs.appendFileSync(LOG, text + '\n'); } catch { /* ignore */ }
}
function ssh(cmd, timeout = 120000) {
  try {
    return execFileSync('ssh',
      ['-i', KEY, '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=no',
        '-o', 'BatchMode=yes', HOST, cmd],
      { encoding: 'utf8', timeout, maxBuffer: 16 * 1024 * 1024 }).trim();
  } catch (e) {
    const out = String(e.stdout || '').trim();
    if (out) return out;
    return `__ERR__ ${String(e.stderr || e.message).slice(0, 200)}`;
  }
}
const b64ps = (s) => `powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand ${Buffer.from(s, 'utf16le').toString('base64')}`;

function loadState() {
  try {
    const s = JSON.parse(fs.readFileSync(STATE, 'utf8'));
    s.machines = s.machines || {};
    s.actions = Array.isArray(s.actions) ? s.actions : [];
    for (const m of MACHINES) {
      const e = (s.machines[m.name] = s.machines[m.name] || {});
      e.starts = e.starts || [];
      e.hist = e.hist || [];
      e.humanUntil = e.humanUntil || null;
      e.lastActionAt = e.lastActionAt || null;
      e.taskState = e.taskState || null;
      e.startFails = e.startFails || 0;
      e.pendingStart = e.pendingStart || null;
      e.ineffective = e.ineffective || 0;
      e.sameScreenStreak = e.sameScreenStreak || 0;   // 🧊 连续多少轮「与上一轮同图」（见 lib/rogue_plan.mjs 的 updateScreen）
      e.frozenShots = e.frozenShots || 0;             // 🧊 上膛后的冻结计数（阶梯用）
      e.screen = e.screen || null;                    // 🧊 上一轮屏幕指纹 {md5, at, since}
      e.lastShot = e.lastShot || null;                // 现场图留证记录
    }
    return s;
  } catch {
    const s = { machines: {}, actions: [] };
    for (const m of MACHINES) s.machines[m.name] = { starts: [], hist: [], humanUntil: null, lastActionAt: null, taskState: null };
    return s;
  }
}
const saveState = (s) => { try { fs.writeFileSync(STATE, JSON.stringify(s, null, 2)); } catch { /* ignore */ } };
const ledger = (o) => { try { fs.appendFileSync(LOG, JSON.stringify({ ts: nowIso(), ...o }) + '\n'); } catch { /* ignore */ } };

// ---------- 采样（复用 probe，顺带把 JSONL 写掉） ----------
function sample() {
  try {
    const out = execFileSync(process.execPath, [PROBE, '--json'], { encoding: 'utf8', timeout: 180000, maxBuffer: 16 * 1024 * 1024 });
    const i = out.indexOf('{');
    return JSON.parse(out.slice(i));
  } catch (e) {
    return { err: String(e.message || e).slice(0, 200) };
  }
}

// ---------- 动作（全部走主机的"登录态"计划任务，SSH 直拉模拟器是无效的） ----------
function startMachine(m, why) {
  log(`  L1 发车 ${m.name}（idx ${m.idx}）: ${why}`);
  // 先 adb connect 再触发：主机的 _launch_one.ps1 用 `adb -s <addr> shell getprop sys.boot_completed`
  // 判断"开机完成"，**不先 connect 会白等 240s 然后 timeout、MAA 根本不启动**（2026-09-23 实测踩过；
  // 已把 _launch_one.ps1 补上 connect，这里再保一道，不让自己的发车依赖别的脚本版本）。
  ssh(`"${ADB}" connect ${m.adb}`, 30000);
  const r = ssh(`schtasks /run /tn MAAOrch-StartOne-${m.idx}`, 60000);
  log(`     schtasks /run ⇒ ${r.replace(/\s+/g, ' ').slice(0, 120) || '(ok)'}`);
  return true;
}
function killMaa(m, why) {
  const p = `E:\\maa-alas\\fz-maa\\l\\${m.name}\\MAA.exe`;
  log(`  L2 收掉 ${m.name} 的 MAA 进程（本会话管辖，非 #0）: ${why}`);
  const ps = `$ProgressPreference='SilentlyContinue';`
    + `Get-Process MAA -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq '${p}' } | ForEach-Object { Stop-Process -Id $_.Id -Force; 'killed ' + $_.Id }`;
  const r = ssh(b64ps(ps), 60000);
  log(`     ${r.replace(/\s+/g, ' ').slice(0, 120) || '(nothing)'}`);
  return true;
}
function killGame(m, why) {
  log(`  L2 关 ${m.name} 的游戏客户端（${m.pkg}）: ${why}`);
  const r = ssh(`"${ADB}" -s ${m.adb} shell am force-stop ${m.pkg}`, 60000);
  log(`     ${r.replace(/\s+/g, ' ').slice(0, 100) || '(ok)'}`);
  return true;
}
function restartEmu(m, why) {
  log(`  L3 重启 ${m.name} 的模拟器（idx ${m.idx}）: ${why}`);
  ssh(`"${MGR}" control --vmindex ${m.idx} shutdown`, 90000);
  // ⚠️ 2026-09-24 §5.22（#0 那次演练实测的坑）：shutdown 之后**必须等 VM 真关掉**再 launch，
  // 否则 launch 返回 `-502 mainnx request failed`，而且 VM 会带着**挪走的 adb 端口**起来 ⇒ MAA 接不上。
  // 机队侧判"关掉了"用 adb 读不到 `sys.boot_completed`（主机的 MuMuManager info 对机队实例不可靠：
  // 跑着的 l-1 也报 is_process_started=false）。这里**不能** `adb kill-server` —— 主机的 adb 服务
  // 是 5 台机队 + 产线共用的，杀了会连坐。
  for (let i = 0; i < 15; i++) {
    const r = ssh(`"${ADB}" -s ${m.adb} shell getprop sys.boot_completed`, 20000);
    if (!/1/.test(r)) { log(`     VM 已下线（adb 读不到 boot_completed，等了约 ${i * 4}s）`); break; }
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 4000);
  }
  // ⚠️ 2026-09-24 15:42 **实测第 2 个坑**（比端口更常见）：`launch` 返回 `errcode:0` **只代表"请求被受理"**，
  // 不代表 VM 起来了 —— 这台冷启动花了 **>94 秒**，而原来只等 ~90 秒就判"看不到设备"，于是白报一次
  // "需要人看"（其实再等一会儿/再发一次 launch 就回来了）。⇒ 每次 launch 后**等足 150 秒**探 adb，
  // 还没起来就**重发 launch**（最多 3 次）；3 次都不行才留痕交人。
  let ok = false;
  for (let attempt = 1; attempt <= 3 && !ok; attempt++) {
    const r = ssh(`"${MGR}" control --vmindex ${m.idx} launch`, 90000);
    log(`     launch（第 ${attempt} 次）⇒ ${String(r).replace(/\s+/g, ' ').slice(0, 100) || '(ok)'}`);
    for (let i = 0; i < 19 && !ok; i++) {
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 8000);
      ssh(`"${ADB}" connect ${m.adb}`, 20000);
      ok = /1/.test(ssh(`"${ADB}" -s ${m.adb} shell getprop sys.boot_completed`, 20000));
    }
    if (!ok) log(`     第 ${attempt} 次 launch 后 150 秒还没看到设备，重发 launch`);
  }
  if (ok) log(`     ✅ ${m.name}: 模拟器回来了（${m.adb} 读到 boot_completed=1）`);
  else log(`     ⚠️ ${m.name}: 重启后 ${m.adb} 一直看不到设备 ⇒ 这一台需要人看`);
  return ok;
}
/** 等模拟器开机完成（adb getprop sys.boot_completed == 1）。`MuMuManager launch` 返回 ≠ 系统起来了，
 *  此时 monkey / adb shell 都会失败（2026-09-23 踩过）。最多探 8 次 ×15s，超时也往下走（发车任务自己还会等）。 */
function waitBoot(m, tries = 8) {
  for (let i = 1; i <= tries; i++) {
    ssh(`"${ADB}" connect ${m.adb}`, 20000);
    const r = ssh(`"${ADB}" -s ${m.adb} shell getprop sys.boot_completed`, 20000);
    if (String(r).trim() === '1') { log(`     开机完成（第 ${i} 次探测，约 ${(i - 1) * 15}s）`); return true; }
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 15000);
  }
  log('     ⚠️ 等开机超时（2 分钟），照发车（发车任务自己会再等）');
  return false;
}
/** 把游戏拉回前台（客户端还在、只是被切到桌面/别的应用）——比"重拉 MAA"便宜得多 */
function bringToFront(m, why) {
  log(`  R3 把 ${m.name} 的游戏拉回前台: ${why}`);
  const r = ssh(`"${ADB}" -s ${m.adb} shell monkey -p ${m.pkg} -c android.intent.category.LAUNCHER 1`, 60000);
  log(`     ${String(r).replace(/\s+/g, ' ').slice(-90) || '(ok)'}`);
  return true;
}
function snapCase(name) {
  const r = execFileSync(process.execPath, [PROBE, '--shot', name], { encoding: 'utf8', timeout: 150000 });
  return r.trim();
}
/**
 * 拍现场图（留证用）。**冻结识别不在这里做了** —— 2026-09-24 §5.21 起改由「每轮屏幕指纹 + updateScreen()」
 * 负责：只有这样才发现得了「MAA 在跑、被判 running-ok、其实画面冻着」的**静默冻结**（l-2 在结算页上
 * 僵了 3.4 小时没人管）。这里只负责：图存下来、md5 进日志、顺手和本轮采样指纹对一下。
 */
function snapCaseLogged(m, e) {
  let r = {};
  try { r = JSON.parse(snapCase(m.name)); } catch (err) {
    log(`     现场图: 拍图失败 ${String(err && err.message).slice(0, 80)}`);
    return null;
  }
  const probeMd5 = e.screen ? e.screen.md5 : null;
  log(`     现场图: ${JSON.stringify(r)}`
    + `${probeMd5 && probeMd5 !== r.md5 ? `（本轮采样指纹 ${String(probeMd5).slice(0, 8)} 与它不同 ⇒ 画面在这几分钟里动过）` : ''}`);
  e.lastShot = { md5: r.md5, at: nowIso(), file: r.file };
  return r;
}

// ---------- 主流程 ----------
function lockBusy() {
  try { return (Date.now() - fs.statSync(LOCK).mtimeMs) / 1000 < 600; } catch { return false; }
}
const takeLock = () => { try { fs.writeFileSync(LOCK, String(process.pid)); } catch { /* ignore */ } };
const dropLock = () => { try { fs.unlinkSync(LOCK); } catch { /* ignore */ } };

if (lockBusy()) { console.log('[rogue_fleet_watch] 已有实例在跑，跳过'); process.exit(0); }
takeLock();
try {
  const s = loadState();
  const rec = sample();
  if (rec.err) { log(`!! 采样失败: ${rec.err}`); dropLock(); process.exit(0); }

  const prodActive = rec.prod ? rec.prod.active : -1;
  // 复核 F8：让路闸以前只看"产线 90 秒内有没有写日志"⇒ 产线 MAA **活着但静默**（卡住、等重试）时会被
  // 当成"产线空了"，我们的车就开上去 ⇒ 同一模拟器两个 MAA（红线③）。加第二道闸：产线 MAA **进程数**。
  // 方向是 fail-closed（宁可我们多等，也不抢日常的位置）。
  const prodMaaProc = rec.prod && rec.prod.maaProc !== undefined ? rec.prod.maaProc : -1;
  const prodRunning = prodActive > 0 || prodMaaProc > 0;
  if (prodRunning) s.prodIdleSince = null; else s.prodIdleSince = s.prodIdleSince || nowIso();
  const prodIdleMin = s.prodIdleSince ? minSince(s.prodIdleSince) : 0;
  const prodIdleEnough = prodIdleMin >= PROD_IDLE_MIN;
  const h = new Date().getHours() + new Date().getMinutes() / 60;
  const inWindow = inWindowOf(h);              // 窗口规则与 lib/rogue_plan.mjs 同一份（那边有自测）
  const ramFree = rec.host ? rec.host.ramFreeGB : -1;

  const rows = [];
  for (const m of MACHINES) {
    const r = rec.machines[m.name] || {};
    const e = s.machines[m.name];
    e.executed = false;   // 每轮复位：否则它会随 state 留下去，"本轮动了 N 台"会变成累计值（2026-09-24 03:11 发现）
    // 零进展跟踪（同一 cur_task 卡多久）。
    // ⚠️ 2026-09-24 复核 F2/F4：`cur_task` 只在**任务切换**时落盘，多数轮次读不到 ⇒ 空值必须**保留上次已知
    // 任务**（sticky），否则 taskState 每轮被清、`stuckMin` 恒 0，零进展判据形同虚设（日志在写但卡死会被放生）。
    if (r.proc <= 0) e.taskState = null;
    else if (r.task && (!e.taskState || e.taskState.task !== r.task)) e.taskState = { task: r.task, since: nowIso() };
    const knownTask = e.taskState ? e.taskState.task : '';
    const stuckMin = e.taskState ? minSince(e.taskState.since) : 0;
    // 把 sticky 的任务名喂给判据：StartUp 豁免要靠它，否则 task 读空的轮次无法判断"是不是在启动"
    const cls = classify(r.task || !knownTask ? r : { ...r, task: knownTask });
    // 🧊 每轮屏幕指纹（探针新增 `screenMd5`；本地 #0 也有）：连着 3 轮（≈6 分钟）同图就「上膛」，
    // 交给 planMachine 的冻结阶梯（1 关客户端 / 2 重启模拟器 / ≥4 交人工）。
    // 这是**静默冻结**唯一的发现手段 —— 判成 running-ok、日志也在写、但画面一像素没动的机器，
    // 旧版一直没人管（l-2 在「探索終了」上僵了 3.4 小时，§5.21）。
    const scrBefore = { streak: e.sameScreenStreak || 0, frozen: e.frozenShots || 0 };
    const scr = updateScreen(e, r.screenMd5, Date.now());
    if ((scr.frozenShots !== scrBefore.frozen) || (scr.streak > scrBefore.streak && scr.streak >= 2)) {
      log(`  🧊 ${m.name}: 画面连着 ${scr.streak} 个样本与上一轮**同图**（自 ${scr.since ? String(scr.since).slice(11, 16) : '?'}）⇒ 冻结计数 ${scr.frozenShots}`
        + `${scr.frozenShots ? '（按冻结阶梯动手）' : '（还没到上膛线 3 个样本）'}`);
      if (scr.frozenShots) ledger({ machine: m.name, act: 'screen-frozen-rounds', streak: scr.streak, frozen: scr.frozenShots, since: scr.since });
    }
    // 发车复核：上一轮发的车到底起来了没有（登录态任务可能悄悄失败，不能只看"我发过车了"）
    if (r.proc > 0) { e.pendingStart = null; e.startFails = 0; }
    else if (e.pendingStart && minSince(e.pendingStart.at) >= 6) {
      e.startFails = (e.startFails || 0) + 1;
      log(`  !! ${m.name}: 发车后 ${Math.round(minSince(e.pendingStart.at))} 分钟仍是 parked ⇒ 这次发车没起来（累计 ${e.startFails} 次）`);
      ledger({ machine: m.name, act: 'start-failed', fails: e.startFails, tried: e.pendingStart.act });
      e.pendingStart = null;
    }
    // 客户端不在的连续计数：客户端重拉要 ~1 分钟，第一轮不动手（怕打断正在进行的重拉）
    e.gameGoneStreak = cls.kind === 'client-gone' ? (e.gameGoneStreak || 0) + 1 : 0;
    e.fgStreak = cls.kind === 'not-foreground' ? (e.fgStreak || 0) + 1 : 0;
    // 时效复核（用户 2026-09-24：「你要确保你的处理有效」）：上一轮动的手，到底救回来没有？
    // 以前台账只记"动了几次"，**不记有没有效** —— 于是 l-7 被无效重拉 3 次还记成 3 次"救援"。
    if (e.verify) {
      const vMin = minSince(e.verify.at);
      // 画面动过没有？—— 2026-09-24 §5.20/§5.21：只看「MAA 在跑」会把冻结屏记成有效（l-2 连记 10 次假有效）
      const screenMoved = !!(e.verify.screen && e.screen && e.screen.md5 && e.verify.screen !== e.screen.md5);
      if (r.proc > 0 && cls.kind === 'running-ok' && (screenMoved || vMin < 4)) {
        log(`  ✅ ${m.name}: 上次「${e.verify.act}」（${e.verify.kind}）有效 —— ${Math.round(vMin)} 分钟后已在跑${screenMoved ? '、画面也动过' : ''}`);
        ledger({ machine: m.name, act: 'rescue-ok', tried: e.verify, mins: Math.round(vMin) });
        e.verify = null;
        e.ineffective = 0;
      } else if (r.proc > 0 && cls.kind === 'running-ok' && !screenMoved && vMin >= 4) {
        // 🧊 **假有效**（§5.20/§5.21）：MAA 在跑、本来要记「✅ 有效」，但屏幕指纹证明这几分钟画面
        // 一像素没动 ⇒ 这次救援其实白做（l-2 那 10 次就是这么被记出来的）。记成无效，喂给交人工闸。
        e.ineffective = (e.ineffective || 0) + 1;
        log(`  ⚠️ ${m.name}: 上次「${e.verify.act}」（${e.verify.kind}）**无效** —— MAA 在跑但画面 ${Math.round(vMin)} 分钟一像素没动（假有效，累计 ${e.ineffective} 次）`);
        ledger({ machine: m.name, act: 'rescue-ineffective', reason: 'screen-frozen', tried: e.verify, fails: e.ineffective });
        e.verify = null;
      } else if (cls.kind === e.verify.kind) {
        e.ineffective = (e.ineffective || 0) + 1;
        log(`  ⚠️ ${m.name}: 上次「${e.verify.act}」（${e.verify.kind}）**无效** —— ${Math.round(vMin)} 分钟后还是同一形态（累计 ${e.ineffective} 次）`);
        ledger({ machine: m.name, act: 'rescue-ineffective', tried: e.verify, now: cls.kind, fails: e.ineffective });
        // ⚠️ 2026-09-24 09:45：**不在写盘前直接置 humanUntil**（那样会绕过"环境事件"判断）。
        // 只累计 `ineffective`，让 planMachine 统一决定要不要交人工（它知道 clusterSize / 形态 / 冷却）。
        e.verify = null;
      } else if (vMin > 15) {
        e.verify = null;
      }
    }
    rows.push({ m, r, cls, e, stuckMin });
  }

  const head = `产线=${prodRunning ? `在跑(${prodActive}/${rec.prod.slots}${prodMaaProc > 0 ? `,MAApproc=${prodMaaProc}` : ''})` : `空闲${Math.round(prodIdleMin)}min`}`
    + ` 窗口内=${inWindow ? 'Y' : 'N'} 内存=${ramFree}G apply=${APPLY}`
    + rows.map(({ m, r, cls, e }) => ` | ${m.name}:${r.proc ? 'run' : 'off'}/${cls.kind}${r.asstAge >= 0 ? `(${r.asstAge}s)` : ''}${e && e.sameScreenStreak >= 2 ? ` 🧊×${e.sameScreenStreak}` : ''}`).join('');
  if (STATUS_ONLY) { console.log(head); saveState(s); dropLock(); process.exit(0); }
  // 2026-09-24：手动清"交人工/时效复核"状态也要走**同一个锁 + 同一条存档路径**。
  // 起因：我先前用 node 一行脚本直接改 state.json（为了测 login-lost 的有效动作），留下了
  // `hist` 里有 needs-human 但 `humanUntil=null`、以及"ledger 有 rescue-ok 但 hist 没记录"的
  // 不一致痕迹，night-watch 据此怀疑"两个版本在并发写 state"。锁其实是好的（实测第二个实例会被挡），
  // 但**手工改状态**这个隐患是真的 ⇒ 以后统一走 `--clear-human <machine>`。
  if (CLEAR_HUMAN) {
    const e = s.machines[CLEAR_HUMAN];
    if (!e) { console.log(`没有这台机器: ${CLEAR_HUMAN}`); dropLock(); process.exit(1); }
    const before = { humanUntil: e.humanUntil, verify: e.verify, ineffective: e.ineffective };
    e.humanUntil = null; e.verify = null; e.ineffective = 0;
    saveState(s);
    log(`  [手动] 清掉 ${CLEAR_HUMAN} 的交人工/时效复核状态（原值 ${JSON.stringify(before)}）`);
    dropLock(); process.exit(0);
  }
  log(head);
  // ⏸ **暂停开关**（2026-09-26 05:5x，用户原话「主要是日常吧 要恢复日常 这肉鸽先往后放放」）：
  // `runtime/rogue_hold.flag` 存在 ⇒ 看守只记状态与心跳、**完全不动手**（不发车、不收工、不救）。
  // 删除该文件即恢复自动盯守。用开关而不是"禁用计划任务"：心跳/看板/一页纸都还活着，谁看都知道是"人按住的"。
  if (fs.existsSync(HOLD)) {
    const why = (() => { try { return fs.readFileSync(HOLD, 'utf8').trim(); } catch { return ''; } })();
    log(`  ⏸ 已暂停（${why || '手动'}）⇒ 本轮不动手（删掉 runtime/rogue_hold.flag 即恢复）`);
    saveState(s);
    dropLock();
    process.exit(0);
  }

  // 预算（小时配额）
  const cut = Date.now() - 3600 * 1000;
  s.actions = s.actions.filter((a) => new Date(a.ts).getTime() > cut);
  // ⚠️ 2026-09-24 02:25 修正：配额只该限制**自动**反复骚扰，不该被**人工演练/--force** 占用。
  // 起因：01:59 的收工让路演练（8 条动作）把 12/h 吃满 ⇒ 02:12–02:26 里 4 台真断连却"到小时配额，跳过"，
  // l-5 被拖了 15 分钟以上。⇒ 手工触发的动作标 `manual:true` 并**不计入**配额。
  // ⚠️⚠️ 2026-09-24 02:47 第二处修正：配额原来是**全队 12/h**，对 5 台机队太紧（一台出问题就能把全队饿死）。
  // 本意是"别把某一台机器反复折腾" ⇒ 改成**每台机器 12/h**（外加全队 40/h 的天花板）。
  const MAX_TOTAL_PER_HOUR = MAX_ACTIONS_PER_HOUR * 4;
  const counted = s.actions.filter((a) => !a.manual && !a.envEvent);
  const budgetLeft = (name) => {
    if (counted.length >= MAX_TOTAL_PER_HOUR) return 0;
    return MAX_ACTIONS_PER_HOUR - counted.filter((a) => a.machine === name).length;
  };

  // 🌐 **环境事件检测**（2026-09-24 02:45 加，当晚最大的教训）：同一轮里 ≥2 台同时处于故障形态
  // ⇒ 这不是"每台机器各自坏了"，而是主机/网络/游戏服务器的环境问题。此时逐台"救 3 次就交人工"是**错的**
  // （当晚把 l-1/l-7/l-4 全静默，5 台里 3 台停着，用户指出「你并没有看住」）。
  const failureKind = (k) => k !== 'running-ok' && k !== 'running-after-error' && k !== 'unknown'
    && k !== 'parked' && k !== 'process-gone';
  const clusterSize = rows.filter(({ cls }) => failureKind(cls.kind)).length;
  if (clusterSize >= 2) {
    log(`🌐 环境事件：同一轮 ${clusterSize} 台同时异常（${rows.filter(({ cls }) => failureKind(cls.kind)).map(({ m }) => m.name).join('/')}）⇒ 不交人工、直接上重动作，且不计入小时配额`);
    ledger({ act: 'env-event', clusterSize, machines: rows.filter(({ cls }) => failureKind(cls.kind)).map(({ m }) => m.name) });
  }

  // ⚠️ 闸门与阶梯一律走 lib/rogue_plan.mjs —— 那份逻辑有离线自测（tools/rogue_plan_selftest.mjs），
  // 这样"发车之前"就能证明决策对，而不是等机器跑起来才发现判错。
  const g = gate({ prodRunning, prodIdleMin, hourFloat: h, ramFree, force: FORCE, prodIdleMinRequired: PROD_IDLE_MIN });
  const perRound = startPerRoundOf(ramFree, START_PER_ROUND);
  const ctx = {
    force: FORCE, nowMs: Date.now(), startGapMin: START_GAP_MIN,
    humanAfter: HUMAN_AFTER, noProgressMin: NO_PROGRESS_MIN, gateOpen: g.open,
    clusterSize,                 // 🌐 ≥2 ⇒ 环境事件：不交人工 + 直接重动作（见 planMachine）
  };
  // 队列体检（每个号发车前都要过）：启用的任务必须都是"我们的"（开始唤醒/肉鸽/自定任务），
  // 且至少含一个真正刷图的（肉鸽或自定任务）。2026-09-23 实测 l-7 只勾了「开始唤醒」
  // ⇒ 这种机器发了车也只是进游戏发呆，白占一台 —— 判据要在这儿拦住。
  const OURS_TASKS = new Set(['StartUp', 'Roguelike', 'Custom']);
  const queueInfo = (r) => {
    const q = Array.isArray(r.queue) ? r.queue : null;
    if (!q || q.length === 0) return { ok: null, list: [] };          // 采不到就别拦（fail-open，别把好机器挡了）
    const ok = q.every((t) => OURS_TASKS.has(t)) && (q.includes('Roguelike') || q.includes('Custom'));
    return { ok, list: q };
  };
  const plans = rows.map(({ m, r, e, cls, stuckMin }) => {
    const qi = queueInfo(r);
    const p = planMachine(cls, e, { ...ctx, queueOk: qi.ok, queueList: qi.list, stuckMin });
    if (qi.ok === false) log(`  ⚠️ ${m.name}: 队列里没有肉鸽（启用: ${qi.list.join('/')}）⇒ 今晚不发它，先留痕`);
    return { m, e, cls, plan: p, queue: qi };
  });

  // 冲突告警：红线⑤"肉鸽×产线不许并行"是本质约束 —— 真撞上了必须大声留痕
  if (prodRunning) {
    const live = rows.filter(({ r }) => r.proc > 0).map(({ m }) => m.name);
    if (live.length) {
      log(`  ⚠️ 冲突：产线在跑（${prodActive}/${rec.prod.slots}），而肉鸽也在跑（${live.join('/')}）—— 不许并行，请尽快收一台`);
      ledger({ act: 'conflict', prodActive, roguelike: live });
    }
  }

  // 收工让路：产线回来了、或天快亮了 ⇒ 把我们自己的 MAA 收掉，避免和产线抢同一台模拟器（红线③）
  const runningNames = rows.filter(({ r }) => r.proc > 0).map(({ m }) => m.name);
  const wu = wrapUp({ prodRunning, hourFloat: h, runningCount: runningNames.length, force: FORCE });
  // --drill-wrapup：**受控演练**收工让路（和 #0 的 l1/l2/l3 演练同一个道理：最危险的路径要先在可控时
  // 演练一遍，别等到"产线突然回来"那一刻才第一次运行）。演练只收 MAA、不动模拟器，练完可用 `--apply --force` 重发车。
  if (APPLY && (wu.need || DRILL_WRAPUP)) {
    const reason = DRILL_WRAPUP ? '演练（人工触发，验证收工让路路径）' : wu.why;
    log(`${DRILL_WRAPUP ? '🧪 演练·' : ''}收工让路（${reason}）：收掉 ${runningNames.join('/')} 的 MAA`);
    for (const { m, r } of rows) {
      if (r.proc <= 0) continue;
      killMaa(m, `收工让路：${reason}`);
      ledger({ machine: m.name, act: DRILL_WRAPUP ? 'wrap-up-drill' : 'wrap-up', reason });
    }
    for (const { m, e } of rows) {
      if (e.pendingStart) e.pendingStart = null;
      e.doneForShift = true;
    }
    saveState(s);
    dropLock();
    process.exit(0);
  }

  // 闸门刚打开时留一条醒目的横幅（早上翻日志一眼就能看到"几点开始发的车"）
  if (APPLY && g.open && s.lastGateOpen === false) {
    log(`🔓 闸门打开（${g.why}；产线空 ${Math.round(prodIdleMin)} 分钟）—— 开始发车`);
    ledger({ act: 'gate-open', prodIdleMin: Math.round(prodIdleMin), ramFree });
  }
  s.lastGateOpen = g.open;

  if (!APPLY) {
    log(`  闸门=${g.open ? '开' : '关'}（${g.why}）｜一轮最多动 ${perRound} 台｜每台配额余 ${MACHINES.map((x) => x.name + ':' + budgetLeft(x.name)).join(' ')}`);
    for (const { m, cls, plan } of plans) {
      if (plan.action === 'none') continue;
      log(`  [dry-run] ${m.name}: ${cls.kind}（${cls.why}）⇒ 闸开时会「${actionLabel(plan.action)}」`);
    }
    saveState(s);
    dropLock();
    process.exit(0);
  }
  if (!g.open) {
    log(`  闸门关：${g.why} ⇒ 本轮不动手`);
    for (const { m, cls } of plans) {
      if (cls.kind !== 'parked' && cls.kind !== 'running-ok' && cls.kind !== 'unknown') {
        log(`    · ${m.name}: ${cls.kind}（记下，等闸开了再处理）`);
      }
    }
    saveState(s);
    dropLock();
    process.exit(0);
  }

  // 闸开了 ⇒ 按计划动手
  let acted = 0;
  for (const { m, e, cls, plan } of plans) {
    if (plan.action === 'none') {
      if (cls.kind !== 'running-ok') log(`  ${m.name}: 不动手（${plan.why}）`);
      continue;
    }
    if (budgetLeft(m.name) <= 0) { log(`  ${m.name}: 到每台配额（${MAX_ACTIONS_PER_HOUR}/台·小时），跳过`); continue; }
    const usesStart = plan.action !== 'needs-human' && plan.action !== 'focus-game';
    if (usesStart && acted >= perRound) {
      log(`  ${m.name}: 本轮动手数已满（${perRound} 台，内存 ${ramFree}G），下一轮`);
      continue;
    }
    const doAct = (act) => {
      s.actions.push({ ts: nowIso(), machine: m.name, act, manual: !!(FORCE || DRILL_WRAPUP), envEvent: clusterSize >= 2 });
      e.lastActionAt = nowIso();
      e.executed = true;            // 只在这里置位 ⇒ "本轮动了 N 台"只数真动手的
      e.hist.push({ ts: nowIso(), kind: cls.kind, act });
      e.hist = e.hist.slice(-20);
      // 时效复核（用户 2026-09-24 的要求：「你要确保你的处理有效」）：
      // 记下这次动作的形态，下一轮开始核对"到底有没有把它救回来"。
      if (act === 'start' || act === 'restart-maa' || act === 'killgame-restart' || act === 'restart-emulator' || act === 'focus-game') {
        e.verify = { at: nowIso(), act, kind: cls.kind, screen: e.screen ? e.screen.md5 : null };   // 记下动手瞬间的画面指纹，供下轮判「有没有真的动」
      } else {
        e.verify = null;              // 交人工/收工不吃时效复核（它本来就不动手）
      }
    };
    log(`  ${m.name}: ${cls.kind} → ${actionLabel(plan.action)}（${plan.why}）`);
    if (plan.action === 'start') {
      startMachine(m, plan.why);
      e.starts.push(nowIso());
      e.pendingStart = { at: nowIso(), act: 'start' };
      doAct('start');
      acted++;
    } else if (plan.action === 'restart-maa') {
      snapCaseLogged(m, e);
      killMaa(m, plan.why);
      startMachine(m, plan.why);
      e.pendingStart = { at: nowIso(), act: 'restart-maa' };
      doAct('restart-maa');
      acted++;
    } else if (plan.action === 'killgame-restart') {
      snapCaseLogged(m, e);
      killGame(m, plan.why);
      // ⚠️ 2026-09-24 10:35：**关掉客户端之后自己把它拉起来**，别再指望 MAA 的「开始唤醒」。
      // 实测（l-1/l-2 每 4~6 分钟被救一次的死循环）：MAA 的 StartUp 拉不起客户端（gui 报「任务出错: 开始唤醒」），
      // 但同一条 `monkey -c LAUNCHER` 一下就把游戏拉起来了（pid + 前台都正常）⇒ 由看守自己拉，MAA 起来时客户端已在。
      bringToFront(m, `killgame-restart 后自己拉起客户端（MAA StartUp 拉不动）`);
      killMaa(m, plan.why);
      startMachine(m, plan.why);
      e.pendingStart = { at: nowIso(), act: 'killgame-restart' };
      doAct('killgame-restart');
      acted++;
    } else if (plan.action === 'restart-emulator') {
      snapCaseLogged(m, e);
      killMaa(m, plan.why);                       // 模拟器一重启 MAA 的 adb 连接就断 ⇒ 先收掉它，别让它刷错误日志
      restartEmu(m, plan.why);
      waitBoot(m);                                // 等开机完成（最多 ~2 分钟）；不等就会对着没起来的 VM 白发车
      bringToFront(m, '重启模拟器后自己把客户端拉起来（MAA 的「开始唤醒」拉不动）');
      startMachine(m, plan.why);
      e.pendingStart = { at: nowIso(), act: 'restart-emulator' };
      doAct('restart-emulator');
      acted++;
    } else if (plan.action === 'focus-game') {
      snapCaseLogged(m, e);
      bringToFront(m, plan.why);
      doAct('focus-game');          // 不算"发车"，不吃一轮的发车上限
    } else if (plan.action === 'needs-human') {
      e.humanUntil = new Date(Date.now() + 2 * 3600 * 1000).toISOString();
      ledger({ machine: m.name, kind: cls.kind, act: 'needs-human', hist: e.hist.slice(-5) });
      snapCaseLogged(m, e);
      doAct('needs-human');
      log('     ⇒ 交人工：2 小时不再碰它（留痕 + 现场图）');
    }
    saveState(s);
  }
  // 一轮的收尾小结（扫日志用；不动手的机器也点出来，免得"没消息"看不出是漏了还是故意的）
  // ⚠️ 2026-09-24 修正：`did` 原来算的是"计划里非 none 的机器"，**包含因配额/冷却没真动手的**
  // ⇒ 日志会出现"动了 5 台"其实只动了 2 台（这种口径误导过一次：把无效重拉当救援）。现在只数真动手的。
  const did = plans.filter((x) => x.e.executed);
  if (APPLY && g.open) {
    const wait = plans.filter((x) => x.plan.action === 'none' && x.cls.kind !== 'running-ok')
      .map((x) => `${x.m.name}:${x.plan.why.slice(0, 26)}`);
    log(`  本轮：动了 ${did.length} 台${did.length ? '（' + did.map((x) => x.m.name).join('/') + '）' : ''}`
      + (wait.length ? `；没动：${wait.join('，')}` : ''));
  }
  saveState(s);
} catch (e) {
  log(`!! 看守异常: ${e && e.message ? e.message : e}`);
} finally {
  dropLock();
}
