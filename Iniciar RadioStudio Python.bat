@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv
    if errorlevel 1 goto :error
)

".venv\Scripts\python.exe" -c "import PySide6, imageio_ffmpeg, keyring, tinytag, pypresence" >nul 2>&1
if errorlevel 1 (
    echo Instalando dependencias do RadioStudio Python...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto :error
)

".venv\Scripts\python.exe" -m radiostudio
if errorlevel 1 goto :error
exit /b 0

:error
echo.
echo Nao foi possivel iniciar o RadioStudio Python.
pause
exit /b 1
