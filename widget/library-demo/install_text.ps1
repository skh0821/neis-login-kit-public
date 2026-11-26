# 바탕화면 글자 위젯 설치 — 빌드 · 시작 메뉴 «글자 위젯» · 로그인하면 자동 시작 (관리자 권한 없이)
# install_text.cmd 를 더블클릭하면 이게 돈다. 결과는 build\install_text.log 에 한 줄씩 남는다 (값 없음).
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$exe = Join-Path $here 'build\TeacherDeskText.exe'
$log = Join-Path $here 'build\install_text.log'
$lines = New-Object System.Collections.Generic.List[string]
function Say([string]$text) { $lines.Add((Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + '  ' + $text); Write-Host $text }

try {
    & (Join-Path $here 'build_text.ps1') | Out-Null
    Say ('글자 위젯 빌드: ' + (Test-Path -LiteralPath $exe))
    $shell = New-Object -ComObject WScript.Shell
    foreach ($folder in 'Programs', 'Startup') {
        $path = Join-Path ([Environment]::GetFolderPath($folder)) '글자 위젯.lnk'
        $lnk = $shell.CreateShortcut($path)
        $lnk.TargetPath = $exe
        $lnk.WorkingDirectory = Split-Path $exe
        $lnk.IconLocation = "$exe,0"
        $lnk.Description = '바탕화면 글자 위젯 — 나이스 · 에듀파인 · 업무포털 로그인'
        $lnk.Save()
        Say (($(if ($folder -eq 'Programs') { '시작 메뉴' } else { '로그인하면 자동 시작' })) + ' 바로가기: ' + (Test-Path -LiteralPath $path))
    }
    Start-Process -FilePath $exe -WorkingDirectory (Split-Path $exe)
    Say '글자 위젯을 켰습니다 — 바탕화면 오른쪽에 글자가 나타납니다'
} catch {
    Say ('실패: ' + $_.Exception.Message)
}
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
[IO.File]::WriteAllLines($log, $lines, (New-Object Text.UTF8Encoding $true))
