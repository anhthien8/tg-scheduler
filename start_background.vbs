Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\DELL\.gemini\antigravity\playground\ecliptic-universe\tg-scheduler"
WshShell.Run """C:\Python314\pythonw.exe"" main.py", 0, False
