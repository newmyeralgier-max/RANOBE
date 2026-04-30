@echo off
REM Double-click launcher for the Telegram bot on Windows.
REM Reads TG_BOT_TOKEN and COHERE_API_KEY from environment.

REM Switch console to UTF-8 so Cyrillic log messages render correctly.
chcp 65001 >nul

setlocal
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "SCRIPT_DIR=%~dp0"
set "EXEC_DIR=%SCRIPT_DIR%.."
cd /d "%EXEC_DIR%"

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
    pause
    exit /b 1
)

if "%TG_BOT_TOKEN%"=="" (
    echo.
    echo TG_BOT_TOKEN не задан.
    echo Получи токен у @BotFather в Telegram, потом запусти в PowerShell:
    echo.
    echo   $env:TG_BOT_TOKEN = "7123456789:AAG..."
    echo   $env:COHERE_API_KEY = "..."
    echo.
    echo и запусти этот .bat снова, или пропиши их в свойствах системы навсегда.
    pause
    exit /b 1
)
if "%COHERE_API_KEY%"=="" (
    echo.
    echo COHERE_API_KEY не задан — без него бот не сможет переводить.
    echo Поставь его так же как TG_BOT_TOKEN.
    pause
    exit /b 1
)

echo Запускаю Telegram-бот через: %PY%
%PY% -c "import sys; print('  ', sys.executable); print('  ', sys.version)"
echo.

%PY% -m novel_dl.bot

if errorlevel 1 (
    echo.
    echo Бот завершился с ошибкой. Нажми любую клавишу, чтобы закрыть окно.
    pause >nul
)
endlocal
