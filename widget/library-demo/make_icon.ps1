# 바탕화면 «상벌점» 아이콘 그림(app.ico) 만들기 — 초록 둥근 네모에 흰 «상».
# build.ps1 이 app.ico 가 없을 때 부른다. 그림을 바꾸려면 app.ico 를 지우고 build.ps1 을 다시 돌린다.
# 16~64 는 보통 비트맵(옛 프로그램도 읽음), 256 은 PNG 로 넣는다.
param([string]$Out = (Join-Path $PSScriptRoot 'app.ico'), [string]$PreviewDir = '')
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

function Draw-Icon([int]$size) {
    $bmp = New-Object System.Drawing.Bitmap $size, $size, ([System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = 'AntiAlias'
    $g.TextRenderingHint = 'AntiAliasGridFit'
    $g.Clear([System.Drawing.Color]::Transparent)
    $pad = [math]::Max(0.5, $size * 0.04)
    $r = $size * 0.22
    $w = $size - 2 * $pad
    $path = New-Object System.Drawing.Drawing2D.GraphicsPath
    $path.AddArc($pad, $pad, 2 * $r, 2 * $r, 180, 90)
    $path.AddArc($pad + $w - 2 * $r, $pad, 2 * $r, 2 * $r, 270, 90)
    $path.AddArc($pad + $w - 2 * $r, $pad + $w - 2 * $r, 2 * $r, 2 * $r, 0, 90)
    $path.AddArc($pad, $pad + $w - 2 * $r, 2 * $r, 2 * $r, 90, 90)
    $path.CloseFigure()
    $g.FillPath((New-Object System.Drawing.SolidBrush ([System.Drawing.ColorTranslator]::FromHtml('#365746'))), $path)
    $font = New-Object System.Drawing.Font 'Malgun Gothic', ([single]($size * 0.6)), ([System.Drawing.FontStyle]::Bold), ([System.Drawing.GraphicsUnit]::Pixel)
    $fmt = New-Object System.Drawing.StringFormat
    $fmt.Alignment = 'Center'; $fmt.LineAlignment = 'Center'
    $rect = New-Object System.Drawing.RectangleF 0, ([single]($size * 0.03)), $size, $size
    $g.DrawString([string][char]0xC0C1, $font, [System.Drawing.Brushes]::White, $rect, $fmt)   # «상»
    $g.Dispose()
    return $bmp
}

function Dib-Bytes($bmp) {
    # 아이콘용 비트맵: BITMAPINFOHEADER(높이 2배) + 아래에서 위로 BGRA + 빈 AND 마스크
    $s = $bmp.Width
    $lock = $bmp.LockBits((New-Object System.Drawing.Rectangle 0, 0, $s, $s), 'ReadOnly', ([System.Drawing.Imaging.PixelFormat]::Format32bppArgb))
    $pixels = New-Object byte[] ($s * $s * 4)
    [System.Runtime.InteropServices.Marshal]::Copy($lock.Scan0, $pixels, 0, $pixels.Length)
    $bmp.UnlockBits($lock)
    $maskRow = [int]([math]::Ceiling($s / 32) * 4)
    $ms = New-Object System.IO.MemoryStream
    $bw = New-Object System.IO.BinaryWriter $ms
    $bw.Write([int]40); $bw.Write([int]$s); $bw.Write([int]($s * 2)); $bw.Write([int16]1); $bw.Write([int16]32)
    $bw.Write([int]0); $bw.Write([int]($s * $s * 4 + $maskRow * $s)); $bw.Write([int]0); $bw.Write([int]0); $bw.Write([int]0); $bw.Write([int]0)
    for ($y = $s - 1; $y -ge 0; $y--) { $bw.Write($pixels, $y * $s * 4, $s * 4) }
    $bw.Write((New-Object byte[] ($maskRow * $s)))
    $bw.Flush()
    return , $ms.ToArray()   # 쉼표: PowerShell 이 바이트 배열을 낱개로 풀지 않게
}

$sizes = 16, 24, 32, 48, 64, 256
$images = @()
foreach ($s in $sizes) {
    $bmp = Draw-Icon $s
    if ($PreviewDir) { New-Item -ItemType Directory -Force $PreviewDir | Out-Null; $bmp.Save((Join-Path $PreviewDir "icon-$s.png"), [System.Drawing.Imaging.ImageFormat]::Png) }
    if ($s -eq 256) {
        $ms = New-Object System.IO.MemoryStream
        $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
        $images += , $ms.ToArray()
    } else {
        $images += , (Dib-Bytes $bmp)
    }
    $bmp.Dispose()
}

$icoStream = New-Object System.IO.MemoryStream
$w = New-Object System.IO.BinaryWriter $icoStream
$w.Write([int16]0); $w.Write([int16]1); $w.Write([int16]$sizes.Count)
$offset = 6 + 16 * $sizes.Count
for ($i = 0; $i -lt $sizes.Count; $i++) {
    $s = $sizes[$i]; $b = if ($s -ge 256) { 0 } else { $s }
    $w.Write([byte]$b); $w.Write([byte]$b); $w.Write([byte]0); $w.Write([byte]0)
    $w.Write([int16]1); $w.Write([int16]32); $w.Write([int]$images[$i].Length); $w.Write([int]$offset)
    $offset += $images[$i].Length
}
foreach ($img in $images) { $w.Write([byte[]]$img) }
$w.Flush()
[System.IO.File]::WriteAllBytes($Out, $icoStream.ToArray())
Write-Output $Out
