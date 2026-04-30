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

REM Pick a Python interpreter. Try the py-launcher first (it points to the
REM official python.org install on most Windows machines), then fall back
REM to whatever `python` is on PATH (this handles MSYS2 / portable Python).
set "PY="
where py >nul 2>nul
if %errorlevel%==0 (
    set "PY=py -3"
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        set "PY=python"
    )
)
if "%PY%"=="" (
    echo.
    echo Не нашёл Python. Поставь Python 3.10+ с https://python.org
    echo (галочка "Add Python to PATH" при установке).
    pause
    exit /b 1
)

REM Print which Python is being used so the user can debug runtime issues
REM (e.g., HTTP timeouts that turn out to be a non-standard build).
echo Запускаю через: %PY%
%PY% -c "import sys; print('  ', sys.executable); print('  ', sys.version)"
echo.

%PY% -m novel_dl.gui

if errorlevel 1 (
    echo.
    echo Скрипт завершился с ошибкой. Нажми любую клавишу, чтобы закрыть окно.
    pause >nul
)
endlocal
