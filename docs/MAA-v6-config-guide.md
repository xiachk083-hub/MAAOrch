# MAA v6 閰嶇疆娉ㄥ叆璇存槑

## 鍓跨伃鍏冲崱浠ｇ爜

MAA v6 浣跨敤涓嶅悓浜?MAA v5 鐨勫壙鐏叧鍗′唬鐮併€?
MAA v5 浣跨敤 `Annihilation_1/2/3` 鏍煎紡锛?*MAA v6 涓嶅啀鏀寔杩欎簺鏃х紪鐮?*銆?

### MAA v6 鏀寔鐨勫€?

| 鏄剧ず鍚?| MAA v5 浠ｇ爜 | MAA v6 `AnnihilationStage` 鍊?|
|--------|------------|------------------------------|
| 鑷姩閫夋嫨 | 鈥?| `""` |
| 褰撴湡鍓跨伃 | `Annihilation` | `"Annihilation"` |
| 鍒囧皵璇轰集鏍?| `Annihilation_1` | `"Chernobog@Annihilation"` |
| 榫欓棬澶栫幆 | `Annihilation_2` | `"LungmenOutskirts@Annihilation"` |
| 榫欓棬甯傚尯 | `Annihilation_3` | `"LungmenDowntown@Annihilation"` |

### StagePlan 瀛楁

`StagePlan` 鍦?MAA v6 涓缁堜负 `["Annihilation"]`锛屼笉闅忓叿浣撳叧鍗″彉鍖栥€?
鍏蜂綋鍏冲崱鐢?`AnnihilationStage` 瀛楁鎺у埗銆?

### 瀛楁鏄犲皠鏉ユ簮

- `maa/v6.11.1/resource/tasks/tasks.json` lines 1130-1140
- 瀹炰緥閰嶇疆 `maa/instances/{N}/config/gui.new.json` 涓?`AnnihilationStage` 瀛楁

## TaskQueue 鏉＄洰鏍煎紡

MAA v6 鐨?TaskQueue 鏉＄洰蹇呴』鍖呭惈 `$type` 瀛楁锛?

| TaskType | `$type` 鍊?|
|----------|-----------|
| StartUp | `StartUpTask` |
| Fight | `FightTask` |
| Infrast | `InfrastTask` |
| Recruit | `RecruitTask` |
| Mall | `MallTask` |
| Award | `AwardTask` |
| Roguelike | `RoguelikeTask` |
| Reclamation | `ReclamationTask` |

## config_injector.py inject_smart 璇存槑

`inject_smart()` 璐熻矗灏嗘櫤鑳借皟搴︾殑浠诲姟鍒楄〃鍐欏叆 MAA 瀹炰緥鐨勯厤缃枃浠?`gui.new.json`銆?

### 鍏抽敭閫昏緫

1. `task_set` = 鐢ㄦ埛鐨勪换鍔＄被鍨嬮泦鍚堬紙灏忓啓锛?
2. 璇诲彇鐜版湁 TaskQueue锛岀Щ闄?`_smart_inserted` 鏍囪鐨勬潯鐩?
3. 瀵规瘡涓潯鐩 `IsEnable = TaskType.lower() in task_set`
4. Fight 鏉＄洰涓?`UseCustomAnnihilation` 鎺у埗鏄惁璺戝壙鐏?
5. 褰撳悓鏃舵湁鍒峰叧鍜屽壙鐏椂锛屾彃鍏ヤ竴涓壙鐏厠闅嗘潯鐩斁鍦ㄥ埛鍏冲墠
6. 褰撳彧鏈夊壙鐏棤鍒峰叧鏃讹紝淇敼鐜版湁 Fight 鏉＄洰涓哄壙鐏ā寮?

### 鍘婚噸閫昏緫

`clean_tq` 鏋勫缓鍚庯紝濡傛灉 Fight 鏉＄洰澶氫簬 1 涓紝淇濈暀鏈€鍚庝竴涓紝鍒犻櫎澶氫綑閲嶅銆?
