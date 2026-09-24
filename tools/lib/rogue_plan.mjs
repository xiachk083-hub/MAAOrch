/**
 * lib/rogue_plan.mjs —— 机队看守的"决策"内核（与"动手"分开，好让它在机器发车之前就能自测）
 * ──────────────────────────────────────────────────────────────────────────────
 * 为什么单独抽出来：用户的口径是「你盯五六个号之后我才认可这个看门狗」。
 * 机队今天被产线压着发不了车，但**决策对不对**现在就能验：把闸门与阶梯写成纯函数，
 * 用真实场景（产线在跑/刚空 3 分钟/窗口外/内存 6G/客户端不在第 1 轮/形态1/假完成…）
 * 逐条断言，见 tools/rogue_plan_selftest.mjs。等真发车时，动手部分照这份决策执行即可。
 *
 * 决策只有 5 种动作（都是"加法"或既定应急手段，绝不乱杀非本会话管辖的进程）：
 *   start            → 触发登录态发车任务（拉模拟器 + 起 MAA，MAA RunDirectly 自动开跑）
 *   restart-maa      → 收掉本机队自己的 MAA 再发车（形态3/4、零进展、假完成后停住、客户端不在）
 *   killgame-restart → 先 `am force-stop` 游戏客户端，再收 MAA 再发车（形态1 放弃确认粘住、形态2 假完成）
 *   needs-human      → 同形态反复犯 ⇒ 交人工，静默 2 小时（不再烧机器）
 *   none             → 什么都不做（在跑、判不出来、冷却中、已交人工…）
 */

/**
 * 时间窗（2026-09-24 08:55 按 MAS 队列真实时刻改）：**只避开发车前 60 分钟**。
 * 依据 `E:\AUTO-MAS\config\QueueConfig.json` 实测：**早班定时 1–12 = 05:00 起（每 3 分一个）**、
 * **晚班定时 1–12 = 19:30 起（实际 19:51 起错峰发车）** ⇒ 让位区 = 04:00–05:00 与 18:30–20:30；其余时间**允许发车**，
 * 真正的保护是"产线在跑/连续 6 分钟没空"的让路闸（那个才反映当下实况）。
 * 旧规则（10–18 / 22–03:30）问题是太死：早班 08:5x 就跑完了，却要干等到 10:00 白丢一个多小时
 * （用户 2026-09-24 08:53 明确要求"把肉鸽抛起来"）。
 */
export function inYieldZone(hourFloat) {
  // 早班 05:00 发车点前 60 分钟；晚班 19:30 发车点前 60 分钟 **+ 爬坡到 20:30**
  // （2026-09-24 补：晚班 12 队是 19:51 起错峰发车，让位区原来 19:30 就结束 ⇒ 19:30–19:51 闸开、
  //  看守会把肉鸽又发出去，19:51 产线一上就"同一模拟器两个 MAA"，正撞红线③。）
  return (hourFloat >= 4 && hourFloat <= 5) || (hourFloat >= 18.5 && hourFloat <= 20.5);
}
export function inWindow(hourFloat) {
  return !inYieldZone(hourFloat);
}

/**
 * 让路闸：返回闸门状态。**产线在跑就一律不发车**（肉鸽×产线不许并行是本质约束）；
 * 产线"空了"必须**连续**够久才算空（两台任务之间会有几十秒空档，单轮 active=0 不算）。
 */
export function gate({ prodRunning, prodIdleMin, hourFloat, ramFree, force, prodIdleMinRequired = 6, ramFloorGB = 8 }) {
  if (force) return { open: true, why: '--force 绕闸' };
  if (prodRunning) return { open: false, why: '产线在跑（让路：肉鸽×产线不许并行）' };
  if (prodIdleMin < prodIdleMinRequired) {
    return { open: false, why: `产线只空了 ${Math.round(prodIdleMin)} 分钟（要求连续 ≥${prodIdleMinRequired} 分钟，避免被任务间空档骗到）` };
  }
  if (!inWindow(hourFloat)) return { open: false, why: '不在肉鸽窗口（让位区：早班 04:00–05:00 / 晚班 18:30–20:30）' };
  if (ramFree >= 0 && ramFree < ramFloorGB) return { open: false, why: `主机内存只剩 ${ramFree}G（<${ramFloorGB}G 不发车，别把主机压垮）` };
  return { open: true, why: '产线已空 + 窗口内 + 内存够' };
}

