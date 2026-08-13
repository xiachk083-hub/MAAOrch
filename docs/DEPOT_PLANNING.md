# 材料规划（仓库识别 + 水位计划）

> 2026-08-13 产品化：项目自动扫仓库 → 算缺口 → 出多日刷取排程，只出报告不入队。

## 1. 功能

1. **仓库识别**：MAA 官方 Depot 任务（Asst 子进程），约 16s 识别完一个账号全仓库（82 种物品实测）
2. **水位规划**：材料低于水位 → 缺口；纯刷取（用户拍板不做合成路线）
3. **多日排程**：任务按 AP 消耗升序，每日预算填满顺延，末日剩余预算补刀余量最小材料
4. **当日有效**：计划基于当次仓库识别；每日 04:30（可配）自动重扫全部非挂起账号

## 2. 识别协议（实测 2026-08-13）

- `append_task('Depot', {'enable': True})` — 需先 StartUp 到主页
- 回调 `SubTaskExtraInfo`（AsstMsg=20003），`what` 字段实测是 **`"DepotInfo"`**（文档写 `"Depot"`，代码按前缀匹配兼容）
- `details.done=true` 时 `details.data` 是 `{"物品ID": 数量}` JSON 字符串
- `item_index.json` 映射 ID → 材料名；数量为 0 的物品不输出

## 3. 规划模型

```
缺口 = max(0, 水位 − 仓库)
选关 = 该材料全量常驻备选表（config/farm_stages.json，企鹅物流掉率，
       按期望 AP/个 升序）中章节 ≤ 账号主线进度(mainline_chapter) 的第一个
次数 = ceil(缺口 ÷ 掉率)
多日 = 任务按 ap_cost 升序，每天预算 ap_budget_per_day 填满顺延
补刀 = 全部缺口排完后剩余预算（全达标=当日全部预算）刷余量最小材料
```

- **磨难关（-HARD）独立成备选条目**（2026-08-13 用户：磨难不能只有普通）：
  严格按期望AP/个排序，普通/磨难按数据自然排列；普通关排前但锁定，
  probe 顺位自然落到磨难（MAA 原生格式 `10-6-HARD`）。磨难与普通掉率、
  AP 实测相同（企鹅数据 0.759 vs 0.760）
- `mainline_chapter=0`（未填）= 不过滤，按最优关出计划（报告标可能含锁关）
- 备选表更新：`python tools/material_stages.py --dump-farm --refresh`

## 4. 组件

| 模块 | 职责 |
|------|------|
| `services/depot_reader.py` | Depot 识别（monitor 域，仿 stage_probe 子进程模式），附 run_cli |
| `services/material_planner.py` | 水位规划纯函数（queue 域），附 run_cli |
| `services/depot_scheduler.py` | DepotScanner（互斥跳过 + 免费实例 + 回收保护）+ DepotScheduler（每日定时） |
| `config/material_water_levels.json` | 17 材料水位 + ap_budget_per_day + daily_scan_time + scan_enabled |
| `config/farm_stages.json` | 全量常驻备选表（掉率降序） |

## 5. API

| 端点 | 说明 |
|------|------|
| `POST /api/depot/scan` | `{account_ids: [...]}` 异步串行扫描 |
| `GET /api/depot/status` | 各账号快照/计划时间 + 扫描状态 |
| `GET /api/depot/plan?account_id=` | 最近一次计划（多日排程 JSON） |
| `GET/POST /api/depot/config` | 水位/预算/定时配置读写 |

## 6. 代理预检（全权委托实测 — 2026-08-13）

**为什么需要**：MAA 对代理锁定的关是「静默跳过」——Fight 导航阶段识别
`UnableToAgent` 模板（tasks.json:610-620）→ 该关不刷直接走完任务链 →
退出码正常被判「完成」→ 计划白给且无报错。

**主防线**：扫描后对计划里实际要刷的关逐个 `stage_probe` 实测：
- `UsePrtsSuccess` 模板命中（实测 0.998）= 代理激活 → 可刷
- `UnableToAgent2` 命中 = 锁定 → 顺位换下一备选关再测，全锁则该材料标
  「代理实测锁定」从排程剔除
- `S-` 资源本跳过（进度到了必可刷，避免误锁）
- 缓存 `models/accounts/<id>/farm_probe.json`：CAN_FARM 长期复用（代理权
  不会丢）；CANNOT/DONE 7 天重测（推主线后可能解锁）；没空闲实例时不
  测（ok=None 不锁关，宁可计划留不确定）

**确认「刷不了」是不是代理失败的证据链**：
1. `asst.log` 出现 `UnableToAgent` 任务执行记录 → 导航阶段识别到锁（直接证据）
2. `events.log` 该关战斗次数 0 → 确实没刷（结果证据）
3. `stage_probe` 现场复判 → 画面级权威结论（UsePrtsSuccess=可刷 / UnableToAgent2=锁）
4. 运行中兜底：预检过了但还失败（罕见，如活动关提前失效）→
   `_recover_account` 恢复链 + 下次扫描自动重预检

## 7. 扫描互斥

跳过的账号：挂起 / 无模拟器绑定 / 队列运行中 / 模拟器被其他账号占用 / MAA 心跳存活（<10s）/ runner 进程在跑。
扫描借用 `.pid` 已死的空闲 MAA 实例（避开 runner `_inst_reserved`）；起模拟器打
`v2.mark_external` + `manual_emu_started` 保护标记防回收器误关（复刻 probe 的教训）。

## 7. 数据

- 快照：`models/accounts/<id>/depot.json`（最新一份覆盖）
- 计划：`models/accounts/<id>/plan.json`
- CLI 模式：`python services/material_planner.py --adb 127.0.0.1:16384 --account X --chapter 10`

## 8. 已验证

- 0 号模拟器实测：识别 82 种物品 16s，两轮独立识别完全一致，落盘零丢失
- 失败路径：ADB 连不上 → 重试 1 次 → 明确报错
- 两种报告形态：部分达标（缺口+多日排程）/ 全达标（余量最小补刀）
- 进度过滤：≤10 章时 异铁组 14-12 锁 → 顺位选 10-11（掉率 0.46，标次优）

## 9. 范围外

- 计划自动入队执行（仍「只出报告」）
- 合成路线（用户已否）
- 芯片/培养目标模型
