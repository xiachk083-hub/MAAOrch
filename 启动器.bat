@echo off
chcp 65001 >nul
where python >nul 2>nul || (
    echo 未检测到 Python，请先安装 Python 3.12+
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)
python -c "import PySide6" 2>nul || (
    echo 正在安装 PySide6...
    pip install PySide6
)
start "" pythonw "%~dp0main.pyw"
