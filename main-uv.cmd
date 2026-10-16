@echo off
setlocal
cd /d "%~dp0"

set "OPENBACHELOR_C=%~dp0..\OpenBachelorC"
if not exist "%OPENBACHELOR_C%\main-uv.cmd" (
    echo OpenBachelorC launcher was not found:
    echo   %OPENBACHELOR_C%\main-uv.cmd
    pause
    exit /b 1
)

start "OpenBachelorS" /D "%~dp0" cmd /k call start-uv.cmd
start "OpenBachelorC --attach_pc" /D "%OPENBACHELOR_C%" cmd /k call main-uv.cmd --attach_pc

echo Started OpenBachelorS and OpenBachelorC --attach_pc.
endlocal
