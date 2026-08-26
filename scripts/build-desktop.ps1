$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Ambiente virtual ausente. Crie .venv antes de gerar o executavel."
}

Push-Location $ProjectRoot
$SmokeData = $null
try {
    & $Python -m pip install -e ".[desktop,browser,build]"
    if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar dependencias do desktop." }
    & $Python -m PyInstaller --noconfirm --clean KADCollector.spec
    if ($LASTEXITCODE -ne 0) { throw "Falha ao gerar KAD-Collector.exe." }
    $Executable = Join-Path $ProjectRoot "dist\KAD-Collector.exe"
    $TempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
    $SmokeData = Join-Path $TempRoot ("kad-collector-smoke-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $SmokeData | Out-Null
    $SmokeProcess = Start-Process -FilePath $Executable -ArgumentList @("--smoke-test", "--data-dir", $SmokeData) -WindowStyle Hidden -Wait -PassThru
    if ($SmokeProcess.ExitCode -ne 0) { throw "O executavel falhou no smoke test." }
}
finally {
    if ($null -ne $SmokeData) {
        $ResolvedSmokeData = [System.IO.Path]::GetFullPath($SmokeData)
        if ($ResolvedSmokeData.StartsWith($TempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $ResolvedSmokeData -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    Pop-Location
}
