@echo off
setlocal
cd /d "%~dp0"
title 시세 새로고침
set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"

if not exist "%PY%" (
  echo  [오류] 파이썬을 찾을 수 없습니다: %PY%
  pause
  exit /b
)

echo.
echo  최신 시세를 받아오는 중...
echo.
chcp 65001 >nul
"%PY%" -X utf8 news_alert.py --refresh
set "RC=%ERRORLEVEL%"
chcp 949 >nul

if not "%RC%"=="0" (
  echo.
  echo  [오류] 시세 갱신에 실패했습니다. 위 메시지를 확인하세요.
  echo.
  pause
  exit /b
)

start "" "%~dp0index.html"
