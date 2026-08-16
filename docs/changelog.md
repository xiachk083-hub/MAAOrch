# 变更记录

## 2026-08-17 — 接手 DSH 未提交工作收尾（LP 规划升级 / 剿灭周常检测 / Go 清理）

### 收尾提交（5 个，f3920ab..697ed47）

1. **LP 全局最小理智规划**（material_planner/penguin_data，f3920ab）：
   scipy linprog 求解 `min Σap·x` 覆盖各材料缺口，合成变量成本 0（消耗低级
   材料+龙门币），副产品按企鹅矩阵期望抵扣（新数据源 config/drop_matrix.json，
   332 关 × 17 蓝材料，regen 随备选榜一起生成）。`plan_mode=lp` 默认，
   scipy 缺失自动回退 greedy；eap 效率门槛 2.5x 防 LP 硬刷低掉率关；
   probe 锁定迭代重解 4 轮；无解缺口转 locked 合成兜底不静默丢缺口。
   本地实测 0.03s 出结果。
2. **剿灭周常检测**（stage_probe，6e0d51e）：终端页有无「剿灭作战」卡片
   = 本周是否可刷（用户实锤+15 台官服样本）。MAA 导航信号
   （GoLastBattle@QuickSwitch→Annihilation=成功；Terminal-Entry 识别失败=
   无卡片=本周已刷）+ 截图交叉验证，返回 CAN_FARM/DONE/SHOTFAIL。
3. **RUNTIME_CANNOT 回写闭环**（runner/depot_scheduler，cf6abc1）：开挂过的
   关能进编队进不了关卡、代理失误等详情页看不出的运行时失败，回写
   farm_probe.json 标 RUNTIME_CANNOT（7 天窗口），planner 下次自动剔除重推。
4. **前端回退 lieflat → 简洁暗色**（a171a9a）：lieflat 设计系统整套移除
   （Google Fonts 外网依赖/Paper 旗舰 token 体系），回 VS Code 风格暗色
   硬编码色；导航简化（队列入更多页），缓存版本 _v=20260816r1。
5. **移除 Go 版 vision_cli**（697ed47）：本地已部署 PaddleOCR 双模型
   （det+rec ONNX），OCR 走本地模型不再需要 Go 工具链；.gitignore 同步清理。

### 核查确认

- 单点收敛 Step 5 完成：logger `_LOG_DIR`=logs/、oplog 迁 logs/、
  api_fastapi/runtime_health 读者双位置兼容、backfill_stats 已迁移；
  asst.log 解析读者唯一（log_watcher），MuMuManager 无硬编码路径
  （find_mumu_cli 单点）。

### 注意点

- LP 合成决策：合成成本 0 AP，LP 会优先合成（如糖组缺口 55 全合成 81 次）
  而不是刷取——与 08-13「纯刷取不合成」拍板有出入，若仍要纯刷需
  关 allow_craft 或限制合成仅兜底。
- 前端回退是工作区状态（DSH 08-16 18:30 之后改的），如 lieflat 方向
  才是本意可 revert a171a9a。

## 2026-06-24 — 移除公式 ADB 端口推算

### 背景

`_auto_derive` 和 `_set_connection` 中存在公式端口推算逻辑：

```python
port = 16384 + int(emu_instance_index) * 32
```

当 `mumu-cli info` 取不到有效 `adb_port` 时，用此公式推算端口并写入账号的 `adb_address`。这导致：
- 公式推算的端口可能是错的（实际端口与公式不符）
- MAA 拿着错误端口去连 ADB → 连不上 → 卡住 10 分钟
- 用户看到"ADB 连不上"但不知道根本原因是端口不对

### 改动

| 文件 | 行 | 改前 | 改后 |
|------|----|------|------|
| `services/runner.py:_auto_derive` | 156-160 | mumu-cli 失败后公式回退 | 直接跳过，不设 adb_address |
| `services/config_injector.py:_set_connection` | 236-240 | 同上 | 同上 |
| `services/runner.py:launch` | 225-230 | `"未配置 ADB 地址和模拟器索引"` | 分两种情况报错 |

### 行为变化

