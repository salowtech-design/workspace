# Local review of the IAM console (synthetic data, SQLite only)

Verified on Linux with these exact steps (browser sign-in as IAM admin and as employee, CSS served).
The Windows commands below follow the README (`uv`) but were not run on Windows.

## 1. Get the code
```powershell
git clone https://github.com/salowtech-design/workspace.git
cd workspace
git checkout final-iam-workspace-multidevice
uv python pin 3.12
uv sync --extra dev
```

## 2. Use local SQLite only
Make sure no TiDB variables are set in this PowerShell window (`TIDB_PASSWORD`, `USE_TIDB`, `TIDB_HOST`).
```powershell
Remove-Item Env:TIDB_PASSWORD, Env:USE_TIDB, Env:TIDB_HOST -ErrorAction SilentlyContinue
$env:DJANGO_SETTINGS_MODULE = "config.settings.local_review"
```

## 3. Dedicated hostnames (optional but needed to see host isolation)
Open Notepad as Administrator, edit `C:\Windows\System32\drivers\etc\hosts`, add:
```
127.0.0.1 iam.salowtech.com
127.0.0.1 app.salowtech.com
```
Without this you can still use `http://localhost:8000/iam/login/`, but the IAM-host and workspace-host
routing is not exercised (path-based fallback only).

## 4. Database and synthetic users
```powershell
Remove-Item test_db.sqlite3 -ErrorAction SilentlyContinue
uv run python manage.py migrate
uv run python scripts/seed_synthetic_review.py
```
The script refuses to run unless the database is local SQLite. It prints the synthetic logins, TOTP secrets
and recovery codes. Add each TOTP secret to an authenticator app, or print a code with
`uv run python -c "import pyotp;print(pyotp.TOTP('SECRET').now())"`.

## 5. Run
```powershell
uv run python manage.py runserver 127.0.0.1:8000
```
- IAM console: http://iam.salowtech.com:8000/iam/login/  (IAM-REVIEW-01)
- Workspace: http://app.salowtech.com:8000/auth/login/?flow=signin  (REVIEW-EMP-1)

Settings are plain HTTP (cookies not Secure). Production settings force HTTPS and `__Host-` cookies.

## 6. What to check
Dashboard, Sessions (Revoke), Authentication Centre, Workspace Control, IAM Administrator Auth, Recovery, at
desktop and phone width. On the IAM host these actions show "not available on the IAM host yet": Security
Shield, device fallback approval, alert acknowledge, SecOps feed and alerts console, public login link.
