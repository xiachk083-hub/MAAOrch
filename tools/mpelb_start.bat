@echo off
rem ============================================================
rem  MPE LocalBridge (mpelb) ??? ? ? MaaFramework ????????
rem  root  = ??? FW ?????pipeline JSON ?? resource\pipeline?
rem  PI    = MXU ????? interface.json????????????? resource?
rem  ??  = 9066?MPE ??????????????????????
rem  ??  = D:\1-2\MAAOrch\logs\mpelb.log?????+ logs\mpelb\??????
rem ============================================================
setlocal
set "MPELB=C:\Users\xiach\AppData\Local\mpelb\mpelb.exe"
set "ROOT=D:\Xiach\M-A\MAAW\maa-arknights-fw"
set "IFACE=D:\Xiach\M-A\MXU\MXU-win-x86_64-v1.21.2\interface.json"
set "LOGDIR=D:\1-2\MAAOrch\logs\mpelb"

if not exist "%LOGDIR%" mkdir "%LOGDIR%"

echo. >> "D:\1-2\MAAOrch\logs\mpelb.log"
echo ===== %date% %time% start mpelb ===== >> "D:\1-2\MAAOrch\logs\mpelb.log"
"%MPELB%" --root "%ROOT%" --interface "%IFACE%" --port 9066 --log-level INFO --log-dir "%LOGDIR%" >> "D:\1-2\MAAOrch\logs\mpelb.log" 2>&1
echo ===== %date% %time% mpelb exit code=%errorlevel% ===== >> "D:\1-2\MAAOrch\logs\mpelb.log"
endlocal
