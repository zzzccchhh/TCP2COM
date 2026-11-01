Option Explicit
Dim shell, files, folder, entry, command
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
folder = files.GetParentFolderName(WScript.ScriptFullName)
entry = files.BuildPath(folder, "lan_manager.py")
shell.CurrentDirectory = folder
command = "pyw -3 -B """ & entry & """"
On Error Resume Next
shell.Run command, 1, False
If Err.Number <> 0 Then
    MsgBox "Could not start Python. Run the BAT launcher for diagnostics.", vbCritical, "Wi-Fi Bridge"
    WScript.Quit 1
End If
