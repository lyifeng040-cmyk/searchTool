# Run lightweight project tests on Windows (PowerShell)
# Usage: Open PowerShell at repo root and run: .\tools\run_tests.ps1

$ErrorActionPreference = 'Stop'
Write-Host "Running lightweight test scripts..."

$tests = @(
    'filesearch\tests\run_column_tests.py',
    'filesearch\tests\run_search_controller_tests.py',
    'filesearch\tests\run_result_renderer_tests.py',
    'filesearch\tests\run_event_handlers_tests.py',
    'filesearch\tests\run_highlight_tests.py',
    'filesearch\tests\run_stat_utils_tests.py'
)

$allok = $true
foreach ($t in $tests) {
    Write-Host "--- Running $t ---"
    python -u $t
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Test failed: $t (exit code $LASTEXITCODE)" -ForegroundColor Red
        $allok = $false
    } else {
        Write-Host "Passed: $t" -ForegroundColor Green
    }
}

if ($allok) {
    Write-Host "All lightweight tests passed." -ForegroundColor Green
    exit 0
} else {
    Write-Host "Some tests failed." -ForegroundColor Red
    exit 1
}
