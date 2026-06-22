# HTTP API 接口文档

## 概述

MAAOrch 启动后自动在 `127.0.0.1` 开启 HTTP REST 服务，供 Web UI 面板调用。

## 连接信息

| 项目 | 说明 |
|------|------|
| 地址 | `http://127.0.0.1:{port}` |
| 默认端口 | `19999`（可在设置中修改） |
| 鉴权 | Header `x-agent-token: {token}`（空字符串则不验证 Web UI 请求） |
| Content-Type | `application/json` |
| 监听范围 | 仅 `127.0.0.1`（可通过 `bind_address` 配置修改） |

## 安全机制

| 机制 | 说明 |
|------|------|
| 地址绑定 | 默认仅监听 `127.0.0.1`（loopback-only） |
| Token 鉴权 | 可选，通过 `x-agent-token` Header 传递；Web UI 不传 Token 时放行 |
| 频率限制 | 200 req/min/IP；`127.0.0.1` 豁免 |
| CORS | `Access-Control-Allow-Origin: *` |
| 自动重启 | 修改端口或 Token 后自动重启服务 |

## 端点参考

### 状态查询

**`GET /api/status`**

返回全部账号运行状态。

```
Response 200:
{
  "accounts": [
    {
      "name": "官服大号",
      "index": 0,
      "running": true,
      "elapsed": 360,
      "adb": "127.0.0.1:16384",
      "emu_index": "0"
    }
  ],
  "pipeline_running": false,
  "running": 1
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `accounts[].name` | string | 账号名 |
| `accounts[].index` | int | 在列表中的序号 |
| `accounts[].running` | bool | 是否正在运行 |
| `accounts[].elapsed` | int | 运行秒数（仅在运行中有意义） |
| `accounts[].adb` | string | ADB 地址 |
| `accounts[].emu_index` | string | 模拟器实例序号 |
| `pipeline_running` | bool | 流水线是否正在执行 |
| `running` | int | 当前运行中账号数 |

---

**`GET /api/account/{index}/status`**

返回单个账号状态。`{index}` 对应 GUI 中账号列表的序号（0-based）。

```
Response 200:
{
  "name": "官服大号",
  "running": true,
  "elapsed": 120
}

Response 404 (index 越界):
{"error": "account not found"}
```

---

**`GET /api/node/info`**

返回节点信息、版本、资源概览。

```
Response 200:
{
  "node_id": "",
  "node_name": "",
  "version": "1.2.0",
  "parallel_max": 3,
  "account_count": 10,
  "running_count": 2,
  "cpu_count": 8,
  "memory_total_mb": 16384,
  "memory_available_mb": 4096
}
```

---

**`GET /api/node/dashboard`**

返回完整仪表盘数据（系统资源、GPU、进程列表、容量评估、采样数据、编年史）。

```
Response 200:
{
  "ok": true,
  "system": { "cpu_pct": 23, "cpu_count": 8, "memory_total_mb": 16384, ... },
  "gpu": { "name": "NVIDIA RTX 3060", "usage": 15, "mem_used_mb": 2048, ... },
  "processes": [{ "aid": "...", "name": "官服大号", "running": true, ... }],
  "capacity": { "parallel_max": 3, "running": 1, "max": 2, "limit_by": "内存", ... },
  "samples": [],
  "gantt": []
}
```

---

### 账号操作

**`POST /api/account/{index}/launch`**

启动单个账号。

```
Response 200:
{"ok": true}

Response 404:
{"ok": false, "error": "account not found"}
```

---

**`POST /api/account/{index}/stop`**

停止单个账号。

```
Response 200:
{"ok": true}
```

---

**`GET /api/account/{index}/screenshot`**

截图。返回 PNG 图片（Content-Type: image/png）。

```
Response 200: (binary PNG data)
Response 400: {"error": "no adb address"}
```

---

### 账号管理

**`GET /api/accounts`**

返回全部账号列表（含运行状态）。

```
Response 200:
{
  "ok": true,
  "accounts": [
    {
      "id": "abc123",
      "name": "官服大号",
      "game_client": "Official",
      "emu_instance_index": "0",
      "account_switch": "",
      "uid": "",
      "running": true,
      "queued": false,
      "failures": 0,
      "suspended": false
    }
  ]
}
```

---

**`POST /api/account`**

创建新账号。

```
Request:
{
  "name": "新账号",
  "game_client": "Official",
  "emu_instance_index": "",
  "account_switch": "",
  "uid": "",
  "note": "",
  "expire_date": ""
}

Response 200:
{"ok": true, "id": "new_uuid"}
```

---

**`POST /api/account/{index}/edit`**

编辑账号字段。

```
Request:
{"name": "新名字", "suspended": true}

Response 200:
{"ok": true}
```

---

**`POST /api/account/{index}/delete`**

删除账号。

```
Response 200:
{"ok": true}
```

---

### 账号配置

**`GET /api/account/{index}/config`**

返回账号的任务设置。

```
Response 200:
{"ok": true, "account_id": "abc123", "task_settings": {...}}
```

---

**`POST /api/account/{index}/config`**

保存账号的任务设置。

```
Request:
{"task_settings": { "FightTask": { "stage": "1-7" } }}

