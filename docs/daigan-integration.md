# MAAOrch HTTP API 鍙傝€?

MAAOrch 鍚姩鍚庤嚜鍔ㄥ湪 `127.0.0.1` 寮€鍚?REST 鏈嶅姟锛屼緵澶栭儴璋冨害绯荤粺璋冪敤銆?

## 杩炴帴

- **鍦板潃**: `http://127.0.0.1:19999`锛堢鍙ｅ彲鍦ㄨ缃腑淇敼锛?
- **閴存潈**: Header `x-agent-token: <token>`锛坱oken 涓虹┖鍒欎笉楠岃瘉锛?
- **Content-Type**: `application/json`

## 绔偣

### 鐘舵€佹煡璇?

**`GET /api/status`**

杩斿洖鎵€鏈夎处鍙疯繍琛岀姸鎬佸拰娴佹按绾跨姸鎬併€?

```json
{
  "accounts": [
    {"name": "瀹樻湇澶у彿", "index": 0, "running": true, "elapsed": 360, "adb": "127.0.0.1:16384", "emu_index": "0"}
  ],
  "pipeline_running": false
}
```

**`GET /api/account/{index}/status`**

鍗曚釜璐﹀彿鐘舵€併€俙index` 瀵瑰簲 MAAOrch 璐﹀彿鍒楄〃涓殑搴忓彿銆?

```json
{"name": "瀹樻湇澶у彿", "running": true, "elapsed": 120}
```

### 娴佹按绾挎帶鍒?

**`POST /api/pipeline/start`** 鈥?鍚姩娴佹按绾?

```js
fetch("http://127.0.0.1:19999/api/pipeline/start", { method: "POST" })
// 鈫?{"ok": true}
```

**`POST /api/pipeline/stop`** 鈥?鍋滄娴佹按绾?

**`POST /api/pipeline/pause`** 鈥?鏆傚仠/鎭㈠

```js
fetch("http://127.0.0.1:19999/api/pipeline/pause", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ action: "pause" }) // 鎴?"resume"
})
// 鈫?{"ok": true, "state": "paused"}
```

### 璐﹀彿鎺у埗

**`POST /api/account/{index}/launch`** 鈥?鍚姩鍗曚釜璐﹀彿

### 鏃ュ織

**`GET /api/logs?lines=100`** 鈥?鏈€杩?N 琛?debug.log

```json
{"lines": ["[08:00:01] 鍚姩", "[08:00:05] MAA v6.11.1 杩愯涓?, "..."]}
```

### 閰嶇疆鍚屾

**`POST /api/config/sync`** 鈥?涓嬪彂 MAA gui.json 閰嶇疆鍒版寚瀹氳处鍙?

```js
fetch("http://127.0.0.1:19999/api/config/sync", {
  method: "POST",
  headers: { "Content-Type": "application/json", "x-agent-token": "your-token" },
  body: JSON.stringify({
    account_name: "瀹樻湇澶у彿",
    gui_json: { /* MAA gui.json 閰嶇疆瀵硅薄 */ }
  })
})
// 鈫?{"ok": true}
```

---

## 杩愯缁熻鏁版嵁 (daigan 瀵规帴)

### 姒傝堪

MAAOrch 姣忔浠诲姟瀹屾垚鍚庯紝灏嗚繍琛岀粨鏋滃啓鍏?`accounts/{account_id}/stats.json`銆傛鏂囦欢鍙湪鏈湴璇诲彇锛屼篃鍙€氳繃 API 鑾峰彇銆傛暟鎹寜娆＄疮绉紝鏀寔鏈堟姤銆佸勾鎶ョ粺璁°€?

### 鏁版嵁鏍煎紡 (stats.json)

浣嶇疆锛歚accounts/{account_id}/stats.json`

```json
{
  "runs": [
    {
      "ts": "2026-06-06 09:45:29",
      "tasks": {
        "寮€濮嬪敜閱?: "瀹屾垚",
        "鍒峰叧浣滄垬": "瀹屾垚",
        "鍏紑鎷涘嫙": "瀹屾垚",
        "鍩哄缓鎹㈢彮": "瀹屾垚",
        "淇＄敤鍟嗗簵": "瀹屾垚",
        "棰嗗彇濂栧姳": "瀹屾垚"
      },
      "drops": {
        "鍥烘簮宀?: 21,
        "璧ら噾": 12,
        "婧愬博": 2,
        "榫欓棬甯?: 2592
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

### 瀛楁璇存槑

| 瀛楁 | 绫诲瀷 | 璇存槑 |
|------|------|------|
| `runs[]` | array | 鍘嗗彶杩愯璁板綍锛屾寜鏃堕棿鍊掑簭 |
| `runs[].ts` | string | 瀹屾垚鏃堕棿 (YYYY-MM-DD HH:MM:SS) |
| `runs[].tasks` | object | 浠诲姟鍚?鈫?鐘舵€併€傜姸鎬侊細`瀹屾垚` / `澶辫触` / `杩愯涓璥 |
| `runs[].drops` | object | 鏉愭枡鍚嶇О 鈫?鏈绱鎺夎惤鏁伴噺 |
| `runs[].sanity` | object | 鍓╀綑鐞嗘櫤淇℃伅 |
| `runs[].sanity.current` | int | 褰撳墠鐞嗘櫤鍊?|
| `runs[].sanity.max` | int | 鐞嗘櫤涓婇檺 |
| `runs[].sanity.deficit` | int | 璺濇弧闇€鎭㈠鐨勭偣鏁?(= max - current) |

### 鍙敤浠诲姟鍚?

| 鑻辨枃 | 涓枃 | 璇存槑 |
|------|------|------|
| StartUp | 寮€濮嬪敜閱?| 鍚姩娓告垙銆佸垏鎹㈣处鍙?|
| Fight | 鍒峰叧浣滄垬 | 鐞嗘櫤鍒峰叧 |
| Recruit | 鍏紑鎷涘嫙 | 鑷姩鍏嫑 |
| Infrast | 鍩哄缓鎹㈢彮 | 鏅鸿兘鍩哄缓鎺掔彮 |
| Mall | 淇＄敤鍟嗗簵 | 淇＄敤閲囪喘銆佹敹鍙?|
| Award | 棰嗗彇濂栧姳 | 姣忔棩/姣忓懆濂栧姳 |
| Roguelike | 鑲夐附鎺㈢储 | 闆嗘垚鎴樼暐 |
| Reclamation | 鐢熸伅婕旂畻 | 鐢熸伅婕旂畻 |
| CloseDown | 鍏抽棴娓告垙 | 鍏抽棴鏄庢棩鏂硅垷 |

### 鑾峰彇鏂瑰紡

#### 鏂瑰紡 A锛氭湰鍦版枃浠惰鍙栵紙MAAOrch 鍜?daigan 鍦ㄥ悓涓€鍙版満鍣級

鐩存帴璇诲彇 JSON 鏂囦欢锛?
```
璺緞: {MAAOrch瀹夎鐩綍}/accounts/{account_id}/stats.json
```

Node.js 绀轰緥锛?
```js
const fs = require("fs")
const path = require("path")
const data = JSON.parse(fs.readFileSync(
  path.join(maaOrchDir, "accounts", accountId, "stats.json"), "utf-8"
))
```

#### 鏂瑰紡 B锛欻TTP 鎺ㄩ€侊紙MAAOrch 鈫?daigan锛?

MAAOrch 璁剧疆闈㈡澘閰嶇疆 daigan 鍦板潃鍚庯紝姣忔 MAA 浠诲姟瀹屾垚浼氳嚜鍔?POST 鍒?daigan锛?

```
POST {daigan_url}/api/maa/stats
Content-Type: application/json

{
  "account_name": "瀹樻湇澶у彿",
  "account_id": "abc123",
  "ts": "2026-06-06 09:45:29",
  "tasks": {"寮€濮嬪敜閱?:"瀹屾垚", "鍒峰叧浣滄垬":"瀹屾垚", "鍏紑鎷涘嫙":"瀹屾垚"},
  "drops": {"鍥烘簮宀?:21, "璧ら噾":12},
  "sanity": {"current":5, "max":210, "deficit":205, "report_time":"2026-06-06 09:36:33"}
}
```

daigan 渚у彧闇€瀹炵幇 `POST /api/maa/stats` 鎺ユ敹绔偣鍗冲彲銆?

### 鏂瑰紡 C锛欻TTP API锛堣繙绋?/ 璺ㄦ満鍣級

**`GET /api/account/{index}/stats`** (璁″垝涓?

杩斿洖璐﹀彿鐨勫畬鏁?stats.json 鍐呭 + 褰撳墠杩愯鐘舵€併€?

```json
{
  "account_name": "瀹樻湇澶у彿",
  "running": false,
  "stats": { /* stats.json 瀹屾暣鍐呭 */ }
}
```

褰撳墠鍙€氳繃 `GET /api/status` + 鏈湴鏂囦欢璇诲彇缁勫悎瀹炵幇銆?

### 缁熻璁＄畻绀轰緥

#### 鏈堟姤

```js
function monthlyReport(runs, yearMonth) {
  const monthRuns = runs.filter(r => r.ts.startsWith(yearMonth))
  return {
    total_runs: monthRuns.length,
    task_success: {} // { "鍒峰叧浣滄垬": { success: 58, fail: 2 } },
    total_drops: {}, // { "鍥烘簮宀?: 1240, "璧ら噾": 720 }
    avg_sanity: 0,   // 骞冲潎缁撴潫鐞嗘櫤
  }
}
```

#### 骞存姤 / 瓒嬪娍

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

### 鐞嗘櫤鎭㈠璁＄畻

```
鎭㈠ 1 鐐圭悊鏅?= 6 鍒嗛挓
鎭㈠鑷虫弧 = deficit 脳 6 鍒嗛挓

绀轰緥: deficit = 205, 鎭㈠鑷虫弧 = 205 脳 6 = 1230 鍒嗛挓 鈮?20.5 灏忔椂
```

daigan 鍙嵁姝よ绠椾笅娆℃渶浣冲惎鍔ㄦ椂闂达紝鐢ㄤ簬瀹氭椂璋冨害銆?

---

## 闆嗘垚绀轰緥锛圢ode.js锛?

```js
const ORCH = "http://127.0.0.1:19999"
const opts = { headers: { "x-agent-token": process.env.ORCH_TOKEN } }

// 瀹氭椂杞鐘舵€?
setInterval(async () => {
  const { accounts, pipeline_running } = await fetch(`${ORCH}/api/status`, opts).then(r => r.json())
  accounts.filter(a => a.running).forEach(a => {
    console.log(`${a.name}: 杩愯涓?${a.elapsed}s`)
  })
}, 5000)

// 鎸夎鍒掑惎鍔?
async function startIfIdle() {
  const { pipeline_running } = await fetch(`${ORCH}/api/status`, opts).then(r => r.json())
  if (!pipeline_running) {
    await fetch(`${ORCH}/api/pipeline/start`, { method: "POST", ...opts })
  }
}
```

## 瀹夊叏

- 浠呯洃鍚?`127.0.0.1`锛屼笉鏆撮湶鍏綉
- Token 鍙€夛紝寤鸿鐢熶骇鐜璁剧疆
- 閫€鍑烘椂鑷姩鍏抽棴鏈嶅姟
