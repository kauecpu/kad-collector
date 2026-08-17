$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Ambiente virtual ausente. Crie .venv antes de gerar o executavel."
}

Push-Location $ProjectRoot
try {
    & $Python -m pip install -e ".[desktop,build]"
    if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar dependencias do desktop." }
    & $Python -m PyInstaller --noconfirm --clean KADCollector.spec
    if ($LASTEXITCODE -ne 0) { throw "Falha ao gerar KAD-Collector.exe." }
    $Executable = Join-Path $ProjectRoot "dist\KAD-Collector.exe"
    $SmokeProcess = Start-Process -FilePath $Executable -ArgumentList @("--smoke-test") -WindowStyle Hidden -Wait -PassThru
    if ($SmokeProcess.ExitCode -ne 0) { throw "O executavel falhou no smoke test." }
}
finally {
    Pop-Location
}
