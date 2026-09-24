#!/usr/bin/env node
/**
 * rogue0_watch.mjs —— 本机 #0「日服 · MuMu 实例 0 · emulator-5554」水月肉鸽保活看守
 * ──────────────────────────────────────────────────────────────────────────────
 * 这台是**用户手动开的 MAA GUI**（`D:\Xiach\M-A\MAA\MAA-v6.12.2-win-x64`，目录名骗人，
 * 实际程序 v6.18.0-beta.x；窗口标题里能读到「MuMu 模拟器 (emulator-5554) - 日服 (YostarJP)」）。
 * 队列只有「开始唤醒 + 自动肉鸽（水月 Mizuki / Exp / 99999 次）」，用户口径是**一直刷**。
 *
 * 红线（MAAOrch rules，任何会话都不得违反）：
 *   · **不许 Stop-Process / taskkill 这台 MAA.exe**、不许 adb 读写游戏私有数据
 *     ⇒ 本看守只点 GUI 按钮、只拉进程/模拟器；唯一的"减法"是 `am force-stop` **游戏客户端**
 *       （L2，等价于人手动关游戏再让 MAA 重拉，是既定应急手段，不碰 MAA 进程）。
 *   · 同一模拟器同一时刻只许一个 MAA ⇒ 只在"MAA 进程不在"时拉。
 *   · 队列不是水月肉鸽（说明别人正拿 #0 干别的）⇒ 只记 WARN、**绝不动手**，不跟人抢。
 *
 * 2026-09-23 20:4x 亲历的第一个真实病例（写进判据）：
 *   MAA 任务链被停 → 再点「开始」→ StartUp 20 次重试全灭、Roguelike 也在 `Mizuki@Roguelike@Begin`
 *   重试 20/20 → TaskChainError。**根因不在 MAA：日服客户端自己卡死在「通信途絶」加载页**
 *   （两张截图 md5 完全一致），MAA 再怎么点都没用 ⇒ 必须 `am force-stop` 游戏让 StartUp 重拉。
 *   ⇒ 判据必须"看内容"（cur_task / cur_retry / retry_times / 画面是否冻结），而不是"日志还在写就没事"。
 *
 * 复活动作（阶梯按**证据**升级，每级都留痕；有冷却与小时配额，防死循环烧机器）：
 *   E1 设备 emulator-5554 不在线      → MuMuManager control -v 0 launch，等 Android ≤240s
 *   E2 MAA.exe 不在                    → Start-Process 拉起，等窗口 ≤120s
 *   L1 空闲 + 队列=水月肉鸽（连续两轮）→ UIA Invoke「开始」(Link Start!)
 *   L2 L1 之后那条链仍报错 / 画面冻结 / 肉鸽入口重试耗尽 → `am force-stop <游戏包>`（只关游戏）→ 再点「开始」
 *   L3 L2 之后那条链仍报错             → 重启模拟器（MuMuManager shutdown→launch）→ 再点「开始」
 *   R1 在跑但"零进展"（同一 cur_task 卡 >15min 且重试耗尽/画面冻结/只剩 screencap）→ 点「停止」再点「开始」
 *   🧊 静默冻结（2026-09-24 §5.21）：按钮=running、日志也在写，但**画面连着几轮一像素没动** ⇒ 逐级加重
 *      （1 关客户端 / 2 重启模拟器 / ≥4 停手 30 分钟交人工）。旧版这一格是空的：只要按钮 running、
 *      客户端在、前台正常就一律判 OK —— 画面冻着的 #0 可以一直"看起来在刷"。
 *   封顶：L3 之后还不行 ⇒ 30 分钟不再动手 + 大声留痕（不把机器烧穿）
 *
 * 用法：
 *   node tools/rogue0_watch.mjs              # dry-run（只看，不动手）
 *   node tools/rogue0_watch.mjs --apply      # 真守（计划任务 MAAOrch-Rogue0Watch 每 2 分钟跑这个）
 *   node tools/rogue0_watch.mjs --status     # 只打状态行
 *   · 日志 runtime/rogue0_watch.log（人看）｜ runtime/rogue0_watch_state.json（机读）
 *   · 取证截图 runtime/rogue0_cases/*.png（每次动手前存一张"现场"）
 */
import { execFileSync } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { lastChainFrom } from './lib/rogue_judge.mjs';
import { updateScreen } from './lib/rogue_plan.mjs';   // 🧊 与机队共用同一份"屏幕指纹 → 冻结计数"内核（有自测）

// ---------- 固定路径（#0 的一切都写死在这里，免得又认错机器） ----------
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const MAA_DIR = 'D:\\Xiach\\M-A\\MAA\\MAA-v6.12.2-win-x64';
const MAA_EXE = path.join(MAA_DIR, 'MAA.exe');
const ASST_LOG = path.join(MAA_DIR, 'debug', 'asst.log');
const GUI_LOG = path.join(MAA_DIR, 'debug', 'gui.log');
const ADB = 'D:\\Xiach\\MuMuPlayer\\nx_main\\adb.exe';
const MGR = 'D:\\Xiach\\MuMuPlayer\\nx_main\\MuMuManager.exe';
const DEVICE = 'emulator-5554';
const GAME_PKG = 'com.YoStarJP.Arknights';        // 日服客户端（#0 就是它）
const VM_INDEX = 0;

const LOG = path.join(ROOT, 'runtime', 'rogue0_watch.log');
const STATE = path.join(ROOT, 'runtime', 'rogue0_watch_state.json');
const LOCK = path.join(ROOT, 'runtime', 'rogue0_watch.lock');
const CASES = path.join(ROOT, 'runtime', 'rogue0_cases');

// ---------- 参数 ----------
const argv = process.argv.slice(2);
const flag = (k, d) => {
  const i = argv.indexOf(k);
  if (i < 0) return d;
  const v = argv[i + 1];
  return v && !v.startsWith('--') ? v : true;
};
const APPLY = argv.includes('--apply');
const STATUS_ONLY = argv.includes('--status');
const DRILL = flag('--drill', null);            // --drill l1 | --drill l2：受控演练复活动作序列
const FREEZE_DRILL = argv.includes('--freeze-drill');   // 受控演练「静默冻结」阶梯：把屏幕指纹固定住，模拟画面冻住

