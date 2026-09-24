#!/usr/bin/env node
/**
 * rogue_plan_selftest.mjs —— 机队看守"决策"自测（发车之前就把闸门与阶梯验穿）
 * ──────────────────────────────────────────────────────────────────────────────
 * 场景都是 2026-09-23 真实遇到过的：产线在跑 / 产线刚空 3 分钟 / 窗口外 / 内存 6G /
 * 客户端不在第 1 轮 / 形态1 放弃确认粘住 / 形态2 假完成 / MiniGame 自环 / 停着的机器 …
 * 每条断言的是"**该不该动手、动哪一级**"，不是"跑没跑起来"（那要等真发车）。
 *
 * 跑法：node tools/rogue_plan_selftest.mjs
 */
import { gate, inWindow, startPerRound, planMachine, wrapUp, actionLabel, updateScreen } from './lib/rogue_plan.mjs';

let pass = 0;
let fail = 0;
const check = (name, got, want, note = '') => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (ok) pass++; else fail++;
  console.log(`${ok ? '  ok  ' : '  FAIL'} ${name}: got=${JSON.stringify(got)} want=${JSON.stringify(want)}${note ? '  « ' + note : ''}`);
};

const NOW = Date.parse('2026-09-23T23:10:00+08:00');
const e0 = (over = {}) => ({ hist: [], gameGoneStreak: 0, lastActionAt: null, humanUntil: null, ...over });
const ctx = (over = {}) => ({ force: false, nowMs: NOW, startGapMin: 8, humanAfter: 2, noProgressMin: 20, gateOpen: true, ...over });
const plan = (kind, e = {}, c = {}) => planMachine({ kind, why: kind }, e, ctx(c));

console.log('── A. 让路闸 / 窗口 / 内存闸 ──');
check('产线在跑 ⇒ 关闸（肉鸽×产线不许并行）',
  gate({ prodRunning: true, prodIdleMin: 0, hourFloat: 23, ramFree: 20, force: false }).open, false);
check('产线刚空 3 分钟 ⇒ 还不算空（要求连续 6 分钟）',
  gate({ prodRunning: false, prodIdleMin: 3, hourFloat: 23, ramFree: 20, force: false }).open, false);
check('产线空 7 分钟 + 23:10 ⇒ 开闸',
  gate({ prodRunning: false, prodIdleMin: 7, hourFloat: 23, ramFree: 20, force: false }).open, true);
check('产线空 7 分钟但 19:30（窗口外）⇒ 关闸',
  gate({ prodRunning: false, prodIdleMin: 7, hourFloat: 19.5, ramFree: 20, force: false }).open, false);
check('产线空 7 分钟、23:10，但内存只剩 6G ⇒ 关闸',
  gate({ prodRunning: false, prodIdleMin: 7, hourFloat: 23, ramFree: 6, force: false }).open, false);
check('白天 13:00 在窗口内', inWindow(13), true);
check('傍晚 19:00 不在窗口内', inWindow(19), false);
check('凌晨 03:00 还在夜窗内', inWindow(3), true);
check('凌晨 04:00 已出夜窗（给 05:00 早班让路）', inWindow(4), false);
check('内存 20G ⇒ 一轮可发 2 台', startPerRound(20), 2);
check('内存 12G ⇒ 一轮只发 1 台', startPerRound(12), 1);

console.log('── B. 单台阶梯（形态 → 动作） ──');
check('停着的机器 ⇒ 发车', plan('parked', e0()).action, 'start');
check('进程刚没 ⇒ 发车', plan('process-gone', e0()).action, 'start');
check('在跑 ⇒ 不动手', plan('running-ok', e0()).action, 'none');
check('判不出来 ⇒ 不动手（别乱来）', plan('unknown', e0()).action, 'none');
check('上条链出错但仍在跑 ⇒ 观察', plan('running-after-error', e0()).action, 'none');
check('形态1 放弃确认粘住 ⇒ 关游戏 + 重拉', plan('entry-stuck', e0()).action, 'killgame-restart');
check('形态2 假完成 ⇒ 关游戏 + 重拉（**绝不许判当日封顶**）', plan('fake-complete', e0()).action, 'killgame-restart');
check('形态3 MiniGame 自环 ⇒ 重拉 MAA', plan('custom-stuck', e0()).action, 'restart-maa');
check('入口 TaskChainError ⇒ 重拉 MAA', plan('entry-error', e0()).action, 'restart-maa');
check('重试耗尽 ⇒ 重拉 MAA', plan('retry-exhausted', e0()).action, 'restart-maa');
check('日志静默且链出错停住 ⇒ 重拉 MAA（旧看守放生的那种）', plan('idle-after-error', e0()).action, 'restart-maa');
check('链干净结束 ⇒ 接着刷', plan('idle-clean', e0()).action, 'restart-maa');

