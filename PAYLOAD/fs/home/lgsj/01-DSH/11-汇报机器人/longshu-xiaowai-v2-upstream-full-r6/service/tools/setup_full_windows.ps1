[CmdletBinding()]
param(
    [string]$Python = "",
    [switch]$SkipLiveKit,
    [switch]$SkipModels
)

$ErrorActionPreference = "Stop"
$serviceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $serviceRoot

function Test-PythonCandidate([string]$Exe) {
    if (-not $Exe) { return $false }
    if (-not (Test-Path -LiteralPath $Exe)) { return $false }
    $savedPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "SilentlyContinue"
        $probe = & $Exe -c "import sys; ok=((3,11) <= sys.version_info[:2] < (3,13)) and (sys.maxsize > 2**32); print('1' if ok else '0')" 2>$null
        return ($LASTEXITCODE -eq 0 -and $probe -and $probe.Trim() -eq "1")
    } finally { $ErrorActionPreference = $savedPreference }
}

function Resolve-PythonExe {
    if ($Python) {
        if (-not (Test-PythonCandidate $Python)) {
            throw "Python must be CPython 3.11/3.12 x64. Invalid interpreter: $Python"
        }
        return (Resolve-Path $Python).Path
    }

    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        foreach ($ver in @("-3.12-64", "-3.12", "-3.11-64", "-3.11")) {
            $savedPreference = $ErrorActionPreference
            try {
                $ErrorActionPreference = "SilentlyContinue"
                $candidate = & py $ver -c "import sys; print(sys.executable)" 2>$null
                $probeExit = $LASTEXITCODE
            } finally { $ErrorActionPreference = $savedPreference }
            if ($probeExit -eq 0 -and $candidate) {
                $exe = $candidate.Trim()
                if (Test-PythonCandidate $exe) { return $exe }
            }
        }
    }

    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "C:\Python312\python.exe",
        "C:\Python311\python.exe"
    )
    foreach ($exe in $candidates) {
        if (Test-PythonCandidate $exe) { return $exe }
    }

    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and (Test-PythonCandidate $cmd.Source)) { return $cmd.Source }

    throw "Python 3.11/3.12 x64 is required. Install Python 3.12 x64 first (for example: winget install --id Python.Python.3.12 --exact --scope user), then reopen CMD/PowerShell and run this script again."
}

$basePython = Resolve-PythonExe
Write-Host "Python: $basePython"
& $basePython -c "import sys,platform; print('Version:', sys.version.split()[0]); print('Architecture:', platform.architecture()[0])"
if ($LASTEXITCODE -ne 0) { throw "Python probe failed" }

$venv = Join-Path $serviceRoot ".venv"
$venvPython = Join-Path $venv "Scripts\python.exe"
if (Test-Path -LiteralPath $venvPython) {
    if (-not (Test-PythonCandidate $venvPython)) {
        Write-Host "Backing up incompatible existing venv: $venv"
        $backup = "$venv.incompatible-$(Get-Date -Format yyyyMMddHHmmss)"
        Move-Item -LiteralPath $venv -Destination $backup
        Write-Host "Previous environment preserved: $backup"
    }
}
if (-not (Test-Path -LiteralPath $venvPython)) {
    & $basePython -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "Failed to create venv" }
}

# Use Tsinghua PyPI mirror inside this project venv.
& $venvPython -m pip install --upgrade "pip==25.2" -i https://pypi.tuna.tsinghua.edu.cn/simple
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }
& $venvPython -m pip config --site set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple | Out-Null

Write-Host "Installing full author feature set: base + face + local-asr ..."
& $venvPython -m pip install "uv==0.8.22" -i https://pypi.tuna.tsinghua.edu.cn/simple
if ($LASTEXITCODE -ne 0) { throw "uv installation failed" }
& $venvPython -m uv sync --project $serviceRoot --active --locked --no-dev --inexact --extra face --extra local-asr
if ($LASTEXITCODE -ne 0) { throw "Full dependency installation failed" }

$wheel = Get-ChildItem -LiteralPath (Join-Path $serviceRoot "src\deskbot_server\iotctl\wheels") -Filter "miloco_miot-*.whl" | Select-Object -First 1
if ($wheel) {
    & $venvPython -m pip install --no-deps --no-index --upgrade $wheel.FullName
    if ($LASTEXITCODE -ne 0) { throw "miloco-miot installation failed" }
}

$envPath = Join-Path $serviceRoot ".env"
if (-not (Test-Path -LiteralPath $envPath)) {
    Copy-Item -LiteralPath (Join-Path $serviceRoot ".env.example") -Destination $envPath
    Write-Host "Created service\.env. Fill LLM / ASR / TTS credentials in the Web console or this file."
}

if (-not $SkipLiveKit) {
    $lk = Join-Path $serviceRoot "tools\install_livekit_windows.ps1"
    & powershell -ExecutionPolicy Bypass -File $lk
    if ($LASTEXITCODE -ne 0) { throw "LiveKit installation failed" }
}

if (-not $SkipModels) {
    $models = @(
        @{
            Name="MediaPipe face landmarker";
            Url=$(if ($env:DESKBOT_FACE_MODEL_URL) { $env:DESKBOT_FACE_MODEL_URL } else { "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task" });
            Path=(Join-Path $serviceRoot "models\mediapipe\face_landmarker.task");
            Min=1048576
        },
        @{
            Name="Silero VAD";
            Url=$(if ($env:DESKBOT_SILERO_VAD_URL) { $env:DESKBOT_SILERO_VAD_URL } else { "https://raw.githubusercontent.com/snakers4/silero-vad/76e3dc408eb2a5c655c34e230d2d5459b4439daa/src/silero_vad/data/silero_vad.onnx" });
            Path=(Join-Path $serviceRoot "models\silero_vad\silero_vad.onnx");
            Min=102400
        }
    )
    foreach ($m in $models) {
        if ((Test-Path -LiteralPath $m.Path) -and ((Get-Item -LiteralPath $m.Path).Length -ge $m.Min)) {
            Write-Host "$($m.Name): already ready"
            continue
        }
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $m.Path) | Out-Null
        $tmp = "$($m.Path).tmp"
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
        Write-Host "Downloading $($m.Name) ..."
        try {
            Invoke-WebRequest -Uri $m.Url -OutFile $tmp -UseBasicParsing -TimeoutSec 300
            if ((Get-Item -LiteralPath $tmp).Length -lt $m.Min) { throw "download too small" }
            Move-Item -LiteralPath $tmp -Destination $m.Path -Force
        } catch {
            Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
            Write-Warning "$($m.Name) download failed. Set the corresponding DESKBOT_*_MODEL_URL to an accessible mirror and rerun. Other features can still continue."
        }
    }
}

Write-Host ""
Write-Host "Full environment is ready."
Write-Host "Start Core + Web with:"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\tools\run_local_windows.ps1"
Write-Host "Web console: http://127.0.0.1:5050/"
