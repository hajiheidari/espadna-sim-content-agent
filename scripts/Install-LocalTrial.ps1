param(
    [Parameter(Mandatory=$true)][string]$ConfigPath,
    [Parameter(Mandatory=$true)][string]$PythonwPath,
    [Parameter(Mandatory=$true)][DateTimeOffset]$FirstRun,
    [ValidateSet('delivery','bot')][string]$Mode = 'delivery',
    [string]$TaskName = 'EspadnaSim-PrivateDelivery-Trial',
    [switch]$Remove
)
$ErrorActionPreference = 'Stop'
$configFull = (Resolve-Path -LiteralPath $ConfigPath).Path
$pythonFull = (Resolve-Path -LiteralPath $PythonwPath).Path
$module = if ($Mode -eq 'bot') { 'espadna.local_bot' } else { 'espadna.local_worker' }
$arguments = '-m ' + $module + ' --config "' + $configFull + '"'
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    $actions = @($existing.Actions)
    if ($actions.Count -ne 1 -or $actions[0].Execute -ne $pythonFull -or $actions[0].Arguments -ne $arguments) {
        throw 'Existing task belongs to a different action; nothing was changed.'
    }
}
if ($Remove) {
    if ($existing) { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false }
    Write-Output 'Project trial task removed; private data and credentials preserved.'
    exit
}
$config = Get-Content -LiteralPath $configFull -Raw | ConvertFrom-Json
if ($config.mode -ne 'telegram' -or $config.allow_network -ne $true -or $config.owner_only -ne $true) {
    throw 'Only an explicitly enabled owner-only trial config can be scheduled.'
}
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $pythonFull -Argument $arguments -WorkingDirectory (Split-Path -Parent $configFull)
$trigger = New-ScheduledTaskTrigger -Once -At $FirstRun.LocalDateTime -RepetitionInterval (New-TimeSpan -Minutes 1)
$trigger.Repetition.StopAtDurationEnd = $false
$timeLimit = if ($Mode -eq 'bot') { [TimeSpan]::Zero } else { New-TimeSpan -Minutes 5 }
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -Hidden -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit $timeLimit
$principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'Owner-only Espadna Sim private delivery trial; no public channel.' -Force | Out-Null
Write-Output 'Project trial task registered. First check uses the supplied local time; later checks run every minute without a visible console.'