console.log('── C. 防止乱动手的限制 ──');
check('客户端不在第 1 轮 ⇒ 先看着（别打断正在重拉）',
  plan('client-gone', e0({ gameGoneStreak: 1 })).action, 'none');
check('客户端不在第 2 轮 ⇒ 还是先看着（2026-09-23 22:42 只判 2 轮误杀过 l-5 启动链）',
  plan('client-gone', e0({ gameGoneStreak: 2 })).action, 'none');
check('客户端不在第 3 轮 ⇒ 重拉 MAA',
  plan('client-gone', e0({ gameGoneStreak: 3 })).action, 'restart-maa');
check('刚动过手 1 分钟 ⇒ 冷却中不动手',
  plan('parked', e0({ lastActionAt: new Date(NOW - 60e3).toISOString() })).action, 'none');
check('停着的机器距上次动手 5 分钟（<8 分钟）⇒ 先等',
  plan('parked', e0({ lastActionAt: new Date(NOW - 5 * 60e3).toISOString() })).action, 'none');
check('停着的机器距上次动手 9 分钟 ⇒ 发车',
  plan('parked', e0({ lastActionAt: new Date(NOW - 9 * 60e3).toISOString() })).action, 'start');
// ⚠️ 2026-09-24 09:45 政策改了：**交人工看"救得活不活"，不看"救了几次"**。
// 起因：l-1 明明画面好好的（停在萨米探索页），却被"1 小时救 3 次"静默了 31 分钟 ⇒ 白送停机。
check('同一形态 1 小时内犯 2 次、但每次都救活了 ⇒ **不交人工**，继续救（环境在抖、机器没坏）',
  plan('entry-stuck', e0({
    hist: [
      { ts: new Date(NOW - 30 * 60e3).toISOString(), kind: 'entry-stuck' },
      { ts: new Date(NOW - 10 * 60e3).toISOString(), kind: 'entry-stuck' },
    ],
    lastActionAt: new Date(NOW - 10 * 60e3).toISOString(),
  })).action, 'killgame-restart');
check('连续 2 次救援**经复核无效** ⇒ 交人工（这台是真坏）',
  plan('entry-error', e0({ ineffective: 2 })).action, 'needs-human');
check('只无效过 1 次 ⇒ 还没到交人工，继续救（换动作再试）',
  plan('entry-error', e0({ ineffective: 1 })).action, 'killgame-restart');
check('客户端不在要多给机会：1 小时内 2 次还没到交人工的线（线=4）',
  plan('client-gone', e0({
    gameGoneStreak: 3,
    hist: [
      { ts: new Date(NOW - 30 * 60e3).toISOString(), kind: 'client-gone' },
      { ts: new Date(NOW - 10 * 60e3).toISOString(), kind: 'client-gone' },
    ],
    lastActionAt: new Date(NOW - 10 * 60e3).toISOString(),
  })).action, 'restart-maa');
check('客户端在、前台被切走第 1 轮 ⇒ 先看着',
  plan('not-foreground', e0({ fgStreak: 1 })).action, 'none');
check('前台被切走第 2 轮 ⇒ 拉回前台（不用重拉，比重启便宜）',
  plan('not-foreground', e0({ fgStreak: 2 })).action, 'focus-game');
check('发车两次都没起来 ⇒ 交人工（别再空烧模拟器）',
  plan('parked', e0({ startFails: 2 })).action, 'needs-human');
check('只失败过一次 ⇒ 还继续试',
  plan('parked', e0({ startFails: 1 })).action, 'start');
check('队列里没有肉鸽（只勾了开始唤醒）⇒ 不发车、不抢',
  plan('parked', e0(), { queueOk: false, queueList: ['StartUp'] }).action, 'none');
check('队列采不到（undefined）⇒ 不拦，正常发车',
  plan('parked', e0(), { queueOk: null }).action, 'start');
check('复核 F4：判 running-ok 但同一任务卡了 25 分钟 ⇒ 重拉（零进展）',
  plan('running-ok', e0(), { stuckMin: 25 }).action, 'restart-maa');
check('判 running-ok 且任务只卡了 5 分钟 ⇒ 不动手',
  plan('running-ok', e0(), { stuckMin: 5 }).action, 'none');
