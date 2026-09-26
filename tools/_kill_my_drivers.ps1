# 只杀我自己今晚起的打关进程（驱动 + 它拉起的 MAA 子进程），不碰 llm-gateway / auto_mas 助手
$mine = 'live_drive|maa_copilot_run|plan_loop|ga_ex_drive|screen_state|Asst\.load'
$procs = Get-CimInstance Win32_Process -Filter "name='python.exe'" |
         Where-Object { $_.CommandLine -match $mine }
foreach ($p in $procs) {
    try {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
        "killed $($p.ProcessId)"
    } catch {
        "FAILED $($p.ProcessId): $($_.Exception.Message)"
    }
}
"--- 剩余 python 进程 ---"
Get-CimInstance Win32_Process -Filter "name like '%python%'" |
    Select-Object ProcessId, @{n='Cmd'; e={$_.CommandLine.Substring(0,[Math]::Min(60,$_.CommandLine.Length))}} |
    Format-Table -AutoSize
