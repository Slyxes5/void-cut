# Keep the backend running on Windows

The Windows Task Scheduler launcher starts a single Uvicorn process at `127.0.0.1:8000`, under the Windows account that installs it. It uses the project's existing Python environment and `backend/.env`. Existing credentials remain in that user's app-data directory. No API keys or account passwords are put in the task definition.

Check the launcher first, from PowerShell in the project directory:

```powershell
.\venv\Scripts\python.exe scripts\run_backend_service.pyw --check
```

Install the task from your own Windows PowerShell session:

```powershell
.\venv\Scripts\python.exe scripts\setup_backend_task.py
```

Installation does not start or stop any server. Wait for analysis, renders, and uploads to finish, then stop any foreground backend with Ctrl+C. Start the managed backend:

```powershell
Start-ScheduledTask -TaskName "Void Cut Backend"
Get-ScheduledTask -TaskName "Void Cut Backend"
```

After startup, open `http://127.0.0.1:8000/api/auth/status`. Keep the normal single-user authentication enabled. Replacing the backend process invalidates in-memory login sessions; sign in again if needed.

Logs go to `logs/backend-service.log`, with rotation at 5 MB and three backups. Follow them from PowerShell:

```powershell
Get-Content .\logs\backend-service.log -Tail 40 -Wait
```

The task runs without a console window. It starts ten seconds after this user logs into Windows and retries failures up to five times at one-minute intervals. There is no execution time limit and overlapping task instances are ignored. It remains available while Windows is locked; it does not run before login or after logout. The laptop must stay awake and connected to the internet. This is a local process setup, not public HTTPS access. A tunnel and Vercel routing are separate steps.

Do not start `npm run dev` or another backend while this task owns port 8000. For frontend-only development, use `npm run dev-frontend`.

To stop the task, first wait for media jobs to finish:

```powershell
Stop-ScheduledTask -TaskName "Void Cut Backend"
```

To remove the startup task after stopping it:

```powershell
Unregister-ScheduledTask -TaskName "Void Cut Backend" -Confirm:$false
```

After moving the project folder again, reinstall the task while stopped so its paths are updated. `--dry-run` prints its intended configuration; `--validate` constructs the task definition without registering or starting it.