const MAX_PER_HOUR = Number(flag('--max-per-hour', 10));
const COOLDOWN_SEC = Number(flag('--cooldown-sec', 45));
const NO_PROGRESS_SEC = Number(flag('--no-progress-sec', 900));   // 同一 cur_task 卡多久算"零进展"
const BOOT_WAIT_SEC = Number(flag('--boot-wait-sec', 240));
const MAA_WAIT_SEC = Number(flag('--maa-wait-sec', 120));
const SHOT_GAP_SEC = Number(flag('--shot-gap-sec', 12));

// ---------- 小工具 ----------
const now = () => new Date();
const sleepSync = (ms) => Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
const pad = (n) => String(n).padStart(2, '0');
const stamp = () => {
  const d = now();
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} `
    + `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
};
function log(line) {
  const text = `[${stamp()}] ${line}`;
  console.log(text);
  try { fs.appendFileSync(LOG, text + '\n'); } catch { /* 日志写不进去不能影响看守 */ }
}
function ps(cmd, timeout = 60000) {
  try {
    return execFileSync('powershell.exe',
      ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', cmd],
      { encoding: 'utf8', timeout }).trim();
  } catch (e) {
    const out = String(e.stdout || '').trim();
    if (out) return out;
    return `__ERR__ ${String(e.stderr || e.message).slice(0, 200)}`;
  }
}
function sh(exe, args, timeout = 60000) {
  try {
    return execFileSync(exe, args, { encoding: 'utf8', timeout }).trim();
  } catch (e) {
    return `__ERR__ ${String(e.stderr || e.message).slice(0, 200)}`;
  }
}
const jsonOf = (s) => { try { return JSON.parse(s); } catch { return null; } };
const minSince = (iso) => (iso ? (now() - new Date(iso)) / 60000 : Infinity);

// ---------- 观测：MAA 进程 ----------
function maaInstances() {
  const out = ps('Get-CimInstance Win32_Process -Filter "Name=\'MAA.exe\'" '
    + '| Select-Object ProcessId,ExecutablePath | ConvertTo-Json -Compress');
  if (out.startsWith('__ERR__')) return null;                 // 取不到 ⇒ fail-closed（不动手）
  if (!out) return [];
  const j = jsonOf(out);
  if (!j) return null;
  return (Array.isArray(j) ? j : [j]).map((r) => ({ pid: r.ProcessId, exe: String(r.ExecutablePath || '') }));
}
const isOurs = (m) => m.exe && m.exe.toLowerCase() === MAA_EXE.toLowerCase();

// ---------- 观测：窗口（UIA） ----------
function uiaState(pid) {
  const script = [
    "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)",
    'Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes',
    '$root=[System.Windows.Automation.AutomationElement]::RootElement',
    `$c=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty,${pid})`,
    '$w=$root.FindFirst([System.Windows.Automation.TreeScope]::Children,$c)',
    'if(-not $w){ \'{"win":false}\'; exit 0 }',
    '$o=@{win=$true;title=$w.Current.Name;offscreen=$w.Current.IsOffscreen;checks=@();buttons=@()}',
    '$ct=[System.Windows.Automation.ControlType]',
    '$cb=$w.FindAll([System.Windows.Automation.TreeScope]::Descendants,(New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,$ct::CheckBox)))',
    'foreach($x in $cb){ $t="?"; try{ $p=$x.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern); $t=$p.Current.ToggleState.ToString() }catch{}; $o.checks+=@{n=$x.Current.Name;t=$t} }',
    '$bs=$w.FindAll([System.Windows.Automation.TreeScope]::Descendants,(New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,$ct::Button)))',
    'foreach($x in $bs){ if($x.Current.Name){ $o.buttons+=@{n=$x.Current.Name;enabled=$x.Current.IsEnabled} } }',
    '$o | ConvertTo-Json -Depth 5 -Compress',
  ].join('; ');
  const out = ps(script, 45000);
  if (out.startsWith('__ERR__')) return { err: out };
  return jsonOf(out) || { err: 'uia-parse' };
}

/** 点主按钮：中文名用码点比（命令行代码页靠不住），英文名直接比 */
function clickMain(pid, which) {
  const want = which === 'stop'
    ? "$n -eq 'Stop' -or ($n.Length -eq 2 -and [int][char]$n[0] -eq 0x505C -and [int][char]$n[1] -eq 0x6B62)"   // 停止
    : "$n -eq 'Link Start!' -or $n -eq 'Start' -or ($n.Length -eq 2 -and [int][char]$n[0] -eq 0x5F00 -and [int][char]$n[1] -eq 0x59CB)"; // 开始
  const script = [
    "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)",
    'Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes',
    '$root=[System.Windows.Automation.AutomationElement]::RootElement',
    `$c=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty,${pid})`,
    '$w=$root.FindFirst([System.Windows.Automation.TreeScope]::Children,$c)',
    'if(-not $w){ \'{"r":"nowindow"}\'; exit 0 }',
    '$ct=[System.Windows.Automation.ControlType]',
    '$bs=$w.FindAll([System.Windows.Automation.TreeScope]::Descendants,(New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,$ct::Button)))',
    '$hit=$null',
    `foreach($x in $bs){ $n=$x.Current.Name; if(-not $n){continue}; if(${want}){ $hit=$x; break } }`,
    'if(-not $hit){ \'{"r":"nobutton"}\'; exit 0 }',
    'try{ $p=$hit.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern); $p.Invoke(); \'{"r":"invoked"}\' }catch{ \'{"r":"invoke-fail"}\' }',
  ].join('; ');
  const j = jsonOf(ps(script, 45000));
  return j ? j.r : 'ps-err';
}

