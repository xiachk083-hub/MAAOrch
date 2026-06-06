# HTTP API 接口文档

## 概述

MAAOrch 启动后自动在 `127.0.0.1` 开启 HTTP REST 服务，供外部调度系统（如任务编排工具、Web 面板）调用。

## 连接信息

| 项目 | 说明 |
|------|------|
| 地址 | `http://127.0.0.1:{port}` |
| 默认端口 | `19999`（可在设置中修改） |
| 鉴权 | Header `x-agent-token: {token}`（token 为空字符串则不验证） |
| Content-Type | `application/json` |
| 监听范围 | 仅 `127.0.0.1`，不暴露公网 |

## 安全机制

| 机制 | 说明 |
|------|------|
| 地址绑定 | 仅监听 `127.0.0.1`（loopback-only） |
| Token 鉴权 | 可选，通过 `x-agent-token` Header 传递 |
| 频率限制 | 每 IP 60 次/分钟，超限返回 HTTP 429 + `Retry-After: 60` |
| CORS | `Access-Control-Allow-Origin: *`，支持 OPTIONS 预检 |
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
  "pipeline_running": false
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `accounts[].name` | string | 账号名 |
| `accounts[].index` | int | 在列表中的序号 |
| `accounts[].running` | bool | 是否正在运行 |
| `accounts[].elapsed` | int | 运行秒数（仅在运行中时有意义） |
| `accounts[].adb` | string | ADB 地址 |
| `accounts[].emu_index` | string | 模拟器实例序号 |
| `pipeline_running` | bool | 流水线是否正在执行 |

---

**`GET /api/account/{index}/status`**

返回单个账号状态。`index` 对应 GUI 中账号列表的序号（0-based）。

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

### 流水线控制

**`POST /api/pipeline/start`**

启动流水线。

```
Request:
(空 body)

Response 200:
{"ok": true}

Response 409 (已在运行):
{"ok": false, "error": "pipeline already running"}
```

---

**`POST /api/pipeline/stop`**

停止流水线。

```
Response 200:
{"ok": true}
```

---

**`POST /api/pipeline/pause`**

暂停或恢复流水线。

```
Request:
{"action": "pause"}   // 暂停
{"action": "resume"}  // 恢复

Response 200:
{"ok": true, "state": "paused"}    // 或 "running"
```

---

### 账号控制

**`POST /api/account/{index}/launch`**

启动单个账号。

```
Response 200:
{"ok": true}

Response 404:
{"ok": false, "error": "account not found"}
```

---

### 日志

**`GET /api/logs?lines=100`**

读取 `debug.log` 最近 N 行（默认 50，最大 500）。

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

### 统计

**`GET /api/account/{index}/stats`**

返回单个账号的完整运行统计（`stats.json` 内容 + 当前状态）。

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

| 字段 | 类型 | 说明 |
|------|------|------|
| `account_name` | string | 账号名 |
| `running` | bool | 是否正在运行 |
| `stats.runs[]` | array | 历史运行记录 |
| `stats.runs[].ts` | string | 完成时间 |
| `stats.runs[].tasks` | object | 任务名→状态 |
| `stats.runs[].drops` | object | 材料名→数量 |
| `stats.runs[].sanity` | object | 剩余理智 |

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

### 配置同步

**`POST /api/config/sync`**

下发 MAA `gui.json` 配置到指定账号，覆盖目标账号的 `gui.json` 和 `gui.new.json`。

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

Response 404 (账号不存在):
{"ok": false, "error": "account not found"}
```

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
| 404 | 资源不存在 |
| 409 | 冲突（如重复启动） |
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
    time.sleep(300)  # 每 5 分钟检查一次
```

### curl 命令行

```bash
# 查看状态
curl http://127.0.0.1:19999/api/status -H "x-agent-token: my-token"

# 启动流水线
curl -X POST http://127.0.0.1:19999/api/pipeline/start -H "x-agent-token: my-token"

# 暂停流水线
curl -X POST http://127.0.0.1:19999/api/pipeline/pause \
  -H "Content-Type: application/json" \
  -H "x-agent-token: my-token" \
  -d '{"action":"pause"}'
```

## 技术实现

`ApiServer`（`api_server.py`）继承 `QThread`，基于 Python 标准库 `http.server.HTTPServer` + `BaseHTTPRequestHandler` 实现：

- 线程隔离：HTTP 服务器运行在独立 QThread 中
- 信号通信：日志信息通过 `log_msg` Signal 发送到主线程
- 实例替换：修改端口/Token 时先停止旧实例再启动新实例
- 优雅退出：关闭窗口时自动调用 `stop_server()`
