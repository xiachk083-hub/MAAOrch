# 无人值守跑队列（Lead 专用）。纪律：同一模拟器只能有一个 MAA。
# 用法： pwsh -File tools\run_campaign.ps1 [-Hours 6] [-MaxUnits 20]
param(
    [double]$Hours = 999,
    [int]$MaxUnits = 999
)

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# 2026-09-24 修复：必须用 3.12（cv2/rapidocr 在 3.11 的 hermes venv 里没有）。
# 同时把它插到 PATH 最前，保证队列/工具里**裸 `python` 起的子进程**也解析到 3.12。
$PY = 'C:\Users\xiach\AppData\Local\Programs\Python\Python312\python.exe'
$env:PATH = (Split-Path -Parent $PY) + ';' + $env:PATH

function Our-Jobs {
    # 只认真正在跑的 python/node 解释器进程。**绝不能按 bash/pwsh 的命令行文本判**：
    #   Hermes 的 shell 会把整条命令原样写进自己的 cmdline，命令里只要提到
    #   `event_sweep.mjs`（哪怕是 `--help` 或一次只读复核）就会被误判成"别人的 sweep"，
    #   启动器于是拒绝启动 —— 2026-09-24 19:24 实测踩到（pid 是 bash.exe，不是 sweep）。
    Get-CimInstance Win32_Process |
        Where-Object { $_.Name -match '^(python|pythonw|node)(\.exe)?$' } |
        # 只读/自检调用**不算"别人在跑"**（2026-09-24：审计复核 `--selftest` 会被原判据误拦，
        #   多智能体并行时审计和跑批会互踩）。放行：--selftest/--help/--dry/只读参数。
        Where-Object { $_.CommandLine -notmatch '--(selftest|help|dry|read-tickets|leave-battle|settle-replay|list|show)\b' } |
        Where-Object { $_.CommandLine -like '*event_sweep.mjs*' -or $_.CommandLine -like '*stage_attempt.py*' -or $_.CommandLine -like '*_prime_queue3.py*' } |
        Where-Object { $_.ProcessId -ne $PID }
}

# ① 服务
$svc = $null
try { $svc = Invoke-RestMethod 'http://127.0.0.1:8765/health' -TimeoutSec 5 } catch { }
if (-not $svc -or -not $svc.ok) {
    Write-Host "[启动器] 服务不在，拉起来…"
    Start-Process -FilePath $PY -ArgumentList 'tools\maaorch_service.py', '--port', '8765' `
        -WorkingDirectory $root -WindowStyle Hidden
    Start-Sleep -Seconds 12
    try { $svc = Invoke-RestMethod 'http://127.0.0.1:8765/health' -TimeoutSec 5 } catch { }
}
if (-not $svc -or -not $svc.ok) { Write-Host "[启动器] ✗ 服务起不来，放弃"; exit 2 }
Write-Host "[启动器] ✓ 服务 ok dev=$($svc.dev)"

# ①b 排序刷新器（2026-09-24 20:45 事故：它存在但**从没被拉起** ⇒ axis_rank.json 静默僵 7 小时，
#     队列一直用 7 小时前的 P(3星) 排序选轴。审计员查出；这里补上"进程守卫 + 新鲜度告警"。）
$refresher = Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" |
    Where-Object { $_.CommandLine -like '*_rank_refresh.py*' }
if (-not $refresher) {
    Write-Host "[启动器] 排序刷新器不在，拉起来（每 180s 重算 axis_rank.json）…"
    Start-Process -FilePath (Join-Path (Split-Path -Parent $PY) 'pythonw.exe') `
        -ArgumentList 'runtime\_rank_refresh.py' -WorkingDirectory $root -WindowStyle Hidden
    Start-Sleep -Seconds 3
    $refresher = Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" |
        Where-Object { $_.CommandLine -like '*_rank_refresh.py*' }
}
if ($refresher) { Write-Host "[启动器] ✓ 排序刷新器在跑 pid=$($refresher.ProcessId -join ',')" }
else { Write-Host "[启动器] ⚠ 排序刷新器起不来 ⇒ 排序会僵（先跑 runtime\_rank_refresh.py --once 看日志）" }

$rankFile = Join-Path $root 'runtime\axis_rank.json'
if (Test-Path $rankFile) {
    $ageMin = [Math]::Round(((Get-Date) - (Get-Item $rankFile).LastWriteTime).TotalMinutes, 1)
    if ($ageMin -gt 10) {
        Write-Host "[启动器] ⚠ axis_rank.json 已 $ageMin 分钟没更新（>10）⇒ 队列会用过期排序选轴"
    } else { Write-Host "[启动器] ✓ 排序新鲜（$ageMin 分钟前）" }
} else { Write-Host "[启动器] ⚠ runtime\axis_rank.json 不在 ⇒ event_sweep 退回启发式排序" }

