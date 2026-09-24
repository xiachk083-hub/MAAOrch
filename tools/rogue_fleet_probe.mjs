#!/usr/bin/env node
/**
 * rogue_fleet_probe.mjs —— 肉鸽机队"盯号"采样器（本机跑，SSH 只读取证）
 * ──────────────────────────────────────────────────────────────────────────────
 * 用户口径（2026-09-23）：「因为肉鸽卡住的问题是多种多样的…**你盯五六个号**之后，我才认可
 * 你做出来的这个看门狗」⇒ 这台采样器就是"盯"的手段：每 60 秒把 5 台机队
 * （l-1/l-2/l-4/l-5/l-7 @ MRFZ-0000）＋本机 #0 的状态**连内容一起**抓下来，
 * 写成 JSONL（runtime/rogue_fleet_probe.jsonl），出错形态自动标注、存证图片。
 *
 * 为什么不能只看 mtime（2026-09-23 取证结论）：
 *   MAA 可以一边"每秒都在写日志"一边**零进展**卡 4.8 小时（l-7 卡在 MiniGame@SPA@LoadingText，
 *   `ProcessTask::run | leave, 17389530 ms`）。⇒ 必须读 cur_task / cur_retry / retry_times /
 *   gui.log 的"任务出错/任务已全部完成"横幅，并且分清 `TaskChainError` 与 `TaskChainCompleted`
 *   （"任务已全部完成！"永远紧跟"任务出错"，**完成 ≠ 成功**；l-2 的"假完成"就是这么骗过旧看守的）。
 *
 * 只读：采样本身不改任何东西、不动任何进程（-NoProfile + 只读命令）。动作留给看守。
 *
 * 用法：
 *   node tools/rogue_fleet_probe.mjs                 # 采一轮（默认），打印一行摘要
 *   node tools/rogue_fleet_probe.mjs --loop 60       # 每 60s 采一轮，直到 Ctrl-C
 *   node tools/rogue_fleet_probe.mjs --json          # 只把这一轮的 JSON 打出来
 *   node tools/rogue_fleet_probe.mjs --shot l-1      # 单独给某台拍张现场图（存 runtime/fleet_cases）
 */
import { execFileSync } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const HOST = 'Administrator@100.79.173.69';
const KEY = path.join(process.env.USERPROFILE || '', '.ssh', 'maaorch_target');
const FLEET = {
  'l-1': '127.0.0.1:17184',
  'l-2': '127.0.0.1:17280',
  'l-4': '127.0.0.1:17376',
  'l-5': '127.0.0.1:16452',
  'l-7': '127.0.0.1:17028',
};
const MACHINES = Object.keys(FLEET);
const JSONL = path.join(ROOT, 'runtime', 'rogue_fleet_probe.jsonl');
const CASES = path.join(ROOT, 'runtime', 'fleet_cases');

// 本机 #0
const MAA0_DIR = 'D:\\Xiach\\M-A\\MAA\\MAA-v6.12.2-win-x64';
const ADB0 = 'D:\\Xiach\\MuMuPlayer\\nx_main\\adb.exe';
const DEV0 = 'emulator-5554';

// ---------- 参数 ----------
const argv = process.argv.slice(2);
const flag = (k, d) => {
  const i = argv.indexOf(k);
  if (i < 0) return d;
  const v = argv[i + 1];
  return v && !v.startsWith('--') ? v : true;
};
const LOOP_SEC = Number(flag('--loop', 0));
const JSON_ONLY = argv.includes('--json');
const SHOT_ONLY = flag('--shot', null);


