"""查目标机模拟器 1 的 MuMuManager 真实状态"""
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
import subprocess, json
cli = r"E:\MuMu Player 12\nx_main\MuMuManager.exe"
import os
print("MuMuManager exists:", os.path.exists(cli))
for idx in ["1"]:
    r = subprocess.run([cli, "info", "-v", idx], capture_output=True, text=True, timeout=10,
                       encoding="utf-8", errors="replace")
    print("=== 模拟器", idx, "returncode:", r.returncode)
    if r.returncode == 0:
        d = json.loads(r.stdout.lstrip(chr(0xfeff)).strip())
        print("  name:", d.get("name"))
        print("  is_process_started:", d.get("is_process_started"))
        print("  is_android_started:", d.get("is_android_started"))
        print("  adb_port:", d.get("adb_port"))
    else:
        print("  stdout:", r.stdout[:150])
        print("  stderr:", r.stderr[:150])
'''
    b64 = base64.b64encode(script.encode("utf-8")).decode()
    ex("[IO.File]::WriteAllText('E:\\emu_maa_tool\\_emu_check.py', [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + b64 + "')))", 30)
    r = ex("python E:\\emu_maa_tool\\_emu_check.py", 60)
    print(r)
