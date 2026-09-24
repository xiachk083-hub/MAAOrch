#!/usr/bin/env node
/**
 * rogue_judge_selftest.mjs —— 判读内核自测：拿 **2026-09-23 真实日志片段**当夹具
 * ──────────────────────────────────────────────────────────────────────────────
 * 为什么要这个：用户的口径是「肉鸽卡住的问题是多种多样的…你盯五六个号之后我才认可」。
 * 在机队还不能发车（产线在跑）之前，先证明**判据对已经取到的真实病例是准的**——
 * 每条夹具都从 `runtime/fleet_forensics_20260923.txt` / 本机 #0 的 debug 日志里逐字抄来，
 * 期望值写的是"这台该怎么被判 + 该不该动手"。
 *
 * 跑法：node tools/rogue_judge_selftest.mjs
 */
import { classify, lastChainFrom } from './lib/rogue_judge.mjs';

let pass = 0;
let fail = 0;
const check = (name, got, want, note = '') => {
  const ok = got === want;
  if (ok) pass++; else fail++;
  console.log(`${ok ? '  ok  ' : '  FAIL'} ${name}: got=${got} want=${want}${note ? '  « ' + note : ''}`);
};

// ══════════════════════════════════════════════════════════════════════════
// 一、asst.log 真实片段（逐字摘自取证文件）
// ══════════════════════════════════════════════════════════════════════════
// 形态1：l-1 @2026-09-23 13:30 —— 放弃确认后 StartExplore 重试耗尽
const L1_STARTEXPLORE = '[2026-09-23 13:30:35.228][INF][Px43676][Tx36988] {"class":"asst::ProcessTask","cur_task":"Sami@Roguelike@StartExplore","details":{"cur_retry":20,"retry_times":20,"to_be_recognized":["Sami@Roguelike@Squad-EnterPoint","Sami@Roguelike@InitialDrop","Sami@Roguelike@ChooseDifficulty","Sami@Roguelike@StartExplore-EnterCheck"]},"first":["Sami@Roguelike@Begin"],"pre_task":"Sami@Roguelike@AbandonConfirm","subtask":"ProcessTask","taskchain":"Roguelike","taskid":2}';
const L1_CHAINERR = '[2026-09-23 13:30:36.368][INF][Px43676][Tx36988] Assistant::append_callback | TaskChainError {"taskchain":"Roguelike","taskid":2,"uuid":"21261d586a93f14f"}';
// 形态2：l-2 @15:05 —— 假完成（TaskChainCompleted / exec times exceeded the limit）
const L2_RECHECK = '[2026-09-23 15:05:42.429][INF][Px15584][Tx9079] {"class":"asst::ProcessTask","cur_task":"Sami@Roguelike@StartActionReCheck","details":{"cur_retry":0,"retry_times":20,"to_be_recognized":["Sami@Roguelike@NoneSelected","Sami@Roguelike@InBattleFlag","Sami@Roguelike@MissionCompletedFlag","Sami@Roguelike@StartActionReCheck"]},"first":["Sami@Roguelike@Begin"],"pre_task":"Sami@Roguelike@StartActionReCheck","subtask":"ProcessTask","taskchain":"Roguelike","taskid":2}';
const L2_COMPLETED = '[2026-09-23 15:05:55.333][INF][Px15584][Tx9079] Assistant::append_callback | TaskChainCompleted {"taskchain":"Roguelike","taskid":2,"uuid":"e2598b72c423c0e5"}';
const L2_EXCEEDED = '[2026-09-23 15:05:55.1xx][INF][Px15584][Tx9079] {"class":"asst::ProcessTask","cur_task":"Sami@Roguelike@StartActionReCheck","details":{"result":{"exceeded":true}},"taskchain":"Roguelike"} exec times exceeded the limit';
// 形态3：l-7 @15:05 —— 自定任务 MiniGame SPA 自环
const L7_MINI = '[2026-09-23 15:05:44.892][INF][Px41320][Tx38276] {"class":"asst::ProcessTask","cur_task":"MiniGame@SPA@LoadingText","details":{"cur_retry":17,"retry_times":20,"to_be_recognized":["MiniGame@SPA@GiveUp","MiniGame@SPA@ReturnHome","MiniGame@SPA@LoadingText","MiniGame@SPA@LoadingIcon"]},"first":["MiniGame@SPA@Begin"],"pre_task":"MiniGame@SPA@LoadingText","subtask":"ProcessTask","taskchain":"Custom","taskid":5}';
// 形态4（陈旧 l-6 @09-07）：入口可识别集合只剩 1 条
const L6_ENTRY = '[2026-09-07 01:28:04.9xx][INF][Px24256][Tx26530] {"class":"asst::ProcessTask","cur_task":"JieGarden@Terminal-Entry","details":{"cur_retry":20,"retry_times":20,"to_be_recognized":["JieGarden@Terminal-Entry@SwitchTheme@ToggleSettingsMenu"]},"first":["JieGarden@Terminal-Entry"],"pre_task":"","subtask":"ProcessTask","taskchain":"Roguelike","taskid":2}';
const L6_ERR = '[2026-09-07 01:28:05.127][ERR][Px24256][Tx26530] Sub error and not ignored JieGarden@Terminal-Entry';
const L6_CHAINERR = '[2026-09-07 01:28:05.195][INF][Px24256][Tx26530] Assistant::append_callback | TaskChainError {"taskchain":"Roguelike","taskid":2,"uuid":"21261d586a93f14f"}';
// 2026-09-24 00:02 实测（复核员抓到的**假阳性**）：MAA 正常刷图时，子步骤级也会出现
// `exec times exceeded the limit`（例：`Mizuki@Roguelike@StageTraderInvestSystem` exec_times=0/max_times=0
// 是 MAA 的正常跳过语义），**没有** TaskChainCompleted。旧判据只看这串字，就会把"正常在刷"判成假完成 ⇒ 直接关游戏重拉（误杀）。
const FALSE_EXCEEDED = '[2026-09-24 00:02:32.100][INF][Px4808][Tx1] {"class":"asst::ProcessTask","cur_task":"Mizuki@Roguelike@StageTraderInvestSystem","details":{"cur_retry":0,"retry_times":3,"action":"ClickRect","algorithm":"JustReturn","exec_times":0,"max_times":0,"result":{"exceeded":true}},"first":["Mizuki@Roguelike@Begin"],"pre_task":"Mizuki@Roguelike@StageTraderInvestSystem","subtask":"ProcessTask","taskchain":"Roguelike","taskid":2}';

