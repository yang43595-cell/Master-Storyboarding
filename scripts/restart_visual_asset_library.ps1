param([int]$Port = 8766, [switch]$RecoverReadOnly)
$ErrorActionPreference = 'Stop'
$assetUrl = "http://127.0.0.1:$Port"
$assetExpectedRoot = Join-Path (Split-Path $PSScriptRoot -Parent) '.master-storyboarding\visual-library'
$assetListener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($assetListener) {
    $assetState = Invoke-RestMethod "$assetUrl/api/state" -TimeoutSec 5
    if ($assetState.root -ne $assetExpectedRoot) { throw 'Another library is using this port. Nothing stopped.' }
    $assetBusy = @($assetState.jobs | ForEach-Object { $_.steps } | Where-Object {
        $_.status -in @('generating','submitting','submitted','querying') -or $_.audit.status -in @('drafting','reviewing')
    })
    $assetPaidBusy = @($assetState.jobs | ForEach-Object { $_.steps } | Where-Object { $_.status -in @('generating','submitting','submitted','querying') })
    $assetGenerationBatch = @($assetState.projects | Where-Object { $_.batch.status -eq 'running' -and $_.batch.mode -eq 'generate' })
    if ($RecoverReadOnly -and ($assetPaidBusy.Count -or $assetGenerationBatch.Count)) {
        throw 'Image generation is active or unresolved. Read-only recovery cannot restart it.'
    }
    if (!$RecoverReadOnly -and ($assetBusy.Count -or @($assetState.projects | Where-Object { $_.planning.status -eq 'running' -or $_.batch.status -eq 'running' }).Count)) {
        throw 'Generation or review is still running. Wait for it to finish before restarting.'
    }
    $assetProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($assetListener.OwningProcess)"
    if ($assetProcess.CommandLine -notmatch 'visual_asset_library\.py.*serve') { throw 'Port owner is not the asset library. Nothing stopped.' }
    Stop-Process -Id $assetProcess.ProcessId -ErrorAction Stop
    Wait-Process -Id $assetProcess.ProcessId -Timeout 10 -ErrorAction SilentlyContinue
}
& (Join-Path $PSScriptRoot 'open_visual_asset_library.ps1') -Port $Port
