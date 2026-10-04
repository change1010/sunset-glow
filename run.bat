@echo off
rem 晚霞预报推送 —— 任务计划调用入口
rem 用法：run.bat digest  |  run.bat alert  |  run.bat test
cd /d "%~dp0"
set "PY=D:\4.Study\Env\Python\pythonw.exe"
if not exist "%PY%" set "PY=pythonw.exe"
set "MODE=%~1"
if "%MODE%"=="" set "MODE=digest"
"%PY%" "%~dp0sunset_glow.py" --mode %MODE%
