$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    python -m PyInstaller --noconfirm OpenDesignCards.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
    Write-Output 'Built: dist\OpenDesignCards.exe'
} finally {
    Pop-Location
}
