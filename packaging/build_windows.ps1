# Build the Windows app and installer for the Offline Video Editor.
#
#   powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
#
# Steps: build tools -> UI (typecheck, tests, build) -> Python tests -> offline check
#        -> PyInstaller one-folder app -> self-check of the built app -> Inno Setup installer.
# Output: build\dist\OfflineVideoEditor\          (portable app folder)
#         build\installer\OfflineVideoEditor-Setup-<version>.exe
#
# Switches:
#   -SkipTests     skip pytest (UI tests still run); use only when re-packaging the same commit
#   -NoInstaller   stop after the app folder (no Inno Setup needed)

param([switch]$SkipTests, [switch]$NoInstaller)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

function Invoke-Step([string]$Title, [scriptblock]$Command) {
    Write-Host ""
    Write-Host "==> $Title" -ForegroundColor Cyan
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$Title failed (exit code $LASTEXITCODE)" }
}

$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { throw "Missing .venv. Create it first: py -3 -m venv .venv; .venv\Scripts\python.exe -m pip install -e .[dev]" }

$Version = (& $Py -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])").Trim()
Write-Host "Offline Video Editor $Version"

Invoke-Step "Install build tools" { & $Py -m pip install --disable-pip-version-check -q -r packaging\requirements-build.txt }

Push-Location ui
try {
    Invoke-Step "UI: npm ci"           { npm ci --no-audit --no-fund }
    Invoke-Step "UI: typecheck"        { npm run typecheck }
    Invoke-Step "UI: tests"            { npm test }
    Invoke-Step "UI: production build" { npm run build }
} finally { Pop-Location }

if (-not $SkipTests) {
    Invoke-Step "Python tests" { & $Py -m pytest -q }
}
Invoke-Step "Offline policy check" { & $Py tools\check_offline.py }

Invoke-Step "PyInstaller" {
    & $Py -m PyInstaller packaging\OfflineVideoEditor.spec --noconfirm --clean `
        --distpath build\dist --workpath build\pyinstaller
}

$Exe = Join-Path $Root "build\dist\OfflineVideoEditor\OfflineVideoEditor.exe"
if (-not (Test-Path $Exe)) { throw "PyInstaller did not produce $Exe" }

# The app is windowed (no console), so --check writes its report to <data>\logs\self_check.json.
$CheckDir = Join-Path $env:TEMP "OfflineVideoEditor-selfcheck"
$env:OFFLINE_EDITOR_DATA = $CheckDir
try {
    Write-Host ""
    Write-Host "==> Self-check of the built app" -ForegroundColor Cyan
    $proc = Start-Process -FilePath $Exe -ArgumentList "--check" -Wait -PassThru
    Get-Content (Join-Path $CheckDir "logs\self_check.json")
    if ($proc.ExitCode -ne 0) { throw "Self-check of the built app failed (see above)" }
} finally {
    Remove-Item Env:\OFFLINE_EDITOR_DATA
}

if ($NoInstaller) {
    Write-Host ""
    Write-Host "App folder ready: build\dist\OfflineVideoEditor" -ForegroundColor Green
    exit 0
}

$Iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue | Select-Object -First 1).Source
if (-not $Iscc) {
    foreach ($candidate in @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
                             "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe")) {
        if (Test-Path $candidate) { $Iscc = $candidate; break }
    }
}
if (-not $Iscc) {
    throw "Inno Setup 6 not found. Install it (winget install JRSoftware.InnoSetup) or run with -NoInstaller."
}

Invoke-Step "Inno Setup" { & $Iscc "/DAppVersion=$Version" packaging\installer.iss }

$Installer = Join-Path $Root "build\installer\OfflineVideoEditor-Setup-$Version.exe"
Write-Host ""
Write-Host "Installer ready: $Installer" -ForegroundColor Green
Get-FileHash -Algorithm SHA256 $Installer | Format-List
