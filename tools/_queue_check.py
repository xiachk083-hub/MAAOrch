"""用 PowerShell 探测生产 queue.json 结构（无 python 嵌套）。"""
import json
import urllib.request
from pathlib import Path

cfg = json.load(open(Path.home() / ".maorch_deploy.json", encoding="utf-8"))
MANAGER = cfg["manager"].rstrip("/")
MTOKEN = cfg["manager_token"]

script = r"""
$raw = [IO.File]::ReadAllText('E:\MAAOrch\services\queue.json')
$q = $raw | ConvertFrom-Json
if ($q -is [System.Array]) {
  Write-Output ("ARRAY len=" + $q.Length)
  Write-Output ($q | Select-Object -First 3 | ConvertTo-Json -Compress -Depth 3)
} else {
  Write-Output ("OBJECT keys=" + (($q.PSObject.Properties.Name) -join ','))
  $p = $q.pending; if ($p) { Write-Output ("pending=" + $p.Count) }
  $a = $q._active_emus; if ($a) { Write-Output ("active_emus=" + ($a.PSObject.Properties.Name -join ',')) }
}
"""
body = json.dumps({"command": script, "timeout": 60}).encode()
req = urllib.request.Request(MANAGER + "/api/exec", data=body,
                             headers={"x-manager-token": MTOKEN, "Content-Type": "application/json"})
d = json.loads(urllib.request.urlopen(req, timeout=70).read().decode("utf-8", "replace"))
print((d.get("stdout") or d.get("stderr", "")).strip())
