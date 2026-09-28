# scheduler_watchdog_launch.ps1 -- hidden launcher for the scheduler watchdog.
# Scheduled task MAAOrch-SchedulerWatchdog (every 5 min) -> tools\run_hidden.vbs -> this file.
# Runs: python tools\scheduler_watchdog.py --once --alert-send
# Exit codes from the tool: 0 = OK / boot grace, 4 = ALERT (alert payload written; notification
# sent when the channel is up). Only codes other than 0/4 are recorded as task failures.
# run_hidden.vbs starts this script detached (no wait), so the code below logs its own failures.
# ASCII only: PowerShell 5.1 mis-decodes non-BOM UTF-8 script files.
$ErrorActionPreference = 'Continue'
$root = 'D:\1-2\MAAOrch'
Set-Location $root

$py = 'C:\Users\xiach\AppData\Local\Programs\Python\Python312\python.exe'
if (-not (Test-Path $py)) {
  $c = Get-Command python -ErrorAction SilentlyContinue
  if ($c) { $py = $c.Source }
}
if (-not $py) { exit 1 }

$out = Join-Path $env:TEMP 'scheduler_watchdog_out.txt'
$err = Join-Path $env:TEMP 'scheduler_watchdog_err.txt'
$p = Start-Process -FilePath $py -ArgumentList 'tools\scheduler_watchdog.py','--once','--alert-send' `
  -WorkingDirectory $root -WindowStyle Hidden -Wait -PassThru `
  -RedirectStandardOutput $out -RedirectStandardError $err

if ($p.ExitCode -ne 0 -and $p.ExitCode -ne 4) {
  $tail = ''
  if (Test-Path $err) { $tail = (Get-Content $err -Raw -ErrorAction SilentlyContinue) }
  Add-Content -Path (Join-Path $root 'logs\scheduler_watchdog_task.log') -Encoding UTF8 `
    -Value ("[{0}] rc={1} {2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $p.ExitCode, ($tail -replace '\s+', ' '))
}
exit $p.ExitCode
