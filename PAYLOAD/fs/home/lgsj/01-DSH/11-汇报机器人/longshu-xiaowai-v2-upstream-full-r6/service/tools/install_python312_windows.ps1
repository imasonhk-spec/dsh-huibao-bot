[CmdletBinding()]
param()
$ErrorActionPreference = "Stop"
$winget = Get-Command winget -ErrorAction SilentlyContinue
if (-not $winget) {
    throw "winget was not found. Install Python 3.12 x64 manually, then rerun setup_full_windows.ps1."
}
Write-Host "Installing Python 3.12 x64 for the current user ..."
& winget install --id Python.Python.3.12 --exact --scope user --accept-package-agreements --accept-source-agreements
if ($LASTEXITCODE -ne 0) { throw "Python 3.12 installation failed" }
Write-Host "Python installation finished. Close this terminal, open a new CMD/PowerShell, run 'py -0p', then run setup_full_windows.ps1."
