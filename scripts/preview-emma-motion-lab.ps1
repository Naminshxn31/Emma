param(
    [string]$RiveCli,
    [switch]$VerifyOnly
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$project = Join-Path $repoRoot "client\rive\emma-motion-lab"
if (-not $RiveCli) {
    $command = Get-Command rive -ErrorAction SilentlyContinue
    $RiveCli = if ($command) { $command.Source } else { Join-Path $env:USERPROFILE ".rive\bin\rive.exe" }
}
if (-not (Test-Path -LiteralPath $RiveCli -PathType Leaf)) {
    throw "Install Rive CLI before opening the motion lab."
}

& python (Join-Path $PSScriptRoot "build-emma-motion-lab.py")
if ($LASTEXITCODE -ne 0) { throw "Could not generate the motion lab." }
& $RiveCli $project --verify
if ($LASTEXITCODE -ne 0) { throw "Motion lab validation failed." }
$inspection = & $RiveCli inspect $project --summary | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $inspection.problems.Count -gt 0) {
    $inspection.problems | Format-List
    throw "Motion lab inspection found problems."
}
Push-Location $project
try {
    & $RiveCli . --screenshot --advance=1
    if ($LASTEXITCODE -ne 0) { throw "Motion lab render failed." }
} finally {
    Pop-Location
}
if (-not $VerifyOnly) {
    # This is the separate native Rive preview, with its own interactive window.
    Start-Process -FilePath $RiveCli -WorkingDirectory $project `
        -ArgumentList @(".", "--fit=contain", "--viewport=1180x780") `
        -WindowStyle Normal `
        -RedirectStandardOutput (Join-Path $project "build\preview.out.log") `
        -RedirectStandardError (Join-Path $project "build\preview.err.log") | Out-Null
    Write-Host "Opened Emma Motion Lab. Select a prompt or Play all motions."
}
