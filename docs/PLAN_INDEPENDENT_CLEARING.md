# 独立推关路线图（外援 2026-09-19 修订版，**以此为准**）

> 本文替代此前"几何过闸 → 指派 → 最小闭环"的旧序。旧序被否的理由：
> **"全图几何完美"不是独立推关的前提** ✗；把"指派"当独立大阶段是本末倒置 ✗
> （规则先回答"需要什么能力"，指派才回答"谁来做"）。

## 0. "独立推关"的定义（第一代，别拉长战线）

**给定 level/routes 数据，系统能自己从地图结构产生部署需求 → 选满足能力的干员 → 完成部署 → 验证结果 →
人工修正后把修正沉淀为下次可复用的规则；不再需要 MAA copilot 提供逐步作业。**

第二代（**不要绑进第一代**）：不知道 level/routes、只看画面、自己认敌人、自己规划 ✓

## 1. 新顺序（七步）

1. **局部几何过闸**（2~3 格内 + 防守点语义锚，部署误差 < 0.5 格）—— 不要求全图 homography ✗
2. **最小策略闭环**：need → candidate → execute → verify
3. **强制产生一次人工修正**（没有这一步 RuleDB 是假的 ✓）
4. **repair rule → RuleDB**（第一版 RuleDB = HumanCorrections ✓，不是 AllBattles ✓）
5. **job_fit 指派**（作为闭环里的一步，不是独立阶段 ✓）
6. **扩大执行器**：状态机 / action transaction / 时间窗口 / fail-closed / 审计 trace
7. **敌人视觉**（先做一致性校验、最后才考虑检测 ✓）

## 2. 必须 / 不必（外援原表）

| 能力 | 第一阶段 | 最小做到什么 |
|---|---|---|
| 全图 similarity/homography 裁决 | **否** | 不需要 |
| 局部部署几何 | **必须** | 目标格附近 2~3 格，误差 < 0.5 格 |
| 地图语义对应 | **必须** | 至少知道"这个局部格 = 哪个逻辑格" |
| 防守点定位 | **必须** | 作为局部语义锚之一 |
| 规则库 | **必须** | 先只记录"人工修正产生的规则" |
| 自动指派 | 必须但很小 | 从候选人里按能力约束选一个 |
| 敌人视觉检测 | **可以不做** | 第一版完全依赖 level routes + 时序 |
| 敌人总数 OCR | 建议做 | 只作为时序一致性 sanity check |
| 端到端 RL / 视觉策略 | **现在不要做** | 信息增益/成本比太差 |
| 全图实时敌人跟踪 | **可以永不补** | 除非以后要摆脱 level 数据 |
| DPS/HP 精确模型 | 可暂缓 | 第一版不需要 |
| 全图高精度 3D 重建 | **可以永不补** | 只要部署局部坐标可靠 |

## 3. 执行器生产化的五件（除几何外的最小必要件）

1. **状态机**：OBSERVE → DECIDE → SELECT_OPERATOR → SELECT_CELL → CONFIRM → WAIT → OBSERVE，任何一步失败可 RECOVER
2. **Action transaction**：每个动作带 `expected_before / action / expected_after / timeout`，after 不成立**不许继续下一动作** ✓
3. **时间是区间不是数字**：`deploy at 13.42` → `valid window [12.8, 14.1]`（我们已有逐格到达时刻 ✓ 直接支持）
4. **fail-closed**：UNKNOWN / FALLBACK / geometry_bad / state_uncertain / expected_after 未见 ⇒ **STOP/PAUSE**，
   **几何不确定 ≠ 拿上一帧坐标继续点** ✗
5. **每个动作可回放审计**：timestamp / state_before / decision / operator / target_cell / screen_xy /
   expected_after / observed_after / result ⇒ 这既用于 debug，**也是未来 RuleDB 的原始数据** ✓

## 4. 第一版规则粒度（只四类，别做"完整战术语言" ✗）

```
Rule 1 need_block(cell, deadline)
Rule 2 release(cell, when)
Rule 3 need_skill(operator, condition)
Rule 4 if expected_state != observed_state: repair(...)
```
规则只描述**为什么动作**，**谁来做**交给 job_fit ✓（别把"谁"硬编码进规则 ✗）

## 5. 唯一被允许做的第一个闭环实验（外援指定）

> **一张已有路线数据的图 + 一个单堵点 + 一个部署动作 + 一次人工修正。**

