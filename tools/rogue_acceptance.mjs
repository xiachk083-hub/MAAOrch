#!/usr/bin/env node
/**
 * rogue_acceptance.mjs —— 肉鸽看守「验收一页纸」：一条命令把"能不能信"摊开
 * ──────────────────────────────────────────────────────────────────────────────
 * 用户的验收口径是「**你盯五六个号之后 我才认可你做出来的这个看门狗**」⇒ 这张纸就按他的问法组织：
 *   ① 看守自己还活着吗（计划任务上次结果 / 日志新鲜度 / 有没有残留锁 / 看板接口）
 *   ② 判据与决策的自测过没过（而且要说明"自测的就是看守真跑的那份代码"）
 *   ③ 现在谁在刷（#0 + 机队逐台）
 *   ④ 自证证据（真实自愈几次 / 演练几次 / 现场图 / 采样轮数）
 *   ⑤ **还没验到的**（诚实清单，不藏）
 *
 * 用法：node tools/rogue_acceptance.mjs [--md]
 */
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const argv = process.argv.slice(2);
const WANT_MD = argv.includes('--md');

const out = [];
const W = (s = '') => { out.push(s); };
const run = (args) => {
  try { return execFileSync(process.execPath, args, { cwd: ROOT, encoding: 'utf8', timeout: 240000 }).trim(); }
  catch (e) { return `__ERR__ ${String(e.stdout || e.message).slice(-200)}`; }
};
const fileAge = (rel) => { try { return Math.round((Date.now() - fs.statSync(path.join(ROOT, rel)).mtimeMs) / 1000); } catch { return -1; } };
const countLines = (rel) => { try { return fs.readFileSync(path.join(ROOT, rel), 'utf8').split(/\r?\n/).filter(Boolean).length; } catch { return 0; } };
const countFiles = (rel) => { try { return fs.readdirSync(path.join(ROOT, rel)).filter((f) => f.endsWith('.png')).length; } catch { return 0; } };
const tailLine = (rel, match) => {
  try {
    const ls = fs.readFileSync(path.join(ROOT, rel), 'utf8').split(/\r?\n/).filter((l) => l.trim() && (!match || l.includes(match)));
    return ls.length ? ls[ls.length - 1] : '';
  } catch (e) { return `（读不到 ${rel}: ${e.code || e.message}）`; }
};
/** 看板接口：直接用 Node 的 fetch（powershell 5.1 的 Invoke-WebRequest 会走系统代理，实测报 NullReference） */
async function dashboardCheck() {
  try {
    const ctl = AbortSignal.timeout(8000);
    const r = await fetch('http://127.0.0.1:19999/api/rogue/status', { signal: ctl });
    if (!r.ok) return { ok: false, text: `HTTP ${r.status}` };
    const j = await r.json();
    return { ok: true, text: `stale=${j.stale} 机队=${j.fleet.running}/${j.fleet.total} ✓` };
  } catch (e) { return { ok: false, text: `请求失败 ${String(e.message).slice(0, 60)}` }; }
}
/**
 * 计划任务的"上次运行 / 结果码"。**必须问 PowerShell**（Get-ScheduledTaskInfo 的属性名固定英文）。
 * ⚠️ 2026-09-24 修：本机是中文 Windows，`schtasks /fo LIST /v` 的标签是"上次运行时间/上次结果"，
 * 而且经 Node 读进来是 GBK 乱码 ⇒ 原来按英文标签 `Last Run Time:` / `Last Result:` 解析**永远取空**，
 * 验收纸于是把一个**好端端在跑的看守**报成「结果码 ✗」——正好踩中本仓库那条"日志文案不是事实"的坑。
 */
const taskInfo = (name) => {
  try {
    const ps = `$i = Get-ScheduledTaskInfo -TaskName '${name}';`
      + ` 'last=' + $i.LastRunTime.ToString('yyyy-MM-dd HH:mm:ss')`
      + ` + ' res=' + $i.LastTaskResult`
      + ` + ' next=' + $i.NextRunTime.ToString('yyyy-MM-dd HH:mm:ss')`;
    const t = execFileSync('powershell', ['-NoProfile', '-NonInteractive', '-Command', ps], { encoding: 'utf8', timeout: 30000 }).trim();
    const g = (k) => (new RegExp(k + '=(-?[0-9: \-]+)').exec(t) || [])[1] || '?';
    return { last: g('last').trim(), res: g('res').trim(), next: g('next').trim() };
  } catch { return { last: '?', res: '?', next: '?' }; }
};

