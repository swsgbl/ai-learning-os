' M14-129: production drift watch 静默执行入口（计划任务 Action 经 wscript //B //Nologo 调用）。
'
' 纪律（与 run_monitoring_pipeline_silent.vbs 同款）：
' - 仓库根自脚本位置推导（文件 → ops → tools → repo），无盘符/机器特定路径硬编码。
' - 零弹窗零终端零浏览器：WScript.Shell.Run(..., 0, True) 以隐藏窗口运行并等待。
' - 不写/不传任何 secret：本 wrapper 只拼两个仓库内路径 + drift watch 自身的
'   门禁旗标（--execute 与精确确认短语——短语是公开安全门禁常量，非 secret；
'   报告/工件落 drift watch 自身的 gitignored .verify 目录，本 wrapper 不另落盘）。
' - drift watch 自身是纯只读采集器（digest fail-closed）；Task Scheduler 侧
'   IgnoreNew 多实例策略防重叠——wrapper 不做额外互斥。
' - 退出码透传：python 的退出码经 WScript.Quit 原样返回给计划任务
'   （Task Scheduler「上次运行结果」可见；0=plan/execute 无漂移、2=拒绝或
'   drift=true——漂移即失败证据，绝不伪装）。
'
' 预检失败退出码（与 drift watch 退出码不冲突的专用段）：2=venv python 缺失；
' 3=drift watch 脚本缺失；4=repo 目录缺失。
Option Explicit

Dim fso, shell, repoRoot, opsDir, pythonExe, watchScript, command, exitCode

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

repoRoot = fso.GetParentFolderName(fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName)))
opsDir = fso.BuildPath(fso.BuildPath(repoRoot, "tools"), "ops")
pythonExe = fso.BuildPath(fso.BuildPath(fso.BuildPath(repoRoot, ".venv"), "Scripts"), "python.exe")
watchScript = fso.BuildPath(opsDir, "production_drift_watch.py")

If Not fso.FolderExists(repoRoot) Then WScript.Quit 4
If Not fso.FileExists(pythonExe) Then WScript.Quit 2
If Not fso.FileExists(watchScript) Then WScript.Quit 3

shell.CurrentDirectory = repoRoot
command = """" & pythonExe & """ """ & watchScript & """ --execute --confirm ""EXECUTE READ-ONLY PRODUCTION DRIFT WATCH"""
exitCode = shell.Run(command, 0, True)

WScript.Quit exitCode
