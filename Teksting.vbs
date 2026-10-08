' Teksting - starts the app without a black window.
' First time (or if something is missing) it runs start.bat, which shows progress.
Option Explicit
Dim sh, fso, here, pyw, py, uv, env, rc
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = here & "\.venv\Scripts\pythonw.exe"
py = here & "\.venv\Scripts\python.exe"
uv = here & "\runtime\uv.exe"

If Not fso.FileExists(pyw) Or Not fso.FileExists(uv) Then
  sh.Run """" & here & "\start.bat""", 1, False
  WScript.Quit
End If

Set env = sh.Environment("PROCESS")
env("UV_CACHE_DIR") = here & "\runtime\uv-cache"
env("UV_PYTHON_INSTALL_DIR") = here & "\runtime\python"
env("UV_PYTHON_PREFERENCE") = "only-managed"
sh.CurrentDirectory = here

' Keep packages in step with requirements.txt after an update (takes a second when nothing changed)
rc = sh.Run("""" & uv & """ pip install --python """ & py & """ --quiet -r """ & here & "\requirements.txt""", 0, True)
If rc <> 0 Then
  sh.Run """" & here & "\start.bat""", 1, False
  WScript.Quit
End If

sh.Run """" & pyw & """ """ & here & "\server.py"" --app", 0, False
