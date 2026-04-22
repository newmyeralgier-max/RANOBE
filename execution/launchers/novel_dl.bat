@echo off
REM Double-click launcher for the novel downloader GUI on Windows.
REM Requires Python 3.10+ (tkinter is included by default).

setlocal
set "SCRIPT_DIR=%~dp0"
set "EXEC_DIR=%SCRIPT_DIR%.."
cd /d "%EXEC_DIR%"

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -m novel_dl.gui
) else (
    python -m novel_dl.gui
)

if errorlevel 1 (
    echo.
    echo Скрипт завершился с ошибкой. Нажми любую клавишу, чтобы закрыть окно.
    pause >nul
)
endlocal
