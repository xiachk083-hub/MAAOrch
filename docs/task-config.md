# MAA 浠诲姟閰嶇疆涓庢敞鍏?

## ConfigService 姒傝堪

`ConfigService`锛坄config_injector.py`锛夎礋璐ｅ皢 MAAOrch 涓厤缃殑璐﹀彿杩炴帴淇℃伅鍜屼换鍔″弬鏁板啓鍏?MAA 鐨勯厤缃枃浠朵腑銆傛敮鎸佷袱绉嶅啓鍏ユā寮忥細

| 妯″紡 | 鐩爣鏂囦欢 | 鐢ㄩ€?|
|------|----------|------|
| `inject()` | `gui.json` + `gui.new.json` | MAA GUI 妯″紡閰嶇疆 |
| `gtc()` | `daily.toml` + `default.toml` | maa-cli 鍛戒护琛屾ā寮忛厤缃?|

## gui.json 娉ㄥ叆 (`inject()`)

鍚屾椂鍐欏叆 `{MAA鐩綍}/config/gui.json` 鍜?`gui.new.json`锛岀‘淇濆吋瀹瑰悇鐗堟湰 MAA銆?

### 杩炴帴閰嶇疆

浠庤处鍙锋暟鎹槧灏勫埌 MAA 閰嶇疆椤癸細

| 璐﹀彿瀛楁 | MAA 閰嶇疆椤?| 璇存槑 |
|----------|------------|------|
| `adb_address` | `Connect.Address` | ADB 鍦板潃 |
| `adb_path` | `Connect.AdbPath` | ADB 鍙墽琛屾枃浠惰矾寰?|
| `connection_preset` | `Connect.ConnectConfig` | MuMuPro 鈫?`MuMuEmulator12` |
| `touch_mode` | `Connect.TouchMode` | MiniTouch 鈫?`minitouch`, MaaTouch 鈫?`maatouch`, ADB 鈫?`adb` |
| `game_client` | `Start.ClientType` | 瀹㈡埛绔尯鏈?|
| - | `Connect.AdbReplaced` | 鍥哄畾 `"True"` |
| - | `Connect.AutoDetect` | 鍥哄畾 `"False"` |
| - | `Connect.AlwaysAutoDetect` | 鍥哄畾 `"False"` |

### 鍚姩閰嶇疆

| 璐﹀彿瀛楁 | MAA 閰嶇疆椤?|
|----------|------------|
| `start_minimized` | `Global.GUI.MinimizeToTray` |
| `start_directly` | `Start.RunDirectly` |
| `post_action` | `MainFunction.PostActions` |
| `adb_retry` > 0 | `Connect.RetryOnDisconnected` |
| `account_switch` | `Start.StartGame` + 浠诲姟闃熷垪涓殑 `AccountName` |

### 妯℃嫙鍣ㄨ嚜鍔ㄥ惎鍔?

鑻?`emu_instance_index` 闈炵┖涓?`emu_launch` 涓?false锛堢敱 MAA 绠＄悊鍚姩锛夛紝鍒欐敞鍏ワ細

```
Start.EmulatorPath = mumu-cli 璺緞
Start.EmulatorAddCommand = control --vmindex {index} launch
Start.OpenEmulatorAfterLaunch = True
Start.EmulatorWaitSeconds = {emu_wait}
```

### 浠诲姟闃熷垪鍚屾

褰?`sync_tasks` 涓?`True` 鏃讹紝閬嶅巻 `gui.json` 涓殑 `TaskQueue` 鏁扮粍锛?

1. 浠诲姟鍦?`task_pipeline` 鍒楄〃涓?鈫?`IsEnable = True`锛屽～鍏ヨ缁嗗弬鏁?
2. 浠诲姟涓嶅湪鍒楄〃涓?鈫?`IsEnable = False`锛堢鐢級

#### 鍚勪换鍔″弬鏁版槧灏?

**鍒峰叧浣滄垬 (Fight)**锛?
| 鍙傛暟 | MAA 閰嶇疆 |
|------|----------|
| `stage` | `StagePlan` |
| `medicine` | `UseMedicine` + `MedicineCount` |

**鍏紑鎷涘嫙 (Recruit)**锛?
| 鍙傛暟 | MAA 閰嶇疆 |
|------|----------|
| `select` | `Level3Choose` / `Level4Choose` / `Level5Choose` |
| `confirm` | `Confirm` |
| `times` | `MaxTimes` |

**鍩哄缓鎹㈢彮 (Infrast)**锛?
| 鍙傛暟 | MAA 閰嶇疆 |
|------|----------|
| `facilities` | `RoomList` |
| `drones` | `UsesOfDrones` |

**淇＄敤鍟嗗簵 (Mall)**锛?
| 鍙傛暟 | MAA 閰嶇疆 |
|------|----------|
| `shopping` | `Shopping` |
| `blacklist` | `BlackList` |

**棰嗗彇濂栧姳 (Award)**锛?
| 鍙傛暟 | MAA 閰嶇疆 |
|------|----------|
| `award` | `Award` |
| `mail` | `Mail` |

**鑲夐附鎺㈢储 (Roguelike)**锛?
| 鍙傛暟 | MAA 閰嶇疆 |
|------|----------|
| `theme` | `Theme` |
| `mode` (0/1) | `Mode` (Exp/Investment) |

**鐢熸伅婕旂畻 (Reclamation)**锛?
| 鍙傛暟 | MAA 閰嶇疆 |
|------|----------|
| `theme` | `Theme` |

## maa-cli TOML 鐢熸垚 (`gtc()`)

涓?CLI 妯″紡鐢熸垚 TOML 閰嶇疆鏂囦欢锛?

### daily.toml锛堜换鍔￠厤缃級

鏍规嵁 `task_pipeline` 鍒楄〃鐢熸垚 `[[tasks]]` 娈碉細

```toml
[[tasks]]
type="Fight"
[tasks.params]
stage="1-7"
```

鏀寔鐨勪换鍔＄被鍨嬶細`StartUp`銆乣Fight`銆乣Recruit`銆乣Infrast`銆乣Mall`銆乣Award`銆乣Roguelike`銆乣Reclamation`銆乣CloseDown`銆?

鐢熸垚璺緞锛歚{MAA鐩綍}/config/tasks/daily.toml`

