"""把下载脚本写到目标机并执行（避免多层引号转义）"""
import base64
import json
import urllib.request

CRED = json.load(open(r"C:\Users\xiach\.maorch_deploy.json", encoding="utf-8"))
MANAGER = CRED.get("manager", "http://100.79.173.69:19998")
TOKEN = CRED.get("manager_token", "")


def ex(cmd, t=600):
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
            return "HTTPERR " + e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            return "ERR " + str(e)[:200]


# 下载脚本（PowerShell，写到目标机再执行）
dl_script = (
    "$ErrorActionPreference='Continue'\n"
    "$url='https://github.com/AUTO-MAS-Project/AUTO-MAS/releases/download/v5.3.1/AUTO-MAS-Full-v5.3.1-x64.zip'\n"
    "$out='E:\\AUTO-MAS-download\\AUTO-MAS-Full-v5.3.1-x64.zip'\n"
    "New-Item -ItemType Directory -Force -Path 'E:\\AUTO-MAS-download' | Out-Null\n"
    "Write-Output 'DOWNLOAD_START'\n"
    "Invoke-WebRequest -Uri $url -OutFile $out -UseBasicParsing\n"
    "if (Test-Path $out) { $len=(Get-Item $out).Length; Write-Output ('DOWNLOAD_DONE ' + $len) } else { Write-Output 'DOWNLOAD_FAIL' }\n"
)
b64 = base64.b64encode(dl_script.encode("utf-8")).decode()
ex("[IO.File]::WriteAllText('E:\\AUTO-MAS-download\\dl.ps1', [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + b64 + "')))", 30)

# 后台执行（Start-Process 只带一个参数，无嵌套引号）
print("启动下载...")
r = ex("Start-Process powershell -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File E:\\AUTO-MAS-download\\dl.ps1' -WindowStyle Hidden -RedirectStandardOutput E:\\AUTO-MAS-download\\dl.log")
print(r)
