# HTTP API 鎺ュ彛鏂囨。

## 姒傝堪

MAAOrch 鍚姩鍚庤嚜鍔ㄥ湪 `127.0.0.1` 寮€鍚?HTTP REST 鏈嶅姟锛屼緵澶栭儴璋冨害绯荤粺锛堝浠诲姟缂栨帓宸ュ叿銆乄eb 闈㈡澘锛夎皟鐢ㄣ€?

## 杩炴帴淇℃伅

| 椤圭洰 | 璇存槑 |
|------|------|
| 鍦板潃 | `http://127.0.0.1:{port}` |
| 榛樿绔彛 | `19999`锛堝彲鍦ㄨ缃腑淇敼锛?|
| 閴存潈 | Header `x-agent-token: {token}`锛坱oken 涓虹┖瀛楃涓插垯涓嶉獙璇侊級 |
| Content-Type | `application/json` |
| 鐩戝惉鑼冨洿 | 浠?`127.0.0.1`锛屼笉鏆撮湶鍏綉 |

## 瀹夊叏鏈哄埗

| 鏈哄埗 | 璇存槑 |
|------|------|
| 鍦板潃缁戝畾 | 浠呯洃鍚?`127.0.0.1`锛坙oopback-only锛?|
| Token 閴存潈 | 鍙€夛紝閫氳繃 `x-agent-token` Header 浼犻€?|
| 棰戠巼闄愬埗 | 姣?IP 60 娆?鍒嗛挓锛岃秴闄愯繑鍥?HTTP 429 + `Retry-After: 60` |
| CORS | `Access-Control-Allow-Origin: *`锛屾敮鎸?OPTIONS 棰勬 |
| 鑷姩閲嶅惎 | 淇敼绔彛鎴?Token 鍚庤嚜鍔ㄩ噸鍚湇鍔?|

## 绔偣鍙傝€?

### 鐘舵€佹煡璇?

**`GET /api/status`**

杩斿洖鍏ㄩ儴璐﹀彿杩愯鐘舵€併€?

```
Response 200:
{
  "accounts": [
    {
      "name": "瀹樻湇澶у彿",
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

| 瀛楁 | 绫诲瀷 | 璇存槑 |
|------|------|------|
| `accounts[].name` | string | 璐﹀彿鍚?|
| `accounts[].index` | int | 鍦ㄥ垪琛ㄤ腑鐨勫簭鍙?|
| `accounts[].running` | bool | 鏄惁姝ｅ湪杩愯 |
| `accounts[].elapsed` | int | 杩愯绉掓暟锛堜粎鍦ㄨ繍琛屼腑鏃舵湁鎰忎箟锛?|
| `accounts[].adb` | string | ADB 鍦板潃 |
| `accounts[].emu_index` | string | 妯℃嫙鍣ㄥ疄渚嬪簭鍙?|
| `pipeline_running` | bool | 娴佹按绾挎槸鍚︽鍦ㄦ墽琛?|

---

**`GET /api/account/{index}/status`**

杩斿洖鍗曚釜璐﹀彿鐘舵€併€俙index` 瀵瑰簲 GUI 涓处鍙峰垪琛ㄧ殑搴忓彿锛?-based锛夈€?

```
Response 200:
{
  "name": "瀹樻湇澶у彿",
  "running": true,
  "elapsed": 120
}

Response 404 (index 瓒婄晫):
{"error": "account not found"}
```

---

### 娴佹按绾挎帶鍒?

**`POST /api/pipeline/start`**

鍚姩娴佹按绾裤€?

```
Request:
(绌?body)

Response 200:
{"ok": true}

Response 409 (宸插湪杩愯):
{"ok": false, "error": "pipeline already running"}
```

---

**`POST /api/pipeline/stop`**

鍋滄娴佹按绾裤€?

```
Response 200:
{"ok": true}
```

---

**`POST /api/pipeline/pause`**

鏆傚仠鎴栨仮澶嶆祦姘寸嚎銆?

```
Request:
{"action": "pause"}   // 鏆傚仠
{"action": "resume"}  // 鎭㈠

Response 200:
{"ok": true, "state": "paused"}    // 鎴?"running"
```

---

### 璐﹀彿鎺у埗

**`POST /api/account/{index}/launch`**

鍚姩鍗曚釜璐﹀彿銆?

```
Response 200:
{"ok": true}

Response 404:
{"ok": false, "error": "account not found"}
```

---

### 鏃ュ織

**`GET /api/logs?lines=100`**

璇诲彇 `debug.log` 鏈€杩?N 琛岋紙榛樿 50锛屾渶澶?500锛夈€?

```
Response 200:
{
  "lines": [
    "[08:00:01] 娴佹按绾垮惎鍔?,
    "[08:00:05] MAA v6.11.1 杩愯涓?,
    "..."
  ]
}
```

---

### 闃熷垪鎺у埗

**`GET /api/queue`**

鏌ョ湅褰撳墠鍚姩闃熷垪鐘舵€併€?

```
Response 200:
{
  "pending": [
    {
      "account_id": "abc123",
      "account_name": "灏忓彿",
      "source": "鐞嗘櫤",
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

灏嗚处鍙峰姞鍏ュ惎鍔ㄩ槦鍒椼€?

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

| 瀛楁 | 绫诲瀷 | 璇存槑 |
|------|------|------|
| `account_index` | int | 璐﹀彿鍒楄〃涓簭鍙?|
| `source` | string | 鏉ユ簮锛歮anual/schedule/sanity |
| `priority` | int | 浼樺厛绾э紝0=鏈€楂?|
| `not_before` | string | 涓嶆棭浜庤繖涓椂闂村惎鍔紙鍙€夛級 |

---

**`POST /api/queue/dequeue`**

浠庨槦鍒椾腑绉婚櫎璐﹀彿銆?

```
Request:
{"account_index": 1}
鎴?
{"account_id": "def456"}

