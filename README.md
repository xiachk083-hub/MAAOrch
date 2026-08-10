# MAAOrch

MAA 多账号自动化调度平台 — 批量管理明日方舟账号的日常/剿灭/体力清空任务。
全流程自动：模拟器启动 → ADB 连接 → MAA 运行 → 任务完成 → 体力清空，无需人工介入。

## 架构

```
MAAOrch
  ├─ main_web.pyw        Web 入口（FastAPI + uvicorn，端口 19999）
  ├─ services/           核心服务
  │    ├─ runner.py           单账号运行器（启动→监控→完成→自动续跑）
  │    ├─ launch_queue.py     调度队列（优先级、恢复、回收、模拟器管理）
  │    ├─ config_injector.py  MAA 配置注入（连接/任务/更新禁用）
  │    ├─ dispatch_pool.py    调度模板池（日常/剿灭/肉鸽）
  │    ├─ stats_aggregator.py 统计聚合（日/周/月/年）
  │    └─ runtime_health.py   运行时健康检查
  ├─ network/api_fastapi.py  API 层（50+ 端点，SSE 实时推送）
  ├─ models/              数据模型（Account / config_manager / stats）
  ├─ ui/web/              Web UI（调度台/连接/账号/日志/统计/编年史/图库等）
  ├─ manager/             常驻管理器（自启动、远程部署、健康恢复）
  └─ tools/deploy.py      一键部署工具
```

## 部署

```bash
# 目标机（Windows 10/11，Python 3.10+，MuMu 12 模拟器）
git clone https://github.com/xiachk083-hub/MAAOrch.git
cd MAAOrch
python main_web.pyw
# 浏览器打开 http://<机器IP>:19999
```

首次启动自动：
- 检测/下载 MAA（未安装时后台下载，~200MB）
- 创建 MAA 实例池
- 用默认模板初始化配置（`models/config.json` 不存在时）

## 更新

```bash
# git 方式（推荐）
git pull --ff-only
# 或 Web UI「设置 → 检查更新」（git 检测优先，zip 模式兜底）

# 远程部署（开发机 → 目标机，需 manager 常驻）
python tools/deploy.py --file services/runner.py   # 单文件
python tools/deploy.py                             # 全量
```

## 配置

- `models/config.json` — 主配置（账号/调度/API token）。**含敏感数据，已在 .gitignore 排除，绝不提交**
- 账号字段：`id / name / game_client / emu_instance_index / account_switch / uid / suspended / smart_annihilation / stages / fight_default` 等
- 调度模式：`daily`（日常）/ `roguelike`（肉鸽）/ `reclamation`（生息）
- `suspended: true` 的账号不自动入队（挂起），需手动解除

## 关键行为

- **模拟器关闭统一优雅退出**（adb reboot -p → 等待完全退出 → 兜底）— 避免 VMM 残留进程
- **MAA 自动更新已禁用**（`Update.CheckOnStartup=False`）— 版本由 MAAOrch 管理
- **完成判定**：MAA 输出 `AllTasksCompleted` → 体力未清空（>30%）自动续跑，清空停止
- **失败恢复**：自动重启（3 次）→ 模拟器失联检测（MAA 空转自动杀+重启）→ 反复失败自动挂起（防无限循环）
- **任务自动续跑**：一轮完成但体力未清空 → 自动重新入队

## 安全说明

- Web API 鉴权：`x-agent-token` header（设置页配置）
- 远程管理（manager）：端口 19998 + token 鉴权（`E:\MAAOrch-Manager\config.json`）
- 敏感数据（账号/凭据/日志/队列状态）全部在 `.gitignore` 排除
- 模拟器路径自动检测（MuMuManager/ADB），支持自定义安装路径

## 技术栈

- Python 3.10+、FastAPI、uvicorn
- MuMu 12（MuMuManager.exe）、ADB
- MAA（MaaAssistantArknights 6.16.x）
