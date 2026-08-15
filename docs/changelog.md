# 变更记录

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
