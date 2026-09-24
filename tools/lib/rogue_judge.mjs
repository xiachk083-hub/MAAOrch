/**
 * lib/rogue_judge.mjs —— 肉鸽"卡住"判读内核（被两个看守共用，单独成文件是为了能自测）
 * ──────────────────────────────────────────────────────────────────────────────
 * 判据来自 2026-09-23 的真实取证（`runtime/fleet_forensics_20260923.txt`）：
 *   · **只看 mtime 是不够的**：MAA 可以一边每秒写日志、一边零进展卡 4.8 小时。
 *   · **"任务已全部完成！" ≠ 成功**：gui.log 里它永远紧跟「任务出错」（差 ~0.5s）；
 *     要分清 asst.log 的 `TaskChainError` 与 `TaskChainCompleted`/`ExceededLimit`（后者＝假完成）。
 *   · 进程在、日志静默，是**最常见的死法**（旧看守正好在这里放生机器）⇒ 必须回看 gui.log 最后一行。
 * 自测：`node tools/rogue_judge_selftest.mjs`（拿真实日志片段当夹具）。
 */

/** 从 `[2026-09-23 13:30:36.368][ERR]...` 里取毫秒时间戳 */
export const tsOf = (line) => {
  const m = String(line || '').match(/^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})/);
  return m ? new Date(m[1].replace(' ', 'T')).getTime() : 0;
};

/**
 * 给一台机器的采样记录判"形态"。
 * @param rec {{proc:number, asstAge:number, asstLast:string[], guiLast:string[], task?:string,
 *              retry?:number, retryTimes?:number, retryExhausted?:boolean}}
 * @returns {{kind:string, why:string}}
 */
