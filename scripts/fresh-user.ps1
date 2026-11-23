# Test Rhapsode the way a stranger first meets it: a clean profile, the real
# installer, no models, no venv, no leftover settings.
#
#   .\scripts\fresh-user.ps1                 # use the newest Setup.exe in dist\
#   .\scripts\fresh-user.ps1 -SkipBuild      # do not run build_installer.ps1 first
#   .\scripts\fresh-user.ps1 -FullFirstRun   # let the ~340 MB voice-model download happen
#   .\scripts\fresh-user.ps1 -KeepSandbox    # keep the temp tree, pass or fail
#
# Why this exists: an install that works on a development machine is not evidence.
# This box has the model weights, the image venv, ffmpeg, WebView2 and a populated
# %LOCALAPPDATA%\Rhapsode; a stranger has none of them. Every run gets a brand new
# LOCALAPPDATA / APPDATA / USERPROFILE / TEMP inside one throwaway directory, installs
# the real artefact per-user, starts it, asks it what a first-time user would ask,
# quits it, uninstalls it - and then checks that nothing escaped into the real profile
# and that the registry entry it displaced was put back.
#
# Not a virtual machine. Windows 11 Home has neither Windows Sandbox nor Hyper-V, so
# the strongest unattended clean-room available here is a fresh user profile. What it
# catches: assumed paths, cached state, missing runtime dependencies, a first-run
# download that never arrives, permissions, writes outside the data directory, process
# leftovers after quit, a displaced uninstall entry. What it cannot: a missing driver,
# machine policy, or a broken Windows install.