// ══════════════════════════════════════════════════════════════════════════
// 二、gui.log 真实片段
// ══════════════════════════════════════════════════════════════════════════
const G = (t, lvl, msg) => `[2026-09-23 ${t}][${lvl}][TaskQueueViewModel]     <2> ${msg}`;
const L1_GUI = [
  G('13:29:32.696', 'INF', '开始任务: 自动肉鸽'),
  G('13:30:15.706', 'INF', '已开始探索 1 次'),
  G('13:30:36.368', 'ERR', '任务出错: 自动肉鸽'),
  G('13:30:36.851', 'INF', '任务已全部完成！'),
];
const L2_GUI = [
  G('15:01:02.000', 'INF', '开始任务: 自动肉鸽'),
  G('15:01:02.000', 'INF', '开始战斗: 违和'),
  G('15:05:55.344', 'INF', '完成任务: 自动肉鸽'),
  G('15:05:55.404', 'INF', '任务已全部完成！'),
];
const L7_GUI = [
  G('13:17:48.000', 'INF', '开始任务: 自定任务'),
  G('15:05:48.598', 'ERR', '任务出错: 自定任务'),
  G('15:05:48.900', 'INF', '任务已全部完成！'),
];
// 形态5（l-2 @14:56）：开始唤醒出错，但队列照走 —— 良性，不许动手
const L5_GUI = [
  G('14:54:21.414', 'INF', '正在连接模拟器……'),
  G('14:56:06.064', 'ERR', '任务出错: 开始唤醒'),
  G('14:56:06.564', 'INF', '开始任务: 自动肉鸽'),
  G('14:59:00.000', 'INF', '关卡: 普通作战'),
];
// 本机 #0 @20:49-20:52：重开链后 开始唤醒+自动肉鸽 双双出错（客户端卡通信途絶）
const Z0_GUI = [
  G('20:49:53.570', 'INF', '开始任务: 开始唤醒'),
  G('20:51:33.202', 'ERR', '任务出错: 开始唤醒'),
  G('20:51:33.702', 'INF', '开始任务: 自动肉鸽'),
  G('20:52:29.136', 'ERR', '任务出错: 自动肉鸽'),
  G('20:52:29.169', 'ERR', '任务已完成，但出现错误！'),
  G('20:52:29.178', 'ERR', '本轮任务出现以下错误：'),
  // ⚠️ 真实 gui.log 里这两行是**没有时间戳前缀的续行**，夹具有样学样（自测第一次就翻在这儿）
  '开始唤醒',
  '自动肉鸽',
  G('20:52:29.179', 'INF', 'Post actions: 无动作'),
];

