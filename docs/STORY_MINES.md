# 主线（主题曲）打关手册

> 目的：主线是**最大的矿**（缺口 79 星；planner-1 估剩余源石 **[28,79]**），但入口/判据和活动完全不同。这份是"要打主线时照着做"的清单，避免重复踩坑。

## 一、入口链（已由 `tools/main_nav.py` 实现）

```
大厅 → 终端(967,160) → 主题曲 → 章节地图 → EPISODE 页签(滑动窗口) → 关卡节点 → 关卡详情页
       ↑ 两个入口坐标已改为"学一次"：runtime/learned/story_nav.json
         （--learn-entry terminal|theme_card 视觉学 / --set-entry terminal=967,160 手写）
       ↑ 校验：进详情页后用**面板上的关卡代号**核实"确实是这一关"，返回 {ok,on_page,stars,reward_primes,cleared}
```
命令：`python tools/main_nav.py --code 7-2` / `--code H8-1`

## 二、判据纪律（主线专属的坑）

| 事项 | 结论 |
|---|---|
| **地图节点颜色** | **主线地图的节点颜色不代表星级**（第一章 46/46 全清，节点仍是灰六边形；`runtime/_y.png` + 视觉模型证据 `runtime/reports/_ocr_y_nodes.json`）⇒ **不要**用活动那套"蓝=三星"判据 |
| **章节进度从哪看** | **完成进度页的章节卡**（`73/82`、`62/79`…，`runtime/_x.png`）——那是"本章三星总数/总星数" |
| **单关状态从哪看** | **章节关卡列表那一屏**（完成进度 → 点章节卡），看每关三颗星；**不是**章节地图那一屏 |
| **有没有矿** | 唯一判据：**点进关卡详情页**看 `reward_primes`（徽章在=源石未发）。`stars==0 且 cleared==False`（从没打过）⇒ 几乎必有首通源石 |
| **突袭/磨难** | `main_nav --raid`；它们各有 1 星，首通也给 1 颗源石 |

## 三、打关流程（和活动一致：先试轴，再真打）

1. **预扫**：进入某章关卡列表后，逐关点进详情页读面板（`tools/scan_map_panels.py` 同款思路），列出"真有矿"的关；
2. **选轴**：主线作业落地约定 `runtime/plans/story_jobs/<代号>_<id>.json`（copilot 原样 + 文件名带关卡代号，`stage_attempt.py` 从文件名取代号）；
3. **演习试轴（0 理智）**：`python tools/stage_attempt.py --job runtime/plans/story_jobs/H8-1_<id>.json --tag h81_<id> --drill`
4. **真打**：同轴去掉 `--drill`；`--abort-on-leak` 默认开（漏怪立刻掐，2★ 不给源石，别浪费理智）；
5. **复核**：结算页星数（`result_stars`，必须 3）+ **大厅源石数 +1**（顶栏读数，肉眼/视觉模型）→ 记 `runtime/stone_ledger.json` + `runtime/axis_outcomes.jsonl`。

## 四、优先打哪几关（planner-1 建议）

`H8-1 → H8-2/3/4 → H9-1 → H11-1 → H7-1`
理由：**H 关理智只要 10**、每关 3 星 + 首通 1 颗源石，且是各章最后放出的内容、**最可能没打过**；EP08（缺 17 星）缺口最大。

## 五、当前状态（2026-09-24）

- 入口链：`main_nav.py` 已存在并被扩展（坐标可学）；**离线自证 17/17**
- 队列接入：`runtime/_prime_queue3.py --with-story` ⇒ 307 个主线单位并入同一 EV 排序；主线走独立 `story_enter()`，有本地轴时**默认只演习**，`--story-run` 才真打
- **尚未真机验证**：`main_nav --code 7-2` 与 `H8-1` 一次都还没在真机上跑过 ⇒ 打主线前必须先跑这一步（只读、不花理智）
- 缺：每关的轴（`runtime/plans/story_jobs/` 还没有文件）