# ② 互斥：不许和别人的 sweep/attempt 抢模拟器
$busy = Our-Jobs
if ($busy) {
    Write-Host "[启动器] ✗ 已有 $($busy.Count) 个 sweep/attempt 在跑 ⇒ 拒绝启动（同一模拟器只能一个 MAA）"
    $busy | ForEach-Object { Write-Host ("   pid={0} {1}" -f $_.ProcessId, ($_.CommandLine -replace '\s+', ' ').Substring(0, [Math]::Min(90, ($_.CommandLine -replace '\s+', ' ').Length))) }
    exit 3
}

$log = "runtime\reports\campaign_$(Get-Date -Format yyyyMMdd_HHmmss).log"
$deadline = (Get-Date).AddHours($Hours)
Write-Host "[启动器] 开始；日志 $log；上限 ${Hours}h / $MaxUnits 单位"

$before = 0
try {
    $led = Get-Content 'runtime\stone_ledger.json' -Raw | ConvertFrom-Json
    $before = [int]$led.'当前持有'
} catch { }
Write-Host "[启动器] 开跑前持有：$before 颗"

$round = 0; $noProgress = 0; $lastHeld = $before
# ③ 循环：每轮跑一个单位（队列自己按 EV/理智 排产、自己演习筛轴）
while ((Get-Date) -lt $deadline -and $round -lt $MaxUnits) {
    $round++
    if (Our-Jobs | Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -like '*event_sweep.mjs*' }) {
        Write-Host "[启动器] 检测到别的 sweep 出现 ⇒ 让位，停止"; break
    }
    "=== 第 $round 轮 $(Get-Date -Format HH:mm:ss) ===" | Tee-Object -FilePath $log -Append
    # 队列的 --dry 只是看排产；真跑用默认（它内部会串行调用 sweep）
    # 队列一次跑完整排队（按理智升序逐个打；自己带理智/等待/演习闸门）。
    # 2026-09-24 修复：旧行传了 `--max-units 1`，而 _prime_queue3.py 没有这个参数
    # ⇒ 每轮 argparse 直接报错退出（连 3 轮无进展误判停摆）。轮数上限由本脚本的 $MaxUnits 管。
    # 2026-09-24 18:2x：**切回活动/插曲侧**（用户指示「去插曲里」）。主线扫了 224/307 只证明
    #   主题曲 7~14 章基本挖干（唯一例外见日志 JT8-3 徽章=True），而插曲/别传侧（EP/PV/MT/LE/EA/
    #   CV/AD 共约 190 个单位）里 EP 8+16、PV 8+31、MT 15+21、LE 7+17、EA 1+22 都还待打。
    #   ⚠ 2026-09-24 决定**不再传 `--retry-noreach`**（达芬奇实测：启动器传它 ⇒ 队列里 `force`
    #   恒真 ⇒ 82 个 noreach 单位**每轮全部无条件重打**，TTL 冷却这条唯一的刹车被拆掉）。
    #   入口修好之前那等于"每轮在同一处再失败一次并重盖时间戳"，吃掉一轮的导航时间。
    #   清扫时机：入口/工装修好后**手动**跑一次
    #   `python runtime\_prime_queue3.py --force-noreach`（手跑钉 Python312 绝对路径；裸 python 无 cv2），然后回到默认（TTL 6h 生效）。
    & $PY runtime\_prime_queue3.py 2>&1 |
        Tee-Object -FilePath $log -Append | Select-Object -Last 3

    # ④断路器（2026-09-25）：队列连续 5 个单位 0 判决会静默停跑（退出码 47 + runtime\_prime_halt.json）。
    #   这里必须接住它 —— 否则启动器会再拉下一轮，把同一堵墙再撞一遍（A 批第 2 轮就是这么来的）。
    if ($LASTEXITCODE -eq 47) {
        Write-Host "[启动器] 🛑 断路器：队列连续 5 个单位 0 判决（系统级卡死）⇒ 停止批。"
        Write-Host "[启动器]    告警：runtime\_prime_halt.json（查清后删掉它再重跑）"
        break
    }

    $held = $lastHeld
    try {
        $led = Get-Content 'runtime\stone_ledger.json' -Raw | ConvertFrom-Json
        $held = [int]$led.'当前持有'
    } catch { }
    if ($held -gt $lastHeld) { $noProgress = 0; $lastHeld = $held } else { $noProgress++ }
    Write-Host "[启动器] 轮 $round 结束：持有 $held（连续无进展 $noProgress）"
    if ($noProgress -ge 3) { Write-Host "[启动器] 连续 3 轮无进展 ⇒ 停（去查为什么挖不出矿）"; break }
}

try {
    $led = Get-Content 'runtime\stone_ledger.json' -Raw | ConvertFrom-Json
    Write-Host "[启动器] 收工：持有 $($led.'当前持有') 颗（本轮新增 $([int]$led.'当前持有' - $before)）"
    Write-Host "[启动器] ⚠ 请人工核对大厅顶栏源石数（账本不作为唯一凭据）"
} catch { }
