@echo off
REM SearchTool Rust - Release Build Script

echo.
echo ========================================
echo  SearchTool Rust - Release Build
echo ========================================
echo.

set PROJECT_DIR=c:\Users\Administrator\Desktop\rust_engine\scanner
set OUTPUT_DIR=%PROJECT_DIR%\target\release

cd /d %PROJECT_DIR%

echo [1] Cleaning previous build...
cargo clean
echo.

echo [2] Building release version...
echo    This may take 2-5 minutes...
cargo build --release --bin filesearch

if errorlevel 1 (
    echo.
    echo ERROR: Build failed!
    pause
    exit /b 1
)

echo.
echo [3] Build complete!
echo.

if exist %OUTPUT_DIR%\filesearch.exe (
    for /F "tokens=*" %%i in ('powershell -Command "(Get-Item ''%OUTPUT_DIR%\filesearch.exe'').Length / 1MB"') do set SIZE=%%i
    echo Output: %OUTPUT_DIR%\filesearch.exe
    echo Size: %SIZE% MB
    echo.
    echo You can now:
    echo 1. Double-click filesearch.exe to run
    echo 2. Create a shortcut on desktop
    echo 3. Pin to Start menu
    echo.
) else (
    echo ERROR: filesearch.exe not found!
)

echo Building Tauri MSI installer...
cargo tauri build
if errorlevel 1 (
    echo.
    echo Note: Tauri installer requires Node.js and additional setup
    echo Executable version works fine for standalone use
)

pause
