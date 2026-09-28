# prod_probe.ps1 —— 生产槽位只读探针（跑在 MRFZ-0000 上；给肉鸽实测基线取证用）
# 目的：找出哪台 MAA-Multi 槽位在活动（asst.log 新鲜度）+ MuMuVMM 进程 + MAA 进程清单。
# 只读：不改、不杀。输出一行 base64（UTF-8 JSON）。
$ErrorActionPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'
$o = @{}
$slots = @()
foreach ($p in (Get-ChildItem 'E:\MAA-Multi' -Directory)) {
  $a = Join-Path $p.FullName 'debug\asst.log'
  if (Test-Path $a) {
    $i = Get-Item $a
    $slots += @{
      name   = $p.Name
      ageSec = [int]((Get-Date) - $i.LastWriteTime).TotalSeconds
      mtime  = $i.LastWriteTime.ToString('MM-dd HH:mm:ss')
    }
  }
}
$o.slots = $slots
$o.vmm = @(Get-Process MuMuVMMHeadless | ForEach-Object { $_.Id })
$o.maa = @(Get-Process MAA | ForEach-Object { @{ pid = $_.Id; path = $_.Path } })
$o.now = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
$json = $o | ConvertTo-Json -Depth 6 -Compress
[Console]::Out.Write([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json)))
