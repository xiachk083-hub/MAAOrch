# rogue_host_probe.ps1 —— 机队只读采样器（跑在 MRFZ-0000 上，由 tools/rogue_fleet_probe.mjs 通过 SSH 调用）
# ──────────────────────────────────────────────────────────────────────────────
# 为什么单独成文件：脚本变大后 `powershell -EncodedCommand` 会撞上 cmd.exe 的 8191 字符上限
# （报"命令行太长"，还是 GBK 花屏）。摆成主机上的文件后，每次调用只剩一条短命令行。
# 只需要**只读**：进程表 / 日志 tail / 配置里的 adb 地址 / adb pidof。绝不写、绝不杀。
# 输出：一行 base64（UTF-8 JSON），由本机解码 —— 免得中文被代码页搞坏。
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$PROBE_VER = '3'

function TailBytes([string]$p, [int]$n) {
  try {
    $fs = [IO.File]::Open($p, 'Open', 'Read', 'ReadWrite')
    $len = $fs.Length
    $start = [Math]::Max(0, $len - $n)
    $fs.Seek($start, 'Begin') | Out-Null
    $buf = New-Object byte[] ($len - $start)
    $fs.Read($buf, 0, $buf.Length) | Out-Null
    $fs.Close()
    return [Text.Encoding]::UTF8.GetString($buf)
  } catch { return '' }
}