param(
    [string]$Setup,
    [switch]$SkipBuild,
    [switch]$FullFirstRun,
    [switch]$KeepSandbox,
    [int]$Port = 8899,
    [int]$FirstRunMinutes = 12
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$root = Join-Path $env:TEMP "opencode\fresh-$stamp"
$results = New-Object System.Collections.ArrayList

function Say([string]$text) { Write-Host $text }
function Check([string]$name, [bool]$ok, [string]$detail = '') {
    [void]$results.Add([pscustomobject]@{ check = $name; ok = $ok; detail = $detail })
    $mark = if ($ok) { 'ok  ' } else { 'FAIL' }
    Write-Host ("  {0} {1}{2}" -f $mark, $name, $(if ($detail) { " - $detail" } else { '' }))
}
function Phase([string]$name, [scriptblock]$body) {
    Say "== $name"
    try { & $body } catch {
        Check "$name completed" $false "$($_.Exception.Message)"
    }
}
function Get-Health([int]$p, [int]$tries = 60) {
    for ($i = 0; $i -lt $tries; $i++) {
        try { return Invoke-RestMethod -Uri "http://127.0.0.1:$p/api/health" -TimeoutSec 5 }
        catch { Start-Sleep -Milliseconds 500 }
    }
    return $null
}
function Get-Tree([string]$path) {
    if (-not (Test-Path $path)) { return @() }
    return @(Get-ChildItem -LiteralPath $path -Recurse -File -ErrorAction SilentlyContinue |
        ForEach-Object { $_.FullName.Substring($path.Length) + '|' + $_.LastWriteTimeUtc.Ticks })
}

# ---------------------------------------------------------------- preconditions
Say "== fresh user check  ($stamp)"
$running = @(Get-Process -Name 'Rhapsode', 'fluxgen' -ErrorAction SilentlyContinue)
if ($running.Count -gt 0) {
    Say "Rhapsode is already running (pid $($running[0].Id)). Quit it first: this test must own the port and the profile."
    exit 2
}

if (-not $Setup) {
    if (-not $SkipBuild) {
        Say "== building the installer (this is the slow part)"
        Push-Location $repo
        try { & "$repo\scripts\build_installer.ps1" | Out-Null } finally { Pop-Location }
    }
    $Setup = Get-ChildItem -Path (Join-Path $repo 'dist') -Filter 'Rhapsode-*-Setup.exe' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $Setup -or -not (Test-Path $Setup)) {
    Say "No Setup.exe found. Build one with scripts\build_installer.ps1, or pass -Setup <path>."
    exit 2
}
# VersionInfo pads its strings to a fixed width: trim before comparing.
$version = (Get-Item $Setup).VersionInfo.ProductVersion.Trim()
Say "   installer: $(Split-Path -Leaf $Setup)  ($([math]::Round((Get-Item $Setup).Length / 1MB, 1)) MB, $version)"

# ---------------------------------------------------------------- the clean room
$sandbox = @{
    root     = $root
    local    = Join-Path $root 'LocalAppData'
    roaming  = Join-Path $root 'AppData'
    profile  = Join-Path $root 'UserProfile'
    temp     = Join-Path $root 'Temp'
}
New-Item -ItemType Directory -Force -Path $sandbox.local, $sandbox.roaming, $sandbox.profile,
    $sandbox.temp | Out-Null
Say "   sandbox:   $root"

$realData = Join-Path $env:LOCALAPPDATA 'Rhapsode'
$realRoaming = Join-Path $env:APPDATA 'Rhapsode'
$beforeData = Get-Tree $realData
$beforeRoaming = Get-Tree $realRoaming

# The installer writes a per-user uninstall key to the real registry - environment
# variables do not redirect that. Save it, then put it back afterwards so running
# this test cannot leave the machine describing a sandbox install that no longer
# exists.
$uninstallKeyPath = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{8D3A1C1E-6F2B-4C6D-9E4A-1F0B0A000001}_is1'
$keyBackup = Join-Path $root 'uninstall-key.reg'
$hadKey = Test-Path ("HKCU:\" + $uninstallKeyPath)
if ($hadKey) { & reg export ("HKCU\" + $uninstallKeyPath) $keyBackup /y | Out-Null }

$saved = @{
    LOCALAPPDATA = $env:LOCALAPPDATA; APPDATA = $env:APPDATA; USERPROFILE = $env:USERPROFILE
    TEMP = $env:TEMP; TMP = $env:TMP; HOMEDRIVE = $env:HOMEDRIVE; HOMEPATH = $env:HOMEPATH
}
$env:LOCALAPPDATA = $sandbox.local
$env:APPDATA = $sandbox.roaming
$env:USERPROFILE = $sandbox.profile
$env:TEMP = $sandbox.temp
$env:TMP = $sandbox.temp
$env:HOMEDRIVE = (Split-Path -Qualifier $root)
$env:HOMEPATH = $root.Substring(2)
Remove-Item Env:\MURMUR_HOME -ErrorAction SilentlyContinue
Remove-Item Env:\MURMUR_PORT -ErrorAction SilentlyContinue
Remove-Item Env:\PYTHONPATH -ErrorAction SilentlyContinue

$appProcess = $null
$exe = $null
try {
    # ------------------------------------------------------------ install
    Phase 'install (silent, per-user, no desktop icon, no stills engine)' {
        $log = Join-Path $root 'install.log'
        $target = Join-Path $root 'Programs\Rhapsode'
        $install = Start-Process -FilePath $Setup -Wait -PassThru -ArgumentList @(
            '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
            ('/LOG=' + $log), ('/DIR=' + $target),
            '/MERGETASKS=!desktopicon,!engine'
        )
        Check 'installer exit code 0' ($install.ExitCode -eq 0) "exit $($install.ExitCode)"

        # Do not assume where it went: find it, then say whether that was inside the
        # sandbox. An installer that ignores the environment is the finding.
        $found = Get-ChildItem -LiteralPath $root -Recurse -File -Filter 'Rhapsode.exe' -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if (-not $found) {
            Check 'the payload landed inside the sandbox' $false 'no Rhapsode.exe under the sandbox root'
            return
        }
        $script:exe = $found.FullName
        Check 'the payload landed inside the sandbox' $true $script:exe
        $appDir = Split-Path -Parent $script:exe
        Check 'licence notice installed' (Test-Path (Join-Path $appDir 'licenses\NOTICE.txt'))
        $bytes = (Get-ChildItem -LiteralPath $appDir -Recurse -File -ErrorAction SilentlyContinue |
            Measure-Object Length -Sum).Sum
        Check 'payload is a plausible size' ($bytes -gt 60MB) "$([math]::Round($bytes / 1MB)) MB"
        $reported = (Get-Item $script:exe).VersionInfo
        Check 'product version matches the installer' ($reported.ProductVersion -eq $version) `
            "$($reported.ProductVersion) vs $version"
    }

    if ($exe) {
        # -------------------------------------------------------- first start
        Phase "first start (a stranger's first launch)" {
            $script:appProcess = Start-Process -FilePath $exe -PassThru -ArgumentList @('--port', "$Port")
            $health = Get-Health $Port
            Check 'the server answers /api/health' ($null -ne $health)
            if (-not $health) { return }
            Check 'health.ok' ([bool]$health.ok)
            Check 'no voice model yet (a stranger has none)' (-not $health.modelReady) `
                "modelReady=$($health.modelReady)"
            $advice = "$($health.modelProblem) $($health.modelError)"
            Check 'the missing model is explained to the user' ([bool]$health.modelProblem -or [bool]$health.modelError) $advice
            # A packaged install has no interpreter: advice must name something in the UI.
            Check 'the advice is something a stranger can follow' `
                ($advice -notmatch 'python -m') $advice
            Check 'an OCR backend is available' ([bool]$health.ocr) "backend=$($health.ocrBackend)"
            $network = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/network" -TimeoutSec 20
            Check 'remote access is off by default' (-not $network.remoteAccess -and -not $network.enabled)
            Check 'the data directory is the default one' (Test-Path (Join-Path $sandbox.local 'Rhapsode')) `
                (Join-Path $sandbox.local 'Rhapsode')
        }

        # -------------------------------------------------------- first-run model
        if ($FullFirstRun) {
            Phase 'first-run download (the slow part: ~340 MB)' {
                # A stranger presses the button the Settings row offers; do the same.
                $started = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/voice-model" -Method Post -TimeoutSec 30
                Check 'the Download button starts a repair' ([bool]$started.state -or [bool]$started.started)
                $deadline = (Get-Date).AddMinutes($FirstRunMinutes)
                $ready = $false
                while ((Get-Date) -lt $deadline) {
                    $h = Get-Health $Port 6
                    if ($h -and $h.modelReady) { $ready = $true; break }
                    Start-Sleep -Seconds 5
                }
                Check 'the voice model downloaded and loaded' $ready
                if ($ready) {
                    $clip = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/tts" -Method Post `
                        -ContentType 'application/json' -TimeoutSec 180 `
                        -Body '{"text":"This is a fresh install.","voice":"af_heart"}'
                    Check 'the first sentence synthesises' (([bool]$clip.url) -and ($clip.duration -gt 0)) `
                        "$($clip.duration)s"
                }
            }
        }

        # -------------------------------------------------------- a document
        Phase 'a document from nothing' {
            $payload = @{
                title = 'Fresh Start'
                text  = 'The first sentence a stranger reads on a brand new install, with no library and no cache anywhere.'
            } | ConvertTo-Json
            $added = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/docs" -Method Post `
                -ContentType 'application/json' -Body $payload -TimeoutSec 60
            Check 'a document can be added' ([bool]$added.id) "id=$($added.id)"
            $docs = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/docs" -TimeoutSec 30
            Check 'it appears in the library' (@($docs.docs).Count -eq 1) "$(@($docs.docs).Count) document(s)"
        }

        # -------------------------------------------------------- quit
        Phase 'quit (frees the models: #96/#99, on a real install)' {
            try { Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/quit" -Method Post -TimeoutSec 20 | Out-Null }
            catch { }
            $gone = $false
            for ($i = 0; $i -lt 40; $i++) {
                if ($script:appProcess -and $script:appProcess.HasExited) { $gone = $true; break }
                Start-Sleep -Milliseconds 500
            }
            Check 'the app exits when asked' $gone
            Start-Sleep -Seconds 2
            $stragglers = @(Get-Process -Name 'Rhapsode', 'fluxgen' -ErrorAction SilentlyContinue)
            $text = ($stragglers | ForEach-Object { "$($_.ProcessName):$($_.Id)" }) -join ' '
            Check 'nothing is left running' ($stragglers.Count -eq 0) $text
        }

        # -------------------------------------------------------- uninstall
        Phase 'uninstall' {
            $entries = Get-ChildItem 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall' -ErrorAction SilentlyContinue |
                Where-Object { (Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue).DisplayName -like 'Rhapsode*' }
            if (-not $entries) {
                Check 'an uninstall entry exists' $false 'none in HKCU'
                return
            }
            $uninstaller = (Get-ItemProperty $entries[0].PSPath).UninstallString
            $uninstaller = $uninstaller.Trim('"')
            $u = Start-Process -FilePath $uninstaller -Wait -PassThru -ArgumentList @(
                '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART')
            Check 'uninstaller exit code 0' ($u.ExitCode -eq 0) "exit $($u.ExitCode)"
            Check 'the program directory is gone' (-not (Test-Path $exe))
            Check 'the library survived the uninstall' (Test-Path (Join-Path $sandbox.local 'Rhapsode'))
        }
    }
}
finally {
    foreach ($key in $saved.Keys) {
        if ($null -eq $saved[$key]) { Remove-Item "Env:\$key" -ErrorAction SilentlyContinue }
        else { Set-Item "Env:\$key" $saved[$key] }
    }
    if ($appProcess -and -not $appProcess.HasExited) { try { $appProcess.Kill() } catch { } }
    Get-Process -Name 'fluxgen' -ErrorAction SilentlyContinue | ForEach-Object { try { $_.Kill() } catch { } }

    # Put the machine's uninstall entry back exactly as it was. `reg` writes to stderr
    # for a missing key, which is fatal under $ErrorActionPreference = 'Stop', so it goes
    # through cmd where the redirection is the shell's, not PowerShell's.
    & cmd /c "reg delete `"HKCU\$uninstallKeyPath`" /f >nul 2>&1"
    if ($hadKey -and (Test-Path $keyBackup)) {
        & cmd /c "reg import `"$keyBackup`" >nul 2>&1"
    }
}

# ---------------------------------------------------------------- leakage
Phase 'did anything escape the sandbox?' {
    $newReal = @((Get-Tree $realData) | Where-Object { $beforeData -notcontains $_ })
    $newRoaming = @((Get-Tree $realRoaming) | Where-Object { $beforeRoaming -notcontains $_ })
    $realText = ($newReal | Select-Object -First 3) -join '; '
    $roamingText = ($newRoaming | Select-Object -First 3) -join '; '
    Check 'nothing written to the real Rhapsode data directory' ($newReal.Count -eq 0) $realText
    Check 'nothing written to the real Roaming directory' ($newRoaming.Count -eq 0) $roamingText
    $restored = $hadKey -eq (Test-Path ("HKCU:\" + $uninstallKeyPath))
    Check 'the uninstall entry is back the way it was' $restored
}

# ---------------------------------------------------------------- report
$failed = @($results | Where-Object { -not $_.ok })
$report = [pscustomobject]@{
    when      = (Get-Date).ToString('o')
    installer = $Setup
    version   = $version
    sandbox   = $root
    port      = $Port
    checks    = $results
    failed    = $failed.Count
}
$json = Join-Path $root 'fresh-report.json'
$report | ConvertTo-Json -Depth 5 | Set-Content -Path $json -Encoding UTF8

Say ""
Say ("{0} of {1} checks passed" -f ($results.Count - $failed.Count), $results.Count)
if ($failed.Count -gt 0) {
    Say 'failed:'
    $failed | ForEach-Object { Say ("  - {0} {1}" -f $_.check, $_.detail) }
}
Say "report: $json"
if (-not $KeepSandbox -and $failed.Count -eq 0) {
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
} else {
    Say "sandbox kept: $root"
}
if ($failed.Count -gt 0) { exit 1 }
exit 0
