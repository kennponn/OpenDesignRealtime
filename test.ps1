$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    # Separate processes avoid intermittent Tcl interpreter cleanup failures.
    $modules = @(
        'test_browser_auth.py', 'test_client.py', 'test_desktop_view.py',
        'test_key_actions.py', 'test_key_inventory.py',
        'test_key_management.py', 'test_session_service.py'
    )
    foreach ($module in $modules) {
        python -m pytest $module -q
        if ($LASTEXITCODE -ne 0) { throw "Tests failed: $module" }
    }
} finally {
    Pop-Location
}
