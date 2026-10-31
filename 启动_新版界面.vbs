Option Explicit

Dim shell, files, folder, entry, command, launchError
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
folder = files.GetParentFolderName(WScript.ScriptFullName)
entry = files.BuildPath(folder, "tcp2com_web.py")

If Not files.FileExists(entry) Then
    MsgBox "tcp2com_web.py was not found. Keep this launcher in the program folder.", vbCritical, "Wi-Fi Bridge"
    WScript.Quit 1
End If

shell.CurrentDirectory = folder
command = "pyw -3 -B """ & entry & """ --windowed"
On Error Resume Next
' pythonw has no console. SW_HIDE would also hide the actual desktop window.
shell.Run command, 1, False
launchError = Err.Number
On Error GoTo 0
If launchError <> 0 Then
    MsgBox "Could not start Python. Install Python 3 with the pyw launcher, then try again.", vbCritical, "Wi-Fi Bridge"
    WScript.Quit 1
End If
