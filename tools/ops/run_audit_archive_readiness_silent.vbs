' M14-53: silent audit archive readiness entry point. The scheduled task
' Action invokes this file via: wscript.exe //B //Nologo "<this path>".
'
' Discipline (same family as run_monitoring_pipeline_silent.vbs):
' - The repo root is derived from this script location (file -> ops ->
'   tools -> repo); no drive letters or machine-specific paths hardcoded.
' - Zero popups, zero console, zero browser: WScript.Shell.Run(..., 0, True)
'   runs the command in a hidden window and waits for it.
' - Always invokes <repo>\.venv\Scripts\python.exe and
'   <repo>\tools\ops\audit_archive_readiness.py. This wrapper offers no
'   Python or script override surface.
' - Never writes or passes secrets: this wrapper only assembles repo-internal
'   paths plus the canonical gitignored state/policy/output paths. The
'   readiness CLI itself is a fail-closed read-only evaluator whose report
'   lands in the canonical gitignored directory.
' - Never passes --now: every scheduled run naturally uses the current UTC
'   time basis of the readiness evaluation.
' - The canonical artifacts directory is ensured only here:
'   audit_archive_readiness.py deliberately creates no directories, so this
'   wrapper makes sure .verify\artifacts\m14-53-audit-archive-readiness\
'   exists before the run.
' - Exit code passthrough: the python readiness exit code is returned
'   verbatim to the scheduled task via WScript.Quit (visible in Task
'   Scheduler "Last Run Result"; 0 = readiness report generated).
'
' Preflight failure exit codes (dedicated range, no collision with the
' readiness CLI exit codes):
'   2 = venv python missing; 3 = readiness CLI script missing;
'   4 = repo root missing; 5 = canonical artifacts dir creation failed.
'
' Source contract: this file must stay pure ASCII (content and comments).
' Non-ASCII bytes fail cscript compilation (missing statement) on the
' default ANSI code page before any preflight runs; ASCII text with LF
' line endings parses safely under cscript.
Option Explicit

Dim fso, shell, repoRoot, opsDir, pythonExe, readinessScript
Dim verifyDir, artifactsParentDir, artifactsDir
Dim statePath, policyPath, outputPath, command, exitCode

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

repoRoot = fso.GetParentFolderName(fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName)))
opsDir = fso.BuildPath(fso.BuildPath(repoRoot, "tools"), "ops")
pythonExe = fso.BuildPath(fso.BuildPath(fso.BuildPath(repoRoot, ".venv"), "Scripts"), "python.exe")
readinessScript = fso.BuildPath(opsDir, "audit_archive_readiness.py")

' Exactly three BuildPath levels, built stepwise with one call per level (a
' stray extra nested BuildPath call fails at runtime with invalid BuildPath
' arguments): repoRoot + .verify, then + artifacts, then the m14-53 leaf.
verifyDir = fso.BuildPath(repoRoot, ".verify")
artifactsParentDir = fso.BuildPath(verifyDir, "artifacts")
artifactsDir = fso.BuildPath(artifactsParentDir, "m14-53-audit-archive-readiness")
statePath = fso.BuildPath(artifactsDir, "state.json")
policyPath = fso.BuildPath(artifactsDir, "policy.json")
outputPath = fso.BuildPath(artifactsDir, "readiness.json")

If Not fso.FolderExists(repoRoot) Then WScript.Quit 4
If Not fso.FileExists(pythonExe) Then WScript.Quit 2
If Not fso.FileExists(readinessScript) Then WScript.Quit 3

' The readiness CLI creates no directories: the canonical artifacts chain is
' this wrapper's sole responsibility. Only the chain
' .verify -> .verify\artifacts -> m14-53 leaf is ensured; no other path is
' touched. CreateFolder is not recursive, so each level is ensured in order
' and an already-existing level is a no-op.
Dim chainItem, artifactsChain
artifactsChain = Array(verifyDir, artifactsParentDir, artifactsDir)
For Each chainItem In artifactsChain
  If Not fso.FolderExists(chainItem) Then
    On Error Resume Next
    fso.CreateFolder(chainItem)
    If Err.Number <> 0 Or Not fso.FolderExists(chainItem) Then
      On Error Goto 0
      WScript.Quit 5
    End If
    On Error Goto 0
  End If
Next

shell.CurrentDirectory = repoRoot
command = """" & pythonExe & """ """ & readinessScript & """ --state """ & statePath & _
          """ --policy """ & policyPath & """ --output """ & outputPath & """"
exitCode = shell.Run(command, 0, True)

WScript.Quit exitCode