// ══════════════════════════════════════════════════════════════════════════
console.log('── A. 形态判读（classify） ──');
const at = (h, m) => new Date(new Date().setHours(h, m, 0, 0)).toISOString();

// 2026-09-23 22:42 实测：l-5 在 StartUp（MAA 正在自己拉客户端）被误判成 client-gone 并杀掉重拉
check('启动阶段 game=false **不算** client-gone（MAA 正自己拉客户端）',
  classify({ proc: 1, asstAge: 1, game: false, task: 'StartUp@StartToWakeUp', asstLast: ['{"class":"asst::ProcessTask","cur_task":"StartUp@StartToWakeUp","details":{"cur_retry":3,"retry_times":50}}'], guiLast: [G('22:40:00.000', 'INF', '开始任务: 开始唤醒')] }).kind,
  'running-ok');
check('启动阶段 StartUpBegin 同样不算 client-gone',
  classify({ proc: 1, asstAge: 1, game: false, task: 'StartUpBegin', asstLast: ['{"class":"asst::ProcessTask","cur_task":"StartUpBegin","details":{"cur_retry":1,"retry_times":50}}'], guiLast: [G('22:40:00.000', 'INF', '开始任务: 开始唤醒')] }).kind,
  'running-ok');
check('非启动阶段的 game=false 才算 client-gone',
  classify({ proc: 1, asstAge: 1, game: false, task: 'Mizuki@Roguelike@Begin', asstLast: ['{"class":"asst::ProcessTask","cur_task":"Mizuki@Roguelike@Begin","details":{"cur_retry":3,"retry_times":20}}'], guiLast: [G('22:40:00.000', 'INF', '开始任务: 自动肉鸽')] }).kind,
  'client-gone');
check('game 未知（adb 没读出来）不该当成 client-gone',
  classify({ proc: 1, asstAge: 1, game: null, task: 'Mizuki@Roguelike@Begin', asstLast: ['{"class":"asst::ProcessTask","cur_task":"Mizuki@Roguelike@Begin","details":{"cur_retry":3,"retry_times":20}}'], guiLast: [G('22:40:00.000', 'INF', '开始任务: 自动肉鸽')] }).kind,
  'running-ok');

check('子步骤级 exec times exceeded（无 TaskChainCompleted）**不算**假完成（2026-09-24 复核抓到的误杀）',
  classify({ proc: 1, asstAge: 1, game: true, fgOk: true, task: 'Mizuki@Roguelike@StageTraderInvestSystem', asstLast: [FALSE_EXCEEDED], guiLast: [G('00:02:00.000', 'INF', '开始任务: 自动肉鸽')] }).kind,
  'running-ok');

check('重试耗尽（机队真实采样 16:02:40Z：task=Sami@Roguelike@ExitThenAbandon、20/20）⇒ 不当 running-ok 放生',
  classify({
    proc: 1, asstAge: 0, game: true, fgOk: true,
    task: 'Sami@Roguelike@ExitThenAbandon', retry: 20, retryTimes: 20,
    asstLast: ['{"class":"asst::ProcessTask","cur_task":"Sami@Roguelike@ExitThenAbandon","details":{"cur_retry":20,"retry_times":20}}'],
    guiLast: [
      G('00:00:17.000', 'INF', '开始任务: 开始唤醒'),
      G('00:01:58.153', 'ERR', '任务出错: 开始唤醒'),
      G('00:01:58.653', 'INF', '开始任务: 自动肉鸽'),
    ],
  }).kind, 'retry-exhausted');

