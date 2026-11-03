# Automatic login was disabled at the user's explicit request.
[CmdletBinding()]
param(
    [string]$Stage='Check', [int]$WindowHandle, [int]$ProcessId,
    [string]$AccountPath, [string]$PasswordPath, [string]$LoginButtonPath,
    [string]$AttemptPath
)
[ordered]@{status='manual_login_required';automatic_login_enabled=$false} | ConvertTo-Json -Compress
exit 1
