@echo off
setlocal
cd /d "%~dp0"
title 종목 토론시키기
set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"

if not exist "%PY%" (
  echo.
  echo  [오류] 파이썬을 찾을 수 없습니다.
  echo    찾는 경로: %PY%
  echo.
  pause
  exit /b
)

:ASK
cls
echo.
echo  ========================================
echo    불리 vs 베어 토론시키기
echo  ========================================
echo.
echo   [미국]  AAPL   애플            MSFT  마이크로소프트
echo           NVDA   엔비디아         GOOGL 알파벳
echo           AMZN   아마존          META  메타
echo           TSLA   테슬라          AVGO  브로드컴
echo           NFLX   넷플릭스         JPM   제이피모건
echo   [한국]  005930 삼성전자         000660 SK하이닉스
echo   [ETF]   SOXL   QQQ   SPY
echo.
echo   여러 개는 쉼표로 구분. 예: NVDA,TSLA
echo   그냥 엔터를 누르면 종료합니다.
echo.

set "TICKERS="
set /p "TICKERS=종목을 입력하세요: "

if not defined TICKERS goto END

echo.
echo  토론 진행 중입니다. 종목당 20초 정도 걸려요...
echo.
chcp 65001 >nul
"%PY%" -X utf8 news_alert.py --debate %TICKERS%
set "RC=%ERRORLEVEL%"
chcp 949 >nul
echo.

if not "%RC%"=="0" (
  echo  [오류] 실행에 실패했습니다. 위 메시지를 확인하세요.
  echo.
  pause
  goto END
)

echo  ----------------------------------------
echo   완료! 텔레그램으로도 결과가 갔습니다.
echo  ----------------------------------------
echo.
set "OPEN="
set /p "OPEN=대시보드를 열까요? (y/n): "
if /i "%OPEN%"=="y" start "" "%~dp0index.html"

echo.
set "AGAIN="
set /p "AGAIN=다른 종목도 토론할까요? (y/n): "
if /i "%AGAIN%"=="y" goto ASK

:END
echo.
echo  창을 닫아도 됩니다.
pause