// 2026-09-24 night-watch 用真实采样（16:57:05Z）复现的剩余假阳性：队列 [StartUp, Roguelike] 下，
// **每次 MAA 启动 StartUp 链正常完成**都会留 TaskChainCompleted {"taskchain":"StartUp"} ⇒ 不能算假完成
check('StartUp 链正常完成（gui 还在跑）**不算**假完成（否则 killgame-restart 误杀健康机）',
  classify({
    proc: 1, asstAge: 1, game: true, fgOk: true,
    task: 'Mizuki@Terminal-Entry',
    asstLast: [
      '[2026-09-24 00:57:05.000][INF][Px1][Tx1] Assistant::append_callback | TaskChainCompleted {"taskchain":"StartUp","taskid":1}',
      '{"class":"asst::ProcessTask","cur_task":"Mizuki@Terminal-Entry","details":{"cur_retry":0,"retry_times":20}}',
    ],
    guiLast: [G('00:56:50.000', 'INF', '开始任务: 自动肉鸽')],
  }).kind, 'running-ok');
check('Roguelike 链干净结束 + 日志已停 60s ⇒ 才是假完成（00:54 l-7 真事故的真实采样形态）',
  classify({
    proc: 1, asstAge: 60, game: true, fgOk: true,
    task: 'Mizuki@Roguelike@StartActionReCheck',
    asstLast: ['[2026-09-24 00:54:02.092][INF][Px1][Tx1] Assistant::append_callback | TaskChainCompleted {"taskchain":"Roguelike","taskid":2}'],
    guiLast: [
      G('00:50:00.000', 'INF', '开始任务: 自动肉鸽'),
      G('00:54:02.500', 'INF', '完成任务: 自动肉鸽'),
      G('00:54:03.000', 'INF', '任务已全部完成！'),
    ],
  }).kind, 'fake-complete');
check('形态2 刚完成那一瞬（日志还在写）先别动手 ⇒ 交给 3 分钟后的 idle-after-error（动作更轻，不 force-stop 游戏）',
  classify({
    proc: 1, asstAge: 2, game: true, fgOk: true,
    task: 'Mizuki@Roguelike@StartActionReCheck',
    asstLast: ['[2026-09-24 00:54:02.092][INF][Px1][Tx1] Assistant::append_callback | TaskChainCompleted {"taskchain":"Roguelike","taskid":2}'],
    guiLast: [
      G('00:50:00.000', 'INF', '开始任务: 自动肉鸽'),
      G('00:54:02.500', 'INF', '完成任务: 自动肉鸽'),
      G('00:54:03.000', 'INF', '任务已全部完成！'),
    ],
  }).kind, 'running-ok');

check('静默 325s + gui 已停止 ⇒ idle-clean（原来这段落在 running-ok，白放生 7 分钟）',
  classify({ proc: 1, asstAge: 325, game: true, fgOk: true, task: 'Mizuki@Roguelike@StartExplore', asstLast: ['{"class":"asst::ProcessTask","cur_task":"Mizuki@Roguelike@StartExplore","details":{"cur_retry":20,"retry_times":20}}'], guiLast: [G('01:00:16.000', 'INF', '开始任务: 开始唤醒'), G('01:00:19.335', 'INF', '已停止')] }).kind,
  'idle-clean');
check('静默 325s 且 gui 什么都不说 ⇒ idle-quiet（同样要动手）',
  classify({ proc: 1, asstAge: 325, game: true, fgOk: true, task: 'Mizuki@Roguelike@StartExplore', asstLast: ['{"class":"asst::ProcessTask","cur_task":"Mizuki@Roguelike@StartExplore","details":{"cur_retry":3,"retry_times":20}}'], guiLast: [G('01:00:16.000', 'INF', '开始任务: 开始唤醒')] }).kind,
  'idle-quiet');