**改前：**
```
账号 emu=5（不存在）
  → mumu-cli info 失败 → 公式 16384+5×32=16544
  → MAA 用 16544 去连 → 端口错 → 卡住 10 分钟 → 日志无提示
```

**改后：**
```
账号 emu=5（不存在）
  → mumu-cli info 失败 → adb_address 保持空
  → launch() 检测到 adb_address 为空
  → emit "模拟器 #5 ADB 未就绪，跳过"
  → 日志可见，用户知道是哪个模拟器的问题
```

### 不影响

- `_launch_job_body` 的 mumu-cli 检测（已经只取真实端口、不设公式）
- 调度系统、队列、前端
- 已有账号的 `adb_address` 值

## 2026-08-16 — LogWatcher FILE_SHARE_DELETE（MoveFile 崩溃最终根因，2487256）

### 背景

647a0a9（stop 立即关句柄）部署后崩溃**依旧**（04:05 官-23/官-6/官-10
连环崩，又挂 4 个）。再次截取错误对话框确认：**同一 MoveFile IOException**。
说明句柄持有者不是旧 watcher 残留，而是**新 watcher 本身**。

### 根因

fc2674b 后 watcher 用普通 `open()` 持续持有 asst.log/gui.log（防丢行）。
Windows 普通 open 默认共享不含 FILE_SHARE_DELETE → MAA 启动时 Bootstrapper
MoveFile 轮转日志被**正在运行的 watcher 句柄**挡住 → IOException 崩溃。
647a0a9 只修了旧 watcher 的释放时机，没修新 watcher 的共享模式。

### 实锤

生产实测：对正在被 watcher 持有的 gui.log 重命名 → `WinError 32`（文件被
另一进程使用）；用 CreateFileW + FILE_SHARE_DELETE 打开后重命名成功。

### 修复（2487256）

`_open_tail`/`_open_gui_tail` 改用 CreateFileW + FILE_SHARE_READ|WRITE|DELETE
+ `msvcrt.open_osfhandle`（Python 3.12 无 `os.O_SHARE_DELETE`）。watcher
可安全持续持有（不丢行），MAA 日志轮转不再被锁。新增 `_open_shared`
静态方法统一实现。

### 教训

1. **"持句柄"与"锁文件"是两回事**：读句柄默认也阻止删除/重命名。
   尾随日志的 watcher 必须显式 FILE_SHARE_DELETE。
2. **修复验证要区分"代码在"与"机制生效"**：647a0a9 代码部署了但没解决
   机制本身（新句柄仍锁）——必须从根上改共享模式。
3. 错误对话框截图是排障利器：连续两次抓到同一 MoveFile 错误，快速排除
   "修复无效是代码没部署"的猜测。

## 2026-08-16 — LogWatcher 句柄锁（ErrorCongratulations 崩溃真正根因，647a0a9）

### 背景

account_switch 守卫（88b88f2）部署后崩溃仍在扩散（15+ 账号、每分钟 1+）。
逐一排除：HotKeys（正常实例也有）、config 文件（干净）、根目录配置、
僵尸进程（spawn 时无残留）、MAA 二进制（全实例哈希一致）。

### 实锤（错误对话框截图）

用 System.Drawing 截取 MAA 错误窗口（600x480 PNG）：

```
System.IO.IOException: The process cannot access the file because it is
being used by another process.
at System.IO.FileSystem.MoveFile(...)
at MaaWpfGui.Main.Bootstrapper.OnStart()
```

**MAA 启动时 Bootstrapper MoveFile（旋转 gui.log/asst.log）被文件锁挡住。**

### 机制（为什么 fc2674b 引发）

- LogWatcher 用普通 `open()` 持有 asst.log/gui.log 句柄（Windows 默认
  共享不含 FILE_SHARE_DELETE → 阻止删除/重命名）
- fc2674b（今晚部署）移除每轮 close+reopen 防丢行 → 句柄**持续持有**
- 循环阻塞在 `readline()`（MAA 空闲常态）时 `stop()` 只置标志不关句柄
  → runner cleanup `join(2.0)` 超时 proceed → 句柄残留
