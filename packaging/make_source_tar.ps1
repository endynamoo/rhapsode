# Package the source tree (no venv, no build output) into a tarball.
#
# Works from any checkout: the root is this script's parent, the staging folder is
# under the system temp directory, and the output lands in <repo>\dist.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$stage = Join-Path ([IO.Path]::GetTempPath()) 'rhapsode-src'
$dist = Join-Path $root 'dist'
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }
$version = (& $python -c "import sys; sys.path.insert(0, r'$root'); import murmur; print(murmur.__version__)").Trim()
if (-not $version) { throw "could not read murmur.__version__" }
New-Item -ItemType Directory -Force -Path $dist | Out-Null
$out = Join-Path $dist "rhapsode-$version-win-source.tar.gz"

if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
robocopy $root $stage /E /XD .venv dist build __pycache__ .pytest_cache .git image-venv /XF *.log *.part /NFL /NDL /NJH /NJS | Out-Null
Get-ChildItem $stage -Directory -Filter '__pycache__' -Recurse -EA SilentlyContinue | Remove-Item -Recurse -Force

if (Test-Path $out) { Remove-Item $out }
tar -czf $out -C $stage .
"tar: $([math]::Round((Get-Item $out).Length / 1MB, 2)) MB -> $out"
"files: $((tar -tzf $out | Measure-Object).Count)"
Remove-Item $stage -Recurse -Force
