$ErrorActionPreference = 'Stop'
$framework = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'
if (-not (Test-Path (Join-Path $framework 'csc.exe'))) {
    $framework = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319'
}
$output = Join-Path $PSScriptRoot 'build'
New-Item -ItemType Directory -Path $output -Force | Out-Null
$exe = Join-Path $output 'TeacherDeskText.exe'
# If the text widget is running, its exe is locked: rename it (the running copy keeps working) and build a new one.
# The renamed .old.exe is removed on the next build once that copy has exited.
Get-ChildItem -LiteralPath $output -Filter 'TeacherDeskText.old.exe' -ErrorAction SilentlyContinue | ForEach-Object { try { Remove-Item -LiteralPath $_.FullName -ErrorAction Stop } catch { } }
if (Test-Path -LiteralPath $exe) {
    try { [IO.File]::Open($exe, 'Open', 'ReadWrite', 'None').Dispose() }
    catch { Rename-Item -LiteralPath $exe -NewName 'TeacherDeskText.old.exe' -ErrorAction SilentlyContinue }
}
$arguments = @('/nologo', '/target:winexe', '/optimize+', '/codepage:65001', '/reference:System.Drawing.dll',
    '/reference:System.Windows.Forms.dll', ('/win32manifest:' + (Join-Path $PSScriptRoot 'app.manifest')),
    # text.ico = 선생님이 고른 logo (노을 그러데이션 둥근 네모 · 산·다리·물결·해, 2026-11-13). 시작 메뉴 «글자 위젯» 바로가기가 이 그림을 쓴다
    ('/win32icon:' + (Join-Path $PSScriptRoot 'text.ico')),
    ('/out:' + $exe), (Join-Path $PSScriptRoot 'DesktopText.cs'))
& (Join-Path $framework 'csc.exe') @arguments
if ($LASTEXITCODE -ne 0) { throw 'Desktop text launcher compilation failed.' }
Write-Output $exe