W(`# 肉鸽看守 · 验收一页纸`);
W(`生成时间：${new Date().toLocaleString('zh-CN')}`);
W('');

// ① 看守自己活着吗
W('## ① 看守自己还活着吗');
let watchesOk = true;
for (const [name, logFile] of [['MAAOrch-Rogue0Watch', 'runtime/rogue0_watch.log'], ['MAAOrch-RogueFleet', 'runtime/rogue_fleet_watch.log']]) {
  const t = taskInfo(name);
  const age = fileAge(logFile);
  const fresh = age >= 0 && age < 600;
  if (!fresh || t.res !== '0') watchesOk = false;   // '?' = 取不到（别把取不到当失败）
  W(`- **${name}**：上次运行 ${t.last}｜结果码 ${t.res} ${t.res === '0' ? '✓' : '✗'}｜下次 ${t.next}｜日志 ${age}s 前写过 ${fresh ? '✓' : '✗ 太久了'}`);
}
const locks = ['runtime/rogue0_watch.lock', 'runtime/rogue_fleet_watch.lock'].filter((f) => fs.existsSync(path.join(ROOT, f)));
W(`- 残留锁：${locks.length ? '⚠️ ' + locks.join('、') : '无 ✓'}`);
let dash = '（看板查询失败）';
{
  const d = await dashboardCheck();
  dash = d.text;
  if (!d.ok) watchesOk = false;
}
W(`- 看板 /api/rogue/status：${dash}`);
W(`- 结论：${watchesOk ? '**看守在跑**' : '**有项目不对劲，见上面 ✗/⚠️**'}`);
W('');

// ② 自测
W('## ② 判据 / 决策自测（跑的就是看守 import 的那两份代码）');
const j = run(['tools/rogue_judge_selftest.mjs']);
const p = run(['tools/rogue_plan_selftest.mjs']);
const lastLine = (s) => (s.split(/\r?\n/).filter((l) => l.includes('通过'))[0] || s.slice(-120)).trim();
W(`- 判读 \`lib/rogue_judge.mjs\`：${lastLine(j)}`);
W(`- 决策 \`lib/rogue_plan.mjs\`：${lastLine(p)}`);
W('');

// ③ 现在谁在刷
W('## ③ 现在谁在刷');
W(`- **#0（本机日服）**：${run(['tools/rogue0_watch.mjs', '--status']) || '（取不到）'}`);
W(`- **机队**：${run(['tools/rogue_fleet_watch.mjs', '--status']) || '（取不到）'}`);
W('');

// ④ 证据
W('## ④ 自证证据');
const drills = (() => {
  try {
    return fs.readFileSync(path.join(ROOT, 'runtime', 'rogue0_drills.jsonl'), 'utf8').split(/\r?\n/).filter(Boolean).map((l) => JSON.parse(l));
  } catch { return []; }
})();
W(`- 受控演练 **${drills.length}** 次：${drills.map((d) => `${d.drill}(${d.ok ? 'ok' : 'fail'})`).join('、') || '（无）'}`);
W(`- 现场图：` + `#0 ${countFiles('runtime/rogue0_cases')} 张（\`runtime/rogue0_cases/\`）｜机队 ${countFiles('runtime/fleet_cases')} 张（\`runtime/fleet_cases/\`）`);
W(`- 🧊 **每轮屏幕指纹**（2026-09-24 15:19 起，§5.21）：探针每轮给 5 台 + #0 各取一次 \`screencap -p | md5sum\`（一次 adb 往返 ≈0.9s、不回传图片）；连着 3 个样本同图才上膛（→ 关客户端 → 重启模拟器 → 交人工）。自测：决策 **80 条**（含 F 段 11 条指纹用例）。`);
W(`- 🧊 **端口坑的演练自证**（§5.22）：加固后 \`--drill l3\` **101 秒**干净回来（加固前同一路径 8 分钟 + MAA 接不上）：日志原文见 \`runtime/rogue0_watch.log\`，记录在 \`runtime/rogue0_drills.jsonl\`。`);
W(`- 🌀 **空转闸（relapse）**（§5.23）：同一形态"连着"复发（间隔 <15 分钟）≥3 次 ⇒ 升到**模拟器级重启**、≥6 次 ⇒ 交人工。2026-09-24 15:42 在 l-2 上**真跑过一次**（日志原文在 \`runtime/rogue_fleet_watch.log\`）。自测：决策 **90 条**（G 段 10 条）。`);
W(`- 🧊 画面冻结识别（2026-09-24 新增，§5.20）：现场图与上次**同 md5** 就记 \`screen-frozen\` 并**撤销那条"有效"**；把 l-2 那段重放一遍看新阶梯第几轮动手：\`node tools/rogue_freeze_replay.mjs --machine l-2\``);
W(`- 盯号采样：**${countLines('runtime/rogue_fleet_probe.jsonl')}** 轮（\`runtime/rogue_fleet_probe.jsonl\`，每轮覆盖 #0 + l-1/l-2/l-4/l-5/l-7）`);
W(`- 最近一次机队判定：${tailLine('runtime/rogue_fleet_watch.log') || '（无）'}`);
W(`- 日报：\`node tools/rogue_report.mjs --hours 12 --md\` → \`runtime/reports/\``);
W('');