- 同实例下一轮 spawn → MAA Bootstrapper MoveFile 被占 → IOException → 崩

### 修复（647a0a9）

- `LogWatcher.stop()`：置标志 + **立即关闭句柄**（readline 立即返回，
  循环自然退出；finally 幂等再关）
- 异常分支加 `_stopped` 检查：stop 后不重开句柄（原逻辑会重开抵消修复）

### 教训

1. **拿不到错误信息时截图错误对话框**：WPF 对话框正文无法 GetWindowText，
   用 System.Drawing CopyFromScreen 截窗（600x480）→ 异常栈一目了然。
2. **文件句柄的 DELETE 共享是隐藏坑**：Python open() 默认共享读写但不
   共享删除 → 持句柄即锁删除/重命名。日志轮转（MoveFile）首当其冲。
3. **fc2674b 修复丢行的方式引入了锁**：持续持句柄 vs 每轮重开丢行 —
   正确解法是 stop 立即关句柄 + 轮转检测（tell>size 重开），两者兼得。

## 2026-08-16 — account_switch 脱敏值守卫（ErrorCongratulations 启动崩溃根治）

### 背景

批量清体力期间，带 `account_switch` 的账号 100% 启动失败
（0 成功 / 39 失败，官-7 连续 4 次失败被自动挂起）。MAA 弹出
`{{ ErrorCongratulations }}` 错误对话框后退出（exit=1），asst.log 全空、
失败截图显示 Android 桌面（游戏未启动）。

### 排查链条（用户问题引出）

用户问"adb 是不是每次现测"→ 核实启动链 **已经是** 每次现测
（runner `_launch_job_body` 启动前现测 + 启动后轮询 `detect_emu_instances`
+ `adb connect` 实测连通才采用 + 连接超时清缓存重测，注释记录了
b-2/b-3 16416→16417、官-47 16769→16708 历史事故）→ **adb 不是问题**。

真实根因：config 里 32 个账号的 `account_switch` 全是**脱敏手机号**
（`186****6119` 字面带星号，0 个真实值）。注入器把这些值写入 MAA
TaskQueue 的 `AccountName` + `AccountSwitchEnabled=True` → MAA 的
SwitchAccount 任务拿脱敏字符串匹配真实账号 → 失败 → 崩溃。

现场证据：debug.log `当前任务: SwitchAccount@GameStart` 循环、
gui.log `SimpleEncryptionHelper Failed to decrypt`、events.log 100% 失败率
相关性（有 switch 账号 0 成功/39 失败）。

### 改动（88b88f2）

- `config_injector.py` 新增 `_valid_switch()`：含 `*` 的切换标识视为无效
  → 不注入 AccountName、AccountSwitchEnabled=False、RunDirectly=True
  （账号按当前已登录账号直接跑，恢复原本的工作方式）
- 三处注入点（gui.json 路径 / TaskQueue 块 / v6 注入块）统一走守卫
- 附带：HotKeys 非空对象同样清理（原只删空 `{}`，残留
  `{'ShowGui': {...}}` 同样触发 SetKvOrMigrate JsonReaderException）

### 教训

1. **用户直觉有价值**：用户质疑"adb 是否每次现测"，核实后发现 adb 逻辑
   本来就对，但顺着失败现场（截图/日志/相关性分析）挖出了真正的根因。
2. **失败率数据是定位利器**：events.log 按账号聚合成功/失败 → 100% vs 50%
   的对比直接锁定 account_switch。
3. **脱敏数据入库是地雷**：显示层脱敏值（带 `*`）被当作真实配置存进
   config → 注入 MAA 即崩溃。配置字段应拒绝明显无效值（或入库前校验）。
4. 现场数据决定论：HotKeys 理论先被实例 3（无 HotKeys 也崩）反证，
   WindowPlacement/AccountName 理论逐一排除 → 最终以"带 switch 账号
   100% 失败"的统计证据定案。

### 遗留

- 32 个账号的 `account_switch` 仍是脱敏死值（守卫使其失效，账号正常跑）。
  如需真正的账号切换功能，需用户提供真实切换标识（MAA 账号管理器里的
  账号名/标识，不含 `*`）。