check('复核 F3：idle-quiet（长时间静默无结论）⇒ 重拉，不再放生',
  plan('idle-quiet', e0()).action, 'restart-maa');
check('复核 night-watch：1 小时内「换花样犯」3 次、但都救活了 ⇒ 不交人工（按无效次数判，不按次数判）',
  plan('fake-complete', e0({
    hist: [
      { ts: new Date(NOW - 50 * 60e3).toISOString(), kind: 'idle-after-error' },
      { ts: new Date(NOW - 20 * 60e3).toISOString(), kind: 'idle-after-error' },
      { ts: new Date(NOW - 5 * 60e3).toISOString(), kind: 'fake-complete' },
    ],
    lastActionAt: new Date(NOW - 5 * 60e3).toISOString(),
  })).action, 'killgame-restart');
check('复核 night-watch：1 小时内 5 次「换花样犯」、都救活 ⇒ 仍不交人工（环境抖动时允许高频救）',
  plan('entry-error', e0({
    hist: [
      { ts: new Date(NOW - 55 * 60e3).toISOString(), kind: 'idle-after-error' },
      { ts: new Date(NOW - 40 * 60e3).toISOString(), kind: 'idle-after-error' },
      { ts: new Date(NOW - 25 * 60e3).toISOString(), kind: 'fake-complete' },
      { ts: new Date(NOW - 10 * 60e3).toISOString(), kind: 'entry-error' },
      { ts: new Date(NOW - 3 * 60e3).toISOString(), kind: 'idle-clean' },
    ],
    lastActionAt: new Date(NOW - 3 * 60e3).toISOString(),
  })).action, 'restart-maa');
check('新形态要允许试一次：已救 3 次但「login-lost」从没试过 ⇒ 先给它一次机会（killgame-restart）',
  plan('login-lost', e0({
    hist: [
      { ts: new Date(NOW - 50 * 60e3).toISOString(), kind: 'idle-after-error' },
      { ts: new Date(NOW - 25 * 60e3).toISOString(), kind: 'fake-complete' },
      { ts: new Date(NOW - 12 * 60e3).toISOString(), kind: 'entry-error' },
    ],
    lastActionAt: new Date(NOW - 12 * 60e3).toISOString(),
  })).action, 'killgame-restart');
check('同一形态试过 3 次、但都救活了 ⇒ 不交人工，继续用有效动作（login-lost）',
  plan('login-lost', e0({
    hist: [
      { ts: new Date(NOW - 50 * 60e3).toISOString(), kind: 'idle-after-error' },
      { ts: new Date(NOW - 25 * 60e3).toISOString(), kind: 'login-lost' },
      { ts: new Date(NOW - 12 * 60e3).toISOString(), kind: 'entry-error' },
    ],
    lastActionAt: new Date(NOW - 12 * 60e3).toISOString(),
  })).action, 'killgame-restart');
check('掉线弹窗 ⇒ 关游戏客户端再重拉（只重拉 MAA 无效，实测 3 次）',
  plan('login-lost', e0()).action, 'killgame-restart');
check('阶梯升级：entry-error 且上次轻动作已被复核无效 ⇒ 直接关客户端重拉（别重复同一个轻动作）',
  plan('entry-error', e0({ ineffective: 1 })).action, 'killgame-restart');
check('阶梯升级：entry-error 首次 ⇒ 仍用轻动作 restart-maa',
  plan('entry-error', e0({ ineffective: 0 })).action, 'restart-maa');
check('🌐 环境事件（同一轮 3 台异常）⇒ 不交人工、直接关客户端重拉（当晚 l-1/l-7/l-4 被逐台静默就是这条缺失）',
  plan('entry-error', e0({
    hist: [
      { ts: new Date(NOW - 50 * 60e3).toISOString(), kind: 'entry-error' },
      { ts: new Date(NOW - 30 * 60e3).toISOString(), kind: 'entry-error' },
      { ts: new Date(NOW - 15 * 60e3).toISOString(), kind: 'idle-after-error' },
      { ts: new Date(NOW - 5 * 60e3).toISOString(), kind: 'entry-error' },
    ],
    lastActionAt: new Date(NOW - 5 * 60e3).toISOString(),
  }), { clusterSize: 3 }).action, 'killgame-restart');
