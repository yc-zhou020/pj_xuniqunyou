# Read-only MSAA inspection. Never sets values, clicks, or invokes actions.
[CmdletBinding()]
param(
    [int]$ProcessId = 0,
    [ValidateRange(1, 10000)][int]$MaxNodes = 5000,
    [ValidateRange(1, 60)][int]$MaxDepth = 40,
    [switch]$IncludeText,
    [switch]$Stdout,
    [string]$OutputPath = "$PSScriptRoot\..\diagnostics\douyin-msaa.json"
)
$ErrorActionPreference = 'Stop'
if ($Stdout) { [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false) }
Add-Type -AssemblyName UIAutomationClient
if (-not ('DouyinMsaaInspector' -as [type])) {
    Add-Type -ReferencedAssemblies Accessibility -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using Accessibility;
public class DouyinMsaaNode {
    public string Path;
    public int Role;
    public int Children;
    public string Name;
    public string DefaultAction;
    public int[] Bounds;
}
public class DouyinMsaaResult {
    public List<DouyinMsaaNode> Nodes = new List<DouyinMsaaNode>();
    public List<string> Errors = new List<string>();
    public bool Truncated;
}
public static class DouyinMsaaInspector {
    [DllImport("oleacc.dll")]
    static extern int AccessibleObjectFromWindow(IntPtr hwnd, uint id, ref Guid iid,
        [MarshalAs(UnmanagedType.Interface)] out IAccessible obj);
    [DllImport("oleacc.dll")]
    static extern int AccessibleChildren(IAccessible obj, int start, int count,
        [Out, MarshalAs(UnmanagedType.LPArray, SizeParamIndex=2)] object[] children, out int obtained);
    static void Walk(IAccessible obj, object childId, string path, int depth,
        bool text, int limit, int maxDepth, DouyinMsaaResult result) {
        if (result.Nodes.Count >= limit) { result.Truncated = true; return; }
        try {
            bool self = Convert.ToInt32(childId) == 0;
            int count = self ? obj.accChildCount : 0;
            int x,y,width,height;
            obj.accLocation(out x,out y,out width,out height,childId);
            result.Nodes.Add(new DouyinMsaaNode {
                Path = path, Role = Convert.ToInt32(obj.get_accRole(childId)), Children = count,
                Name = text ? obj.get_accName(childId) : "[redacted]",
                DefaultAction = obj.get_accDefaultAction(childId),
                Bounds = new int[]{x,y,width,height}
            });
            if (count == 0) return;
            if (depth >= maxDepth) { result.Truncated = true; return; }
            int capacity = Math.Min(count, limit - result.Nodes.Count);
            if (capacity <= 0) { result.Truncated = true; return; }
            if (capacity < count) result.Truncated = true;
            object[] children = new object[capacity];
            int obtained;
            int hr = AccessibleChildren(obj, 0, capacity, children, out obtained);
            if (hr < 0) { result.Errors.Add(path + " children_hresult=" + hr); return; }
            for (int i = 0; i < obtained; i++) {
                if (result.Nodes.Count >= limit) { result.Truncated = true; break; }
                IAccessible child = children[i] as IAccessible;
                if (child != null) Walk(child, 0, path + "/" + i, depth + 1, text, limit, maxDepth, result);
                else if (children[i] is int) Walk(obj, children[i], path + "/" + i, depth + 1, text, limit, maxDepth, result);
            }
        } catch (Exception e) { result.Errors.Add(path + " " + e.GetType().Name); }
    }
    public static DouyinMsaaResult Read(IntPtr hwnd, bool text, int limit, int depth) {
        var result = new DouyinMsaaResult();
        Guid iid = new Guid("618736E0-3C3D-11CF-810C-00AA00389B71");
        IAccessible obj;
        int hr = AccessibleObjectFromWindow(hwnd, 0xFFFFFFFC, ref iid, out obj);
        if (hr == 0 && obj != null) Walk(obj, 0, "root", 0, text, limit, depth, result);
        else result.Errors.Add("root_hresult=" + hr);
        return result;
    }
}
'@
}
$desktop = [Windows.Automation.AutomationElement]::RootElement
$windows = $desktop.FindAll([Windows.Automation.TreeScope]::Children, [Windows.Automation.Condition]::TrueCondition)
$reports = @(foreach ($window in $windows) {
    $info = $window.Current
    $process = Get-Process -Id $info.ProcessId -ErrorAction SilentlyContinue
    if (($ProcessId -gt 0 -and $info.ProcessId -eq $ProcessId) -or
        ($ProcessId -eq 0 -and $process.ProcessName -eq 'douyin')) {
        [pscustomobject]@{
            process_id = $info.ProcessId
            process_started_at = $process.StartTime.ToUniversalTime().ToString('o')
            window_handle = $info.NativeWindowHandle
            class_name = $info.ClassName
            is_offscreen = $info.IsOffscreen
            result = [DouyinMsaaInspector]::Read([IntPtr]$info.NativeWindowHandle, [bool]$IncludeText, $MaxNodes, $MaxDepth)
        }
    }
})
$report = [ordered]@{
    schema = 1
    captured_at = [DateTime]::UtcNow.ToString('o')
    read_only = $true
    text_included = [bool]$IncludeText
    windows = $reports
}
if ($Stdout) {
    $report | ConvertTo-Json -Depth 10
    return
}
$resolvedOutput = [IO.Path]::GetFullPath($OutputPath)
[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($resolvedOutput)) | Out-Null
$report | ConvertTo-Json -Depth 10 | Set-Content -Encoding UTF8 -LiteralPath $resolvedOutput
foreach ($entry in $reports) {
    Write-Host "PID $($entry.process_id): $($entry.result.Nodes.Count) nodes; truncated=$($entry.result.Truncated); errors=$($entry.result.Errors.Count)"
}
Write-Host "Saved to $resolvedOutput"
if ($IncludeText) { Write-Host 'Contains visible names and messages. Keep local or redact before sharing.' }
