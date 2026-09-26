' run_hidden.vbs — 让"计划任务里跑 .ps1/.bat"变成彻底无窗口，桌面不再闪黑窗
' ────────────────────────────────────────────────────────────────────
' 由来：2026-09-22 用户「一直有弹窗」。实测 MAAOrch-WebUI 任务每 5 分钟
'   在**交互会话**里跑 mas_web_ui.bat ? cmd.exe 分配控制台 ? 桌面闪一个黑窗。
'   CREATE_NO_WINDOW 只能用于我们自己 spawn 的子进程；计划任务的动作本身
'   一定会分配控制台，所以外面必须套一层 GUI 子系统进程（wscript）。
' 用法（任务动作）：
'   wscript.exe //nologo "D:\1-2\MAAOrch\tools\run_hidden.vbs" "<脚本路径.ps1|.bat>" [参数...]
' 原理：wscript 自身无控制台；WScript.Shell.Run(cmd, 0, False) 的窗口样式 0 = 隐藏。
' ────────────────────────────────────────────────────────────────────
Option Explicit

Dim sh, fso, args, script, extra, i, cmd

Set args = WScript.Arguments
If args.Count = 0 Then
  WScript.Quit 2
End If

script = args(0)
extra = ""
For i = 1 To args.Count - 1
  extra = extra & " " & Chr(34) & args(i) & Chr(34)
Next

Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = fso.GetParentFolderName(script)

If LCase(Right(script, 4)) = ".ps1" Then
  cmd = "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File " & Chr(34) & script & Chr(34) & extra
Else
  cmd = "cmd.exe /c " & Chr(34) & script & Chr(34) & extra
End If

' 0 = 隐藏窗口；False = 不等待返回（计划任务立刻结束，不留任何可见窗口）
sh.Run cmd, 0, False
