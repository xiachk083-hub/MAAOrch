# Daigan ↔ MAAOrch 集成需求文档

## 背景

MAAOrch v1.0.0 已内置 HTTP API 服务（端口 19999），Daigan 调度引擎可直接通过 REST 接口控制本地 MAA 执行。替代原 `automation-agent.js` 的 spawn + log 监控模式。

## MAAOrch API 参考

- **地址**: `http://127.0.0.1:19999`
- **鉴权**: Header `x-agent-token: <token>`（token 在 MAAOrch 设置中配置）
- **Content-Type**: `application/json`

### 端点一览

| 方法 | 路径 | Body | 返回 |
|------|------|------|------|
| GET | `/api/status` | - | `{accounts:[{name,index,running,elapsed,adb,emu_index}], pipeline_running:bool}` |
| GET | `/api/account/{index}/status` | - | `{name, running, elapsed}` |
| GET | `/api/logs?lines=100` | - | `{lines:[...]}` |
| POST | `/api/pipeline/start` | - | `{ok:true}` |
| POST | `/api/pipeline/stop` | - | `{ok:true}` |
| POST | `/api/pipeline/pause` | `{action:"pause"|"resume"}` | `{ok:true, state:"paused"|"running"}` |
| POST | `/api/account/{index}/launch` | - | `{ok:true}` |
| POST | `/api/config/sync` | `{account_name, gui_json}` | `{ok:true}` |

## 改造范围

### 1. 引擎层 (`server/automation/engine.js`)

**现状**: 定时轮询 → 找空闲 agent → 下发 session → agent 拉取执行
**改为**: 定时轮询 → 直接调 MAAOrch API 启动 → 定时查状态 → 写回 sessions

```js
// engine.js 调度循环改为:
async function tick() {
  const url = "http://127.0.0.1:19999"
  const opts = { headers: { "x-agent-token": config.apiToken } }

  // 1. 查当前状态
  const { pipeline_running, accounts } = await fetch(`${url}/api/status`, opts).then(r => r.json())

  // 2. 如果有到期该执行的 schedule 且 pipeline 空闲 → 启动
  if (hasPendingSchedule() && !pipeline_running) {
    await fetch(`${url}/api/pipeline/start`, { method: "POST", ...opts })
  }

  // 3. 把 accounts 状态写回 automation_sessions 表
  for (const a of accounts) {
    if (a.running) updateSession(a.name, "running", a.elapsed)
    else updateSession(a.name, a.running ? "running" : "idle")
  }
}
```

### 2. 配置同步 (`server/automation/routes/schedules.js`)

- 新增端点 `POST /api/automation/schedules/:id/push-config`
- 把 `maa_configs` 表中的 `gui_json` 下发到 MAAOrch：

```js
router.post("/:id/push-config", async (req, res) => {
  const schedule = await db.getSchedule(req.params.id)
  for (const item of schedule.queue_order) {
    const config = await db.getMaaConfig(item.account_id)
    await fetch("http://127.0.0.1:19999/api/config/sync", {
      method: "POST",
      headers: { "x-agent-token": config.apiToken, "Content-Type": "application/json" },
      body: JSON.stringify({ account_name: item.account_name, gui_json: config.gui_json })
    })
  }
  res.json({ ok: true })
})
```

### 3. 前端仪表盘 (`frontend/src/components/AutomationView.vue`)

新增面板展示 MAAOrch 实时状态：

```vue
<template>
  <div class="maaorch-status">
    <h3>MAAOrch 状态</h3>
    <div v-if="status">
      <span :class="status.pipeline_running ? 'running' : 'idle'">
        {{ status.pipeline_running ? '流水线运行中' : '空闲' }}
      </span>
      <table>
        <tr v-for="a in status.accounts" :key="a.index">
          <td>{{ a.name }}</td>
          <td>{{ a.adb }}</td>
          <td :class="a.running ? 'on' : 'off'">{{ a.running ? `运行 ${a.elapsed}s` : '离线' }}</td>
        </tr>
      </table>
    </div>
    <div v-else class="error">无法连接 MAAOrch</div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
const status = ref(null); let timer
onMounted(() => {
  const fetchStatus = () => fetch("http://127.0.0.1:19999/api/status").then(r => r.json()).then(d => status.value = d).catch(() => status.value = null)
  fetchStatus(); timer = setInterval(fetchStatus, 5000)
})
onUnmounted(() => clearInterval(timer))
</script>
```

### 4. 管理后台配置 (`frontend/src/components/SettingsView.vue` 或等价)

用现有的 settings 页加两项：

- **MAAOrch 端口**: 默认 `19999`
- **MAAOrch Token**: 和 MAAOrch 设置中的 token 一致

存到 `config` 表或 `.env`。

### 5. 干掉旧 Agent（可选）

上线稳定后可移除：
- `automation-agent.js` — MAAOrch API 替代
- `agent.js` — MAAOrch 模拟器管理已内置（保留也可，做模拟器远程控制用）
- `server/automation/routes/agent.js` 中的 agent polling 逻辑 — API 用不到了

## 迁移步骤

| 阶段 | 内容 | 预估 |
|------|------|------|
| Phase 1 | 后端加 config 表字段 `api_port`/`api_token`，engine.js 改调 MAAOrch API | 半天 |
| Phase 2 | 前端加 MAAOrch 状态面板 + 设置页 | 半天 |
| Phase 3 | 配置同步 flow（schedule → push-config → MAAOrch） | 半天 |
| Phase 4 | 联调测试，下线旧 agent | 1 天 |

## MAAOrch 侧已就绪

- 启动后自动开启 HTTP 服务
- 设置 → HTTP API 可配端口和 token
- 只监听 127.0.0.1，不暴露公网
- 退出时自动关闭服务