check('同一组历史、只有 1 台异常（非环境事件）⇒ 也**不**交人工：只要救得活就继续救',
  plan('entry-error', e0({
    hist: [
      { ts: new Date(NOW - 50 * 60e3).toISOString(), kind: 'entry-error' },
      { ts: new Date(NOW - 30 * 60e3).toISOString(), kind: 'entry-error' },
      { ts: new Date(NOW - 15 * 60e3).toISOString(), kind: 'idle-after-error' },
      { ts: new Date(NOW - 5 * 60e3).toISOString(), kind: 'entry-error' },
    ],
    lastActionAt: new Date(NOW - 5 * 60e3).toISOString(),
  }), { clusterSize: 1 }).action, 'restart-maa');
check('idle-clean 反复出现也**不交人工**（轻动作、无破坏性；机器此刻确实没在刷 ⇒ 静默=白送停机）',
  plan('idle-clean', e0({
    hist: [
      { ts: new Date(NOW - 50 * 60e3).toISOString(), kind: 'idle-clean' },
      { ts: new Date(NOW - 20 * 60e3).toISOString(), kind: 'idle-clean' },
      { ts: new Date(NOW - 5 * 60e3).toISOString(), kind: 'idle-clean' },
    ],
    lastActionAt: new Date(NOW - 5 * 60e3).toISOString(),
  })).action, 'restart-maa');
check('已交人工（2 小时静默）⇒ 什么都不做',
  plan('parked', e0({ humanUntil: new Date(NOW + 60 * 60e3).toISOString() })).action, 'none');
check('--force（演练）⇒ 冷却也不再挡路',
  plan('parked', e0({ lastActionAt: new Date(NOW - 60e3).toISOString() }), { force: true }).action, 'start');

console.log('── D. 收工让路（别让肉鸽和早班抢同一台模拟器） ──');
check('产线恢复且我们还在跑 ⇒ 收工',
  wrapUp({ prodRunning: true, hourFloat: 23, runningCount: 2, force: false }).need, true);
check('凌晨 04:00（早班临近）且我们还在跑 ⇒ 收工',
  wrapUp({ prodRunning: false, hourFloat: 4, runningCount: 2, force: false }).need, true);
check('夜里 23:30 产线空闲、我们还在跑 ⇒ 继续刷，不收',
  wrapUp({ prodRunning: false, hourFloat: 23.5, runningCount: 2, force: false }).need, false);
check('白天 13:00 窗口内、产线空闲 ⇒ 继续刷',
  wrapUp({ prodRunning: false, hourFloat: 13, runningCount: 1, force: false }).need, false);
check('没有在跑的 ⇒ 没什么可收',
  wrapUp({ prodRunning: true, hourFloat: 4, runningCount: 0, force: false }).need, false);

console.log('── E. 🧊 画面冻结阶梯（2026-09-24 l-2 实证：同一张图连拍 10 次 / 55 分钟） ──');
check('拍到第 2 张同一张图（冻结计数 1）⇒ 关客户端重拉（冻结屏轻动作救不动）',
  plan('entry-error', e0({ frozenShots: 1, screen: { md5: 'aaa', at: new Date(NOW - 3 * 60e3).toISOString() } })).action, 'killgame-restart');
check('冻结计数 1 时 entry-error **不再**用轻动作 restart-maa',
  plan('entry-error', e0({ frozenShots: 1, screen: { md5: 'aaa', at: new Date(NOW - 3 * 60e3).toISOString() } })).action === 'restart-maa', false);
check('关客户端后画面仍未变（冻结计数 2）⇒ 重启模拟器（关客户端这一级已证明无效）',
  plan('entry-error', e0({ frozenShots: 2, screen: { md5: 'aaa', at: new Date(NOW - 3 * 60e3).toISOString() } })).action, 'restart-emulator');
check('running-ok 但画面冻结 ⇒ 照样动手（"MAA 在写日志" ≠ "画面在动"；l-2 就是这么假有效 10 次的）',
  plan('running-ok', e0({ frozenShots: 1, screen: { md5: 'aaa', at: new Date(NOW - 3 * 60e3).toISOString() } })).action, 'killgame-restart');
check('连拍 5 张同图、各级都试过 ⇒ 交人工',
  plan('entry-error', e0({ frozenShots: 4, screen: { md5: 'aaa', at: new Date(NOW - 3 * 60e3).toISOString() } })).action, 'needs-human');
check('画面没冻结（计数 0）⇒ 阶梯照旧（entry-error 首次仍用轻动作）',
  plan('entry-error', e0({ frozenShots: 0 })).action, 'restart-maa');
