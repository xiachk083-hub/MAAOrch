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

## 持续学习与迭代
- 每次对话结束时，评估是否有新的优化空间并更新 AGENTS.md
- 定期从类似开源项目（MAA、同类调度工具）学习架构模式，应用到项目中
- 每次架构变更后，重新评估性能、可维护性、用户体验
- 保留「待优化清单」在项目文档中，按优先级迭代
- 遇到不熟悉的模式或技术，先在子代理中做 research 再决定是否引入

## 自我迭代
- 每次对话结束前，主动审查 AGENTS.md 是否需要更新
- 每次修完 bug 或完成架构变更后，把根因、改动、关键决策记录到项目上下文
- 如果对话中发现某个规则不合理或过时，当场提出修改建议
- 每 3 次对话后，系统性检查 AGENTS.md 是否完整

## 问题排查
- 用户报告问题时，先自己查 `debug.log` 和 `crash.log` 定位根因，不直接问用户
- 能复现的优先自己看日志、自己测 API，不给用户增加负担
- 需要用户操作的（如重启、贴日志），给出具体的一步操作指令
- 修复后说明根因，让用户知道问题出在哪

## 项目上下文 (2026-06-13)
- MAAOrch: Python + PySide6 + Web UI (pywebview/浏览器双模)
- 目录结构: `app/` `services/` `ui/` `models/` `infrastructure/` `network/`
- 三种运行模式: `main.pyw` (Qt), `main_web.pyw` (Web推荐)
- MAA v6 使用 gui.new.json，要求 TaskQueue 条目带 $type 字段
- PostActions="12" (MAA v6 编码: 4=ExitEmulator, 8=ExitSelf)
- 调度模板池: `services/dispatch_pool.py` + account.dispatch_id
- 三种调度模式: `config.schedule_mode = daily / roguelike / reclamation`
- `_persist_plan` 只在 exit_code == 0 时清理（崩溃/断连保留）
- ADB server 自愈: 检测 protocol fault → kill-server + start-server
- `find_mumu_cli()` 已缓存 (`@lru_cache`)
- MaaCore ctypes 直连 (`infrastructure/maa_core.py`) — 跳过 MAA.exe 子进程
- Web UI SSE 推送 (`/api/sse`) — 替代 3s 轮询
- Go 性能服务: `services/adb_monitor/`, `log_monitor/`, `health_monitor/`
- **Qt 信号在 pywebview 模式不送达** → 改为直呼 `on_account_finished` 兜底
- **QTimer 需 Qt 事件循环** → 改用 `threading.Timer` 或后台 daemon 线程兜底
- **`type('MW', (), {})` 创建的 mock 属性被转为方法描述符** → lambda 须接受 `self` 参数
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
