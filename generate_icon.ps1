Add-Type -AssemblyName System.Drawing

$iconPath = Join-Path $PSScriptRoot "app_icon.ico"

if (Test-Path $iconPath) {
    Write-Host "Icon da ton tai: $iconPath"
    exit 0
}

$size = 256
$bmp = New-Object System.Drawing.Bitmap $size, $size
$graphics = [System.Drawing.Graphics]::FromImage($bmp)

$graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$graphics.Clear([System.Drawing.Color]::FromArgb(15, 23, 42))

$bgBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(37, 99, 235))
$graphics.FillEllipse($bgBrush, 16, 16, 224, 224)

$font = New-Object System.Drawing.Font("Segoe UI", 120, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
$txtBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::White)
$graphics.DrawString("S", $font, $txtBrush, 70, 46)

$hIcon = $bmp.GetHicon()
$icon = [System.Drawing.Icon]::FromHandle($hIcon)

$fs = [System.IO.File]::Open($iconPath, [System.IO.FileMode]::Create)
$icon.Save($fs)
$fs.Close()

[System.Runtime.InteropServices.Marshal]::Release($hIcon) | Out-Null

$graphics.Dispose()
$bgBrush.Dispose()
$font.Dispose()
$txtBrush.Dispose()
$bmp.Dispose()

Write-Host "Da tao icon: $iconPath"