$o = @{}
$o.ver = $PROBE_VER
$os = Get-CimInstance Win32_OperatingSystem
$o.host = @{
  ramFreeGB  = [int]($os.FreePhysicalMemory / 1MB)
  ramTotalGB = [int]($os.TotalVisibleMemorySize / 1MB)
  cpu        = (Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average
  diskEFreeGB = [int]((Get-PSDrive E).Free / 1GB)
}

# 产线在不在跑：E:\MAA-Multi 的槽位里有没有 90 秒内写过 asst.log 的
$prodActive = 0
$prodAll = 0
foreach ($p in (Get-ChildItem 'E:\MAA-Multi' -Directory -ErrorAction SilentlyContinue)) {
  $a = Join-Path $p.FullName 'debug\asst.log'
  if (Test-Path $a) {
    $prodAll++
    if (([int]((Get-Date) - (Get-Item $a).LastWriteTime).TotalSeconds) -lt 90) { $prodActive++ }
  }
}
$o.prod = @{ slots = [int]$prodAll; active = [int]$prodActive; maaProc = [int]@(Get-Process MAA -ErrorAction SilentlyContinue | Where-Object { $_.Path -like 'E:\MAA-Multi\*' }).Count }   # 强制 int：实测出现过 active=null（显示 null/16 很难看）

$adbE = 'E:\MuMu Player 12\shell\adb.exe'
$pkgs = @{
  'l-1' = 'com.YoStarJP.Arknights'; 'l-2' = 'com.YoStarJP.Arknights'
  'l-4' = 'com.hypergryph.arknights'; 'l-5' = 'com.YoStarJP.Arknights'
  'l-7' = 'com.YoStarJP.Arknights'
}
$o.machines = @{}
foreach ($n in @('l-1', 'l-2', 'l-4', 'l-5', 'l-7')) {
  $d = Join-Path 'E:\maa-alas\fz-maa' ('l\' + $n)
  $m = @{}
  $ap = Join-Path $d 'debug\asst.log'
  $gp = Join-Path $d 'debug\gui.log'
  $mp = Join-Path $d 'MAA.exe'
  $cfg = Join-Path $d 'config\gui.new.json'
  # 队列体检：启用了哪些任务（2026-09-23 实测 l-7 只勾了「开始唤醒」⇒ 发了车也只是进游戏发呆，白占一台）
  try {
    $co = Get-Content $cfg -Raw -Encoding UTF8 | ConvertFrom-Json
    $dd = $co.Configurations.($co.Current)
    if ($dd) { $m.queue = @($dd.TaskQueue | Where-Object { $_.IsEnable } | ForEach-Object { $_.TaskType }) }
  } catch { $m.queue = @() }
  $m.proc = @(Get-Process MAA -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $mp }).Count
  if (Test-Path $ap) { $i = Get-Item $ap; $m.asstSizeKB = [int]($i.Length / 1KB); $m.asstAge = [int]((Get-Date) - $i.LastWriteTime).TotalSeconds } else { $m.asstAge = -1 }
  if (Test-Path $gp) { $i = Get-Item $gp; $m.guiSizeKB = [int]($i.Length / 1KB); $m.guiAge = [int]((Get-Date) - $i.LastWriteTime).TotalSeconds } else { $m.guiAge = -1 }

  $t = TailBytes $ap 32000
  if ($t) {
    # ⚠️ 复核 F2：原来把 `ProcessTask|Call .` 全抓进来，6 行窗口常被无信息的 Call 行占满 ⇒ cur_task 取不到
    # （活体 192/320 条为空）。现在只留**有信息**的行（cur_task / 链结局 / 超限 / 子错误），窗口全给它们。
    $ls = $t -split "\r?\n" | Where-Object { $_ -match 'cur_task|TaskChainError|TaskChainCompleted|ExceededLimit|SubTaskError' }
    $m.asstLast = @($ls | Select-Object -Last 6)
  }
  $g = TailBytes $gp 6000
  if ($g) {
    $m.guiLast = @(($g -split "\r?\n" | Where-Object { $_ -match '开始任务|完成任务|任务出错|任务已全部完成|本轮任务出现以下错误|已停止|正在运行中|FPS|游戏掉线' } | Select-Object -Last 8))
  }

  # 游戏客户端活着吗？MAA"在跑"不等于客户端在（2026-09-23 实测：客户端没了它会一路空转）
  if (Test-Path $cfg) {
    $ct = [IO.File]::ReadAllText($cfg, [Text.Encoding]::UTF8)
    $am = [regex]::Match($ct, '"Address"\s*:\s*"([^"]+)"')
    if ($am.Success) { $m.addr = $am.Groups[1].Value }
  }
  if ($m.proc -gt 0 -and $m.addr) {
    & $adbE connect $m.addr 2>&1 | Out-Null
    # 一次 adb 拿两个信号：游戏进程在不在 + 前台是不是游戏（marker 分段，免得 pidof 空输出把 dumpsys 里的 hash 当 pid）。
    # ⚠️ 2026-09-24 独立复核 F1：以前只要看到 P-MARK 就下结论 —— 而 adb 半截返回（P-MARK 回来了、
    # pidof/dumpsys 却是空的，实测 28/28 次 game=false **全部**是这种半截读）会把"其实在跑的客户端"
    # 判成"客户端不在" ⇒ 看守 3 轮后 killMaa，误杀正常在刷的机器。
    # ⇒ 必须**三个证据齐全**（P-MARK + F-MARK + mCurrentFocus=）才敢写 game；否则重试，两次都不全就**不设**
    #    （JS 侧当"未知"，永远不会落进 client-gone）。
    for ($try = 1; $try -le 2; $try++) {
      $raw = ((& $adbE -s $m.addr shell "echo P-MARK; pidof $($pkgs[$n]); echo F-MARK; dumpsys window | grep mCurrentFocus") 2>&1) -join "`n"
      if (($raw -match 'P-MARK') -and ($raw -match 'F-MARK') -and ($raw -match 'mCurrentFocus=')) {
        $pSeg = ($raw -split 'F-MARK')[0] -replace '(?s).*P-MARK', ''
        $m.game = [bool]($pSeg -match '(?m)^\s*\d+')
        $fm = [regex]::Match($raw, 'mCurrentFocus=Window\{[^}]*?\s([\w.]+)/')
        if ($fm.Success) {
          $m.fg = $fm.Groups[1].Value
          $m.fgOk = ($m.fg -eq $pkgs[$n])
        }
        break
      }
      Start-Sleep -Milliseconds 1500
    }
  }

  # 屏幕指纹（2026-09-24 新增，见 docs/ROGUE_WATCH.md §5.21）：设备侧 `screencap -p | md5sum`，
  # **一次 adb 往返**、不回传图片（实测 ~0.9s/台）。为什么必须有它："MAA 在跑、日志也新鲜，但画面
  # 一像素没动"这种**静默冻结**只看进程/日志永远发现不了（l-2 在「探索終了」上僵 3.4 小时）。
  # 取不到就**不设字段**（JS 侧当"未知"，不据此下任何结论）；与 pidof 一样要求 marker 齐全才认。
  if ($m.proc -gt 0 -and $m.addr) {
    for ($t3 = 1; $t3 -le 2; $t3++) {
      $raw3 = ((& $adbE -s $m.addr shell "echo S-MARK; screencap -p | md5sum; echo E-MARK") 2>&1) -join "`n"
      $hs = [regex]::Match($raw3, '(?m)^([0-9a-f]{32})\s')
      if (($raw3 -match 'S-MARK') -and ($raw3 -match 'E-MARK') -and $hs.Success) { $m.screenMd5 = $hs.Groups[1].Value; break }
      Start-Sleep -Milliseconds 1200
    }
  }

  $o.machines[$n] = $m
}

$json = $o | ConvertTo-Json -Depth 8 -Compress
[Console]::Out.Write([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json)))

