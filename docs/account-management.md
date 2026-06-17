# 澶氳处鍙蜂笌妯℃嫙鍣ㄧ鐞?

## 璐﹀彿鏁版嵁缁撴瀯

### Account 绫?(`account.py`)

`Account` 鏄竴涓?dataclass锛屾彁渚涚被鍨嬪寲鐨勮处鍙锋ā鍨嬶紝鍚屾椂鍏煎鏃х殑 dict 璁块棶鏂瑰紡锛堟敮鎸?`__getitem__`銆乣__setitem__`銆乣get()`銆乣setdefault()`銆乣update()`锛夈€?

```python
@dataclass
class Account:
    id: str = ""
    name: str = "鏈懡鍚?
    game_client: str = "Official"
    adb_path: str = ""
    adb_address: str = ""
    connection_preset: str = ""
    touch_mode: str = "ADB"
    account_switch: str = ""
    emu_path: str = ""
    emu_instance_index: str = ""
    emu_instance_name: str = ""
    emu_launch: bool = False
    emu_wait: int = 30
    emu_add_cmd: str = ""
    adb_fail_launch_emu: bool = False
    adb_retry: int = 0
    start_minimized: bool = False
    start_directly: bool = False
    sync_tasks: bool = False
    post_action: str = ""
    fight_stage: str = ""
    task_pipeline: str = ""
    task_settings: dict = field(default_factory=dict)
    task_templates: dict = field(default_factory=dict)
    pipe_templates: dict = field(default_factory=dict)
    stats: dict = field(default_factory=dict)
    loop_enabled: bool = False
    loop_interval: int = 5
    loop_max_rounds: int = 10
    sanity_driven: bool = False
    min_sanity: int = 0
```

### config.json 涓殑瀛樺偍

姣忎釜璐﹀彿鍦?`config.json` 涓瓨鍌ㄤ负 `accounts[]` 鏁扮粍鐨勪竴涓厓绱狅細

```json
{
  "id": "a1b2c3d4",
  "name": "瀹樻湇澶у彿",
  "game_client": "Official",
  "adb_path": "C:\\platform-tools\\adb.exe",
  "adb_address": "127.0.0.1:16384",
  "connection_preset": "MuMuPro",
  "touch_mode": "ADB",
  "account_switch": "",
  "emu_path": "C:\\MuMu Player 12\\shell\\MuMuPlayer.exe",
  "emu_launch": true,
  "emu_wait": 30,
  "emu_add_cmd": "",
  "emu_instance_index": "0",
  "emu_instance_name": "MuMu 妯℃嫙鍣?,
  "post_action": "",
  "start_minimized": false,
  "start_directly": false,
  "adb_fail_launch_emu": false,
  "adb_retry": 0,
  "task_settings": {},
  "sync_tasks": false,
  "stats": {},
  "sanity_driven": false,
  "min_sanity": 0
}
```

### 瀛楁璇存槑

| 瀛楁 | 绫诲瀷 | 璇存槑 |
|------|------|------|
| `id` | string | 鍞竴鏍囪瘑锛?浣嶉殢鏈篒D |
| `name` | string | 璐﹀彿鏄剧ず鍚?|
| `game_client` | string | 鍖烘湇鏍囪瘑 |
| `adb_path` | string | ADB 鍙墽琛屾枃浠惰矾寰勶紝绌?绯荤粺榛樿 |
| `adb_address` | string | ADB 杩炴帴鍦板潃锛屽 `127.0.0.1:16384` |
| `connection_preset` | string | 杩炴帴棰勮 |
| `touch_mode` | string | 瑙︽帶妯″紡 |
| `account_switch` | string | 璐﹀彿鍒囨崲鏍囪瘑锛堟墜鏈哄彿/閭锛夛紝绌?涓嶅垏鎹?|
| `emu_*` | various | 妯℃嫙鍣ㄧ浉鍏抽厤缃?|
| `task_pipeline` | string | 閫楀彿鍒嗛殧鐨勪换鍔￠摼 |
| `task_settings` | object | 鍚勪换鍔＄殑璇︾粏鍙傛暟 |
| `sanity_driven` | bool | 鐞嗘櫤鍥炴弧鑷姩鍐嶅惎鍔?|
| `min_sanity` | int | 鐞嗘櫤鏈€浣庨槇鍊?|

## 鍖烘湇鏀寔

`game_client` 瀛楁鏀寔浠ヤ笅鍊硷紝瀵瑰簲鏄庢棩鏂硅垷鍚勫鎴风锛?

| 鍊?| 瀹㈡埛绔?|
|----|--------|
| `Official` | 瀹樻湇 |
| `Bilibili` | B 鏈?|
| `YoStarEN` | 鍥介檯鏈?|
| `YoStarJP` | 鏃ユ湇 |
| `YoStarKR` | 闊╂湇 |
| `Txwy` | 绻佷腑鏈?|

## 杩炴帴閰嶇疆

### 杩炴帴棰勮

`connection_preset` 鍐冲畾 MAA 鐨勮繛鎺ユā寮忥紝鍙€夐」锛?

| 棰勮 | 璇存槑 |
|------|------|
| `General` | 閫氱敤妯″紡 |
| `MuMuPro` | MuMu 妯℃嫙鍣?12 |
| `BlueStack` | 钃濆彔妯℃嫙鍣?|
| `Nox` | 澶滅妯℃嫙鍣?|
| `Xiaoyao` | 閫嶉仴妯℃嫙鍣?|
| `Ledi` | 闆风數妯℃嫙鍣?|

