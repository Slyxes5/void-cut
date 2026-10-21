"""Register Void Cut for the Windows user running this script; does not start it."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
TASK_NAME = 'Void Cut Backend'
MARKER = 'Void Cut managed backend: single local Python process; starts at user logon.'
POWERSHELL = r'''
$ErrorActionPreference = 'Stop'
Import-Module ScheduledTasks
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$root = $env:VOID_CUT_TASK_ROOT
$taskName = 'Void Cut Backend'
$marker = 'Void Cut managed backend: single local Python process; starts at user logon.'
$python = Join-Path $root 'venv\Scripts\pythonw.exe'
$runner = Join-Path $root 'scripts\run_backend_service.pyw'
$action = New-ScheduledTaskAction -Execute $python -Argument ('-E -u "' + $runner + '"') -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity.Name
$trigger.Delay = 'PT10S'
$principal = New-ScheduledTaskPrincipal -UserId $identity.Name -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$definition = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description $marker
if ($env:VOID_CUT_TASK_VALIDATE -eq '1') {
    Write-Output 'Task definition valid. Nothing registered or started.'
    exit 0
}
$existing = Get-ScheduledTask -TaskName $taskName -TaskPath '\' -ErrorAction SilentlyContinue
if ($existing) {
    if ($existing.Description -ne $marker) { throw 'An unrelated task already uses this name. It was not changed.' }
    if ($existing.State -eq 'Running') { throw 'Task is running. Wait for jobs to finish, then stop the task before updating it.' }
    $existingUser = $existing.Principal.UserId
    if ($existingUser -ne $identity.Name -and $existingUser -ne $identity.User.Value) { throw 'Existing task belongs to another user. It was not changed.' }
}
Register-ScheduledTask -TaskName $taskName -TaskPath '\' -InputObject $definition -Force | Out-Null
Write-Output ('Task installed for ' + $identity.Name + '. It has not been started.')
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='Show configuration without changing Windows tasks.')
    parser.add_argument('--validate', action='store_true', help='Build the task definition without registering or starting it.')
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('This installer is for Windows only.')
    pythonw = ROOT / 'venv' / 'Scripts' / 'pythonw.exe'
    runner = ROOT / 'scripts' / 'run_backend_service.pyw'
    if not pythonw.is_file() or not runner.is_file() or not (ROOT / 'backend' / '.env').is_file():
        parser.error('Missing local Python environment, launcher, or backend/.env. Run the local setup first.')
    if args.dry_run:
        print(json.dumps({'task': TASK_NAME, 'project': str(ROOT), 'executable': str(pythonw),
                          'arguments': ['-E', '-u', str(runner)], 'trigger': 'current user logon + 10 seconds',
                          'restart_on_failure': '5 attempts, 1 minute apart', 'starts_now': False}, indent=2))
        return 0
    environment = dict(os.environ, VOID_CUT_TASK_ROOT=str(ROOT), VOID_CUT_TASK_VALIDATE='1' if args.validate else '0')
    shell = Path(os.environ['SystemRoot']) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
    result = subprocess.run([str(shell), '-NoProfile', '-NonInteractive', '-Command', POWERSHELL], env=environment)
    if result.returncode == 0 and not args.validate:
        print('After stopping the foreground backend, start it with:')
        print('Start-ScheduledTask -TaskName "Void Cut Backend"')
        print('Status: Get-ScheduledTask -TaskName "Void Cut Backend"')
        print('Log: logs/backend-service.log')
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
