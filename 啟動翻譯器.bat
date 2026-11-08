@echo off
rem 梅露可物語 離線版 故事翻譯器 啟動腳本（Windows：雙擊執行）
chcp 65001 >nul
cd /d "%~dp0"
set "VENV=%USERPROFILE%\.venvs\merc-story"
if exist "%VENV%\Scripts\python.exe" goto run
echo 第一次執行：建立 Python 環境（需要 Python 3.10 以上）…
set "PY="
for %%c in (py python python3) do (
  if not defined PY (
    %%c -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1 && set "PY=%%c"
  )
)
if not defined PY (
  echo 找不到 Python 3.10 以上版本，請先從 https://www.python.org/ 安裝（安裝時勾選 Add python.exe to PATH）。
  pause
  exit /b 1
)
%PY% -m venv "%VENV%" || goto fail
"%VENV%\Scripts\python.exe" -m pip install -q --upgrade pip || goto fail
"%VENV%\Scripts\python.exe" -m pip install -q -r requirements.txt || goto fail
:run
"%VENV%\Scripts\python.exe" translator\server.py %*
pause
exit /b 0
:fail
echo 安裝套件失敗，請確認網路連線後再試一次。
pause
exit /b 1
