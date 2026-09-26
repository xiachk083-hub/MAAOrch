# MAAOrch 平台 Node 化：终版与前后对比（2026-09-21）

> 起因：用户「**别再用 Python 了 这个占用太高了**」。
> 做法：**门面 + 代理**渐进迁移 —— Node 接管用户入口 19999，已实现的路由自己答，
> 未实现的透传 Python 上游；按 `_proxy_stats` 的**实际调用命中**决定搬运顺序。

## 一、前后对比（同口径：10 秒实时采样差分，非累计 CPU 时间）

| 项 | 迁移前 | 迁移后 | 说明 |
|---|---|---|---|
| **Python 看板 `main_web.pyw`** | **46.9% CPU / 141 MB RSS**（15 线程；`psutil` 逐线程定位 **单线程 2348 独占 45.3%** 自旋） | **0%（已下线）** | 目标②要省的就是这一块 |
| **Node 看板 `server/maaorch-web.mjs`** | — | **0.4% CPU / 70 MB RSS** | 空转几乎为零；零第三方依赖 |
| **平台入口 19999** | Python（FastAPI+uvicorn+PySide6 托盘） | **Node（门面）** | `/` = 完整前端 + 顶部状态条；`/status` = 运维面板 |
| **Python 上游 19997** | 常驻 | **按需**（`tools\start_python_upstream.bat`，或 Node 用 WMI 后台拉起） | 只在点击"尚未搬到 Node 的旧功能"时才需要 |
| **自启** | `MAAOrch-看板.bat`（拉 Python） | `MAAOrch-平台.bat`（**只拉 Node 门面**） | 旧 bat 已 `.disabled` 并备份 `.bak-python` |
| 静态资源 | 上游提供 | **Node 直接从 `ui/web/` 供** | Python 停掉页面也不会丢 CSS/JS |

**机器整体（同一时刻采样，供参考）**：MuMu 三实例 ≈ 162%、Chrome ≈ 119%、DSH node ≈ 21%、其他零头 ⇒ **MAAOrch 平台自身已降到 ~0.4%**，大头是模拟器与浏览器（这部分要用户按产线/使用习惯决定）。

## 二、迁移了什么（已通过"与上游逐键对照"验收）

**读端点（17）**：`health / status / queue / stages / account_status / accounts / config / settings.smart / node.dashboard / logs / emulators / connect.status / mas.status / production / runs / stats / sse`
**写端点（3）**：`POST mas.rerun / mas.cleanup / account.toggle`（默认 **dry-run**、账号名走数组传参防注入、每次调用留痕 `runtime/node_dashboard_actions.log`）
**验证方式**：每搬一个就同时打 Node(19999) 与 Python(19997)，比对顶层键集合与首条记录字段；`/api/logs`、`/api/emulators`、`/api/connect/status` 已做到**内容逐字节一致**；`/api/node/dashboard` 的 `capacity`（`by_memory/by_gpu/max/limit_by`）与上游完全一致（GPU 走 `nvidia-smi`）。

## 三、还没搬的（Python 独有，共约 77 条，**均为点击类**，页面加载不触发）

`action/*`（smart_all / smart_selected / smart_fight / smart_annihilation / smart_login / smart_maintenance / stop_all / connect_running_emus）、`connect/launch|stop|restart`、`config`(POST)、`accounts/batch_save|csv_import`、`screenshots/*`、`depot/*`、`ai/*`、`account/{idx}/*`、`emulator/{idx}`、`maalog`、`kill_audit` 等。
**行为**：Python 离线时这些端点返回 **503 `upstream_starting`**（6 秒内，不再挂死）并提示"已后台拉起上游、请稍后重试"。

## 四、关键实现细节（踩坑换来的，别再踩）

1. **鉴权**：`/api/*` **本机（127.0.0.1/::1）免凭据**；非本机必须带 `?token=` 或 `x-agent-token` 头。理由：旧前端不带凭据，一刀切要 token 会让页面**全 401、到处 undefined**（CDP 抓包才查出来）。
2. **透明代理**：先读全请求体再转发，并 **`req.resume()`** —— 否则带 body 的 POST 等不到 `end`，连兜底守卫都不执行（前端一直转圈）。
3. **兜底守卫**：进入转发函数就挂 6 秒 `setTimeout`，任何异常路径都必须给客户端一个可读 JSON。
4. **请求处理器总兜底**：`createServer` 回调里 `try/catch` 包住整个处理体，单个请求的 bug 不许打死服务（真发生过：一个 `ReferenceError` 让 Node 进程退出）。
5. **静态资源 Node 直供**：不要白名单走代理（Python 一停页面就丢样式）。
6. **诊断利器**：`runtime/_pilot/cdp_probe.mjs`（CDP 抓页面网络+console）；`_proxy_stats` 看"哪些请求还在走代理"。
7. **`MuMuManager.exe info -v all`** 是模拟器信息的权威源（name/adb_port/is_android_started），别自己扫目录拼名字。

## 五、待办（后续轮次）

1. 按点击频率继续搬（优先 `depot/*` 材料规划、`screenshots/*`、`action/*` 里的高频项）；
2. 查 Python 那个**自旋线程**（若要重新常驻，先修它；不常驻则可不修）；
3. 目标③：跑局/导航工具（`stage_attempt`/`bi_goto`/`bi_state`/`axis_viz`）Node 化，只把 `MaaCore.dll` 的 ctypes 留作按需小助手；
4. 搬完后可考虑收掉 Python GUI 依赖（PySide6+PyQt6+PyQt5 ≈ 980MB）。
