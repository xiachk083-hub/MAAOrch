@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo ============================================
echo   AUTO-MAS 账号信息导出
echo ============================================
python tools\auto_mas_export.py
echo.
echo 表格已导出到 data\auto_mas_accounts.csv
echo 按任意键用 Excel 打开...
pause >nul
start "" "%~dp0..\data\auto_mas_accounts.csv"
