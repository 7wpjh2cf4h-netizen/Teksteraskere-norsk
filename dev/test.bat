@echo off
rem Runs the test suite with the app's own Python (pytest and httpx are fetched by uv, not installed into the app).
cd /d "%~dp0.."
for /d %%P in (runtime\python\cpython-3.12*) do set "PY=%%P\python.exe"
set "UV_CACHE_DIR=runtime\uv-cache"
runtime\uv.exe run -q --python "%PY%" --no-project --with-requirements requirements-dev.txt python -m pytest %*
