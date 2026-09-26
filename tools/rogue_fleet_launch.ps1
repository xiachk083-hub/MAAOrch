# rogue_fleet_launch.ps1 -- hidden launcher for the roguelike fleet keeper (l-1/l-2/l-4/l-5/l-7).
# Scheduled task MAAOrch-RogueFleet (every 2 min) -> tools\run_hidden.vbs -> this -> node ... --apply
# The node script keeps its own logs (runtime\rogue_fleet_watch.log + .jsonl); here we only
# record a non-zero exit so silent failures do not vanish.
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
if (-not $node) { exit 1 }

$out = Join-Path $env:TEMP 'rogue_fleet_out.txt'
$err = Join-Path $env:TEMP 'rogue_fleet_err.txt'
$p = Start-Process -FilePath $node -ArgumentList 'tools\rogue_fleet_watch.mjs', '--apply' `
  -WorkingDirectory $root -WindowStyle Hidden -Wait -PassThru `
  -RedirectStandardOutput $out -RedirectStandardError $err

if ($p.ExitCode -ne 0) {
  $tail = ''
  if (Test-Path $err) { $tail = (Get-Content $err -Raw -ErrorAction SilentlyContinue) }
  Add-Content -Path (Join-Path $root 'logs\rogue_fleet_task.log') -Encoding UTF8 `
    -Value ("[{0}] rc={1} {2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $p.ExitCode, ($tail -replace '\s+', ' '))
}
