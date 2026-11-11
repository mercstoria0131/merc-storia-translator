@echo off
rem Merc Storia offline story translator - Windows launcher (double-click to run).
rem Keep this file ASCII-only: cmd mangles multi-byte lines after goto/call.
rem User-facing Chinese messages live in tools\launcher_messages.txt.
rem Prefers conda (Miniconda/Miniforge): creates an env named merc-storia; falls back to a Python venv.
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "MSGS=tools\launcher_messages.txt"
set "ENVNAME=merc-storia"
set "VENV=%USERPROFILE%\.venvs\merc-storia"
set "PYVER=3.12"
set "OK310=import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)"
rem On Windows on ARM we need x64 Python (emulated): FMOD, used to decode BGM/SE, has no ARM64 build.
set "OKX64=import sys, sysconfig; sys.exit(0 if sys.version_info>=(3,10) and sysconfig.get_platform()=='win-amd64' else 1)"
set "ARM="
if /i "%PROCESSOR_ARCHITECTURE%"=="ARM64" set "ARM=1"
if /i "%PROCESSOR_ARCHITEW6432%"=="ARM64" set "ARM=1"
call :find_conda

rem ---- 1. conda env already set up ----
if not defined CONDA goto try_venv
call :conda_env_python
if not defined CPY goto try_venv
call :usable "%CPY%" || goto try_venv
if not exist "%CPYDIR%\.merc-installed" goto try_venv
set "VPY=%CPY%"
call :conda_path
goto run

rem ---- 2. venv already set up ----
:try_venv
if not exist "%VENV%\Scripts\python.exe" goto new_env
call :usable "%VENV%\Scripts\python.exe" || goto new_env
if not exist "%VENV%\.merc-installed" goto new_env
set "VPY=%VENV%\Scripts\python.exe"
goto run

rem ---- 3. create an environment: conda if available, else venv ----
:new_env
if defined CONDA goto conda_setup
goto venv_setup

