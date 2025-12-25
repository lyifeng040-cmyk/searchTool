@echo off
REM SearchTool Rust - Quick Start Script

echo.
echo ========================================
echo  SearchTool Rust - Quick Start
echo ========================================
echo.

set PROJECT_DIR=c:\Users\Administrator\Desktop\rust_engine\scanner

cd /d %PROJECT_DIR%

echo [1] Checking Rust installation...
rustc --version
cargo --version
echo.

echo [2] Running tests...
powershell -ExecutionPolicy Bypass -File test_integration.ps1
echo.

echo [3] Starting development server...
echo    Press Ctrl+C to stop
echo    Alt+Space to open search window
echo.

cargo tauri dev
