# MPE LocalBridge 自启（无窗口）—— 计划任务 MAAOrch-MpeLocalBridge 登录时调一次
# ────────────────────────────────────────────────────────────────────
# 2026-09-22 修（用户报「一直有弹窗」）：
#   旧任务动作 = cmd.exe /c mpelb_start.bat ⇒ 交互会话里分配控制台，桌面弹一个黑窗；
#   而且 mpelb.exe 是**前台**跑，那个黑窗会一直挂在桌面上直到 mpelb 退出。
#   现在：run_hidden.vbs（wscript，无控制台）→ 本脚本（隐藏）→ mpelb.exe（-WindowStyle Hidden）。
# 幂等：9066 已在监听就直接退出。日志：logs\mpelb_task.log（mpelb 自己的日志仍在 logs\mpelb\）。
# ────────────────────────────────────────────────────────────────────
$ErrorActionPreference = 'Continue'

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$logDir = Join-Path $root 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$log = Join-Path $logDir 'mpelb_task.log'

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

$mpelb = Join-Path $env:LOCALAPPDATA 'mpelb\mpelb.exe'
$fwRoot = 'D:\Xiach\M-A\MAAW\maa-arknights-fw'
$iface  = 'D:\Xiach\M-A\MXU\MXU-win-x86_64-v1.21.2\interface.json'
$plog   = Join-Path $logDir 'mpelb'

if (-not (Test-Path $mpelb)) { Write-Log ("FAIL: 找不到 " + $mpelb); exit 1 }
if (-not (Test-Path $iface)) { Write-Log ("FAIL: 找不到 " + $iface); exit 1 }

if (Test-Port 9066) {
  Write-Log 'skip: 9066 已在监听（MPE LocalBridge 在跑）'
  exit 0
}

if (-not (Test-Path $plog)) { New-Item -ItemType Directory -Path $plog -Force | Out-Null }
Write-Log ("start: {0} --root {1} --interface {2} --port 9066" -f $mpelb, $fwRoot, $iface)

try {
  Start-Process -FilePath $mpelb `
    -ArgumentList @('--root', $fwRoot, '--interface', $iface, '--port', '9066', '--log-level', 'INFO', '--log-dir', $plog) `
    -WorkingDirectory $root -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logDir 'mpelb.run.log') `
    -RedirectStandardError  (Join-Path $logDir 'mpelb.err.log')
} catch {
  Write-Log ("FAIL: 起进程异常 " + $_.Exception.Message)
  exit 1
}

$ok = $false
foreach ($i in 1..8) {
  Start-Sleep -Milliseconds 500
  if (Test-Port 9066) { $ok = $true; break }
}
if ($ok) { Write-Log 'ok: 9066 已起来' ; exit 0 } else { Write-Log 'WARN: 进程起了但 9066 未监听（看 logs\mpelb.err.log）'; exit 2 }
