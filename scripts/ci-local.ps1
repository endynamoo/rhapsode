# Run the GitHub workflows locally.
#
#   .\scripts\ci-local.ps1                    # every job this host can run
#   .\scripts\ci-local.ps1 -Job unit,ui       # just those
#   .\scripts\ci-local.ps1 -Job installer -SkipBuild
#   .\scripts\ci-local.ps1 -Job installer -Setup dist\Rhapsode-0.3.3-beta-Setup.exe
#
# Mirrors .github/workflows/, job for job:
#   windows.yml   -> unit       the suite, in a venv built from requirements-test.txt
#   linux.yml ui  -> ui         the browser journeys, in a real browser
#   linux.yml run -> run        a real server started the README way, asked for health
#   installer.yml -> installer  build, install into a temp dir, health, uninstall
#   macos.yml     -> macos      impossible off a Mac; reported as skipped, not passed
#
# Exists because Actions minutes can run out and a release still has to be
# verified. Nothing here is a substitute for the real runner's clean machine: it
# uses this box's Edge, this box's ffmpeg, and this box's GPU. The macOS job it
# cannot do at all, and says so.
param(
    [ValidateSet('unit', 'ui', 'run', 'installer', 'macos')]
    [string[]]$Job = @('unit', 'ui', 'run', 'installer', 'macos'),
    [switch]$SkipBuild,
    [string]$Setup,
    [string]$CiVenv = (Join-Path $env:TEMP 'rhapsode-ci-venv')
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location $root
$logDir = Join-Path $env:TEMP 'rhapsode-ci-local'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# Write-Host, not the pipeline: these functions return a boolean, and anything
# written to the pipeline would be collected into it - a failing job whose
# diagnostics were piped would read as passed.
function Say($m) { Write-Host "   $m" }
function Head($m) { Write-Host ""; Write-Host "== $m" }

function Invoke-Native($exe, [string[]]$nativeArgs, [string]$log) {
    # Native stderr becomes a terminating error while $ErrorActionPreference is
    # Stop, and pytest/unittest/pyinstaller all write progress there. Judge on the
    # exit code only.
    $err = [System.IO.Path]::ChangeExtension($log, '.err.log')
    $proc = Start-Process -FilePath $exe -ArgumentList $nativeArgs -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput $log -RedirectStandardError $err
    if (Test-Path $err) {
        Add-Content -Path $log -Value (Get-Content $err)
        Remove-Item $err -Force
    }
    return $proc.ExitCode
}

function Wait-Health([string]$url, [int]$seconds, $proc) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 700
        try { return Invoke-RestMethod $url -TimeoutSec 3 } catch {}
        if ($proc -and $proc.HasExited) { return $null }
    }
    return $null
}

function Get-Python {
    # Prefer the project venv (it has the runtime requirements); fall back to the
    # interpreter on PATH, which is what a CI runner would use.
    $venv = Join-Path $root '.venv\Scripts\python.exe'
    if (Test-Path $venv) { return (Resolve-Path $venv).Path }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw 'no Python: create .venv or put python on PATH'
}

# ---------------------------------------------------------------- jobs ---------

function Invoke-Unit {
    # The runner installs requirements-test.txt into a bare interpreter, so do the
    # same: a missing dependency must fail here, not silently skip a test.
    if (-not (Test-Path (Join-Path $CiVenv 'Scripts\python.exe'))) {
        Say "creating $CiVenv"
        $seed = Get-Command py -ErrorAction SilentlyContinue
        if ($seed) { $code = Invoke-Native 'py' @('-3.12', '-m', 'venv', $CiVenv) (Join-Path $logDir 'venv.log') }
        else { $code = Invoke-Native (Get-Python) @('-m', 'venv', $CiVenv) (Join-Path $logDir 'venv.log') }
        if ($code) { return $false }
    }
    $py = Join-Path $CiVenv 'Scripts\python.exe'
    Say 'pip install -r requirements-test.txt'
    $code = Invoke-Native $py @('-m', 'pip', 'install', '-q', '-r', 'requirements-test.txt') (Join-Path $logDir 'pip.log')
    if ($code) { Say "pip failed ($code), see $logDir\pip.log"; return $false }

    Say 'unittest discover -s tests -v'
    $log = Join-Path $logDir 'unit.log'
    $code = Invoke-Native $py @('-m', 'unittest', 'discover', '-s', 'tests', '-v') $log
    Get-Content $log | Select-String -Pattern '^Ran |^OK|^FAILED' | ForEach-Object { Write-Host "   $($_.Line)" }
    if ($code) { Say "see $log" }
    return ($code -eq 0)
}

