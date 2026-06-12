# Agent 规则与偏好

## 工作方式
- 能用子代理并行时优先使用
- 复杂改动（跨 3+ 文件）拆成子代理并行完成
- **并行规则**: 不改同一个文件的子代理同时跑；改同一文件的串行排队（不能合并到一个子代理里绕过）
- 每次改动前，先用子代理做全链路分析，确认所有受影响的地方
- 一次性提交完整方案，不要挤牙膏式迭代
- 方案里自带边缘案例和回退逻辑

## 每次改完必须附上
1. **文件改动表** — 文件、改动内容、行数
2. **受影响流程的结构图** — 改前 vs 改后
3. **数据流前后对比** — 用表格说明行为变化

## 沟通
- 简短直接，不用客套
- 多用表格对比方案
- 有问题直接问，不推测

## 技术
- 所有改动须通过 pytest 测试
- 提交信息用中文，格式: "类型: 简短说明"
- type: fix / feat / refactor / docs / perf / chore

## 项目上下文 (2025-06-12)
- MAAOrch: Python + PySide6 Qt Widgets
- 目录结构: `app/` `services/` `ui/` `models/` `infrastructure/` `network/`
- MAA v6 使用 gui.new.json，要求 TaskQueue 条目带 $type 字段
- PostActions="12" (MAA v6 编码: 4=ExitEmulator, 8=ExitSelf)
- 调度模板池: `services/dispatch_pool.py` + account.dispatch_id 取代 smart_plan
- 三种调度模式: `config.schedule_mode = daily / roguelike / reclamation`
- `_persist_plan` 只在 exit_code == 0 时清理（崩溃/断连保留）
- ADB server 自愈: 检测 protocol fault → kill-server + start-server
- `find_mumu_cli()` 已缓存 (`@lru_cache`)
- mumu-cli 子命令是 shutdown 不是 quit
- subprocess 必须加 encoding="utf-8", errors="replace"
- 所有改动须与现有 68 个测试兼容

### 实体关系模型
- **账号** = (模拟器VM, 服务器APP, 手动登录的角色)
- 账号绑**模拟器 VM + 服务器 APP**（不是绑 MAA 实例）
- `game_client` 决定 MAA 启动哪个 APK（Bilibili / Official 是不同的 APK）
- `emu_instance_index` 标记账号在哪个 VM 上
- `account_switch` 是 APP 内的账号标识
- **MAA 是无状态工具**，谁都可以配它去连模拟器操作
- 用户手动在模拟器里打开方舟 APK → 登录账号 → MAAOrch 只记录"这个 VM 的某个服上登了什么号"
