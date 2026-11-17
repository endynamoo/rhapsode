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

# ---- version, from the one place the app and the installer both read ----------
$version = (& '.venv\Scripts\python.exe' -c "import murmur; print(murmur.__version__)" 2>$null | Select-Object -Last 1)
if (-not $version) { Fail 'could not read murmur.__version__' }
$version = $version.Trim()
Say "version: $version"

# ---- build --------------------------------------------------------------------
if (-not $SkipBuild) {
    Say 'pyinstaller (a few minutes)'
    & '.venv\Scripts\pyinstaller.exe' --noconfirm --clean packaging\Rhapsode-win.spec *> "$env:TEMP\pyi_build.log"
    if ($LASTEXITCODE) { Fail "pyinstaller failed, see $env:TEMP\pyi_build.log" }
    Say 'web assets'
    robocopy web 'dist\Rhapsode\_internal\web' /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
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
& $iscc packaging\Rhapsode-setup.iss *> "$env:TEMP\iscc_build.log"
if ($LASTEXITCODE) { Fail "ISCC failed, see $env:TEMP\iscc_build.log" }

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
