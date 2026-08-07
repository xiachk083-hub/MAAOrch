# MAAOrch-Manager One-Click Deploy/Update
# Usage: powershell -ExecutionPolicy Bypass -File deploy.ps1
$ErrorActionPreference = 'Stop'
$dir = 'E:\MAAOrch-Manager'
New-Item -ItemType Directory -Path $dir -Force | Out-Null

Write-Host "[1/6] Downloading manager.py..."
curl.exe -L -o "$dir\manager.py.new" "https://raw.githubusercontent.com/xiachk083-hub/MAAOrch/main/manager/manager.py" --max-time 90
if (-not (Test-Path "$dir\manager.py.new")) { Write-Host "Download FAILED" -ForegroundColor Red; exit 1 }
$content = Get-Content "$dir\manager.py.new" -Raw -Encoding UTF8
if ($content -notmatch 'MAAOrch-Manager') { Write-Host "Checksum FAILED" -ForegroundColor Red; exit 1 }

Write-Host "[2/6] Stopping old manager (port 19998)..."
$conn = Get-NetTCPConnection -LocalPort 19998 -ErrorAction SilentlyContinue
if ($conn) { Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue; Start-Sleep 2 }

Write-Host "[3/6] Replacing manager.py..."
if (Test-Path "$dir\manager.py") { Copy-Item "$dir\manager.py" "$dir\manager.py.old" -Force }
Move-Item "$dir\manager.py.new" "$dir\manager.py" -Force

Write-Host "[4/6] Starting manager..."
Start-Process pythonw -ArgumentList "$dir\manager.py" -WorkingDirectory $dir
Start-Sleep 3

Write-Host "[5/6] Verifying..."
$token = ''
if (Test-Path "$dir\config.json") { $token = (Get-Content "$dir\config.json" -Raw | ConvertFrom-Json).token }
if ($token) {
    try {
        $resp = Invoke-RestMethod -Uri "http://127.0.0.1:19998/api/status" -Headers @{"x-manager-token" = $token } -TimeoutSec 8
        Write-Host "Manager OK - project_exists: $($resp.project_exists)" -ForegroundColor Green
    } catch {
        Write-Host "Manager starting..." -ForegroundColor Yellow
    }
} else {
    Write-Host "First run - config generating..." -ForegroundColor Yellow
}

Write-Host "[6/6] Token:"
if (Test-Path "$dir\config.json") { (Get-Content "$dir\config.json" -Raw | ConvertFrom-Json).token }
Write-Host "DONE"
