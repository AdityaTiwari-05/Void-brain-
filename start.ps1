# ABHEDYA-CHAKRA — One-command startup script (PowerShell)
# Usage: .\start.ps1
# Or run backend and frontend separately as shown below.

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "  OPERATION ABHEDYA-CHAKRA — Startup" -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host ""

# ── Check Python ───────────────────────────────────────────────────────────
$pythonCmd = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $ver = & $cmd --version 2>&1
        if ($ver -match "Python 3\.(1[0-9]|[2-9]\d)") {
            $pythonCmd = $cmd
            Write-Host "  [✓] Python: $ver" -ForegroundColor Green
            break
        }
    } catch {}
}
if (-not $pythonCmd) {
    Write-Host "  [✗] Python 3.10+ not found. Install from https://python.org" -ForegroundColor Red
    exit 1
}

# ── Check Node ─────────────────────────────────────────────────────────────
$nodeOk = $false
try {
    $nodeVer = & node --version 2>&1
    Write-Host "  [✓] Node.js: $nodeVer" -ForegroundColor Green
    $nodeOk = $true
} catch {
    Write-Host "  [!] Node.js not found. Frontend will not start." -ForegroundColor Yellow
    Write-Host "      Install from https://nodejs.org" -ForegroundColor Yellow
}

Write-Host ""

# ── Backend ────────────────────────────────────────────────────────────────
$backendDir = Join-Path $PSScriptRoot "backend"
Push-Location $backendDir

# Install dependencies if needed
if (-not (Test-Path ".\.venv") -and -not (Get-Command uvicorn -ErrorAction SilentlyContinue)) {
    Write-Host "  Installing backend dependencies..." -ForegroundColor Yellow
    & $pythonCmd -m pip install -r requirements.txt --quiet
}

# Copy .env if missing
if (-not (Test-Path ".env") -and (Test-Path ".env.example")) {
    Copy-Item ".env.example" ".env"
    Write-Host "  [✓] Created .env from .env.example" -ForegroundColor Green
}

Pop-Location

Write-Host ""
Write-Host "  Starting backend on http://localhost:8000 ..." -ForegroundColor Yellow
Write-Host "  (Run in a separate terminal:)" -ForegroundColor Gray
Write-Host ""
Write-Host "    cd backend" -ForegroundColor White
Write-Host "    uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload" -ForegroundColor White
Write-Host ""

if ($nodeOk) {
    Write-Host "  Starting frontend on http://localhost:5173 ..." -ForegroundColor Yellow
    Write-Host "  (Run in another separate terminal:)" -ForegroundColor Gray
    Write-Host ""
    Write-Host "    cd frontend" -ForegroundColor White
    Write-Host "    npm install" -ForegroundColor White
    Write-Host "    npm run dev" -ForegroundColor White
    Write-Host ""
}

Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "  Quick start order:" -ForegroundColor Cyan
Write-Host ""
Write-Host "  1. Terminal 1:  cd backend && uvicorn app.main:app --reload --port 8000" -ForegroundColor White
Write-Host "  2. Terminal 2:  cd frontend && npm install && npm run dev" -ForegroundColor White
Write-Host "  3. Browser:     http://localhost:5173" -ForegroundColor White
Write-Host "  4. API Docs:    http://localhost:8000/docs" -ForegroundColor White
Write-Host ""
Write-Host "  Generate test data:" -ForegroundColor Cyan
Write-Host "    python scripts/generate_synthetic.py --rows 100000 --output data/raw/test.csv" -ForegroundColor White
Write-Host ""
Write-Host "  Run tests:" -ForegroundColor Cyan
Write-Host "    cd backend && python -m pytest tests/ -v" -ForegroundColor White
Write-Host ""
Write-Host "  Run benchmark:" -ForegroundColor Cyan
Write-Host "    python scripts/benchmark_ingestion.py --rows 2000000" -ForegroundColor White
Write-Host "================================================================" -ForegroundColor Cyan
