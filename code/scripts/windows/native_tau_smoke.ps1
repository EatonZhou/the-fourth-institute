# CI infrastructure validation only. The scheduled process escapes Actions'
# enclosing Job, as the desktop installation does. No Portal account or model.
param([Parameter(Mandatory)][string]$Evidence)
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true') { throw 'Disposable Actions fixture only' }
$repo = (Resolve-Path "$PSScriptRoot/../../..").Path
$python = (Get-Command python).Source
$evidencePath = [IO.Path]::GetFullPath($Evidence)
New-Item -ItemType Directory -Force $evidencePath | Out-Null
$taskName = 'TFI-tau-native-' + $env:GITHUB_RUN_ID + '-' + $env:GITHUB_RUN_ATTEMPT
$bootstrap = Join-Path $evidencePath 'run-native.ps1'
# Use the CI account's S4U local token. No password, saved credential or network
# authentication is required; dependencies are installed before starting it.
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$escape = { param($value) $value.Replace("'", "''") }
$code = & $escape (Join-Path $repo 'code')
$binary = & $escape $python
$output = & $escape $evidencePath
$path = & $escape $env:PATH
$browsers = & $escape $env:PLAYWRIGHT_BROWSERS_PATH
@"
`$ErrorActionPreference = 'Stop'
try {
    Set-Location '$code'
    `$env:PATH = '$path'
    `$env:PYTHONUTF8 = '1'
    `$env:PLAYWRIGHT_BROWSERS_PATH = '$browsers'
    & '$binary' -m unittest tests.cases.test_windows_tau_executor -v *> '$output/native-tests.log'
    `$result = `$LASTEXITCODE
    @{ exit_code=`$result; platform='Windows'; campaign_started=`$false; real_model_used=`$false } | ConvertTo-Json | Set-Content '$output/native-result.json' -Encoding utf8
    exit `$result
} catch {
    `$_ | Out-String | Set-Content '$output/native-error.log' -Encoding utf8
    @{ exit_code=1; campaign_started=`$false } | ConvertTo-Json | Set-Content '$output/native-result.json' -Encoding utf8
    exit 1
}
"@ | Set-Content $bootstrap -Encoding utf8
try {
    $action = New-ScheduledTaskAction -Execute (Get-Command pwsh).Source -Argument ('-NoProfile -NonInteractive -File "' + $bootstrap + '"') -WorkingDirectory $repo
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 8) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $taskName -InputObject (New-ScheduledTask -Action $action -Principal $principal -Settings $settings) -Force | Out-Null
    Export-ScheduledTask -TaskName $taskName | Set-Content (Join-Path $evidencePath 'task-definition.log')
    Start-ScheduledTask -TaskName $taskName
    $deadline = (Get-Date).AddMinutes(8)
    while (-not (Test-Path "$evidencePath/native-result.json")) {
        if ((Get-Date) -gt $deadline) { throw 'Native test task did not finish; no TAU result exists' }
        Start-Sleep -Seconds 2
    }
    Get-Content "$evidencePath/native-tests.log" -ErrorAction Continue
    $result = Get-Content "$evidencePath/native-result.json" -Raw | ConvertFrom-Json
    if ($result.exit_code -ne 0) { throw 'Native Windows TAU infrastructure tests failed' }
} finally {
    Get-ScheduledTaskInfo -TaskName $taskName -ErrorAction Continue | Select-Object LastRunTime, LastTaskResult | ConvertTo-Json | Set-Content (Join-Path $evidencePath 'task-final.json')
    Stop-ScheduledTask -TaskName $taskName -ErrorAction Continue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction Continue
}