```
level route → 第一个 critical cell → deadline → need(blocker, cell, deadline)
          → job_fit 选人 → 局部几何 → 点击部署 → verify
          → 失败 ⇒ 人工改一次 ⇒ 记录 delta ⇒ 生成第一条 repair rule
```
**同时不要做**：敌人检测 / 全图 homography / 多单位联合优化 / DPS / HP / RL / 自动规则归纳 ✓

## 6. 敌人视觉的三层校验（成本从低到高，够用就停）

- **L0 HUD**：暂停态 / 费用 / 生命 / 波次
- **L1 敌人数 OCR**：预测 ≈ 7 vs 观测 ≈ 7 ⇒ 超阈值即 `STATE_DRIFT`
- **L2 关键位占用**：chokepoint / 防守点邻格 / critical lane 是否明显异常
- **L3 敌人检测**：只有前三层都解释不了失败才上 ✓

## 7. 我们的现状与 Gate A 预检（本轮实测，如实记录）

- **Gate A 预检失败（方法学）** ✗：我用"最大的蓝色连通块"当防守点锚 ⇒ 两张不同的图给出了**同一个坐标 (450,468)**，
  那是固定位置的**蓝色 UI 元素** ✗ ⇒ 该批 1.0~10.8 格的误差数字**全部作废** ✓
- **唯一有效的锚点数据**（此前**人工核对过**的三帧）：防守点标记 ↔ 预测格心 = **52 / 65 / 67 px**，
  除以各图投影格距（约 120~160 px）⇒ **≈0.35~0.55 格** ⇒ **正卡在 0.5 格线上** ✓（不是明显达标，也不是明显不达标）
- 我们已有的原料：777 条路线（含逐格到达时刻）/ 23 张图 / min-cut 表 / 720 帧采样库 / `job_fit` /
  `observe_job` 的逐动作回放 ✓
- 我们**没有**：敌人检测权重 ✗、几何注册表只 2 关 ✗、A-2c 全图切格卡住（**按外援指示：不继续攻** ✓）

## 8. 台账：可以"永不补"的五项（避免反复立项）

① 全图像素级几何 ② 实时敌人检测（前提：routes 可信且策略不需要敌人身份）③ 完整 3D 重建
④ 端到端策略网络 ⑤ DPS/HP 精确仿真（除非出现大量"理论上能挡、实际扛不住" ✗）

---

## 9. 2026-09-19 基线锁定（用户/外援最终确认：三条硬约束 + 六个 🚫）

**P0 序列（以此为准）**
```
局部几何过闸 → 最小策略闭环 → 强制人工修正 → HumanCorrections→RuleDB
→ job_fit（闭环内一步）→ 执行器五件套生产化 → 敌人视觉 L0→L1→L2→L3
```

**三条硬约束**
1. **不再追全图几何** ✗ —— 只证明"目标 2~3 格 + 防守点"能稳定做到 `< 0.5 格` ✓
2. **RuleDB 必须从一次真实人工修正产生** ✓ —— 没有 human correction → delta → repair rule，不得宣称已有第一代规则系统 ✗
3. **第一闭环严格缩小为**：
```
level/routes → critical cell → deadline → need(blocker, cell, deadline)
→ job_fit → local geometry → deploy → verify → human correction → repair rule
```

**两个 Gate**
- **Gate A（局部几何）**：当前唯一可信的三帧 = 52 / 65 / 67 px ⇒ ≈0.35 / 0.4x / 0.5x 格 ⇒
  **正好卡在 0.5 cell 附近，不能写成 PASS** ✓；`(450,468)` 那批因锚点是固定 UI 元素 ⇒ **整批作废，不进统计** ✓
- **Gate B（单堵点闭环）**：A 一旦给出可靠局部部署坐标，立刻做"一图 + 一堵点 + 一动作 + 一次人工修正" ✓

**冻结的六个 🚫**（除非实验证明最小闭环被其中某项实际阻塞，否则不重新立项）
```
🚫 全图 homography        🚫 全图实时敌人检测     🚫 多单位联合优化
🚫 DPS / HP 精确模拟      🚫 RL / 端到端策略      🚫 自动规则归纳
```

**下一步工单**：Step 1「设计并验收 Gate A 的局部几何」⇒ 过闸立即转 Step 2 单堵点闭环 ✓
（不要再开"独立推关还缺什么"的新单 ✗ —— 这件事已经回答完了 ✓）