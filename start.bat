@echo off
setlocal
cd /d "%~dp0"
title 武汉内涝二三维孪生调度平台
echo ==============================================
echo    江汉流域防汛与武汉内涝二三维孪生平台
echo ==============================================
echo.

where python >nul 2>&1
if errorlevel 1 goto :nopython

python scripts\launch.py
echo.
echo 服务已停止，按任意键关闭窗口。
pause >nul
exit /b 0

:nopython
echo [错误] 未找到 Python 解释器。
echo        请先安装 Python 3.10 及以上版本，安装时勾选 "Add python.exe to PATH"。
echo        下载地址：https://www.python.org/downloads/
echo.
pause
exit /b 1
