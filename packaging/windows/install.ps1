# Install staywake as a Windows Scheduled Task that runs at user logon.
#
# Usage (Run PowerShell as Administrator):
#     .\install.ps1                # install + start
#     .\install.ps1 -Uninstall     # remove
#
# Why a Scheduled Task and not a Windows Service?
# - SetThreadExecutionState assertions are per-process; running the daemon as
#   a per-user task means the assertion is in the right user's session.
# - aggressive=true (powercfg lid override) does need admin; the task runs as
#   the user who logged in, but with HighestAvailable run-level so admin users
#   keep their elevation. Non-admin users will see lid override skipped (ES_*
#   alone still prevents idle sleep).
# - No third-party deps. Just Python + this script.

param(
    [switch]$Uninstall,
    [string]$TaskName = "staywake",
    [string]$Python = "py"   # use py launcher by default; override with full path if needed
)

$ErrorActionPreference = "Stop"

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed scheduled task '$TaskName'."
    } else {
        Write-Host "Task '$TaskName' not found."
    }
    exit 0
}

# --- preflight -------------------------------------------------------------

# Verify staywake is importable for the chosen Python.
$check = & $Python -c "import staywake; print(staywake.__version__)" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Error @"
staywake is not importable for '$Python'.
Install it first:
    pip install --user staywake
or from this checkout:
    pip install --user .
"@
    exit 1
}
Write-Host "Detected staywake $check"

# State + config under %LOCALAPPDATA% / %APPDATA%
$StateDir  = Join-Path $env:LOCALAPPDATA "staywake"
$ConfigDir = Join-Path $env:APPDATA      "staywake"
$LogDir    = Join-Path $env:LOCALAPPDATA "staywake\logs"
New-Item -ItemType Directory -Force -Path $StateDir, $ConfigDir, $LogDir | Out-Null

$StatePath  = Join-Path $StateDir  "holders.json"
$ConfigPath = Join-Path $ConfigDir "config.toml"
$LogPath    = Join-Path $LogDir    "staywake.log"

# --- register scheduled task ----------------------------------------------

$arguments = "-m staywake.cli daemon --state-path `"$StatePath`" --config-path `"$ConfigPath`""

$action  = New-ScheduledTaskAction -Execute $Python -Argument $arguments
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "staywake — keep system awake while AI agents are working" | Out-Null

Start-ScheduledTask -TaskName $TaskName

Write-Host ""
Write-Host "Installed scheduled task '$TaskName'."
Write-Host "  python:      $Python"
Write-Host "  state path:  $StatePath"
Write-Host "  config path: $ConfigPath"
Write-Host "  log:         $LogPath  (also via Get-WinEvent if you prefer)"
Write-Host ""
Write-Host "Try it:"
Write-Host "  & $Python -m staywake.cli hold demo --reason test"
Write-Host "  & $Python -m staywake.cli status"
Write-Host "  & $Python -m staywake.cli release demo"
