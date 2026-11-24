# Build the Windows installer and prove it matches the version in the tree.
#
#   .\scripts\build_installer.ps1              # pyinstaller + web assets + ISCC + check
#   .\scripts\build_installer.ps1 -SkipBuild   # reuse dist\Rhapsode, just repackage
#
# The check exists because a stale installer silently shadowed a newer one once:
# the version bump renamed the output (Rhapsode-0.2.0-alpha-Setup.exe) while the
# install step kept running the old file name, so an "install" reinstalled the
# previous build and every test passed against the wrong binary.
param(
    [switch]$SkipBuild
)
$ErrorActionPreference = 'Stop'
Set-Location "$PSScriptRoot\.."

function Fail($m) { "!! $m"; exit 1 }
function Say($m) { "   $m" }

function Invoke-Native($exe, [string[]]$nativeArgs, $log) {
    # Windows PowerShell turns a native program's stderr into a terminating
    # error while $ErrorActionPreference is Stop - PyInstaller logs to stderr.
    # Start-Process keeps the two streams in files and reports only an exit
    # code, so the build is judged on that code and nothing else.
    $err = [System.IO.Path]::ChangeExtension($log, '.err.log')
    $proc = Start-Process -FilePath $exe -ArgumentList $nativeArgs -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput $log -RedirectStandardError $err
    $code = $proc.ExitCode
    if (Test-Path $err) {
        Add-Content -Path $log -Value (Get-Content $err)
        Remove-Item $err -Force
    }
    return $code
}

# ---- version, from the one place the app and the installer both read ----------
# A checkout has .venv; a clean CI runner installs into the system interpreter.
$venvPython = '.venv\Scripts\python.exe'
if (Test-Path $venvPython) {
    $python = (Resolve-Path $venvPython).Path
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $python = (Get-Command python).Source
} else {
    Fail 'no Python: create .venv or put python on PATH'
}
$version = (& $python -c "import murmur; print(murmur.__version__)" 2>$null | Select-Object -Last 1)
if (-not $version) { Fail 'could not read murmur.__version__' }
$version = $version.Trim()
Say "version: $version"

# ---- the Windows file properties, from the same version -----------------------
# packaging/version_info.txt is static, and it had drifted to 0.2.0-alpha while the
# app was on 0.3.x - the exe's Properties dialog showed the old numbers. Rewrite it
# from murmur.__version__ on every build. No BOM: PyInstaller reads it as UTF-8.
$quad = $version.Split('-')[0]
$parts = @($quad.Split('.'))
while ($parts.Count -lt 4) { $parts += '0' }
$versionTuple = ($parts[0..3] -join ', ')
$fileVersion = ($parts[0..3] -join '.')
$infoPath = Join-Path $PWD 'packaging\version_info.txt'
$info = [System.IO.File]::ReadAllText($infoPath)
$info = [regex]::Replace($info, 'filevers=\(\d+, \d+, \d+, \d+\)', "filevers=($versionTuple)")
$info = [regex]::Replace($info, 'prodvers=\(\d+, \d+, \d+, \d+\)', "prodvers=($versionTuple)")
$info = [regex]::Replace($info, 'StringStruct\("FileVersion", "[^"]*"\)', "StringStruct(`"FileVersion`", `"$fileVersion`")")
$info = [regex]::Replace($info, 'StringStruct\("ProductVersion", "[^"]*"\)', "StringStruct(`"ProductVersion`", `"$version`")")
[System.IO.File]::WriteAllText($infoPath, $info, (New-Object System.Text.UTF8Encoding($false)))
Say "file properties: $fileVersion / $version"