/** 一条内存闸：内存越紧，一轮越少发车 */
export const startPerRound = (ramFree, normal = 2) => (ramFree >= 0 && ramFree < 14 ? 1 : normal);

/**
 * 收工判断：**该不该把我们的肉鸽收掉让路**。
 * 为什么需要它：l-1/l-2/l-7 的模拟器（emu 25/28/52）同时也是产线早班/晚班的槽位 ⇒
 * 如果天亮时我们的肉鸽还在跑，产线 04:00 计划重启后拉起自己的 MAA，**同一模拟器上就会有两个 MAA**
 * （红线③）。所以只要「产线回来了」或「已出窗口/临近早班（03:30–10:00）」，就把我们自己的 MAA 收掉。
 * #0 在本机、与产线不同机，不受这条约束。
 */
export function wrapUp({ prodRunning, hourFloat, runningCount, force }) {
  if (force) return { need: false, why: '--force 演练' };
  if (!runningCount) return { need: false, why: '没有在跑的肉鸽' };
  if (prodRunning) return { need: true, why: '产线已恢复（同一模拟器不许两个 MAA）' };
  if (inYieldZone(hourFloat)) {
    return { need: true, why: '处在让位区（早班 04:00–05:00 / 晚班 18:30–20:30 爬坡）⇒ 让位' };
  }
  return { need: false, why: '窗口内且产线空闲，继续刷' };
}

const ACT = {
  start: '发车（拉模拟器 + 起 MAA + 开跑）',
  'restart-maa': '收掉本机队 MAA 再发车',
  'killgame-restart': '关游戏客户端 + 收 MAA 再发车',
  'restart-emulator': '重启这台模拟器 + 再发车',
  'focus-game': '把游戏拉回前台（monkey 发 launcher intent）',
  'needs-human': '交人工（静默 2 小时，不再烧机器）',
  none: '不动手',
};
export const actionLabel = (a) => ACT[a] || a;

/**
 * 单台机器的决策（纯函数：只看采样 + 当日台账，不改任何东西）。
 * @param cls  {{kind, why}} 来自 lib/rogue_judge.mjs 的 classify()
 * @param e    该台的当日状态（hist / gameGoneStreak / lastActionAt / humanUntil）
 * @param ctx  {force, nowMs, startGapMin, humanAfter, noProgressMin, gateOpen}
 */
/**
 * 每轮**屏幕指纹** → "画面同图"计数（2026-09-24 §5.21 新增；唯一能发现**静默冻结**的信号）。
 * 连续 `armRounds` 个样本（默认 3；看守每 2 分钟一轮 ⇒ 至少 4 分钟画面一像素没动）才"上膛"：
 * 上膛后 `frozenShots` 从 1 开始，交给 planMachine 里那条冻结阶梯（1 关客户端 / 2 重启模拟器 / ≥4 交人工）。
 * 为什么先要 3 个样本：**短暂的静止画面是正常的**（结算页、切图瞬间、MAA 等按钮），单轮同图就动手会误杀。
 * 拿不到指纹（`md5` 空）就一律归零 —— 宁可漏判，也不拿"未知"当"冻结"。
 * @returns {{streak:number, frozenShots:number, since:(string|null), moved:boolean}}
 */
export function updateScreen(e, md5, nowMs, armRounds = 3) {
  if (!md5) {
    e.sameScreenStreak = 0;
    e.frozenShots = 0;
    return { streak: 0, frozenShots: 0, since: null, moved: false };
  }
  const same = !!(e.screen && e.screen.md5 === md5);
  const at = new Date(nowMs).toISOString();
  // 按"样本数"计：第一次看到某张图也算 1 个样本（连续 3 个样本 = 至少 4 分钟没动）
  e.sameScreenStreak = same ? (e.sameScreenStreak || 0) + 1 : 1;
  e.screen = { md5, at, since: same && e.screen && e.screen.since ? e.screen.since : at };
  e.frozenShots = Math.max(0, e.sameScreenStreak - (armRounds - 1));
  return { streak: e.sameScreenStreak, frozenShots: e.frozenShots, since: e.screen.since, moved: !same };
}

