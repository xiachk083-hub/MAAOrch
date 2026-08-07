# 娴佹按绾胯皟搴?

## 鍒嗙粍涓庝粨搴?

MAAOrch 鏀寔灏嗗彲鎵ц绋嬪簭缁勭粐涓哄垎缁勶紝鎸夌粍鎵归噺鍚姩銆?

### 鏁版嵁缁撴瀯

**鍒嗙粍** (`groups[]`)锛?

```json
{
  "name": "涓诲姏璐﹀彿",
  "mode": "serial",
  "post_delay": 3,
  "programs": [
    {"ref": "warehouse_id_1", "pre_delay": 0},
    {"ref": "warehouse_id_2", "pre_delay": 5}
  ]
}
```

**浠撳簱鏉＄洰** (`warehouse[]`)锛?

```json
{
  "id": "warehouse_id_1",
  "path": "C:\\path\\to\\MAA.exe",
  "args": [],
  "cwd": "",
  "env": {},
  "maa_type": "maa",
  "maa_version": "v6.11.1",
  "account_ref": "account_id",
  "launch_mode": "gui",
  "task_pipeline": "startup,fight,recruit,infrast,mall,award",
  "guard_enabled": true,
  "guard_max_restart": 3,
  "guard_capture_log": false
}
```

### 鍒嗙粍妯″紡

| 妯″紡 | 琛屼负 |
|------|------|
| `serial` | 閫愪釜鍚姩锛屾瘡椤逛箣闂存湁 `pre_delay` 绉掗棿闅?|
| `parallel` | 鍚屾椂鍚姩鎵€鏈夌▼搴?|

### 绋嬪簭绫诲瀷

`maa_type` 瀛楁鏍囪瘑绋嬪簭绫诲瀷锛?

| 鍊?| 璇存槑 |
|----|------|
| `maa` | MAA 鍥惧舰鐣岄潰绋嬪簭 |
| `maa-cli` | maa-cli 鍛戒护琛屽伐鍏?|
| `general` | 閫氱敤鍙墽琛岀▼搴?|

## LaunchQueue 缁熶竴鍚姩闃熷垪

`LaunchQueue`锛坄launch_queue.py`锛夋槸绯荤粺鐨勬牳蹇冭皟搴﹀叆鍙ｏ紝鎵€鏈夊惎鍔ㄨ姹傞兘鍏堣繘鍏ラ槦鍒椼€?

### 闃熷垪鏉＄洰

```python
@dataclass
class QueueEntry:
    sort_key: tuple     # (priority, not_before)
    account_id: str
    source: str         # "manual" | "schedule" | "sanity"
    not_before: datetime
```

### 浼樺厛绾?

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

### API

```python
queue.enqueue(account_id, source, priority, not_before)
queue.enqueue_batch(source, priority, accounts)
queue.dequeue(account_id)
queue.pending_count          # 鎺掗槦鏁?
queue.active_count           # 杩愯涓暟
queue.is_queued(account_id)
queue.is_running(account_id)
queue.pending_summary()      # 鐘舵€佹爮鏂囨湰
queue.get_next_for(account_id)  # 涓嬫鍚姩鏃堕棿
```

## PipelineThread 璋冨害绾跨▼

`PipelineThread`锛坄pipeline_thread.py`锛夌户鎵?`QThread`锛岀敤浜庡垎缁勬壒閲忓惎鍔紙闈為槦鍒楁ā寮忥級锛?

```
for 姣忎釜鍒嗙粍:
    if stop_flag: break
    while pause_flag: sleep(200ms)
    鍙戝皠 progress 淇″彿
    if mode == "parallel":
        for 姣忎釜绋嬪簭: _launch(绋嬪簭)
    else (serial):
        for 姣忎釜绋嬪簭:
            sleep(pre_delay)
            _launch(绋嬪簭)
    sleep(post_delay)
鍙戝皠 finished 淇″彿
```

