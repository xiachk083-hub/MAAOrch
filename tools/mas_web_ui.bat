@echo off
rem MAAOrch Web 看板启动器（带日志，便于排查为什么没起来）
cd /d "%~dp0.."
if not exist logs mkdir logs
echo [%date% %time%] 启动 MAAOrch Web 看板 ... >> logs\web_ui.log
"C:\Users\xiach\AppData\Local\Programs\Python\Python312\python.exe" main_web.pyw >> logs\web_ui.log 2>&1
echo [%date% %time%] MAAOrch Web 看板已退出（exit=%errorlevel%） >> logs\web_ui.log
exit /b 0
