# MAS 班次自动化缺口清单（按优先级）

> 来源：卡 `t_9d24454f` 端到端演练（2026-09-27 23:07–23:55，只读/dry-run；完整剧本见 `docs/MAS_SHIFT_PLAYBOOK.md`）
> 口径：**优先级 = 对「标准班次无人化」的阻塞程度 × 出现频率 × 修复成本**。
> 每条都带本次演练的实测证据；建议落点写成「改哪里」，不是泛泛而谈。

## 修复顺序建议（一条最短路径）

```
G2（一行×N：绕代理）→ G1+G6（接线 mas-observe + 刷新 cron 工具面）
→ G3（补跑判定落地，先拍板 3 项）→ G4（preflight 接入，依赖 G2/G9）
→ G5/G7（通道加固 + Y3 缺口检测）→ 其余
```

---

## P0 —— 不修则「无人化」是假的

### G2. 系统代理拦截内网 ⇒ 全部 urllib 工具间歇 502（今晚多个失败的总根因）
- **现象**：对 `http://100.79.173.69:36163` 的请求间歇返回 `502 Bad Gateway`（空 body、每次卡 5.0s）或 `10054 连接重置`；curl 不受影响。
- **证据（本卡 A/B 实测，23:55）**：`getproxies()={'http':'http://127.0.0.1:7890',…}`；默认 urllib **2/6 次 502**，显式 `ProxyHandler({})` **6/6 全 200**。跨卡复核（t_52ebdda4，经 t_7d630aa6 转述）：绕代理后连续 20/20 全 200。curl 只认环境变量代理、不读系统代理 ⇒ 此前「curl 好、urllib 坏」的怪现象即此。
- **影响面**：`grep -l urlopen tools/*.py services/*.py` = **62 个文件，仅 1 个（`mas_stage_fix.py`）显式绕代理**；今晚实证命中：`mas_preflight`（3 次误 NO-GO）、`mas_rerun` MCP 通道（3 连败后才改手动）、`mas_switch_gate`（rest 元数据缺失）等。
- **建议落点**：统一一个 `mas_net.py`（或每工具一行）：`opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))`；优先修关键链路五件套 `mas_preflight.py / mas_dispatch.py / mas_maaorch_daily_proxy.py(或 mas_l3) / maa_error_daily.py / mas_switch_gate.py` + MCP server 侧 adapter。
- **红线**：**不要去改 AUTO-MAS 侧配置**（不是它的问题）。
- **现状**：未修（本次换到手动路径 + 重试兜底）。

### G1. 巡检三个观察行（CLIENT_STATE / POWER / IDENTITY）未接线，恒为 UNKNOWN
- **现象**：巡检报告 3 行永远 `UNKNOWN:工具面未提供`；演练中这 3 类信息只能靠人补探针。
- **证据**：23:03 巡检输出（t_562840d3 工件）；`docs/MCP_TOOL_ROADMAP.md` §2–§4 已给出 5 个只读工具与接线 YAML（**先不改**）；等价 CLI 都已存在且实跑过（`client_state_check.py` / `mas_idle_emulators.py` / `mas_identity_trace.py`）。
- **建议落点**：新建只读 stdio server `mas-observe`（5 工具）→ `platform_toolsets.cron` 加一行；巡检提示词已按名写好消费语句（无需再改提示词）。
- **现状**：设计文档齐、代码齐、接线未做。

---

## P1 —— 高频人肉 / 误判风险

### G3. 补跑判定（C3 规则）没有单一工具实现；triage 至今会串号误判
- **现象**：`triage_error(hbznn119-日)` 今日实测返回 `kind=startup_failed`（**错**，取的是同目录他号的 Px26660 行）；按规则应判 N1「不补」。判定全靠人对照 `MAS_RERUN_POLICY.md`。
- **证据**：本卡 23:44 triage 复现；policy §6.1（该号 12/15 条资源类、7 次补跑 0 成功）；`MAS_RERUN_TOOLCHAIN_DRAFT.md` 草案已写好四处 diff（未实施）。
- **建议落点**：按草案落地 `tools/mas_rerun_policy.py`（单一判定源）→ `triage_error` 加 `rerun_verdict/rerun_rule/rerun_evidence`（按账号收敛）→ `mas_rerun` 预演返回 `verdicts[]/blocked[]` → 巡检提示词消费 `RERUN_VERDICT`。
- **前置**：policy §8 三项拍板（1–8 号队列批量补否 / 一天一成功 vs 一班一成功 / N 值）。
- **现状**：未实施；演练中判定 4 例全靠人（结果写进 playbook §6）。

### G4. preflight 未接进任何流程（发车前/巡检/班前哨兵）
- **现象**：6 步链路里第①步没有任何人自动调用；调度器发车前不做体检。
- **建议落点**：`docs/audits/2026-09-27_mas_preflight_wiring.md` 三方案——A) `mas_dispatch.dispatch()` 发车前调用（每队列每天 ≤1 次，NO-GO 拒发、`--force` 人工绕）；B) 巡检 MCP 只读包装；C) 班前哨兵 cron（04:55/19:25，`--skip-screen` 批量汇总）。
- **依赖**：先修 G2（否则误 NO-GO 会拦发车）、G9（总时限）。
- **现状**：CLI 可用，接线未做。