Response 200:
{"ok": true}
```

---

### 队列控制

**`GET /api/queue`**

查看当前启动队列状态。

```
Response 200:
{
  "pending": [
    {
      "account_id": "abc123",
      "account_name": "小号",
      "source": "理智",
      "priority": 2,
      "not_before": "2026-06-07 04:30:00",
      "suspended": false
    }
  ],
  "active": ["def456"],
  "pending_count": 1,
  "active_count": 1,
  "paused": false
}
```

---

**`POST /api/queue/enqueue`**

将账号加入启动队列。

```
Request:
{
  "account_index": 1,
  "source": "manual",
  "priority": 0,
  "not_before": "2026-06-07 04:30:00"
}

Response 200:
{"ok": true, "account_id": "def456", "pending_count": 2}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `account_index` | int | 账号列表中序号 |
| `source` | string | 来源：manual/schedule/sanity |
| `priority` | int | 优先级，0=最高 |
| `not_before` | string | 不早于这个时间启动（可选） |

---

**`POST /api/queue/dequeue`**

从队列中移除账号。

```
Request:
{"account_index": 1}
或
{"account_id": "def456"}

Response 200:
{"ok": true, "account_id": "def456", "pending_count": 0}
```

---

**`POST /api/queue/clear`**

清空队列。

```
Response 200:
{"ok": true}
```

---

**`POST /api/queue/pause`**

暂停队列处理。

```
Response 200:
{"ok": true, "paused": true}
```

---

**`POST /api/queue/resume`**

恢复队列处理。

```
Response 200:
{"ok": true, "paused": false}
```

---

### 集群操作

**`POST /api/action/smart_all`**

一键调度所有可用账号入队。

```
Request:
{ "include_anni": true, "only_anni": false }

Response 200:
{"ok": true, "count": 7}
```

---

**`POST /api/action/smart_selected`**

调度选中账号入队。

```
Request:
{ "account_ids": ["abc123", "def456"], "include_anni": true }

Response 200:
{"ok": true, "count": 2}
```

---

**`POST /api/action/stop_all`**

停止全部运行中的账号和队列。

```
Response 200:
{"ok": true, "stopped": 5}
```

---

### 统计

**`GET /api/stats/dashboard`**

返回全部账号统计汇总（热力图、每日运行、材料掉落）。

```
Response 200:
{
  "ok": true,
  "summary": { "total_runs": 120, "today_runs": 5, "accounts": 10, "total_drops": 350 },
  "heatmap": [[0,0,...], ...],
  "weekdays": ["周一","周二",...],
  "daily_runs": { "2026-06-23": 5 },
  "daily_drops": { "2026-06-23": { "固源岩": 21 } },
  "top_materials": ["固源岩", "赤金", ...]
}
```

---

**`GET /api/account/{index}/stats`**

返回单个账号的完整运行统计。

```
Response 200:
{
  "account_name": "官服大号",
  "running": false,
  "stats": {
    "runs": [
      {
        "ts": "2026-06-06 09:45:29",
        "tasks": {"开始唤醒":"完成", "刷关作战":"完成", "公开招募":"完成"},
        "drops": {"固源岩":21, "赤金":12},
        "sanity": {"current":5, "max":210, "deficit":205}
      }
    ]
  }
}
```

---

**`GET /api/stats`**

返回全部账号统计汇总。

```
Response 200:
{
  "accounts": [
    {
      "index": 0,
      "account_name": "官服大号",
      "running": false,
      "total_runs": 24,
      "stats": { "runs": [...] }
    }
  ]
}
```

---

### 日志

**`GET /api/logs?lines=100`**

读取 `debug.log` 最近 N 行（默认 50）。

```
Response 200:
{
  "lines": [
    "[08:00:01] 流水线启动",
    "[08:00:05] MAA v6.11.1 运行中",
    "..."
  ]
}
```

---

**`GET /api/maa/log?aid={id}&lines=100`**

读取指定账号的 MAA 运行日志（asst.log）。

```
Response 200:
{"lines": ["...", "..."], "name": "官服大号"}
```

---

### 配置

**`GET /api/config`**

获取全部配置。

```
Response 200:
{
  "ok": true,
  "config": {
    "maa_version": "v6.12.0",
    "parallel_max": 8,
    "schedule_mode": "daily",
    ...
  }
}
```

---

**`POST /api/config`**

保存配置。

```
Request:
{ "parallel_max": 8, "schedule_mode": "daily" }

Response 200:
{"ok": true}
```

---

**`POST /api/config/sync`**

下发 MAA `gui.json` 配置到指定账号，覆盖目标账号的 `gui.json` 和 `gui.new.json`。

```
Request:
{
  "account_name": "官服大号",
  "gui_json": {
    "Configurations": {
      "Default": { "Connect.Address": "127.0.0.1:16384", ... }
    }
  }
}

Response 200:
{"ok": true}
```

---

### 模拟器管理

