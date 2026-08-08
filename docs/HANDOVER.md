# MAAOrch 交接报告

> 交接日期：2026-08-08
> 交接人：AI 开发会话（2026-08-07 ~ 08-08 连续两天的功能开发 + 事故修复）
> 本文档覆盖：当前状态、完成功能、修复的关键 bug、遗留待办、操作手册、架构要点

---

## 1. 项目当前状态

### 1.1 部署信息

| 项 | 值 |
|----|-----|
| 项目位置 | `E:\MAAOrch`（目标机器 100.79.173.69） |
| Manager 位置 | `E:\MAAOrch-Manager`（常驻管理服务，端口 19998） |
| Web UI | `http://100.79.173.69:19999` |
| 最新部署提交 | `65b6a4b`（全部提交已推送 `main`） |
| MAA 版本 | 6.16.5（官方包，含外服资源 `resource/global/YoStarEN/JP/KR/txwy`） |
| 实例池 | 11 个（parallel_max=10） |
| 账号 | 45 个（官服 30 / B服 11 / 日服 4），跳过 l-* 系和碧蓝航线 |

### 1.2 运行状态（交接时）

- MAA `ready: True`，实例池 11 个正常
- 账号配置完整（game_client 按服区、adb_address 运行中的已填）
- 服务器维护时段（国服 18:00 后恢复）— 交接后先验证任务可跑

### 1.3 架构摘要

```
main_web.pyw (可选 PySide6) → WebContext → AccountRunner / LaunchQueue
  ├─ AccountRunner (services/runner.py)      — MAA 生命周期 + 关卡降级 + 自动恢复
  ├─ LaunchQueue (services/launch_queue.py)  — 优先级队列（20s 启动间隔）
  ├─ ConfigService (services/config_injector.py) — MAA 配置注入（v6 Gui 嵌套）
  ├─ FastAPI (network/api_fastapi.py)        — 2400+ 行，50+ 端点，SSE
  └─ MAAOrch-Manager (manager/manager.py)    — 常驻管理（下载/启动/exec/日志/自启）
```

---

## 2. 本轮完成的功能

### 2.1 MAA 6.16 配置注入适配（核心里程碑）

**背景**：MAA 6.16 把连接/启动设置从 `gui.json` 扁平键迁移到 `gui.new.json` 的 `Configurations.Default.Gui.*` 嵌套结构（ConfigFactory 读取）。旧代码写 gui.json 扁平键 → 6.16 不读 → AutoDetect 默认开（地址填不进）、RunDirectly 默认关（不自动连）、更新后跳过运行。

**实现**（`config_injector._set_connection_v6_gui`）：
- `Gui.ConnectSettings`: AutoDetect=False / Address / Config="MuMuEmulator12" / Extras.MuMuEmulator12.InstanceIndex（绕开自动检测错位 16992 vs 16708）/ AllowAdbRestart=False
- `Gui.StartUpSettings`: RunDirectly=True / **SkipStartupAutoRunAfterUpdate=False**（MAA 更新后默认跳过自动运行）/ StartEmulator=False / EmulatorPath / EmulatorAddCommand
- `Gui.RuntimeSettings.ClientType`: **枚举成员名**（Official/Bilibili/EN/JP/KR/Txwy — "YoStarJP" 是 UI 显示名，写入会被 InvalidEnumValueRemoveConverter 静默移除回退 Official）
- 完整映射手册：`docs/maa-config-mapping.md`

### 2.2 连接页（监控墙）

- 网格布局（auto-fill minmax 280px，手机单列）
- 勾选批量操作：全选/全不选/批量启动（仅连接/日常·官服/B服/日服/国际服/韩服/繁中）/停止 MAA/关闭模拟器
- 截图轮询：5s 链式刷新 + 错峰（不再 2s 齐发）
- 状态增量更新（不闪烁）+ 名称拼音自然排序

### 2.3 模拟器管理

- 勾选 + 批量启动/重启/关闭（`POST /api/emulator/batch/{action}`，注册在单卡端点前防路由抢占）
- 名称自然排序（`_natCmp` — 数字段数值比较，官-1 < 官-2 < 官-10）

### 2.4 MAA 日志归档

- 任务结束（任何退出路径）归档 `asst.log` + `gui.log` 到 `logs/maa_history/{aid}/`（每账号 30 次运行）
- 日志页「📁 历史」面板：选账号 → 文件列表 → 查看内容（`GET /api/maa/logs_history`）
- `logs/maa_history` 已加入 manager 部署保留列表

### 2.5 截图性能（防限流/防过载）

