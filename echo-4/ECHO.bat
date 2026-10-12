@echo off
setlocal
cd /d "%~dp0"

if not exist venv (
    echo Creating virtual environment...
    py -3.12 -m venv venv
)

call venv\Scripts\activate.bat

echo Checking dependencies...
pip install -q -r backend\requirements.txt

echo Checking Ollama...
ollama list >nul 2>&1
if errorlevel 1 (
    echo Starting Ollama service...
    start "Ollama" /min ollama serve
    timeout /t 3 /nobreak >nul
)

ollama list | findstr /i "echo" >nul
if errorlevel 1 (
    echo First-time setup: pulling qwen2.5:14b and building the echo persona...
    echo This is a ~9GB download and may take a while.
    ollama pull qwen2.5:14b
    ollama create echo -f backend\Modelfile
)

ollama list | findstr /i "qwen2.5vl" >nul
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