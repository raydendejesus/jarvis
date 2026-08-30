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

ollama list | findstr /i "^jarvis" >nul
if errorlevel 1 (
    echo First-time setup: pulling qwen2.5:14b and building the jarvis persona...
    echo This is a ~9GB download and may take a while.
    ollama pull qwen2.5:14b
    ollama create jarvis -f backend\Modelfile
)

ollama list | findstr /i "qwen2.5vl" >nul
if errorlevel 1 (
    echo First-time setup: pulling the qwen2.5vl:7b vision model (~6GB)...
    ollama pull qwen2.5vl:7b
)

set STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
if not exist "%STARTUP_DIR%\Jarvis.lnk" (
    echo Registering Jarvis to start automatically at login...
    powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%STARTUP_DIR%\Jarvis.lnk'); $s.TargetPath='%~dp0venv\Scripts\pythonw.exe'; $s.Arguments='\"%~dp0JARVIS_TRAY.pyw\"'; $s.WorkingDirectory='%~dp0'; $s.Save()"
)

echo Starting Jarvis...
start "" venv\Scripts\pythonw.exe JARVIS_TRAY.pyw

endlocal