"""用 MuMuManager 启动模拟器 1 并等待安卓系统就绪"""
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
    script = r'''
import subprocess, json, time, os
cli = r"E:\MuMu Player 12\nx_main\MuMuManager.exe"
idx = "1"
print("launch 模拟器", idx)
r = subprocess.run([cli, "control", "-v", idx, "launch"], capture_output=True, text=True, timeout=30,
                   encoding="utf-8", errors="replace")
print("launch rc:", r.returncode, r.stdout[:100], r.stderr[:100])
# 等待安卓系统就绪（最多 120s）
for i in range(40):
    time.sleep(3)
    r = subprocess.run([cli, "info", "-v", idx], capture_output=True, text=True, timeout=10,
                       encoding="utf-8", errors="replace")
    if r.returncode == 0:
        d = json.loads(r.stdout.lstrip(chr(0xfeff)).strip())
        print(f"  检查{i}: process={d.get('is_process_started')} android={d.get('is_android_started')} adb={d.get('adb_port')}")
        if d.get("is_android_started") and d.get("adb_port"):
            print("READY adb_port=", d.get("adb_port"))
            break
    else:
        print(f"  检查{i}: 查询失败")
'''
    b64 = base64.b64encode(script.encode("utf-8")).decode()
    ex("[IO.File]::WriteAllText('E:\\emu_maa_tool\\_emu_launch.py', [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + b64 + "')))", 30)
    r = ex("python E:\\emu_maa_tool\\_emu_launch.py", 180)
    print(r[-1200:])