// ---------- 观测：设备 / 画面 / 日志内容 ----------
function deviceOnline() {
  const out = sh(ADB, ['devices'], 30000);
  if (out.startsWith('__ERR__')) return null;
  return out.split(/\r?\n/).some((l) => {
    const m = l.match(/^(\S+)\s+(device|offline|unauthorized|no permissions)\b/);
    return m && m[1] === DEVICE && m[2] === 'device';
  });
}
/** 截一张图：返回 {md5, bytes}；失败返回 null */
/** 屏幕指纹（设备侧 `screencap -p | md5sum`，一次 adb 往返、不回传图片）—— 每轮都取，用于发现**静默冻结**。 */
function screenFingerprint() {
  if (FREEZE_DRILL) return 'frozen-drill-000000000000000000000000';   // 演练：假装画面冻住（见 --freeze-drill）
  const r = sh(ADB, ['-s', DEVICE, 'shell', 'screencap -p | md5sum'], 30000);
  const m = /^([0-9a-f]{32})\s/m.exec(r || '');
  return m ? m[1] : null;
}

function screencap() {
  try {
    const buf = execFileSync(ADB, ['-s', DEVICE, 'exec-out', 'screencap', '-p'],
      { maxBuffer: 32 * 1024 * 1024, timeout: 30000 });
    if (!buf || buf.length < 20000) return null;
    return { buf, md5: crypto.createHash('md5').update(buf).digest('hex') };
  } catch { return null; }
}
function saveCase(tag, shot) {
  try {
    fs.mkdirSync(CASES, { recursive: true });
    const p = path.join(CASES, `${tag}.png`);
    fs.writeFileSync(p, shot.buf);
    return p;
  } catch { return ''; }
}
/** 游戏客户端还活着吗？前台是不是它？—— MAA"在跑"不等于客户端在（2026-09-23 演练实测）。
 *  一次 adb 调用拿两个信号（用 marker 分段，免得 pidof 空输出时把 dumpsys 里的 hash 当 pid）。 */
