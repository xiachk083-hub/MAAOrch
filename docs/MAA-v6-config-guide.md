# MAA v6 配置注入说明

## 剿灭关卡代码

MAA v6 使用不同�?MAA v5 的剿灭关卡代码�?
MAA v5 使用 `Annihilation_1/2/3` 格式�?*MAA v6 不再支持这些旧编�?*�?

### MAA v6 支持的�?

| 显示�?| MAA v5 代码 | MAA v6 `AnnihilationStage` �?|
|--------|------------|------------------------------|
| 自动选择 | �?| `""` |
| 当期剿灭 | `Annihilation` | `"Annihilation"` |
| 切尔诺伯�?| `Annihilation_1` | `"Chernobog@Annihilation"` |
| 龙门外环 | `Annihilation_2` | `"LungmenOutskirts@Annihilation"` |
| 龙门市区 | `Annihilation_3` | `"LungmenDowntown@Annihilation"` |

### StagePlan 字段

`StagePlan` �?MAA v6 中始终为 `["Annihilation"]`，不随具体关卡变化�?
具体关卡�?`AnnihilationStage` 字段控制�?

### 字段映射来源

- `maa/v6.11.1/resource/tasks/tasks.json` lines 1130-1140
- 实例配置 `maa/instances/{N}/config/gui.new.json` �?`AnnihilationStage` 字段

## TaskQueue 条目格式

MAA v6 �?TaskQueue 条目必须包含 `$type` 字段�?

| TaskType | `$type` �?|
|----------|-----------|
| StartUp | `StartUpTask` |
| Fight | `FightTask` |
| Infrast | `InfrastTask` |
| Recruit | `RecruitTask` |
| Mall | `MallTask` |
| Award | `AwardTask` |
| Roguelike | `RoguelikeTask` |
| Reclamation | `ReclamationTask` |

## config_injector.py inject_smart 说明

`inject_smart()` 负责将智能调度的任务列表写入 MAA 实例的配置文�?`gui.new.json`�?

### 关键逻辑

1. `task_set` = 用户的任务类型集合（小写�?
2. 读取现有 TaskQueue，移�?`_smart_inserted` 标记的条�?
3. 对每个条目设 `IsEnable = TaskType.lower() in task_set`
4. Fight 条目�?`UseCustomAnnihilation` 控制是否跑剿�?
5. 当同时有刷关和剿灭时，插入一个剿灭克隆条目放在刷关前
6. 当只有剿灭无刷关时，修改现有 Fight 条目为剿灭模�?

### 去重逻辑

`clean_tq` 构建后，如果 Fight 条目多于 1 个，保留最后一个，删除多余重复�?
