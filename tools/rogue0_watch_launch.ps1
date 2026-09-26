# rogue0_watch_launch.ps1 -- hidden launcher for the #0 (JP / emulator-5554) roguelike keeper.
# Called by scheduled task MAAOrch-Rogue0Watch (every 2 min) via tools\run_hidden.vbs
#   -> wscript (no console window) -> this script -> node tools\rogue0_watch.mjs --apply
# The node script writes its own human log to runtime\rogue0_watch.log and state to
# runtime\rogue0_watch_state.json, so here we only record the exit code.
# ASCII only: PowerShell 5.1 mis-decodes non-BOM UTF-8 script files.
$ErrorActionPreference = 'Continue'
$root = 'D:\1-2\MAAOrch'
Set-Location $root

$node = (Get-Command node -ErrorAction SilentlyContinue).Source
if (-not $node) {
  foreach ($c in @('C:\Program Files\nodejs\node.exe', "$env:LOCALAPPDATA\Programs\nodejs\node.exe")) {
    if (Test-Path $c) { $node = $c; break }
  }
}
if (-not $node) {
  Add-Content -Path (Join-Path $root 'logs\rogue0_task.log') -Value ("[{0}] FAIL node.exe not found" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')) -Encoding UTF8
  exit 1
}

$out = Join-Path $env:TEMP 'rogue0_watch_out.txt'
$err = Join-Path $env:TEMP 'rogue0_watch_err.txt'
$p = Start-Process -FilePath $node -ArgumentList 'tools\rogue0_watch.mjs', '--apply' `
  -WorkingDirectory $root -WindowStyle Hidden -Wait -PassThru `
  -RedirectStandardOutput $out -RedirectStandardError $err

if ($p.ExitCode -ne 0) {
  $tail = ''
  if (Test-Path $err) { $tail = (Get-Content $err -Raw -ErrorAction SilentlyContinue) }
  $line = "[{0}] rc={1} err={2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $p.ExitCode, ($tail -replace '\s+', ' ').Substring(0, [Math]::Min(300, $tail.Length))
  Add-Content -Path (Join-Path $root 'logs\rogue0_task.log') -Value $line -Encoding UTF8
}
