# Disposable CI only: exercise the desktop product with a real standard user.
param([Parameter(Mandatory)][string]$Installer, [Parameter(Mandatory)][string]$Evidence)
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true') { throw 'This fixture creates a disposable CI user; run in Actions only.' }
$username = 'tfi_native_test'
$python = (Get-Command python).Source
$fixture = Join-Path $env:PUBLIC ("TFI-native-" + $env:GITHUB_RUN_ID + '-' + $env:GITHUB_RUN_ATTEMPT)
$evidencePath = [IO.Path]::GetFullPath($Evidence)
New-Item -ItemType Directory -Force $evidencePath | Out-Null
New-Item -ItemType Directory $fixture | Out-Null
$password = ConvertTo-SecureString (([Guid]::NewGuid().ToString('N')) + 'aA9!') -AsPlainText -Force
$user = New-LocalUser -Name $username -Password $password -AccountNeverExpires
$result = 1
try {
    Add-LocalGroupMember -SID 'S-1-5-32-545' -Member $user
    $sid = $user.SID.Value
    & icacls $fixture /grant "*${sid}:(OI)(CI)M" /Q | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot grant the fixture to the test user' }
    & icacls (Split-Path $python) /grant "*${sid}:(OI)(CI)RX" /T /Q | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot grant the CI Python runtime to the test user' }
    Copy-Item $Installer (Join-Path $fixture 'Setup.exe')
    Copy-Item "$PSScriptRoot/native_smoke.py" (Join-Path $fixture 'native_smoke.py')
    Copy-Item "$PSScriptRoot/native_cli_smoke.py" (Join-Path $fixture 'native_cli_smoke.py')
    $quotedPython = $python.Replace("'", "''")
    $sha = $env:GITHUB_SHA
    $packageSha = (Get-Content (Join-Path (Split-Path $Installer) 'receipt.json') -Raw | ConvertFrom-Json).source_commit
    if ($packageSha -notmatch '^[0-9a-f]{40}$') { throw 'Missing package source commit' }
    $script = @"
`$ErrorActionPreference = 'Stop'
Set-Location `$PSScriptRoot
`$env:PYTHONUTF8 = '1'
`$env:GITHUB_SHA = '$sha'
`$env:TFI_PACKAGE_SHA = '$packageSha'
`$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
`$profile = (Get-ItemProperty ("HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\" + `$sid)).ProfileImagePath
if (-not `$profile) { throw 'The standard user has no loaded profile' }
`$env:USERPROFILE = [Environment]::ExpandEnvironmentVariables(`$profile)
`$env:LOCALAPPDATA = Join-Path `$env:USERPROFILE 'AppData\Local'
`$env:APPDATA = Join-Path `$env:USERPROFILE 'AppData\Roaming'
`$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(`$false)
`$env:TEMP = Join-Path `$env:LOCALAPPDATA 'Temp'
`$env:TMP = `$env:TEMP
New-Item -ItemType Directory -Force `$env:TEMP | Out-Null
New-Item -ItemType Directory -Force `$env:APPDATA | Out-Null
New-Item -ItemType Directory evidence | Out-Null
`$ErrorActionPreference = 'Continue'
& '$quotedPython' native_smoke.py --installer Setup.exe --work installations --evidence evidence 2>&1 | Out-File evidence/smoke.log -Encoding utf8
`$code = `$LASTEXITCODE
if (`$code -eq 0) {
    `$installedCode = Get-ChildItem installations -Directory | Select-Object -First 1 | ForEach-Object { Join-Path `$_.FullName 'code' }
    & '$quotedPython' native_cli_smoke.py --code `$installedCode --work 'CLI 中文 空格' --evidence evidence 2>&1 | Out-File evidence/native-cli.log -Encoding utf8
    `$code = `$LASTEXITCODE
}
Get-ChildItem installations -Filter *.log -Recurse -ErrorAction SilentlyContinue |
    Where-Object { `$_.Directory.Name -eq 'runtime' } | ForEach-Object {
        `$case = if (`$_.FullName -match 'Lenia') { 'full' } else { 'base' }
        Get-Content `$_.FullName -Tail 80 -Encoding utf8 |
            Set-Content (Join-Path 'evidence' (`$case + '-component-' + `$_.Name)) -Encoding utf8
    }
exit `$code
"@
    $entry = Join-Path $fixture 'run.ps1'
    Set-Content $entry $script -Encoding utf8
    $credential = [PSCredential]::new("$env:COMPUTERNAME\$username", $password)
    $process = Start-Process "$env:SystemRoot/System32/WindowsPowerShell/v1.0/powershell.exe" `
        -Credential $credential -LoadUserProfile -WorkingDirectory $fixture `
        -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $entry + '"')) `
        -RedirectStandardOutput "$fixture/bootstrap.log" -RedirectStandardError "$fixture/bootstrap-error.log" -PassThru
    if (-not $process.WaitForExit(2400000)) { throw 'Standard-user native smoke exceeded 40 minutes' }
    $result = $process.ExitCode
    if ($null -eq $result) { throw 'Native test process has no exit code' }
} catch {
    $_.ToString() | Set-Content (Join-Path $evidencePath 'fixture-error.log') -Encoding utf8
    throw
} finally {
    if (Test-Path "$fixture/evidence") {
        Copy-Item "$fixture/evidence/*" $evidencePath -Force
    }
    Get-ChildItem "$fixture/bootstrap*.log" -ErrorAction SilentlyContinue | Copy-Item -Destination $evidencePath -Force
    Remove-LocalUser -Name $username
}
if (Test-Path (Join-Path $evidencePath 'smoke.log')) {
    Get-Content (Join-Path $evidencePath 'smoke.log') -Encoding utf8
} else { throw "Native smoke did not produce a log (process exit $result); see bootstrap diagnostics" }
exit $result
