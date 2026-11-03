# Start a visible client for local accessibility diagnostics. Never stops processes.
[CmdletBinding()]
param([string]$ExecutablePath = 'C:\Program Files (x86)\ByteDance\douyin\douyin.exe')
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $ExecutablePath -PathType Leaf)) {
    throw 'Douyin executable not found; pass -ExecutablePath with the installed executable.'
}
if (@(Get-Process -Name douyin -ErrorAction SilentlyContinue).Count -gt 0) {
    throw 'Exit Douyin completely first so the existing instance cannot ignore the accessibility flag.'
}
$hadNodeMode = Test-Path Env:ELECTRON_RUN_AS_NODE
$savedNodeMode = $env:ELECTRON_RUN_AS_NODE
try {
    Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
    $client = Start-Process -FilePath $ExecutablePath -WorkingDirectory (Split-Path -Parent $ExecutablePath) -ArgumentList '--force-renderer-accessibility' -PassThru
    Write-Host "Started PID $($client.Id) with --force-renderer-accessibility. Open the target group manually."
} finally {
    if ($hadNodeMode) { $env:ELECTRON_RUN_AS_NODE = $savedNodeMode }
}
