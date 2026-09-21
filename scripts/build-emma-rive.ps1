param(
    [string]$RiveCli
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$project = Join-Path $repoRoot "client\rive\emma"
$output = Join-Path $repoRoot "client\assets\emma\emma.riv"

if (-not $RiveCli) {
    $command = Get-Command rive -ErrorAction SilentlyContinue
    if ($command) {
        $RiveCli = $command.Source
    } else {
        $RiveCli = Join-Path $env:USERPROFILE ".rive\bin\rive.exe"
    }
}

if (-not (Test-Path -LiteralPath $RiveCli -PathType Leaf)) {
    throw "Rive CLI not found. Install it from https://rive.app/docs/cli/getting-started"
}

& $RiveCli $project --verify
if ($LASTEXITCODE -ne 0) { throw "Emma Rive verification failed." }

& $RiveCli $project --once
if ($LASTEXITCODE -ne 0) { throw "Emma Rive build failed." }

$built = Join-Path $project "build\emma.riv"
Copy-Item -LiteralPath $built -Destination $output -Force
Write-Host "Built $output"
