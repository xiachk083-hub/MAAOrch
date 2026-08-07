# 鏃ュ織銆佺粺璁°€佺洃鎺т笌璋冨害

## 鏃ュ織绯荤粺

### 鍚姩鍣ㄦ棩蹇?

MAAOrch 鑷韩鐨勮繍琛屾棩蹇楀啓鍏ユ牴鐩綍鐨?`debug.log`锛?

- 鏈€澶?100KB锛岃秴杩囪嚜鍔ㄨ鍓繚鐣欐渶杩?200 琛?
- 閫氳繃搴曢儴鐘舵€佹爮鐐瑰嚮灞曞紑/鏀惰捣鏃ュ織闈㈡澘瀹炴椂鏌ョ湅
- HTTP API `GET /api/logs?lines=N` 鍙鍙栨渶杩?N 琛?

### MAA 鏃ュ織

姣忎釜 MAA 绋嬪簭鐨勬棩蹇椾綅浜?`{MAA鐩綍}/debug/asst.log`锛岀敱 MAA 鑷韩鍐欏叆锛孧AAOrch 璐熻矗瑙ｆ瀽鍜屽睍绀恒€?

### MAA 鏃ュ織杞浆

姣忚疆 MAA 杩愯瀹屾垚鍚庯紝`AccountRunner._cleanup()` 璋冪敤 `LogService.rotate_log()` 鑷姩杞浆锛?

- 鎵弿 `asst.log` 鎵惧埌鎵€鏈?`Version v` 鏍囪
- 淇濈暀鏈€杩?3 杞殑鏃ュ織
- 鍒犻櫎鏇存棭鐨勫唴瀹?
- 鏂囦欢灏忎簬 50KB 鏃惰烦杩?

杩欓槻姝簡 asst.log 鏃犻檺鑶ㄨ儉锛堝吀鍨嬩娇鐢細3澶?10MB 鈫?杞浆鍚?~1MB锛夈€?

## 鏃ュ織瑙ｆ瀽 (`LogService`)

### parse_log()

`LogService.parse_log()` 閫氳繃浠ヤ笅绛栫暐鎻愬彇鏈€鏂颁竴杞繍琛岀粨鏋滐細

1. **Version 瀹氫綅** 鈥?鍊掑簭鎵弿 asst.log锛屾壘鍒?`Version v` 鏍囪锛屽彧瑙ｆ瀽鏈€鏂颁竴杞?
2. **缁熶竴 JSON 鎻愬彇** 鈥?鍚屾椂鍏煎 `append_callback | SubTaskStart {...}` 鍜岃８ `{"taskchain":"Fight",...}` 涓ょ鏃ュ織鏍煎紡
3. **鍘婚噸** 鈥?鍚屼竴 taskchain 閲嶅鍑虹幇鏃舵洿鏂拌€岄潪鏂板锛堝簲瀵瑰杞噸鍚満鏅級
4. **杩囨护** 鈥?鎺掗櫎 MAA 鍐呴儴浠诲姟閾撅紙Depot銆丱perBox锛?

杩斿洖 `(tasks, sanity, drops)` 涓夊厓缁勶細

```python
tasks = [
    {"name": "鍒峰叧浣滄垬", "start": "08:39:03", "status": "瀹屾垚", "drops": "", "error": ""},
    {"name": "鍏紑鎷涘嫙", "start": "08:40:15", "status": "瀹屾垚", "drops": "", "error": ""},
    ...
]
sanity = {"current": 5, "max": 210, "report_time": "2026-06-06 09:36:33"}
drops = {"鍥烘簮宀?: 21, "璧ら噾": 12}
```

#### 瑙ｆ瀽瑙勫垯 (v6)