### G5. 劣化通道下「全量拉日志」类工具不可用（identity report / error_daily 今晚双双失败）
- **现象**：`mas_identity_trace.py report` 两次 `TimeoutExpired`（拉 `MAA_6\gui.log` >300s）；`maa_error_daily.py` 一次 >300s 静默、一次 `UNKNOWN:ssh 超时`（fail-closed 正确）。
- **证据**：23:38/23:43/23:50 运行记录；文件尺寸 `gui.log`=2.34MB / `asst.log`=41MB / `app.log`=12.6MB；2.34MB 文件 `ssh type` >200s 未拉完（有效带宽 <12KB/s，同期多 worker 共用通道）。
- **建议落点**：pull 层加固——`ssh -C`（压缩）/ gzip / 增量（记录 offset+tail）/ 字节与秒双上限；工具已支持 `--logs` 本地缓存路径，可加“本地缓存目录”约定；巡检侧同类拉取加错峰/单飞标记。
- **现状**：未加固；健康通道下这些工具可用（22:00 曾有正常输出）。

### G6. 巡检 cron 工具面刷新滞后（error_daily 未加载；mas-observe 未接）
- **证据**：23:03 巡检 `ERR_SUMMARY=UNAVAILABLE:工具未加载`（t_562840d3/t_d054ba36 记录；需 gateway 刷新）。
- **建议落点**：卡 `t_6cfe7865` 已建——随 G1 的一次刷新一起做，避免两次重连。
- **现状**：已建卡、未执行。

### G7. Y3 缺口（never_ran）没有自动检测入口——今晚 43 号靠人肉发现
- **现象**：`mas_watch` 自动补跑只看 `failures`（最后一条非 Success），**截断/孤儿化号不在其中**（在 `never_ran`，只报告不补跑）。
- **证据**：policy §2.3 注 + §6.2；今晚实测 `never_ran=43`、队列 1–8 `done=0`，全部由 t_7d630aa6 手工串行处理。
- **建议落点**：policy §7.3 契约——`mas_status.never_ran` 非空 + 该队列本班次被派发过 + 重启/无收尾证据 ⇒ 按 Y3 组队补（≤8/轮、先 P1/P3 体检）；落点候选：巡检第六步（只读报告版）或 `mas_watch`。
- **现状**：未实施（他卡手工兜底中）。

---

## P2 —— 能力/健壮性

### G8. 判定口径三处一致靠人：dispatch 冲突判定 / rerun policy / 巡检提示词
- **现状**：`mas_dispatch` 侧已加固（resources/busy 窗口/单飞）；policy 与巡检侧待**单一判定源**（G3 落实后自然收敛）；本次演练未发现新的口径冲突，但人工保持一致的窗口风险仍在。

### G9. preflight 健壮性：无总时限、无内部重试、截图偶发失败
- **证据**：本次 7 连试中——1 次 >300s 无输出（被外部杀掉；默认 `--timeout 240` 叠加劣化通道）；「screencap 无有效 PNG」2 次（idx11/idx25/idx58 共 3 实例各 1 次）。
- **建议落点**：加 `--deadline`（整单上限，超时按 NO-GO|UNKNOWN 输出）；REST 探测重试 3 次退避；截图单实例重试 1 次后转 SKIP 并注明。

### G10. 报告交付面：巡检 deliver=local、日报 `latest.md` 陈旧
- **证据**：演练中发现 `mas_status.report.latest.md` 的 mtime=09-22（四天前）；cron job 52b88b0ffca0 与 2600f6415f16 都是 `deliver=local`（日志里存着，但没人被通知）。
- **建议落点**：`mas_report`（ROADMAP §2.2 全量版未建）+ 给巡检/发车加一条「异常时的投递通道」（如 deliver=telegram 仅异常时推）。属产品决策，建议与 lead 确认。

### G11. 临时补跑队列残留清理无自动收尾
- **证据**：调度地图 §5.2——9 条「临时-补跑-*」残留（最老 0923）；残留会占目录、误发车即抢目录；`auto_mas_rerun.py --cleanup` 需人工拍板。
- **建议落点**：巡检加一行「残留计数 + 候选清单」（只报）；定期清理走人工/单独卡。

---

## P3 —— 打磨项

### G12. 多 worker 并发无仲裁/可见性（通道与实例双层面）
- **证据**：今晚同一时段至少三路动作（t_7d630aa6 串行补跑、`stage_fix` 配置手术、本卡只读演练）+ 他会话 `MAAOrch-StartOne-25`；实证互扰 = 通道带宽骤降（G5）与「谁在飞」需现查进程。
- **建议落点**：沿用 `_preempt.flag` 让位思路为**产线实例**加「占用公告」（谁、何时、哪台/哪目录）；巡检报告加一行占用者（现在只有计数）。

---

## 附：本次演练「已验证可用」清单（防止重复排查）

- `mas_dispatch.py` 全套只读命令（`--check / --reconcile / --busy / --list / --check-schedule`）——23:08–23:47 全部实跑通过。
- `mas_preflight.py` 六项检查逻辑本身正确（健康时段 21:04 GO 样本；NO-GO 判定均对应真实原因，包括他卡已进入时的「槽位忙」防互杀）。
- `mas_switch_gate.py --check`、`mas_identity_trace.py selftest`、`mas_dispatch._` 状态自愈与告警台账——实跑通过。
- fail-closed 行为全面正确：REST 不可达 ⇒ preflight NO-GO；渠道超时 ⇒ error_daily `UNKNOWN`；工具面缺 ⇒ 巡检 `UNKNOWN`（不顶替、不猜）。