check('掉线弹窗（l-7 01:12 真实片段：gui「游戏掉线，停止任务」+ 已停止 + 静默 685s）⇒ login-lost，不是 idle',
  classify({
    proc: 1, asstAge: 685, game: true, fgOk: true, task: 'Mizuki@Roguelike@StartExplore',
    asstLast: ['[2026-09-24 01:00:18.833][INF][Px1][Tx1] Assistant::append_callback | TaskChainCompleted {"taskchain":"StartUp","taskid":1}'],
    guiLast: [G('01:00:18.835', 'INF', '游戏掉线，停止任务'), G('01:00:19.335', 'INF', '已停止')],
  }).kind, 'login-lost');

check('形态1 放弃确认粘住（l-1 真实片段）',
  classify({ proc: 1, asstAge: 3, asstLast: [L1_STARTEXPLORE, L1_CHAINERR], guiLast: L1_GUI, task: 'Sami@Roguelike@StartExplore', retry: 20, retryTimes: 20, retryExhausted: true }).kind,
  'entry-stuck');

check('形态2 假完成（l-2 真实片段：TaskChainCompleted，无 TaskChainError；日志已停 45s）',
  classify({ proc: 1, asstAge: 45, asstLast: [L2_RECHECK, L2_EXCEEDED, L2_COMPLETED], guiLast: L2_GUI, task: 'Sami@Roguelike@StartActionReCheck', retry: 0, retryTimes: 20, retryExhausted: false }).kind,
  'fake-complete');

check('形态3 自定任务/MiniGame 自环（l-7 真实片段）',
  classify({ proc: 1, asstAge: 5, asstLast: [L7_MINI], guiLast: L7_GUI, task: 'MiniGame@SPA@LoadingText', retry: 17, retryTimes: 20 }).kind,
  'custom-stuck');

check('形态4 入口集合只剩 1 条（陈旧 l-6 片段，含真实 Sub error/TaskChainError）',
  classify({ proc: 1, asstAge: 4, asstLast: [L6_ENTRY, L6_ERR, L6_CHAINERR], guiLast: L5_GUI, task: 'JieGarden@Terminal-Entry', retry: 20, retryTimes: 20, retryExhausted: true }).kind,
  'entry-error');

check('形态5 开始唤醒出错但队列照走 ⇒ 只观察不动手（判 running-ok 也对）',
  classify({ proc: 1, asstAge: 2, asstLast: ['{"class":"asst::ProcessTask","cur_task":"Sami@Roguelike@StartAction"}'], guiLast: L5_GUI, task: 'Sami@Roguelike@StartAction' }).kind,
  'running-ok');

// 2026-09-23 23:52 实测：l-2 的链 23:45 出错并停住，600s 阈值要等到 23:56 才动手 ⇒ 半静默阈值收到 180s
check('链已出错 + 日志静默 200s ⇒ 当它停了（别干等 10 分钟）',
  classify({ proc: 1, asstAge: 200, asstLast: ['{"class":"asst::ProcessTask","cur_task":"Sami@Roguelike@StartAction","details":{"cur_retry":0,"retry_times":20}}'], guiLast: L1_GUI }).kind,
  'idle-after-error');
check('链已出错但日志只静默 30s ⇒ 仍在跑，观察',
  classify({ proc: 1, asstAge: 30, asstLast: ['{"class":"asst::ProcessTask","cur_task":"Sami@Roguelike@StartAction","details":{"cur_retry":0,"retry_times":20}}'], guiLast: L1_GUI }).kind,
  'running-after-error');

check('停着的机器（proc=0 且日志早已不动）',
  classify({ proc: 0, asstAge: 26900, asstLast: [L1_CHAINERR], guiLast: L1_GUI }).kind,
  'parked');

check('进程刚没（日志一分钟前还在写）',
  classify({ proc: 0, asstAge: 40, asstLast: [L1_CHAINERR], guiLast: L1_GUI }).kind,
  'process-gone');

