# MAAOrch HTTP API 参考

MAAOrch 启动后自动在 `127.0.0.1` 开启 REST 服务，供外部调度系统调用。

## 连接

- **地址**: `http://127.0.0.1:19999`（端口可在设置中修改）
- **鉴权**: Header `x-agent-token: <token>`（token 为空则不验证）
- **Content-Type**: `application/json`

## 端点

### 状态查询

**`GET /api/status`**

返回所有账号运行状态和流水线状态。

```json
{
  "accounts": [
    {"name": "官服大号", "index": 0, "running": true, "elapsed": 360, "adb": "127.0.0.1:16384", "emu_index": "0"}
  ],
  "pipeline_running": false
}
```

**`GET /api/account/{index}/status`**

单个账号状态。`index` 对应 MAAOrch 账号列表中的序号。

```json
{"name": "官服大号", "running": true, "elapsed": 120}
```

### 流水线控制

**`POST /api/pipeline/start`** — 启动流水线

```js
fetch("http://127.0.0.1:19999/api/pipeline/start", { method: "POST" })
// → {"ok": true}
```

**`POST /api/pipeline/stop`** — 停止流水线

**`POST /api/pipeline/pause`** — 暂停/恢复

```js
fetch("http://127.0.0.1:19999/api/pipeline/pause", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ action: "pause" }) // 或 "resume"
})
// → {"ok": true, "state": "paused"}
```

### 账号控制

**`POST /api/account/{index}/launch`** — 启动单个账号

### 日志

**`GET /api/logs?lines=100`** — 最近 N 行 debug.log

```json
{"lines": ["[08:00:01] 启动", "[08:00:05] MAA v6.11.1 运行中", "..."]}
```

### 配置同步

**`POST /api/config/sync`** — 下发 MAA gui.json 配置到指定账号

```js
fetch("http://127.0.0.1:19999/api/config/sync", {
  method: "POST",
  headers: { "Content-Type": "application/json", "x-agent-token": "your-token" },
  body: JSON.stringify({
    account_name: "官服大号",
    gui_json: { /* MAA gui.json 配置对象 */ }
  })
})
// → {"ok": true}
```

## 集成示例（Node.js）

```js
const ORCH = "http://127.0.0.1:19999"
const opts = { headers: { "x-agent-token": process.env.ORCH_TOKEN } }

// 定时轮询状态
setInterval(async () => {
  const { accounts, pipeline_running } = await fetch(`${ORCH}/api/status`, opts).then(r => r.json())
  accounts.filter(a => a.running).forEach(a => {
    console.log(`${a.name}: 运行中 ${a.elapsed}s`)
  })
}, 5000)

// 按计划启动
async function startIfIdle() {
  const { pipeline_running } = await fetch(`${ORCH}/api/status`, opts).then(r => r.json())
  if (!pipeline_running) {
    await fetch(`${ORCH}/api/pipeline/start`, { method: "POST", ...opts })
  }
}
```

## 安全

- 仅监听 `127.0.0.1`，不暴露公网
- Token 可选，建议生产环境设置
- 退出时自动关闭服务
