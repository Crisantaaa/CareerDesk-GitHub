Option Explicit

Dim shell, fileSystem, projectDirectory, launcher
Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")

projectDirectory = fileSystem.GetParentFolderName(WScript.ScriptFullName)
launcher = Chr(34) & projectDirectory & "\start_careerdesk.cmd" & Chr(34)

' Hide the server console while keeping the existing start script and browser behavior.
shell.Run launcher, 0, False