/**
 * ⚠️ 2026-09-24 15:16–15:34 **空转闸（l-2 实测）**：形态2「假完成」每隔 2 分钟复发一次，
 * 而**每一次救援都会被时效复核记成「有效」**（+2 分钟确实在跑、画面也确实动过）⇒ `ineffective`
 * 永远是 0、`frozenShots` 也被自己这次动作清零 ⇒ 阶梯永远停在 `killgame-restart`，
 * 机器就 2 分钟一轮地被"救"到天荒地老（还在烧动作配额、号一秒没刷）。
 * ⇒ 判据：**同一形态"连着"被救 ≥3 次（相邻两次间隔 <15 分钟）= 反复复发** ⇒ 顶一级动作
 *    （killgame-restart → restart-emulator）；连着 ≥6 次说明连模拟器级重启都没治住 ⇒ 交人工 + 留现场图。
 * 注意这跟 2026-09-24 09:45 那条教训**不冲突**：那时删的是"救几次就静默"（会误杀好机器），
 * 这里升级的是**动作强度**，不停手 —— 只要还能救活就继续救，只是不再原地打转。
 */
const FAIL_KINDS = new Set([
  'fake-complete', 'entry-stuck', 'login-lost', 'entry-error', 'retry-exhausted',
  'idle-quiet', 'idle-clean', 'idle-after-error', 'client-gone', 'not-foreground', 'custom-stuck',
]);