| 鏃ュ織鍏抽敭璇?| 鎿嶄綔 |
|------------|------|
| `SubTaskStart` JSON 鍚?`taskchain` | 璁板綍鏂颁换鍔★紙鍘婚噸锛氬悓涓€ chain 鏇存柊鑰岄潪鏂板锛?|
| `TaskChainCompleted` JSON | 鏍囪瀵瑰簲浠诲姟瀹屾垚 |
| `AllTasksCompleted` JSON | 鏍囪鎵€鏈?`杩愯涓璥 浠诲姟涓哄畬鎴?|
| `SubTaskExtraInfo` + `"what":"SanityBeforeStage"` | 鎻愬彇褰撳墠鐞嗘櫤鍊?|
| `SubTaskExtraInfo` + `"what":"StageDrops"` | 鎻愬彇绱Н鎺夎惤缁熻 |
| `SubTaskExtraInfo` + `"what":"ExceededLimit"` | 鏍囪浠诲姟澶辫触 |
| `[ERR]` | 鏍囪褰撳墠浠诲姟澶辫触锛屾彁鍙栭敊璇俊鎭?|

### 浠诲姟鍚嶆槧灏?

| 鏃ュ織鏍囪瘑 | 涓枃鍚?| 鏄惁涓讳换鍔?|
|----------|--------|-----------|
| StartUp | 寮€濮嬪敜閱?| 鉁?|
| Fight | 鍒峰叧浣滄垬 | 鉁?|
| Recruit | 鍏紑鎷涘嫙 | 鉁?|
| Infrast | 鍩哄缓鎹㈢彮 | 鉁?|
| Mall | 淇＄敤鍟嗗簵 | 鉁?|
| Award | 棰嗗彇濂栧姳 | 鉁?|
| Roguelike | 鑲夐附鎺㈢储 | 鉁?|
| Reclamation | 鐢熸伅婕旂畻 | 鉁?|
| CloseDown | 鍏抽棴娓告垙 | 鉁?|
| Depot | 浠撳簱鎵弿 | 鉂?鍐呴儴 |
| OperBox | 骞插憳璇嗗埆 | 鉂?鍐呴儴 |

## 缁熻灞曠ず涓庢寔涔呭寲

### show_stats()

鐐瑰嚮銆岎煋?缁熻銆嶆墦寮€澶氭爣绛惧璇濇锛?

| 鏍囩 | 鏁版嵁婧?| 璇存槑 |
|------|--------|------|
| 鏈€杩?| stats.json | 鏈€杩?15 娆¤繍琛岃褰?|
| 浠婃棩 | stats.json | 褰撴棩姹囨€伙紙娆℃暟銆佷换鍔°€佹帀钀斤級 |
| 鏈懆 | stats.json | 鏈懆姹囨€?|
| 瀹炴椂 | asst.log | 褰撳墠杩愯瀹炴椂瑙ｆ瀽锛堜綔涓鸿ˉ鍏咃級 |

鏄剧ず鐞嗘櫤鎭㈠鍊掕鏃跺拰鎺夎惤姹囨€汇€傝嫢鏃犲巻鍙茶褰曞垯鍥為€€鍒板疄鏃惰В鏋愩€?

### stats.json 鎸佷箙鍖?

`RunStats` 绫伙紙`stats.py`锛夋瘡娆¤繍琛屽畬鎴愬悗鑷姩鍐欏叆 `accounts/{id}/stats.json`锛?

```json
{
  "runs": [{
    "ts": "2026-06-06 09:45:29",
    "tasks": {"寮€濮嬪敜閱?:"瀹屾垚", "鍒峰叧浣滄垬":"瀹屾垚"},
    "drops": {"鍥烘簮宀?:21, "璧ら噾":12},
    "sanity": {"current":5, "max":210, "deficit":205}
  }]
}
```

淇濈暀鏈€杩?200 娆★紝鏀寔璺ㄥ惎鍔ㄦ煡璇㈡湰鏈?鏈懆姹囨€汇€?

## 瀹炴椂鐩戞帶 (`MaintService.poll()` 鈫?`AccountRunner.check_processes()`)

`MaintService.poll()` 鐢变富绐楀彛瀹氭椂鍣ㄦ瘡 2 绉掕皟鐢紝鎵ц锛?

### 1. CLI 杩涚▼妫€娴?

閬嶅巻 `_cli_procs`锛岃鍙?stdout/stderr锛岄€€鍑烘椂璁板綍鏃ュ織鍜岄€氱煡銆?

### 2. GUI 杩涚▼妫€娴?鈫?濮旀墭缁?Runner

璋冪敤 `runner.check_processes()`锛岀敱 `AccountRunner` 缁熶竴绠＄悊杩涚▼鐢熷懡鍛ㄦ湡锛?

- 杩涚▼閫€鍑?鈫?璋冪敤 `_parse_log()` 瑙ｆ瀽 asst.log
- 鎻愬彇浠诲姟鐘舵€併€佺悊鏅恒€佹帀钀?
- 鍙戝皠 `account_finished(aid, exit_code, tasks)` 淇″彿
- 鑷姩淇濆瓨鍒?`stats.json`

### 3. 鐘舵€佹爮鏇存柊

- 鏄剧ず杩愯鏃堕暱锛堝垎閽?绉掞級
- 浠?asst.log 灏鹃儴鎻愬彇褰撳墠 taskchain锛屾樉绀?"MAA: 鍒峰叧..."

## 鍚姩闃熷垪 (`LaunchQueue`)

### 缁熶竴鍏ュ彛

鎵€鏈夊惎鍔ㄨ姹傞兘杩涘叆 `LaunchQueue`锛屾寜浼樺厛绾ф帓搴忥細

| 鏉ユ簮 | priority | 瑙﹀彂鏂瑰紡 |
|------|----------|----------|
| 鎵嬪姩 | 0锛堟渶楂橈級 | 鐢ㄦ埛鐐瑰嚮銆屸柖 鍚姩銆嶆垨 API |
| 瀹氭椂 | 1 | ScheduleThread 瀹氭椂瑙﹀彂 |
| 鐞嗘櫤 | 2 | 涓婁竴杞畬鎴愬悗鑷姩鍏ラ槦锛岃缃?`not_before` 涓烘仮澶嶆椂闂?|

### 璋冨害瑙勫垯锛坱ick 姣?30 绉掞級

```
鍙栭槦棣?鈫?妫€鏌ワ細
  鈶?宸插湪杩愯锛?鈫?璺宠繃
  鈶?妯℃嫙鍣ㄨ鍗狅紵 鈫?璺宠繃
  鈶?杩樻病鍒?not_before锛?鈫?璺宠繃锛堝悗闈㈢殑涔熶笉浼氬埌鏃堕棿锛?
  鈶?鐞嗘櫤涓嶅锛堜粎 sanity 鏉ユ簮锛夛紵 鈫?璺宠繃
  鈹€鈹€ 鍏ㄩ儴婊¤冻 鈹€鈹€
  鈫?鍚姩 鈫?鏍囪妯℃嫙鍣ㄥ崰鐢?
```

**鏍稿績鍘熷垯锛氱粷涓嶄腑鏂鍦ㄨ繍琛岀殑 MAA銆傚彧绛夌┖闂叉椂鍚姩涓嬩竴涓€?*

### 鐞嗘櫤椹卞姩娴佺▼

```
account_finished(澶у彿)
  鈫?璇?stats.json 鑾峰彇鏈€鍚庣悊鏅?(5/210)
  鈫?璁＄畻鎭㈠鏃堕棿: 205 脳 6 = 1230min 鈮?20.5h
  鈫?LaunchQueue.enqueue("澶у彿", "sanity", priority=2, not_before=鏄庡ぉ04:30)

tick 姣?30s:
  鈫?妫€鏌ラ槦鍒?鈫?澶у彿 not_before 鏈埌 鈫?璺宠繃
  鈫?灏忓彿婊¤冻鏉′欢 鈫?鍚姩
  鈫?灏忓彿璺戝畬 鈫?澶у彿杩樻病鍒版椂闂?鈫?璺宠繃
  鈫?...绗簩澶?04:30...
  鈫?澶у彿 not_before 宸茶繃锛屾ā鎷熷櫒绌洪棽 鈫?鍚姩
```

## 閫氱煡绯荤粺

### 鎵樼洏閫氱煡

`MaintService.notify()` 璋冪敤 `QSystemTrayIcon.showMessage()` 寮瑰嚭姘旀场锛?

- 瀹屾垚閫氱煡锛氫俊鎭浘鏍?+ 鐞嗘櫤鎭㈠鍊掕鏃讹紙"MAA 瀹屾垚: 5 涓换鍔?| 鐞嗘櫤 5/210 (20h30m鍥炴弧)"锛?
- 閿欒閫氱煡锛氶敊璇浘鏍囷紝鎸佺画 3 绉?

### Webhook 閫氱煡

鑻ラ厤缃簡 `webhook_url`锛屽悓姝ュ彂閫?HTTP POST锛屾牸寮忎笌浼佷笟寰俊/閽夐拤鍏煎銆?

## 瀹堟姢杩涚▼閰嶇疆

姣忎釜浠撳簱鏉＄洰鏀寔鐙珛瀹堟姢璁剧疆锛?

| 瀛楁 | 绫诲瀷 | 榛樿鍊?| 璇存槑 |
|------|------|--------|------|
| `guard_enabled` | bool | MAA 鑷姩涓嬭浇=true | 鏄惁鍚敤瀹堟姢 |
| `guard_max_restart` | int | 3 | 鏈€澶ч噸鍚鏁?|
| `guard_capture_log` | bool | false | 宕╂簝鏃舵槸鍚︽崟鑾锋棩蹇?|
