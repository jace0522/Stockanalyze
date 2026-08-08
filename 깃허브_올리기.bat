@echo off
chcp 65001 >nul
title 깃허브에 올리기
cd /d "%~dp0"

echo.
echo ========================================
echo   토스뉴스알리미 - 깃허브에 올리기
echo ========================================
echo.
echo  [먼저 확인] 깃허브에서 저장소를 만들었나요?
echo    안 만들었다면 https://github.com/new 에서
echo    이름 아무거나 + Private 선택 + Create repository
echo.
echo  만든 저장소 주소를 붙여넣으세요.
echo    예시: https://github.com/jaehy/toss-news-alert.git
echo    (주소창 주소 끝에 .git 을 붙이면 됩니다)
echo.

set "REPOURL="
set /p "REPOURL=저장소 주소: "

if "%REPOURL%"=="" (
  echo.
  echo  주소를 입력하지 않았습니다. 창을 닫고 다시 실행해주세요.
  pause
  exit /b
)

echo.
echo  연결 중...
git remote remove origin >nul 2>&1
git remote add origin %REPOURL%
if errorlevel 1 (
  echo  [실패] 주소 형식이 올바르지 않은 것 같습니다.
  pause
  exit /b
)

echo  올리는 중... (로그인 창이 뜨면 깃허브 계정으로 로그인하세요)
echo.
git push -u origin main

if errorlevel 1 (
  echo.
  echo  ========================================
  echo   [실패] 아래를 확인해보세요
  echo  ========================================
  echo   1. 저장소 주소가 맞는지 (끝에 .git)
  echo   2. 깃허브 로그인을 완료했는지
  echo   3. 저장소를 만들 때 README 등을 추가하지 않았는지
  echo      (추가했다면 저장소를 지우고 빈 상태로 다시 만드세요)
  echo.
) else (
  echo.
  echo  ========================================
  echo   [성공] 코드가 깃허브에 올라갔습니다!
  echo  ========================================
  echo.
  echo   다음 단계: 비밀키 3개 등록
  echo   저장소 페이지 - Settings - Secrets and variables - Actions
  echo   자세한 방법은 클라우드_설정.md 파일을 보세요.
  echo.
)
pause
