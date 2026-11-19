# Package the Rhapsode source (no venv, no build output) into a tarball.
$ErrorActionPreference = 'Stop'
$root = 'C:\Apps\Rhapsode'
$stage = 'C:\Users\corte\AppData\Local\Temp\opencode\rhapsode-src'
$version = (& 'C:\Apps\Rhapsode\.venv\Scripts\python.exe' -c "import murmur; print(murmur.__version__)").Trim()
$out = "C:\Apps\Rhapsode\dist\rhapsode-$version-win-source.tar.gz"

if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
robocopy $root $stage /E /XD .venv dist build __pycache__ .pytest_cache .git /XF *.log *.txt.bak t1_err.txt t2_err.txt dbg_err.txt selftest_err.txt selftest_out.txt /NFL /NDL /NJH /NJS | Out-Null

# keep the packaging notes but drop transient probe files
Get-ChildItem $stage -Filter '*_err.txt' -EA SilentlyContinue | Remove-Item -Force
Get-ChildItem $stage -Directory -Filter '__pycache__' -Recurse -EA SilentlyContinue | Remove-Item -Recurse -Force

if (Test-Path $out) { Remove-Item $out }
tar -czf $out -C $stage .
"tar: $([math]::Round((Get-Item $out).Length / 1MB, 2)) MB -> $out"
"files: $((tar -tzf $out | Measure-Object).Count)"
Remove-Item $stage -Recurse -Force
