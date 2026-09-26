@echo off
rem ─────────────────────────────────────────────────────────────
rem MAAOrch 平台启动器（2026-09-21 起：**只拉起 Node 门面**）
rem   Node  : 19999（用户入口；自带 21+ 个端点的原生实现，其余透传上游）
rem   Python: 19997（旧看板，**不再常驻** —— 实测它空转 47% CPU/141MB）
rem           ⇒ 需要那些"尚未搬到 Node 的旧功能"时，由以下任一方式启动：
rem              ① 双击 tools\start_python_upstream.bat
rem              ② 由 Node 门面在收到未实现请求时自动拉起（WMI，约 10 秒）
rem ─────────────────────────────────────────────────────────────
cd /d "%~dp0.."
if not exist logs mkdir logs
echo [%date% %time%] ==== 启动 MAAOrch 平台（Node 门面）==== >> logs\web_ui.log

rem 门面：Node（首页=完整前端+状态条，/status=运维面板，未实现路由透传 19997）
echo [%date% %time%] 启动 Node 门面(19999) ... >> logs\web_ui.log
start "" /B node server\maaorch-web.mjs --port 19999 --upstream http://127.0.0.1:19997 >> logs\web_ui.log 2>&1

echo [%date% %time%] 启动完成（Node 19999；Python 上游按需） >> logs\web_ui.log
exit /b 0
