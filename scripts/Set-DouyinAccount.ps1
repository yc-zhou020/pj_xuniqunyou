# Interactive local enrollment. No secrets appear in command-line arguments.
[CmdletBinding()]
param([switch]$Replace, [switch]$Phone)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$Host.UI.RawUI.WindowTitle = 'Douyin account setup - private input'
Write-Host 'Enter the dedicated Douyin account and password below. All input is hidden.'
Write-Host 'Storage: Windows Credential Manager. Nothing is written to project files.'
$credentialArgs = @('run','--locked','python','-m','group_friend','credentials','set')
if ($Replace) { $credentialArgs += '--replace' }
if ($Phone) { $credentialArgs += '--phone' }
& uv @credentialArgs
if ($LASTEXITCODE -eq 0) {
    Write-Host 'Saved. You may close this window and tell Codex setup is complete.'
} else {
    Write-Host 'Setup did not finish. Existing credentials, if any, were preserved.'
}
$null = Read-Host 'Press Enter to close'
