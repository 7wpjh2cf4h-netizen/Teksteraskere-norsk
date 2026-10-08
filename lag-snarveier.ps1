# Creates "Teksting" shortcuts (with icon) on the desktop and in the Start menu.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$shell = New-Object -ComObject WScript.Shell
$q = [char]34
foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
  if (-not $dir) { continue }
  $lnk = $shell.CreateShortcut((Join-Path $dir 'Teksting.lnk'))
  $lnk.TargetPath = Join-Path $env:WINDIR 'System32\wscript.exe'
  $lnk.Arguments = $q + (Join-Path $here 'Teksting.vbs') + $q
  $lnk.WorkingDirectory = $here
  $lnk.IconLocation = (Join-Path $here 'static\teksting.ico') + ',0'
  $lnk.Description = 'Teksting - norske undertekster'
  $lnk.Save()
}
