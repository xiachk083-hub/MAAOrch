@echo off
rem 手动启动 Python 上游（19997）—— 只有那些"还没搬到 Node 的旧功能"需要它。
rem 平时不必跑：实测它空转 ~47%% CPU / 141MB（有个线程在自旋，待查）。
cd /d "%~dp0.."
start "" /B "C:\Users\xiach\AppData\Local\Programs\Python\Python312\python.exe" main_web.pyw >> logs\web_ui.log 2>&1
echo [%date% %time%] 已启动 Python 上游（config.api_port，应为 19997） >> logs\web_ui.log
exit /b 0
