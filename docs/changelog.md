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