check('冻结判据不适用于"停着的机器"（它本来就没在刷，别被旧图带偏）',
  plan('parked', e0({ frozenShots: 5, screen: { md5: 'aaa', at: new Date(NOW - 3 * 60e3).toISOString() } })).action, 'start');
check('🧊 冻结证据过期（最后一张图 40 分钟前）⇒ **不作数**，回到原阶梯（否则会把好机器误交人工）',
  plan('entry-error', e0({ frozenShots: 5, screen: { md5: 'aaa', at: new Date(NOW - 40 * 60e3).toISOString() } })).action, 'restart-maa');
check('🧊 同一份计数但图是 3 分钟前拍的 ⇒ 算数（按冻结阶梯升级）',
  plan('entry-error', e0({ frozenShots: 2, screen: { md5: 'aaa', at: new Date(NOW - 3 * 60e3).toISOString() } })).action, 'restart-emulator');
check('📄 动作标签认识新一级 restart-emulator',
  actionLabel('restart-emulator').includes('重启'), true);
console.log('── F. 🧊 每轮屏幕指纹：静默冻结的发现（2026-09-24 §5.21） ──');
const mkE = () => ({ hist: [], lastActionAt: null, humanUntil: null });
const T0 = NOW;
{
  const e = mkE();
  check('第 1 轮拿到指纹 ⇒ 计数 1（第一次看到也算 1 个样本，还无从比较）', updateScreen(e, 'aaa', T0).streak, 1);
  check('第 2 轮同一张图 ⇒ 2 个样本，但**还没上膛**（短暂的静止画面是正常的）',
    updateScreen(e, 'aaa', T0 + 120e3).frozenShots, 0);
  check('第 3 轮还是同一张图（每 2 分钟一轮 ⇒ 至少 4 分钟没动）⇒ 上膛，冻结计数 1（阶梯开始动手）',
    updateScreen(e, 'aaa', T0 + 240e3).frozenShots, 1);
  check('第 4 轮仍同图 ⇒ 冻结计数 2（下一级：重启模拟器）',
    updateScreen(e, 'aaa', T0 + 360e3).frozenShots, 2);
  check('第 5 轮仍同图 ⇒ 冻结计数 3（还在"重启模拟器"这一级）',
    updateScreen(e, 'aaa', T0 + 480e3).frozenShots, 3);
  check('第 6 轮仍同图 ⇒ 冻结计数 4（到交人工线）',
    updateScreen(e, 'aaa', T0 + 600e3).frozenShots, 4);
  check('画面终于动了 ⇒ 立刻归零（不再动手）', updateScreen(e, 'bbb', T0 + 720e3).frozenShots, 0);
}
check('拿不到指纹（探针没采到）⇒ 一律归零，不拿「未知」当「冻结」',
  (() => { const x = mkE(); updateScreen(x, 'aaa', T0); updateScreen(x, 'aaa', T0 + 120e3); updateScreen(x, 'aaa', T0 + 240e3); return updateScreen(x, null, T0 + 360e3).frozenShots; })(), 0);
check('指纹断了之后又同图 ⇒ 重新按 3 轮上膛（不被旧计数带偏）',
  (() => { const x = mkE(); updateScreen(x, 'aaa', T0); updateScreen(x, 'aaa', T0 + 120e3); updateScreen(x, 'aaa', T0 + 240e3); updateScreen(x, null, T0 + 360e3); return updateScreen(x, 'aaa', T0 + 480e3).frozenShots; })(), 0);
check('同图计数 3 时喂给决策 ⇒ 就是冻结阶梯第 1 级（关客户端）',
  (() => { const x = mkE(); updateScreen(x, 'aaa', T0); updateScreen(x, 'aaa', T0 + 120e3); updateScreen(x, 'aaa', T0 + 240e3); return planMachine({ kind: 'running-ok', why: 'x' }, x, ctx()).action; })(), 'killgame-restart');
check('📄 updateScreen 在 5 台独立记（互不影响）',
  (() => { const a = mkE(), b = mkE(); updateScreen(a, 'aaa', T0); updateScreen(a, 'aaa', T0 + 120e3); updateScreen(b, 'zzz', T0); return [a.sameScreenStreak, b.sameScreenStreak]; })(), [2, 1]);

