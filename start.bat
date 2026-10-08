@echo off
setlocal
chcp 65001 >nul
title Teksting
cd /d "%~dp0"

rem Everything (Python, packages, models, your work) stays inside this folder.
set "HERE=%~dp0"
set "UV=%HERE%runtime\uv.exe"
set "UV_CACHE_DIR=%HERE%runtime\uv-cache"
set "UV_PYTHON_INSTALL_DIR=%HERE%runtime\python"
set "UV_PYTHON_PREFERENCE=only-managed"

if not exist "%UV%" (
  echo.
  echo  Gjor klar Teksting for forste gang. Det tar noen minutter.
  echo  Preparing Teksting for the first time. This takes a few minutes.
  echo.
  if not exist "%HERE%runtime" mkdir "%HERE%runtime"
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol='Tls12'; $d=Join-Path $env:HERE 'runtime'; $z=Join-Path $d 'uv.zip'; Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip' -OutFile $z; Expand-Archive -Force $z $d; Remove-Item $z"
  if not exist "%UV%" goto fail
)

rem Python lives in runtime\python. No virtual environment: its launcher (.venv\Scripts\python.exe)
rem is a freshly made, unsigned .exe that company antivirus can block ("Ingen tilgang").
call :findpy
if not defined PY (
  rem --no-bin / --no-registry: nothing outside this folder (no python.exe in .local\bin, no registry entry)
  "%UV%" python install 3.12 --no-bin --no-registry
  if errorlevel 1 goto fail
  call :findpy
)
if not defined PY goto fail

"%UV%" pip install --python "%PY%" --break-system-packages --quiet -r "%HERE%requirements.txt"
if errorlevel 1 goto fail

rem Desktop + Start menu shortcuts with icon (right-click the Start menu entry to pin it to the taskbar)
if not "%TEKSTING_NO_SHORTCUTS%"=="1" powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%lag-snarveier.ps1" >nul 2>&1

"%PY%" "%HERE%server.py" --app
if errorlevel 1 goto fail
goto end

:findpy
set "PY="
for /d %%D in ("%HERE%runtime\python\cpython-3.12*") do if exist "%%D\python.exe" set "PY=%%D\python.exe"
exit /b 0

:fail
echo.
echo  Noe gikk galt. Ta et skjermbilde av dette vinduet og send det til den som ga deg appen.
echo  Something went wrong. Take a screenshot of this window and send it to whoever gave you the app.
echo.
pause

:end
endlocal
