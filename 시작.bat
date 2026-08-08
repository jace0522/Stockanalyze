@echo off
setlocal
cd /d "%~dp0"
title 토스뉴스알리미
set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"

if not exist "%PY%" (
  echo  [오류] 파이썬을 찾을 수 없습니다: %PY%
  pause
  exit /b
)

echo.
echo  실시간 감시를 시작합니다. 이 창을 열어두세요.
echo  (끄려면 이 창을 닫으면 됩니다)
echo.
chcp 65001 >nul
"%PY%" -X utf8 news_alert.py
chcp 949 >nul
echo.
pause