// ---------- 工具 ----------
function ssh(remoteCmd, timeout = 90000) {
  try {
    return execFileSync('ssh',
      ['-i', KEY, '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=no',
        '-o', 'ServerAliveInterval=15', '-o', 'BatchMode=yes', HOST, remoteCmd],
      { encoding: 'utf8', timeout, maxBuffer: 16 * 1024 * 1024 }).trim();
  } catch (e) {
    const out = String(e.stdout || '').trim();
    if (out) return out;
    return `__ERR__ ${String(e.stderr || e.message).slice(0, 200)}`;
  }
}
const b64ps = (s) => `powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand ${Buffer.from(s, 'utf16le').toString('base64')}`;
const nowIso = () => new Date().toISOString();
const pad = (n) => String(n).padStart(2, '0');
const hm = (d = new Date()) => `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;

// ---------- 采样 ----------
/** 主机上的只读采样脚本；不存在/坏了就现部署一次（见 tools/deploy_host_file.mjs 的来龙去脉） */
const REMOTE_PROBE = 'E:\\maa-alas\\fz-maa\\_mporch_probe.ps1';
const LOCAL_PROBE = path.join(ROOT, 'tools', 'rogue_host_probe.ps1');
const DEPLOYER = path.join(ROOT, 'tools', 'deploy_host_file.mjs');
function sampleRemote() {
  const run = () => ssh(`powershell -NoProfile -ExecutionPolicy Bypass -File "${REMOTE_PROBE}"`, 150000);
  let raw = run();
  if (!/[A-Za-z0-9+/=]{40,}/.test(raw)) {
    console.log('  （主机采样脚本缺失/异常 ⇒ 部署一次再试）');
    try {
      execFileSync(process.execPath, [DEPLOYER, '--local', LOCAL_PROBE, '--remote', REMOTE_PROBE],
        { encoding: 'utf8', timeout: 300000 });
    } catch (e) { /* 部署失败就按原样报错 */ }
    raw = run();
  }
  if (raw.startsWith('__ERR__')) return { err: raw };
  const m = raw.match(/[A-Za-z0-9+/=]{40,}/);
  if (!m) return { err: `no-base64:${raw.slice(0, 120)}` };
  try {
    return JSON.parse(Buffer.from(m[0], 'base64').toString('utf8'));
  } catch (e) { return { err: `decode:${e.message}` }; }
}
function sampleLocal0() {
  const rd = (p, bytes) => {
    try {
      const size = fs.statSync(p).size;
      const fd = fs.openSync(p, 'r');
      const start = Math.max(0, size - bytes);
      const buf = Buffer.alloc(size - start);
      fs.readSync(fd, buf, 0, buf.length, start);
      fs.closeSync(fd);
      return buf.toString('utf8');
    } catch { return ''; }
  };
  const ap = path.join(MAA0_DIR, 'debug', 'asst.log');
  const gp = path.join(MAA0_DIR, 'debug', 'gui.log');
  let age = -1; let sizeKB = -1;
  try { const i = fs.statSync(ap); age = Math.round((Date.now() - i.mtimeMs) / 1000); sizeKB = Math.round(i.size / 1024); } catch { /* ignore */ }
  const tasks = [];
  let proc = 0;
  try {
    const out = execFileSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command',
      "$ProgressPreference='SilentlyContinue'; Get-CimInstance Win32_Process -Filter \"Name='MAA.exe'\" | Select-Object -ExpandProperty ExecutablePath"],
      { encoding: 'utf8', timeout: 30000 });
    proc = out.split(/\r?\n/).filter((l) => l.trim().toLowerCase() === path.join(MAA0_DIR, 'MAA.exe').toLowerCase()).length;
  } catch { proc = -1; }
  const at = rd(ap, 8000).split(/\r?\n/).filter((l) => /ProcessTask|TaskChainError|TaskChainCompleted|ExceededLimit|SubTaskError/.test(l));
  if (at.length) tasks.push(...at.slice(-3));
  const gl = rd(gp, 6000).split(/\r?\n/)
    .filter((l) => /开始任务|完成任务|任务出错|任务已全部完成|本轮任务出现以下错误|已停止|正在运行中/.test(l)).slice(-8);
  // 游戏客户端活着吗？—— MAA"在跑"不等于客户端在（2026-09-23 实测：客户端被杀后 MAA 一路空转）
  let game = null;
  if (proc > 0) {
    try {
      const g = execFileSync(ADB0, ['-s', DEV0, 'shell', 'pidof', 'com.YoStarJP.Arknights'], { encoding: 'utf8', timeout: 25000 });
      game = g.trim().length > 0;
    } catch { game = null; }
  }
  // 屏幕指纹（本地 #0，与机队同一套逻辑）：设备侧 `screencap -p | md5sum`，一次 adb 往返
  let screenMd5 = null;
  if (proc > 0) {
    try {
      const h = execFileSync(ADB0, ['-s', DEV0, 'shell', 'screencap -p | md5sum'], { encoding: 'utf8', timeout: 30000 });
      const hm = /^([0-9a-f]{32})\s/m.exec(h);
      if (hm) screenMd5 = hm[1];
    } catch { /* 取不到就当未知 */ }
  }
  return { proc, asstAge: age, asstSizeKB: sizeKB, asstLast: tasks, guiLast: gl, adb: DEV0, game, screenMd5 };
}

// ---------- 判读（内容级） ----------
function analyze(rec) {
  const t = (rec.asstLast || []).join('\n');
  // ⚠️ 取**最后一条** cur_task（原来是 .match 取第一条 ⇒ 复核 F2：尾部 6 行里可能先有旧任务）
  const allTasks = [...t.matchAll(/"cur_task":"([^"]*)"/g)];
  const mTask = allTasks.length ? allTasks[allTasks.length - 1] : null;
  // ⚠️ 2026-09-24 night-watch 复核：原来 `.match()` 取**第一条** `cur_retry` ⇒ 3–6 行尾窗里常取到旧值
  // （l-7 00:02:40Z 那条实际最后一行已是 20/20，却读成 19/20）⇒ "重试耗尽"识别不可靠。取最后一条。
  const allRetry = [...t.matchAll(/"cur_retry":(\d+)/g)];
  const allTimes = [...t.matchAll(/"retry_times":(\d+)/g)];
  const mRetry = allRetry.length ? allRetry[allRetry.length - 1] : null;
  const mTimes = allTimes.length ? allTimes[allTimes.length - 1] : null;
  const task = mTask ? mTask[1] : '';
  const retry = mRetry ? Number(mRetry[1]) : -1;
  const retryTimes = mTimes ? Number(mTimes[1]) : -1;
  const g = (rec.guiLast || []).join('\n');
  const chainError = /任务出错:/.test(g) && !/开始任务:/.test(g.split('任务出错:').pop());
  const lastStart = (rec.guiLast || []).filter((l) => /开始任务:/.test(l)).pop() || '';
  const errLine = (rec.guiLast || []).filter((l) => /任务出错:/.test(l)).pop() || '';
  const startedAfterError = !!lastStart && !!errLine
    && (rec.guiLast.indexOf(lastStart) > rec.guiLast.indexOf(errLine));
  const flags = [];
  if (rec.proc === 0) flags.push('进程不在');
  if (retry >= 0 && retryTimes > 0 && retry >= retryTimes) flags.push('重试耗尽');
  if (/ExceededLimit|exec times exceeded/.test(t)) flags.push('假完成(ExceededLimit)');
  if (/TaskChainError/.test(t)) flags.push('TaskChainError');
  if (retry === 0 && /StartActionReCheck/.test(t)) flags.push('战斗零进展(StartActionReCheck)');
  if (/MiniGame@|taskchain":"Custom/.test(t)) flags.push('自定任务/MiniGame');
  if (errLine && !startedAfterError) flags.push('链已出错停住');
  if (/RemoteControlService endpoint failed/.test(g)) flags.push('19999 报错刷屏');
  if (/30 FPS/.test(g)) flags.push('30FPS 降帧');
  const idleRec = rec.proc === 0 || (errLine && !startedAfterError);
  // 进程不在 + 日志很久没动 ⇒ 只是"停着"，别把几小时前的形态当现场（会误判、会误动手）
  const stale = rec.proc === 0 && (rec.asstAge === undefined || rec.asstAge < 0 || rec.asstAge > 600)
    || (rec.proc !== 0 && rec.asstAge > 600);
  // ⚠️ 必须**产出** retryExhausted：机队看守是把这份记录直接喂给 classify 的，以前没有这个字段
  // ⇒ `rogue_judge.mjs` 里那条"重试耗尽"判据在机队路径上**恒为 undefined = 死枝**（2026-09-24 night-watch 复核）。
  const retryExhausted = retry >= 0 && retryTimes > 0 && retry >= retryTimes;
  return { task, retry, retryTimes, retryExhausted, flags, idle: idleRec, stale, chainError: !!errLine };
}

// ---------- 现场图 ----------
function shotMachine(name) {
  const port = FLEET[name].split(':')[1];
  // ⚠️ 不能用 `adb exec-out screencap -p > file`：PowerShell 的 `>` 是**文本**重定向，
  // 二进制 PNG 会被编码搞坏（2026-09-23 实测：stdout 直接空、只剩 CLIXML 报错）。
  // 走 `adb shell screencap -p /sdcard/... ` + `adb pull`，由 adb 自己写文件，二进制安全。
  const ps = `$ErrorActionPreference='Continue'; $ProgressPreference='SilentlyContinue';`
    + `$a='E:\\MuMu Player 12\\shell\\adb.exe'; $d='127.0.0.1:${port}';`
    + `& $a connect $d 2>&1 | Out-Null;`
    + `& $a -s $d shell screencap -p /sdcard/_rf_${name}.png 2>&1 | Out-Null;`
    + `& $a -s $d pull /sdcard/_rf_${name}.png "$env:TEMP\\_rf_${name}.png" 2>&1 | Out-Null;`
    + `$p="$env:TEMP\\_rf_${name}.png"; if((Test-Path $p) -and ((Get-Item $p).Length -gt 20000)){ [Convert]::ToBase64String([IO.File]::ReadAllBytes($p)) } else { 'TOO_SMALL_OR_MISSING' }`;
  const raw = ssh(b64ps(ps), 150000);
  const m = raw.match(/[A-Za-z0-9+/=]{1000,}/);
  if (!m) return { err: String(raw).replace(/\s+/g, ' ').slice(0, 200) };
  try {
    const buf = Buffer.from(m[0], 'base64');
    fs.mkdirSync(CASES, { recursive: true });
    const file = path.join(CASES, `${new Date().toISOString().replace(/[:.]/g, '')}_${name}.png`);
    fs.writeFileSync(file, buf);
    return { file, md5: crypto.createHash('md5').update(buf).digest('hex'), bytes: buf.length };
  } catch (e) { return { err: e.message }; }
}

// ---------- 主 ----------
if (SHOT_ONLY && SHOT_ONLY !== true) {
  const r = shotMachine(String(SHOT_ONLY));
  console.log(JSON.stringify(r));
  process.exit(0);
}
const round = () => {
  const ts = nowIso();
  const remote = sampleRemote();
  const local = sampleLocal0();
  const rec = { ts, host: remote.err ? null : remote.host, prod: remote.err ? null : remote.prod, machines: {}, local0: null };
  if (remote.err) { rec.err = remote.err; console.log(`[${hm()}] !! 远端采样失败: ${remote.err}`); }
  else {
    for (const n of MACHINES) {
      const m = remote.machines[n] || {};
      rec.machines[n] = Object.assign({}, m, analyze(m));
    }
  }
  rec.local0 = Object.assign({}, local, analyze(local));
  fs.appendFileSync(JSONL, JSON.stringify(rec) + '\n');

  const parts = [];
  if (rec.host) parts.push(`host cpu=${rec.host.cpu}% ramFree=${rec.host.ramFreeGB}G`);
  if (rec.prod) parts.push(`产线 active=${rec.prod.active}/${rec.prod.slots}`);
  for (const n of MACHINES) {
    const a = rec.machines[n];
    if (!a) continue;
    parts.push(`${n}:${a.proc ? (a.idle ? 'STOP' : 'run') : 'GONE'}`
      + `${a.task ? ' ' + a.task.split('@').slice(-1)[0] : ''}`
      + `${a.flags.length ? ' [' + a.flags.join(',') + ']' : ''}`);
  }
  const l0 = rec.local0;
  parts.push(`#0:${l0.proc ? (l0.idle ? 'STOP' : 'run') : 'GONE'} ${l0.task ? l0.task.split('@').slice(-1)[0] : ''}`
    + `${l0.flags.length ? ' [' + l0.flags.join(',') + ']' : ''}`);
  if (JSON_ONLY) console.log(JSON.stringify(rec, null, 2));
  else console.log(`[${hm()}] ${parts.join(' | ')}`);
  return rec;
};

if (LOOP_SEC > 0) {
  console.log(`[rogue_fleet_probe] 每 ${LOOP_SEC}s 采一轮，写 ${JSONL}`);
  for (;;) {
    try { round(); } catch (e) { console.log(`[${hm()}] !! 采样异常: ${e.message}`); }
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, LOOP_SEC * 1000);
  }
} else {
  round();
}
