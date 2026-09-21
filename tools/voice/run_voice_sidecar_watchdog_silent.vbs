' M14-77: 语音健康 sidecar 看护静默入口（计划任务 Action 经 wscript //B //Nologo 调用）。
'
' 职责：周期性幂等 ensure——调用 voice_health_sidecar_control.py start：
'   - sidecar 存活（manifest 归属核验 + 双端口健康/启动中）→ start 幂等跳过
'     （零 spawn、零信号）；
'   - sidecar 静默退出（stale manifest）→ start 清理残留并以全部既有安全
'     核验（生产保护 PID/标记、身份标记、端口/绑定事实）重新拉起——死亡
'     暴露窗收敛到本任务周期（PT5M，< 监控管道 PT15M）；
'   - WSL 管理面不可用 → start rc=3 可见失败（Task Scheduler「上次运行
'     结果」可见，下一轮自动重试；绝不掩盖、绝不伪造成功）。
'
' 纪律（与 run_monitoring_pipeline_silent.vbs 同款，M14-14 模式）：
' - 仓库根自脚本位置推导（文件 → voice → tools → repo），无盘符/机器
'   特定路径硬编码。
' - 零弹窗零终端：WScript.Shell.Run(..., 0, True) 隐藏窗口运行并等待。
' - 不写/不传任何 secret：本 wrapper 只拼两个仓库内路径 + start 子命令；
'   编排证据（sidecar-control.log）由控制器自身落 gitignored .verify 目录，
'   本 wrapper 不另落盘。
' - 独立计划任务（PT5M 间隔 / PT4M 执行时限），不挤占监控管道任务
'   AIOS-Monitoring-Pipeline 的 PT12M 执行预算；Task Scheduler 侧
'   IgnoreNew 多实例策略防重叠，控制器自身 ControlLock 串行化并发
'   start/stop。
' - 退出码透传：start 的退出码经 WScript.Quit 原样返回给计划任务
'   （0=健康/已重启/幂等跳过；1=启动失败；3=安全拒绝/WSL 管理不可用
'   ——均可见不遮蔽）。
'
' 运维注记：看护任务在线期间 sidecar 期望恒运行——人工维护前请先停看护
' 任务（voice_sidecar_watchdog_task.py uninstall，supervisor 操作），否则
' stop 后 ≤5 分钟内会被幂等 ensure 重新拉起（设计意图）。
'
' 预检失败退出码：2=venv python 缺失；3=控制器脚本缺失。
Option Explicit

Dim fso, shell, repoRoot, voiceDir, pythonExe, controllerScript, command, exitCode

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

repoRoot = fso.GetParentFolderName(fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName)))
voiceDir = fso.BuildPath(fso.BuildPath(repoRoot, "tools"), "voice")
pythonExe = fso.BuildPath(fso.BuildPath(fso.BuildPath(repoRoot, ".venv"), "Scripts"), "python.exe")
controllerScript = fso.BuildPath(voiceDir, "voice_health_sidecar_control.py")

If Not fso.FolderExists(repoRoot) Then WScript.Quit 4
If Not fso.FileExists(pythonExe) Then WScript.Quit 2
If Not fso.FileExists(controllerScript) Then WScript.Quit 3

shell.CurrentDirectory = repoRoot
command = """" & pythonExe & """ """ & controllerScript & """ start"
exitCode = shell.Run(command, 0, True)

WScript.Quit exitCode