Response 200:
{"ok": true, "account_id": "def456", "pending_count": 0}
```

---

### 缁熻

**`GET /api/account/{index}/stats`**

杩斿洖鍗曚釜璐﹀彿鐨勫畬鏁磋繍琛岀粺璁★紙`stats.json` 鍐呭 + 褰撳墠鐘舵€侊級銆?

```
Response 200:
{
  "account_name": "瀹樻湇澶у彿",
  "running": false,
  "stats": {
    "runs": [
      {
        "ts": "2026-06-06 09:45:29",
        "tasks": {"寮€濮嬪敜閱?:"瀹屾垚", "鍒峰叧浣滄垬":"瀹屾垚", "鍏紑鎷涘嫙":"瀹屾垚"},
        "drops": {"鍥烘簮宀?:21, "璧ら噾":12},
        "sanity": {"current":5, "max":210, "deficit":205}
      }
    ]
  }
}
```

| 瀛楁 | 绫诲瀷 | 璇存槑 |
|------|------|------|
| `account_name` | string | 璐﹀彿鍚?|
| `running` | bool | 鏄惁姝ｅ湪杩愯 |
| `stats.runs[]` | array | 鍘嗗彶杩愯璁板綍 |
| `stats.runs[].ts` | string | 瀹屾垚鏃堕棿 |
| `stats.runs[].tasks` | object | 浠诲姟鍚嶁啋鐘舵€?|
| `stats.runs[].drops` | object | 鏉愭枡鍚嶁啋鏁伴噺 |
| `stats.runs[].sanity` | object | 鍓╀綑鐞嗘櫤 |

---

**`GET /api/stats`**

杩斿洖鍏ㄩ儴璐﹀彿缁熻姹囨€汇€?

```
Response 200:
{
  "accounts": [
    {
      "index": 0,
      "account_name": "瀹樻湇澶у彿",
      "running": false,
      "total_runs": 24,
      "stats": { "runs": [...] }
    }
  ]
}
```

---

### 閰嶇疆鍚屾

**`POST /api/config/sync`**

涓嬪彂 MAA `gui.json` 閰嶇疆鍒版寚瀹氳处鍙凤紝瑕嗙洊鐩爣璐﹀彿鐨?`gui.json` 鍜?`gui.new.json`銆?

```
Request:
{
  "account_name": "瀹樻湇澶у彿",
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

Response 404 (璐﹀彿涓嶅瓨鍦?:
{"ok": false, "error": "account not found"}
```

## 閿欒鍝嶅簲鏍煎紡

鎵€鏈夐敊璇搷搴旈伒寰粺涓€鏍煎紡锛?

```json
{
  "ok": false,
  "error": "浜虹被鍙鐨勯敊璇弿杩?
}
```

HTTP 鐘舵€佺爜閬靛惊 REST 璇箟锛?

| 鐘舵€佺爜 | 鍦烘櫙 |
|--------|------|
| 200 | 姝ｅ父鍝嶅簲 |
| 404 | 璧勬簮涓嶅瓨鍦?|
| 409 | 鍐茬獊锛堝閲嶅鍚姩锛?|
| 429 | 棰戠巼闄愬埗 |
| 500 | 鍐呴儴閿欒 |

## 闆嗘垚绀轰緥

### Node.js 杞鐩戞帶

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

### Python 瀹氭椂鍚姩

```python
import requests, time

ORCH = "http://127.0.0.1:19999"
HEADERS = {"x-agent-token": "my-token"}

while True:
    status = requests.get(f"{ORCH}/api/status", headers=HEADERS).json()
    if not status["pipeline_running"]:
        requests.post(f"{ORCH}/api/pipeline/start", headers=HEADERS)
        print("娴佹按绾垮凡鍚姩")
    time.sleep(300)  # 姣?5 鍒嗛挓妫€鏌ヤ竴娆?
```

### curl 鍛戒护琛?

```bash
# 鏌ョ湅鐘舵€?
curl http://127.0.0.1:19999/api/status -H "x-agent-token: my-token"

# 鍚姩娴佹按绾?
curl -X POST http://127.0.0.1:19999/api/pipeline/start -H "x-agent-token: my-token"

# 鏆傚仠娴佹按绾?
curl -X POST http://127.0.0.1:19999/api/pipeline/pause \
  -H "Content-Type: application/json" \
  -H "x-agent-token: my-token" \
  -d '{"action":"pause"}'
```

## 鎶€鏈疄鐜?

`ApiServer`锛坄api_server.py`锛夌户鎵?`QThread`锛屽熀浜?Python 鏍囧噯搴?`http.server.HTTPServer` + `BaseHTTPRequestHandler` 瀹炵幇锛?

- 绾跨▼闅旂锛欻TTP 鏈嶅姟鍣ㄨ繍琛屽湪鐙珛 QThread 涓?
- 淇″彿閫氫俊锛氭棩蹇椾俊鎭€氳繃 `log_msg` Signal 鍙戦€佸埌涓荤嚎绋?
- 瀹炰緥鏇挎崲锛氫慨鏀圭鍙?Token 鏃跺厛鍋滄鏃у疄渚嬪啀鍚姩鏂板疄渚?
- 浼橀泤閫€鍑猴細鍏抽棴绐楀彛鏃惰嚜鍔ㄨ皟鐢?`stop_server()`