function gameState() {
  const out = sh(ADB, ['-s', DEVICE, 'shell',
    `echo P-MARK; pidof ${GAME_PKG}; echo F-MARK; dumpsys window | grep mCurrentFocus`], 30000);
  if (out.startsWith('__ERR__')) return null;
  const seg = out.split(/F-MARK/);
  const pSeg = (seg[0] || '').split(/P-MARK/)[1] || '';
  const alive = /^\s*\d+/m.test(pSeg);
  const fg = (out.match(/mCurrentFocus=Window\{[^}]*?\s([\w.]+)\//) || [])[1] || '';
  return { alive, fg };
}
/** 把游戏拉回前台（客户端还在、只是被切到桌面/别的应用） */
function bringGameToFront() {
  const r = sh(ADB, ['-s', DEVICE, 'shell', 'monkey', '-p', GAME_PKG, '-c', 'android.intent.category.LAUNCHER', '1'], 30000);
  log(`  R3 把游戏拉回前台 ⇒ ${String(r).replace(/\s+/g, ' ').slice(0, 80)}`);
  sleepSync(8000);
  const st = gameState();
  log(`     现在前台=${st ? (st.fg || '?') : 'n/a'}`);
  return !!(st && (!st.fg || st.fg === GAME_PKG));
}
function asstSilentSec() {
  try { return Math.round((now() - fs.statSync(ASST_LOG).mtime) / 1000); } catch { return -1; }
}
function readTail(file, bytes) {
  try {
    const size = fs.statSync(file).size;
    const fd = fs.openSync(file, 'r');
    const start = Math.max(0, size - bytes);
    const buf = Buffer.alloc(size - start);
    fs.readSync(fd, buf, 0, buf.length, start);
    fs.closeSync(fd);
    return buf.toString('utf8');
  } catch { return ''; }
}
/** asst.log 的"进展"：最后一条 ProcessTask 的 cur_task/重试情况（看内容，不看 mtime） */
function asstProgress() {
  const text = readTail(ASST_LOG, 300 * 1024);
  if (!text) return { task: '', retry: -1, retryTimes: -1, at: null, retryExhausted: false, ocrOnly: true };
  const lines = text.split(/\r?\n/);
  const lastTaskLine = [...lines].reverse().find((l) => l.includes('"class":"asst::ProcessTask"'));
  let task = ''; let retry = -1; let retryTimes = -1; let at = null;
  if (lastTaskLine) {
    const m1 = lastTaskLine.match(/"cur_task":"([^"]*)"/);
    const m2 = lastTaskLine.match(/"cur_retry":(\d+)/);
    const m3 = lastTaskLine.match(/"retry_times":(\d+)/);
    const m4 = lastTaskLine.match(/^\[([\d-]+ [\d:.]+)\]/);
    task = m1 ? m1[1] : '';
    retry = m2 ? Number(m2[1]) : -1;
    retryTimes = m3 ? Number(m3[1]) : -1;
    at = m4 ? m4[1] : null;
  }
  // 最后 120 行里有没有"实质进展"行（cur_task / OCR / 战斗），还是只剩 screencap 噪声
  const tail120 = lines.slice(-120).join('\n');
  const ocrOnly = !/"class":"asst::ProcessTask"/.test(tail120) && !/WordOcr/.test(tail120);
  const lastLine = [...lines].reverse().find((l) => l.trim());
  const lm = lastLine ? lastLine.match(/^\[([\d-]+ [\d:.]+)\]/) : null;
  return {
    task, retry, retryTimes, at,
    retryExhausted: retryTimes > 0 && retry >= retryTimes,
    ocrOnly,
    lastLineAt: lm ? lm[1] : null,
  };
}
/** gui.log 的"最后一条任务链"：开始时间 + 结局（出错/干净完成/被停）+ 出错的任务名 */
function lastChain() {
  const text = readTail(GUI_LOG, 400 * 1024);
  return lastChainFrom(text.split(/\r?\n/));
}

// ---------- 队列判据 ----------
const OTHER_TASKS = ['理智作战', '基建换班', '自动公招', '信用收支', '领取奖励', '生息演算', '自定任务'];
function queueVerdict(st) {
  if (!st || st.err || !st.win) return { kind: 'unknown', on: [], off: [] };
  const on = st.checks.filter((c) => c.t === 'On').map((c) => c.n);
  const roguelike = on.includes('自动肉鸽');
  const startup = on.includes('开始唤醒');
  const others = on.filter((n) => OTHER_TASKS.includes(n));
  if (roguelike && startup && others.length === 0) return { kind: 'roguelike', on, off: [] };
  if (!roguelike && !startup && others.length === 0) return { kind: 'empty', on, off: [] };
  return { kind: 'other', on, off: others };
}
/**
 * 主按钮识别（2026-09-23 实测这版 MAA）：**空闲时叫「Link Start!」，在跑时叫「停止」**
 * —— 我原先按"开始"想当然，第一次实测就翻在 `按钮=?` 上 ⇒ 中英文都认、留兜底。
 */
const RUNNING_NAMES = ['停止', 'Stop'];
const IDLE_NAMES = ['Link Start!', '开始', 'Start'];
function buttonKind(st) {
  if (!st || st.err || !st.win) return '?';
  const names = (st.buttons || []).map((b) => b.n).filter(Boolean);
  if (names.some((n) => RUNNING_NAMES.includes(n))) return 'running';
  if (names.some((n) => IDLE_NAMES.includes(n))) return 'idle';
  return '?';
}

// ---------- 动作 ----------
function launchEmulator() {
  log(`  E1 设备 ${DEVICE} 不在线 ⇒ MuMuManager control -v ${VM_INDEX} launch`);
  const r = ps(`& '${MGR}' control -v ${VM_INDEX} launch | Out-String`);
  log(`     launch 返回: ${r.replace(/\s+/g, ' ').slice(0, 160)}`);
  const t0 = Date.now();
  while ((Date.now() - t0) / 1000 < BOOT_WAIT_SEC) {
    sleepSync(10000);
    if (deviceOnline()) {
      log(`     设备上线（等了 ${Math.round((Date.now() - t0) / 1000)}s）`);
      return true;
    }
  }
  log(`     等了 ${BOOT_WAIT_SEC}s 仍不见设备（Android 可能还在起；下一轮再看）`);
  return false;
}
function launchMaa() {
  log('  E2 MAA.exe 不在 ⇒ Start-Process 拉起（不动任何已有进程）');
  ps(`Start-Process -FilePath '${MAA_EXE}' -WorkingDirectory '${MAA_DIR}'`);
  const t0 = Date.now();
  while ((Date.now() - t0) / 1000 < MAA_WAIT_SEC) {
    sleepSync(5000);
    const inst = maaInstances();
    if (inst && inst.some(isOurs)) {
      log(`     MAA 进程已起（等了 ${Math.round((Date.now() - t0) / 1000)}s）`);
      sleepSync(12000);
      return true;
    }
  }
  log('     MAA 拉起后没等到进程（看 gui.log）');
  return false;
}
/** L2 用：只关游戏客户端（MAA 的 StartUp 会重新拉起它），不动 MAA 进程 */
function forceStopGame() {
  const r = sh(ADB, ['-s', DEVICE, 'shell', 'am', 'force-stop', GAME_PKG], 30000);
  log(`  L2 am force-stop ${GAME_PKG} ⇒ ${r.replace(/\s+/g, ' ').slice(0, 80) || '(no output)'}`);
  return !r.startsWith('__ERR__');
}
/** L3 用：重启模拟器实例（最后手段）。
 * ⚠️ 2026-09-24 15:21 **受控演练踩出来的坑**（都记在 docs/ROGUE_WATCH.md §5.22）：
 *   ① 原来 shutdown 后只 sleep 12s 就 launch ⇒ VM 还在关机中，返回 `{"errcode":-502,"errmsg":"mainnx request failed"}`；
 *   ② 它**照样会起来**，但 adb 端口会挪到 **5556**（5554 被上一轮残留占着）—— 而 #0 的 MAA 配置写死
 *      `emulator-5554` ⇒ 变成"机器起来了、MAA 接不上"（设备=OFFLINE、gui 弹"请检查连接设置"），白停 8 分钟。
 *   ⇒ 三步修法：等 VM **真关掉** → `adb kill-server`（放掉残留的端口占用，实测这一步就能让 5554 回来）
 *      → launch 失败**重试**；起来后若还看不到 `emulator-5554` 就把真实设备列表打进日志（交人看）。 */
function restartEmulator() {
  log(`  L3 重启模拟器实例 ${VM_INDEX}（shutdown → 等它真关掉 → kill-server → launch）`);
  const down = ps(`& '${MGR}' control -v ${VM_INDEX} shutdown | Out-String`, 60000);
  log(`     shutdown: ${down.replace(/\s+/g, ' ').slice(0, 120)}`);
  for (let i = 0; i < 15; i++) {
    const info = ps(`& '${MGR}' info -v ${VM_INDEX} | Out-String`, 30000);
    if (/"is_process_started"\s*:\s*false/.test(info)) { log(`     VM 已关机（等了约 ${i * 4}s）`); break; }
    sleepSync(4000);
  }
  sh(ADB, ['kill-server'], 20000);
  let launched = false;
  for (let i = 1; i <= 3 && !launched; i++) {
    const up = ps(`& '${MGR}' control -v ${VM_INDEX} launch | Out-String`, 60000);
    launched = /"errcode"\s*:\s*0/.test(up);
    log(`     launch（第 ${i} 次）: ${up.replace(/\s+/g, ' ').slice(0, 140)}`);
    if (!launched) sleepSync(8000);
  }
  const t0 = Date.now();
  while ((Date.now() - t0) / 1000 < BOOT_WAIT_SEC) {
    sleepSync(10000);
    if (deviceOnline()) { log(`     设备回来了（${Math.round((Date.now() - t0) / 1000)}s）`); return true; }
  }
  const dl = sh(ADB, ['devices'], 20000).replace(/\s+/g, ' ');
  log(`     ⚠️ 等不到 ${DEVICE}；当前 adb devices: ${dl.slice(0, 180)}`);
  log('     ⇒ 端口可能又被挪走了（MAA 配置写死 emulator-5554）—— 需要人看一眼');
  return false;
}
function startChain(pid, why) {
  const r = clickMain(pid, 'start');
  log(`  ${why} 点「开始」⇒ ${r}`);
  sleepSync(20000);
  const kind = buttonKind(uiaState(pid));
  log(`     20s 后按钮=${kind}`);
  return /invoked/.test(r);
}
/** R1 用：MAA 还在跑但零进展 ⇒ 先停再开（等价于人手动停一下） */
function restartChainSoft(pid) {
  const r = clickMain(pid, 'stop');
  log(`  R1 零进展 ⇒ 点「停止」⇒ ${r}`);
  sleepSync(12000);
  return startChain(pid, 'R1 重新');
}

// ---------- 受控演练（--drill l1|l2） ----------
/**
 * 为什么不只是"等真事故发生"：L2（关游戏客户端再重拉）此前只在真事故里**手工**走过一次，
 * 代码路径没有自证过。演练＝主动造出"客户端不在"的局面，让看守的**真动作序列**跑一遍，
 * 并把前后现场图 + 判定结果写进 runtime/rogue0_drills.jsonl 当证据。
 * 前提：队列必须是水月肉鸽（否则一律不动手）。代价：约 1 分钟不刷（肉鸽不消耗理智）。
 */
function runDrill(kind) {
  const tag = `${stamp().replace(/[: ]/g, '')}_0_drill-${kind}`;
  const inst = maaInstances();
  const mine = inst && inst.find(isOurs);
  if (!mine) { log(`[drill ${kind}] 找不到 #0 的 MAA 进程 ⇒ 中止`); return; }
  const st = uiaState(mine.pid);
  const q = queueVerdict(st);
  if (q.kind !== 'roguelike') {
    log(`[drill ${kind}] 队列不是水月肉鸽（勾选: ${q.on.join('/') || '无'}）⇒ 中止，不动手`);
    return;
  }
  log(`[drill ${kind}] 开始受控演练：pid=${mine.pid} 按钮=${buttonKind(st)} asst静默=${asstSilentSec()}s`);
  const before = screencap();
  if (before) log(`  演练前现场图: ${saveCase(`${tag}_before`, before)}`);
  const t0 = Date.now();
  // ⚠️ 演练必须**照着看守真实的动作序列**走：L2 只会在 MAA 空闲（按钮=Link Start!）时才触发，
  // 所以先把链停掉，再关客户端、再点开始 —— 否则练的是"另一种顺序"，自证不了。
  if (buttonKind(st) === 'running') {
    clickMain(mine.pid, 'stop');
    sleepSync(12000);
    log('  （先把链停掉，模拟看守遇到该级动作时的空闲态）');
  }
  if (kind === 'l3') {
    // L3＝最重的一级：整台模拟器重启（E1 的同类机制），然后重新点开始让 StartUp 拉起游戏
    const okDev = restartEmulator();
    log(`     模拟器回来了=${okDev}`);
    sleepSync(15000);
    startChain(mine.pid, '[drill L3]');
  } else if (kind === 'l2') {
    forceStopGame();
    sleepSync(6000);
    startChain(mine.pid, '[drill L2]');
  } else {
    startChain(mine.pid, '[drill L1]');
  }
  sleepSync(30000);
  // ⚠️ 判"回来了没有"必须看**客户端进程 + 前台**，不能只看 MAA 的按钮：
  // 第一次演练就栽在这（按钮=running、asst.log 在写，但屏上是 Android 桌面 ⇒ 假阳性）
  let gs = gameState();
  const tw = Date.now();
  while (!(gs && gs.alive) && (Date.now() - tw) / 1000 < 150) {
    sleepSync(10000);
    gs = gameState();
  }
  const after = screencap();
  if (after) log(`  演练后现场图: ${saveCase(`${tag}_after`, after)}`);
  const kind2 = buttonKind(uiaState(mine.pid));
  const silent = asstSilentSec();
  const prog = asstProgress();
  const ch = lastChain();
  const alive = !!(gs && gs.alive);
  const fgOk = !gs || !gs.fg || gs.fg === GAME_PKG;
  const ok = kind2 === 'running' && silent >= 0 && silent < 60 && alive && fgOk;
  log(`[drill ${kind}] 结果：按钮=${kind2} 游戏=${gs ? (gs.alive ? 'alive' : 'GONE') : 'n/a'}`
    + ` 前台=${gs ? (gs.fg || '?') : 'n/a'} asst静默=${silent}s 任务=${prog.task || 'n/a'} 上条链=${ch.outcome}`
    + ` 用时=${Math.round((Date.now() - t0) / 1000)}s ⇒ ${ok ? '✅ 已回到在跑（动作序列有效）' : '⚠️ 没回到在跑，需要人看'}`);
  try {
    fs.appendFileSync(path.join(ROOT, 'runtime', 'rogue0_drills.jsonl'),
      JSON.stringify({ ts: now().toISOString(), drill: kind, ok, button: kind2, game: gs ? gs.alive : null,
        fg: gs ? gs.fg : null, asstSilentSec: silent, task: prog.task, chain: ch.outcome,
        before: `${tag}_before`, after: `${tag}_after` }) + '\n');
  } catch { /* ignore */ }
}

// ---------- 状态文件 ----------
function loadState() {
  try {
    const s = JSON.parse(fs.readFileSync(STATE, 'utf8'));
    s.actionTimes = Array.isArray(s.actionTimes) ? s.actionTimes : [];
    s.idleStreak = Number(s.idleStreak) || 0;
    return s;
  } catch {
    return { idleStreak: 0, actionTimes: [], taskState: null };
  }
}
const saveState = (s) => { try { fs.writeFileSync(STATE, JSON.stringify(s, null, 2)); } catch { /* ignore */ } };
function budgetOk(s) {
  const cut = now().getTime() - 3600 * 1000;
  s.actionTimes = s.actionTimes.filter((t) => new Date(t).getTime() > cut);
  if (s.cooldownUntil && now() < new Date(s.cooldownUntil)) return false;
  if (s.actionTimes.length >= MAX_PER_HOUR) return false;
  return minSince(s.lastActionAt) * 60 >= COOLDOWN_SEC;
}
const noteAction = (s, what) => {
  s.actionTimes.push(now().toISOString());
  s.lastAction = what;
  s.lastActionAt = now().toISOString();
  s.idleStreak = 0;
};

// ---------- 锁（run_hidden.vbs 是"起了就不管"，得自己防重入） ----------
function lockBusy() {
  try {
    const st = fs.statSync(LOCK);
    if ((Date.now() - st.mtimeMs) / 1000 < 900) return true;
  } catch { /* 无锁 */ }
  return false;
}
const takeLock = () => { try { fs.writeFileSync(LOCK, `${process.pid} ${stamp()}`); } catch { /* ignore */ } };
const dropLock = () => { try { fs.unlinkSync(LOCK); } catch { /* ignore */ } };

// ---------- 主流程 ----------
if (lockBusy()) { console.log('[rogue0_watch] 已有实例在跑，跳过'); process.exit(0); }
takeLock();
try {
  if (DRILL && DRILL !== true) {
    runDrill(String(DRILL));
    saveState(loadState());
    dropLock();
    process.exit(0);
  }
  const s = loadState();
  if (FREEZE_DRILL) log('[freeze-drill] 受控演练：本轮屏幕指纹被**固定**（模拟画面冻结），验的是 §5.21 的静默冻结阶梯');
  const inst = maaInstances();
  if (inst === null) {
    log('!! 取不到 MAA 进程列表（fail-closed，本轮只记一行）');
    dropLock();
    process.exit(0);
  }
  const mine = inst.find(isOurs);
  let dev = deviceOnline();
  const st = mine ? uiaState(mine.pid) : null;
  const q = mine ? queueVerdict(st) : { kind: 'unknown', on: [] };
  const asst = asstSilentSec();
  const prog = mine ? asstProgress() : null;
  const ch = mine ? lastChain() : null;

  // 🧊 每轮屏幕指纹（§5.21）：连着 3 个样本（每 2 分钟一轮）同图 ⇒ 上膛，走冻结阶梯。
  // 为什么不复用"链报错才拍两张图"那套：静默冻结的特征恰恰是**链不报错、按钮还是 running**。
  const fpBefore = { streak: s.sameScreenStreak || 0, frozen: s.frozenShots || 0 };
  const fp = updateScreen(s, dev ? screenFingerprint() : null, Date.now());
  if ((fp.frozenShots !== fpBefore.frozen) || (fp.streak > fpBefore.streak && fp.streak >= 2)) {
    log(`  🧊 画面连着 ${fp.streak} 个样本与上一轮**同图**（自 ${fp.since ? String(fp.since).slice(11, 16) : '?'}）⇒ 冻结计数 ${fp.frozenShots}`
      + `${fp.frozenShots ? '（按冻结阶梯动手）' : '（还没到上膛线 3 个样本）'}`);
  }

  const btn = mine ? buttonKind(st) : 'gone';
  const running = btn === 'running' || (btn === '?' && asst >= 0 && asst <= 60);
  const idle = !mine || btn === 'idle';
  const gs = dev ? gameState() : null;
  const game = gs ? gs.alive : null;
  const fgOk = !gs || !gs.fg || gs.fg === GAME_PKG;      // 前台不在游戏里（比如被切到桌面）= 画面层故障
  if (running && game === false) s.gameGoneStreak = (s.gameGoneStreak || 0) + 1;
  else s.gameGoneStreak = 0;
  if (running && game === true && !fgOk) s.notFgStreak = (s.notFgStreak || 0) + 1;
  else s.notFgStreak = 0;
  const head = `MAA=${mine ? `pid ${mine.pid}` : '无'} 按钮=${btn} 队列=${q.kind}`
    + ` 设备=${dev === null ? '查询失败' : (dev ? 'online' : 'OFFLINE')}`
    + ` 游戏=${game === null ? 'n/a' : (game ? 'alive' : 'GONE')}`
    + (gs && gs.fg && !fgOk ? ` 前台=${gs.fg}(非游戏!)` : '')
    + ` asst静默=${asst < 0 ? 'n/a' : asst + 's'}`
    + (prog && prog.task ? ` 任务=${prog.task}(重试${prog.retry}/${prog.retryTimes}${prog.retryExhausted ? ',耗尽' : ''})` : '')
    + (s.sameScreenStreak >= 2 ? ` 🧊画面同图=${s.sameScreenStreak}轮(冻结计数${s.frozenShots})` : '')
    + (ch ? ` 上条链=${ch.outcome}${ch.errors.length ? '(' + ch.errors.join('/') + ')' : ''}` : '');

  if (STATUS_ONLY) { console.log(head); dropLock(); process.exit(0); }

  // ---- 零进展跟踪（同一 cur_task 卡多久）----
  const taskKey = prog && prog.task ? `${prog.task}` : '';
  if (taskKey && s.taskState && s.taskState.task === taskKey) {
    // 同一任务：保留 since
  } else if (taskKey) {
    s.taskState = { task: taskKey, since: now().toISOString() };
  }
  const noProgressMin = s.taskState && s.taskState.task === taskKey ? minSince(s.taskState.since) : 0;

  s.idleStreak = idle ? s.idleStreak + 1 : 0;

  let verdict = 'OK';
  const actions = [];

  if (!mine) {
    // ---- E2：进程没了（被误杀 / 崩了）----
    if (APPLY && budgetOk(s)) {
      const ok = launchMaa();
      const after = maaInstances();
      const alive = after && after.some(isOurs);
      let started = false;
      if (alive) {
        const pid2 = (after.find(isOurs) || {}).pid;
        if (queueVerdict(uiaState(pid2)).kind === 'roguelike') {
          started = startChain(pid2, 'E2 拉起后');
          actions.push('E2+start');
        } else {
          log(`  !! 队列判定=${queueVerdict(uiaState(pid2)).kind} ⇒ 不点开始（等下一轮）`);
          actions.push('E2');
        }
      } else { actions.push('E2'); }
      noteAction(s, actions.join('+'));
      verdict = ok ? (started ? 'E2 已拉回并开跑' : 'E2 已拉进程，待下一轮确认') : 'E2 拉起失败';
    } else {
      verdict = APPLY ? 'E2 需要动作但配额/冷却不允许' : 'DRY-RUN 会执行 E2';
    }
  } else if (!dev) {
    // ---- E1：设备掉线 ----
    if (APPLY && budgetOk(s) && !running) {
      const ok = launchEmulator();
      dev = deviceOnline();
      noteAction(s, 'E1-launch-emulator');
      actions.push('E1');
      verdict = ok ? 'E1 设备已回（下一轮看 MAA）' : 'E1 设备仍未上线';
    } else {
      verdict = APPLY
        ? (running ? '设备掉线但 MAA 仍在跑（MAA 自己会报错，等它停）' : 'E1 需要动作但配额/冷却不允许')
        : 'DRY-RUN 会执行 E1';
    }
  } else if (idle && q.kind !== 'roguelike') {
    // ---- 队列被别人改了 ⇒ 不抢，只喊 ----
    verdict = `队列不是水月肉鸽（勾选: ${q.on.join('/') || '无'}）⇒ 不动手`;
  } else if (idle) {
    // ---- 空闲 + 队列就是水月肉鸽：按证据决定用哪一级 ----
    if (s.idleStreak < 2) {
      verdict = `空闲第 ${s.idleStreak} 轮（连续 2 轮才动手）`;
    } else if (!(APPLY && budgetOk(s))) {
      verdict = APPLY ? '需要动作但配额/冷却不允许（封顶中）' : 'DRY-RUN：会按阶梯动手';
    } else {
      // 证据：上一条链什么时候开始、我们上次在哪一级动过手 ⇒ 上一级是不是白干
      const chainStart = ch && ch.startAt ? new Date(ch.startAt.replace(' ', 'T')) : null;
      const after = (iso) => (iso && chainStart ? chainStart > new Date(iso) : false);
      const failedAgain = ch && ch.outcome === 'error';
      // 画面冻结（只在要升 L2/L3、或链刚报错时才拍，省开销）
      let frozen = null;
      if (failedAgain) {
        const a = screencap();
        if (a) { sleepSync(SHOT_GAP_SEC * 1000); const b = screencap(); if (b) frozen = a.md5 === b.md5; }
      }
      const retryExhausted = !!(prog && prog.retryExhausted);
      const l1Failed = after(s.lastL1At);
      const l2Failed = after(s.lastL2At);
      const l3Failed = after(s.lastL3At);
      const tag = `${stamp().replace(/[: ]/g, '')}_0`;

      if (failedAgain && l3Failed && minSince(s.lastL3At) < 40) {
        s.cooldownUntil = new Date(Date.now() + 30 * 60 * 1000).toISOString();
        verdict = `L1/L2/L3 都试过还是报错（${ch.errors.join('/')}）⇒ 30 分钟不再动手，需要人看`;
        log('  !! 三级阶梯全灭：留一张现场图 + 停止动手 30 分钟（别再烧机器）');
        if (st && st.win) { const shot = screencap(); if (shot) log(`     现场图: ${saveCase(`${tag}_giveup`, shot)}`); }
        noteAction(s, 'giveup');
      } else if (failedAgain && l2Failed && minSince(s.lastL2At) < 40) {
        // L2 之后那条链还是错 ⇒ L3 重启模拟器
        const shot = screencap(); if (shot) log(`     现场图: ${saveCase(`${tag}_l3`, shot)}`);
        restartEmulator();
        s.lastL3At = now().toISOString();
        actions.push('L3-restart-emulator');
        const pid3 = (maaInstances() || []).find(isOurs);
        if (pid3) { startChain(pid3.pid, 'L3 重启模拟器后'); actions.push('start'); }
        noteAction(s, actions.join('+'));
        verdict = `L3 重启模拟器后重新点开始（上条链错: ${ch.errors.join('/')}）`;
      } else if (failedAgain && (l1Failed || frozen === true || retryExhausted)) {
        // L1 之后那条链还是错 / 画面冻结 / 入口重试耗尽 ⇒ 关游戏客户端让它重拉
        const shot = screencap();
        if (shot) log(`     现场图: ${saveCase(`${tag}_l2`, shot)}`);
        if (frozen === true) log('     证据: 两次截图 md5 相同（画面冻结）');
        if (retryExhausted) log(`     证据: ${prog.task} 重试 ${prog.retry}/${prog.retryTimes} 耗尽`);
        forceStopGame();
        sleepSync(6000);
        s.lastL2At = now().toISOString();
        actions.push('L2-kill-game');
        startChain(mine.pid, 'L2 关游戏后');
        actions.push('start');
        noteAction(s, actions.join('+'));
        verdict = `L2 关掉游戏客户端 + 重新点开始（上条链错: ${ch.errors.join('/')}${frozen === true ? '，画面冻结' : ''}）`;
      } else {
        // 首级：只是停着（或上一条链是干净结束/被停）
        const shot = screencap();
        if (shot) log(`     现场图: ${saveCase(`${tag}_l1`, shot)}`);
        s.lastL1At = now().toISOString();
        actions.push('L1-start');
        startChain(mine.pid, 'L1 空闲');
        noteAction(s, actions.join('+'));
        verdict = `L1 点开始（上条链=${ch ? ch.outcome : 'n/a'}${ch && ch.errors.length ? ':' + ch.errors.join('/') : ''}）`;
      }
    }
  } else if (running && game === true && !fgOk) {
    // ---- R3：客户端在、但前台不是游戏（被切到桌面/别的应用 ⇒ MAA 截到的是别的画面）----
    if (s.notFgStreak < 2) {
      verdict = `前台非游戏第 ${s.notFgStreak} 轮（连续 2 轮才动手，怕误判瞬时的系统弹层）`;
    } else if (APPLY && budgetOk(s)) {
      const shot = screencap();
      if (shot) log(`     现场图: ${saveCase(`${stamp().replace(/[: ]/g, '')}_0_r3`, shot)}`);
      log(`     证据: MAA 按钮=${btn}、asst.log 在写，但前台=${gs.fg}`);
      const ok = bringGameToFront();
      noteAction(s, 'R3-focus-game');
      actions.push('R3-focus-game');
      verdict = ok ? 'R3 已把游戏拉回前台' : 'R3 拉回前台没成功（前台仍是别的应用）';
    } else {
      verdict = '前台非游戏，但配额/冷却不允许动手';
    }
  } else if (running && game === false) {
    // ---- R2：MAA 在跑，但游戏客户端根本不在了（客户端被关/崩，MAA 空转）----
    if (s.gameGoneStreak < 2) {
      verdict = `客户端不在第 ${s.gameGoneStreak} 轮（连续 2 轮才动手，怕打断正在进行的重拉）`;
    } else if (APPLY && budgetOk(s)) {
      const shot = screencap();
      if (shot) log(`     现场图: ${saveCase(`${stamp().replace(/[: ]/g, '')}_0_r2`, shot)}`);
      log(`     证据: MAA 按钮=${btn}、asst.log 在写，但 pidof ${GAME_PKG} 为空`);
      restartChainSoft(mine.pid);
      s.lastL1At = now().toISOString();
      noteAction(s, 'R2-client-gone-stop-start');
      actions.push('R2-stop-start');
      verdict = 'R2 客户端不在 ⇒ 停掉空转的链再重开（StartUp 会把客户端起回来）';
    } else {
      verdict = '客户端不在，但配额/冷却不允许动手';
    }
  } else if (running && fgOk && game !== false && s.frozenShots >= 1) {
    // ---- 🧊 静默冻结（§5.21）：MAA 说在跑、asst.log 也在写，但**画面连着几轮一像素没动** ----
    // 画面冻住时 MAA 自己也认不出东西（这正是"入口反复失败"的真因），光重拉 MAA 没用。
    const tag = `${stamp().replace(/[: ]/g, '')}_0`;
    if (!(APPLY && budgetOk(s))) {
      verdict = APPLY
        ? `画面冻结第 ${s.frozenShots} 级（${s.sameScreenStreak} 轮同图）但配额/冷却不允许动手`
        : `DRY-RUN：画面冻结第 ${s.frozenShots} 级（${s.sameScreenStreak} 轮同图）⇒ 会执行冻结阶梯`;
    } else if (s.frozenShots >= 4) {
      // 三级都试过还是冻着 ⇒ 别再烧机器
      s.cooldownUntil = new Date(Date.now() + 30 * 60 * 1000).toISOString();
      const shot = screencap(); if (shot) log(`     现场图: ${saveCase(`${tag}_frozen-giveup`, shot)}`);
      noteAction(s, 'frozen-giveup');
      actions.push('frozen-giveup');
      verdict = `画面连着 ${s.sameScreenStreak} 个样本没动（关客户端 + 重启模拟器都试过）⇒ 停手 30 分钟，需要人看`;
    } else if (s.frozenShots >= 2) {
      const shot = screencap(); if (shot) log(`     现场图: ${saveCase(`${tag}_frozen-l3`, shot)}`);
      log(`     证据: 画面 ${s.sameScreenStreak} 个样本同图（自 ${String(s.screen && s.screen.since).slice(11, 16)}），按钮=${btn}、asst 静默 ${asst}s`);
      const okDev2 = restartEmulator();
      s.lastL3At = now().toISOString();
      actions.push('frozen-L3-restart-emulator');
      const pid4 = (maaInstances() || []).find(isOurs);
      if (pid4) { startChain(pid4.pid, '冻结 L3 重启模拟器后'); actions.push('start'); }
      noteAction(s, actions.join('+'));
      verdict = `画面冻结第 ${s.frozenShots} 级 ⇒ 重启模拟器（设备回来=${okDev2}）`;
    } else {
      const shot = screencap(); if (shot) log(`     现场图: ${saveCase(`${tag}_frozen-l2`, shot)}`);
      log(`     证据: 画面 ${s.sameScreenStreak} 个样本同图（自 ${String(s.screen && s.screen.since).slice(11, 16)}），按钮=${btn}、asst 静默 ${asst}s`);
      forceStopGame();
      sleepSync(6000);
      s.lastL2At = now().toISOString();
      actions.push('frozen-L2-kill-game');
      startChain(mine.pid, '冻结 L2 关客户端后');
      actions.push('start');
      noteAction(s, actions.join('+'));
      verdict = `画面冻结第 ${s.frozenShots} 级 ⇒ 关游戏客户端重拉（冻住的画面 MAA 认不出来，光重拉 MAA 没用）`;
    }
  } else if (running && noProgressMin >= NO_PROGRESS_SEC / 60
             && (prog.retryExhausted || prog.ocrOnly)) {
    // ---- R1：在跑，但同一 cur_task 卡了很久 + 重试耗尽 / 只剩 screencap 噪声 ----
    if (APPLY && budgetOk(s)) {
      const shot = screencap();
      if (shot) log(`     现场图: ${saveCase(`${stamp().replace(/[: ]/g, '')}_0_r1`, shot)}`);
      log(`     证据: ${prog.task} 卡 ${Math.round(noProgressMin)} 分钟，重试 ${prog.retry}/${prog.retryTimes}`
        + `，screencap-only=${prog.ocrOnly}`);
      restartChainSoft(mine.pid);
      s.lastL1At = now().toISOString();
      noteAction(s, 'R1-stop-start');
      actions.push('R1-stop-start');
      verdict = `R1 零进展 ${Math.round(noProgressMin)} 分钟 ⇒ 停一下重开`;
    } else {
      verdict = `零进展 ${Math.round(noProgressMin)} 分钟（配额/冷却不允许动手）`;
    }
  } else if (running && asst > 900) {
    verdict = `在跑但 asst.log 静默 ${asst}s（MAA 25 分钟停滞超时会自己收尾，先观察）`;
  }

  log(`${head} ⇒ ${verdict}` + (actions.length ? ` [动作 ${actions.join(',')}]` : ''));
  if (running && asst >= 0 && asst <= 900 && prog && prog.task) {
    log(`    进展: ${prog.task} 重试 ${prog.retry}/${prog.retryTimes}｜已卡 ${Math.round(noProgressMin)} 分钟`);
  }
  saveState(s);
} finally {
  dropLock();
}
