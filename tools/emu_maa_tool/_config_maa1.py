"""配好 MAA_1 的 config（连接 + 任务），作为所有脚本的 ConfigFile 基线模板"""
import base64
import json
import urllib.request

CRED = json.load(open(r"C:\Users\xiach\.maorch_deploy.json", encoding="utf-8"))
MANAGER = CRED.get("manager", "http://100.79.173.69:19998")
TOKEN = CRED.get("manager_token", "")


def ex(cmd, t=60):
    req = urllib.request.Request(
        MANAGER + "/api/exec",
        data=json.dumps({"command": cmd}).encode(),
        headers={"x-manager-token": TOKEN, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=t).read())
        return (r.get("stdout", "") + r.get("stderr", "")).strip() or "(空)"
    except Exception as e:
        try:
            return "HTTPERR " + e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            return "ERR " + str(e)[:150]


if __name__ == "__main__":
    # 脚本内容：用 chr(92) 代替反斜杠，避免转义问题
    script = (
        "import json, pathlib\n"
        "BS = chr(92)\n"
        "p = pathlib.Path('E:' + BS + 'MAA-Multi' + BS + 'MAA_1' + BS + 'config' + BS + 'gui.new.json')\n"
        "d = json.loads(p.read_text(encoding='utf-8'))\n"
        "gui = d.setdefault('Configurations', {}).setdefault('Default', {}).setdefault('Gui', {})\n"
        "cs = gui.setdefault('ConnectSettings', {})\n"
        "cs['Address'] = '127.0.0.1:16416'\n"
        "cs['AdbPath'] = 'E:' + BS + 'MuMu Player 12' + BS + 'nx_main' + BS + 'adb.exe'\n"
        "cs['AutoDetect'] = False\n"
        "cs['AlwaysAutoDetect'] = False\n"
        "cs['AdbReplaced'] = True\n"
        "cs['Config'] = 'MuMuEmulator12'\n"
        "extras = cs.setdefault('Extras', {}).setdefault('MuMuEmulator12', {})\n"
        "extras['InstanceIndex'] = 1\n"
        "tq = d.setdefault('Configurations', {}).setdefault('Default', {}).setdefault('TaskQueue', [])\n"
        "for t in tq:\n"
        "    if isinstance(t, dict):\n"
        "        tt = t.get('TaskType', '')\n"
        "        if tt in ('StartUp','Recruit','Infrast','Mall','Award'):\n"
        "            t['IsEnable'] = True\n"
        "        elif tt in ('Fight','Roguelike','Reclamation'):\n"
        "            t['IsEnable'] = False\n"
        "p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding='utf-8')\n"
        "print('CONFIG_OK')\n"
    )
    b64 = base64.b64encode(script.encode("utf-8")).decode()
    # 写到目标机再执行
    ex("[IO.File]::WriteAllText('E:\\MAA-Multi\\_cfg1.py', [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + b64 + "')))", 30)
    r = ex("python E:\\MAA-Multi\\_cfg1.py", 60)
    print(r[:200])