export function planMachine(cls, e, ctx) {
  const nowMs = ctx.nowMs;
  if (e.humanUntil && new Date(e.humanUntil).getTime() > nowMs) {
    return { action: 'none', why: `已交人工（到 ${String(e.humanUntil).slice(11, 16)}）` };
  }
  // 复核 F4：机队原来**根本没接**"零进展"判据（`stuckMin` 算了不用）⇒ 日志一直在写但同一任务卡住的
  // 机器（正是旧 rogue_watch 漏掉的那类：l-7 卡 MiniGame 4.8 小时）会被放生。
  // ⚠️ 必须放在下面那三行"在跑就不动手"**之前**，否则永远走不到。
  // 🧊 **画面冻结阶梯**（2026-09-24 10:31–11:26 实证，见 docs/ROGUE_WATCH.md §5.20）：
  // l-2 停在「探索終了」结算页上被连拍 **10 张现场图、md5 完全相同**（一像素没变，55 分钟），
  // 而每一轮轻动作之后 2 分钟都被记成「✅ 有效 —— 已在跑」——因为**"MAA 在写日志" ≠ "画面在动"**。
  // 冻结屏连 MAA 自己都认不出来（这正是 entry-error 的真因），所以轻动作（只重拉 MAA、不碰画面）
  // 治不好它；实测 `killgame-restart` 也没解开 ⇒ 需要**模拟器级**重启（旧版阶梯到关客户端就断了）。
  // ⚠️ 必须放在下面「在跑就不动手」之前，否则 running-ok 的冻结屏永远走不到（同 F4 那个坑）。
  // ⚠️ 冻结证据**必须新鲜**（2026-09-24 15:0x 重放 l-2 时发现的反例）：l-2 最后一张冻结图是 11:26，
  // 之后 3.4 小时没人动手（判据判它 running-ok、没拍图）⇒ 到 14:50 那次 `retry-exhausted`（本来
  // 一次轻动作就好了）如果还拿着 11:26 的旧计数，就会直接"交人工静默 2 小时"——又是白送停机。
  // ⇒ 只有"最后一张现场图是最近 20 分钟内拍的"才算数（和"时效复核 15 分钟"同一个道理）。
  // 证据时刻改用**每轮屏幕指纹**的时刻（`e.screen.at`）——2026-09-24 §5.21 起，冻结不再依赖"动手时才拍图"。
  const screenAgeMin = e.screen && e.screen.at ? (nowMs - new Date(e.screen.at).getTime()) / 60000 : Infinity;
  const frozen = screenAgeMin <= (ctx.frozenFreshMin || 20) ? (e.frozenShots || 0) : 0;
  const isRescueKind = (k) => k !== 'parked' && k !== 'process-gone' && k !== 'unknown';
  if (isRescueKind(cls.kind) && frozen >= 1) {
    if (frozen >= 4) {
      return { action: 'needs-human', why: `画面连着 ${frozen + 1} 张现场图 md5 相同（关客户端 + 重启模拟器都没解开）⇒ 交人工` };
    }
    if (frozen >= 2) {
      return { action: 'restart-emulator', why: `画面连着 ${frozen + 1} 张同图（关客户端重拉也无效）⇒ 重启这台模拟器` };
    }
    return { action: 'killgame-restart', why: `画面连着 ${frozen + 1} 张同图（画面卡死，轻动作救不动）⇒ 关客户端重拉` };
  }

  if (cls.kind === 'running-ok' && ctx.stuckMin !== undefined && ctx.stuckMin >= (ctx.noProgressMin || 20)) {
    return { action: 'restart-maa', why: `同一个任务卡了 ${Math.round(ctx.stuckMin)} 分钟（零进展）` };
  }
  if (cls.kind === 'running-ok') return { action: 'none', why: '在跑，不动手' };
  if (cls.kind === 'unknown') return { action: 'none', why: '判不出来，只记录' };
  if (cls.kind === 'running-after-error') return { action: 'none', why: '上条链出过错但仍在跑 ⇒ 观察' };
  // 队列体检：启用的任务里没有肉鸽/自定任务（例如 2026-09-23 实测 l-7 只勾了「开始唤醒」）
  // ⇒ 发了车也只是进游戏发呆，白占一台机器。这种情况一律不动手，只留痕。
  if (ctx.queueOk === false) {
    return { action: 'none', why: `队列里没有肉鸽（启用: ${(ctx.queueList || []).join('/') || '无'}）⇒ 不抢、不发车` };
  }

  const hist = e.hist || [];
  // ⚠️⚠️ 2026-09-24 02:45 **最重要的修正**：环境事件 ≠ 每台机器各自坏了。
  // 当晚 02:10–02:35 主机侧瞬时断连 ⇒ 4 台同时卡 `Roguelike@Begin`；而"1 小时救 3 次就交人工"的闸
  // **逐台把 l-1/l-7/l-4 静默了 2 小时** —— 结果 5 台里 3 台停着，用户当场指出「**你并没有看住**」。
  // ⇒ 判定集群/环境事件（同一轮 ≥2 台处于故障形态）时：
  //    ㈠ **不交人工**（这不是单机毛病，静默等于放弃）；㈡ 直接上**重动作**（断连屏只能靠关客户端清掉）；
  //    ㈢ 只保留短冷却，避免热循环。等环境恢复后它们自然回到 running-ok。
  const failureKind = (k) => k !== 'running-ok' && k !== 'running-after-error' && k !== 'unknown'
    && k !== 'parked' && k !== 'process-gone' && k !== 'idle-clean';
  // 🌀 **空转闸（relapse）**：同一形态反复复发时**升级动作强度**，而不是原地重复（2026-09-24 15:16–15:40 l-2 实测：
  //    形态2 假完成每 4~6 分钟复发一次、每次救援都被复核"有效"，于是阶梯永远停在 killgame-restart，烧动作不刷号）。
  // ⚠️ 放在「环境事件」之前：l-2 那种机器**即使旁边还有一台在抖**（集群=2），它自己的连着复发也是单机毛病，该升就升。
  // ⚠️ 只算「**连着**复发」：相邻两次同形态动作间隔 <15 分钟才算一轮空转 —— 同一形态每隔 20~30 分钟犯一次、
  //    每次轻动作都救活了（09:45 那两条老夹具）不算坏机器，"救得活就别停手"那条教训还得守。
  // ⚠️ 再加一道 `(e.ineffective||0)===0`（上次救援**被复核有效**）：真的是环境事件时救援会被判"无效"，
  //    于是这里不插手，交给下面那条集群规则（它专门为环境事件而设）—— 两类情况的判据互不重叠。
  const gapMs = e.lastActionAt ? nowMs - new Date(e.lastActionAt).getTime() : Infinity;
  const gap = gapMs / 60000;
  const sameKindMs = hist
    .filter((x) => x.kind === cls.kind && nowMs - new Date(x.ts).getTime() < 3600e3)
    .map((x) => nowMs - new Date(x.ts).getTime())
    .sort((a, b) => b - a); // 由远到近
  let tight = 0;
  for (let i = 1; i < sameKindMs.length; i++) {
    if ((sameKindMs[i - 1] - sameKindMs[i]) / 60000 < (ctx.relapseTightMin || 15)) tight++;
  }
  const relapseKind = FAIL_KINDS.has(cls.kind) && (e.ineffective || 0) === 0 && (ctx.force || gap >= 3);
  if (relapseKind && tight >= (ctx.relapseHuman || 5)) {
    return { action: 'needs-human', why: `同一形态「${cls.kind}」连着复发 ${tight + 1} 次、间隔都 <15 分钟（连模拟器级重启都没治住）⇒ 交人工 + 现场图` };
  }
  if (relapseKind && tight >= (ctx.relapseLimit || 2)) {
    return { action: 'restart-emulator', why: `同一形态「${cls.kind}」连着复发 ${tight + 1} 次、每次间隔 <15 分钟（救活几分钟又犯）⇒ 升到模拟器级重启` };
  }

  const clusterMode = (ctx.clusterSize || 0) >= 2;
  if (clusterMode && failureKind(cls.kind)) {
    return { action: 'killgame-restart', why: `环境事件（同一轮 ${ctx.clusterSize} 台同时异常）⇒ 不交人工，直接关客户端重拉` };
  }
  const sameKind = hist.filter((x) => x.kind === cls.kind && nowMs - new Date(x.ts).getTime() < 3600e3).length;
  const rescueKind = (k) => k !== 'parked' && k !== 'process-gone' && k !== 'unknown';
  const totalRescues = hist.filter((x) => rescueKind(x.kind) && nowMs - new Date(x.ts).getTime() < 3600e3).length;
  // ⚠️⚠️⚠️ 2026-09-24 09:45 **第四次修正（它又造成了一次停机）**：用户报「有一些好像卡住了」——
  // 查下来 **l-1 被我自己的"交人工"静默按死了 31 分钟**（09:14 判"1 小时救 3 次 ⇒ 静默 2h"），
  // 而它的画面明明好得很（停在萨米探索页、链干净结束、只是没人点开始）。
  // ⇒ **判据应该看"救得活不活"，而不是"救了几次"**：环境抖动会让一台机器一小时被救很多次，
  //   但每次都救回来了（`rescue-ok`）⇒ 它没坏，**不该停**。只有**连续救不回来**才交人工。
  //   所以：删掉"按次数"的两道闸（sameKind / totalRescues），改成**按无效次数**。
  const ineffective = e.ineffective || 0;
  if (rescueKind(cls.kind) && ineffective >= (ctx.ineffectiveLimit || 2)) {
    return { action: 'needs-human', why: `连续 ${ineffective} 次救援**经复核无效**（同形态仍在犯）⇒ 这台是真坏，交人工静默 2h` };
  }

  if (!ctx.force && gap < 3) return { action: 'none', why: `冷却中（距上次动手 ${Math.round(gap)} 分钟）` };


  switch (cls.kind) {
    case 'parked':
    case 'process-gone': {
      // 发车两次都没起来（模拟器起不来 / 登录态掉了）⇒ 别再空烧，交人工
      if ((e.startFails || 0) >= 2) {
        return { action: 'needs-human', why: '发车两次都没起来（多半是模拟器/登录态的问题）' };
      }
      if (!ctx.force && gap < ctx.startGapMin) return { action: 'none', why: `距上次动手 ${Math.round(gap)} 分钟（<${ctx.startGapMin} 分钟，先等）` };
      return { action: 'start', why: cls.kind === 'parked' ? '停着没在刷' : '进程刚消失' };
    }
    case 'login-lost':
      // 有效动作是"关掉游戏客户端再重拉"：只重拉 MAA 会反复撞同一个掉线弹窗（实测 3 次全无效）
      return { action: 'killgame-restart', why: '掉线弹窗 ⇒ force-stop 游戏客户端 + 重拉（只重拉 MAA 无效）' };
    case 'client-gone': {
      // 客户端重拉要 ~1 分钟；而且 adb 偶发读不到（探针已把"读不到"标成未知，不会落到这里），
      // 所以要求**连续 3 轮**（≈6 分钟）才动手 —— 2026-09-23 22:42 就因为只判 2 轮误杀过 l-5 的启动链。
      if ((e.gameGoneStreak || 0) < 3) {
        return { action: 'none', why: `客户端不在第 ${e.gameGoneStreak || 0} 轮（连续 3 轮才动手）` };
      }
      return { action: 'restart-maa', why: '客户端不在（MAA 空转）⇒ 重拉 MAA，让 StartUp 把客户端起回来' };
    }
    case 'not-foreground': {
      // 客户端还在，只是被切走了 ⇒ 拉回前台就行，**不用重拉**（比重启便宜得多）
      if ((e.fgStreak || 0) < 2) {
        return { action: 'none', why: `前台非游戏第 ${e.fgStreak || 0} 轮（连续 2 轮才动手，怕误判系统弹层）` };
      }
      return { action: 'focus-game', why: cls.why };
    }
    case 'entry-stuck':
      // 形态1：取证证明 L1/L2/L3 都无效 ⇒ 一次"关游戏 + 重拉"，再犯交人工
      return { action: 'killgame-restart', why: '形态1 放弃确认弹层粘住' };
    case 'fake-complete':
      // 形态2：**绝不能**判 done-for-day（旧看守最大的坑）
      return { action: 'killgame-restart', why: '形态2 假完成（看着正常，其实死了）' };
    case 'custom-stuck':
      return { action: 'restart-maa', why: '形态3 自定任务/MiniGame 自环' };
    case 'entry-error':
    case 'retry-exhausted':
    case 'idle-after-error':
      // **阶梯升级**（2026-09-24 02:25 实测）：4 台同时断连（官服「联系中断」/日服登录弹窗），
      // 轻动作 `restart-maa` 反复无效（画面根本没变）。既然"时效复核"已经知道上一次同形态**无效**
      // （`e.ineffective>=1`），就别再重复同一个轻动作 ⇒ 升级为 `killgame-restart`（force-stop 客户端再重拉，
      // 01:25 已验证过一次见效）。
      if ((e.ineffective || 0) >= 1) {
        return { action: 'killgame-restart', why: `${cls.kind} 且上次轻动作已被复核为**无效** ⇒ 升级为关客户端重拉` };
      }
      return { action: 'restart-maa', why: `${cls.kind}：重拉 MAA` };
    case 'idle-quiet':
      return { action: 'restart-maa', why: '日志长时间静默、无报错也无结束横幅 ⇒ 按停了处理' };
    case 'idle-clean':
      return { action: 'restart-maa', why: '链干净结束但没在刷 ⇒ 接着刷' };
    case 'client-gone-unknown':
      return { action: 'none', why: '未知' };
    // 复核 F4：机队原来**根本没接**"零进展"判据（`stuckMin` 算了不用）⇒ 日志一直在写但同一任务卡住的
    // 机器（正是旧 rogue_watch 漏掉的那类：l-7 卡 MiniGame 4.8 小时）会被放生。
    // 放在 default 前统一处理：只要判成"在跑"但同一 cur_task 卡够久，就重拉。
    case 'running-ok':
      return { action: 'none', why: '在跑，不动手' };
    default:
      return { action: 'none', why: `未覆盖的形态 ${cls.kind}` };
  }
}

