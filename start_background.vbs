' Launch tg-scheduler in the background (no console window).
' Resolves paths relative to this script so it works from any checkout location.
'
' IMPORTANT: this must run with the PROJECT venv interpreter. A bare
' "pythonw.exe" resolves via PATH and can land on a different install
' (e.g. C:\Python314) that lacks the dependencies — the server then starts,
' fails to import the app, and serves nothing while holding engine.lock.
' So: refuse to launch rather than silently fall back.
Option Explicit

Set fso = CreateObject("Scripting.FileSystemObject")
Set WshShell = CreateObject("WScript.Shell")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = scriptDir

' Require the project venv — no silent fallback to a system interpreter.
venvPythonw = scriptDir & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(venvPythonw) Then
    MsgBox "Khong tim thay .venv\Scripts\pythonw.exe" & vbCrLf & vbCrLf & _
           "Hay tao venv truoc:" & vbCrLf & _
           "  python -m venv .venv" & vbCrLf & _
           "  .venv\Scripts\pip install -r requirements.txt", _
           16, "tg-scheduler: thieu venv"
    WScript.Quit 1
End If

' Run via cmd so stdout+stderr land in startup.log (otherwise failures are
' invisible). 0 = hidden window, False = don't wait.
logFile = scriptDir & "\startup.log"
cmd = "cmd /c """"" & venvPythonw & """ """ & scriptDir & "\main.py"" > """ & _
      logFile & """ 2>&1"""
WshShell.Run cmd, 0, False