### 绋嬪簭鍚姩 `_launch()`

1. 鏌ユ壘浠撳簱鏉＄洰鑾峰彇璺緞銆佸弬鏁般€佸伐浣滅洰褰?
2. 鑻ョ粦瀹氳处鍙凤紙`account_ref` 闈炵┖锛夛紝璋冪敤 `ConfigService.inject_for_thread()` 娉ㄥ叆閰嶇疆
3. `subprocess.Popen()` 鍚姩杩涚▼
4. 灏嗚繘绋嬪璞″姞鍏?`_running` 鍒楄〃
5. 鍙戝皠 `program_started` 淇″彿

### 鏆傚仠/鎭㈠

- `pause()`: 璁剧疆 `pause_flag=True`锛岀嚎绋嬪湪涓诲惊鐜腑妫€娴嬪苟杩涘叆 200ms 浼戠湢绛夊緟
- `resume()`: 娓呴櫎 `pause_flag`锛岀嚎绋嬬户缁墽琛?
- 鏆傚仠鏈熼棿宸茬粡鍚姩鐨勫瓙杩涚▼缁х画杩愯锛屼笉浼氱粓姝?
- 鍙€氳繃 HTTP API `POST /api/pipeline/pause` 澶栭儴鎺у埗

### 鍋滄

- `stop()`: 璁剧疆 `stop_flag=True`锛屽鎵€鏈?`_running` 涓殑杩涚▼璋冪敤 `terminate()`

### 杩涚▼瀛樻椿妫€娴?

鍦?`_sleep()` 绛夊緟寰幆涓紝姣?100ms 妫€鏌ヤ竴娆?`_running` 鍒楄〃涓繘绋嬫槸鍚﹂€€鍑猴紙`poll() is None`锛夛紝宸查€€鍑虹殑杩涚▼鑷姩绉婚櫎銆?

## AccountRunner 鍗曞彿鍚姩闂幆

`AccountRunner`锛坄runner.py`锛夌鐞嗗崟涓处鍙风殑瀹屾暣鐢熷懡鍛ㄦ湡锛?

```
launch(row)
  鈫?妫€鏌ュ墠鎻愶紙鏈?MAA 绋嬪簭锛熺粦瀹氭ā鎷熷櫒锛熸ā鎷熷櫒绌洪棽锛燂級
  鈫?鍚姩/杩炴帴妯℃嫙鍣?
  鈫?ConfigService.inject() 娉ㄥ叆閰嶇疆
  鈫?subprocess.Popen() 鍚姩 MAA
  鈫?璁板綍鍒?_procs[aid]
  鈫?鍙戝皠 account_started(aid)

check_processes() (姣?2s 鐢?proc_timer 璋冪敤)
  鈫?杩涚▼閫€鍑猴紵
     鈫?parse_log() 瑙ｆ瀽浠诲姟鐘舵€併€佺悊鏅恒€佹帀钀?
     鈫?RunStats.save_run() 鎸佷箙鍖?
     鈫?鍙戝皠 account_finished(aid, exit_code, tasks)
     鈫?LaunchQueue.on_account_finished() 閲婃斁妯℃嫙鍣?
```

## 瀹氭椂浠诲姟

`ScheduleThread`锛坄schedule_thread.py`锛夋敮鎸佷袱绉嶆ā寮忥細

### 姣忔棩瀹氭椂

