@echo off
REM Double-click launcher for the novel downloader GUI on Windows.
REM Requires Python 3.10+ (tkinter is included by default).

REM Switch console to UTF-8 so Cyrillic messages render correctly.
chcp 65001 >nul

setlocal
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "SCRIPT_DIR=%~dp0"
set "EXEC_DIR=%SCRIPT_DIR%.."
cd /d "%EXEC_DIR%"

echo Запускаю старый оконный интерфейс Ranobe Downloader...
python -m novel_dl.gui

if errorlevel 1 (
    echo.
    echo Скрипт завершился с ошибкой. Нажми любую клавишу, чтобы закрыть окно.
    pause >nul
)
endlocal
