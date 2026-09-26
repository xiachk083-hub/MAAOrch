param([string]$Image,[string]$Out,[int]$X,[int]$Y,[int]$W,[int]$H,[double]$Scale=1.0)
Add-Type -AssemblyName System.Drawing
$bytes = [System.IO.File]::ReadAllBytes($Image)
$ms = New-Object System.IO.MemoryStream(,$bytes)
$src = [System.Drawing.Bitmap]::FromStream($ms)
$rect = New-Object System.Drawing.Rectangle($X,$Y,$W,$H)
$crop = $src.Clone($rect, $src.PixelFormat)
if ($Scale -ne 1.0) {
  $nw = [int]($crop.Width * $Scale); $nh = [int]($crop.Height * $Scale)
  $resized = New-Object System.Drawing.Bitmap($nw, $nh)
  $g = [System.Drawing.Graphics]::FromImage($resized)
  $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
  $g.DrawImage($crop, 0, 0, $nw, $nh); $g.Dispose()
  $crop.Dispose(); $crop = $resized
}
$crop.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
"$Out  <- $(Split-Path $Image -Leaf) [$X,$Y,$W,$H] -> $($crop.Width)x$($crop.Height)"
$crop.Dispose(); $src.Dispose(); $ms.Dispose()
