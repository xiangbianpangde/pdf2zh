@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title pdf2zh
cd /d "%~dp0.."

echo ==========================================
echo   pdf2zh - PDF to Chinese Markdown
echo ==========================================
echo.

REM === 1. Python detection ===
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found in PATH.
    echo.
    echo Install Python 3.10, 3.11, or 3.12 from:
    echo   https://www.python.org/downloads/
    echo During install, check "Add Python to PATH".
    echo Then re-run this script.
    echo.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set "PYVER=%%v"
for /f "tokens=1,2 delims=." %%a in ("!PYVER!") do (
    set "PYMAJ=%%a"
    set "PYMIN=%%b"
)
echo Python !PYVER! detected.

if not "!PYMAJ!"=="3" goto :pyverbad
if !PYMIN! lss 10 goto :pyverbad
if !PYMIN! geq 13 goto :pyverbad
goto :pyverok
:pyverbad
echo [ERROR] Python !PYVER! is not supported.
echo Required: 3.10, 3.11, or 3.12.
echo (mineru's ray dependency does not support 3.13+ on Windows.)
echo.
pause
exit /b 1
:pyverok

REM === 2. Create venv if missing ===
if not exist ".venv\Scripts\python.exe" (
    echo.
    echo Creating virtual environment in .venv\ ...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create venv.
        pause
        exit /b 1
    )
)

REM === 3. Activate venv ===
call ".venv\Scripts\activate.bat"

REM === 4. Install dependencies on first run ===
if exist ".venv\.installed" goto :pipdone

echo.
echo Installing dependencies. First run takes 5-10 minutes ^(about 7 GB^).
echo Subsequent runs will skip this step.
echo.
python -m pip install --upgrade pip
if errorlevel 1 goto :pipfail
python -m pip install -e ".[ui,local]"
if errorlevel 1 goto :pipfail
echo installed > ".venv\.installed"
goto :pipdone

:pipfail
echo.
echo [ERROR] pip install failed. See messages above for the failing package.
echo Possible fixes:
echo   - Check internet connection / proxy
echo   - Delete .venv\ and re-run this script
echo.
pause
exit /b 1

:pipdone

REM === 5. GPU check + optional CUDA torch ===
where nvidia-smi >nul 2>&1
if errorlevel 1 goto :gpudone
python -c "import torch, sys; sys.exit(0 if torch.cuda.is_available() else 1)" 2>nul
if not errorlevel 1 goto :gpudone

echo.
echo NVIDIA GPU detected, but PyTorch is CPU-only.
echo Installing CUDA 12.8 PyTorch will use about 3.5 GB extra and gives 5-10x speedup.
set "INSTALL_CUDA="
set /p "INSTALL_CUDA=Install CUDA PyTorch now? [Y/N] (default N): "
if /i not "!INSTALL_CUDA!"=="Y" goto :gpudone
python -m pip install --force-reinstall torch torchvision --index-url https://download.pytorch.org/whl/cu128
if errorlevel 1 echo [WARN] CUDA torch install failed; continuing with CPU torch.
:gpudone

REM === 6. Load .env if present ===
if exist ".env" (
    for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
        set "_K=%%a"
        if not "!_K!"=="" if not "!_K:~0,1!"=="#" set "%%a=%%b"
    )
)

REM === 7. Prompt for DEEPSEEK_API_KEY if missing ===
if not "!DEEPSEEK_API_KEY!"=="" goto :keydone
if exist ".env" goto :keydone

echo.
echo DEEPSEEK_API_KEY is not set.
echo Get one at https://platform.deepseek.com (recommended for translation quality).
echo Or press Enter to skip if you plan to use only Ollama / LM Studio.
set "APIKEY="
set /p "APIKEY=Paste your DeepSeek API key (sk-...): "
if not defined APIKEY goto :keydone
> ".env" echo DEEPSEEK_API_KEY=!APIKEY!
set "DEEPSEEK_API_KEY=!APIKEY!"
echo Saved to .env (this file is gitignored).
:keydone

REM === 8. Launch ===
echo.
echo Starting Gradio UI at http://127.0.0.1:7860 ...
echo Close this window to stop the service.
echo.
set PYTHONIOENCODING=utf-8
python -m pdf2zh.webui

if errorlevel 1 (
    echo.
    echo app.py exited with an error. See messages above.
    pause
)

endlocal
