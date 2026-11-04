# Called by human review or the explicitly enabled whitelist runtime after a durable send claim.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$RequestPath, [switch]$CheckOnly, [switch]$InspectOnly)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$inputStarted = $false
$outcome = 'not_sent'
$stage = 'initializing'
$reason = $null
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
        $candidates = @()
        foreach ($node in $nodes) {
            if ($node.Role -ne 20 -or $node.DefaultAction -cne ([string][char]0x6fc0 + [char]0x6d3b)) { continue }
            $main = $node.Path -replace '/[^/]+/[^/]+/[^/]+$', ''
            $base = $main + '/0/0'
            # Inline private pane: the contact-list label is not a target header.
            if ($node.Path -ceq ($main + '/3/1/0') -or $node.Path -ceq ($main + '/2/1/0')) {
                $pane = $main -replace '/[^/]+/[^/]+/[^/]+$', ''
                $headerPrefix = $pane + '/0/'
                $header = @($nodes | Where-Object { $_.Path.StartsWith($headerPrefix) -and $_.Role -eq 41 -and $_.Name -ceq $binding.group_heading })
                $privateBase = $main + '/0'
                if ($main -ceq ($pane + '/1/0/0') -and $header.Count -eq 1 -and @($nodes | Where-Object { $_.Path -ceq $privateBase }).Count -eq 1) {
                    $candidates += [pscustomobject]@{Path=$node.Path; Base=$privateBase; Private=$true}
                    continue
                }
            }
            if ($node.Path.StartsWith($main + '/2/') -and @($nodes | Where-Object { $_.Path -ceq $base }).Count -eq 1) {
                if (@($nodes | Where-Object { $_.Name -ceq $binding.group_heading }).Count -eq 1) {
                    $candidates += [pscustomobject]@{Path=$node.Path; Base=$base; Private=$false}
                }
            }
        }
        if ($candidates.Count -ne 1) { throw 'Composer not unique' }
        $composer = $candidates[0].Path
        $base = $candidates[0].Base
        $privateChat = $candidates[0].Private
        if ($request.require_incoming -and -not $privateChat) { throw 'Private chat required' }
        if ($privateChat) {
            $children = @($nodes | Where-Object { $_.Path.StartsWith($base + '/') -and $_.Path.Substring($base.Length + 1) -notmatch '/' })
            if ($children.Count -eq 1 -and $children[0].Role -eq 20) {
                $wrapper = $children[0]
                $outer = @($nodes | Where-Object { $_.Path -ceq $base })[0]
                $sameBounds = ($outer.Bounds -join ',') -ceq ($wrapper.Bounds -join ',')
                $timeline = @($nodes | Where-Object { $_.Path.StartsWith($wrapper.Path + '/') -and $_.Path.Substring($wrapper.Path.Length + 1) -notmatch '/' })
                $timePattern = '^(?:\u521a\u521a|\u524d\u5929|\u6628\u5929(?: \d{1,2}:\d{2})?|\d+\u5206\u949f\u524d|\d+\u5c0f\u65f6\u524d|\d{1,2}:\d{2}|\d{1,2}[\u6708/]\d{1,2}\u65e5?)$'
                if ($sameBounds -and @($timeline | Where-Object { $_.Role -eq 41 -and $_.Name -match $timePattern }).Count -gt 0 -and
                    @($timeline | Where-Object { $_.Role -eq 20 }).Count -gt 0) {
                    $base = $wrapper.Path
                }
            }
        }
        $direction = 'unknown'
        $sourceIncoming = $false
        $directions = New-Object 'System.Collections.Generic.List[string]'
        $baseNode = @($nodes | Where-Object { $_.Path -ceq $base })[0]
        $rows = @($nodes | Where-Object { $_.Role -eq 20 -and $_.Path.StartsWith($base + '/') -and $_.Path.Substring($base.Length + 1) -notmatch '/' })
        $rows = @($rows | Sort-Object { [int]($_.Path -replace '^.*/','') } -Descending)
        $texts = @(foreach ($row in $rows) {
            $header = @($nodes | Where-Object { $_.Path -ceq ($row.Path + '/1/0') -and $_.Role -eq 41 })
            if ($privateChat) {
                $avatar = @($nodes | Where-Object { $_.Path.StartsWith($row.Path + '/0/') -and $_.Role -eq 40 })
                if ($avatar.Count) {
                    $direction = 'unknown'
                    if ($avatar.Count -eq 1 -and $avatar[0].Bounds.Count -eq 4 -and $avatar[0].Bounds[2] -gt 0 -and $baseNode.Bounds[2] -gt 0) {
                        $center = $avatar[0].Bounds[0] + $avatar[0].Bounds[2] / 2
                        $left = $baseNode.Bounds[0]
                        $width = $baseNode.Bounds[2]
                        if ($center -ge $left -and $center -lt ($left + $width * 0.25)) { $direction = 'incoming' }
                        elseif ($center -gt ($left + $width * 0.75) -and $center -le ($left + $width)) { $direction = 'outgoing' }
                    }
                }
                $body = $row.Path + $(if ($avatar.Count) {'/1'} else {'/0'})
                if (@($nodes | Where-Object { $_.Path -ceq $body -and $_.Role -eq 20 }).Count -ne 1) { $direction='unknown'; continue }
            } elseif ($header.Count -eq 1) {
                $badge = @($nodes | Where-Object { $_.Path -ceq ($row.Path + '/2') -and $_.Role -eq 41 })
                $body = $row.Path + $(if ($badge.Count) {'/3'} else {'/2'})
            } else { $body = $row.Path + '/0' }
            $bodyNodes = @($nodes | Where-Object { $_.Path -ceq $body -or $_.Path.StartsWith($body + '/') })
            if (@($bodyNodes | Where-Object { $_.Role -in @(30,40,43,59) }).Count) { continue }
            $text = (($bodyNodes | Where-Object { $_.Role -eq 41 } | ForEach-Object { $_.Name }) -join '').Replace([string][char]0x200b,'')
            if ($text -ceq $request.source -and $direction -ceq 'incoming') { $sourceIncoming = $true }
            if ($text) { $directions.Add($direction); $text }
        })
        if ($null -ne $request.source_anchor) {
            if (-not $request.require_incoming -or -not $privateChat) { throw 'Private occurrence anchor required' }
            $anchorTexts = @($request.source_anchor.texts)
            $sourceIndex = [int]$request.source_anchor.index
            if ($sourceIndex -lt 0 -or $sourceIndex -ge $anchorTexts.Count -or $texts.Count -lt $anchorTexts.Count) { throw 'Source occurrence unavailable' }
            if ($anchorTexts[$sourceIndex] -cne $request.source) { throw 'Source occurrence mismatched' }
            for ($i=0; $i -lt $anchorTexts.Count; $i++) {
                if ($texts[$i] -cne $anchorTexts[$i]) { throw 'Source sequence changed' }
            }
            $sourceIncoming = $directions[$sourceIndex] -ceq 'incoming'
        } elseif (@($texts | Where-Object { $_ -ceq $request.source }).Count -ne 1) { throw 'Source anchor changed' }
        if ($request.require_incoming -and -not $sourceIncoming) { throw 'Incoming source unconfirmed' }
        $placeholder = [string][char]0x53d1 + [char]0x9001 + [char]0x6d88 + [char]0x606f
        $draft = (($nodes | Where-Object { $_.Path.StartsWith($composer + '/') -and $_.Role -eq 41 -and $_.Name -cne $placeholder } | ForEach-Object { $_.Name }) -join '').Replace([string][char]0x200b,'')
        $sendButton = $null
        if ($privateChat) {
            $toolbar = $composer -replace '/[^/]+/[^/]+$', ''
            $toolbarNode = @($nodes | Where-Object { $_.Path -ceq $toolbar })[0]
            $clickAction = [string][char]0x70b9 + [char]0x51fb
            $buttons = @($nodes | Where-Object {
                $_.Path.StartsWith($toolbar + '/') -and
                $_.Path.Substring($toolbar.Length + 1) -notmatch '/' -and
                $_.Role -eq 40 -and $_.DefaultAction -ceq $clickAction -and
                $_.Bounds[2] -gt 0 -and $_.Bounds[3] -gt 0 -and
                ($_.Bounds[0] + $_.Bounds[2] / 2) -gt ($toolbarNode.Bounds[0] + $toolbarNode.Bounds[2] * 0.8) -and
                ($_.Bounds[0] + $_.Bounds[2]) -le ($toolbarNode.Bounds[0] + $toolbarNode.Bounds[2])
            })
            if ($buttons.Count -ne 1) { throw 'Private send button not unique' }
            $sendButton = $buttons[0].Path
        }
        [pscustomobject]@{Composer=$composer; Draft=$draft; SendButton=$sendButton; Private=$privateChat; ReplyCount=@($texts | Where-Object { $_ -ceq $request.text }).Count}
    }
    $stage = 'preflight'
    $context = Get-Context
    if ($InspectOnly) {
        [ordered]@{outcome='inspected'; stage='read_only_inspection'; draft_matches=($context.Draft -ceq $request.text);
            composer_focused=[DouyinSendProbe]::Focused([IntPtr]$binding.window_handle,$context.Composer);
            send_button_available=([bool]$context.SendButton)} | ConvertTo-Json -Compress
        return
    }
    if ($context.Draft.Length -gt 0) { throw 'Existing draft preserved' }
    if ($CheckOnly) {
        [ordered]@{outcome='ready'; stage='read_only_preflight'; mode='read_only_preflight'} | ConvertTo-Json -Compress
        return
    }
    $beforeCount = $context.ReplyCount
    $stage = 'focusing'
    [DouyinSendProbe]::Focus([IntPtr]$binding.window_handle,$context.Composer)
    Start-Sleep -Milliseconds 200
    $context = Get-Context
    if ($context.Draft.Length -gt 0) { throw 'Draft changed' }
    Assert-NotPaused
    $inputStarted = $true
    $stage = 'typing'
    [DouyinSendProbe]::TypeText([IntPtr]$binding.window_handle,$context.Composer,$request.text)
    Start-Sleep -Milliseconds 300
    $stage = 'validating_draft'
    $context = Get-Context
    if ($context.Draft -cne $request.text) { throw 'Exact draft not confirmed' }
    Assert-NotPaused
    $stage = 'invoking_send'
    if ($context.Private) {
        [DouyinSendProbe]::InvokePrivateSend([IntPtr]$binding.window_handle,$context.SendButton)
    } else {
        [DouyinSendProbe]::InvokeSend([IntPtr]$binding.window_handle,$context.Composer)
    }
    $outcome = 'unknown'
    $stage = 'confirming_send'
    foreach ($attempt in 1..3) {
        Start-Sleep -Milliseconds 400
        $context = Get-Context
        if ($context.Draft.Length -eq 0 -and $context.ReplyCount -eq ($beforeCount + 1)) {
            $outcome = 'visible_sent'
            $stage = 'confirmed'
            break
        }
    }
} catch {
    # No UI text, request bodies, or exception details are printed.
    $outcome = $(if ($inputStarted) {'unknown'} else {'not_sent'})
    # Fixed diagnostic codes only; never echo exception text containing UI data.
    $reason = switch ($_.Exception.Message) {
        'Exact draft not confirmed' { 'draft_mismatch' }
        'Composer focus changed' { 'composer_focus_changed' }
        'Composer focus not confirmed' { 'composer_focus_not_confirmed' }
        'Send window focus changed' { 'window_focus_changed' }
        'Private send control unavailable' { 'send_control_unavailable' }
        'Private send control obscured' { 'send_control_obscured' }
        'Existing draft preserved' { 'existing_draft' }
        'Source sequence changed' { 'source_sequence_changed' }
        'Private send button not unique' { 'send_control_not_unique' }
        default { 'native_operation_failed' }
    }
}
[ordered]@{outcome=$outcome;stage=$stage;reason=$reason} | ConvertTo-Json -Compress
