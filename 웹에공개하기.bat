@echo off
setlocal
cd /d "%~dp0"
title 웹에 공개하기

echo.
echo  ============================================================
echo    핸드폰에서도 보게 만들기 (GitHub Pages)
echo  ============================================================
echo.
echo   [먼저 할 일] 깃허브에서 '새 공개 저장소'를 만드세요.
echo     1. https://github.com/new 접속
echo     2. 이름 입력 (예: my-stock-dashboard)
echo     3. Public 선택   ^<-- 중요! Private 아님
echo     4. README 등 체크박스는 건드리지 말고 Create repository
echo.
echo   [왜 새로 만드나요?]
echo     지금 저장소의 과거 기록에 계좌 금액이 남아 있어서,
echo     그대로 공개하면 그것까지 보입니다.
echo     그래서 기록을 새로 시작해 깨끗한 상태로 올립니다.
echo.

set "URL="
set /p "URL=새 공개 저장소 주소 (끝에 .git): "
if not defined URL goto CANCEL

echo.
echo   올릴 내용을 확인하세요:
echo     - 분석 코드, 대시보드, 분석 결과(종목 점수/뉴스/토론)
echo     - 비밀키는 올라가지 않습니다 (config.json 제외됨)
echo     - 계좌 금액 기능은 앱에서 제거되었습니다
echo.
set "OK="
set /p "OK=이 내용을 인터넷에 공개합니다. 진행할까요? (yes 입력): "
if /i not "%OK%"=="yes" goto CANCEL

echo.
echo  [1/4] 깨끗한 기록으로 새로 시작하는 중...
git checkout --orphan __clean >nul 2>&1
if errorlevel 1 goto FAIL
git add -A
git -c user.name="jaehy" -c user.email="jaehyeonshin070522@gmail.com" commit -q -m "내 주식 비서 - 뉴스 분석 알림 + 웹 대시보드"
if errorlevel 1 goto FAIL

echo  [2/4] 기존 기록 정리 중...
git branch -D main >nul 2>&1
git branch -m main
git reflog expire --expire=now --all >nul 2>&1
git gc --prune=now --quiet >nul 2>&1

echo  [3/4] 공개 저장소에 연결하는 중...
git remote remove origin >nul 2>&1
git remote add origin %URL%
if errorlevel 1 goto FAIL

echo  [4/4] 올리는 중... (로그인 창이 뜨면 로그인하세요)
echo.
git push -u origin main --force
if errorlevel 1 goto PUSHFAIL

echo.
echo  ============================================================
echo   성공! 이제 마지막 3가지만 하면 끝입니다.
echo  ============================================================
echo.
echo   (1) 웹 주소 켜기
echo       저장소 - Settings - Pages
echo       Source: Deploy from a branch / Branch: main / 폴더: / (root)
echo       Save 누르고 1~2분 기다리면 주소가 나옵니다.
echo       주소 형태: https://내아이디.github.io/저장소이름/
echo.
echo   (2) 비밀키 3개 다시 넣기 (새 저장소라 다시 필요합니다)
echo       Settings - Secrets and variables - Actions
echo       TELEGRAM_TOKEN / TELEGRAM_CHAT_ID / ANTHROPIC_API_KEY
echo       값은 이 폴더의 config.json 에 있습니다.
echo.
echo   (3) 예전 비공개 저장소 정리
echo       예전 저장소는 계좌 금액 기록이 남아 있고
echo       알림도 중복으로 보내니 삭제하는 것을 권합니다.
echo       예전 저장소 - Settings - 맨 아래 Delete this repository
echo.
goto END

:PUSHFAIL
echo.
echo  [실패] 올리지 못했습니다. 확인해보세요:
echo    1. 저장소 주소가 맞는지 (끝에 .git)
echo    2. Public 으로 만들었는지
echo    3. 만들 때 README 등을 추가하지 않았는지
echo.
goto END

:FAIL
echo.
echo  [실패] 준비 중 문제가 생겼습니다. 위 메시지를 확인하세요.
echo.
goto END

:CANCEL
echo.
echo  취소했습니다. 아무것도 바뀌지 않았습니다.

:END
echo.
pause