### 瑙︽帶妯″紡

`touch_mode` 鎺у埗 MAA 鐨勮Е鎺ф柟寮忥細

| 鍊?| 瀵瑰簲 MAA 閰嶇疆 |
|----|---------------|
| `ADB` | `adb`锛堥粯璁わ級 |
| `MiniTouch` | `minitouch` |
| `MaaTouch` | `maatouch` |

## ADB 宸ュ叿

### 鎵弿璁惧

`EmuService.scan()` 鎵ц娴佺▼锛?

1. 璋冪敤 `adb devices` 鑾峰彇宸茶繛鎺ヨ澶囧垪琛?
2. 瑙ｆ瀽杈撳嚭锛岃繃婊?`device` / `unauthorized` / `offline` 鐘舵€?
3. 鑻ユ棤鍦ㄧ嚎璁惧锛岄亶鍘嗘墍鏈夋ā鎷熷櫒棰勮绔彛锛屾墽琛?`adb connect` 鎺㈡祴鍚庨噸鏂版壂鎻?

### 娴嬭瘯杩炴帴

`EmuService.test_adb()` 瀵规寚瀹氬湴鍧€鎵ц `adb connect`锛屾牴鎹緭鍑哄垽鏂繛鎺ョ姸鎬併€?

### 鎴浘

`EmuService.screenshot()` 閫氳繃 `adb exec-out screencap -p` 鑾峰彇璁惧灞忓箷鎴浘锛屼繚瀛樺埌 `screenshots/` 鐩綍锛屾枃浠跺悕鏍煎紡 `MAA_YYYYMMDD_HHMMSS.png`銆?

## 妯℃嫙鍣ㄥ瀹炰緥

### 鏀寔鐨勬ā鎷熷櫒

閫氳繃 `task_constants.py` 涓殑 `EMU_PRESETS` 瀹氫箟锛?

| 妯℃嫙鍣?| 鍏抽敭绔彛 |
|--------|----------|
| MuMu 12 | 16384 + index 脳 32 |
| MuMu 6 | 7555 + index 脳 32 |
| 闆风數 | 5555, 5556, 5557... |
| 澶滅 | 62001, 62025... |
| 閫嶉仴 | 21503, 21513... |
| 钃濆彔 | 5555 |

### mumu-cli 闆嗘垚

MuMu 妯℃嫙鍣ㄩ€氳繃 `mumu-cli` 鍛戒护琛屽伐鍏风鐞嗭紝鎼滅储璺緞浼樺厛绾э細

1. 鐜鍙橀噺 `MUMU_CLI_HOME`
2. `C:\Program Files\MuMu Player 12\shell\`
3. `C:\Program Files\Nemu\vmonitor\bin\`
4. `C:\Program Files\Muvm6\emulator\nemu\EmulatorShell\`

### 瀹炰緥妫€娴?

`detect_emu_instances()` 鍑芥暟锛坄task_constants.py`锛夋墽琛岋細

1. 璋冪敤 `mumu-cli info --vmindex all` 鑾峰彇瀹炰緥鍒楄〃
2. 瑙ｆ瀽姣忎釜瀹炰緥鐨?`name`銆乣adb_port`銆乣running` 鐘舵€?
3. 鑻?mumu-cli 涓嶅彲鐢紝鍥為€€鍒拌鍙?`MUMU_INSTANCE_DIRS` 涓悇瀹炰緥鐩綍鐨?`config.json`

### 瀹炰緥鐘舵€佺洃鎺?

`EmuMonitor`锛坄task_constants.py`锛夋槸涓€涓寔缁繍琛岀殑鍚庡彴绾跨▼锛屾瘡 30 绉掗€氳繃 `mumu-cli info --vmindex all` 杞鎵€鏈?MuMu 瀹炰緥鐨勮繍琛岀姸鎬侊紝鏇存柊 `emu_status` 瀛楀吀渚?UI 鏄剧ず銆?

### 鎵鍙ｆ祦绋?

`EmuService.scan_port()` 涓夋鎿嶄綔锛?

1. **鍚姩**锛歚mumu-cli control --vmindex {index} launch`
2. **绛夊緟**锛氫紤鐪?5 绉掔瓑寰呭紑鏈?
3. **鑾峰彇绔彛**锛氬厛璇诲疄渚嬬洰褰?`config.json` 鐨?`adb_port` 瀛楁 鈫?澶辫触鍒欒皟 `detect_emu_instances()` 鈫?鏈€缁堝洖閫€鍏紡 `16384 + index 脳 32`
4. **杩炴帴**锛歚adb connect 127.0.0.1:{port}`

### 鍏抽棴妯℃嫙鍣?

`EmuService.stop_emu()` 璋冪敤 `mumu-cli control --vmindex {index} shutdown`銆?

## 閰嶇疆瀵煎叆/瀵煎嚭

鏀寔閫氳繃鑿滃崟瀵煎嚭褰撳墠鍏ㄥ眬閰嶇疆鎴栧崟涓处鍙烽厤缃负 JSON 鏂囦欢锛屼互鍙婁粠鏂囦欢瀵煎叆鍚堝苟閰嶇疆銆傚鍑烘椂浼氳嚜鍔ㄥ幓闄ゆ晱鎰熻矾寰勪俊鎭€?
