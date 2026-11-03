# Read-only UI Automation inspection. Never clicks, types, logs in, or sends.
[CmdletBinding()]
param(
    [int]$ProcessId = 0,
    [int]$MaxNodes = 1500,
    [int]$MaxDepth = 12,
    [ValidateSet('Control', 'Raw')]
    [string]$TreeView = 'Control',
    [switch]$IncludeText,
    [string]$OutputPath = ""
)
$ErrorActionPreference = 'Stop'
if ($MaxNodes -lt 1 -or $MaxNodes -gt 10000 -or $MaxDepth -lt 1 -or $MaxDepth -gt 30) {
    throw 'MaxNodes must be 1..10000; MaxDepth must be 1..30.'
}
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
if (-not $OutputPath) {
    $OutputPath = Join-Path $PSScriptRoot '..\diagnostics\douyin-uia.json'
}
$resolvedOutput = [IO.Path]::GetFullPath($OutputPath)
[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($resolvedOutput)) | Out-Null
$root = [Windows.Automation.AutomationElement]::RootElement
$windows = $root.FindAll(
    [Windows.Automation.TreeScope]::Children,
    [Windows.Automation.Condition]::TrueCondition
)
$candidates = @()
foreach ($window in $windows) {
    try {
        $info = $window.Current
        $process = Get-Process -Id $info.ProcessId -ErrorAction SilentlyContinue
        if (($ProcessId -gt 0 -and $info.ProcessId -eq $ProcessId) -or
            ($ProcessId -eq 0 -and $process.ProcessName -match '(?i)douyin')) {
            $candidates += [PSCustomObject]@{
                element = $window
                process_id = $info.ProcessId
                process_name = $process.ProcessName
                # Window titles may contain group names; include only on explicit request.
                title = $(if ($IncludeText) { $info.Name } else { '[redacted]' })
            }
        }
    } catch { continue }
}
$walker = if ($TreeView -eq 'Raw') {
    [Windows.Automation.TreeWalker]::RawViewWalker
} else {
    [Windows.Automation.TreeWalker]::ControlViewWalker
}
$nodes = [Collections.Generic.List[object]]::new()
$errors = [Collections.Generic.List[string]]::new()
$script:truncated = $false
function Read-Node($element, [string]$nodePath, [int]$depth) {
    if ($nodes.Count -ge $MaxNodes) { $script:truncated = $true; return }
    try {
        $info = $element.Current
        $patterns = @($element.GetSupportedPatterns() | ForEach-Object { $_.ProgrammaticName })
        $nodes.Add([PSCustomObject]@{
            path = $nodePath
            process_id = $info.ProcessId
            control_type = $info.ControlType.ProgrammaticName
            automation_id = $info.AutomationId
            class_name = $info.ClassName
            name = $(if ($IncludeText) { $info.Name } else { '[redacted]' })
            enabled = $info.IsEnabled
            offscreen = $info.IsOffscreen
            patterns = $patterns
        })
        if ($depth -ge $MaxDepth) { $script:truncated = $true; return }
        $child = $walker.GetFirstChild($element)
        $index = 0
        while ($null -ne $child) {
            Read-Node $child "$nodePath/$index" ($depth + 1)
            if ($nodes.Count -ge $MaxNodes) { $script:truncated = $true; break }
            $child = $walker.GetNextSibling($child)
            $index++
        }
    } catch { $errors.Add('UIA node unavailable; window may have changed.') }
}
$index = 0
foreach ($candidate in $candidates) {
    Read-Node $candidate.element "window-$index" 0
    $index++
}
$report = [ordered]@{
    schema = 1
    captured_at = [DateTime]::UtcNow.ToString('o')
    read_only = $true
    text_included = [bool]$IncludeText
    tree_view = $TreeView
    desktop_window_count = $windows.Count
    session_id = (Get-Process -Id $PID).SessionId
    windows = @($candidates | Select-Object process_id,process_name,title)
    nodes = @($nodes.ToArray())
    truncated = $script:truncated
    errors = @($errors.ToArray())
}
$report | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 -LiteralPath $resolvedOutput
Write-Host "Saved $($nodes.Count) UIA nodes to $resolvedOutput"
if ($windows.Count -eq 0) {
    Write-Host 'UIA cannot see any desktop windows in this execution context. This does not prove the client is closed. Run this script in a local Windows PowerShell window on the desktop.'
} elseif ($candidates.Count -eq 0) {
    Write-Host 'No Douyin window found. Open the desktop client, or pass -ProcessId from Task Manager.'
}
if (-not $IncludeText) {
    Write-Host 'Names are redacted. Review locally before sharing any report generated with -IncludeText.'
}
