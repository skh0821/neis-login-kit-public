# -Out build-test : 위젯이 켜져 있어 build\TeacherDeskDemo.exe 가 잠겼을 때 시험용으로 옆 폴더에 만든다
param([string]$Out = 'build')
$ErrorActionPreference = 'Stop'
$framework = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'
if (-not (Test-Path (Join-Path $framework 'csc.exe'))) {
    $framework = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319'
}
$output = Join-Path $PSScriptRoot $Out
New-Item -ItemType Directory -Path $output -Force | Out-Null
# 위젯이 켜져 있으면 실행 파일이 잠겨 덮어쓸 수 없다 — 이름만 바꿔 두면(돌던 위젯은 그대로) 새 파일을 놓을 수 있다.
# 지난번에 바꿔 둔 .old.exe 는 그 위젯이 꺼졌으면 지운다
Get-ChildItem -LiteralPath $output -Filter '*.old.exe' -ErrorAction SilentlyContinue | ForEach-Object { try { Remove-Item -LiteralPath $_.FullName -ErrorAction Stop } catch { } }
$exe = Join-Path $output 'TeacherDeskDemo.exe'
if (Test-Path -LiteralPath $exe) {
    try { [IO.File]::Open($exe, 'Open', 'ReadWrite', 'None').Dispose() }
    catch { Rename-Item -LiteralPath $exe -NewName 'TeacherDeskDemo.old.exe' -ErrorAction SilentlyContinue }
}
$references = @('System.dll', 'System.Core.dll', 'System.Xml.dll', 'System.Drawing.dll', 'System.Windows.Forms.dll', 'WPF\WindowsBase.dll', 'WPF\PresentationCore.dll', 'WPF\PresentationFramework.dll', 'System.Xaml.dll', 'System.Web.Extensions.dll')
$icon = Join-Path $PSScriptRoot 'app.ico'   # 바탕화면 «상벌점» 아이콘 · 작업표시줄 그림
if (-not (Test-Path -LiteralPath $icon)) { & (Join-Path $PSScriptRoot 'make_icon.ps1') -Out $icon | Out-Null }
$arguments = @('/nologo', '/target:winexe', '/optimize+', '/codepage:65001', ('/win32manifest:' + (Join-Path $PSScriptRoot 'app.manifest')), ('/win32icon:' + $icon), ('/out:' + (Join-Path $output 'TeacherDeskDemo.exe')))
foreach ($reference in $references) { $arguments += '/reference:' + (Join-Path $framework $reference) }
$arguments += Join-Path $PSScriptRoot 'TeacherDesk.cs'
& (Join-Path $framework 'csc.exe') @arguments
if ($LASTEXITCODE -ne 0) { throw 'Widget compilation failed.' }
@'
<?xml version="1.0" encoding="utf-8"?>
<configuration><startup><supportedRuntime version="v4.0" sku=".NETFramework,Version=v4.8" /></startup><runtime><AppContextSwitchOverrides value="Switch.System.Windows.DoNotScaleForDpiChanges=false" /></runtime></configuration>
'@ | Set-Content -LiteralPath (Join-Path $output 'TeacherDeskDemo.exe.config') -Encoding UTF8
Write-Output (Join-Path $output 'TeacherDeskDemo.exe')
