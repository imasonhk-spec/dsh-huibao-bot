[CmdletBinding()]
param(
    [switch]$BuildInstaller
)

$ErrorActionPreference = "Stop"
$clientRoot = $PSScriptRoot
$serviceRoot = (Resolve-Path (Join-Path $clientRoot "..")).Path
$venvPython = Join-Path $serviceRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "service\.venv 不存在。先运行: powershell -ExecutionPolicy Bypass -File service\tools\setup_full_windows.ps1"
}

# Full author build: keep the face stack.  If setup_full_windows.ps1 was used,
# local FunASR packages are already in the venv and will be staged too.
$build = Join-Path $clientRoot "Build-Client.ps1"
if ($BuildInstaller) {
    & $build -IncludeFaceStack -BuildInstaller
} else {
    & $build -IncludeFaceStack
}
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
