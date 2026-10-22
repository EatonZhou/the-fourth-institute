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
    $quotedPython = $python.Replace("'", "''")
    $sha = $env:GITHUB_SHA
    $script = @"
`$ErrorActionPreference = 'Stop'
Set-Location `$PSScriptRoot
`$env:PYTHONUTF8 = '1'
`$env:GITHUB_SHA = '$sha'
New-Item -ItemType Directory evidence | Out-Null
`$ErrorActionPreference = 'Continue'
& '$quotedPython' native_smoke.py --installer Setup.exe --work installations --evidence evidence *> evidence/smoke.log
`$code = `$LASTEXITCODE
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
        -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $entry + '"')) -PassThru
    if (-not $process.WaitForExit(1500000)) { throw 'Standard-user native smoke exceeded 25 minutes' }
    $result = $process.ExitCode
} finally {
    if (Test-Path "$fixture/evidence") {
        Copy-Item "$fixture/evidence/*" $evidencePath -Force
    }
    Remove-LocalUser -Name $username
}
Get-Content (Join-Path $evidencePath 'smoke.log') -Encoding utf8
exit $result