console.log('');
// ── G. 空转闸（relapse）：同一形态反复复发 ⇒ 升动作强度（2026-09-24 15:16–15:34 l-2 实测） ──
console.log('\n── G. 空转闸（反复复发 ⇒ 升一级动作 / 到顶交人工） ──');
{
  const H = (kind, n, stepMin = 4) => ({
    hist: Array.from({ length: n }, (_, i) => ({ ts: new Date(NOW - (n - i) * stepMin * 60000).toISOString(), kind, act: 'killgame-restart' })),
    lastActionAt: new Date(NOW - 4 * 60000).toISOString(),
    ineffective: 0,
  });
  check('形态2 救过 2 次 ⇒ 还是关客户端重拉（没到复发线）',
    plan('fake-complete', H('fake-complete', 2)), { action: 'killgame-restart', why: '形态2 假完成（看着正常，其实死了）' });
  check('形态2 救过 3 次 ⇒ 升到模拟器级重启',
    plan('fake-complete', H('fake-complete', 3)).action, 'restart-emulator');
  check('形态2 救过 3 次的理由里带复发次数',
    /复发 3 次/.test(plan('fake-complete', H('fake-complete', 3)).why), true);
  check('形态2 救过 6 次 ⇒ 交人工（模拟器级也没治住）',
    plan('fake-complete', H('fake-complete', 6)).action, 'needs-human');
  check('另一种形态各救 2 次（不重叠）⇒ 不升级',
    plan('fake-complete', {
      hist: [
        { ts: new Date(NOW - 30 * 60000).toISOString(), kind: 'entry-error', act: 'restart-maa' },
        { ts: new Date(NOW - 26 * 60000).toISOString(), kind: 'entry-error', act: 'restart-maa' },
        { ts: new Date(NOW - 20 * 60000).toISOString(), kind: 'fake-complete', act: 'killgame-restart' },
        { ts: new Date(NOW - 16 * 60000).toISOString(), kind: 'fake-complete', act: 'killgame-restart' },
      ],
      lastActionAt: new Date(NOW - 16 * 60000).toISOString(),
      ineffective: 0,
    }).action, 'killgame-restart');
  check('同样是 3 次，但都在 1 小时以前 ⇒ 不算复发',
    plan('fake-complete', {
      hist: Array.from({ length: 3 }, (_, i) => ({ ts: new Date(NOW - (90 + i) * 60000).toISOString(), kind: 'fake-complete', act: 'killgame-restart' })),
      lastActionAt: new Date(NOW - 4 * 60000).toISOString(),
      ineffective: 0,
    }).action, 'killgame-restart');
  check('同一形态 3 次但**间隔 20 分钟**（轻动作每次都救活）⇒ 不升级（09:45 那条教训）',
    plan('entry-error', {
      hist: [
        { ts: new Date(NOW - 50 * 60000).toISOString(), kind: 'entry-error', act: 'restart-maa' },
        { ts: new Date(NOW - 30 * 60000).toISOString(), kind: 'entry-error', act: 'restart-maa' },
        { ts: new Date(NOW - 10 * 60000).toISOString(), kind: 'entry-error', act: 'restart-maa' },
      ],
      lastActionAt: new Date(NOW - 10 * 60000).toISOString(),
      ineffective: 0,
    }).action, 'restart-maa');
  check('连着复发 6 次（间隔都 <15 分钟）⇒ 交人工',
    plan('idle-clean', (() => {
      const hist = Array.from({ length: 6 }, (_, i) => ({ ts: new Date(NOW - (26 - i * 4) * 60000).toISOString(), kind: 'idle-clean', act: 'restart-maa' }));
      return { hist, lastActionAt: hist[hist.length - 1].ts, ineffective: 0 };
    })()).action, 'needs-human');
  check('连着复发 3 次但上次救援被复核**无效**（ineffective=1）⇒ 不升（那是环境事件的活）',
    plan('entry-error', (() => {
      const hist = Array.from({ length: 3 }, (_, i) => ({ ts: new Date(NOW - (14 - i * 4) * 60000).toISOString(), kind: 'entry-error', act: 'restart-maa' }));
      return { hist, lastActionAt: hist[hist.length - 1].ts, ineffective: 1 };
    })(), { clusterSize: 1 }).action, 'killgame-restart');
  check('在跑（running-ok）不因为救得多而升级',
    plan('running-ok', {
      hist: Array.from({ length: 5 }, (_, i) => ({ ts: new Date(NOW - (20 + i) * 60000).toISOString(), kind: 'running-ok', act: 'none' })),
      lastActionAt: new Date(NOW - 4 * 60000).toISOString(),
      ineffective: 0,
    }).action, 'none');
}

console.log('结果：' + pass + ' 通过 / ' + fail + ' 失败');
process.exit(fail ? 1 : 0);