- 截图端点**豁免限流**（画面墙 N×2s 轮询击穿 200/min → 全 429 → 图片全黑）
- 2.5s 每模拟器缓存 + **全局串行锁**（防 adb 通道饱和拖慢 MAA 任务）
- 去掉每帧 KEYCODE_WAKEUP、adb 空值 fallback find_adb()
- 前端 5s 轮询 + 错峰 + 链式刷新

### 2.6 关卡优先级逐关降级（详见 §4）

---

## 3. 修复的关键 Bug（根因 → 修复）

### 3.1 部署事故链（2026-08-08 凌晨 — 最严重）

**事故链**：目标机器重启 → manager 替换项目 `WinError 183` → 项目半损坏（ui/ 缺失、source 缺 MAA.exe）→ 账号 config.json 被默认覆盖丢失 → MAA 下载卡 `_extract_zip` → source 缺 config 反复下载循环。

**加固清单**（每项都有对应提交）：

| # | 修复 | 提交 |
|---|------|------|
| 1 | 替换用 `copytree(dirs_exist_ok=True)` 合并残留 + 失败自动回滚旧项目 | `e7d52a4` |
| 2 | 替换前 `taskkill /IM MAA.exe`（孤儿进程锁文件根因） | `f4b0ed0` |
| 3 | `_extract_zip` 逐文件解压 + 每 1000 文件进度 + 单文件容错（去 extractall） | `de6d10d` |
| 4 | MAA.exe 在但 config 缺失 → `_init_maa_source` 初始化而非重新下载（防循环） | `de6d10d` |
| 5 | 部署前备份 config.json 到 manager backups + 部署后完整性校验（`"accounts" in raw` 否则恢复） | `de6d10d` |
| 6 | `check_update` 检测 source 完整性（损坏不再误报"已是最新"）+ 镜像 fallback | `f4b0ed0` |
| 7 | MAA 下载镜像 fallback（ghfast/ghproxy/gh-proxy）— `_get_download_url` 也加镜像 | `95cedf2` + `efeac25` |
| 8 | `Logger.warning → warn`（MaaCore 加载失败分支崩溃） | `5482db0` |
| 9 | manager 启动自动拉起项目（重启只需启动 manager） | `c59a74f` |
| 10 | manager `POST /api/exec` 远程 PowerShell 执行（诊断/恢复，token 保护） | `3bdfa98` |
| 11 | MAA source 清理前杀残留 MAA.exe + rmtree 重试 + exist_ok | `a5e0e3b` |

**恢复路径**（事故时用）：
- 账号：`%TEMP%\maorch_mgr_*\preserve\models\config.json`（部署前快照）
- MAA source：preserve 里的 `services/maa/source` 或手动 `Expand-Archive`（zip 无顶层目录，MAA.exe 在根）+ 复制 preserve 的 config

### 3.2 关卡注入三连坑（2026-08-08 下午 — 定位费时）

| # | 根因 | 修复 | 提交 |
|---|------|------|------|
| 1 | `Account` dataclass **无 fight_* 字段** — `from_dict` 丢弃动态属性 → 重启/部署后 fight 配置全部重置（**"deploy 重置配置"的元凶**） | `models/account.py` 补 8 个字段（fight_mode/fight_default/schedule_weekly/schedule_monthly/fight_priority/fight_materials/fight_times_per_stage/fight_series） | `d6e342c` |
| 2 | 755 行 stage whitelist 旧逻辑**无条件用 `stages[0]` 覆盖**策略选好的关卡 | 仅无 fight_mode 的旧账号生效 | `65b6a4b` |
| 3 | **残留 MAA 运行中保存配置覆盖注入** | `_launch_for_instance` 注入前清理实例残留（.pid + taskkill） | `dafc254` |

### 3.3 MAA 意外退出感知与自动恢复

- `_check_one` poll 检测 → **主动清理残留标记**（之前只 return 依赖 `_wait_exit` 线程，线程失效则残留标记挡住重启）
- 异常退出（exit≠0 非用户停）→ **自动重新入队**（3 次上限防循环）
- **StartUp 卡加载超时清理**（游戏加载卡 LoadingIcon 循环 — asst.log 持续写导致静止检测失效 → 卡死占实例）
- 任务完成挂起清理（asst.log 静止 5 分钟）；connect 模式挂起正常不清理

### 3.4 其他

- 截图端点限流豁免（`a22d22f`）
- MAA 日志归档（`2ac6e96`）
- manager project_log 支持 `?inst=N` 读任意实例日志（`7abd2be`）

---

## 4. 关卡优先级逐关降级（本轮核心功能）

