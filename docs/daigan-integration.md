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

---

## 运行统计数据 (daigan 对接)

### 概述

MAAOrch 每次任务完成后，将运行结果写入 `accounts/{account_id}/stats.json`。此文件可在本地读取，也可通过 API 获取。数据按次累积，支持月报、年报统计。

### 数据格式 (stats.json)

位置：`accounts/{account_id}/stats.json`

```json
{
  "runs": [
    {
      "ts": "2026-06-06 09:45:29",
      "tasks": {
        "开始唤醒": "完成",
        "刷关作战": "完成",
        "公开招募": "完成",
        "基建换班": "完成",
        "信用商店": "完成",
        "领取奖励": "完成"
      },
      "drops": {
        "固源岩": 21,
        "赤金": 12,
        "源岩": 2,
        "龙门币": 2592
      },
      "sanity": {
        "current": 5,
        "max": 210,
        "deficit": 205
      }
    }
  ],
  "last_read_line": 0
}
```

### 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `runs[]` | array | 历史运行记录，按时间倒序 |
| `runs[].ts` | string | 完成时间 (YYYY-MM-DD HH:MM:SS) |
| `runs[].tasks` | object | 任务名 → 状态。状态：`完成` / `失败` / `运行中` |
| `runs[].drops` | object | 材料名称 → 本次累计掉落数量 |
| `runs[].sanity` | object | 剩余理智信息 |
| `runs[].sanity.current` | int | 当前理智值 |
| `runs[].sanity.max` | int | 理智上限 |
| `runs[].sanity.deficit` | int | 距满需恢复的点数 (= max - current) |

### 可用任务名

| 英文 | 中文 | 说明 |
|------|------|------|
| StartUp | 开始唤醒 | 启动游戏、切换账号 |
| Fight | 刷关作战 | 理智刷关 |
| Recruit | 公开招募 | 自动公招 |
| Infrast | 基建换班 | 智能基建排班 |
| Mall | 信用商店 | 信用采购、收取 |
| Award | 领取奖励 | 每日/每周奖励 |
| Roguelike | 肉鸽探索 | 集成战略 |
| Reclamation | 生息演算 | 生息演算 |
| CloseDown | 关闭游戏 | 关闭明日方舟 |

### 获取方式

#### 方式 A：本地文件读取（MAAOrch 和 daigan 在同一台机器）

直接读取 JSON 文件：
```
路径: {MAAOrch安装目录}/accounts/{account_id}/stats.json
```

Node.js 示例：
```js
const fs = require("fs")
const path = require("path")
const data = JSON.parse(fs.readFileSync(
  path.join(maaOrchDir, "accounts", accountId, "stats.json"), "utf-8"
))
```

#### 方式 B：HTTP 推送（MAAOrch → daigan）

MAAOrch 设置面板配置 daigan 地址后，每次 MAA 任务完成会自动 POST 到 daigan：

```
POST {daigan_url}/api/maa/stats
Content-Type: application/json

{
  "account_name": "官服大号",
  "account_id": "abc123",
  "ts": "2026-06-06 09:45:29",
  "tasks": {"开始唤醒":"完成", "刷关作战":"完成", "公开招募":"完成"},
  "drops": {"固源岩":21, "赤金":12},
  "sanity": {"current":5, "max":210, "deficit":205, "report_time":"2026-06-06 09:36:33"}
}
```

daigan 侧只需实现 `POST /api/maa/stats` 接收端点即可。

### 方式 C：HTTP API（远程 / 跨机器）

**`GET /api/account/{index}/stats`** (计划中)

返回账号的完整 stats.json 内容 + 当前运行状态。

```json
{
  "account_name": "官服大号",
  "running": false,
  "stats": { /* stats.json 完整内容 */ }
}
```

当前可通过 `GET /api/status` + 本地文件读取组合实现。

### 统计计算示例

#### 月报

```js
function monthlyReport(runs, yearMonth) {
  const monthRuns = runs.filter(r => r.ts.startsWith(yearMonth))
  return {
    total_runs: monthRuns.length,
    task_success: {} // { "刷关作战": { success: 58, fail: 2 } },
    total_drops: {}, // { "固源岩": 1240, "赤金": 720 }
    avg_sanity: 0,   // 平均结束理智
  }
}
```

#### 年报 / 趋势

```js
function yearlySummary(runs) {
  const byMonth = {}
  runs.forEach(r => {
    const month = r.ts.substring(0, 7) // "2026-06"
    byMonth[month] = (byMonth[month] || 0) + 1
  })
  return byMonth // { "2026-06": 58, "2026-07": 62 }
}
```

### 理智恢复计算

```
恢复 1 点理智 = 6 分钟
恢复至满 = deficit × 6 分钟

示例: deficit = 205, 恢复至满 = 205 × 6 = 1230 分钟 ≈ 20.5 小时
```

daigan 可据此计算下次最佳启动时间，用于定时调度。

---

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
