# Called only after an exact revision is approved and atomically claimed in SQLite.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$RequestPath, [switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$inputStarted = $false
$outcome = 'not_sent'
try {
    $request = Get-Content -LiteralPath $RequestPath -Encoding UTF8 -Raw | ConvertFrom-Json
    $binding = $request.binding
    if (-not $request.text -or $request.text.Length -gt 600 -or $request.text -match '[\x00-\x1f\x7f-\x9f]') {
        throw 'Invalid single-line reply'
    }
    # Load read-only inspector types without writing raw snapshots or printing text.
    $null = & "$PSScriptRoot\Inspect-DouyinMsaa.ps1" -ProcessId $binding.process_id -IncludeText -Stdout
    . "$PSScriptRoot\DouyinInput.ps1"
    function Assert-NotPaused {
        if (Test-Path -LiteralPath $request.pause_path) { throw 'Paused' }
    }
    function Get-Context {
        Assert-NotPaused
        $process = Get-Process -Id $binding.process_id -ErrorAction Stop
        if ($process.StartTime.ToUniversalTime().ToString('o') -cne $binding.process_started_at) { throw 'Client restarted' }
        $window = [Windows.Automation.AutomationElement]::FromHandle([IntPtr]$binding.window_handle)
        if ($window.Current.ProcessId -ne $binding.process_id -or $window.Current.IsOffscreen) { throw 'Target unavailable' }
        $snapshot = [DouyinMsaaInspector]::Read([IntPtr]$binding.window_handle,$true,5000,40)
        if ($snapshot.Truncated -or $snapshot.Errors.Count) { throw 'Incomplete snapshot' }
        $nodes = @($snapshot.Nodes)
        if (-not $nodes.Count -or $nodes[0].Name -cne $binding.window_title) { throw 'Window changed' }
        if (@($nodes | Where-Object { $_.Name -ceq $binding.group_heading }).Count -ne 1) { throw 'Heading changed' }
        $candidates = @()
        foreach ($node in $nodes) {
            if ($node.Role -ne 20 -or $node.DefaultAction -cne ([string][char]0x6fc0 + [char]0x6d3b)) { continue }
            $main = $node.Path -replace '/[^/]+/[^/]+/[^/]+$', ''
            $base = $main + '/0/0'
            if ($node.Path.StartsWith($main + '/2/') -and @($nodes | Where-Object { $_.Path -ceq $base }).Count -eq 1) {
                $candidates += [pscustomobject]@{Path=$node.Path; Base=$base}
            }
        }
        if ($candidates.Count -ne 1) { throw 'Composer not unique' }
        $composer = $candidates[0].Path
        $base = $candidates[0].Base
        $rows = @($nodes | Where-Object { $_.Role -eq 20 -and $_.Path.StartsWith($base + '/') -and $_.Path.Substring($base.Length + 1) -notmatch '/' })
        $texts = @(foreach ($row in $rows) {
            $header = @($nodes | Where-Object { $_.Path -ceq ($row.Path + '/1/0') -and $_.Role -eq 41 })
            if ($header.Count -eq 1) {
                $badge = @($nodes | Where-Object { $_.Path -ceq ($row.Path + '/2') -and $_.Role -eq 41 })
                $body = $row.Path + $(if ($badge.Count) {'/3'} else {'/2'})
            } else { $body = $row.Path + '/0' }
            $bodyNodes = @($nodes | Where-Object { $_.Path -ceq $body -or $_.Path.StartsWith($body + '/') })
            if (@($bodyNodes | Where-Object { $_.Role -in @(30,40,43,59) }).Count) { continue }
            $text = (($bodyNodes | Where-Object { $_.Role -eq 41 } | ForEach-Object { $_.Name }) -join '').Replace([string][char]0x200b,'')
            if ($text) { $text }
        })
        if (@($texts | Where-Object { $_ -ceq $request.source }).Count -ne 1) { throw 'Source anchor changed' }
        $placeholder = [string][char]0x53d1 + [char]0x9001 + [char]0x6d88 + [char]0x606f
        $draft = (($nodes | Where-Object { $_.Path.StartsWith($composer + '/') -and $_.Role -eq 41 -and $_.Name -cne $placeholder } | ForEach-Object { $_.Name }) -join '').Replace([string][char]0x200b,'')
        [pscustomobject]@{Composer=$composer; Draft=$draft; ReplyCount=@($texts | Where-Object { $_ -ceq $request.text }).Count}
    }
    $context = Get-Context
    if ($context.Draft.Length -gt 0) { throw 'Existing draft preserved' }
    if ($CheckOnly) {
        [ordered]@{outcome='ready'; mode='read_only_preflight'} | ConvertTo-Json -Compress
        return
    }
    $beforeCount = $context.ReplyCount
    [DouyinSendProbe]::Focus([IntPtr]$binding.window_handle,$context.Composer)
    Start-Sleep -Milliseconds 200
    $context = Get-Context
    if ($context.Draft.Length -gt 0) { throw 'Draft changed' }
    Assert-NotPaused
    $inputStarted = $true
    [DouyinSendProbe]::TypeText([IntPtr]$binding.window_handle,$context.Composer,$request.text)
    Start-Sleep -Milliseconds 300
    $context = Get-Context
    if ($context.Draft -cne $request.text) { throw 'Exact draft not confirmed' }
    Assert-NotPaused
    [DouyinSendProbe]::InvokeSend([IntPtr]$binding.window_handle,$context.Composer)
    $outcome = 'unknown'
    foreach ($attempt in 1..3) {
        Start-Sleep -Milliseconds 400
        $context = Get-Context
        if ($context.Draft.Length -eq 0 -and $context.ReplyCount -eq ($beforeCount + 1)) {
            $outcome = 'visible_sent'
            break
        }
    }
} catch {
    # No UI text, request bodies, or exception details are printed.
    $outcome = $(if ($inputStarted) {'unknown'} else {'not_sent'})
}
[ordered]@{outcome=$outcome} | ConvertTo-Json -Compress