### 4.1 设计

用户场景：46 个账号无法人工检测每个号的活动第 8 关能否刷 → 需要"先刷最高优先级关卡，刷不了自动降级下一个"。

**为什么不用多 Fight 条目**：MAA 6.16 任务失败**即停队列**（PR #16357 "关卡没解锁时报错停止"）→ 多条目不成立。

**实现**（单 Fight + MAAOrch 重启降级）：
```
注入: Fight(最高优先级关, TimesLimit=每关次数, Series=固定倍率) + 存 _stage_fallback
  ├─ 刷成功 → FightTimes.finished → 收尾（不刷后面的）→ 体力只耗最优关
  ├─ 失败(未解锁/导航失败) → 记录 stage_checks → 移除该关 → 重注入下一关 → 重启
  └─ 理智不足(<20) → 停止降级（所有关都刷不了）
```

### 4.2 关键机制

| 机制 | 位置 |
|------|------|
| 降级列表 + 倍率 + 次数 | `config_injector.py` priority 分支（`_stage_fallback`/`_stage_current`/`_stage_override`） |
| 降级触发（gui.log "理智作战: ...添加任务失败"） | `runner._check_one`（独立于 asst.log 增量 — MAA 卡死时 asst.log 静止） |
| Fight 失败检测（`_maybe_downgrade`：ExceededLimit / Fight 从未启动但有后续任务） | `runner.py` |
| 检测记录持久化 | `models/stage_checks.json`（每账号 20 条） |
| 前端显示 | 刷关策略面板「🔍 关卡检测记录」+ 每关次数 + 代理倍率输入 |

### 4.3 验证状态

| 验证 | 结果 |
|------|------|
| FAKE-99（无效关卡）→ 自动降级 1-7 | ✅ 17:44 验证（降级日志 + MAA 重启 + stage_checks 记录） |
| 日-2 刷 1-7（倍率 1 × 1 次） | ✅ 18:36 验证（GetFightStage 1-7 + "开始行动 1 次, -6理智"） |
| 理智不足停止降级 | 逻辑已实现（sanity < 20），**待真实场景验证** |
| 真实活动关降级 | **待服务器稳定后验证**（用真实未解锁活动关测） |

### 4.4 已知限制

- 理智不足判定：sanity < 20 硬阈值（无精确关卡消耗判断）
- 降级触发依赖 gui.log 的中文日志（"理智作战"）— 若 MAA 改文案需同步
- `fight_times_per_stage`/`fight_series` 是账号字段，前端面板可配

---

## 5. 遗留待办（按优先级）

| 优先级 | 事项 | 状态/说明 |
|--------|------|-----------|
| **P1** | 关卡降级真实场景验证 | 用真实未解锁活动关（如当前活动关 + CE-6）跑一次完整降级 + 理智不足场景 |
| **P1** | **z.ai 接入**（用户明确要求） | `_PROVIDER_HINTS["zai"] = {endpoint: "https://api.z.ai/api/paas/v4/chat/completions", model: "glm-5.2"}` + 设置页前端下拉选项。OpenAI 兼容格式直接可用 |
| **P2** | 下次 deploy 验证 fight 配置不再重置 | Account 字段修复已部署（`d6e342c`），deploy 前后对比 config.json 的 fight_mode |
| **P2** | 截图 JPEG 压缩 | 需装 Pillow（当前无依赖）— 串行+缓存+5s 后卡顿已解决，若再卡评估 |
| **P2** | `_init_maa_source` 首次初始化可靠性 | 等待 60s + TaskQueue 非空判定已加固，新机器首次安装验证 |
| **P3** | 实例池 mtime/size 比较 | 已用 size 比较（防无限重建），极端情况（同 size 不同内容）未处理 |
| **P3** | 归档保留验证 | logs/maa_history 已加入 manager 保留列表，下次部署验证归档不丢 |
| **P3** | 前端 bug：`_updateConnectStatus` 的 `launchDaily` 引用 | 菜单重构后旧选择器残留（有 null 保护，无害） |

---

## 6. 操作手册

### 6.1 Manager（远程管理）

| 端点 | 用途 |
|------|------|
| `POST /api/status` | 项目运行状态 |
| `POST /api/download` | 部署最新 main（备份 config + 校验 + 回滚保护） |
| `POST /api/start` / `stop` / `delete` | 启停/删除项目 |
| `POST /api/update_manager` | 更新 manager 自身（含新端点） |
| `POST /api/exec` | 远程 PowerShell（token 保护）— `{command, timeout}` |
| `POST /api/config_backup?file=<name>` | 读 config.json / backups |
| `GET /api/project_log?file=<name>&inst=<n>` | 读 debug/stderr/crash/MAA 实例日志 |

