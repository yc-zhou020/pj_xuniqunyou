# Invoke only an explicitly observed control after checking window, path, role and name.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][int]$WindowHandle,
    [Parameter(Mandatory=$true)][int]$ProcessId,
    [Parameter(Mandatory=$true)][string]$NodePath,
    [Parameter(Mandatory=$true)][AllowEmptyString()][string]$ExpectedName,
    [Parameter(Mandatory=$true)][int]$ExpectedRole,
    [switch]$Hover
)
$ErrorActionPreference = 'Stop'
$null = & "$PSScriptRoot\Inspect-DouyinMsaa.ps1" -ProcessId $ProcessId -IncludeText -Stdout
. "$PSScriptRoot\DouyinInput.ps1"
$window = [Windows.Automation.AutomationElement]::FromHandle([IntPtr]$WindowHandle)
if ($window.Current.ProcessId -ne $ProcessId) { throw 'Window process changed' }
$snapshot = [DouyinMsaaInspector]::Read([IntPtr]$WindowHandle,$true,5000,40)
if ($snapshot.Truncated -or $snapshot.Errors.Count) { throw 'Incomplete snapshot' }
$matches = @($snapshot.Nodes | Where-Object { $_.Path -ceq $NodePath -and ([string]$_.Name) -ceq $ExpectedName -and $_.Role -eq $ExpectedRole })
if ($matches.Count -ne 1) { throw 'Observed control changed' }
if (-not [DouyinSendProbe]::SetForegroundWindow([IntPtr]$WindowHandle) -and [DouyinSendProbe]::GetForegroundWindow() -ne [IntPtr]$WindowHandle) {
    throw 'Target is not foreground'
}
if ($Hover) {
    [DouyinSendProbe]::Hover([IntPtr]$WindowHandle,$NodePath)
} else {
    [DouyinSendProbe]::Resolve([IntPtr]$WindowHandle,$NodePath).accDoDefaultAction(0)
}
Write-Output 'Observed control invoked.'
