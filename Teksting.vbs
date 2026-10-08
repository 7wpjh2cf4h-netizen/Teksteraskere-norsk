' Teksting - starts the app without a black window.
' First time (or if something is missing) it runs start.bat, which shows progress.
Option Explicit
Dim sh, fso, here, pydir, pyw, py, uv, env, rc, d
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
uv = here & "\runtime\uv.exe"

' Python lives in runtime\python\cpython-3.12... (no virtual environment, see start.bat)
pydir = ""
If fso.FolderExists(here & "\runtime\python") Then
  For Each d In fso.GetFolder(here & "\runtime\python").SubFolders
    If LCase(Left(d.Name, 12)) = "cpython-3.12" And fso.FileExists(d.Path & "\pythonw.exe") Then pydir = d.Path
  Next
End If
pyw = pydir & "\pythonw.exe"
py = pydir & "\python.exe"

If pydir = "" Or Not fso.FileExists(uv) Then
  sh.Run """" & here & "\start.bat""", 1, False
  WScript.Quit
End If

Set env = sh.Environment("PROCESS")
env("UV_CACHE_DIR") = here & "\runtime\uv-cache"
env("UV_PYTHON_INSTALL_DIR") = here & "\runtime\python"
env("UV_PYTHON_PREFERENCE") = "only-managed"
sh.CurrentDirectory = here

' Keep packages in step with requirements.txt after an update (takes a second when nothing changed)
rc = sh.Run("""" & uv & """ pip install --python """ & py & """ --break-system-packages --quiet -r """ & here & "\requirements.txt""", 0, True)
If rc <> 0 Then
  sh.Run """" & here & "\start.bat""", 1, False
  WScript.Quit
End If

' Window style 1, not 0: pythonw has no console anyway, and 0 would also hide Teksting's own window
sh.Run """" & pyw & """ """ & here & "\server.py"" --app", 1, False