**Manager 自启**：`E:\MAAOrch-Manager\manager.bat` 双击启动 → **新版自动拉起项目**（重启只需启动 manager）。开机自启需在目标机器跑 `install.bat`（当前**未注册** — 用户已知）。

### 6.2 远程 exec 最佳实践

- PowerShell 转义易错 → **写本地临时 .py 脚本再调用 exec**（如 `C:\Users\xiach\AppData\Local\Temp\opencode\*.py`）
- 复杂命令用 base64：`powershell -Command "python -c \"import base64;exec(base64.b64decode('...'))\""`

### 6.3 部署注意事项（防再犯）

1. 部署前 manager **自动备份 config.json**（`E:\MAAOrch-Manager\backups\config_predeploy_*.json`）
2. 替换用 `dirs_exist_ok` 合并 + 失败**自动回滚**（不会半损坏）
3. 替换前杀孤儿 MAA.exe（防文件锁）
4. 部署后完整性校验（accounts 丢失自动从备份恢复）
5. **部署会重置账号 fight 配置的历史问题** — Account 字段修复后应不再发生（待验证）

### 6.4 恢复路径（事故应急）

| 丢失 | 恢复源 |
|------|--------|
| 账号（config.json） | `%TEMP%\maorch_mgr_*\preserve\models\config.json` 或 manager backups |
| MAA source | preserve 的 `services/maa/source` 或重新下载（镜像）或手动 Expand-Archive |
| MAA 日志归档 | `logs/maa_history/`（已加入保留列表） |

---

## 7. 架构与技术要点（新开发者上手）

### 7.1 无 Qt 核心

- `runner.py` / `launch_queue.py` 去 QObject — callback 列表替代 Signal（`emit_log` 等）
- PySide6 可选（有则托盘，无则纯 Web）

### 7.2 MAA 6.16 配置映射（核心知识）

- **双配置文件**：`gui.json`（ConfigurationHelper — Global 区仍读）+ `gui.new.json`（ConfigFactory — 全部核心设置）
- **6.16 嵌套结构**：`Configurations.Default.Gui.ConnectSettings` / `StartUpSettings` / `RuntimeSettings`
- **ClientType 枚举成员名**（JP 不是 YoStarJP）— 详见 `docs/maa-config-mapping.md`
- 注入链路：`_launch_for_instance` → `inject_smart`（priority 分支设降级列表）→ `_spawn_instance`（注入前清理残留）

### 7.3 日志信号（降级/状态判定）

| 信号 | 日志 | 用途 |
|------|------|------|
| 理智 | gui.log "开始行动 X 次, -Y理智 / 理智: N/M" | 次数+理智提示 |
| 理智信息 | asst.log `SubTaskExtraInfo what="SanityBeforeStage"` | last_sanity |
| 次数刷完 | asst.log `what="FightTimes" {finished:true}` | 正常成功信号 |
| 重试耗尽 | asst.log `what="ExceededLimit"` | 任务失败 |
| 无效关卡 | gui.log "理智作战: ...添加任务失败" | 触发降级 |
| 全部完成 | gui.log "任务已全部完成" | 收尾 |

### 7.4 关键文件索引

| 文件 | 职责 |
|------|------|
| `services/config_injector.py` | 配置注入（v6 Gui 嵌套 + TaskQueue + 降级列表） |
| `services/runner.py` | MAA 生命周期 + 降级循环 + 自动恢复 + 卡死兜底 |
| `services/maa_download.py` | MAA 下载/初始化（镜像/逐文件解压/防循环） |
| `manager/manager.py` | 部署/管理/exec/备份/校验 |
| `services/log_parser.py` | asst.log 解析（FightTimes/ExceededLimit/理智） |
| `models/account.py` | Account 模型（fight_* 字段 — 必须保持 dataclass 字段！） |
| `models/stage_checks.json` | 关卡检测记录（运行数据） |

---

## 8. 敏感信息

- **Manager token**：`E:\MAAOrch-Manager\config.json`（`e210ad4863f14c4e` — 已在会话中使用，如需轮换在 manager 停运行时改该文件）
- **API token**：MAAOrch 设置页 `api_token`（`cdab9ec6e0d53501af4513557b45ff15` — 远程调试用，建议正式环境轮换）
- **config.json 含账号数据** — 已加入 .gitignore，**切勿提交**
- z.ai API Key：待接入时填设置页（存 config.json — 注意勿提交）
