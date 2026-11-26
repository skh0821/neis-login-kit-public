# 바탕화면에 «상벌점» 아이콘을 놓고, 예전 «교무 도구» 위젯의 자동 시작을 뺀다 (2026-11-10 «아이콘 방식»).
# install_icon.cmd 를 더블클릭하면 이게 돈다. 결과는 build\install_icon.log 에 남긴다 (값 없음 — 무엇을 했는지만).
# 아이콘을 누르면 상벌점 창만 뜨고, 창을 닫으면 프로그램이 완전히 꺼진다 — 늘 켜져 있는 것이 없다.
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$exe = Join-Path $here 'build\TeacherDeskDemo.exe'
$log = Join-Path $here 'build\install_icon.log'
$lines = New-Object System.Collections.Generic.List[string]
function Say([string]$text) { $lines.Add((Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + '  ' + $text) }

try {
    if (-not (Test-Path -LiteralPath $exe)) { & (Join-Path $here 'build.ps1') | Out-Null; Say '실행 파일이 없어 새로 빌드함' }
    $shell = New-Object -ComObject WScript.Shell

    # 1) 바탕화면 «상벌점» 아이콘 (인자 없음 = 상벌점 창만)
    $desk = Join-Path ([Environment]::GetFolderPath('Desktop')) '상벌점.lnk'
    $lnk = $shell.CreateShortcut($desk)
    $lnk.TargetPath = $exe
    $lnk.Arguments = ''
    $lnk.WorkingDirectory = Split-Path $exe
    $lnk.IconLocation = "$exe,0"
    $lnk.Description = '디벗 미보관 상벌점 — 누를 때만 뜨고, 닫으면 꺼집니다'
    $lnk.Save()
    Say ('바탕화면 아이콘 만듦: ' + (Test-Path -LiteralPath $desk))

    # 2) 예전 위젯 자동 시작·바로가기 빼기 — 이 실행 파일을 가리키는 것만, 휴지통으로
    Add-Type -AssemblyName Microsoft.VisualBasic
    foreach ($folder in 'Startup', 'Desktop') {
        $old = Join-Path ([Environment]::GetFolderPath($folder)) '교무 도구 데모.lnk'
        if (-not (Test-Path -LiteralPath $old)) { Say "$folder «교무 도구 데모» 없음 — 그대로"; continue }
        $target = $shell.CreateShortcut($old).TargetPath
        if ([string]::Equals([IO.Path]::GetFullPath($target), [IO.Path]::GetFullPath($exe), 'OrdinalIgnoreCase')) {
            [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile($old, 'OnlyErrorDialogs', 'SendToRecycleBin')
            Say ("$folder «교무 도구 데모» 휴지통으로: " + (-not (Test-Path -LiteralPath $old)))
        } else {
            Say "$folder «교무 도구 데모» 는 다른 프로그램을 가리켜서 그대로 둠"
        }
    }

    # 3) 켜져 있는 예전 위젯은 닫는다 (창 위치는 저장됨). 아이콘 방식 창은 건드리지 않는다
    foreach ($proc in @(Get-Process TeacherDeskDemo -ErrorAction SilentlyContinue)) {
        if ($proc.MainWindowTitle -eq '교무 도구') { $proc.CloseMainWindow() | Out-Null; Say ('켜져 있던 위젯 닫음: ' + $proc.WaitForExit(5000)) }
    }
    Say '끝'
} catch {
    Say ('실패: ' + $_.Exception.Message)
}
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
[IO.File]::WriteAllLines($log, $lines, (New-Object Text.UTF8Encoding $true))
