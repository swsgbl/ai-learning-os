' M14-06 Round 2: 生产恢复静默执行入口（计划任务 Action 经 wscript //B //Nologo 调用）。
'
' 纪律：
' - 仓库根自脚本位置推导（文件 → ops → tools → repo），无盘符/机器特定路径硬编码。
' - 零弹窗零终端零浏览器：WScript.Shell.Run(..., 0, True) 以隐藏窗口运行并等待。
' - 不写/不传任何 secret：pin env 由 production_recovery.py 自行经 docker compose
'   --env-file 读取；本 wrapper 只拼两个仓库内路径。
' - 日志复用 recovery 自身（artifacts/recovery/，gitignored）；本 wrapper 不另落盘。
' - 退出码透传：python 的 recovery 退出码经 WScript.Quit 原样返回给计划任务
'   （Task Scheduler「上次运行结果」可见，0=成功）。
'
' 预检失败退出码（与 recovery 退出码不冲突的专用段）：2=venv python 缺失；3=recovery 脚本缺失。
Option Explicit

Dim fso, shell, repoRoot, opsDir, pythonExe, recoveryScript, command, exitCode

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

repoRoot = fso.GetParentFolderName(fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName)))
opsDir = fso.BuildPath(fso.BuildPath(repoRoot, "tools"), "ops")
pythonExe = fso.BuildPath(fso.BuildPath(fso.BuildPath(repoRoot, ".venv"), "Scripts"), "python.exe")
recoveryScript = fso.BuildPath(opsDir, "production_recovery.py")

If Not fso.FolderExists(repoRoot) Then WScript.Quit 4
If Not fso.FileExists(pythonExe) Then WScript.Quit 2
If Not fso.FileExists(recoveryScript) Then WScript.Quit 3

shell.CurrentDirectory = repoRoot
command = """" & pythonExe & """ """ & recoveryScript & """"
exitCode = shell.Run(command, 0, True)

WScript.Quit exitCode
