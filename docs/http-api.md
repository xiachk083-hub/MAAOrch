# HTTP API 接口文档

## 概述

MAAOrch 启动后自动在 `127.0.0.1` 开�?HTTP REST 服务，供外部调度系统（如任务编排工具、Web 面板）调用�?

## 连接信息

| 项目 | 说明 |
|------|------|
| 地址 | `http://127.0.0.1:{port}` |
| 默认端口 | `19999`（可在设置中修改�?|
| 鉴权 | Header `x-agent-token: {token}`（token 为空字符串则不验证） |
| Content-Type | `application/json` |
| 监听范围 | �?`127.0.0.1`，不暴露公网 |

## 安全机制

| 机制 | 说明 |
|------|------|
| 地址绑定 | 仅监�?`127.0.0.1`（loopback-only�?|
| Token 鉴权 | 可选，通过 `x-agent-token` Header 传�?|
| 频率限制 | �?IP 60 �?分钟，超限返�?HTTP 429 + `Retry-After: 60` |
| CORS | `Access-Control-Allow-Origin: *`，支�?OPTIONS 预检 |
| 自动重启 | 修改端口�?Token 后自动重启服�?|

## 端点参�?

### 状态查�?

**`GET /api/status`**

返回全部账号运行状态�?

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
  "pipeline_running": false
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `accounts[].name` | string | 账号�?|
| `accounts[].index` | int | 在列表中的序�?|
| `accounts[].running` | bool | 是否正在运行 |
| `accounts[].elapsed` | int | 运行秒数（仅在运行中时有意义�?|
| `accounts[].adb` | string | ADB 地址 |
| `accounts[].emu_index` | string | 模拟器实例序�?|
| `pipeline_running` | bool | 流水线是否正在执�?|

---

**`GET /api/account/{index}/status`**

返回单个账号状态。`index` 对应 GUI 中账号列表的序号�?-based）�?

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

### 流水线控�?

**`POST /api/pipeline/start`**

启动流水线�?

```
Request:
(�?body)

Response 200:
{"ok": true}

Response 409 (已在运行):
{"ok": false, "error": "pipeline already running"}
```

---

**`POST /api/pipeline/stop`**

停止流水线�?

```
Response 200:
{"ok": true}
```

---

**`POST /api/pipeline/pause`**

暂停或恢复流水线�?

```
Request:
{"action": "pause"}   // 暂停
{"action": "resume"}  // 恢复

Response 200:
{"ok": true, "state": "paused"}    // �?"running"
```

---

### 账号控制

**`POST /api/account/{index}/launch`**

启动单个账号�?

```
Response 200:
{"ok": true}

Response 404:
{"ok": false, "error": "account not found"}
```

---

### 日志

**`GET /api/logs?lines=100`**

读取 `debug.log` 最�?N 行（默认 50，最�?500）�?

```
Response 200:
{
  "lines": [
    "[08:00:01] 流水线启�?,
    "[08:00:05] MAA v6.11.1 运行�?,
    "..."
  ]
}
```

---

### 队列控制

**`GET /api/queue`**

查看当前启动队列状态�?

```
Response 200:
{
  "pending": [
    {
      "account_id": "abc123",
      "account_name": "小号",
      "source": "理智",
      "priority": 2,
      "not_before": "2026-06-07 04:30:00"
    }
  ],
  "active": ["def456"],
  "pending_count": 1,
  "active_count": 1
}
```

---

**`POST /api/queue/enqueue`**

将账号加入启动队列�?

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
| `account_index` | int | 账号列表中序�?|
| `source` | string | 来源：manual/schedule/sanity |
| `priority` | int | 优先级，0=最�?|
| `not_before` | string | 不早于这个时间启动（可选） |

---

**`POST /api/queue/dequeue`**

从队列中移除账号�?

```
Request:
{"account_index": 1}
�?
{"account_id": "def456"}

Response 200:
{"ok": true, "account_id": "def456", "pending_count": 0}
```

---

### 统计

**`GET /api/account/{index}/stats`**

返回单个账号的完整运行统计（`stats.json` 内容 + 当前状态）�?

```
Response 200:
{
  "account_name": "官服大号",
  "running": false,
  "stats": {
    "runs": [
      {
        "ts": "2026-06-06 09:45:29",
        "tasks": {"开始唤�?:"完成", "刷关作战":"完成", "公开招募":"完成"},
        "drops": {"固源�?:21, "赤金":12},
        "sanity": {"current":5, "max":210, "deficit":205}
      }
    ]
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `account_name` | string | 账号�?|
| `running` | bool | 是否正在运行 |
| `stats.runs[]` | array | 历史运行记录 |
| `stats.runs[].ts` | string | 完成时间 |
| `stats.runs[].tasks` | object | 任务名→状�?|
| `stats.runs[].drops` | object | 材料名→数量 |
| `stats.runs[].sanity` | object | 剩余理智 |

---

**`GET /api/stats`**

返回全部账号统计汇总�?

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

### 配置同步

**`POST /api/config/sync`**

下发 MAA `gui.json` 配置到指定账号，覆盖目标账号�?`gui.json` �?`gui.new.json`�?

```
Request:
{
  "account_name": "官服大号",
  "gui_json": {
    "Configurations": {
      "Default": {
        "Connect.Address": "127.0.0.1:16384",
        ...
      }
    }
  }
}

Response 200:
{"ok": true}

Response 404 (账号不存�?:
{"ok": false, "error": "account not found"}
```

## 错误响应格式

所有错误响应遵循统一格式�?

```json
{
  "ok": false,
  "error": "人类可读的错误描�?
}
```

HTTP 状态码遵循 REST 语义�?

| 状态码 | 场景 |
|--------|------|
| 200 | 正常响应 |
| 404 | 资源不存�?|
| 409 | 冲突（如重复启动�?|
| 429 | 频率限制 |
| 500 | 内部错误 |

## 集成示例

### Node.js 轮询监控

```js
const ORCH = "http://127.0.0.1:19999"
const opts = {
  headers: { "x-agent-token": process.env.ORCH_TOKEN }
}

setInterval(async () => {
  const { accounts, pipeline_running } =
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
    if not status["pipeline_running"]:
        requests.post(f"{ORCH}/api/pipeline/start", headers=HEADERS)
        print("流水线已启动")
    time.sleep(300)  # �?5 分钟检查一�?
```

### curl 命令�?

```bash
# 查看状�?
curl http://127.0.0.1:19999/api/status -H "x-agent-token: my-token"

# 启动流水�?
curl -X POST http://127.0.0.1:19999/api/pipeline/start -H "x-agent-token: my-token"

# 暂停流水�?
curl -X POST http://127.0.0.1:19999/api/pipeline/pause \
  -H "Content-Type: application/json" \
  -H "x-agent-token: my-token" \
  -d '{"action":"pause"}'
```

## 技术实�?

`ApiServer`（`api_server.py`）继�?`QThread`，基�?Python 标准�?`http.server.HTTPServer` + `BaseHTTPRequestHandler` 实现�?

- 线程隔离：HTTP 服务器运行在独立 QThread �?
- 信号通信：日志信息通过 `log_msg` Signal 发送到主线�?
- 实例替换：修改端�?Token 时先停止旧实例再启动新实�?
- 优雅退出：关闭窗口时自动调�?`stop_server()`
