# MAAOrch 看板自启（无窗口）—— 计划任务 MAAOrch-WebUI 每 5 分钟调一次
# ────────────────────────────────────────────────────────────────────
# 2026-09-22 修（用户报「一直有弹窗」）：
#   旧任务动作 = D:\1-2\MAAOrch\tools\mas_web_ui.bat（Python 旧看板 main_web.pyw）。
#   两个毛病：① 交互会话里跑 .bat 会分配控制台 ⇒ 桌面每 5 分钟闪一个黑窗；
#   ② payload 早失效 —— 实测 web_ui.log 停在 05:25、19999 从来没起来过。
#   现在改成：run_hidden.vbs（wscript，无控制台）→ 本脚本（隐藏）
#             → node server\maaorch-web.mjs 门面（19999）。
# 幂等：19999 已在监听就直接退出，不重复起；每次跑一行日志到 logs\webui_task.log。
# 说明：Node 门面自带 21+ 个原生端点，未实现的路由才透传 127.0.0.1:19997（Python 上游按需拉起）。
# ────────────────────────────────────────────────────────────────────
$ErrorActionPreference = 'Continue'

$root = Split-Path -Parent $PSScriptRoot          # tools -> 仓库根
Set-Location $root
$logDir = Join-Path $root 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$log = Join-Path $logDir 'webui_task.log'

function Write-Log([string]$msg) {
  Add-Content -Path $log -Value ("[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg) -Encoding UTF8
}

function Test-Port([int]$port, [int]$timeoutMs = 500) {
  $client = New-Object System.Net.Sockets.TcpClient
  try {
    $iar = $client.BeginConnect('127.0.0.1', $port, $null, $null)
    if (-not $iar.AsyncWaitHandle.WaitOne($timeoutMs)) { return $false }
    $client.EndConnect($iar)
    return $client.Connected
  } catch {
    return $false
  } finally {
    $client.Close()
  }
}

if (Test-Port 19999) {
  Write-Log 'skip: 19999 已在监听（看板在跑，什么都不做）'
  exit 0
}

$node = (Get-Command node -ErrorAction SilentlyContinue).Source
if (-not $node) {
  foreach ($cand in @('C:\Program Files\nodejs\node.exe', "$env:ProgramFiles\nodejs\node.exe", "$env:LOCALAPPDATA\Programs\nodejs\node.exe")) {
    if (Test-Path $cand) { $node = $cand; break }
  }
}
if (-not $node) {
  Write-Log 'FAIL: 找不到 node.exe'
  exit 1
}

Write-Log ("start: {0} server\maaorch-web.mjs --port 19999 --upstream http://127.0.0.1:19997" -f $node)

try {
  Start-Process -FilePath $node `
    -ArgumentList @('server\maaorch-web.mjs', '--port', '19999', '--upstream', 'http://127.0.0.1:19997') `
    -WorkingDirectory $root -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logDir 'node_dashboard.log') `
    -RedirectStandardError  (Join-Path $logDir 'node_dashboard.err.log')
} catch {
  Write-Log ("FAIL: 起进程异常 " + $_.Exception.Message)
  exit 1
}

# 起完确认一下（Node 门面启动 < 1 秒；给 3 秒余量）
$ok = $false
foreach ($i in 1..6) {
  Start-Sleep -Milliseconds 500
  if (Test-Port 19999) { $ok = $true; break }
}

if ($ok) {
  Write-Log 'ok: 19999 已起来（看板可用）'
  exit 0
} else {
  Write-Log 'WARN: 进程已起但 19999 还没监听（看 logs\node_dashboard.err.log）'
  exit 2
}
