' Launch tg-scheduler in the background (no console window).
' Resolves paths relative to this script so it works from any checkout location.
Set fso = CreateObject("Scripting.FileSystemObject")
Set WshShell = CreateObject("WScript.Shell")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = scriptDir

' Prefer the project venv pythonw.exe; fall back to a system pythonw on PATH.
venvPythonw = scriptDir & "\.venv\Scripts\pythonw.exe"
If fso.FileExists(venvPythonw) Then
    pythonw = venvPythonw
Else
    pythonw = "pythonw.exe"
End If

WshShell.Run """" & pythonw & """ """ & scriptDir & "\main.py""", 0, False