### default.toml锛堣繛鎺ラ厤缃級

```toml
[connection]
address="127.0.0.1:16384"
adb_path="C:\\platform-tools\\adb.exe"
preset="MuMuPro"

[instance_options]
touch_mode="ADB"
```

鐢熸垚璺緞锛歚{MAA鐩綍}/config/profiles/default.toml`

## 浠诲姟甯搁噺

`task_constants.py` 涓畾涔夛細

```python
TASK_NAMES = {
    "StartUp": "寮€濮嬪敜閱?, "Fight": "鍒峰叧浣滄垬", "Recruit": "鍏紑鎷涘嫙",
    "Infrast": "鍩哄缓鎹㈢彮", "Mall": "淇＄敤鍟嗗簵", "Award": "棰嗗彇濂栧姳",
    "Roguelike": "鑲夐附鎺㈢储", "Reclamation": "鐢熸伅婕旂畻", "CloseDown": "鍏抽棴娓告垙"
}

TASK_DEFAULTS = {
    "Fight": {"stage": "1-7", "medicine": 0},
    "Recruit": {"select": [3,4,5], "confirm": [3,4,5], "times": 4},
    "Infrast": {"facilities": ["Trade","Reception","Mfg","Control","Power","Office","Dorm"], "drones": "Money"},
    "Mall": {"shopping": True, "blacklist": []},
    "Award": {"award": True, "mail": True},
    "Roguelike": {"theme": "Sarkaz", "mode": 0},
    "Reclamation": {"theme": "Tales"}
}
```

## 鍙傛暟妯℃澘

鏀寔灏嗗綋鍓嶄换鍔″弬鏁颁繚瀛樹负鍛藉悕妯℃澘锛屽悗缁彲鍔犺浇濂楃敤銆傛ā鏉垮姛鑳介€氳繃 `TaskSettingsDialog`锛坄dialogs.py`锛夊疄鐜帮紝鏁版嵁瀛樺偍鍦ㄨ处鍙风殑 `task_settings` 瀛楁涓€?
