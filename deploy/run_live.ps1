# Go live from this PC: start the backend and publish it on a permanent public HTTPS address (Tailscale Funnel).
#
#   Right-click > Run with PowerShell      or      powershell -ExecutionPolicy Bypass -File deploy\run_live.ps1
#
# Why: free Hugging Face Docker Spaces now need a paid plan, so the backend runs here. Tailscale Funnel gives a
# fixed https://<pc-name>.<tailnet>.ts.net address (free), so the Vercel dashboard, the Gmail bridge and the Telegram
# relay never need updating. Keep the PC awake and online while reviewers might use the demo.
#
# Stop: close this window (or Ctrl+C). The public address goes offline until you run this again.

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Repo "backend"
$Python = Join-Path $Backend ".venv\Scripts\python.exe"
$Port = 8000
$Tailscale = "C:\Program Files\Tailscale\tailscale.exe"

if (-not (Test-Path $Python)) { throw "Backend virtualenv missing: $Python (see HANDOVER.md section 5)" }
if (-not (Test-Path $Tailscale)) { throw "Tailscale is not installed: winget install Tailscale.Tailscale" }

# 1. Public address: Funnel forwards https://<this-pc>.ts.net -> http://127.0.0.1:8000 (keeps running in the background)
& $Tailscale funnel --bg $Port | Out-Host
$dns = ((& $Tailscale status --json | ConvertFrom-Json).Self.DNSName).TrimEnd(".")
Write-Host ""
Write-Host "Public API address: https://$dns" -ForegroundColor Green
Write-Host "Health check:       https://$dns/api/health" -ForegroundColor Green
Write-Host ""

# 2. Backend, restarted automatically if it ever crashes
$env:PYTHONIOENCODING = "utf-8"
Set-Location $Backend
while ($true) {
    Write-Host "$(Get-Date -Format s) starting backend on port $Port ..." -ForegroundColor Cyan
    & $Python -m uvicorn app.main:app --host 127.0.0.1 --port $Port
    Write-Host "$(Get-Date -Format s) backend stopped (exit $LASTEXITCODE) - restarting in 5 s. Close this window to stop." -ForegroundColor Yellow
    Start-Sleep -Seconds 5
}
