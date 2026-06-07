# 🧠 智能调度

## 概述

智能调度是 MAAOrch 的自动化任务决策模块。开启后，程序根据**时间、体力、材料库存**自动决定每个账号要跑什么任务，无需手动配置流水线。

## 开启方式

1. 切换到 `🧠 智能` tab
2. 勾选「启用智能调度」
3. 配置全局默认参数
4. 为每个账号设置默认关卡（空 = MAA 自行决定）
5. 点「保存」

开启后 500ms 内会自动执行一次全量检查，符合条件的账号自动入队。

## 架构

### 文件结构

| 文件 | 职责 |
|------|------|
| `smart_scheduler.py` | 决策引擎：根据配置 + 当前时间 + 账号状态决定任务列表 |
| `ui/smart_panel.py` | 智能调度 tab 的全部 UI |
| `config_ops.py` | `inject_smart()` — 将决策结果写入 MAA gui.json |
| `runner.py` | 启动时判断智能模式 → 走 smart_scheduler + inject_smart |
| `main_window.py` | `_smart_tick()` — 定时触发器，每分钟检查一次是否需要入队 |

### 决策流程

```
_smart_tick() 每分钟执行:
  对每个不在运行/排队的账号:
    基建班次到点?              → 入队
    体力 > 阈值%?              → 入队
    仓库材料低于下限?          → 入队
    
  对正在运行的账号:
    基建班次到点?              → 标记 smart_pending，跑完补入队
```

```
smart_scheduler.decide() 每次启动执行:
  固定:            Award
  04:00 班次:      Depot + Infrast + Recruit
  16:00 班次:      Infrast + Recruit + Mall
  周一 & 剿灭未满:  Fight(剿灭关卡)
  体力 > 阈值:      Fight(当天关卡/默认关卡)
  仓库缺材料:       Fight(材料映射关卡)
  收尾:            CloseDown
```

### 数据流

```
全局配置 (config["smart_global"])
  ├── 体力阈值 / 过期药 / 每周剿灭
  ├── 基建班次 / 公招 / 信用商店
  ├── 跑完后动作
  └── 材料监控列表（名称/下限/优先级/开关）

账号配置 (account["smart_*"])
  ├── smart_stage         默认关卡
  ├── smart_annihilation  剿灭关卡
  ├── smart_mon~sun       周日程覆盖
  └── smart_materials_enabled 材料监控开关

决策引擎 decide(account, global_cfg) → [Award, Fight, ...]
    ↓
inject_smart(task_list, ac, w) → 写入 gui.json / gui.new.json
    ↓
MAA 启动执行
    ↓
runner._cleanup() → 解析日志 → 更新 stats.json（剿灭标记/体力）
```

## 全局配置项

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `threshold` | 80 | 体力阈值百分比，高于此值才启动刷关 |
| `expiring_medicine` | true | 是否使用即将过期的理智药（MAA自控） |
| `medicine_days` | 2 | 过期前几天开始使用 |
| `annihilation_enabled` | true | 每周自动跑剿灭 |
| `infrast_times` | ["04:00","16:00"] | 基建换班时间 |
| `recruit_enabled` | true | 公招随基建一起跑 |
| `mall_enabled` | true | 信用商店随 16:00 班次跑 |
| `post_action` | ExitArknights,ExitSelf | 跑完后关闭游戏和 MAA |
| `materials` | [...] | 材料监控列表（名称/下限/优先级/开关） |

## 账号配置项

| 字段 | 说明 |
|------|------|
| `smart_stage` | 默认刷关卡，空 = MAA 自行决定 |
| `smart_annihilation` | 剿灭关卡，空 = MAA 自动选择 |
| `smart_mon~sun` | 周日程覆盖，空 = 用默认关卡 |
| `smart_materials_enabled` | 是否启用材料监控（默认 true） |

## 材料→关卡映射

内置映射表 (`smart_scheduler.py:MATERIAL_STAGES`)：

| 材料 | 最优关卡 | 兜底关卡 |
|------|---------|---------|
| 固源岩 | 1-7 | 1-7 |
| 装置 | S3-4 | 1-7 |
| 聚酸酯 | S3-3 | 1-7 |
| 糖 | S3-1 | 1-7 |
| 异铁 | S3-2 | 1-7 |
| 酮凝集 | S3-5 | 1-7 |
| 龙门币 | CE-6 | CE-5 |
| 作战记录 | LS-6 | LS-5 |

> 映射表可扩展，后续版本计划支持用户自定义映射。

## 队列面板

智能模式开启后，队列面板的「运行中」表格会多显示一列**计划**，展示该账号当前轮次的任务列表（如 `A,I,R,F,C`）。

右上角「上限并行数」控制同时最多运行几个账号，超出的排队等待。

## 待办检查

`📋 待办` 按钮扫描所有账号，检查以下问题：

| 检查项 | 范围 |
|--------|------|
| 未配置连接 | 没选实例且 ADB 为空 |
| 未绑定 MAA | 无 warehouse 条目 |
| 智能关卡为空 | 智能模式开启但未设默认关卡 |
| 材料监控未初始化 | 材料监控开启但 depot.json 不存在 |

## 剿灭追踪

每周剿灭完成后自动标记 `stats.json` 的 `weekly_annihilation` 字段：

```json
{
  "weekly_annihilation": {"week": "2026-W23", "done": true}
}
```

下周一检测到此标记后不再重复跑剿灭，直到换周。