**`GET /api/emulators`**

返回所有检测到的模拟器实例。

```
Response 200:
{"ok": true, "emulators": [{"index": "0", "adb_port": 16384, ...}]}
```

---

**`GET /api/emulator/{idx}`**

返回单个模拟器状态。

```
Response 200:
{"ok": true, "emulator": {"index": "0", ...}}
```

---

**`POST /api/emulator/{idx}/start`**

启动模拟器。

```
Response 200:
{"ok": true, "action": "started"}
```

---

**`POST /api/emulator/{idx}/stop`**

关闭模拟器。

```
Response 200:
{"ok": true, "action": "stopped"}
```

---

**`POST /api/emulator/{idx}/restart`**

重启模拟器。

```
Response 200:
{"ok": true, "action": "restarted"}
```

---

### 系统

**`POST /api/kill_maa`**

杀死所有 MAA.exe 进程。

```
Response 200:
{"ok": true, "killed": 3}
```

---

**`POST /api/system/restart`**

重启 MAAOrch 服务。

```
Response 200:
{"ok": true, "message": "重启中..."}
```

---

**`POST /api/system/close_popups`**

关闭 MuMu 异常弹窗。

```
Response 200:
{"ok": true, "message": "弹窗已关闭"}
```

---

### 更新管理

**`GET /api/maa/check_update`**

检查 MAA 更新。

```
Response 200:
{"ok": true, "has_update": false, "current": "v6.11.1", "latest": "v6.12.0"}
```

---

**`POST /api/maa/download_update`**

后台下载 MAA 更新。

```
Response 200:
{"ok": true, "message": "更新已开始后台下载"}
```

---

**`POST /api/orch/check_update`**

检查 MAAOrch 更新。

```
Response 200:
{"ok": true, "latest": "v1.2.0", "html_url": "https://github.com/..."}
```

---

### SSE 实时推送

**`GET /api/sse`**

服务端推送事件流，每秒推送当前状态。

```
data: {"ok":true,"accounts":[...],"queue":{"count":0},"notifications":[],"ai_insights":[]}
```

客户端示例：

```js
const es = new EventSource('/api/sse');
es.onmessage = e => {
  const data = JSON.parse(e.data);
  updateUI(data.accounts, data.queue);
};
```

---

### 配置导出/导入

**`POST /api/config/export`**

导出账号配置和部分全局配置。

```
Response 200:
{"ok": true, "data": {"accounts": [...], "config": {...}}}
```

---

**`POST /api/config/import`**

导入账号配置。

```
Request:
{"data": {"accounts": [...], "config": {...}}}

Response 200:
{"ok": true, "imported": 3}
```

---

## 错误响应格式

所有错误响应遵循统一格式：

```json
{
  "ok": false,
  "error": "人类可读的错误描述"
}
```

HTTP 状态码遵循 REST 语义：

| 状态码 | 场景 |
|--------|------|
| 200 | 正常响应 |
| 400 | 请求参数错误 |
| 401 | Token 鉴权失败 |
| 403 | 权限不足 |
| 404 | 资源不存在 |
| 429 | 频率限制 |
| 500 | 内部错误 |
| 503 | 服务不可用（如 runner/queue 未初始化） |

## 集成示例

### Node.js 轮询监控

```js
const ORCH = "http://127.0.0.1:19999"
const opts = {
  headers: { "x-agent-token": process.env.ORCH_TOKEN }
}

setInterval(async () => {
  const { accounts, running } =
    await fetch(`${ORCH}/api/status`, opts).then(r => r.json())

  accounts.filter(a => a.running).forEach(a => {
    console.log(`${a.name}: ${a.elapsed}s`)
  })
}, 5000)
```

### Python 定时启动

```python
import requests, time

ORCH = "http://127.0.0.1:19999"
HEADERS = {"x-agent-token": "my-token"}

while True:
    status = requests.get(f"{ORCH}/api/status", headers=HEADERS).json()
    if not status.get("running"):
        r = requests.post(f"{ORCH}/api/action/smart_all", headers=HEADERS)
        print("已调度")
    time.sleep(300)
```

### SSE 实时订阅

```python
import requests, json

ORCH = "http://127.0.0.1:19999"
r = requests.get(f"{ORCH}/api/sse", stream=True)
for line in r.iter_lines():
    if line.startswith(b"data: "):
        data = json.loads(line[6:])
        print(data["accounts"])
```

## 技术实现

`api_fastapi.py` 基于 **FastAPI + uvicorn** 实现，运行在守护线程中：

- 异步事件驱动：uvicorn 负责请求分发，全部处理器同步执行
- SSE 推送：`/api/sse` 使用 `StreamingResponse` + `async for` 实现实时状态推送
- 限流器：200 req/min/IP（`127.0.0.1` 豁免）
- 鉴权：`x-agent-token` header 对比（hmac.compare_digest），空 token 时 Web UI 免登
- 静态文件：catch-all 路由兜底到 `ui/web/`，SPA 单页入口
- 旧版 `api_server.py` 保留备用（基于 `BaseHTTPRequestHandler`）
