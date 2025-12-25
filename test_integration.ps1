Write-Host "========================================"
Write-Host "  SearchTool Rust - Test Suite"
Write-Host "========================================"
Write-Host ""

$testDir = "c:\Users\Administrator\Desktop\rust_engine\scanner"
$binPath = "$testDir\target\debug\filesearch.exe"

# Test 1: Check compilation
Write-Host "[1] Checking compilation..." -ForegroundColor Yellow
cd $testDir
$compileResult = cargo check 2>&1 | Where-Object {$_ -match "Finished|error"}
if ($compileResult -match "Finished") {
    Write-Host "OK - Compilation successful" -ForegroundColor Green
} else {
    Write-Host "FAIL - Compilation failed" -ForegroundColor Red
    exit 1
}

# Test 2: Check library
Write-Host "[2] Checking library..." -ForegroundColor Yellow
$libResult = cargo check --lib 2>&1 | Where-Object {$_ -match "Finished|error"}
if ($libResult -match "Finished") {
    Write-Host "OK - Library compiled" -ForegroundColor Green
} else {
    Write-Host "FAIL - Library failed" -ForegroundColor Red
}

# Test 3: Build binary
Write-Host "[3] Building binary..." -ForegroundColor Yellow
$binResult = cargo build --bin filesearch 2>&1 | Where-Object {$_ -match "Finished|error"}
if ($binResult -match "Finished") {
    Write-Host "OK - Binary built" -ForegroundColor Green
    if (Test-Path $binPath) {
        $size = (Get-Item $binPath).Length / 1MB
        Write-Host "    Size: $([Math]::Round($size, 2)) MB" -ForegroundColor Gray
    }
} else {
    Write-Host "FAIL - Binary build failed" -ForegroundColor Red
}

# Test 4: Check modules
Write-Host "[4] Checking modules..." -ForegroundColor Yellow
$modules = @("commands.rs", "index_engine.rs", "hotkey.rs", "tray.rs", "config.rs")
foreach ($mod in $modules) {
    $path = "$testDir\src\$mod"
    if (Test-Path $path) {
        Write-Host "    OK - $mod" -ForegroundColor Green
    } else {
        Write-Host "    MISSING - $mod" -ForegroundColor Red
    }
}

# Test 5: Code statistics
Write-Host "[5] Code statistics..." -ForegroundColor Yellow
$srcFiles = Get-ChildItem "$testDir\src\*.rs" -File
$lineCount = 0
foreach ($file in $srcFiles) {
    if ($file.Name -ne "lib.rs") {
        $lines = (Get-Content $file | Measure-Object -Line).Lines
        $lineCount += $lines
        Write-Host "    $($file.Name): $lines lines" -ForegroundColor Gray
    }
}
Write-Host "    Total: $lineCount lines (excluding lib.rs)" -ForegroundColor Cyan

Write-Host ""
Write-Host "========================================"
Write-Host "  All tests complete!"
Write-Host "========================================"
Write-Host ""
Write-Host "Next steps:"
Write-Host "1. Run: cargo build --release"
Write-Host "2. Run: cargo tauri dev"
Write-Host "3. Press Alt+Space to open search"
Write-Host "4. Type keywords to search"
Write-Host ""
