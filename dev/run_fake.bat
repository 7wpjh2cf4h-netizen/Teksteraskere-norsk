@echo off
rem Fake mode: no models, instant fake transcripts, separate data folder dev\fakedata.
rem Runs on port 8766 so it never collides with the real app on 8765.
setlocal
cd /d "%~dp0.."
set "TEKSTING_FAKE=1"
set "TEKSTING_DATA=%~dp0fakedata"
set "TEKSTING_PORT=8766"
".venv\Scripts\python.exe" server.py
endlocal