function Invoke-Ui {
    $py = Get-Python
    $log = Join-Path $logDir 'ui.log'
    $code = Invoke-Native $py @('-c', 'import playwright') $log
    if ($code) {
        Say 'installing playwright'
        $code = Invoke-Native $py @('-m', 'pip', 'install', '-q', '"playwright>=1.47"') $log
        if ($code) { return $false }
    }
    # Chromium is a large download and may be blocked; Edge is already here.
    $edge = @(
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
        "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    $had = $env:PLAYWRIGHT_CHANNEL
    if ($edge) { $env:PLAYWRIGHT_CHANNEL = 'msedge'; Say "browser: $edge" } else { Say 'browser: bundled Chromium' }
    try {
        Say 'unittest tests.ui.test_smoke -v'
        $code = Invoke-Native $py @('-m', 'unittest', 'tests.ui.test_smoke', '-v') $log
        Get-Content $log | Select-String -Pattern '^Ran |^OK|^FAILED' | ForEach-Object { Write-Host "   $($_.Line)" }
    } finally {
        if ($null -eq $had) { Remove-Item Env:\PLAYWRIGHT_CHANNEL -ErrorAction SilentlyContinue } else { $env:PLAYWRIGHT_CHANNEL = $had }
    }
    if ($code) { Say "see $log" }
    return ($code -eq 0)
}

function Invoke-Run {
    # linux.yml starts the server the README way and asks it for health. Same
    # shape here, except the OCR backend is this platform's (windows/macos, not
    # tesseract), which is the only part of that job that is Linux-specific.
    $py = Get-Python
    $dataHome = Join-Path $env:TEMP ("rh-ci-run-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Force -Path $dataHome | Out-Null
    $port = 8879
    $old = @{ HOME = $env:MURMUR_HOME; PORT = $env:MURMUR_PORT; WARM = $env:MURMUR_WARM_STILLS }
    $env:MURMUR_HOME = $dataHome
    $env:MURMUR_PORT = "$port"
    $env:MURMUR_WARM_STILLS = '0'
    $serverLog = Join-Path $logDir 'server.log'
    $serverErr = Join-Path $logDir 'server.err.log'
    try {
        Say "starting python -m murmur on $port (home $dataHome)"
        $proc = Start-Process -FilePath $py -ArgumentList @('-m', 'murmur') -NoNewWindow -PassThru `
            -RedirectStandardOutput $serverLog -RedirectStandardError $serverErr
        $health = Wait-Health "http://127.0.0.1:$port/api/health" 150 $proc
        if (-not $health) {
            Say 'the server did not answer /api/health'
            if (Test-Path $serverLog) { Get-Content $serverLog -Tail 25 | ForEach-Object { Write-Host "   $_" } }
            if (Test-Path $serverErr) { Get-Content $serverErr -Tail 25 | ForEach-Object { Write-Host "   $_" } }
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
            return $false
        }
        Say "health ok=$($health.ok) ocr=$($health.ocr) backend=$($health.ocrBackend) modelReady=$($health.modelReady)"
        $network = Invoke-RestMethod "http://127.0.0.1:$port/api/network" -TimeoutSec 5
        Say "network enabled=$($network.enabled) remoteAccess=$($network.remoteAccess)"
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        if (-not $health.ok) { return $false }
        if ($network.enabled -or $network.remoteAccess) { Say 'remote access must stay off by default'; return $false }
        return $true
    } finally {
        if ($null -eq $old.HOME) { Remove-Item Env:\MURMUR_HOME -ErrorAction SilentlyContinue } else { $env:MURMUR_HOME = $old.HOME }
        if ($null -eq $old.PORT) { Remove-Item Env:\MURMUR_PORT -ErrorAction SilentlyContinue } else { $env:MURMUR_PORT = $old.PORT }
        if ($null -eq $old.WARM) { Remove-Item Env:\MURMUR_WARM_STILLS -ErrorAction SilentlyContinue } else { $env:MURMUR_WARM_STILLS = $old.WARM }
    }
}

function Invoke-Installer {
    $setup = $Setup
    if (-not $setup) {
        if ($SkipBuild -and (Test-Path 'dist\Rhapsode\Rhapsode.exe')) {
            Say 'reusing dist\Rhapsode (-SkipBuild)'
        } else {
            Head 'build_installer.ps1'
            $code = Invoke-Native 'powershell' @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', 'scripts\build_installer.ps1') (Join-Path $logDir 'installer-build.log')
            if ($code) { Say "build failed ($code), see $logDir\installer-build.log"; return $false }
        }
        $setup = (Get-ChildItem dist -Filter 'Rhapsode-*-Setup.exe' |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
    }
    if (-not $setup -or -not (Test-Path $setup)) { Say 'no Setup.exe found'; return $false }
    Say "installer: $setup ($([math]::Round((Get-Item $setup).Length / 1MB, 1)) MB)"

    $app = Join-Path $env:TEMP ('rh-ci-install-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Force -Path $app | Out-Null
    $code = (Start-Process -FilePath $setup -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/DIR=$app" -Wait -PassThru).ExitCode
    if ($code) { Say "installer exited $code"; return $false }

    $exe = Join-Path $app 'Rhapsode.exe'
    if (-not (Test-Path $exe)) { Say "no Rhapsode.exe in $app"; return $false }
    Say "installed: $((Get-Item $exe).Length) bytes at $exe"
    $engine = Join-Path $app 'scripts\setup_engine.ps1'
    if (-not (Test-Path $engine)) { Say 'the engine script was not shipped'; return $false }
    # The licence folder is the compliance artefact; an installer without it is a
    # GPL-3.0 distribution missing its notices.
    $notice = Join-Path $app 'licenses\NOTICE.txt'
    $sources = Join-Path $app 'licenses\SOURCES.md'
    if (-not (Test-Path $notice) -or -not (Test-Path $sources)) { Say 'licenses\ was not installed'; return $false }
    Say 'licenses\NOTICE.txt and licenses\SOURCES.md are installed'

    $dataHome = Join-Path $app '_data'
    $oldHome = $env:MURMUR_HOME
    $env:MURMUR_HOME = $dataHome
    try {
        Say 'setup_engine.ps1 -Profile cpu -SkipWeights -DryRun'
        $code = Invoke-Native 'powershell' @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $engine, '-Profile', 'cpu', '-SkipWeights', '-DryRun') (Join-Path $logDir 'engine.log')
        if ($code) { Say "setup_engine.ps1 exited $code"; return $false }

        Say 'starting the installed app on 8877'
        $proc = Start-Process -FilePath $exe -ArgumentList '--port', '8877' -PassThru
        $health = Wait-Health 'http://127.0.0.1:8877/api/health' 90 $proc
        if (-not $health) {
            $appLog = Join-Path $dataHome 'app.log'
            if (Test-Path $appLog) { Get-Content $appLog -Tail 25 | ForEach-Object { Write-Host "   $_" } }
            Say 'the installed app did not report healthy'
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
            return $false
        }
        Say "health ok=$($health.ok) profile=$($health.profile.name) ocr=$($health.ocr)"
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    } finally {
        if ($null -eq $oldHome) { Remove-Item Env:\MURMUR_HOME -ErrorAction SilentlyContinue } else { $env:MURMUR_HOME = $oldHome }
    }

    $uninst = Join-Path $app 'unins000.exe'
    if (-not (Test-Path $uninst)) { Say 'no uninstaller'; return $false }
    Start-Sleep -Seconds 2
    & $uninst /VERYSILENT /SUPPRESSMSGBOXES /NORESTART | Out-Null
    Start-Sleep -Seconds 3
    if (Test-Path $exe) { Say 'the app survived uninstall'; return $false }
    Say 'uninstalled cleanly'
    return $true
}

function Invoke-Macos {
    if (-not ($PSVersionTable.Platform -eq 'Unix' -and (Test-Path '/usr/bin/sw_vers'))) {
        Say 'macos.yml needs a Mac: build_app.sh uses sips, .icns and macOS PyInstaller settings'
        return $null   # skipped, not passed
    }
    $code = Invoke-Native 'bash' @('./build_app.sh') (Join-Path $logDir 'macos.log')
    if ($code) { return $false }
    $code = Invoke-Native './dist/Rhapsode.app/Contents/MacOS/Rhapsode' @('--selftest') (Join-Path $logDir 'selftest.log')
    Get-Content (Join-Path $logDir 'selftest.log') -Tail 3 | ForEach-Object { Write-Host "   $_" }
    return ($code -eq 0)
}

# ---------------------------------------------------------------- main ---------

$results = New-Object System.Collections.Specialized.OrderedDictionary
foreach ($name in $Job) {
    Head "$name"
    $started = Get-Date
    switch ($name) {
        'unit' { $ok = Invoke-Unit }
        'ui' { $ok = Invoke-Ui }
        'run' { $ok = Invoke-Run }
        'installer' { $ok = Invoke-Installer }
        'macos' { $ok = Invoke-Macos }
    }
    $seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 1)
    $state = if ($null -eq $ok) { 'skipped' } elseif ($ok) { 'passed' } else { 'FAILED' }
    $results[$name] = "$state $seconds s"
}

Write-Host ""
Write-Host ('{0,-10} {1}' -f 'job', 'result')
$failed = $false
foreach ($name in $results.Keys) {
    Write-Host ('{0,-10} {1}' -f $name, $results[$name])
    if ($results[$name] -like 'FAILED*') { $failed = $true }
}
Write-Host "logs: $logDir"
if ($failed) { exit 1 }