# ---- build --------------------------------------------------------------------
if (-not $SkipBuild) {
    $venvPyinstaller = '.venv\Scripts\pyinstaller.exe'
    if (Test-Path $venvPyinstaller) {
        $pyinstaller = (Resolve-Path $venvPyinstaller).Path
    } elseif (Get-Command pyinstaller -ErrorAction SilentlyContinue) {
        $pyinstaller = (Get-Command pyinstaller).Source
    } else {
        Fail 'pyinstaller is missing: uv pip install -r requirements-build.txt'
    }

    # The installer promises 54 voices in 9 languages, Japanese and Chinese included. Both
    # need the optional language extra (requirements-lang.txt); a build without it would
    # ship a binary whose picker greys two languages out. Fail here, not in the release.
    # This runs after the pyinstaller lookup deliberately: inside that else it only ever
    # ran after Fail had exited, so it never protected a real build.
    & $python -c "import importlib.util, sys; missing=[n for n in ('misaki','pyopenjtalk') if importlib.util.find_spec(n) is None]; print('missing ' + ' '.join(missing) if missing else 'language extra present'); sys.exit(1 if missing else 0)"
    if ($LASTEXITCODE -ne 0) {
        Fail 'the language extra is missing (uv pip install -r requirements-lang.txt) - the installer must ship Japanese and Chinese'
    }
    Say 'pyinstaller (a few minutes)'
    $buildLog = "$env:TEMP\pyi_build.log"
    $code = Invoke-Native $pyinstaller @('--noconfirm', '--clean', 'packaging\Rhapsode-win.spec') $buildLog
    if ($code) { Fail "pyinstaller failed ($code), see $buildLog" }
    # OpenCV ships a 39 MB FFmpeg videoio DLL that nothing here calls: the app uses
    # FaceDetectorYN, resize and a log-level call, all measured to work without it. It is
    # also LGPL, so shipping it bought a licence obligation and no feature.
    $videoio = Get-ChildItem 'dist\Rhapsode\_internal\cv2' -Filter 'opencv_videoio_ffmpeg*.dll' -ErrorAction SilentlyContinue
    foreach ($file in $videoio) {
        $mb = [math]::Round($file.Length / 1MB, 1)
        Remove-Item $file.FullName -Force
        Say "dropped $($file.Name) ($mb MB, unused)"
    }

    Say 'web assets'
    $copyLog = "$env:TEMP\robocopy.log"
    $code = Invoke-Native robocopy @('web', 'dist\Rhapsode\_internal\web', '/MIR', '/NFL', '/NDL', '/NJH', '/NJS', '/NP') $copyLog
    if ($code -gt 7) { Fail "robocopy failed ($code), see $copyLog" }
} elseif (-not (Test-Path 'dist\Rhapsode\Rhapsode.exe')) {
    Fail 'dist\Rhapsode is missing and -SkipBuild was given'
}

# ---- package ------------------------------------------------------------------
$iscc = @(
    'C:\Program Files\Inno Setup 7\ISCC.exe',
    'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
    'C:\Program Files\Inno Setup 6\ISCC.exe'
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { Fail 'ISCC.exe not found: install Inno Setup 6 or 7' }
Say "compiling with $iscc"
$isccLog = "$env:TEMP\iscc_build.log"
$code = Invoke-Native $iscc @("/DAppVersion=$version", 'packaging\Rhapsode-setup.iss') $isccLog
if ($code) { Fail "ISCC failed ($code), see $isccLog" }

# ---- prove the artifact is this version ---------------------------------------
$expected = "dist\Rhapsode-$version-Setup.exe"
if (-not (Test-Path $expected)) { Fail "expected $expected and it is not there" }
$stamp = (Get-Item $expected).LastWriteTime
if ($stamp -lt (Get-Date).AddMinutes(-30)) {
    Fail "$expected was not written by this run (older than 30 minutes) - is AppVersion in the .iss still $version?"
}
Say "installer: $expected ($([math]::Round((Get-Item $expected).Length/1MB,1)) MB, $stamp)"

# An older installer left in dist is a foot-gun for anybody copying "the" setup exe.
Get-ChildItem dist -Filter 'Rhapsode-*-Setup.exe' | Where-Object { $_.FullName -ne (Resolve-Path $expected).Path } |
    ForEach-Object { Say "note: older artifact still present: $($_.Name)" }
Say 'done'
