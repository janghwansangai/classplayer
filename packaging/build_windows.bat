@echo off
REM 윈도우용 ClassPlayer 빌드 (아직 실기 검증 전)
REM 사전 준비:
REM   1) Python 3.12, uv (winget install astral-sh.uv), ffmpeg (winget install Gyan.FFmpeg), deno (winget install DenoLand.Deno)
REM   2) https://github.com/shinchiro/mpv-winbuild-cmake/releases 에서 mpv-dev-x86_64-*.7z 를 받아
REM      libmpv-2.dll 을 이 저장소의 bin\ 폴더에 둔다.
REM   3) uv sync
setlocal
cd /d "%~dp0\.."
if not exist bin\libmpv-2.dll (
  echo bin\libmpv-2.dll 이 없습니다. 위 안내대로 받아 두세요.
  exit /b 1
)
rmdir /s /q build dist 2>nul
uv run pyinstaller --noconfirm --clean packaging\classplayer.spec
if errorlevel 1 exit /b 1
echo == 자가 점검 ==
set CLASSPLAYER_SELFTEST=%cd%\tests\assets\test_h264_1080p.mp4
dist\ClassPlayer\ClassPlayer.exe
echo 완료: dist\ClassPlayer\ClassPlayer.exe  (폴더 전체를 압축해 배포)
endlocal