:conda_setup
call :msg CONDA_SETUP
call :msg CONDA_FOUND
echo(  %CONDA%
call :conda_env_python
if defined CPY call :usable "%CPY%" || set "CPY="
if defined CPY goto conda_deps
if exist "%CPYDIR%" call "%CONDA%" env remove -y -q -n %ENVNAME% >nul 2>&1
if defined ARM set "CONDA_SUBDIR=win-64"
call "%CONDA%" create -y -q -n %ENVNAME% --override-channels -c conda-forge python=%PYVER% pip
if errorlevel 1 goto fail
set "CONDA_SUBDIR="
call :conda_env_python
if not defined CPY goto fail
rem On ARM, pin this env to x64 packages for later conda installs too.
if defined ARM >"%CPYDIR%\.condarc" echo subdir: win-64
:conda_deps
set "VPY=%CPY%"
call :conda_path
goto deps

:venv_setup
call :msg VENV_SETUP
set "PY="
if defined ARM call :find_x64
if not defined PY call :find_any
if defined PY goto venv_create
call :msg NO_PY1
call :msg NO_PY2
call :msg NO_PY3
pause
exit /b 1
:venv_create
if not exist "%VENV%\Scripts\python.exe" goto venv_make
call :usable "%VENV%\Scripts\python.exe" && goto venv_ready
if defined ARM rmdir /s /q "%VENV%"
:venv_make
if not exist "%VENV%\Scripts\python.exe" %PY% -m venv "%VENV%"
if errorlevel 1 goto fail
:venv_ready
set "VPY=%VENV%\Scripts\python.exe"
if not defined ARM goto deps
call :usable "%VPY%" && goto deps
call :msg ARM_WARN

rem ---- install packages ----
:deps
call :msg DEPS
for %%p in ("%VPY%") do set "VDIR=%%~dpp"
rem venv keeps python.exe under Scripts\; the marker goes in the env root
if /i "%VDIR:~-9%"=="\Scripts\" for %%p in ("%VDIR%..") do set "VDIR=%%~fp\"
"%VPY%" -m pip install -q --upgrade pip
if errorlevel 1 goto fail
"%VPY%" -c "import sysconfig, sys; sys.exit(0 if sysconfig.get_platform() == 'win-arm64' else 1)"
if errorlevel 1 goto deps_std
rem ARM64 Python: etcpak has no ARM64 wheel (and is unused here), so skip it.
"%VPY%" -m pip install -q -r requirements-win-arm64.txt
if errorlevel 1 goto fail
"%VPY%" -m pip install -q --no-deps --no-warn-conflicts "UnityPy>=1.25"
if errorlevel 1 goto fail
goto deps_ok
:deps_std
"%VPY%" -m pip install -q -r requirements.txt
if errorlevel 1 goto fail
:deps_ok
type nul > "%VDIR%.merc-installed"

:run
call :msg USING
"%VPY%" -c "import sys, sysconfig; print('  ' + sys.executable + '  (Python %%d.%%d, %%s)' %% (sys.version_info[0], sys.version_info[1], sysconfig.get_platform()))"
"%VPY%" translator\server.py %*
pause
exit /b 0
:fail
call :msg FAIL
pause
exit /b 1

rem ======== subroutines ========

rem Print a message from tools\launcher_messages.txt by key.
:msg
for /f "usebackq tokens=1,* delims==" %%a in (`findstr /b /c:"%~1=" "%MSGS%"`) do echo(%%b
exit /b 0

rem Is this Python usable? (3.10+; must be x64 on Windows on ARM)
:usable
if not exist "%~1" exit /b 1
if defined ARM goto usable_arm
"%~1" -c "%OK310%" >nul 2>&1
exit /b %errorlevel%
:usable_arm
"%~1" -c "%OKX64%" >nul 2>&1
exit /b %errorlevel%

rem Find conda without needing Anaconda Prompt: an activated conda (CONDA_EXE), conda on PATH,
rem the install location Miniconda/Anaconda records in the registry, then common install folders.
:find_conda
set "CONDA="
if defined CONDA_EXE if exist "%CONDA_EXE%" set "CONDA=%CONDA_EXE%"
for %%c in (conda.exe conda.bat) do (
  if not defined CONDA if not "%%~$PATH:c"=="" set "CONDA=%%~$PATH:c"
)
for %%k in (HKCU HKLM) do (
  for /f "tokens=2,*" %%a in ('reg query "%%k\Software\Python\ContinuumAnalytics" /s /ve 2^>nul ^| findstr /c:"REG_SZ"') do call :conda_root "%%b"
)
for /d %%d in ("%USERPROFILE%\*conda*" "%USERPROFILE%\*forge*" "%LOCALAPPDATA%\*conda*" "%LOCALAPPDATA%\*forge*" "%ProgramData%\*conda*" "%ProgramData%\*forge*" "%SystemDrive%\*conda*" "%SystemDrive%\*forge*") do call :conda_root "%%~d"
exit /b 0

rem Use <folder> as the conda install if it is one (Scripts\conda.exe, or condabin\conda.bat in newer installs).
:conda_root
if defined CONDA exit /b 0
if exist "%~1\Scripts\conda.exe" set "CONDA=%~1\Scripts\conda.exe"
if defined CONDA exit /b 0
if exist "%~1\condabin\conda.bat" set "CONDA=%~1\condabin\conda.bat"
exit /b 0

rem python.exe of the merc-storia env (under <conda root>\envs\ or %USERPROFILE%\.conda\envs\).
:conda_env_python
set "CPY="
for %%p in ("%CONDA%") do for %%q in ("%%~dpp..") do set "CBASE=%%~fq"
set "CPYDIR=%CBASE%\envs\%ENVNAME%"
if exist "%CPYDIR%\python.exe" set "CPY=%CPYDIR%\python.exe"
if defined CPY exit /b 0
if not exist "%USERPROFILE%\.conda\envs\%ENVNAME%\python.exe" exit /b 0
set "CPYDIR=%USERPROFILE%\.conda\envs\%ENVNAME%"
set "CPY=%CPYDIR%\python.exe"
exit /b 0

rem Put the conda env on PATH (what conda activate does; otherwise DLLs such as SSL may not be found).
:conda_path
set "PATH=%CPYDIR%;%CPYDIR%\Library\mingw-w64\bin;%CPYDIR%\Library\usr\bin;%CPYDIR%\Library\bin;%CPYDIR%\Scripts;%CPYDIR%\bin;%PATH%"
exit /b 0

rem Find an x64 Python (python.org default locations, py launcher, PATH).
:find_x64
set "PY="
for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python3*" "%ProgramFiles%\Python3*") do (
  if not defined PY if exist "%%~d\python.exe" "%%~d\python.exe" -c "%OKX64%" >nul 2>&1 && set PY="%%~d\python.exe"
)
for %%v in (3.13 3.12 3.14 3.11 3.10) do (
  if not defined PY py -V:%%v -c "%OKX64%" >nul 2>&1 && set "PY=py -V:%%v"
)
for %%c in (python python3) do (
  if not defined PY %%c -c "%OKX64%" >nul 2>&1 && set "PY=%%c"
)
exit /b 0

rem Find any Python 3.10+.
:find_any
set "PY="
for %%c in (py python python3) do (
  if not defined PY %%c -c "%OK310%" >nul 2>&1 && set "PY=%%c"
)
exit /b 0