- `type: "daily"`锛宍time: "08:00"`
- 姣忓ぉ鍦ㄦ寚瀹氭椂闂磋Е鍙戜竴娆?
- 鑻ュ綋鍓嶆椂闂村凡杩囩洰鏍囷紝椤哄欢鍒版鏃?

### 姣忓懆瀹氭椂

- `type: "weekly"`锛宍time: "08:00"`锛宍days_of_week: [0,3,6]`锛堝懆涓€=0锛?
- 浠呭湪鎸囧畾鏄熸湡鍑犺Е鍙?
- 鎼滅储鏈潵 7 澶╁唴绗竴涓尮閰嶇殑瑙﹀彂鏃堕棿

### 闃查噸澶?

鑻ヤ笂娆¤Е鍙戝湪 120 绉掑唴锛岃烦杩囨湰娆¤Е鍙戯紙闃叉 NTP 鏍℃椂绛夊鑷撮噸澶嶏級銆?

## 鍚姩閫夐」

姣忎釜璐﹀彿鏀寔浠ヤ笅鍚姩琛屼负鎺у埗锛?

| 閫夐」 | 璇存槑 |
|------|------|
| `start_minimized` | MAA 鍚姩鍚庢渶灏忓寲鍒版墭鐩?|
| `start_directly` | 璺宠繃鍞ら啋闃舵锛岀洿鎺ヨ繘鍏ヤ换鍔￠槦鍒?|
| `adb_fail_launch_emu` | ADB 杩炴帴澶辫触鏃惰嚜鍔ㄥ惎鍔ㄦā鎷熷櫒 |
| `adb_retry` | ADB 杩炴帴澶辫触閲嶈瘯娆℃暟 |
| `sync_tasks` | 鍚姩鏃跺皢浠诲姟鍙傛暟鍚屾鍐欏叆 gui.json |
| `round_robin_deficit` | 璺濇弧宸?N 鐐硅嚜鍔ㄥ惎鍔紙0=鍥炴弧锛?|

## 寰幆璋冨害

`鈿?璋冨害`鏍囩椤电鐞嗗叏灞€寰幆璋冨害閰嶇疆锛?

### 鍏ㄥ眬璁剧疆

| 瀛楁 | 璇存槑 |
|------|------|
| `daily_batch_time` | 姣忔棩鎵归噺鍏ラ槦鏃堕棿锛堝 "04:00"锛岀┖=鍏抽棴锛?|
| `parallel_max` | 鏈€澶у苟琛?MAA 杩涚▼鏁?|
| `round_robin_deficit` | 璺濇弧宸?N 鐐瑰嵆鑷姩鍏ラ槦锛?=鍥炴弧锛?0=宸?30 鐐瑰氨鍚姩锛?|

### 璋冨害閫昏緫

```
1. 姣忔棩鎵归噺: daily_batch_time 鍒扮偣 鈫?鍏ㄩ儴璐﹀彿鍏ラ槦 (priority=1)
2. 璺戝畬鑷姩: 绠楁仮澶嶆椂闂?鈫?璺濇弧 鈮?deficit 鏃惰嚜鍔ㄥ叆闃?(priority=2)
3. 骞惰鎺у埗: 鍚屾椂杩愯 鈮?parallel_max锛岃秴杩囦笂闄愭帓闃熺瓑寰?
```

### 涓庡畾鏃朵换鍔＄殑鍏崇郴

- 瀹氭椂浠诲姟锛圫cheduleThread锛夛細鍥哄畾鏃堕棿瑙﹀彂锛堟瘡澶?姣忓懆锛?
- 寰幆璋冨害锛氭瘡鏃ユ壒閲?+ 璺戝畬鑷姩绠楁仮澶嶆椂闂?
- 鎵嬪姩鍏ラ槦锛氫紭鍏堢骇鏈€楂?0)锛岄殢鏃跺彲鎻掗槦

## 鍚姩鍚庢搷浣?

`post_action` 瀛楁鎺у埗 MAA 瀹屾垚鍚庤嚜鍔ㄦ墽琛岀殑鎿嶄綔锛屼綅鎺╃爜缁勫悎锛?

| 鎿嶄綔 | 鍐呴儴鏍囩 |
|------|----------|
| 杩斿洖涓诲睆 | `ReturnToMain` |
| 閫€鍑烘柟鑸?| `ExitArknights` |
| 鍏抽棴妯℃嫙鍣?| `CloseEmulator` |
| 閫€鍑?MAA | `ExitMAA` |