// ⚠️ 2026-09-24 09:02 发现：下面两条夹具原来写死 `G('21:00:00')`（固定日期 2026-09-23）⇒ 过了 12 小时
// `guiAgeMin` 超限、判据落到 `unknown`，**测试自己在白天烂掉**（夜里 33 全绿、早上 31/33）。
// ⇒ 凡是依赖"距现在多久"的夹具，时间戳必须**相对现在**生成（`NowG(minutesAgo, ...)`）。
const NowG = (minsAgo, lvl, msg) => {
  const d = new Date(Date.now() - minsAgo * 60e3);
  const p = (n) => String(n).padStart(2, '0');
  return `[${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}.000][${lvl}][TaskQueueViewModel]     <2> ${msg}`;
};
check('进程在、日志静默、gui 最后一行是「任务出错」⇒ 旧看守放生的那种死法',
  classify({ proc: 1, asstAge: 1200, asstLast: [L1_CHAINERR], guiLast: [NowG(2, 'ERR', '任务出错: 自动肉鸽')] }).kind,
  'idle-after-error');

check('进程在、日志静默、gui 显示链已结束（如 l-2 那种"假完成"停住）',
  classify({ proc: 1, asstAge: 1200, asstLast: [], guiLast: [NowG(2, 'INF', '任务已全部完成！')] }).kind,
  'idle-clean');

check('客户端不在（MAA 还在跑、还在写日志）⇒ client-gone（2026-09-23 演练挖出的新形态）',
  classify({ proc: 1, asstAge: 1, game: false, asstLast: ['{"class":"asst::ProcessTask","cur_task":"Mizuki@Roguelike@Begin","details":{"cur_retry":3,"retry_times":20}}'], guiLast: [G('21:04:00.000', 'INF', '开始任务: 自动肉鸽')] }).kind,
  'client-gone');

check('客户端在、但前台不是游戏（被切到桌面）⇒ not-foreground',
  classify({ proc: 1, asstAge: 1, game: true, fgOk: false, fg: 'app.lawnchair', asstLast: ['{"class":"asst::ProcessTask","cur_task":"Mizuki@Roguelike@Begin","details":{"cur_retry":2,"retry_times":20}}'], guiLast: [G('21:04:00.000', 'INF', '开始任务: 自动肉鸽')] }).kind,
  'not-foreground');

check('正常在跑（真的别乱动它）',
  classify({ proc: 1, asstAge: 1, asstLast: ['{"class":"asst::ProcessTask","cur_task":"Sami@Roguelike@StageCombatOps","details":{"cur_retry":1,"retry_times":20}}'], guiLast: [G('21:00:00.000', 'INF', '关卡: 普通作战')], task: 'Sami@Roguelike@StageCombatOps', retry: 1, retryTimes: 20 }).kind,
  'running-ok');

console.log('── B. 任务链结局判读（lastChainFrom） ──');
const chk = (name, lines, wantOutcome, wantErrors) => {
  const r = lastChainFrom(lines);
  check(name, r.outcome, wantOutcome, `errors=${JSON.stringify(r.errors)}`);
  // 出错任务名的**顺序**不重要（同一轮里中文出错行与"本轮任务出现以下错误"块的先后会变），比集合
  if (wantErrors) check(name + ' 的出错任务名', JSON.stringify([...r.errors].sort()), JSON.stringify([...wantErrors].sort()));
};
chk('形态1 链结局 = error（"任务已全部完成！"紧跟出错，不能当成功）', L1_GUI, 'error', ['自动肉鸽']);
chk('形态2 链结局 = clean（只有完成、没有出错 ⇒ 但它其实是死的，靠假完成判据兜住）', L2_GUI, 'clean');
chk('#0 卡通信途絶那条链：结局 = error，两个任务都记上', Z0_GUI, 'error', ['开始唤醒', '自动肉鸽']);
chk('形态5 那条链：最后一个「开始任务」之后没有出错 ⇒ 判 running（不许误报）', L5_GUI, 'running');
chk('空日志 ⇒ unknown', [], 'unknown');

console.log(`\n结果：${pass} 通过 / ${fail} 失败`);
process.exit(fail ? 1 : 0);