export function classify(rec) {
  if (!rec) return { kind: 'unknown', why: 'no-sample' };
  const asst = (rec.asstLast || []).join('\n');
  const guiLines = rec.guiLast || [];
  const gui = guiLines.join('\n');
  const lastGui = guiLines.slice(-1)[0] || '';
  const guiAgeMin = lastGui ? (Date.now() - tsOf(lastGui)) / 60000 : Infinity;
  const logQuiet = rec.asstAge === undefined || rec.asstAge < 0 || rec.asstAge > 600;
  // 半静默：MAA 正常时 asst.log 每秒都在写 ⇒ 3 分钟没写基本就是"停了"。
  // 2026-09-23 23:52 实测：l-2 的链 23:45 出错并停住，但 600s 阈值要等到 23:56 才动手（白等 10 分钟）。
  const looksStopped = rec.asstAge >= 0 && rec.asstAge > 180;
  // 5 分钟没写 + 没有报错也没有结束横幅 ⇒ 复核 F3 说的那种"看着在跑其实停了"，按停了处理
  const silent5 = rec.asstAge >= 0 && rec.asstAge > 300;
  // task 优先取调用方解析好的；没有就自己从 asst 尾部里找**最后一条** cur_task
  // （复核 F2：探针窗口经常取不到 cur_task，192/320 条为空 ⇒ 不能只依赖调用方）
  const task = rec.task || (() => {
    const all = [...asst.matchAll(/"cur_task":"([^"]*)"/g)];
    return all.length ? all[all.length - 1][1] : '';
  })();

  if (!rec.proc) {
    if (logQuiet) return { kind: 'parked', why: `进程不在（日志 ${rec.asstAge}s 没动 ⇒ 早就停了）` };
    return { kind: 'process-gone', why: '进程刚没了（日志刚还在写）' };
  }
  // 形态：**掉线弹窗**（日服「ログインに失敗しました。再度お試しください」）。
  // 2026-09-24 01:00 实测（l-7）：画面静止在该弹窗上，MAA OCR 到 → 匹配 `OfflineConfirm` 0.998 →
  // **`Action: Stop`**（MAA 新版"检测到掉线弹窗后直接停止任务"）⇒ asst.log 立刻静默、gui 打
  // 「游戏掉线，停止任务」。画面三张现场图 md5 完全相同（12–18 分钟一像素没变）。
  // ⇒ **只重拉 MAA 是无效的**（重新 StartUp 又会看到同一个弹窗、2.5 秒后再 Stop）。
  // 有效动作＝关掉游戏客户端（`am force-stop`）再重拉 —— 用户要看画面就是这个原因。
  if (/游戏掉线|OfflineConfirm/.test(gui) && (looksStopped || /已停止/.test(lastGui))) {
    return { kind: 'login-lost', why: '掉线弹窗（MAA 检测到掉线会直接停止任务；只重拉 MAA 无效）' };
  }
  // 进程在、日志静默 ⇒ MAA 停在原地（链已结束/卡死）。不能因为"读不出 cur_task"就放过。
  if (logQuiet) {
    if (guiAgeMin < 12 * 60 && /任务出错/.test(lastGui)) {
      return { kind: 'idle-after-error', why: `日志静默 ${Math.round(rec.asstAge / 60)} 分钟，gui 最后一行是「任务出错」（${Math.round(guiAgeMin)} 分钟前）` };
    }
    if (guiAgeMin < 12 * 60 && /任务已全部完成|已停止/.test(lastGui)) {
      return { kind: 'idle-clean', why: `日志静默 ${Math.round(rec.asstAge / 60)} 分钟，gui 显示链已结束（${Math.round(guiAgeMin)} 分钟前）` };
    }
    // ⚠️ 2026-09-24 复核 F3：这里原来返回 `unknown`，而 planMachine 对 unknown 恒为 none ⇒ **永久放生**。
    // 10 分钟完全没写日志、gui 又给不出结论 ⇒ 按"停了"处理（动手重拉），别再留一条合法放生通道。
    if (guiAgeMin < 12 * 60) {
      return { kind: 'idle-quiet', why: `日志静默 ${Math.round(rec.asstAge / 60)} 分钟、gui 也无结论 ⇒ 按已停处理` };
    }
    return { kind: 'unknown', why: `进程在、日志 ${rec.asstAge}s 没动、gui 也读不出结论（>12h 的陈迹）` };
  }
  // 客户端不在（但 MAA 还"在跑"）—— 2026-09-23 实测的新形态。
  // ⚠️ 两条硬约束（2026-09-24 独立复核的 F1/F2，都会被误杀）：
  //   ㈠ `task` 必须读得到（探针 3 行窗口经常取不到 cur_task，192/320 条为空）——读不到就不敢判；
  //   ㈡ 启动阶段（`StartUp@…`/`StartUpBegin`）客户端本来就还没起来（MAA 正自己拉），不算故障。
  const inStartUp = /^StartUp/i.test(task);
  if (rec.game === false && task && !inStartUp) {
    return { kind: 'client-gone', why: '游戏客户端进程不在（MAA 可能在空转）' };
  }
  // 客户端在、但**前台不是游戏**（被切到桌面/别的应用）⇒ MAA 截到的是别的画面，等于瞎点
  if (rec.fgOk === false) {
    return { kind: 'not-foreground', why: `前台是 ${rec.fg || '别的应用'}（不在游戏里）` };
  }
  const chainErr = lastChainFrom(guiLines).outcome === 'error';   // 只看**最后一条链**里的出错（见 B 段自测：老口径会被历史噪声误伤）
  // 链已出错 + 日志半静默（≥3 分钟没写）⇒ 当它已经停了，直接按"出错停住"处理，别干等 10 分钟
  if (chainErr && looksStopped) {
    return { kind: 'idle-after-error', why: `链已出错且日志静默 ${rec.asstAge}s（≥180s 视为已停）` };
  }
  const taskErr = /TaskChainError/.test(asst);
  // 5 分钟没写日志 ⇒ 不管 gui 是「已停止/任务已全部完成」还是给不出结论，都按"停了"处理。
  // ⚠️ 2026-09-24 night-watch 实测的**放生缝**：原来这里排除了「已停止」，而 `logQuiet` 分支又要 >600s
  // ⇒ 300–600s 之间直接落到 `running-ok`。l-7 01:02–01:10 连续 5 轮（85s/205s/325s/446s/565s）就是这么
  // 被放过的，直到 685s 才救 —— 白放生 7 分钟。现在两个分支合并：有结束横幅 ⇒ idle-clean，否则 idle-quiet。
  if (silent5 && !chainErr && !taskErr) {
    if (/任务已全部完成|已停止/.test(lastGui)) {
      return { kind: 'idle-clean', why: `日志静默 ${rec.asstAge}s + gui 显示链已结束 ⇒ 接着刷` };
    }
    return { kind: 'idle-quiet', why: `日志静默 ${rec.asstAge}s、无报错也无结束横幅 ⇒ 按已停处理` };
  }
  // ⚠️ 2026-09-24 复核修正：`exec times exceeded` 在**子步骤级**也会出现（MAA 正常跳过语义，如
  // `StageTraderInvestSystem` 的 exec_times=0/max_times=0）。单看这串字会把"正常在刷"判成假完成
  // ⇒ 机队会直接"关游戏+收 MAA 再发车"=**误杀**。必须有**链级**的 `TaskChainCompleted` 才算形态2。
  const lastChain = lastChainFrom(guiLines);
  const chainClean = lastChain.outcome === 'clean' || lastChain.outcome === 'stopped';
  // ⚠️ 2026-09-24 night-watch 用真实采样复现的**剩余假阳性**：l-7 队列是 `[StartUp, Roguelike]`，
  // **每次 MAA 启动时 StartUp 链正常完成**都会在 asst 窗里留 `TaskChainCompleted {"taskchain":"StartUp"}`
  // ⇒ 光看"有 TaskChainCompleted 且无 TaskChainError"会把**健康机**判成假完成，而这一形态的动作是
  // `killgame-restart`（会 force-stop 游戏客户端）＝最重的误杀。
  // 用**决定性的信号**区分（16:54 真事故 vs 16:57 假阳性两段真实采样验证）：
  //   ㈠ 完成的链名不是 `StartUp`（StartUp 完成是正常流程）；㈡ 日志**已经停了** >30s（链结束、MAA 不再写）。
  // 日志还在写的那一瞬（asstAge 很小）不动手 —— 那种情况交给 3 分钟后的 `idle-after-error`（动作更轻：
  // 只重拉 MAA，不关游戏），宁可轻一点，也不要在健康机上 force-stop 游戏。
  const doneChains = [...asst.matchAll(/TaskChainCompleted[^\n]*"taskchain":"([^"]+)"/g)].map((m) => m[1]);
  const lastDone = doneChains.length ? doneChains[doneChains.length - 1] : '';
  const fakeComplete = !taskErr && lastDone && lastDone !== 'StartUp' && rec.asstAge > 30;
  if (fakeComplete) {
    return { kind: 'fake-complete', why: `链 ${lastDone} 干净完成、日志停了 ${Math.round(rec.asstAge)}s（gui ${chainClean ? '显示已全部完成' : '未给出结论'}）⇒ 假完成` };
  }
  // 形态3 自定任务/MiniGame 自环：**必须**要有"确实在打转"的证据（重试耗尽或链已报错）。
  // ⚠️ 2026-09-24 复核 F6：队列闸允许 Custom（用户可能切卫戍协议），但旧判据只要看到 Custom/MiniGame@
  // 就判自环 ⇒ 会去杀用户正要刷的负载。正常运行中的 Custom（retry 3/20）**不算**。
  const customish = /MiniGame@|taskchain":"Custom/.test(asst);
  if (customish && (rec.retryExhausted || taskErr || chainErr)) return { kind: 'custom-stuck', why: '自定任务/MiniGame 卡住（重试耗尽或链报错）' };
  if (taskErr && /AbandonConfirm/.test(asst)) return { kind: 'entry-stuck', why: '放弃确认后 StartExplore 重试耗尽（形态1）' };
  if (taskErr) return { kind: 'entry-error', why: 'TaskChainError' };
  if (chainErr) return { kind: 'running-after-error', why: '上条链出过错但仍在跑' };
  if (rec.retryExhausted) return { kind: 'retry-exhausted', why: `重试 ${rec.retry}/${rec.retryTimes} 耗尽` };
  // 兜底：调用方没给 retryExhausted（机队探针以前不产出该字段 ⇒ 这条判据曾是死枝）时自己算
  if (rec.retry >= 0 && rec.retryTimes > 0 && rec.retry >= rec.retryTimes) {
    return { kind: 'retry-exhausted', why: `重试 ${rec.retry}/${rec.retryTimes} 耗尽（判据自算）` };
  }
  return { kind: 'running-ok', why: task || '在跑' };
}