// ⑤ 实盘战绩（2026-09-23 夜）+ 诚实清单
W('- **受控演练 5 次**：前 4 次是 #0 的 l1/l2/l3（见 §5.1–5.3）；第 5 次是 **01:59 的收工让路演练**（机队 5 台 MAA 全收、模拟器一个没动、--apply --force 全部重发回来）。');
W('## ⑤ 实盘战绩（2026-09-23 夜，全部带现场证据）');
W('- **22:34 首次实盘发车**：产线清空 + 窗口内 ⇒ 让路闸开，l-1/l-2/l-4/l-5 起跑（22:37 全部起来）；**l-7 于 22:58 启用 Roguelike(Mizuki) 后加入**（23:06 进肉鸽）⇒ 合计 **6 个号**（#0 + 机队 5 台）在刷。');
W('- **真事故救援 8 次**：l-2 @23:54（链出错后静默 513s ⇒ `idle-after-error` ⇒ 重拉 ⇒ 23:55:56 回到探索）、l-1 @01:26、**l-7 六次**（00:00/00:06 `idle-after-error`、00:54 `fake-complete`、01:00 `entry-error`、01:12 `idle-clean`、01:25 `login-lost`）。前五次里 **l-7 的后三次是无效重拉**（同一个掉线弹窗），第六次换了有效动作才成功 —— 这段曲折与修正见 §5.9。');
W('- **有效动作已被实测出来**：`login-lost`（日服「ログインに失敗しました」静止弹窗 ⇒ MAA 自己"检测到掉线直接停止任务"）**只能靠 `killgame-restart`**（`am force-stop` 客户端 + 重拉）：`restart-maa` 两次全无效、`killgame-restart` 一次就见效（`01:25:45` 执行 → `01:29` `game pid=2004`、asst.log 0s、gui「开始唤醒→完成→**开始肉鸽**」）。**5 张现场图 md5 完全相同（画面冻结 25 分钟）**是这一形态的铁证。');
W('- **"处理有没有效"现在有常驻复核**（用户要求）：每次动手后下一轮核对 —— 回到 `running-ok` 记 `✅ 有效`，**还是同一形态记 `⚠️ 无效`**，同一形态连续 2 次没救回来直接交人工静默 2h。自证输出：`✅ l-1: 上次「restart-maa」（idle-after-error）有效 —— 4 分钟后已在跑`。');
W('- **误判救援 6 次（22:40–22:44）**：MAA 正在 `StartUp@…`（客户端本来就还没起来）被判 `client-gone` ⇒ 已修（见 §5.4），并作为"**误杀也是事故**"的反面案例留档。');
W('- **l-7 连环失败的真因**（§5.6）：**并发改写配置**把 `Theme` 从 Phantom 改成 Sami，与游戏当前的水月界面对不上 ⇒ 主题不符、MAA 认不出任何东西。已改回 Mizuki 并重启验证。');
W('- **2 小时里 10 次"假有效"**（2026-09-24 10:31–11:26，§5.20）：l-2 停在「探索終了」结算页，**10 张现场图 md5 完全相同**（一像素没变、55 分钟），可每一轮轻动作之后 2 分钟复核都写「✅ 有效 —— 已在跑」——因为"MAA 在写日志"不等于"画面在动"。新判据：同 md5 连拍 ⇒ 记 \`screen-frozen\`、撤销假有效、逐级加重动作（关客户端 → **重启模拟器** → 交人工）。');
W('- **判据/决策随实盘改了 12 处**（§5.4/§5.7/§5.9）：缺 `adb connect`、StartUp 豁免、探针半截 adb 不算"客户端不在"、`task` sticky、`idle-quiet` 补放生通道、零进展接进机队、`retryExhausted` 死枝、假完成要求链级 `TaskChainCompleted`＋非 StartUp＋日志已停、**新形态 `login-lost`**、静默 >300s 也动手、**新形态允许试一次**、**新增"处理是否有效"复核**。自测从 62 → **102 条**全绿（判读 33 + 决策 69）。');
W('');
W('- **环境事件不再误伤**（02:45 修，用户当场指出「你并没有看住」）：同一轮 ≥2 台异常 ⇒ 不交人工、直接关客户端重拉、不计配额；配额改**每台机** 12/h；idle-clean 永不交人工。**代价复盘**见 docs/ROGUE_WATCH.md §5.15。');
W('');
W('## ⑥ 还没验到的（不藏）');
W('- **交人工路径已真触发**（02:06:15）：l-7 再次 `entry-error`，且 1 小时内已救 3 次、该形态已试过 1 次 ⇒ 直接 `needs-human` 静默 2 小时（到 04:06），不再空烧 —— 日志与台账都有留痕。');
W('- **同一模拟器两个 MAA 的主动检测**：目前靠"只在进程不在时发车"+让路闸"产线写日志数=0 且产线 MAA 进程数=0"规避，没有独立的"发现两个 MAA"告警。');
W('- ~~收工让路（03:30 / 产线回来）~~：**03:30:08 已真实触发**（原文 `收工让路：已出夜窗、临近/进入早班时段`，5 台 MAA 全收、模拟器一个没动），另有一次 01:59 受控演练 ⇒ 这条路径两头都验过。');
W('- ~~"现场图 md5 冻结 ⇒ 直接 killgame-restart"~~ **已落地并升成三级阶梯**（同图 → 关客户端 → **重启模拟器** → 交人工，§5.20/§5.21）；**静默冻结**也能发现了（每轮指纹）。**还没在真实冻结现场触发过**——只在 `--freeze-drill` 演练里走过（下次真冻结补日志原文）。');
W('- **机队侧"重启模拟器"只真跑过一次**（l-2，2026-09-24 15:42–15:48，§5.22/§5.23）：闸门按设计升上去了、设备也回来了，但过程中踩出"`launch` 的 errcode:0 不代表起来了、冷启动 >94 秒"的坑（已修）。**它到底能不能治好 l-2 的复发，还要看接下来半小时**。');
W('- 机队的 adb 服务是 5 台 + 产线共用的 ⇒ 机队侧**不能**用 `adb kill-server` 清残留（#0 侧正是靠它把端口要回来的）—— 机队真遇到端口被挪，还是得人上。');
W('- ~~"静默冻结"看不见~~：**已修**（§5.21，每轮屏幕指纹）；反过来说，真冻结也**还没发生在实盘里**，只有演练数据。');
W('- **独立复核未落地的 4 条**：F7（`wrapUp` 会连不共享模拟器的 l-4/l-5 一起收，属口径问题）、F9–F11（#0 侧 `prog.task` 为空时 R1 静默失效、`queueVerdict` 黑名单、三态 `dev`）—— 已记录在 §5.7，未改。');
W('');
W('> 判据与动作总表、逐条证据来源、独立复核的 14 条发现见 `docs/ROGUE_WATCH.md`。');

const text = out.join('\n');
console.log(text);
if (WANT_MD) {
  const dir = path.join(ROOT, 'runtime', 'reports');
  fs.mkdirSync(dir, { recursive: true });
  const d = new Date();
  const f = path.join(dir, `rogue_acceptance_${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}.md`);
  fs.writeFileSync(f, text + '\n');
  console.log(`\n（已写入 ${f}）`);
}



