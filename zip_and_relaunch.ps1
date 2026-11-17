$ErrorActionPreference = 'Stop'
$dist = 'C:\Apps\Rhapsode\dist\Rhapsode'
$zip = 'C:\Apps\Rhapsode\dist\Rhapsode-0.2.0-alpha-win-x64.zip'

Stop-Process -Name Rhapsode -Force -EA SilentlyContinue
Start-Sleep 3
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path $dist -DestinationPath $zip -CompressionLevel Fastest
"zip MB: $([math]::Round((Get-Item $zip).Length / 1MB))"

Start-Process "$dist\Rhapsode.exe"
"app relaunched"