/**
 * 从 gui.log 的行数组里读出"最后一条任务链"：开始时刻 / 结局 / 出错的任务名。
 * 结局取值：running（还在这条链里）/ error / clean / stopped / unknown。
 * 注意：`任务已全部完成！` 与 `任务出错` 常常只差 0.5 秒 ⇒ 只要这段里出现过「任务出错」，就算 error。
 */
export function lastChainFrom(lines) {
  const arr = (lines || []).filter(Boolean);
  let startIdx = -1;
  for (let i = arr.length - 1; i >= 0; i--) {
    if (/开始任务: /.test(arr[i])) { startIdx = i; break; }
  }
  if (startIdx < 0) return { startAt: null, outcome: 'unknown', errors: [] };
  const seg = arr.slice(startIdx);
  const m = seg[0].match(/^\[([\d-]+ [\d:.]+)\]/);
  const startAt = m ? m[1] : null;
  let outcome = 'running';
  const errors = [];
  for (let i = 0; i < seg.length; i++) {
    if (/本轮任务出现以下错误：/.test(seg[i])) {
      outcome = 'error';
      for (let j = i + 1; j < seg.length; j++) {
        const t = seg[j].trim();
        if (!t || /^\[/.test(t)) break;
        if (!errors.includes(t)) errors.push(t);      // 出错块里的名字常与上面「任务出错: X」重复，去重
      }
    } else if (/任务出错: /.test(seg[i])) {
      outcome = 'error';
      const t = seg[i].match(/任务出错: (.+)$/);
      if (t && !errors.includes(t[1].trim())) errors.push(t[1].trim());
    } else if (/任务已全部完成！/.test(seg[i])) {
      if (outcome !== 'error') outcome = 'clean';
    } else if (/已停止/.test(seg[i])) {
      if (outcome === 'running') outcome = 'stopped';
    }
  }
  return { startAt, outcome, errors };
}
