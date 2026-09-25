' M14-141: production drift watch 告警分发静默执行入口（计划任务 Action 经
' wscript //B //Nologo 调用）。
'
' 纪律（与 run_production_drift_watch_silent.vbs（M14-129）同款，按 M14-141
' 告警链扩展）：
' - 仓库根自脚本位置推导（文件 → ops → tools → repo），无盘符/机器特定路径
'   硬编码。
' - 零弹窗零终端零浏览器：WScript.Shell.Run(..., 0, True) 以隐藏窗口运行
'   并等待。
' - 只调用仓库内 M14-137 告警任务桥（production_drift_watch_alert_task.py）
'   的 execute 形态：--secret-file <操作者文件> --execute --confirm
'   "EXECUTE PRODUCTION DRIFT WATCH ALERT TASK"（M14-137 自有短语，公开
'   安全门禁常量，非 secret）。任务桥再按其结构性白名单移交 M14-135
'   分发 CLI——本 wrapper 绝不直接调用 M14-135，绝不绕过任何门禁。
' - secret 纪律：操作者 webhook secret JSON 放在固定仓库相对路径
'   infra/env.production-drift-watch-alert-secret.json（gitignored；与
'   M14-06 infra/env.production-recovery 同款「固定路径 + 仅存在性检查」
'   先例）。本 wrapper 只对该路径做 FileExists 存在性检查——**绝不读取、
'   绝不回显、绝不落盘其内容**；内容校验全部由 M14-135 承担。任何输出面
'   （XML/wrapper/日志）只允许出现该路径本身，绝不出现 secret 值。
' - 不写任何文件：报告/台账/工件全部由任务桥与 M14-135 按各自 gitignored
'   .verify 目录语义落盘，本 wrapper 零落盘。
' - 退出码透传：python 的退出码经 WScript.Quit 原样返回给计划任务（Task
'   Scheduler「上次运行结果」可见；0=无需分发（skipped-no-alerts）或移交
'   成功、2=一切 fail-closed 拒绝（门禁/报告校验/分发失败/重复分发）、
'   3 不会出现——wrapper 只运行 execute 形态，而任务桥仅 plan 才用 3）。
'
' 预检失败退出码（专用段，先于 python 调用）：2=venv python 缺失；
' 3=告警任务桥脚本缺失；4=repo 目录缺失；5=secret 文件缺失（存在性）。
Option Explicit

Dim fso, shell, repoRoot, opsDir, pythonExe, alertScript, secretFile, command, exitCode

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

repoRoot = fso.GetParentFolderName(fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName)))
opsDir = fso.BuildPath(fso.BuildPath(repoRoot, "tools"), "ops")
pythonExe = fso.BuildPath(fso.BuildPath(fso.BuildPath(repoRoot, ".venv"), "Scripts"), "python.exe")
alertScript = fso.BuildPath(opsDir, "production_drift_watch_alert_task.py")
secretFile = fso.BuildPath(fso.BuildPath(repoRoot, "infra"), "env.production-drift-watch-alert-secret.json")

If Not fso.FolderExists(repoRoot) Then WScript.Quit 4
If Not fso.FileExists(pythonExe) Then WScript.Quit 2
If Not fso.FileExists(alertScript) Then WScript.Quit 3
If Not fso.FileExists(secretFile) Then WScript.Quit 5

shell.CurrentDirectory = repoRoot
command = """" & pythonExe & """ """ & alertScript & """ --secret-file """ & secretFile & """ --execute --confirm ""EXECUTE PRODUCTION DRIFT WATCH ALERT TASK"""
exitCode = shell.Run(command, 0, True)

WScript.Quit exitCode
