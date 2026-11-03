# One manually authorized, window-bound send probe. Draft and Send are separate steps.
[CmdletBinding()]
param(
    [ValidateSet('Draft', 'VerifyDraft', 'Send')][string]$Stage = 'Draft',
    [string]$RequestPath = "$PSScriptRoot\..\state\send-probe-request.json",
    [string]$ReceiptPath = "$PSScriptRoot\..\state\send-probe-receipt.json"
)
$ErrorActionPreference = 'Stop'
$request = Get-Content -LiteralPath $RequestPath -Encoding UTF8 -Raw | ConvertFrom-Json
if (-not $request.text -or $request.text.Length -gt 300 -or $request.text -match '[\r\n]') { throw 'Invalid single-line probe text' }
& "$PSScriptRoot\Inspect-DouyinMsaa.ps1" -ProcessId $request.process_id -IncludeText -OutputPath "$PSScriptRoot\..\diagnostics\douyin-send-preflight.json"
. "$PSScriptRoot\DouyinInput.ps1"
function Get-ProbeContext {
    $snapshot = [DouyinMsaaInspector]::Read([IntPtr]$request.window_handle, $true, 5000, 40)
    if ($snapshot.Truncated -or $snapshot.Errors.Count -gt 0) { throw 'Incomplete live snapshot' }
    $nodes = @($snapshot.Nodes)
    if ($nodes[0].Name -cne $request.window_title) { throw 'Target window title changed' }
    $window = [Windows.Automation.AutomationElement]::FromHandle([IntPtr]$request.window_handle)
    if ($window.Current.ProcessId -ne $request.process_id -or $window.Current.IsOffscreen) { throw 'Target window unavailable' }
    if (@($nodes | Where-Object { $_.Name -ceq $request.group_heading }).Count -ne 1) { throw 'Group heading not uniquely confirmed' }
    if (@($nodes | Where-Object { $_.Name -ceq $request.received_marker }).Count -lt 1) { throw 'Received test marker absent' }
    $labels = @($nodes | Where-Object { $_.Role -eq 41 -and ($_.Name -ceq $request.composer_label -or $_.Name -ceq $request.text) })
    if ($labels.Count -ne 1) { throw 'Composer label not unique' }
    $composerPath = $labels[0].Path -replace '/[^/]+/[^/]+$', ''
    $composer = @($nodes | Where-Object { $_.Path -ceq $composerPath })
    if ($composer.Count -ne 1 -or $composer[0].Role -ne 20) { throw 'Composer structure changed' }
    # The client removes its placeholder and changes toolbar nodes after typing.
    # Use one Enter keypress in the independently confirmed focused composer.
    $textNodes = @($nodes | Where-Object { $_.Path.StartsWith($composerPath + '/') -and $_.Role -eq 41 -and $_.Name -cne $request.composer_label })
    $draftText = ($textNodes | ForEach-Object { $_.Name }) -join ''
    [pscustomobject]@{ Composer=$composerPath; Draft=$draftText.Replace([string][char]0x200B,'') }
}
function Save-Receipt([string]$status) {
    [ordered]@{status=$status; at=[DateTime]::UtcNow.ToString('o'); request=$request} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $ReceiptPath -Encoding UTF8
}
$context = Get-ProbeContext
if ($Stage -eq 'Draft') {
    if (Test-Path -LiteralPath $ReceiptPath) { throw 'Probe receipt exists; refusing duplicate draft or send' }
    if ($context.Draft.Length -gt 0) { throw 'Composer contains an existing draft; no input performed' }
    Save-Receipt 'drafting'
    [DouyinSendProbe]::Focus([IntPtr]$request.window_handle,$context.Composer)
    Start-Sleep -Milliseconds 300
    [DouyinSendProbe]::TypeText([IntPtr]$request.window_handle,$context.Composer,$request.text)
    Start-Sleep -Milliseconds 500
    $context = Get-ProbeContext
    if ($context.Draft -cne $request.text) { throw 'Draft text not confirmed; no send performed' }
    Save-Receipt 'drafted'
    Write-Output 'Exact approved draft confirmed. Nothing sent.'
} elseif ($Stage -eq 'VerifyDraft') {
    $receipt = Get-Content -LiteralPath $ReceiptPath -Encoding UTF8 -Raw | ConvertFrom-Json
    if ($receipt.status -notin @('drafting', 'drafted') -or ($receipt.request | ConvertTo-Json -Compress) -cne ($request | ConvertTo-Json -Compress)) { throw 'No matching draft operation' }
    if ($context.Draft -cne $request.text) { throw 'Exact draft not confirmed' }
    Save-Receipt 'drafted'
    Write-Output 'Exact approved draft confirmed without typing or sending.'
} else {
    $receipt = Get-Content -LiteralPath $ReceiptPath -Encoding UTF8 -Raw | ConvertFrom-Json
    if ($receipt.status -ne 'drafted' -or ($receipt.request | ConvertTo-Json -Compress) -cne ($request | ConvertTo-Json -Compress)) { throw 'No matching confirmed draft; refusing send' }
    if ($context.Draft -cne $request.text) { throw 'Draft text changed; refusing send' }
    [DouyinSendProbe]::Focus([IntPtr]$request.window_handle,$context.Composer)
    Start-Sleep -Milliseconds 200
    $context = Get-ProbeContext
    if ($context.Draft -cne $request.text) { throw 'Draft text changed; refusing send' }
    Save-Receipt 'sending_unknown'
    [DouyinSendProbe]::InvokeSend([IntPtr]$request.window_handle,$context.Composer)
    Write-Output 'Send action invoked once. Verify visible message; do not retry automatically.'
}
