param([int]$Port = 8766)
$ErrorActionPreference = 'Stop'
$libraryRoot = Join-Path (Split-Path $PSScriptRoot -Parent) '.master-storyboarding\visual-library'
$pythonRuntime = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if (-not (Test-Path -LiteralPath $pythonRuntime)) {
    throw '未找到 Codex 内置 Python 环境。请在 Codex 中重新打开资产库并核对运行环境。'
}
$libraryUrl = "http://127.0.0.1:$Port"
try {
    $running = Invoke-RestMethod -Uri "$libraryUrl/api/state" -TimeoutSec 2
    if ($running.root -eq $libraryRoot) {
        Start-Process $libraryUrl
        return
    }
    throw "端口 $Port 已被其他资产库占用。"
} catch {
    if ($_.Exception.Message -like '*其他资产库占用*') { throw }
}
$scriptPath = Join-Path $PSScriptRoot 'visual_asset_library.py'
Start-Process -FilePath $pythonRuntime -ArgumentList @('"' + $scriptPath + '"', 'serve', '--port', "$Port", '--open') -WindowStyle Hidden
