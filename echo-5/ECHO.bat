@echo off
setlocal
cd /d "%~dp0"

if not exist venv (
    echo Creating virtual environment...
    py -3.12 -m venv venv
)

call venv\Scripts\activate.bat

echo Checking dependencies...
for %%F in (backend\requirements.txt) do set REQ_STAMP=%%~zF-%%~tF
set OLD_STAMP=
if exist venv\.requirements_stamp set /p OLD_STAMP=<venv\.requirements_stamp
if "%REQ_STAMP%"=="%OLD_STAMP%" goto deps_ok
pip install -q -r backend\requirements.txt
>venv\.requirements_stamp echo %REQ_STAMP%
:deps_ok

echo Checking Ollama...
set MODELS_FILE=%TEMP%\echo_ollama_models.txt
ollama list >"%MODELS_FILE%" 2>nul
if errorlevel 1 (
    echo Starting Ollama service...
    start "Ollama" /min ollama serve
    timeout /t 3 /nobreak >nul
    ollama list >"%MODELS_FILE%" 2>nul
)

findstr /i "echo" "%MODELS_FILE%" >nul
if errorlevel 1 (
    echo First-time setup: pulling qwen2.5:14b and building the echo persona...
    echo This is a ~9GB download and may take a while.
    ollama pull qwen2.5:14b
    ollama create echo -f backend\Modelfile
)

findstr /i "qwen2.5vl" "%MODELS_FILE%" >nul
if errorlevel 1 (
    echo First-time setup: pulling the qwen2.5vl:7b vision model, about 6GB...
    ollama pull qwen2.5vl:7b
)

set STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
if exist "%STARTUP_DIR%\Echo.lnk" goto skip_autostart
echo Registering Echo to start automatically at login...
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%STARTUP_DIR%\Echo.lnk'); $s.TargetPath='%~dp0venv\Scripts\pythonw.exe'; $s.Arguments='\"%~dp0ECHO_TRAY.pyw\"'; $s.WorkingDirectory='%~dp0'; $s.Save()"
:skip_autostart

echo Starting Echo...
start "" venv\Scripts\pythonw.exe ECHO_TRAY.pyw

endlocal